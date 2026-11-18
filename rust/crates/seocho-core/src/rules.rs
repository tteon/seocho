//! Rule inference, kept byte-identical to the Python reference in
//! `python/seocho_core/_fallback.py`.
//!
//! Parity notes: input is parsed into an insertion-ordered value model
//! (`PyValue`) instead of `serde_json::Value`, because serde_json's default
//! `Map` is a sorted `BTreeMap` and Python dicts keep insertion order. Output
//! is written by `write_py_json`, which mirrors `json.dumps` defaults
//! (`", "` / `": "` separators, `ensure_ascii=True`, `float.__repr__`).
use pyo3::prelude::*;
use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::collections::{HashMap, HashSet};
use std::fmt;

/// A decoded JSON value with Python `json.loads` semantics: int and float
/// stay distinct (so `3.0` is a float) and object keys keep first-seen order
/// (a duplicate key overwrites the value in place, like dict assignment).
#[derive(Clone, Debug, PartialEq)]
enum PyValue {
    Null,
    Bool(bool),
    Int(i128),
    Float(f64),
    Str(String),
    List(Vec<PyValue>),
    Object(Vec<(String, PyValue)>),
}

impl PyValue {
    /// Python truthiness, used for `node.get(...) or default`.
    fn is_truthy(&self) -> bool {
        match self {
            PyValue::Null => false,
            PyValue::Bool(b) => *b,
            PyValue::Int(i) => *i != 0,
            PyValue::Float(f) => *f != 0.0,
            PyValue::Str(s) => !s.is_empty(),
            PyValue::List(v) => !v.is_empty(),
            PyValue::Object(v) => !v.is_empty(),
        }
    }

    fn get(&self, key: &str) -> Option<&PyValue> {
        match self {
            PyValue::Object(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
}

struct PyValueVisitor;

impl<'de> Visitor<'de> for PyValueVisitor {
    type Value = PyValue;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("any JSON value")
    }
    fn visit_unit<E>(self) -> Result<PyValue, E> {
        Ok(PyValue::Null)
    }
    fn visit_none<E>(self) -> Result<PyValue, E> {
        Ok(PyValue::Null)
    }
    fn visit_bool<E>(self, v: bool) -> Result<PyValue, E> {
        Ok(PyValue::Bool(v))
    }
    fn visit_i64<E>(self, v: i64) -> Result<PyValue, E> {
        Ok(PyValue::Int(i128::from(v)))
    }
    fn visit_u64<E>(self, v: u64) -> Result<PyValue, E> {
        Ok(PyValue::Int(i128::from(v)))
    }
    fn visit_f64<E>(self, v: f64) -> Result<PyValue, E> {
        Ok(PyValue::Float(v))
    }
    fn visit_str<E>(self, v: &str) -> Result<PyValue, E> {
        Ok(PyValue::Str(v.to_owned()))
    }
    fn visit_string<E>(self, v: String) -> Result<PyValue, E> {
        Ok(PyValue::Str(v))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<PyValue, A::Error> {
        let mut items = Vec::new();
        while let Some(item) = seq.next_element::<PyValue>()? {
            items.push(item);
        }
        Ok(PyValue::List(items))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<PyValue, A::Error> {
        let mut entries: Vec<(String, PyValue)> = Vec::new();
        let mut index: HashMap<String, usize> = HashMap::new();
        while let Some((key, value)) = map.next_entry::<String, PyValue>()? {
            match index.get(&key) {
                Some(&i) => entries[i].1 = value,
                None => {
                    index.insert(key.clone(), entries.len());
                    entries.push((key, value));
                }
            }
        }
        Ok(PyValue::Object(entries))
    }
}

impl<'de> Deserialize<'de> for PyValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<PyValue, D::Error> {
        deserializer.deserialize_any(PyValueVisitor)
    }
}

struct PropertyStats {
    total: usize,
    nonnull_values: Vec<PyValue>,
}

/// Infer rules from a JSON array of nodes.
/// Input: JSON string of nodes array (same format as extracted_data["nodes"]).
/// Output: JSON string of rules array, byte-identical to the Python fallback.
///
/// Parameters `required_threshold` and `enum_max_size` control inference sensitivity.
#[pyfunction]
#[pyo3(signature = (nodes_json, required_threshold=0.98, enum_max_size=20))]
pub fn infer_rules_from_nodes(
    nodes_json: &str,
    required_threshold: f64,
    enum_max_size: usize,
) -> PyResult<String> {
    infer_rules_json(nodes_json, required_threshold, enum_max_size)
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

fn infer_rules_json(
    nodes_json: &str,
    required_threshold: f64,
    enum_max_size: usize,
) -> Result<String, String> {
    let parsed: PyValue =
        serde_json::from_str(nodes_json).map_err(|e| format!("Invalid JSON: {e}"))?;
    let nodes = match parsed {
        PyValue::List(nodes) => nodes,
        _ => return Err("Invalid input: expected a JSON array of nodes".to_string()),
    };

    // Bucket: (label, property_name) -> stats, in first-seen order like a
    // Python dict. `index` maps a key to its slot in `buckets`.
    let mut buckets: Vec<((String, String), PropertyStats)> = Vec::new();
    let mut index: HashMap<(String, String), usize> = HashMap::new();

    for node in &nodes {
        if !matches!(node, PyValue::Object(_)) {
            return Err("Invalid input: each node must be a JSON object".to_string());
        }
        // `(node.get("label") or "").strip()`
        let label = match node.get("label") {
            Some(PyValue::Str(s)) => py_strip(s),
            Some(v) if v.is_truthy() => {
                return Err("Invalid input: node label must be a string".to_string())
            }
            _ => continue,
        };
        if label.is_empty() {
            continue;
        }
        // `node.get("properties") or {}`
        let props = match node.get("properties") {
            Some(PyValue::Object(entries)) => entries,
            Some(v) if v.is_truthy() => {
                return Err("Invalid input: node properties must be an object".to_string())
            }
            _ => continue,
        };
        for (key, value) in props {
            let bucket_key = (label.to_string(), key.clone());
            let slot = match index.get(&bucket_key) {
                Some(&i) => i,
                None => {
                    index.insert(bucket_key.clone(), buckets.len());
                    buckets.push((
                        bucket_key,
                        PropertyStats {
                            total: 0,
                            nonnull_values: Vec::new(),
                        },
                    ));
                    buckets.len() - 1
                }
            };
            let stats = &mut buckets[slot].1;
            stats.total += 1;
            if *value != PyValue::Null {
                stats.nonnull_values.push(value.clone());
            }
        }
    }

    let mut rules: Vec<PyValue> = Vec::new();
    let mut push_rule = |label: &str, prop_name: &str, kind: &str, params: Vec<(&str, PyValue)>| {
        rules.push(PyValue::Object(vec![
            ("label".to_string(), PyValue::Str(label.to_string())),
            ("property_name".to_string(), PyValue::Str(prop_name.to_string())),
            ("kind".to_string(), PyValue::Str(kind.to_string())),
            (
                "params".to_string(),
                PyValue::Object(params.into_iter().map(|(k, v)| (k.to_string(), v)).collect()),
            ),
        ]));
    };

    for ((label, prop_name), stats) in &buckets {
        let total = stats.total;
        if total == 0 {
            continue;
        }
        let nonnull = &stats.nonnull_values;
        let completeness = nonnull.len() as f64 / total as f64;

        // Required rule
        if completeness >= required_threshold {
            push_rule(label, prop_name, "required", vec![("minCount", PyValue::Int(1))]);
        }

        // Datatype rule
        if let Some(dominant) = infer_dominant_type(nonnull) {
            push_rule(
                label,
                prop_name,
                "datatype",
                vec![("datatype", PyValue::Str(dominant.to_string()))],
            );
        }

        // Enum rule; `int(total * 0.2)` truncates toward zero like `as usize`.
        let unique = dedupe_values(nonnull);
        let max_enum = std::cmp::max(2, (total as f64 * 0.2) as usize);
        if !unique.is_empty() && unique.len() <= enum_max_size && unique.len() <= max_enum {
            push_rule(label, prop_name, "enum", vec![("allowedValues", PyValue::List(unique))]);
        }

        // Range rule; Python applies float() to both ends, so 30 -> 30.0.
        if let Some((min_val, max_val)) = infer_numeric_range(nonnull) {
            push_rule(
                label,
                prop_name,
                "range",
                vec![
                    ("minInclusive", PyValue::Float(min_val)),
                    ("maxInclusive", PyValue::Float(max_val)),
                ],
            );
        }
    }

    let mut out = String::new();
    write_py_json(&PyValue::List(rules), &mut out);
    Ok(out)
}

/// Python `str.strip()`: `str.isspace()` also covers U+001C..U+001F, which
/// Rust's `char::is_whitespace` does not.
fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

fn type_name(v: &PyValue) -> &'static str {
    match v {
        PyValue::Bool(_) => "boolean",
        PyValue::Int(_) => "integer",
        PyValue::Float(_) => "number",
        _ => "string",
    }
}

/// `max(counts.items(), key=count)` over an insertion-ordered dict: the
/// first-seen kind wins a tie.
fn infer_dominant_type(values: &[PyValue]) -> Option<&'static str> {
    let mut counts: Vec<(&'static str, usize)> = Vec::new();
    for v in values {
        let kind = type_name(v);
        match counts.iter_mut().find(|(k, _)| *k == kind) {
            Some(entry) => entry.1 += 1,
            None => counts.push((kind, 1)),
        }
    }
    let mut best: Option<(&'static str, usize)> = None;
    for (kind, count) in counts {
        if best.is_none_or(|(_, c)| count > c) {
            best = Some((kind, count));
        }
    }
    best.map(|(k, _)| k)
}

/// `min(nums), max(nums)` with Python's keep-first comparison order, which
/// only matters for signed zeros.
fn infer_numeric_range(values: &[PyValue]) -> Option<(f64, f64)> {
    let mut nums = values.iter().filter_map(|v| match v {
        PyValue::Int(i) => Some(*i as f64),
        PyValue::Float(f) => Some(*f),
        _ => None,
    });
    let first = nums.next()?;
    let (mut min, mut max) = (first, first);
    for n in nums {
        if n < min {
            min = n;
        }
        if n > max {
            max = n;
        }
    }
    Some((min, max))
}

/// Dedupe by `repr(v)`. The Python-style JSON text is an equally injective
/// marker (int vs float vs bool vs str stay distinct; dict order matters).
fn dedupe_values(values: &[PyValue]) -> Vec<PyValue> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for v in values {
        let mut key = String::new();
        write_py_json(v, &mut key);
        if seen.insert(key) {
            result.push(v.clone());
        }
    }
    result
}

/// Serialize like Python `json.dumps(value)` with default arguments.
fn write_py_json(v: &PyValue, out: &mut String) {
    match v {
        PyValue::Null => out.push_str("null"),
        PyValue::Bool(true) => out.push_str("true"),
        PyValue::Bool(false) => out.push_str("false"),
        PyValue::Int(i) => out.push_str(&i.to_string()),
        PyValue::Float(f) => out.push_str(&py_float_repr(*f)),
        PyValue::Str(s) => write_py_str(s, out),
        PyValue::List(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py_json(item, out);
            }
            out.push(']');
        }
        PyValue::Object(entries) => {
            out.push('{');
            for (i, (k, item)) in entries.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py_str(k, out);
                out.push_str(": ");
                write_py_json(item, out);
            }
            out.push('}');
        }
    }
}

/// `ensure_ascii=True` string encoding: everything outside printable ASCII
/// becomes `\uXXXX` (lowercase hex, UTF-16 surrogate pairs above the BMP).
fn write_py_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// Python `float.__repr__` (as used by `json.dumps`): shortest round-trip
/// digits, fixed notation when the decimal exponent is in [-4, 16), else
/// `d.ddde+XX` with at least two exponent digits.
fn py_float_repr(f: f64) -> String {
    if f.is_nan() {
        return "NaN".to_string();
    }
    if f.is_infinite() {
        return if f > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    let sign = if f.is_sign_negative() { "-" } else { "" };
    // `{:e}` yields the shortest round-trip digits, e.g. "1.5e16", "0e0".
    let sci = format!("{:e}", f.abs());
    let (mantissa, exp) = match sci.split_once('e') {
        Some((m, e)) => (m, e.parse::<i32>().unwrap_or(0)),
        None => (sci.as_str(), 0),
    };
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let n = digits.len() as i32;
    let body = if (-4..16).contains(&exp) {
        // Position of the decimal point within the digit string.
        let decpt = exp + 1;
        if decpt <= 0 {
            format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
        } else if decpt < n {
            format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
        } else {
            format!("{}{}.0", digits, "0".repeat((decpt - n) as usize))
        }
    } else {
        let tail = if n > 1 {
            format!("{}.{}", &digits[..1], &digits[1..])
        } else {
            digits.clone()
        };
        let esign = if exp < 0 { '-' } else { '+' };
        format!("{}e{}{:02}", tail, esign, exp.abs())
    };
    format!("{sign}{body}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_inference() -> Result<(), Box<dyn std::error::Error>> {
        let nodes = r#"[
            {"label": "Person", "properties": {"name": "Alice", "age": 30}},
            {"label": "Person", "properties": {"name": "Bob", "age": 25}}
        ]"#;
        let result = infer_rules_from_nodes(nodes, 0.98, 20).map_err(|e| e.to_string())?;
        let rules: Vec<serde_json::Value> = serde_json::from_str(&result)?;
        assert!(!rules.is_empty());

        // Should have required + datatype rules for both name and age
        let required_rules: Vec<_> = rules.iter().filter(|r| r["kind"] == "required").collect();
        assert!(required_rules.len() >= 2); // name and age both 100% complete
        Ok(())
    }

    #[test]
    fn test_empty_input() -> Result<(), String> {
        let result = infer_rules_from_nodes("[]", 0.98, 20).map_err(|e| e.to_string())?;
        assert_eq!(result, "[]");
        Ok(())
    }

    #[test]
    fn test_invalid_json() {
        let result = infer_rules_from_nodes("not json", 0.98, 20);
        assert!(result.is_err());
    }

    #[test]
    fn test_py_float_repr() {
        let cases = [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (30.0, "30.0"),
            (2.5, "2.5"),
            (0.1, "0.1"),
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.2345e20, "1.2345e+20"),
            (123456789.125, "123456789.125"),
            (1e300, "1e+300"),
            (f64::MAX, "1.7976931348623157e+308"),
            (5e-324, "5e-324"),
        ];
        for (f, want) in cases {
            assert_eq!(py_float_repr(f), want, "repr of {f:e}");
        }
    }

    /// Golden fixture shared with `python/tests/test_parity.py`.
    #[test]
    fn test_parity_fixture() -> Result<(), Box<dyn std::error::Error>> {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../tests/fixtures/rules_parity.json"))?;
        let cases = fixture["cases"].as_array().ok_or("fixture has no cases")?;
        assert!(!cases.is_empty());
        for case in cases {
            let name = case["name"].as_str().ok_or("case without name")?;
            let input = case["input"].as_str().ok_or("case without input")?;
            let expected = case["expected"].as_str().ok_or("case without expected")?;
            let threshold = case["required_threshold"].as_f64().unwrap_or(0.98);
            let enum_max = case["enum_max_size"].as_u64().unwrap_or(20) as usize;
            let got = infer_rules_json(input, threshold, enum_max)?;
            assert_eq!(got, expected, "parity case {name}");
        }
        Ok(())
    }
}
