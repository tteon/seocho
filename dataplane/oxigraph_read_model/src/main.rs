//! Local RDF ontology read model. It deliberately owns no canonical writes.

use oxigraph::io::{RdfFormat, RdfParser};
use oxigraph::store::Store;
use serde::{Deserialize, Serialize};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;

#[derive(Deserialize)]
struct Config { socket: String, turtle: String, bundle_sha256: String }

#[derive(Deserialize)]
struct Request { op: String, term: Option<String>, workspace_id: Option<String>, ontology_context_hash: Option<String> }

#[derive(Serialize)]
struct Response { ok: bool, found: bool, uri: Option<String>, label: Option<String>, definition: Option<String>, bundle_sha256: String, error: Option<String> }

fn load_store(turtle: &str) -> Result<Store, String> {
    let store = Store::new().map_err(|e| e.to_string())?;
    let file = File::open(turtle).map_err(|e| e.to_string())?;
    store.load_from_reader(RdfParser::from_format(RdfFormat::Turtle), file)
        .map_err(|e| e.to_string())?;
    Ok(store)
}

fn handle(store: &Store, req: Request, bundle_sha256: &str) -> Response {
    let _request_receipt = (&req.workspace_id, &req.ontology_context_hash);
    if req.op == "health" {
        return Response { ok: true, found: true, uri: None, label: None, definition: None, bundle_sha256: bundle_sha256.into(), error: None };
    }
    if req.op != "term" || req.term.as_deref().unwrap_or("").len() > 512 {
        return Response { ok: false, found: false, uri: None, label: None, definition: None, bundle_sha256: bundle_sha256.into(), error: Some("invalid request".into()) };
    }
    // RDF terms are read from the local ontology graph only. Workspace/context
    // are protocol receipts used by the Python control plane; they never become
    // an RDF authorization mechanism.
    let needle = req.term.unwrap().to_ascii_lowercase();
    for quad in store.quads_for_pattern(None, None, None, None) {
        let Ok(quad) = quad else { continue };
        let subject = quad.subject.to_string();
        let object = quad.object.to_string();
        if subject.to_ascii_lowercase().contains(&needle) || object.to_ascii_lowercase().contains(&needle) {
            return Response { ok: true, found: true, uri: Some(subject), label: Some(object), definition: None, bundle_sha256: bundle_sha256.into(), error: None };
        }
    }
    Response { ok: true, found: false, uri: None, label: None, definition: None, bundle_sha256: bundle_sha256.into(), error: None }
}

fn serve_connection(mut stream: UnixStream, store: &Store, digest: &str) {
    let mut line = String::new();
    let response = match BufReader::new(&stream).read_line(&mut line) {
        Ok(_) if line.len() <= 16_384 => match serde_json::from_str::<Request>(&line) {
            Ok(request) => handle(store, request, digest),
            Err(_) => Response { ok: false, found: false, uri: None, label: None, definition: None, bundle_sha256: digest.into(), error: Some("invalid json".into()) },
        },
        _ => Response { ok: false, found: false, uri: None, label: None, definition: None, bundle_sha256: digest.into(), error: Some("invalid request".into()) },
    };
    let _ = writeln!(stream, "{}", serde_json::to_string(&response).unwrap());
}

fn main() -> Result<(), String> {
    let config_path = std::env::args().nth(1).ok_or("usage: seocho-oxigraph-read-model <config.json>")?;
    let config: Config = serde_json::from_reader(File::open(config_path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let socket_path = Path::new(&config.socket);
    if socket_path.exists() { fs::remove_file(socket_path).map_err(|e| e.to_string())?; }
    let store = Arc::new(load_store(&config.turtle)?);
    let listener = UnixListener::bind(socket_path).map_err(|e| e.to_string())?;
    for stream in listener.incoming() {
        if let Ok(stream) = stream { serve_connection(stream, &store, &config.bundle_sha256); }
    }
    Ok(())
}
