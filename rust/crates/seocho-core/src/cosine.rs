use pyo3::prelude::*;

/// Compute cosine similarity between two vectors.
/// Returns 0.0 for empty or mismatched-length inputs.
#[pyfunction]
pub fn cosine_similarity(a: Vec<f64>, b: Vec<f64>) -> f64 {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }

    let mut dot = 0.0_f64;
    let mut norm_a = 0.0_f64;
    let mut norm_b = 0.0_f64;

    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }

    if norm_a <= 0.0 || norm_b <= 0.0 {
        return 0.0;
    }

    let result = dot / (norm_a.sqrt() * norm_b.sqrt());
    result.clamp(-1.0, 1.0)
}

/// Compute NxN cosine similarity matrix for a list of vectors.
/// Returns a list of lists (row-major).
#[pyfunction]
pub fn cosine_similarity_matrix(vecs: Vec<Vec<f64>>) -> Vec<Vec<f64>> {
    let n = vecs.len();
    if n == 0 {
        return vec![];
    }

    // Pre-compute norms
    let norms: Vec<f64> = vecs
        .iter()
        .map(|v| {
            let s: f64 = v.iter().map(|x| x * x).sum();
            s.sqrt()
        })
        .collect();

    let mut matrix = vec![vec![0.0_f64; n]; n];

    for i in 0..n {
        matrix[i][i] = 1.0;
        for j in (i + 1)..n {
            if norms[i] <= 0.0 || norms[j] <= 0.0 || vecs[i].len() != vecs[j].len() {
                continue;
            }
            let dot: f64 = vecs[i].iter().zip(vecs[j].iter()).map(|(a, b)| a * b).sum();
            let sim = (dot / (norms[i] * norms[j])).clamp(-1.0, 1.0);
            matrix[i][j] = sim;
            matrix[j][i] = sim;
        }
    }

    matrix
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_identical_vectors() {
        let a = vec![1.0, 0.0, 0.0];
        let b = vec![1.0, 0.0, 0.0];
        assert!((cosine_similarity(a, b) - 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_orthogonal_vectors() {
        let a = vec![1.0, 0.0];
        let b = vec![0.0, 1.0];
        assert!(cosine_similarity(a, b).abs() < 1e-9);
    }

    #[test]
    fn test_opposite_vectors() {
        let a = vec![1.0, 0.0];
        let b = vec![-1.0, 0.0];
        assert!((cosine_similarity(a, b) + 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_empty_vectors() {
        assert_eq!(cosine_similarity(vec![], vec![]), 0.0);
    }

    #[test]
    fn test_mismatched_length() {
        assert_eq!(cosine_similarity(vec![1.0], vec![1.0, 2.0]), 0.0);
    }

    #[test]
    fn test_matrix_3x3() {
        let vecs = vec![
            vec![1.0, 0.0],
            vec![0.0, 1.0],
            vec![1.0, 1.0],
        ];
        let m = cosine_similarity_matrix(vecs);
        assert_eq!(m.len(), 3);
        assert!((m[0][0] - 1.0).abs() < 1e-9);
        assert!(m[0][1].abs() < 1e-9); // orthogonal
        let expected_02 = 1.0 / 2.0_f64.sqrt();
        assert!((m[0][2] - expected_02).abs() < 1e-9);
        assert_eq!(m[0][1], m[1][0]); // symmetric
    }
}
