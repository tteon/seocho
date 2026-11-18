//! Live Bolt integration tests for seochod against a real DozerDB/Neo4j.
//!
//! Every test returns early (printing "skipped") unless
//! `SEOCHOD_IT_BOLT_URI` and `SEOCHOD_IT_BOLT_PASSWORD` are set
//! (`SEOCHOD_IT_BOLT_USER` defaults to `neo4j`). Each test spawns the real
//! `seochod` binary on a socket in a fresh 0700 directory and projects into a
//! database of its own (`CREATE DATABASE`, dropped afterwards); assertions
//! read the graph back through the neo4j crate directly.
//!
//! `SEOCHOD_IT_REQUIRE=1` turns a skip into a failure. `SEOCHOD_IT_DESTRUCTIVE=1` additionally enables the restart-recovery test,
//! which runs `docker restart` on `SEOCHOD_IT_CONTAINER` (default
//! `seocho-rs-it-dozer`). The latency comparison is `#[ignore]`d:
//!
//! ```text
//! export SEOCHOD_IT_BOLT_URI=bolt://127.0.0.1:7687 SEOCHOD_IT_BOLT_PASSWORD=<password>
//! cargo test --offline -p seochod --test bolt_it
//! cargo test --offline -p seochod --test bolt_it -- --ignored --nocapture bench
//! ```

use neo4j::bookmarks::Bookmarks;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, RoutingControl};
use neo4j::session::SessionConfig;
use neo4j::transaction::Transaction;
use neo4j::{value_map, ValueReceive, ValueSend};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::error::Error;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type TestResult = Result<(), Box<dyn Error>>;

/// Normal tests hold it shared; the restart test holds it exclusively.
static SERVER: RwLock<()> = RwLock::new(());
static NEXT: AtomicU64 = AtomicU64::new(0);

struct ItEnv {
    uri: String,
    user: String,
    password: String,
}

fn it_env(test: &str) -> Option<ItEnv> {
    let uri = std::env::var("SEOCHOD_IT_BOLT_URI").ok().filter(|v| !v.is_empty());
    let password = std::env::var("SEOCHOD_IT_BOLT_PASSWORD").ok().filter(|v| !v.is_empty());
    match (uri, password) {
        (Some(uri), Some(password)) => Some(ItEnv {
            uri,
            user: std::env::var("SEOCHOD_IT_BOLT_USER").unwrap_or_else(|_| "neo4j".into()),
            password,
        }),
        _ => {
            // A skipped test still reports as passed; a gate that must
            // prove the live suite ran sets SEOCHOD_IT_REQUIRE=1.
            if std::env::var("SEOCHOD_IT_REQUIRE").ok().as_deref() == Some("1") {
                panic!("{test}: SEOCHOD_IT_REQUIRE=1 but SEOCHOD_IT_BOLT_URI / SEOCHOD_IT_BOLT_PASSWORD unset");
            }
            eprintln!("{test}: skipped (SEOCHOD_IT_BOLT_URI / SEOCHOD_IT_BOLT_PASSWORD unset)");
            None
        }
    }
}

fn driver(env: &ItEnv) -> Result<Driver, Box<dyn Error>> {
    let auth = AuthToken::new_basic_auth(&env.user, &env.password);
    Ok(Driver::new(
        ConnectionConfig::from_str(&env.uri)?,
        DriverConfig::new()
            .with_auth(Arc::new(auth))
            .with_connection_acquisition_timeout(Duration::from_secs(10))
            // The restart test leaves dead pooled connections behind.
            .with_idle_time_before_connection_test(Duration::ZERO),
    ))
}

fn unique(prefix: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() % 1_000_000_000)
        .unwrap_or_default();
    format!(
        "{prefix}{}x{}x{nanos}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    )
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or_default()
}

/// A database of its own, dropped when the test ends.
struct TestDb {
    driver: Driver,
    name: String,
}

impl TestDb {
    fn create(env: &ItEnv) -> Result<Self, Box<dyn Error>> {
        let driver = driver(env)?;
        // Neo4j database names allow no '_'; seochod allows no '-' or '.'.
        let name = unique("it");
        let system = Arc::new("system".to_string());
        driver
            .execute_query(format!("CREATE DATABASE {name} IF NOT EXISTS"))
            .with_database(Arc::clone(&system))
            .run()?;
        // DozerDB has no `WAIT` clause: poll until the database is online.
        let started = Instant::now();
        loop {
            let result = driver
                .execute_query("SHOW DATABASES YIELD name, currentStatus WHERE name = $name RETURN currentStatus")
                .with_database(Arc::clone(&system))
                .with_parameters(value_map!({"name": name.clone()}))
                .run()?;
            let online = result.records.first().and_then(|r| r.value("currentStatus"))
                == Some(&ValueReceive::String("online".into()));
            if online {
                break;
            }
            if started.elapsed() > Duration::from_secs(60) {
                return Err(format!("database {name} did not come online").into());
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        Ok(Self { driver, name })
    }

    fn query(&self, query: &str, params: HashMap<String, ValueSend>) -> Result<Vec<BTreeMap<String, Value>>, Box<dyn Error>> {
        let result = self
            .driver
            .execute_query(query)
            .with_database(Arc::new(self.name.clone()))
            .with_parameters(params)
            .run()?;
        Ok(result
            .records
            .iter()
            .map(|record| {
                result
                    .keys
                    .iter()
                    .map(|key| (key.to_string(), record.value(key).map(json).unwrap_or(Value::Null)))
                    .collect()
            })
            .collect())
    }

    fn count(&self, query: &str) -> Result<i64, Box<dyn Error>> {
        let rows = self.query(query, no_params())?;
        rows.first()
            .and_then(|row| row.get("c"))
            .and_then(Value::as_i64)
            .ok_or_else(|| format!("no count from {query}").into())
    }

    /// The whole graph as sorted JSON, for comparing two databases.
    fn dump(&self) -> Result<Vec<String>, Box<dyn Error>> {
        let mut out: Vec<String> = self
            .query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props", no_params())?
            .into_iter()
            .map(|row| json!(row).to_string())
            .collect();
        out.extend(
            self.query(
                "MATCH (a)-[r]->(b) RETURN type(r) AS type, a.id AS src, b.id AS tgt, properties(r) AS props",
                no_params(),
            )?
            .into_iter()
            .map(|row| json!(row).to_string()),
        );
        out.sort();
        Ok(out)
    }
}

impl Drop for TestDb {
    fn drop(&mut self) {
        let _ = self
            .driver
            .execute_query(format!("DROP DATABASE {} IF EXISTS", self.name))
            .with_database(Arc::new("system".into()))
            .run();
    }
}

fn no_params() -> HashMap<String, ValueSend> {
    HashMap::new()
}

fn json(value: &ValueReceive) -> Value {
    match value {
        ValueReceive::Null => Value::Null,
        ValueReceive::Boolean(v) => json!(v),
        ValueReceive::Integer(v) => json!(v),
        ValueReceive::Float(v) => json!(v),
        ValueReceive::String(v) => json!(v),
        ValueReceive::List(v) => Value::Array(v.iter().map(json).collect()),
        ValueReceive::Map(v) => Value::Object(v.iter().map(|(k, v)| (k.clone(), json(v))).collect()),
        other => json!(format!("{other:?}")),
    }
}

/// The real daemon on a socket in a private directory.
struct Daemon {
    child: Child,
    dir: PathBuf,
    socket: PathBuf,
}

impl Daemon {
    fn spawn(env: &ItEnv, extra: &[(&str, &str)]) -> Result<Self, Box<dyn Error>> {
        let dir = std::env::temp_dir().join(unique("seochod-it-"));
        std::fs::create_dir_all(&dir)?;
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
        let socket = dir.join("seochod.sock");
        let mut command = Command::new(env!("CARGO_BIN_EXE_seochod"));
        command
            .arg(&socket)
            .env_remove("SEOCHOD_CONTROL_DB")
            .env_remove("SEOCHOD_BOLT_HOST")
            .env_remove("SEOCHOD_BOLT_PORT")
            .env("SEOCHOD_BOLT_URI", &env.uri)
            .env("SEOCHOD_BOLT_USER", &env.user)
            .env("SEOCHOD_BOLT_PASSWORD", &env.password)
            .stdout(Stdio::null())
            .stderr(Stdio::inherit());
        if !extra.iter().any(|(k, _)| *k == "SEOCHOD_CONTROL_DB") {
            command.env("SEOCHOD_ALLOW_UNGOVERNED", "1");
        }
        for (key, value) in extra {
            command.env(key, value);
        }
        let child = command.spawn()?;
        let daemon = Self { child, dir, socket };
        let started = Instant::now();
        while daemon.request(&json!({"op": "health"})).is_err() {
            if started.elapsed() > Duration::from_secs(10) {
                return Err("seochod did not start".into());
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        Ok(daemon)
    }

    fn request(&self, payload: &Value) -> Result<Value, Box<dyn Error>> {
        let mut stream = UnixStream::connect(&self.socket)?;
        stream.set_read_timeout(Some(Duration::from_secs(120)))?;
        writeln!(stream, "{payload}")?;
        let mut line = String::new();
        BufReader::new(stream).read_line(&mut line)?;
        Ok(serde_json::from_str(&line)?)
    }

    fn project(&self, db: &TestDb, body: Value) -> Result<Value, Box<dyn Error>> {
        let mut payload = json!({"op": "project", "database": db.name, "workspace_id": "ws", "source_id": "it"});
        if let (Some(target), Some(extra)) = (payload.as_object_mut(), body.as_object()) {
            target.extend(extra.clone());
        }
        self.request(&payload)
    }
}

impl Drop for Daemon {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn node(id: &str, label: &str, props: Value) -> Value {
    json!({"id": id, "label": label, "properties": props})
}

fn rel(source: &str, target: &str, kind: &str, labels: Option<(&str, &str)>) -> Value {
    let mut value = json!({"source": source, "target": target, "type": kind, "properties": {"w": 1}});
    if let (Some((s, t)), Some(map)) = (labels, value.as_object_mut()) {
        map.insert("source_label".into(), json!(s));
        map.insert("target_label".into(), json!(t));
    }
    value
}

fn ok(response: &Value) -> bool {
    response["ok"] == json!(true)
}

/// Normalises what must match between write modes.
fn summary(response: &Value) -> Value {
    json!({
        "ok": response["ok"],
        "nodes_created": response["nodes_created"],
        "relationships_created": response["relationships_created"],
        "merge_conflicts": response["merge_conflicts"],
        "errors": response["errors"],
        "error_kind": response.get("error_kind"),
    })
}

#[test]
fn unwind_batches_match_per_item_semantics() -> TestResult {
    let Some(env) = it_env("unwind_batches_match_per_item_semantics") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let ts = now_secs() - 10.0;
    let requests = [
        // Create: two labels, list props, labelled and unlabelled (but
        // resolvable) relationships, a repeated node key in one request.
        json!({"writer_ts": ts, "nodes": [
            node("a", "Person", json!({"name": "Ann", "tags": ["x", "y"], "n": 1})),
            node("b", "Person", json!({"name": "Bob"})),
            node("o", "Org", json!({"name": "Acme", "scores": [1, 2.5]})),
            node("a", "Person", json!({"name": "Ann2"})),
        ], "relationships": [
            rel("a", "o", "WORKS_AT", Some(("Person", "Org"))),
            rel("b", "o", "WORKS_AT", None),
            rel("a", "b", "KNOWS", None),
            rel("a", "b", "KNOWS", None),
        ]}),
        // Re-merge with changes: nothing created, conflicts reported.
        json!({"writer_ts": ts + 1.0, "nodes": [
            node("a", "Person", json!({"name": "Anna", "n": 2})),
            node("b", "Person", json!({"name": "Bob"})),
            node("o", "Org", json!({"name": "Acme Inc"})),
        ], "relationships": [rel("a", "o", "WORKS_AT", Some(("Person", "Org")))]}),
        // Stale write: silent no-op.
        json!({"writer_ts": ts - 5.0, "nodes": [node("a", "Person", json!({"name": "Old"}))]}),
    ];
    let unwind_db = TestDb::create(&env)?;
    let per_item_db = TestDb::create(&env)?;
    let unwind = Daemon::spawn(&env, &[])?;
    let per_item = Daemon::spawn(&env, &[("SEOCHOD_WRITE_MODE", "per_item")])?;
    for (step, request) in requests.iter().enumerate() {
        let a = unwind.project(&unwind_db, request.clone())?;
        let b = per_item.project(&per_item_db, request.clone())?;
        assert!(ok(&a), "step {step}: {a}");
        assert_eq!(summary(&a), summary(&b), "step {step}");
        if step == 0 {
            assert_eq!(a["nodes_created"], json!(3));
            assert_eq!(a["relationships_created"], json!(3));
            // The repeated `a` conflicts with its own first write.
            assert_eq!(a["merge_conflicts"].as_array().map(Vec::len), Some(1));
        }
        if step == 1 {
            assert_eq!((a["nodes_created"].as_i64(), a["relationships_created"].as_i64()), (Some(0), Some(0)));
            let properties: Vec<&str> = a["merge_conflicts"]
                .as_array()
                .map(|c| c.iter().filter_map(|e| e["property"].as_str()).collect())
                .unwrap_or_default();
            assert_eq!(properties.len(), 3, "{a}");
        }
    }
    assert_eq!(unwind_db.dump()?, per_item_db.dump()?);
    let rows = unwind_db.query("MATCH (n:Person {id: 'a'}) RETURN n.name AS name, n.n AS n", no_params())?;
    assert_eq!(rows[0]["name"], json!("Anna"));
    assert_eq!(rows[0]["n"], json!(2));
    Ok(())
}

/// Writes of one entity that fall into different UNWIND groups (a
/// multi-label node written under two labels; a relationship given once
/// with endpoint labels and once without) must still apply in request
/// order, exactly as per-item writes do.
#[test]
fn unwind_keeps_request_order_for_one_entity_across_groups() -> TestResult {
    let Some(env) = it_env("unwind_keeps_request_order_for_one_entity_across_groups") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let ts = now_secs() - 10.0;
    let rel_with = |w: i64, labels: bool| {
        let mut value = json!({"source": "a", "target": "o", "type": "R", "properties": {"w": w}});
        if let (true, Some(map)) = (labels, value.as_object_mut()) {
            map.insert("source_label".into(), json!("Person"));
            map.insert("target_label".into(), json!("Org"));
        }
        value
    };
    let request = json!({"writer_ts": ts, "nodes": [
        node("m", "Person", json!({"v": "1"})),
        node("m", "Org", json!({"v": "2"})),
        node("m", "Person", json!({"v": "3"})),
    ], "relationships": [rel_with(1, true), rel_with(2, false), rel_with(3, true)]});
    let unwind_db = TestDb::create(&env)?;
    let per_item_db = TestDb::create(&env)?;
    // `a`, `o` and the multi-label `m` exist beforehand and are not part of
    // the request, so the unlabelled relationship keeps no labels.
    for db in [&unwind_db, &per_item_db] {
        db.query(
            "CREATE (:Person {id: 'a', _workspace_id: 'ws'}), (:Org {id: 'o', _workspace_id: 'ws'}), (:Person:Org {id: 'm', _workspace_id: 'ws', v: '0'})",
            no_params(),
        )?;
    }
    let unwind = Daemon::spawn(&env, &[])?;
    let per_item = Daemon::spawn(&env, &[("SEOCHOD_WRITE_MODE", "per_item")])?;
    let a = unwind.project(&unwind_db, request.clone())?;
    let b = per_item.project(&per_item_db, request)?;
    assert!(ok(&a), "{a}");
    assert_eq!(summary(&a), summary(&b));
    let incoming: Vec<&str> = a["merge_conflicts"]
        .as_array()
        .map(|c| c.iter().filter_map(|e| e["incoming"].as_str()).collect())
        .unwrap_or_default();
    assert_eq!(incoming, vec!["1", "2", "3"], "{a}");
    assert_eq!(unwind_db.dump()?, per_item_db.dump()?);
    let rows = unwind_db.query("MATCH (n {id: 'm'}) RETURN n.v AS v", no_params())?;
    assert_eq!(rows[0]["v"], json!("3"));
    let rows = unwind_db.query("MATCH ({id: 'a'})-[r:R]->({id: 'o'}) RETURN r.w AS w", no_params())?;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["w"], json!(3));
    Ok(())
}

#[test]
fn merge_conflicts_use_the_python_writer_shape() -> TestResult {
    let Some(env) = it_env("merge_conflicts_use_the_python_writer_shape") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[])?;
    let ts = now_secs() - 10.0;
    assert!(ok(&daemon.project(&db, json!({"writer_ts": ts, "nodes": [node("a", "Person", json!({"name": "A", "age": 3}))]}))?));
    let response = daemon.project(&db, json!({"writer_ts": ts + 1.0, "nodes": [node("a", "Person", json!({"name": "B", "age": 3}))]}))?;
    assert!(ok(&response));
    assert_eq!(
        response["merge_conflicts"],
        json!([{"label": "Person", "key": "a", "property": "name", "existing": "A", "incoming": "B", "source_id": "it"}])
    );
    Ok(())
}

#[test]
fn stale_writer_ts_is_a_silent_noop() -> TestResult {
    let Some(env) = it_env("stale_writer_ts_is_a_silent_noop") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[])?;
    let ts = now_secs() - 10.0;
    let write = |ts: f64, name: &str| {
        daemon.project(&db, json!({"writer_ts": ts, "nodes": [node("a", "Person", json!({"name": name}))], "relationships": []}))
    };
    let name = || -> Result<Value, Box<dyn Error>> {
        Ok(db.query("MATCH (n:Person {id: 'a'}) RETURN n.name AS name, n._writer_ts AS ts", no_params())?[0]["name"].clone())
    };
    assert!(ok(&write(ts, "first")?));
    let stale = write(ts - 1.0, "stale")?;
    assert!(ok(&stale), "{stale}");
    assert_eq!(name()?, json!("first"));
    assert!(ok(&write(ts + 1.0, "newer")?));
    assert_eq!(name()?, json!("newer"));
    Ok(())
}

#[test]
fn missing_endpoint_rolls_back_the_whole_request() -> TestResult {
    let Some(env) = it_env("missing_endpoint_rolls_back_the_whole_request") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    for mode in ["unwind", "per_item"] {
        let daemon = Daemon::spawn(&env, &[("SEOCHOD_WRITE_MODE", mode)])?;
        let response = daemon.project(&db, json!({"nodes": [
            node("a", "Person", json!({})), node("b", "Person", json!({})), node("o", "Org", json!({})),
        ], "relationships": [
            rel("a", "o", "WORKS_AT", Some(("Person", "Org"))),
            rel("b", "ghost", "WORKS_AT", Some(("Person", "Org"))),
        ]}))?;
        assert!(!ok(&response), "{mode}: {response}");
        assert_eq!(response["error_kind"], json!("graph_rejected"));
        let error = response["error"].as_str().unwrap_or_default();
        assert!(error.contains("relationship b-[WORKS_AT]->ghost: endpoint node not found"), "{error}");
        assert_eq!(db.count("MATCH (n) RETURN count(n) AS c")?, 0, "{mode}");
        assert_eq!(db.count("MATCH ()-[r]->() RETURN count(r) AS c")?, 0, "{mode}");
    }
    Ok(())
}

#[test]
fn invalid_values_are_rejected_before_any_write() -> TestResult {
    let Some(env) = it_env("invalid_values_are_rejected_before_any_write") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[])?;
    for bad in [json!(18446744073709551615u64), json!([1, "a"]), json!([1, null])] {
        let response = daemon.project(&db, json!({"nodes": [
            node("ok", "Person", json!({})), node("bad", "Person", json!({"v": bad})),
        ]}))?;
        assert!(!ok(&response), "{response}");
        assert_eq!(response["error_kind"], json!("invalid_request"));
        assert!(response.get("attempts").is_none());
    }
    assert_eq!(db.count("MATCH (n) RETURN count(n) AS c")?, 0);
    Ok(())
}

#[test]
// The transaction closure returns the driver's `neo4j::Result`.
#[allow(clippy::result_large_err)]
fn bookmarks_give_read_your_writes() -> TestResult {
    let Some(env) = it_env("bookmarks_give_read_your_writes") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[])?;
    let first = daemon.project(&db, json!({"nodes": [node("a", "Person", json!({}))]}))?;
    assert!(ok(&first), "{first}");
    let bookmarks: Vec<String> = serde_json::from_value(first["bookmarks"].clone())?;
    assert!(!bookmarks.is_empty() && bookmarks.iter().all(|b| !b.is_empty()));
    // A reader session that starts from the writer's bookmarks sees the write.
    let mut session = db.driver.session(
        SessionConfig::new()
            .with_database(Arc::new(db.name.clone()))
            .with_bookmarks(Arc::new(Bookmarks::from_raw(bookmarks.clone()))),
    );
    let count = session
        .transaction()
        .with_routing_control(RoutingControl::Read)
        .run(|tx: Transaction| {
            let mut stream = tx.query("MATCH (n:Person {id: 'a'}) RETURN count(n) AS c").run()?;
            let mut count = None;
            for record in stream.by_ref() {
                count = record?.value("c").and_then(ValueReceive::as_int);
            }
            stream.consume()?;
            tx.commit()?;
            Ok(count)
        })?;
    assert_eq!(count, Some(1));
    // A later write may causally follow those bookmarks and gets new ones.
    let second = daemon.project(&db, json!({"bookmarks": bookmarks, "nodes": [node("b", "Person", json!({}))]}))?;
    assert!(ok(&second), "{second}");
    assert_ne!(second["bookmarks"], first["bookmarks"]);
    let invalid = daemon.project(&db, json!({"bookmarks": [""], "nodes": []}))?;
    assert_eq!(invalid["error_kind"], json!("invalid_request"));
    Ok(())
}

/// Lifecycle fixture: pointer token 1 for `pkg`, projection leases `old`
/// (token 3) and `new` (token 5), both live.
fn control_db(dir: &std::path::Path) -> Result<(PathBuf, String), Box<dyn Error>> {
    let path = dir.join("lifecycle.sqlite");
    let conn = Connection::open(&path)?;
    conn.execute_batch(
        "CREATE TABLE active_ontology (workspace_id TEXT, package_id TEXT, version TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, PRIMARY KEY(workspace_id, package_id));
         CREATE TABLE ontology_lease (lease_id TEXT PRIMARY KEY, workspace_id TEXT, package_id TEXT, purpose TEXT, owner TEXT, fingerprint TEXT, generation INTEGER, epoch INTEGER, fencing_token INTEGER, acquired_at_ms INTEGER, expires_at_ms INTEGER);
         CREATE TABLE projection_idempotency (idempotency_key TEXT PRIMARY KEY, workspace_id TEXT, fingerprint TEXT, state TEXT, response_json TEXT, created_at_ms INTEGER, completed_at_ms INTEGER);",
    )?;
    let digest = "a".repeat(64);
    conn.execute("INSERT INTO active_ontology VALUES ('ws','pkg','1.0',?1,0,0,1)", [&digest])?;
    for (lease, token) in [("old", 3), ("new", 5)] {
        conn.execute(
            "INSERT INTO ontology_lease VALUES (?1,'ws','pkg','projection','owner',?2,0,0,?3,0,9999999999999)",
            (lease, &digest, token),
        )?;
    }
    Ok((path, digest))
}

fn governed(digest: &str, lease: &str, token: i64, key: &str) -> Value {
    json!({
        "idempotency_key": key,
        "semantic_receipt": {
            "schema_version": "seocho.canonical_projection_receipt.v1",
            "rdf_bundle_sha256": digest,
            "rdf_data_graph_sha256": "b".repeat(64),
            "agent_profile_sha256": "c".repeat(64),
            "projection_receipt_sha256": "d".repeat(64),
        },
        "admission": {"lease_id": lease, "fingerprint": digest, "generation": 0, "epoch": 0, "fencing_token": token},
    })
}

fn merged(mut base: Value, extra: Value) -> Value {
    if let (Some(target), Some(extra)) = (base.as_object_mut(), extra.as_object()) {
        target.extend(extra.clone());
    }
    base
}

#[test]
fn fenced_item_rolls_back_the_whole_request() -> TestResult {
    let Some(env) = it_env("fenced_item_rolls_back_the_whole_request") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let fixture_dir = std::env::temp_dir().join(unique("seochod-it-control-"));
    std::fs::create_dir_all(&fixture_dir)?;
    let (path, digest) = control_db(&fixture_dir)?;
    let control = path.to_str().ok_or("non-utf8 path")?.to_string();
    let daemon = Daemon::spawn(&env, &[("SEOCHOD_CONTROL_DB", control.as_str())])?;
    // The newer lease projects `a` (fence 5, scope pkg).
    let first = daemon.project(&db, merged(governed(&digest, "new", 5, "k1"), json!({"nodes": [node("a", "Person", json!({"name": "new"}))]})))?;
    assert!(ok(&first), "{first}");
    let stamped = db.query("MATCH (n:Person {id: 'a'}) RETURN n._fencing_token AS t, n._fence_scope AS s", no_params())?;
    assert_eq!((stamped[0]["t"].clone(), stamped[0]["s"].clone()), (json!(5), json!("pkg")));
    // The superseded lease writes new items plus `a`: everything rolls back.
    let stale = daemon.project(&db, merged(governed(&digest, "old", 3, "k2"), json!({"nodes": [
        node("fresh", "Org", json!({})),
        node("b", "Person", json!({})),
        node("a", "Person", json!({"name": "old"})),
    ], "relationships": [rel("b", "fresh", "WORKS_AT", Some(("Person", "Org")))]})))?;
    assert!(!ok(&stale), "{stale}");
    assert_eq!(stale["error_kind"], json!("graph_rejected"));
    assert!(stale["error"].as_str().unwrap_or_default().contains("node a: fenced off"));
    assert_eq!(db.count("MATCH (n) RETURN count(n) AS c")?, 1);
    assert_eq!(db.query("MATCH (n:Person {id: 'a'}) RETURN n.name AS name", no_params())?[0]["name"], json!("new"));
    // Ungoverned writes cannot overwrite fenced data either.
    let ungoverned_db = Daemon::spawn(&env, &[])?;
    let ungoverned = ungoverned_db.project(&db, json!({"nodes": [node("a", "Person", json!({"name": "raw"}))]}))?;
    assert_eq!(ungoverned["error_kind"], json!("graph_rejected"));
    // The completed key replays its receipt, bookmarks included.
    let replay = daemon.project(&db, merged(governed(&digest, "new", 5, "k1"), json!({"nodes": [node("a", "Person", json!({"name": "new"}))]})))?;
    assert_eq!(replay["bookmarks"], first["bookmarks"]);
    drop(daemon);
    let _ = std::fs::remove_dir_all(&fixture_dir);
    Ok(())
}

#[test]
fn relationship_writes_seek_labelled_endpoints() -> TestResult {
    let Some(env) = it_env("relationship_writes_seek_labelled_endpoints") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    db.query("CREATE INDEX person_id IF NOT EXISTS FOR (n:Person) ON (n.id)", no_params())?;
    db.query("CALL db.awaitIndexes(60)", no_params())?;
    // Mirrors batch::relationship_unwind_query (asserted textually by the
    // unit tests) with the Person/Person labels.
    let query = "EXPLAIN UNWIND $rows AS row MATCH (a:Person {id: row.src, _workspace_id: $ws}), (b:Person {id: row.tgt, _workspace_id: $ws}) MERGE (a)-[r:KNOWS]->(b) WITH row, r, coalesce(r._fencing_token IS NULL OR (r._fence_scope IS NOT NULL AND r._fence_scope <> $fence_scope) OR r._fencing_token <= $fence, false) AS fence_ok, (r._writer_ts IS NULL OR r._writer_ts <= row.props._writer_ts) AS fresh SET r += CASE WHEN fence_ok AND fresh THEN row.props ELSE {} END RETURN row.i AS i, fence_ok";
    let result = db
        .driver
        .execute_query(query)
        .with_database(Arc::new(db.name.clone()))
        .with_parameters(value_map!({"rows": ValueSend::List(vec![]), "ws": "ws", "fence": ValueSend::Null, "fence_scope": ValueSend::Null}))
        .run()?;
    fn ops(plan: &neo4j::summary::Plan, out: &mut Vec<String>) {
        out.push(plan.op_type.clone());
        for child in &plan.children {
            ops(child, out);
        }
    }
    let mut operators = vec![];
    if let Some(plan) = result.summary.plan.as_ref() {
        ops(plan, &mut operators);
    }
    assert!(!operators.is_empty());
    assert!(operators.iter().all(|op| !op.contains("AllNodesScan")), "{operators:?}");
    assert!(operators.iter().any(|op| op.contains("NodeIndexSeek")), "{operators:?}");
    Ok(())
}

/// Catches a TCP_NODELAY regression: 150 per-item writes of 12 KB nodes stall
/// ~40 ms each without it (about 6 s); with it they take well under 2 s.
#[test]
fn large_properties_do_not_stall_on_nagle() -> TestResult {
    let Some(env) = it_env("large_properties_do_not_stall_on_nagle") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[("SEOCHOD_WRITE_MODE", "per_item")])?;
    let pad = "x".repeat(12 * 1024);
    let nodes: Vec<Value> = (0..150).map(|i| node(&format!("n{i}"), "Big", json!({"pad": pad}))).collect();
    let started = Instant::now();
    let response = daemon.project(&db, json!({"nodes": nodes}))?;
    let elapsed = started.elapsed();
    assert!(ok(&response), "{response}");
    assert_eq!(response["nodes_created"], json!(150));
    assert!(elapsed < Duration::from_secs(2), "150 x 12 KB per-item writes took {elapsed:?}");
    Ok(())
}

fn wait_for_server(env: &ItEnv) -> Result<f64, Box<dyn Error>> {
    let started = Instant::now();
    loop {
        if let Ok(driver) = driver(env) {
            if driver.execute_query("RETURN 1").with_database(Arc::new("neo4j".into())).run().is_ok() {
                return Ok(started.elapsed().as_secs_f64());
            }
        }
        if started.elapsed() > Duration::from_secs(120) {
            return Err("server did not come back".into());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn docker_restart() -> TestResult {
    let container = std::env::var("SEOCHOD_IT_CONTAINER").unwrap_or_else(|_| "seocho-rs-it-dozer".into());
    let status = Command::new("docker").args(["restart", container.as_str()]).stdout(Stdio::null()).status()?;
    if !status.success() {
        return Err(format!("docker restart {container} failed").into());
    }
    Ok(())
}

/// Restarts DozerDB under a running daemon: once fully back, the stale pooled
/// connection is caught by the liveness check; while still coming up, the
/// transient failures are retried within the budget.
#[test]
fn daemon_recovers_from_a_dozerdb_restart() -> TestResult {
    let Some(env) = it_env("daemon_recovers_from_a_dozerdb_restart") else {
        return Ok(());
    };
    if std::env::var("SEOCHOD_IT_DESTRUCTIVE").ok().as_deref() != Some("1") {
        eprintln!("daemon_recovers_from_a_dozerdb_restart: skipped (SEOCHOD_IT_DESTRUCTIVE!=1)");
        return Ok(());
    }
    let _exclusive = SERVER.write().map_err(|e| e.to_string())?;
    let db = TestDb::create(&env)?;
    let daemon = Daemon::spawn(&env, &[("SEOCHOD_BOLT_RETRY_BUDGET_MS", "25000"), ("SEOCHOD_BOLT_MAX_ATTEMPTS", "40")])?;
    let write = |id: &str| daemon.project(&db, json!({"nodes": [node(id, "Person", json!({}))]}));
    assert!(ok(&write("warm")?));
    docker_restart()?;
    let back = wait_for_server(&env)?;
    let after = write("after-restart")?;
    eprintln!("restart: server back after {back:.1}s; first request {after}");
    assert!(ok(&after), "{after}");
    // Restart again and send the request right away.
    let started = Instant::now();
    docker_restart()?;
    let during = write("during-restart")?;
    eprintln!("restart: request sent during restart answered after {:.1}s: {during}", started.elapsed().as_secs_f64());
    assert!(ok(&during), "{during}");
    assert_eq!(db.count("MATCH (n:Person) RETURN count(n) AS c")?, 3);
    Ok(())
}

/// Per-item vs UNWIND latency through the real daemon (prints JSON lines).
/// `SEOCHOD_IT_BENCH_N` (default 1000) nodes and as many relationships.
#[test]
#[ignore]
fn bench_per_item_vs_unwind() -> TestResult {
    let Some(env) = it_env("bench_per_item_vs_unwind") else {
        return Ok(());
    };
    let _shared = SERVER.read().map_err(|e| e.to_string())?;
    let n: usize = std::env::var("SEOCHOD_IT_BENCH_N").ok().and_then(|v| v.parse().ok()).unwrap_or(1000);
    let reps: usize = std::env::var("SEOCHOD_IT_BENCH_REPS").ok().and_then(|v| v.parse().ok()).unwrap_or(3);
    // 12 KB props are capped by the 2 MiB request frame.
    for (pad_bytes, count) in [(0usize, n), (12 * 1024, 120usize.min(n))] {
        let pad = "x".repeat(pad_bytes);
        let nodes: Vec<Value> = (0..count).map(|i| node(&format!("n{i}"), "BenchNode", json!({"name": format!("node-{i}"), "amount": i as f64 * 1.5, "pad": pad}))).collect();
        let rels: Vec<Value> = (0..count).map(|i| rel(&format!("n{i}"), &format!("n{}", (i + 1) % count), "BENCH_REL", Some(("BenchNode", "BenchNode")))).collect();
        for rep in 0..reps {
            for mode in ["per_item", "unwind"] {
                let db = TestDb::create(&env)?;
                db.query("CREATE INDEX bench_id IF NOT EXISTS FOR (n:BenchNode) ON (n.id)", no_params())?;
                db.query("CALL db.awaitIndexes(60)", no_params())?;
                let daemon = Daemon::spawn(&env, &[("SEOCHOD_WRITE_MODE", mode)])?;
                let body = json!({"nodes": nodes, "relationships": rels});
                let started = Instant::now();
                let create = daemon.project(&db, body.clone())?;
                let create_ms = started.elapsed().as_secs_f64() * 1000.0;
                let started = Instant::now();
                let remerge = daemon.project(&db, body)?;
                let remerge_ms = started.elapsed().as_secs_f64() * 1000.0;
                assert!(ok(&create) && ok(&remerge), "{create} {remerge}");
                println!(
                    "{}",
                    json!({"mode": mode, "rep": rep, "nodes": count, "relationships": count, "pad_bytes": pad_bytes,
                           "create_ms": (create_ms * 10.0).round() / 10.0, "remerge_ms": (remerge_ms * 10.0).round() / 10.0,
                           "daemon_create_ms": create["duration_ms"], "daemon_remerge_ms": remerge["duration_ms"],
                           "nodes_created": create["nodes_created"], "relationships_created": create["relationships_created"]})
                );
            }
        }
    }
    Ok(())
}
