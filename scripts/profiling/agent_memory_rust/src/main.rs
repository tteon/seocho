use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::postgres::{PgPool, PgPoolOptions};
use tokio::sync::{Mutex, Semaphore};
use tokio::task::JoinSet;

#[derive(Parser, Debug)]
struct Args {
    #[arg(long, env = "SEOCHO_POSTGRES_DSN")]
    dsn: String,
    #[arg(long, default_value_t = 10_000)]
    events: usize,
    #[arg(long, default_value_t = 64)]
    concurrency: usize,
    #[arg(long, default_value_t = 10_000)]
    aggregate_count: usize,
    #[arg(long, default_value = "uniform")]
    distribution: String,
    #[arg(long, default_value_t = 20_260_713)]
    seed: u64,
    #[arg(long)]
    output: Option<PathBuf>,
}

fn aggregate_for(distribution: &str, ordinal: usize, count: usize, seed: u64) -> String {
    let index = if distribution == "hot-one" {
        0
    } else {
        // SplitMix64 gives a stable, allocation-free uniform key distribution.
        let mut value = seed.wrapping_add((ordinal as u64).wrapping_mul(104_729));
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        ((value ^ (value >> 31)) as usize) % count
    };
    format!("wallet-{index:08}")
}

async fn allocate_strict_sequence(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    workspace: &str,
) -> Result<i64, sqlx::Error> {
    if let Some(sequence) = sqlx::query_scalar::<_, i64>(
        "UPDATE agent_memory_heads SET next_sequence=next_sequence+1 \
         WHERE workspace_id=$1 RETURNING next_sequence-1",
    )
    .bind(workspace)
    .fetch_optional(&mut **transaction)
    .await?
    {
        return Ok(sequence);
    }
    if let Some(sequence) = sqlx::query_scalar::<_, i64>(
        "INSERT INTO agent_memory_heads(workspace_id,next_sequence) VALUES($1,2) \
         ON CONFLICT(workspace_id) DO NOTHING RETURNING next_sequence-1",
    )
    .bind(workspace)
    .fetch_optional(&mut **transaction)
    .await?
    {
        return Ok(sequence);
    }
    sqlx::query_scalar::<_, i64>(
        "UPDATE agent_memory_heads SET next_sequence=next_sequence+1 \
         WHERE workspace_id=$1 RETURNING next_sequence-1",
    )
    .bind(workspace)
    .fetch_one(&mut **transaction)
    .await
}

async fn commit_revision(
    pool: &PgPool,
    workspace: &str,
    memory_id: &str,
    ordinal: usize,
) -> Result<(), sqlx::Error> {
    let idempotency_key = format!("rust-live:{ordinal}");
    let provenance_id = format!("rust-live:{ordinal}");
    let payload = json!({"ordinal": ordinal, "state": "observed"});
    let payload_text = serde_json::to_string(&payload).expect("JSON payload");
    let payload_hash = hex::encode(Sha256::digest(payload_text.as_bytes()));
    let aggregate_lock = format!("{}:{}{}", workspace.len(), workspace, memory_id);
    let mut transaction = pool.begin().await?;

    let duplicate = sqlx::query_scalar::<_, i64>(
        "SELECT sequence FROM agent_memory_idempotency \
         WHERE workspace_id=$1 AND idempotency_key=$2",
    )
    .bind(workspace)
    .bind(&idempotency_key)
    .fetch_optional(&mut *transaction)
    .await?;
    if duplicate.is_some() {
        transaction.rollback().await?;
        return Ok(());
    }

    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))")
        .bind(aggregate_lock)
        .execute(&mut *transaction)
        .await?;
    let sequence = allocate_strict_sequence(&mut transaction, workspace).await?;
    let previous_revision = sqlx::query_scalar::<_, i64>(
        "SELECT revision FROM agent_memory_revisions \
         WHERE workspace_id=$1 AND memory_id=$2 ORDER BY revision DESC LIMIT 1",
    )
    .bind(workspace)
    .bind(memory_id)
    .fetch_optional(&mut *transaction)
    .await?
    .unwrap_or(0);
    let revision = previous_revision + 1;
    if previous_revision > 0 {
        sqlx::query(
            "UPDATE agent_memory_revisions SET canonical=false \
             WHERE workspace_id=$1 AND memory_id=$2 AND canonical",
        )
        .bind(workspace)
        .bind(memory_id)
        .execute(&mut *transaction)
        .await?;
    }
    sqlx::query(
        "INSERT INTO agent_memory_revisions \
         (workspace_id,memory_id,revision,sequence,event_type,occurred_at,ingested_at,\
          provenance_id,payload,payload_hash,supersedes_revision,canonical,schema_version) \
         VALUES($1,$2,$3,$4,'transaction.observed',now(),now(),$5,$6,$7,$8,true,\
                'agent-memory.v1')",
    )
    .bind(workspace)
    .bind(memory_id)
    .bind(revision)
    .bind(sequence)
    .bind(&provenance_id)
    .bind(&payload)
    .bind(&payload_hash)
    .bind(if previous_revision == 0 {
        None
    } else {
        Some(previous_revision)
    })
    .execute(&mut *transaction)
    .await?;
    sqlx::query(
        "INSERT INTO agent_memory_outbox \
         (workspace_id,sequence,ordinal,operation,aggregate_type,aggregate_id,payload) \
         VALUES($1,$2,0,'upsert','memory_revision',$3,$4)",
    )
    .bind(workspace)
    .bind(sequence)
    .bind(memory_id)
    .bind(&payload)
    .execute(&mut *transaction)
    .await?;
    sqlx::query(
        "INSERT INTO agent_memory_idempotency \
         (workspace_id,idempotency_key,memory_id,revision,sequence,payload_hash) \
         VALUES($1,$2,$3,$4,$5,$6)",
    )
    .bind(workspace)
    .bind(idempotency_key)
    .bind(memory_id)
    .bind(revision)
    .bind(sequence)
    .bind(payload_hash)
    .execute(&mut *transaction)
    .await?;
    transaction.commit().await
}

fn percentile(values: &[f64], quantile: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let index = (((ordered.len() - 1) as f64) * quantile).round() as usize;
    ordered[index]
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    if args.events == 0 || args.concurrency == 0 || args.aggregate_count == 0 {
        return Err("events, concurrency, and aggregate-count must be positive".into());
    }
    let pool = PgPoolOptions::new()
        .min_connections(args.concurrency.min(4) as u32)
        .max_connections(args.concurrency.min(64) as u32)
        .acquire_timeout(Duration::from_secs(30))
        .connect(&args.dsn)
        .await?;
    let run_id = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let workspace = Arc::new(format!("seq-rust-{run_id:x}"));
    let semaphore = Arc::new(Semaphore::new(args.concurrency));
    let latencies = Arc::new(Mutex::new(Vec::<f64>::with_capacity(args.events)));
    let errors = Arc::new(Mutex::new(Vec::<String>::new()));
    let started = Instant::now();
    let mut tasks = JoinSet::new();

    for ordinal in 0..args.events {
        let permit = semaphore.clone().acquire_owned().await?;
        let pool = pool.clone();
        let workspace = workspace.clone();
        let latencies = latencies.clone();
        let errors = errors.clone();
        let memory_id = aggregate_for(&args.distribution, ordinal, args.aggregate_count, args.seed);
        tasks.spawn(async move {
            let operation_started = Instant::now();
            let result = commit_revision(&pool, &workspace, &memory_id, ordinal).await;
            let latency = operation_started.elapsed().as_secs_f64() * 1_000.0;
            drop(permit);
            match result {
                Ok(()) => latencies.lock().await.push(latency),
                Err(error) => errors.lock().await.push(error.to_string()),
            }
        });
    }
    while let Some(result) = tasks.join_next().await {
        result?;
    }
    let elapsed = started.elapsed().as_secs_f64();
    let latencies = latencies.lock().await;
    let errors = errors.lock().await;
    let counts: (i64, i64, i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM agent_memory_revisions WHERE workspace_id=$1),\
                (SELECT count(*) FROM agent_memory_idempotency WHERE workspace_id=$1),\
                (SELECT count(*) FROM agent_memory_outbox WHERE workspace_id=$1),\
                (SELECT COALESCE(next_sequence-1,0) FROM agent_memory_heads \
                 WHERE workspace_id=$1)",
    )
    .bind(&*workspace)
    .fetch_one(&pool)
    .await?;
    let correct = errors.is_empty()
        && counts.0 == args.events as i64
        && counts.1 == args.events as i64
        && counts.2 == args.events as i64
        && counts.3 == args.events as i64;
    let output: Value = json!({
        "artifact_schema": "seocho.agent-memory-rust-parity.v1",
        "scope": "full_memory_commit",
        "client": "rust_tokio_sqlx",
        "mode": "strict_workspace",
        "events": args.events,
        "concurrency": args.concurrency,
        "distribution": args.distribution,
        "elapsed_seconds": elapsed,
        "throughput_events_s": (latencies.len() as f64) / elapsed,
        "latency": {
            "count": latencies.len(),
            "p50_ms": percentile(&latencies, 0.50),
            "p95_ms": percentile(&latencies, 0.95),
            "p99_ms": percentile(&latencies, 0.99),
            "max_ms": latencies.iter().copied().fold(0.0, f64::max),
        },
        "cardinality": {
            "revisions": counts.0,
            "idempotency": counts.1,
            "outbox": counts.2,
            "head_sequence": counts.3,
        },
        "error_count": errors.len(),
        "errors": errors.iter().take(20).collect::<Vec<_>>(),
        "correct": correct,
        "interpretation_guardrail": "Same v1 revision/idempotency/outbox/strict-head semantics as Python; SQLx uses database now() for timestamps.",
    });
    let encoded = serde_json::to_string_pretty(&output)? + "\n";
    if let Some(path) = &args.output {
        std::fs::write(path, &encoded)?;
    }
    print!("{encoded}");

    for table in [
        "agent_memory_outbox",
        "agent_memory_idempotency",
        "agent_memory_revisions",
        "agent_memory_heads",
    ] {
        let query = format!("DELETE FROM {table} WHERE workspace_id=$1");
        sqlx::query(&query).bind(&*workspace).execute(&pool).await?;
    }
    pool.close().await;
    if !correct {
        std::process::exit(1);
    }
    Ok(())
}
