//! Golden contract fixtures for `seocho.lifecycle.v1` (design I12). Python
//! will replay the same files; changing one is a wire break.

use seocho_lifecycle_proto::{Capability, ErrorCode};
use std::path::PathBuf;

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn fixture(name: &str) -> Result<String, std::io::Error> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("contract/v1")
        .join(name);
    std::fs::read_to_string(path)
}

#[test]
fn legacy_five_field_admission_round_trips() -> TestResult {
    let raw = fixture("capability_legacy.json")?;
    let cap: Capability = serde_json::from_str(&raw)?;
    assert_eq!(cap.fencing_token.get(), 12);
    assert!(
        !cap.is_projection(),
        "a capability without purpose must fail closed"
    );
    let back = serde_json::to_value(&cap)?;
    let original: serde_json::Value = serde_json::from_str(&raw)?;
    assert_eq!(back, original);
    Ok(())
}

#[test]
fn admit_capability_round_trips() -> TestResult {
    let raw = fixture("capability_admit.json")?;
    let cap: Capability = serde_json::from_str(&raw)?;
    assert!(cap.is_projection());
    let back = serde_json::to_value(&cap)?;
    let original: serde_json::Value = serde_json::from_str(&raw)?;
    assert_eq!(back, original);
    Ok(())
}

#[test]
fn error_code_list_is_frozen() -> TestResult {
    let raw = fixture("error_codes.json")?;
    let codes: Vec<ErrorCode> = serde_json::from_str(&raw)?;
    let names: Vec<String> = serde_json::from_str(&raw)?;
    assert_eq!(codes.len(), 15);
    let spelled: Vec<&str> = codes.iter().map(|c| c.as_str()).collect();
    assert_eq!(spelled, names);
    Ok(())
}
