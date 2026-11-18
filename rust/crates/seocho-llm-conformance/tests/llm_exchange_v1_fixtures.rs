//! `seocho.llm_exchange.v1` golden fixtures are consumable from Rust.
//!
//! Parses every fixture under `spec/fixtures/`, recomputes `response.body_sha256` from the
//! stored bytes and `rec_id` from `run_id||logical_call_id||attempt||body_sha256` (spec §4;
//! records without `logical_call_id` append `||req_hash||occurrence||agent`), and checks both
//! against the fixture and `expected.json`. Uses only dev-dependencies that
//! are already in the workspace lockfile (`serde_json`, `sha2`, `hex`).

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn spec_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("spec")
}

fn sha256_tag(data: &[u8]) -> String {
    format!("sha256:{}", hex::encode(Sha256::digest(data)))
}

/// Standard base64 (RFC 4648, padded) decoder; no base64 crate offline.
fn b64_decode(s: &str) -> Result<Vec<u8>, String> {
    let mut out = Vec::with_capacity(s.len() / 4 * 3);
    let mut buf = 0u32;
    let mut bits = 0u32;
    let trimmed = s.trim_end_matches('=');
    if !s.len().is_multiple_of(4) || s.len() - trimmed.len() > 2 {
        return Err(format!("bad base64 length/padding: {}", s.len()));
    }
    for c in trimmed.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return Err(format!("bad base64 byte {c:#x}")),
        };
        buf = (buf << 6) | u32::from(v);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push(((buf >> bits) & 0xff) as u8);
        }
    }
    Ok(out)
}

/// `null` / absent -> "", strings verbatim, integers in decimal (spec §4).
fn part(v: Option<&Value>) -> Result<String, String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Number(n)) if n.is_u64() => Ok(n.to_string()),
        Some(other) => Err(format!(
            "rec_id component must be string/int/null, got {other}"
        )),
    }
}

fn body_bytes(resp: &Value) -> Result<Option<Vec<u8>>, String> {
    match (resp.get("body_utf8"), resp.get("body_b64")) {
        (Some(Value::String(_)), Some(Value::String(_))) => {
            Err("both body_utf8 and body_b64".into())
        }
        (Some(Value::String(s)), _) => Ok(Some(s.as_bytes().to_vec())),
        (_, Some(Value::String(b))) => b64_decode(b).map(Some),
        _ => Ok(None),
    }
}

/// Spec §4. A null `logical_call_id` (imports only) appends the §5 fallback observation identity
/// `req_hash||occurrence||agent`, so `rec_id` is unique whenever the fallback replay key is.
fn rec_id(rec: &Value) -> Result<String, String> {
    let mut parts = vec![
        part(rec.pointer("/source/run_id"))?,
        part(rec.pointer("/chain/logical_call_id"))?,
        part(rec.pointer("/chain/attempt"))?,
        part(rec.pointer("/response/body_sha256"))?,
    ];
    if matches!(
        rec.pointer("/chain/logical_call_id"),
        None | Some(Value::Null)
    ) {
        parts.push(part(rec.pointer("/request/req_hash"))?);
        parts.push(part(rec.pointer("/request/occurrence"))?);
        parts.push(part(rec.pointer("/chain/agent"))?);
    }
    Ok(sha256_tag(parts.join("||").as_bytes()))
}

fn fixtures() -> Result<Vec<PathBuf>, std::io::Error> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(spec_dir().join("fixtures"))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    Ok(paths)
}

#[test]
fn every_fixture_parses_and_hashes_recompute() -> TestResult {
    let expected: Value =
        serde_json::from_str(&std::fs::read_to_string(spec_dir().join("expected.json"))?)?;
    let paths = fixtures()?;
    assert!(
        paths.len() >= 8,
        "need at least 8 golden fixtures, found {}",
        paths.len()
    );
    for path in &paths {
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or("bad file name")?;
        let rec: Value = serde_json::from_str(&std::fs::read_to_string(path)?)?;
        assert_eq!(rec["schema_version"], "seocho.llm_exchange.v1", "{name}");
        let resp = &rec["response"];
        let body = body_bytes(resp)?;
        let body_sha = body.as_deref().map(sha256_tag);
        assert_eq!(
            resp["body_sha256"].as_str().map(str::to_owned),
            body_sha,
            "{name}: body_sha256"
        );
        let want = rec_id(&rec)?;
        assert_eq!(
            rec["rec_id"].as_str(),
            Some(want.as_str()),
            "{name}: rec_id"
        );
        let exp = &expected["fixtures"][name];
        assert_eq!(
            exp["rec_id"].as_str(),
            Some(want.as_str()),
            "{name}: expected.json rec_id"
        );
        assert_eq!(
            exp["body_sha256"], resp["body_sha256"],
            "{name}: expected.json body_sha256"
        );
        assert_eq!(
            exp["replay_class"], rec["redaction"]["replay_class"],
            "{name}"
        );
        assert_eq!(exp["replayable"], rec["redaction"]["replayable"], "{name}");
        // structural invariants a Rust consumer relies on
        let replayable = rec["redaction"]["replayable"]
            .as_bool()
            .ok_or("replayable")?;
        assert_eq!(
            replayable,
            rec["redaction"]["replay_class"] != "none",
            "{name}: S7"
        );
        if let Some(headers) = resp["headers"].as_object() {
            for k in headers.keys() {
                assert!(
                    k == "content-type" || k == "x-request-id",
                    "{name}: header {k} outside allowlist"
                );
            }
        }
    }
    let listed = expected["fixtures"]
        .as_object()
        .ok_or("fixtures map")?
        .len();
    assert_eq!(listed, paths.len(), "expected.json lists every fixture");
    Ok(())
}

#[test]
fn null_logical_call_id_rec_id_includes_fallback_identity() -> TestResult {
    // Two imported observations without logical_call_id that differ only in request.occurrence
    // (same run, attempt, agent, req_hash and body) have distinct fallback replay keys, so they
    // must have distinct rec_ids.
    let mut a: Value = serde_json::from_str(&std::fs::read_to_string(
        spec_dir().join("fixtures/08-legacy-502-truncated.json"),
    )?)?;
    a["chain"]["logical_call_id"] = Value::Null;
    a["request"]["occurrence"] = 0.into();
    let mut b = a.clone();
    b["request"]["occurrence"] = 1.into();
    let (id_a, id_b) = (rec_id(&a)?, rec_id(&b)?);
    assert_ne!(id_a, id_b);
    let material = format!(
        "{}||||{}||{}||{}||0||{}",
        a["source"]["run_id"].as_str().ok_or("run_id")?,
        a["chain"]["attempt"],
        a["response"]["body_sha256"].as_str().ok_or("body_sha256")?,
        a["request"]["req_hash"].as_str().ok_or("req_hash")?,
        a["chain"]["agent"].as_str().ok_or("agent")?,
    );
    assert_eq!(id_a, sha256_tag(material.as_bytes()));
    // with a logical_call_id the four-part form is unchanged (fallback parts are not appended)
    let fixed: Value = serde_json::from_str(&std::fs::read_to_string(
        spec_dir().join("fixtures/08-legacy-502-truncated.json"),
    )?)?;
    assert_eq!(fixed["rec_id"].as_str(), Some(rec_id(&fixed)?.as_str()));
    Ok(())
}

#[test]
fn raw_bodies_keep_wire_bytes() -> TestResult {
    // 13 is not UTF-8 and must round-trip through body_b64 byte for byte
    let rec: Value = serde_json::from_str(&std::fs::read_to_string(
        spec_dir().join("fixtures/13-non-utf8-body-b64.json"),
    )?)?;
    let body = body_bytes(&rec["response"])?.ok_or("no body")?;
    assert!(std::str::from_utf8(&body).is_err());
    assert!(body.contains(&0xe9));
    Ok(())
}

#[test]
fn base64_decoder_matches_rfc4648_vectors() -> TestResult {
    for (enc, dec) in [
        ("", ""),
        ("Zg==", "f"),
        ("Zm8=", "fo"),
        ("Zm9v", "foo"),
        ("Zm9vYmFy", "foobar"),
    ] {
        assert_eq!(b64_decode(enc)?, dec.as_bytes());
    }
    assert!(b64_decode("Zg=").is_err());
    Ok(())
}
