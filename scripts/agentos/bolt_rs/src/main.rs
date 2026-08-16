//! Multi-agent replay harness: N OS threads, each an "agent" owning its session and
//! transactions against DozerDB (FinBench), measuring the agent<->DB exchange at fleet scale.
//!
//! Arms (one hypothesis each — see EXPERIMENT.md):
//!   scale   <db> <N> <episodes>   consume-side CPU wall: does thread-per-agent scale?
//!   mix     <db> <N> <K> <secs>   noisy neighbor: K pagers inflate lookup p99?
//!   contend <N> <mode> <writes>   hot-node write contention (isolated db `agentcontend`)
//!   dedup   <db> <N> <R> <mode>   redundant interchange: no cache between rows and context
//!   smoke   <db> <N>              original connectivity smoke
//!
//! Arms print a single JSON object to stdout (raw per-op samples included); human chatter
//! goes to stderr. scripts/bench_multiagent.py attaches the runmeta manifest.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, RoutingControl};
use neo4j::retry::ExponentialBackoff;
use neo4j::session::SessionConfig;
use neo4j::{value_map, ValueReceive};
use serde_json::{json, Value as Json};

const WS: &str = "default";
const PAGE_ROWS: i64 = 200;
const PAGES_PER_SCAN: i64 = 5;

const LOOKUP_Q: &str = "MATCH (a:Account {acct_no:$a,_workspace_id:$ws})-[t:TRANSFER]->\
                        (b:Account {_workspace_id:$ws}) \
                        RETURN b.acct_no AS b, t.amount AS amount, t.channel_risk AS risk \
                        LIMIT 200";
const PAGE_Q: &str = "MATCH (a:Account {_workspace_id:$ws})-[t:TRANSFER]->\
                      (b:Account {_workspace_id:$ws}) \
                      RETURN a.acct_no AS a, b.acct_no AS b, t.amount AS amount, \
                      t.channel_risk AS risk \
                      ORDER BY t.amount DESC SKIP $skip LIMIT $limit";
const WRITE_Q: &str = "MATCH (h:CAccount {id:$t}) SET h.balance = h.balance + 1 \
                       RETURN h.balance AS b";
// Paging without ORDER BY: the scale arm measures the client's consume cost, so the rows
// must flow at wire speed instead of behind a server-side top-N sort (that sort is exactly
// what the mix arm's pagers are for).
const STREAM_Q: &str = "MATCH (a:Account {_workspace_id:$ws})-[t:TRANSFER]->\
                        (b:Account {_workspace_id:$ws}) \
                        RETURN a.acct_no AS a, b.acct_no AS b, t.amount AS amount, \
                        t.channel_risk AS risk SKIP $skip LIMIT $limit";

fn make_driver() -> Driver {
    // env-configurable so the same harness runs against dozerdb-h0 (17687/h0gatepass)
    // for the SEOCHO bolt-rs I/O-plane organ measurement, not just the default.
    let host = std::env::var("BOLT_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port: u16 = std::env::var("BOLT_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(7687);
    let user = std::env::var("BOLT_USER").unwrap_or_else(|_| "neo4j".to_string());
    let pass = std::env::var("BOLT_PASS").unwrap_or_else(|_| "neo4jpassword".to_string());
    let address = Address::from((host.as_str(), port));
    let auth = AuthToken::new_basic_auth(&user, &pass);
    Driver::new(
        ConnectionConfig::new(address),
        DriverConfig::new().with_auth(Arc::new(auth)),
    )
}

/// utime + stime of this process (all threads), from /proc/self/stat fields 14/15.
fn cpu_seconds() -> f64 {
    let stat = std::fs::read_to_string("/proc/self/stat").unwrap();
    let after = &stat[stat.rfind(')').unwrap() + 1..];
    let f: Vec<&str> = after.split_whitespace().collect();
    let ticks: u64 = f[11].parse::<u64>().unwrap() + f[12].parse::<u64>().unwrap();
    ticks as f64 / 100.0
}

fn to_json(v: ValueReceive) -> Json {
    match v {
        ValueReceive::Null => Json::Null,
        ValueReceive::Boolean(b) => json!(b),
        ValueReceive::Integer(i) => json!(i),
        ValueReceive::Float(f) => json!(f),
        ValueReceive::String(s) => json!(s),
        other => json!(format!("{other:?}")),
    }
}

fn pct(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx]
}

#[derive(Clone, Debug)]
struct Sample {
    op: &'static str,
    agent: usize,
    seq: usize,
    ms_total: f64,
    ms_drain: f64,
    ms_json: f64,
    ms_csv: f64,
    rows: usize,
    bytes_json: usize,
    bytes_csv: usize,
    retries: usize,
}

impl Sample {
    fn to_value(&self) -> Json {
        json!({
            "op": self.op, "agent": self.agent, "seq": self.seq,
            "ms_total": self.ms_total, "ms_drain": self.ms_drain,
            "ms_json": self.ms_json, "ms_csv": self.ms_csv,
            "rows": self.rows, "bytes_json": self.bytes_json,
            "bytes_csv": self.bytes_csv, "retries": self.retries,
        })
    }
}

/// Serialize a drained batch the way an agent turns rows into LLM context: once as a JSON
/// array of objects, once as CSV. Returns (ms_json, bytes_json, ms_csv, bytes_csv).
fn measure_interchange(cols: &[&str], rows: &[Vec<Json>]) -> (f64, usize, f64, usize) {
    let t = Instant::now();
    let objs: Vec<Json> = rows
        .iter()
        .map(|r| {
            let mut m = serde_json::Map::with_capacity(cols.len());
            for (c, v) in cols.iter().zip(r.iter()) {
                m.insert((*c).to_owned(), v.clone());
            }
            Json::Object(m)
        })
        .collect();
    let json_str = serde_json::to_string(&objs).expect("json serialize");
    let ms_json = t.elapsed().as_secs_f64() * 1000.0;
    let bytes_json = json_str.len();

    let t = Instant::now();
    let mut csv = String::with_capacity(rows.len() * 32);
    csv.push_str(&cols.join(","));
    csv.push('\n');
    for r in rows {
        let mut first = true;
        for v in r {
            if !first {
                csv.push(',');
            }
            first = false;
            match v {
                Json::String(s) => csv.push_str(s),
                other => csv.push_str(&other.to_string()),
            }
        }
        csv.push('\n');
    }
    let ms_csv = t.elapsed().as_secs_f64() * 1000.0;
    (ms_json, bytes_json, ms_csv, csv.len())
}

/// One operation = one explicit transaction on the agent's own session: run, drain every
/// row into owned values, commit, then pay the rows->context serialization cost.
macro_rules! read_op {
    ($session:expr, $op:expr, $agent:expr, $seq:expr, $routing:expr, $cols:expr,
     $query:expr, $params:tt) => {{
        let cols: &[&str] = $cols;
        let tries = std::cell::Cell::new(0usize);
        let t_all = Instant::now();
        let (rows, ms_drain) = $session
            .transaction()
            .with_routing_control($routing)
            .run_with_retry(ExponentialBackoff::default(), |tx| {
                tries.set(tries.get() + 1);
                let t0 = Instant::now();
                let mut rows: Vec<Vec<Json>> = Vec::new();
                let mut stream = tx.query($query).with_parameters(value_map!$params).run()?;
                for record in stream.by_ref() {
                    let mut record = record?;
                    rows.push(
                        cols.iter()
                            .map(|c| to_json(record.take_value(c).unwrap_or(ValueReceive::Null)))
                            .collect(),
                    );
                }
                stream.consume()?;
                tx.commit()?;
                Ok((rows, t0.elapsed().as_secs_f64() * 1000.0))
            })
            .expect("op failed");
        let ms_total = t_all.elapsed().as_secs_f64() * 1000.0;
        let (ms_json, bytes_json, ms_csv, bytes_csv) = measure_interchange(cols, &rows);
        Sample {
            op: $op,
            agent: $agent,
            seq: $seq,
            ms_total,
            ms_drain,
            ms_json,
            ms_csv,
            rows: rows.len(),
            bytes_json,
            bytes_csv,
            retries: tries.get().saturating_sub(1),
        }
    }};
}

fn top_anchors(driver: &Driver, db: Arc<String>, n: i64) -> Vec<i64> {
    let result = driver
        .execute_query(
            "MATCH (a:Account {_workspace_id:$ws})-[:TRANSFER]->() \
             RETURN a.acct_no AS acct, count(*) AS d ORDER BY d DESC LIMIT $n",
        )
        .with_database(db)
        .with_routing_control(RoutingControl::Read)
        .with_parameters(value_map!({"ws": WS, "n": n}))
        .run_with_retry(ExponentialBackoff::default())
        .expect("anchor query failed");
    result
        .records
        .into_iter()
        .map(|mut r| match r.take_value("acct") {
            Some(ValueReceive::Integer(i)) => i,
            other => panic!("unexpected acct: {other:?}"),
        })
        .collect()
}

fn summarize(
    arm: &str,
    db: &str,
    params: Json,
    samples: Vec<Sample>,
    wall_s: f64,
    cpu_s: f64,
) -> Json {
    let mut by_op: std::collections::BTreeMap<&'static str, Vec<&Sample>> = Default::default();
    for s in &samples {
        by_op.entry(s.op).or_default().push(s);
    }
    let mut ops = serde_json::Map::new();
    let mut total_rows = 0usize;
    for (op, ss) in &by_op {
        let mut ms: Vec<f64> = ss.iter().map(|s| s.ms_total).collect();
        ms.sort_by(f64::total_cmp);
        let rows: usize = ss.iter().map(|s| s.rows).sum();
        total_rows += rows;
        ops.insert(
            (*op).into(),
            json!({
                "n": ss.len(),
                "rows": rows,
                "p50_ms": pct(&ms, 0.50),
                "p99_ms": pct(&ms, 0.99),
                "mean_ms": ms.iter().sum::<f64>() / ms.len() as f64,
                "sum_drain_ms": ss.iter().map(|s| s.ms_drain).sum::<f64>(),
                "sum_json_ms": ss.iter().map(|s| s.ms_json).sum::<f64>(),
                "sum_csv_ms": ss.iter().map(|s| s.ms_csv).sum::<f64>(),
                "bytes_json": ss.iter().map(|s| s.bytes_json).sum::<usize>(),
                "retries": ss.iter().map(|s| s.retries).sum::<usize>(),
            }),
        );
    }
    json!({
        "arm": arm,
        "db": db,
        "params": params,
        "wall_s": wall_s,
        "client_cpu_cores": cpu_s / wall_s,
        "agg_rows_per_s": total_rows as f64 / wall_s,
        "ops": Json::Object(ops),
        "samples": samples.iter().map(Sample::to_value).collect::<Vec<Json>>(),
    })
}

fn arm_scale(driver: Arc<Driver>, db: Arc<String>, n_agents: usize, episodes: usize) -> Json {
    let anchors = top_anchors(&driver, Arc::clone(&db), 32);
    let samples = Arc::new(Mutex::new(Vec::<Sample>::new()));
    let cpu0 = cpu_seconds();
    let t0 = Instant::now();
    let handles: Vec<_> = (0..n_agents)
        .map(|id| {
            let driver = Arc::clone(&driver);
            let db = Arc::clone(&db);
            let anchors = anchors.clone();
            let samples = Arc::clone(&samples);
            std::thread::spawn(move || {
                let mut session = driver.session(SessionConfig::new().with_database(db));
                let mut local = Vec::new();
                for e in 0..episodes {
                    let anchor = anchors[(id + e) % anchors.len()];
                    local.push(read_op!(
                        session, "lookup", id, e, RoutingControl::Read,
                        &["b", "amount", "risk"], LOOKUP_Q, ({"ws": WS, "a": anchor})
                    ));
                    for page in 0..PAGES_PER_SCAN {
                        local.push(read_op!(
                            session, "stream", id, e, RoutingControl::Read,
                            &["a", "b", "amount", "risk"], STREAM_Q,
                            ({"ws": WS, "skip": page * PAGE_ROWS, "limit": PAGE_ROWS})
                        ));
                    }
                }
                samples.lock().unwrap().extend(local);
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let wall_s = t0.elapsed().as_secs_f64();
    let cpu_s = cpu_seconds() - cpu0;
    let samples = Arc::try_unwrap(samples).unwrap().into_inner().unwrap();
    summarize(
        "scale",
        &db,
        json!({"n_agents": n_agents, "episodes": episodes}),
        samples,
        wall_s,
        cpu_s,
    )
}

fn arm_mix(
    driver: Arc<Driver>,
    db: Arc<String>,
    n_agents: usize,
    n_pagers: usize,
    secs: f64,
) -> Json {
    let anchors = top_anchors(&driver, Arc::clone(&db), 32);
    let samples = Arc::new(Mutex::new(Vec::<Sample>::new()));
    let cpu0 = cpu_seconds();
    let t0 = Instant::now();
    let deadline = t0 + Duration::from_secs_f64(secs);
    let handles: Vec<_> = (0..n_agents)
        .map(|id| {
            let driver = Arc::clone(&driver);
            let db = Arc::clone(&db);
            let anchors = anchors.clone();
            let samples = Arc::clone(&samples);
            std::thread::spawn(move || {
                let mut session = driver.session(SessionConfig::new().with_database(db));
                let mut local = Vec::new();
                let mut seq = 0usize;
                if id < n_pagers {
                    'outer: loop {
                        for page in 0..PAGES_PER_SCAN {
                            local.push(read_op!(
                                session, "page", id, seq, RoutingControl::Read,
                                &["a", "b", "amount", "risk"], PAGE_Q,
                                ({"ws": WS, "skip": page * PAGE_ROWS, "limit": PAGE_ROWS})
                            ));
                            seq += 1;
                            if Instant::now() >= deadline {
                                break 'outer;
                            }
                        }
                    }
                } else {
                    while Instant::now() < deadline {
                        let anchor = anchors[(id * 7 + seq) % anchors.len()];
                        local.push(read_op!(
                            session, "lookup", id, seq, RoutingControl::Read,
                            &["b", "amount", "risk"], LOOKUP_Q, ({"ws": WS, "a": anchor})
                        ));
                        seq += 1;
                    }
                }
                samples.lock().unwrap().extend(local);
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let wall_s = t0.elapsed().as_secs_f64();
    let cpu_s = cpu_seconds() - cpu0;
    let samples = Arc::try_unwrap(samples).unwrap().into_inner().unwrap();
    summarize(
        "mix",
        &db,
        json!({"n_agents": n_agents, "n_pagers": n_pagers, "seconds": secs}),
        samples,
        wall_s,
        cpu_s,
    )
}

/// The contention arm never touches finbenchl* — it creates and uses an isolated database.
fn setup_contend(driver: &Driver) {
    let sysdb = Arc::new("system".to_string());
    let mut sys = driver.session(SessionConfig::new().with_database(Arc::clone(&sysdb)));
    sys.auto_commit("CREATE DATABASE agentcontend IF NOT EXISTS")
        .run()
        .expect("create database");
    let mut online = false;
    for _ in 0..60 {
        let res = sys.auto_commit("SHOW DATABASES").run().expect("show databases");
        online = res.records.into_iter().any(|mut r| {
            matches!(r.take_value("name"), Some(ValueReceive::String(n)) if n == "agentcontend")
                && matches!(r.take_value("currentStatus"),
                            Some(ValueReceive::String(s)) if s == "online")
        });
        if online {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    assert!(online, "agentcontend did not come online");

    let cdb = Arc::new("agentcontend".to_string());
    let mut s = driver.session(SessionConfig::new().with_database(cdb));
    s.auto_commit("CREATE INDEX caccount_id IF NOT EXISTS FOR (c:CAccount) ON (c.id)")
        .run()
        .expect("create index");
    let count = match s
        .auto_commit("MATCH (c:CAccount) RETURN count(c) AS n")
        .with_routing_control(RoutingControl::Read)
        .run()
        .expect("count")
        .into_scalar()
    {
        Ok(ValueReceive::Integer(i)) => i,
        _ => 0,
    };
    if count == 0 {
        s.auto_commit("UNWIND range(0,999) AS i CREATE (:CAccount {id: i, balance: 0})")
            .run()
            .expect("populate");
        eprintln!("[contend] populated agentcontend with 1000 CAccount nodes");
    }
    s.auto_commit("CALL db.awaitIndexes(60)").run().ok();
}

fn arm_contend(driver: Arc<Driver>, n_agents: usize, mode: String, writes: usize) -> Json {
    setup_contend(&driver);
    let cdb = Arc::new("agentcontend".to_string());
    let samples = Arc::new(Mutex::new(Vec::<Sample>::new()));
    let cpu0 = cpu_seconds();
    let t0 = Instant::now();
    let handles: Vec<_> = (0..n_agents)
        .map(|id| {
            let driver = Arc::clone(&driver);
            let db = Arc::clone(&cdb);
            let samples = Arc::clone(&samples);
            let target: i64 = if mode == "same" { 0 } else { (id as i64 * 37) % 999 + 1 };
            std::thread::spawn(move || {
                let mut session = driver.session(SessionConfig::new().with_database(db));
                let mut local = Vec::new();
                for seq in 0..writes {
                    local.push(read_op!(
                        session, "write", id, seq, RoutingControl::Write,
                        &["b"], WRITE_Q, ({"t": target})
                    ));
                }
                samples.lock().unwrap().extend(local);
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let wall_s = t0.elapsed().as_secs_f64();
    let cpu_s = cpu_seconds() - cpu0;
    let samples = Arc::try_unwrap(samples).unwrap().into_inner().unwrap();
    summarize(
        "contend",
        "agentcontend",
        json!({"n_agents": n_agents, "mode": mode, "writes_per_agent": writes}),
        samples,
        wall_s,
        cpu_s,
    )
}

fn arm_dedup(
    driver: Arc<Driver>,
    db: Arc<String>,
    n_agents: usize,
    repeats: usize,
    mode: String,
) -> Json {
    let anchors = top_anchors(&driver, Arc::clone(&db), 32);
    let samples = Arc::new(Mutex::new(Vec::<Sample>::new()));
    let cpu0 = cpu_seconds();
    let t0 = Instant::now();
    let handles: Vec<_> = (0..n_agents)
        .map(|id| {
            let driver = Arc::clone(&driver);
            let db = Arc::clone(&db);
            let samples = Arc::clone(&samples);
            let anchor = if mode == "same" { anchors[0] } else { anchors[id % anchors.len()] };
            std::thread::spawn(move || {
                let mut session = driver.session(SessionConfig::new().with_database(db));
                let mut local = Vec::new();
                for seq in 0..repeats {
                    local.push(read_op!(
                        session, "lookup", id, seq, RoutingControl::Read,
                        &["b", "amount", "risk"], LOOKUP_Q, ({"ws": WS, "a": anchor})
                    ));
                }
                samples.lock().unwrap().extend(local);
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }
    let wall_s = t0.elapsed().as_secs_f64();
    let cpu_s = cpu_seconds() - cpu0;
    let samples = Arc::try_unwrap(samples).unwrap().into_inner().unwrap();

    // Redundancy: bytes every agent serialized vs bytes a shared interchange cache would
    // have produced once. Warm split shows the server (page cache) saving what the client
    // cannot.
    let total_bytes: usize = samples.iter().map(|s| s.bytes_json).sum();
    let unique_bytes: usize = if mode == "same" {
        samples.first().map(|s| s.bytes_json).unwrap_or(0)
    } else {
        let mut seen = std::collections::BTreeMap::new();
        for s in &samples {
            seen.entry(s.agent).or_insert(s.bytes_json);
        }
        seen.values().sum()
    };
    let first: Vec<f64> = samples.iter().filter(|s| s.seq == 0).map(|s| s.ms_total).collect();
    let rest: Vec<f64> = samples.iter().filter(|s| s.seq > 0).map(|s| s.ms_total).collect();
    let mean = |v: &[f64]| v.iter().sum::<f64>() / v.len().max(1) as f64;
    let extra = json!({
        "total_bytes_json": total_bytes,
        "unique_bytes_json": unique_bytes,
        "redundancy_factor": total_bytes as f64 / unique_bytes.max(1) as f64,
        "mean_ms_first_repeat": mean(&first),
        "mean_ms_later_repeats": mean(&rest),
    });

    let mut out = summarize(
        "dedup",
        &db,
        json!({"n_agents": n_agents, "repeats": repeats, "mode": mode}),
        samples,
        wall_s,
        cpu_s,
    );
    out.as_object_mut().unwrap().insert("dedup".into(), extra);
    out
}

// ---- original smoke ----

fn count_accounts(driver: &Driver, database: Arc<String>) -> i64 {
    let result = driver
        .execute_query("MATCH (a:Account {_workspace_id:$ws}) RETURN count(a) AS n")
        .with_database(database)
        .with_routing_control(RoutingControl::Read)
        .with_parameters(value_map!({"ws": WS}))
        .run_with_retry(ExponentialBackoff::default())
        .expect("count query failed");
    match result.into_scalar() {
        Ok(ValueReceive::Integer(n)) => n,
        other => panic!("unexpected scalar: {other:?}"),
    }
}

fn smoke(driver: Arc<Driver>, db: Arc<String>, n_agents: usize) {
    let t = Instant::now();
    let n = count_accounts(&driver, Arc::clone(&db));
    eprintln!(
        "[1] execute_query   {db}: {n} accounts ({:.1} ms)",
        t.elapsed().as_secs_f64() * 1000.0
    );
    let anchors = top_anchors(&driver, Arc::clone(&db), 4);
    eprintln!("[2] top anchors     {anchors:?}");
    let t = Instant::now();
    let handles: Vec<_> = (0..n_agents)
        .map(|i| {
            let driver = Arc::clone(&driver);
            let db = Arc::clone(&db);
            let anchor = anchors[i % anchors.len()];
            std::thread::spawn(move || {
                let mut session = driver.session(SessionConfig::new().with_database(db));
                let s = read_op!(
                    session, "lookup", i, 0, RoutingControl::Read,
                    &["b", "amount", "risk"], LOOKUP_Q, ({"ws": WS, "a": anchor})
                );
                (i, s.rows, s.ms_total)
            })
        })
        .collect();
    let mut results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    results.sort_by_key(|(id, ..)| *id);
    for (id, rows, ms) in &results {
        eprintln!("      agent {id}: {rows} rows in {ms:.1} ms");
    }
    eprintln!(
        "[3] thread-per-agent {} threads done in {:.1} ms",
        n_agents,
        t.elapsed().as_secs_f64() * 1000.0
    );
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cmd = args.first().map(String::as_str).unwrap_or("smoke");
    let sarg = |i: usize, d: &str| args.get(i).cloned().unwrap_or_else(|| d.to_string());
    let narg = |i: usize, d: usize| args.get(i).and_then(|s| s.parse().ok()).unwrap_or(d);

    let driver = Arc::new(make_driver());
    let out = match cmd {
        "smoke" => {
            smoke(driver, Arc::new(sarg(1, "finbenchl1")), narg(2, 4));
            return;
        }
        "scale" => arm_scale(driver, Arc::new(sarg(1, "finbenchl10")), narg(2, 4), narg(3, 15)),
        "mix" => arm_mix(
            driver,
            Arc::new(sarg(1, "finbenchl100")),
            narg(2, 8),
            narg(3, 2),
            narg(4, 15) as f64,
        ),
        "contend" => arm_contend(driver, narg(1, 8), sarg(2, "same"), narg(3, 200)),
        "dedup" => arm_dedup(
            driver,
            Arc::new(sarg(1, "finbenchl100")),
            narg(2, 8),
            narg(3, 25),
            sarg(4, "same"),
        ),
        other => {
            eprintln!("unknown subcommand: {other}");
            std::process::exit(2);
        }
    };
    println!("{}", serde_json::to_string(&out).unwrap());
}
