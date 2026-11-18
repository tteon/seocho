use pyo3::prelude::*;

mod cosine;
mod rules;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cosine::cosine_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(cosine::cosine_similarity_matrix, m)?)?;
    m.add_function(wrap_pyfunction!(rules::infer_rules_from_nodes, m)?)?;
    Ok(())
}
