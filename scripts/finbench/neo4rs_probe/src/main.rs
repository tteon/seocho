//! Does a native driver change the picture, and by how much?
//!
//! The Python driver measurement was unambiguous about the *share* and ambiguous about the
//! *stakes*. On a 50,000-row query: 678 ms wall, 21 ms server, so 97% of the time was spent
//! client-side. But discarding the same result without decoding took 11 ms, which locates
//! the cost precisely — it is Python `Record` construction at roughly 13 µs per row, not
//! transport. And the agent path caps results at 50 rows, where that same cost is 0.65 ms
//! against 37 s of end-to-end model time.
//!
//! Two things are therefore true at once, and the ratio alone cannot decide anything. This
//! probe settles it by running the identical queries through neo4rs and reporting the same
//! split, so the comparison is like for like rather than inferred.
//!
//! It measures two distinct things, and the second is the interesting one:
//!
//! 1. **Decode throughput.** Same query, same rows, wall-clock against the Python numbers.
//!    Tells us what a native driver buys on a row-returning workload.
//!
//! 2. **Early abort.** Take 50 rows from a query that would return every account, then drop
//!    the stream. This is the enforcement case: the lazy `LIMIT` behaviour that kept a hub
//!    query at 163 db hits is something the *model* has to remember to emit, and a driver
//!    can apply unconditionally. If aborting really costs only the rows consumed, a bound
//!    can be enforced below the query language — the one thing a query-level guardrail
//!    cannot do.
//!
//! Deliberately not measured: aggregate queries. `count(DISTINCT …)` on a hub returns one row
//! and times out on server work, so no driver changes its outcome. Claiming otherwise would
//! be the most tempting wrong conclusion available here.
//!
//! Usage:
//!   NEO4J_URI=127.0.0.1:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=… NEO4J_DB=finbenchsf1000real \
//!     cargo run --release

use neo4rs::{query, ConfigBuilder, Graph};
use serde_json::json;
use std::time::Instant;

/// Median of a small sample. The Python side reports medians, so this must too — comparing
/// a median against a mean would manufacture a difference that is not there.
fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    if n == 0 {
        return 0.0;
    }
    if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

async fn connect() -> Result<Graph, Box<dyn std::error::Error>> {
    let uri = std::env::var("NEO4J_URI").unwrap_or_else(|_| "127.0.0.1:7687".into());
    let user = std::env::var("NEO4J_USER").unwrap_or_else(|_| "neo4j".into());
    let pass = std::env::var("NEO4J_PASSWORD")?;
    let db = std::env::var("NEO4J_DB").unwrap_or_else(|_| "neo4j".into());
    let cfg = ConfigBuilder::default()
        .uri(uri)
        .user(user)
        .password(pass)
        .db(db)
        .build()?;
    Ok(Graph::connect(cfg).await?)
}

/// Decode every row, so the measurement includes the per-row cost Python pays. Reading the
/// column rather than only counting rows matters: skipping the `get` would measure streaming
/// and then call it decoding.
async fn decode_all(graph: &Graph, cypher: &str) -> Result<(usize, f64), neo4rs::Error> {
    let t0 = Instant::now();
    let mut rows = graph.execute(query(cypher)).await?;
    let mut n = 0usize;
    let mut sink: i64 = 0;
    while let Ok(Some(row)) = rows.next().await {
        if let Ok(v) = row.get::<i64>("v") {
            sink = sink.wrapping_add(v);
        }
        n += 1;
    }
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    std::hint::black_box(sink);
    Ok((n, ms))
}

/// Stream without touching columns: separates decode from transport, the same split that
/// located the Python cost (11 ms discarded against 678 ms decoded).
async fn stream_no_decode(graph: &Graph, cypher: &str) -> Result<(usize, f64), neo4rs::Error> {
    let t0 = Instant::now();
    let mut rows = graph.execute(query(cypher)).await?;
    let mut n = 0usize;
    while let Ok(Some(_row)) = rows.next().await {
        n += 1;
    }
    Ok((n, t0.elapsed().as_secs_f64() * 1000.0))
}

/// Take `take` rows and drop the stream. The enforcement case: a bound applied below the
/// query language, on a query whose text contains no `LIMIT` for a guardrail to inspect.
async fn early_abort(
    graph: &Graph,
    cypher: &str,
    take: usize,
) -> Result<(usize, f64), neo4rs::Error> {
    let t0 = Instant::now();
    let mut rows = graph.execute(query(cypher)).await?;
    let mut n = 0usize;
    let mut sink: i64 = 0;
    while n < take {
        match rows.next().await {
            Ok(Some(row)) => {
                if let Ok(v) = row.get::<i64>("v") {
                    sink = sink.wrapping_add(v);
                }
                n += 1;
            }
            _ => break,
        }
    }
    // Dropping here is the abort. If the server keeps producing regardless, this number will
    // not differ from a full decode and the enforcement idea is dead — which is the point of
    // measuring it rather than assuming it.
    drop(rows);
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    std::hint::black_box(sink);
    Ok((n, ms))
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let graph = connect().await?;
    let repeats: usize = std::env::var("REPEATS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(7);

    // Discarded warm pass: the first query pays connection and routing setup, which would
    // otherwise be attributed to decode.
    let _ = decode_all(&graph, "RETURN 1 AS v").await?;

    let q1k = "MATCH (a:Account) RETURN a.acct_no AS v LIMIT 1000";
    let q50k = "MATCH (a:Account) RETURN a.acct_no AS v LIMIT 50000";
    // No LIMIT in the text at all — what a driver-level bound has to handle, since there is
    // nothing here for a query-level guardrail to find.
    let q_unbounded = "MATCH (a:Account) RETURN a.acct_no AS v";

    let mut out = Vec::new();

    for (name, cypher) in [("rows_1k", q1k), ("rows_50k", q50k)] {
        let mut decoded = Vec::new();
        let mut rows = 0usize;
        for _ in 0..repeats {
            let (n, t) = decode_all(&graph, cypher).await?;
            rows = n;
            decoded.push(t);
        }
        let mut streamed = Vec::new();
        for _ in 0..repeats {
            let (_n, t) = stream_no_decode(&graph, cypher).await?;
            streamed.push(t);
        }
        let dec = median(decoded);
        out.push(json!({
            "probe": name,
            "rows": rows,
            "decode_ms": round3(dec),
            "stream_only_ms": round3(median(streamed)),
            "per_row_us": if rows > 0 { round3(dec * 1000.0 / rows as f64) } else { 0.0 },
        }));
    }

    // Early abort against the same query decoded in full, so the result is a ratio rather
    // than an absolute nobody can calibrate.
    let mut abort_ms = Vec::new();
    for _ in 0..repeats {
        let (_n, t) = early_abort(&graph, q_unbounded, 50).await?;
        abort_ms.push(t);
    }
    let mut full_ms = Vec::new();
    for _ in 0..repeats {
        let (_n, t) = decode_all(&graph, q50k).await?;
        full_ms.push(t);
    }
    let abort = median(abort_ms);
    let full = median(full_ms);
    out.push(json!({
        "probe": "early_abort_50_of_unbounded",
        "note": "query text carries no LIMIT; the bound is applied by the client",
        "abort_ms": round3(abort),
        "full_50k_decode_ms": round3(full),
        "saving_ratio": if abort > 0.0 { round3(full / abort) } else { 0.0 },
    }));

    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "schema_version": "seocho.finbench.neo4rs-probe.v1",
            "driver": "neo4rs 0.8.0",
            "database": std::env::var("NEO4J_DB").unwrap_or_default(),
            "repeats": repeats,
            "probes": out,
        }))?
    );
    Ok(())
}
