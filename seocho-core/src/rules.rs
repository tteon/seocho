use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Serialize)]
struct RuleOutput {
    label: String,
    property_name: String,
    kind: String,
    params: serde_json::Value,
}

#[derive(Deserialize)]
struct Node {
    label: Option<String>,
    properties: Option<HashMap<String, serde_json::Value>>,
}

struct PropertyStats {
    values: Vec<serde_json::Value>,
    nonnull_values: Vec<serde_json::Value>,
}

/// Infer rules from a JSON array of nodes.
/// Input: JSON string of nodes array (same format as extracted_data["nodes"]).
/// Output: JSON string of rules array.
///
/// Parameters `required_threshold` and `enum_max_size` control inference sensitivity.
#[pyfunction]
#[pyo3(signature = (nodes_json, required_threshold=0.98, enum_max_size=20))]
pub fn infer_rules_from_nodes(
    nodes_json: &str,
    required_threshold: f64,
    enum_max_size: usize,
) -> PyResult<String> {
    let nodes: Vec<Node> = serde_json::from_str(nodes_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid JSON: {e}")))?;

    // Bucket: (label, property_name) -> stats
    let mut buckets: HashMap<(String, String), PropertyStats> = HashMap::new();

    for node in &nodes {
        let label = match &node.label {
            Some(l) if !l.is_empty() => l.clone(),
            _ => continue,
        };
        let props = match &node.properties {
            Some(p) => p,
            None => continue,
        };
        for (key, value) in props {
            let stats = buckets
                .entry((label.clone(), key.clone()))
                .or_insert_with(|| PropertyStats {
                    values: Vec::new(),
                    nonnull_values: Vec::new(),
                });
            stats.values.push(value.clone());
            if !value.is_null() {
                stats.nonnull_values.push(value.clone());
            }
        }
    }

    let mut rules: Vec<RuleOutput> = Vec::new();

    for ((label, prop_name), stats) in &buckets {
        let total = stats.values.len();
        if total == 0 {
            continue;
        }
        let nonnull_count = stats.nonnull_values.len();
        let completeness = nonnull_count as f64 / total as f64;

        // Required rule
        if completeness >= required_threshold {
            rules.push(RuleOutput {
                label: label.clone(),
                property_name: prop_name.clone(),
                kind: "required".to_string(),
                params: serde_json::json!({"minCount": 1}),
            });
        }

        // Datatype rule
        if let Some(dominant) = infer_dominant_type(&stats.nonnull_values) {
            rules.push(RuleOutput {
                label: label.clone(),
                property_name: prop_name.clone(),
                kind: "datatype".to_string(),
                params: serde_json::json!({"datatype": dominant}),
            });
        }

        // Enum rule
        let unique = dedupe_values(&stats.nonnull_values);
        let max_enum = std::cmp::max(2, (total as f64 * 0.2) as usize);
        if !unique.is_empty() && unique.len() <= enum_max_size && unique.len() <= max_enum {
            rules.push(RuleOutput {
                label: label.clone(),
                property_name: prop_name.clone(),
                kind: "enum".to_string(),
                params: serde_json::json!({"allowedValues": unique}),
            });
        }

        // Range rule
        if let Some((min_val, max_val)) = infer_numeric_range(&stats.nonnull_values) {
            rules.push(RuleOutput {
                label: label.clone(),
                property_name: prop_name.clone(),
                kind: "range".to_string(),
                params: serde_json::json!({"minInclusive": min_val, "maxInclusive": max_val}),
            });
        }
    }

    serde_json::to_string(&rules)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization error: {e}")))
}

fn infer_dominant_type(values: &[serde_json::Value]) -> Option<String> {
    if values.is_empty() {
        return None;
    }
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for v in values {
        let kind = match v {
            serde_json::Value::Bool(_) => "boolean",
            serde_json::Value::Number(n) => {
                if n.is_f64() && n.as_f64().map_or(false, |f| f.fract() != 0.0) {
                    "number"
                } else {
                    "integer"
                }
            }
            _ => "string",
        };
        *counts.entry(kind).or_insert(0) += 1;
    }
    counts
        .into_iter()
        .max_by_key(|(_, c)| *c)
        .map(|(k, _)| k.to_string())
}

fn infer_numeric_range(values: &[serde_json::Value]) -> Option<(f64, f64)> {
    let nums: Vec<f64> = values
        .iter()
        .filter_map(|v| match v {
            serde_json::Value::Bool(_) => None,
            serde_json::Value::Number(n) => n.as_f64(),
            _ => None,
        })
        .collect();
    if nums.is_empty() {
        return None;
    }
    let min = nums.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = nums.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    Some((min, max))
}

fn dedupe_values(values: &[serde_json::Value]) -> Vec<serde_json::Value> {
    let mut seen = std::collections::HashSet::new();
    let mut result = Vec::new();
    for v in values {
        let key = v.to_string();
        if seen.insert(key) {
            result.push(v.clone());
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_inference() {
        let nodes = r#"[
            {"label": "Person", "properties": {"name": "Alice", "age": 30}},
            {"label": "Person", "properties": {"name": "Bob", "age": 25}}
        ]"#;
        let result = infer_rules_from_nodes(nodes, 0.98, 20).unwrap();
        let rules: Vec<serde_json::Value> = serde_json::from_str(&result).unwrap();
        assert!(!rules.is_empty());

        // Should have required + datatype rules for both name and age
        let required_rules: Vec<_> = rules.iter().filter(|r| r["kind"] == "required").collect();
        assert!(required_rules.len() >= 2); // name and age both 100% complete
    }

    #[test]
    fn test_empty_input() {
        let result = infer_rules_from_nodes("[]", 0.98, 20).unwrap();
        assert_eq!(result, "[]");
    }

    #[test]
    fn test_invalid_json() {
        let result = infer_rules_from_nodes("not json", 0.98, 20);
        assert!(result.is_err());
    }
}
