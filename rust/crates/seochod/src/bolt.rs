//! Bolt driver configuration, failure classification and the bounded retry
//! loop around one projection transaction.
//!
//! The retry loop is generic over the attempt so the riskiest paths (retry
//! after a rolled-back transient failure, never after a commit whose outcome
//! is unknown) are unit-testable without a server: `neo4j::Neo4jError` cannot
//! be constructed outside the driver crate.

use neo4j::address::Address;
use neo4j::driver::auth::AuthToken;
use neo4j::driver::{ConnectionConfig, Driver, DriverConfig, KeepAliveConfig};
use neo4j::Neo4jError;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use crate::env_ms;

/// Pool and timeout defaults sized for a serial, single-host daemon whose
/// Python client gives up after 30 s.
const DEFAULT_POOL_SIZE: i64 = 4;
const DEFAULT_ACQUIRE_TIMEOUT_MS: i64 = 5_000;
const DEFAULT_CONNECT_TIMEOUT_MS: i64 = 3_000;
/// A pooled connection idle longer than this is liveness-checked (one RESET
/// round trip) before use, so the first request after a DozerDB restart does
/// not fail on a dead socket.
const DEFAULT_IDLE_TEST_MS: i64 = 1_000;
const KEEPALIVE: Duration = Duration::from_secs(60);

/// Builds the driver from `SEOCHOD_BOLT_*`. `SEOCHOD_BOLT_URI` (for example
/// `bolt://host:7687` or `neo4j://cluster:7687`) wins over HOST/PORT; the
/// HOST/PORT form connects directly (`bolt://`, no routing), which is what a
/// single DozerDB instance needs.
pub(crate) fn driver_from_env() -> Result<Driver, String> {
    let user = std::env::var("SEOCHOD_BOLT_USER").unwrap_or_else(|_| "neo4j".into());
    let password = std::env::var("SEOCHOD_BOLT_PASSWORD")
        .map_err(|_| "SEOCHOD_BOLT_PASSWORD is required".to_string())?;
    let connection = match std::env::var("SEOCHOD_BOLT_URI").ok().filter(|v| !v.is_empty()) {
        Some(uri) => ConnectionConfig::from_str(&uri)
            .map_err(|e| format!("invalid SEOCHOD_BOLT_URI: {e}"))?,
        None => {
            let host = std::env::var("SEOCHOD_BOLT_HOST").unwrap_or_else(|_| "127.0.0.1".into());
            let port = std::env::var("SEOCHOD_BOLT_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(7687);
            ConnectionConfig::new(Address::from((host.as_str(), port))).with_routing(false)
        }
    };
    let auth = AuthToken::new_basic_auth(&user, &password);
    let config = DriverConfig::new()
        .with_auth(Arc::new(auth))
        .with_user_agent(format!("seochod/{}", env!("CARGO_PKG_VERSION")))
        .with_telemetry(false)
        .with_max_connection_pool_size(
            usize::try_from(env_ms("SEOCHOD_BOLT_POOL_SIZE", DEFAULT_POOL_SIZE, 1)).unwrap_or(4),
        )
        .with_connection_acquisition_timeout(env_duration(
            "SEOCHOD_BOLT_ACQUIRE_TIMEOUT_MS",
            DEFAULT_ACQUIRE_TIMEOUT_MS,
            1,
        ))
        .with_connection_timeout(env_duration(
            "SEOCHOD_BOLT_CONNECT_TIMEOUT_MS",
            DEFAULT_CONNECT_TIMEOUT_MS,
            1,
        ))
        .with_idle_time_before_connection_test(env_duration(
            "SEOCHOD_BOLT_IDLE_TEST_MS",
            DEFAULT_IDLE_TEST_MS,
            0,
        ))
        .with_keep_alive(KeepAliveConfig::CustomTime(KEEPALIVE));
    Ok(Driver::new(connection, config))
}

fn env_duration(name: &str, default: i64, min: i64) -> Duration {
    Duration::from_millis(u64::try_from(env_ms(name, default, min)).unwrap_or(0))
}

/// How a failed projection transaction ended, as far as the daemon knows.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum FailureKind {
    /// Rolled back (nothing committed) and may succeed when re-run, e.g. a
    /// dropped connection before COMMIT or a deadlock.
    Transient,
    /// COMMIT was sent and no answer arrived: the writes may or may not have
    /// been applied. Never retried blindly.
    CommitUnknown,
    /// Rolled back and re-running the same statements would fail again.
    Fatal,
}

impl FailureKind {
    /// The stable `error_kind` the response reports.
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            FailureKind::Transient => "transient",
            FailureKind::CommitUnknown => "commit_unknown",
            FailureKind::Fatal => "fatal",
        }
    }
}

/// `commit_sent` is true once the daemon called COMMIT. A server FAILURE in
/// reply to COMMIT means the transaction was not committed; any other error
/// from that point on (a lost connection above all) leaves the outcome
/// unknown, even when the driver did not flag it `during_commit`.
pub(crate) fn classify(error: &Neo4jError, commit_sent: bool) -> FailureKind {
    classify_parts(
        commit_sent,
        matches!(error, Neo4jError::ServerError { .. }),
        matches!(error, Neo4jError::Disconnect { during_commit: true, .. }),
        error.is_retryable(),
    )
}

pub(crate) fn classify_parts(
    commit_sent: bool,
    server_error: bool,
    disconnect_during_commit: bool,
    retryable: bool,
) -> FailureKind {
    if disconnect_during_commit || (commit_sent && !server_error) {
        FailureKind::CommitUnknown
    } else if retryable {
        FailureKind::Transient
    } else {
        FailureKind::Fatal
    }
}

#[derive(Debug, PartialEq)]
pub(crate) struct BoltFailure {
    pub(crate) kind: FailureKind,
    pub(crate) message: String,
}

/// Retry budget for one projection. Only `Transient` failures are retried,
/// each after a doubling backoff, while both the attempt count and the
/// wall-clock deadline allow it.
pub(crate) struct RetryPolicy {
    pub(crate) deadline_ms: i64,
    pub(crate) max_attempts: u32,
    pub(crate) initial_backoff_ms: i64,
    pub(crate) max_backoff_ms: i64,
}

pub(crate) const DEFAULT_RETRY_BUDGET_MS: i64 = 15_000;
pub(crate) const DEFAULT_MAX_ATTEMPTS: i64 = 10;

impl RetryPolicy {
    pub(crate) fn from_env(now_ms: i64) -> Self {
        Self {
            deadline_ms: now_ms.saturating_add(env_ms(
                "SEOCHOD_BOLT_RETRY_BUDGET_MS",
                DEFAULT_RETRY_BUDGET_MS,
                0,
            )),
            max_attempts: u32::try_from(env_ms("SEOCHOD_BOLT_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, 1))
                .unwrap_or(1),
            initial_backoff_ms: 100,
            max_backoff_ms: 2_000,
        }
    }
}

/// Runs `attempt` until it succeeds or fails in a way that must not be
/// retried. `timeout_at(now)` gives each attempt its transaction timeout and
/// returns `None` when no time is left; before the first attempt that yields
/// `no_time`. Returns the value or the last failure, with the attempt count.
pub(crate) fn run_with_retries<T>(
    policy: &RetryPolicy,
    now_ms: &mut dyn FnMut() -> i64,
    sleep: &mut dyn FnMut(Duration),
    timeout_at: &mut dyn FnMut(i64) -> Option<i64>,
    no_time: BoltFailure,
    attempt: &mut dyn FnMut(i64) -> Result<T, BoltFailure>,
) -> Result<(T, u32), (BoltFailure, u32)> {
    let mut backoff = policy.initial_backoff_ms.max(0);
    let mut attempts = 0u32;
    let mut last = no_time;
    loop {
        let Some(timeout) = timeout_at(now_ms()) else {
            return Err((last, attempts));
        };
        attempts += 1;
        let failure = match attempt(timeout) {
            Ok(value) => return Ok((value, attempts)),
            Err(failure) => failure,
        };
        let retry = failure.kind == FailureKind::Transient
            && attempts < policy.max_attempts
            && now_ms().saturating_add(backoff) < policy.deadline_ms;
        if !retry {
            return Err((failure, attempts));
        }
        sleep(Duration::from_millis(u64::try_from(backoff).unwrap_or(0)));
        backoff = backoff.saturating_mul(2).min(policy.max_backoff_ms);
        last = failure;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn failure(kind: FailureKind) -> BoltFailure {
        BoltFailure {
            kind,
            message: kind.as_str().into(),
        }
    }

    fn policy(budget: i64, attempts: u32) -> RetryPolicy {
        RetryPolicy {
            deadline_ms: budget,
            max_attempts: attempts,
            initial_backoff_ms: 100,
            max_backoff_ms: 400,
        }
    }

    type Driven = (Result<(u8, u32), (BoltFailure, u32)>, Vec<i64>, Vec<u64>);

    /// Drives `run_with_retries` on a fake clock that advances by the sleeps
    /// plus `attempt_ms` per attempt; `outcomes` are returned in order.
    fn drive(
        policy: &RetryPolicy,
        attempt_ms: i64,
        mut outcomes: Vec<Result<u8, FailureKind>>,
        timeout: Option<i64>,
    ) -> Driven {
        let clock = Cell::new(0i64);
        let mut sleeps = vec![];
        let mut timeouts = vec![];
        outcomes.reverse();
        let result = run_with_retries(
            policy,
            &mut || clock.get(),
            &mut |d| {
                sleeps.push(d.as_millis() as u64);
                clock.set(clock.get() + d.as_millis() as i64);
            },
            &mut |_| timeout,
            failure(FailureKind::Fatal),
            &mut |t| {
                timeouts.push(t);
                clock.set(clock.get() + attempt_ms);
                outcomes.pop().unwrap_or(Ok(0)).map_err(failure)
            },
        );
        (result, timeouts, sleeps)
    }

    #[test]
    fn transient_failures_are_retried_with_backoff() {
        let (result, timeouts, sleeps) = drive(
            &policy(10_000, 10),
            10,
            vec![
                Err(FailureKind::Transient),
                Err(FailureKind::Transient),
                Err(FailureKind::Transient),
                Ok(7),
            ],
            Some(500),
        );
        assert_eq!(result.ok(), Some((7, 4)));
        assert_eq!(timeouts, vec![500; 4]);
        assert_eq!(sleeps, vec![100, 200, 400]);
    }

    #[test]
    fn commit_unknown_and_fatal_are_never_retried() {
        for kind in [FailureKind::CommitUnknown, FailureKind::Fatal] {
            let (result, timeouts, sleeps) =
                drive(&policy(10_000, 10), 10, vec![Err(kind), Ok(1)], Some(500));
            let (failure, attempts) = result.err().unwrap_or((failure(FailureKind::Transient), 0));
            assert_eq!((failure.kind, attempts), (kind, 1));
            assert_eq!(timeouts.len(), 1);
            assert!(sleeps.is_empty());
        }
    }

    #[test]
    fn retries_stop_at_attempt_limit_and_deadline() {
        let all_transient = vec![Err(FailureKind::Transient); 20];
        let (result, _, _) = drive(&policy(1_000_000, 3), 10, all_transient.clone(), Some(500));
        let (failure, attempts) = result.err().unwrap_or((failure(FailureKind::Fatal), 0));
        assert_eq!((failure.kind, attempts), (FailureKind::Transient, 3));
        // 1 s budget: attempts take 300 ms each, backoffs 100/200/400.
        let (result, _, sleeps) = drive(&policy(1_000, 20), 300, all_transient, Some(500));
        assert!(result.is_err());
        assert_eq!(sleeps, vec![100, 200]);
    }

    #[test]
    fn no_time_left_fails_before_any_attempt() {
        let (result, timeouts, _) = drive(&policy(10_000, 10), 10, vec![Ok(1)], None);
        let (failure, attempts) = result.err().unwrap_or((failure(FailureKind::Transient), 9));
        assert_eq!((failure.kind, attempts), (FailureKind::Fatal, 0));
        assert!(timeouts.is_empty());
    }

    #[test]
    fn commit_outcome_is_unknown_unless_the_server_answered() {
        // Lost connection after COMMIT, flagged by the driver or not.
        assert_eq!(classify_parts(true, false, true, false), FailureKind::CommitUnknown);
        assert_eq!(classify_parts(true, false, false, true), FailureKind::CommitUnknown);
        // A FAILURE reply to COMMIT: not committed, classified as usual.
        assert_eq!(classify_parts(true, true, false, true), FailureKind::Transient);
        assert_eq!(classify_parts(true, true, false, false), FailureKind::Fatal);
        // Before COMMIT nothing can have been applied.
        assert_eq!(classify_parts(false, false, false, true), FailureKind::Transient);
        assert_eq!(classify_parts(false, true, false, false), FailureKind::Fatal);
    }
}
