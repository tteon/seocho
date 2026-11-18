//! Local RDF ontology read model. It deliberately owns no canonical writes.

use oxigraph::io::{RdfFormat, RdfParser};
use oxigraph::model::vocab::rdfs;
use oxigraph::model::{NamedNode, NamedNodeRef, NamedOrBlankNode, Term};
use oxigraph::store::Store;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

const MAX_REQUEST: usize = 16_384;
const MAX_RESPONSE: usize = 65_536;
/// Maximum label quads examined per term lookup.
const LOOKUP_BUDGET: usize = 100_000;
/// Per-subject quads examined when reading a label or definition.
const SUBJECT_PROBE_LIMIT: usize = 256;
/// Returned label/definition text is truncated to keep responses bounded.
const MAX_TEXT_BYTES: usize = 8_192;
const SKOS_PREF_LABEL: NamedNodeRef<'_> =
    NamedNodeRef::new_unchecked("http://www.w3.org/2004/02/skos/core#prefLabel");
const SKOS_ALT_LABEL: NamedNodeRef<'_> =
    NamedNodeRef::new_unchecked("http://www.w3.org/2004/02/skos/core#altLabel");
const SKOS_DEFINITION: NamedNodeRef<'_> =
    NamedNodeRef::new_unchecked("http://www.w3.org/2004/02/skos/core#definition");
const LABEL_PREDICATES: [NamedNodeRef<'_>; 3] = [rdfs::LABEL, SKOS_PREF_LABEL, SKOS_ALT_LABEL];
// Preference order: skos:definition before rdfs:comment.
const DEFINITION_PREDICATES: [NamedNodeRef<'_>; 2] = [SKOS_DEFINITION, rdfs::COMMENT];

#[derive(Deserialize)]
struct Config {
    socket: String,
    turtle: String,
    bundle_sha256: String,
}

#[derive(Deserialize)]
struct Request {
    op: String,
    term: Option<String>,
    workspace_id: Option<String>,
    ontology_context_hash: Option<String>,
}

#[derive(Serialize)]
struct Response {
    ok: bool,
    found: bool,
    uri: Option<String>,
    label: Option<String>,
    definition: Option<String>,
    bundle_sha256: String,
    error: Option<String>,
}

fn load_store(turtle: &str) -> Result<Store, String> {
    let store = Store::new().map_err(|e| e.to_string())?;
    let file = File::open(turtle).map_err(|e| e.to_string())?;
    store
        .load_from_reader(RdfParser::from_format(RdfFormat::Turtle), file)
        .map_err(|e| e.to_string())?;
    Ok(store)
}

fn handle(store: &Store, req: Request, bundle_sha256: &str) -> Response {
    let _request_receipt = (&req.workspace_id, &req.ontology_context_hash);
    if req.op == "health" {
        return Response {
            ok: true,
            found: true,
            uri: None,
            label: None,
            definition: None,
            bundle_sha256: bundle_sha256.into(),
            error: None,
        };
    }
    let term = match req.term.as_deref().map(str::trim) {
        Some(t) if req.op == "term" && !t.is_empty() && t.len() <= 512 => t,
        _ => return failure(bundle_sha256, "invalid_term"),
    };
    // RDF terms are read from the local ontology graph only. Workspace/context
    // are protocol receipts used by the Python control plane; they never become
    // an RDF authorization mechanism.
    let needle = term.to_lowercase();
    let mut candidates: BTreeMap<String, (NamedOrBlankNode, Option<String>)> = BTreeMap::new();
    // Exact-subject probe: one indexed lookup instead of comparing every subject.
    if let Ok(iri) = NamedNode::new(term.trim_matches(['<', '>'])) {
        let mut probe = store.quads_for_pattern(Some(iri.as_ref().into()), None, None, None);
        match probe.next() {
            Some(Ok(_)) => {
                candidates.insert(iri.to_string(), (iri.into(), None));
            }
            Some(Err(_)) => return failure(bundle_sha256, "store_read_failed"),
            None => {}
        }
    }
    // Label matches only examine label quads (POS index), so the budget bounds
    // vocabulary size rather than total store size.
    let mut examined = 0usize;
    for predicate in LABEL_PREDICATES {
        for quad in store.quads_for_pattern(None, Some(predicate), None, None) {
            examined += 1;
            if examined > LOOKUP_BUDGET {
                return failure(bundle_sha256, "lookup_budget_exhausted");
            }
            let Ok(quad) = quad else {
                return failure(bundle_sha256, "store_read_failed");
            };
            let Term::Literal(literal) = &quad.object else {
                continue;
            };
            if literal.value().to_lowercase() != needle {
                continue;
            }
            let label = bounded_text(literal.value());
            let entry = candidates
                .entry(quad.subject.to_string())
                .or_insert((quad.subject, None));
            entry.1.get_or_insert(label);
            if candidates.len() > 1 {
                return failure(bundle_sha256, "ambiguous_term");
            }
        }
    }
    if let Some((uri, (subject, label))) = candidates.into_iter().next() {
        // A URI-only match still reports the subject's first label, if any.
        let label = match label {
            Some(label) => Some(label),
            None => match first_literal(store, &subject, &LABEL_PREDICATES) {
                Ok(label) => label,
                Err(error) => return failure(bundle_sha256, error),
            },
        };
        let definition = match first_literal(store, &subject, &DEFINITION_PREDICATES) {
            Ok(definition) => definition,
            Err(error) => return failure(bundle_sha256, error),
        };
        return Response {
            ok: true,
            found: true,
            uri: Some(uri),
            label,
            definition,
            bundle_sha256: bundle_sha256.into(),
            error: None,
        };
    }
    Response {
        ok: true,
        found: false,
        uri: None,
        label: None,
        definition: None,
        bundle_sha256: bundle_sha256.into(),
        error: None,
    }
}

/// First literal object of `subject` over `predicates`, in preference order and
/// store order within each predicate. Each predicate scan is bounded.
fn first_literal(
    store: &Store,
    subject: &NamedOrBlankNode,
    predicates: &[NamedNodeRef<'_>],
) -> Result<Option<String>, &'static str> {
    for predicate in predicates {
        let quads = store
            .quads_for_pattern(Some(subject.as_ref()), Some(*predicate), None, None)
            .take(SUBJECT_PROBE_LIMIT);
        for quad in quads {
            let quad = quad.map_err(|_| "store_read_failed")?;
            if let Term::Literal(literal) = &quad.object {
                return Ok(Some(bounded_text(literal.value())));
            }
        }
    }
    Ok(None)
}

fn bounded_text(value: &str) -> String {
    let mut end = value.len().min(MAX_TEXT_BYTES);
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_owned()
}

fn failure(digest: &str, error: &str) -> Response {
    Response {
        ok: false,
        found: false,
        uri: None,
        label: None,
        definition: None,
        bundle_sha256: digest.into(),
        error: Some(error.into()),
    }
}

fn decode_request(reader: impl Read) -> Result<Request, &'static str> {
    let mut bytes = Vec::new();
    BufReader::new(reader.take((MAX_REQUEST + 1) as u64))
        .read_until(b'\n', &mut bytes)
        .map_err(|_| "request_read_failed")?;
    if bytes.len() > MAX_REQUEST || bytes.last() != Some(&b'\n') {
        return Err("invalid_request_frame");
    }
    serde_json::from_slice(&bytes).map_err(|_| "invalid_json")
}

fn serve_connection(mut stream: UnixStream, store: &Store, digest: &str) {
    if stream
        .set_read_timeout(Some(Duration::from_millis(100)))
        .is_err()
        || stream
            .set_write_timeout(Some(Duration::from_millis(100)))
            .is_err()
    {
        return;
    }
    struct DeadlineReader<'a> {
        stream: &'a UnixStream,
        deadline: Instant,
    }
    impl Read for DeadlineReader<'_> {
        fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
            let remaining = self
                .deadline
                .checked_duration_since(Instant::now())
                .ok_or_else(|| std::io::Error::from(std::io::ErrorKind::TimedOut))?;
            self.stream.set_read_timeout(Some(remaining))?;
            self.stream.read(bytes)
        }
    }
    let response = match decode_request(DeadlineReader {
        stream: &stream,
        deadline: Instant::now() + Duration::from_millis(100),
    }) {
        Ok(request) => handle(store, request, digest),
        Err(error) => failure(digest, error),
    };
    let Ok(mut encoded) = serde_json::to_vec(&response) else {
        return;
    };
    if encoded.len() + 1 > MAX_RESPONSE {
        let Ok(bounded) = serde_json::to_vec(&failure(digest, "response_too_large")) else {
            return;
        };
        encoded = bounded;
    }
    encoded.push(b'\n');
    if stream.write_all(&encoded).is_err() {
        eprintln!("ontology read-model response_write_failed");
    }
}

fn main() -> Result<(), String> {
    let config_path = std::env::args()
        .nth(1)
        .ok_or("usage: seocho-oxigraph-read-model <config.json>")?;
    let config: Config =
        serde_json::from_reader(File::open(config_path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    let socket_path = Path::new(&config.socket);
    if config.bundle_sha256.len() > 128 {
        return Err("invalid_bundle_receipt".into());
    }
    if socket_path.symlink_metadata().is_ok() {
        return Err("socket_path_exists".into());
    }
    let store = Arc::new(load_store(&config.turtle)?);
    let listener = UnixListener::bind(socket_path).map_err(|e| e.to_string())?;
    for stream in listener.incoming().flatten() {
        serve_connection(stream, &store, &config.bundle_sha256);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn lookup(store: &Store, term: Option<&str>) -> Response {
        handle(
            store,
            Request {
                op: "term".into(),
                term: term.map(str::to_owned),
                workspace_id: None,
                ontology_context_hash: None,
            },
            "test",
        )
    }
    #[test]
    fn invalid_terms_are_errors_not_panics() -> Result<(), Box<dyn std::error::Error>> {
        let store = Store::new()?;
        let long = "x".repeat(513);
        for term in [None, Some(""), Some("  "), Some(long.as_str())] {
            assert_eq!(lookup(&store, term).error.as_deref(), Some("invalid_term"));
        }
        Ok(())
    }
    #[test]
    fn bounded_frames_require_newline() {
        assert!(decode_request(&b"{\"op\":\"health\"}\n"[..]).is_ok());
        assert!(decode_request(&b"{\"op\":\"health\"}"[..]).is_err());
        assert!(decode_request(vec![b'x'; MAX_REQUEST + 1].as_slice()).is_err());
    }
    #[test]
    fn label_predicates_and_ambiguity() -> Result<(), Box<dyn std::error::Error>> {
        let store = Store::new()?;
        store.load_from_reader(
            RdfParser::from_format(RdfFormat::Turtle),
            &b"<urn:a> a <urn:Company>; <http://www.w3.org/2000/01/rdf-schema#label> \"Acme\" .\n<urn:b> <http://www.w3.org/2000/01/rdf-schema#label> \"Acme\" ."[..],
        )?;
        assert!(!lookup(&store, Some("Company")).found);
        assert_eq!(
            lookup(&store, Some("Acme")).error.as_deref(),
            Some("ambiguous_term")
        );
        assert!(lookup(&store, Some("urn:a")).found);
        Ok(())
    }
    #[test]
    fn definition_prefers_skos_then_comment() -> Result<(), Box<dyn std::error::Error>> {
        let store = Store::new()?;
        store.load_from_reader(
            RdfParser::from_format(RdfFormat::Turtle),
            &br#"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
<urn:a> rdfs:label "Acme"; rdfs:comment "A comment"; skos:definition "A definition" .
<urn:b> skos:prefLabel "Beta"; rdfs:comment "Only comment" .
<urn:c> skos:altLabel "Gamma"; rdfs:comment <urn:not-a-literal> .
"#[..],
        )?;
        let acme = lookup(&store, Some("acme"));
        assert_eq!(acme.label.as_deref(), Some("Acme"));
        assert_eq!(acme.definition.as_deref(), Some("A definition"));
        let beta = lookup(&store, Some("BETA"));
        assert_eq!(beta.uri.as_deref(), Some("<urn:b>"));
        assert_eq!(beta.definition.as_deref(), Some("Only comment"));
        let gamma = lookup(&store, Some("Gamma"));
        assert!(gamma.found);
        assert_eq!(gamma.definition, None);
        // URI-only matches still report the subject's label and definition.
        let by_uri = lookup(&store, Some("<urn:a>"));
        assert_eq!(by_uri.label.as_deref(), Some("Acme"));
        assert_eq!(by_uri.definition.as_deref(), Some("A definition"));
        Ok(())
    }
    #[test]
    fn uri_and_label_on_same_subject_is_one_candidate() -> Result<(), Box<dyn std::error::Error>>
    {
        let store = Store::new()?;
        // The subject's IRI text is also its label, so both paths hit it.
        store.load_from_reader(
            RdfParser::from_format(RdfFormat::Turtle),
            &b"<urn:acme> <http://www.w3.org/2000/01/rdf-schema#label> \"urn:acme\" ."[..],
        )?;
        for term in ["urn:acme", "<urn:acme>"] {
            let found = lookup(&store, Some(term));
            assert_eq!(found.error, None);
            assert_eq!(found.uri.as_deref(), Some("<urn:acme>"));
            assert_eq!(found.label.as_deref(), Some("urn:acme"));
        }
        Ok(())
    }
    #[test]
    fn budget_counts_label_quads_only() -> Result<(), Box<dyn std::error::Error>> {
        let store = Store::new()?;
        let mut turtle = String::from(
            "<urn:target> <http://www.w3.org/2004/02/skos/core#prefLabel> \"Target\" .\n",
        );
        for i in 0..(LOOKUP_BUDGET + 10_000) {
            turtle.push_str(&format!("<urn:s{i}> <urn:p> \"v{i}\" .\n"));
        }
        store.load_from_reader(RdfParser::from_format(RdfFormat::Turtle), turtle.as_bytes())?;
        let found = lookup(&store, Some("target"));
        assert_eq!(found.error, None);
        assert_eq!(found.uri.as_deref(), Some("<urn:target>"));
        assert!(lookup(&store, Some("urn:s5")).found);
        assert!(!lookup(&store, Some("v5")).found);
        Ok(())
    }
    #[test]
    fn label_budget_exhaustion_is_an_error() -> Result<(), Box<dyn std::error::Error>> {
        let store = Store::new()?;
        let mut turtle = String::new();
        for i in 0..=LOOKUP_BUDGET {
            turtle.push_str(&format!(
                "<urn:s{i}> <http://www.w3.org/2000/01/rdf-schema#label> \"l{i}\" .\n"
            ));
        }
        store.load_from_reader(RdfParser::from_format(RdfFormat::Turtle), turtle.as_bytes())?;
        assert_eq!(
            lookup(&store, Some("missing")).error.as_deref(),
            Some("lookup_budget_exhausted")
        );
        Ok(())
    }
}
