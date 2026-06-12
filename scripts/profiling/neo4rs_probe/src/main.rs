//! W2 bulk-scan reference probe: full node+rel reads from a local DozerDB.
//!
//! Caveats (recorded in the ADR): rows are iterated and one string column is
//! extracted per row, but property maps are not converted to native Rust
//! structures — this UNDER-hydrates relative to the Python arms' `.data()`,
//! so treat the number as a ceiling, not a like-for-like comparison.
//! Run: NEO4J_PASSWORD=... cargo run --release [database]

use neo4rs::{query, ConfigBuilder, Graph};
use std::time::Instant;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let pw = std::env::var("NEO4J_PASSWORD")?;
    let db = std::env::args().nth(1).unwrap_or_else(|| "yitae0530grok".into());
    let config = ConfigBuilder::default()
        .uri("127.0.0.1:7687")
        .user("neo4j")
        .password(pw)
        .db(db.as_str())
        .fetch_size(1000)
        .build()?;
    let graph = Graph::connect(config).await?;

    let workloads = [
        ("nodes", "MATCH (n) RETURN labels(n) AS l, properties(n) AS p, elementId(n) AS e", "e"),
        ("rels", "MATCH (a)-[r]->(b) RETURN type(r) AS t, properties(r) AS p, elementId(a) AS s, elementId(b) AS o", "s"),
    ];
    for (name, q, key) in workloads {
        // warmup
        let mut res = graph.execute(query(q)).await?;
        while res.next().await?.is_some() {}
        // timed
        let t0 = Instant::now();
        let mut rows: u64 = 0;
        let mut bytes: u64 = 0;
        let mut result = graph.execute(query(q)).await?;
        while let Some(row) = result.next().await? {
            rows += 1;
            if let Ok(s) = row.get::<String>(key) {
                bytes += s.len() as u64;
            }
        }
        let ms = t0.elapsed().as_secs_f64() * 1000.0;
        println!(
            "{name}: rows={rows} wall_ms={ms:.1} rows_per_s={:.0} (id_bytes={bytes})",
            rows as f64 / (ms / 1000.0)
        );
    }
    Ok(())
}
