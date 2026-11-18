// Copyright Rouven Bauer
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::result::Result as StdResult;
use std::thread::sleep;
use std::time::Duration;

use log::warn;
use rand;
use rand::Rng;
use thiserror::Error;

use crate::error_::{Neo4jError, Result};
use crate::time::Instant;

// imports for docs
#[allow(unused)]
use crate::driver::session::TransactionBuilder;
#[allow(unused)]
use crate::driver::ExecuteQueryBuilder;

/// Specifies how to retry work.
///
/// The driver uses this trait in places like [`ExecuteQueryBuilder::run_with_retry()`] and
/// [`TransactionBuilder::run_with_retry()`].
///
/// A default implementation is provided through [`ExponentialBackoff`].
///
/// # Example
/// ```
/// use neo4j::error::ServerError;
/// use neo4j::retry::RetryPolicy;
/// use neo4j::{Neo4jError, Result as Neo4jResult};
///
/// /// Function that will always fail with a retryable Neo4jError.
/// fn fail_retryable() -> Neo4jResult<()> {
///     // ...
///     # doc_test_utils::error_retryable()
/// }
///
/// /// Function that will always fail with a non-retryable Neo4jError.
/// fn fail_non_retryable() -> Neo4jResult<()> {
///     // ...
///     # doc_test_utils::error_non_retryable()
/// }
///
/// /// Custom retry policy that will never give up. Never gonna let you down.
/// /// ... expect, it might get stuck in an infinite retry loop ¯\_(ツ)_/¯
/// /// Also, it doesn't pause between retries.
/// struct MyRetryPolicy;
///
/// struct RetryError(Neo4jError);
///
/// impl RetryPolicy for MyRetryPolicy {
///     type Error = RetryError;
///
///     fn execute<R>(&self, mut work: impl FnMut() -> Neo4jResult<R>) -> Result<R, Self::Error> {
///         // The policy gets to decide what errors to retry,
///         // how often, and for how long to wait inbetween retries.
///         loop {
///             match work() {
///                 Ok(r) => return Ok(r),
///                 Err(err) => {
///                     if !err.is_retryable() {
///                         return Err(RetryError(err));
///                     }
///                 }
///             }
///         }
///     }
/// }
///
/// let retry_policy = MyRetryPolicy;
///
/// let mut count = 0;
/// let result = retry_policy.execute(|| {
///     // The actual work function.
///     // Simulate failing 3 times with a retryable error, then fail with a non-retryable one.
///     count += 1;
///     if count <= 3 {
///         fail_retryable()
///     } else {
///         fail_non_retryable()
///     }
/// });
/// assert!(result.is_err());
/// assert_eq!(count, 4);
/// ```
pub trait RetryPolicy {
    type Error;

    fn execute<R>(&self, work: impl FnMut() -> Result<R>) -> StdResult<R, Self::Error>;
}

/// [`RetryPolicy`] that retries work with exponential backoff.
///
/// Exponential backoff means that the time between retries will increase exponentially.
/// For example:
///  * work fails → wait 1 second
///  * work fails → wait 2 seconds
///  * work fails → wait 4 seconds
///  * ...
///
/// By default, it will retry for up to 30 seconds in total.
/// This can be changed with [`ExponentialBackoff::with_max_retry_time()`].
///
/// Currently, the implementation uses a start pause of 1 second, a factor of 2, and a random
/// jitter factor of `(0.8..=1.2)`.
/// This is an implementation detail and might change in the future.
///
/// The policy will return a [`RetryError::Neo4jError`] if the work function returns a
/// non-retryable [`Neo4jError`].
/// It will return a [`RetryError::Timeout`] the policy would start another attempt, but the time
/// since the end of the first attempt exceeds the maximum retry time.
#[derive(Debug, Clone, Copy)]
pub struct ExponentialBackoff {
    initial_delay: Duration,
    max_retry_time: Duration,
    factor: f64,
    jitter: f64,
}

/// Error type that can be returned by [`RetryPolicy::execute()`] to indicate whether the work
/// failed with a non-retryable error or a timeout occurred while retrying.
///
/// In particular, [`ExponentialBackoff`] makes use of this error type.
#[derive(Error, Debug)]
pub enum RetryError {
    /// The work failed with a non-retryable driver error.
    #[error("Non-retryable error occurred: {0}")]
    Neo4jError(#[from] Neo4jError),
    /// A timeout occurred while retrying.
    #[error("{0}")]
    Timeout(#[from] TimeoutError),
}

/// Used to indicate that a retry loop timed out.
///
/// All errors encountered during the retry loop are collected and can be accessed through
/// [`TimeoutError::errors`].
///
/// See also [`RetryError::Timeout`].
#[derive(Error, Debug)]
#[error("Timeout occurred while retrying. Last error: {}", .errors.last().unwrap())]
pub struct TimeoutError {
    /// Errors encountered during the retry loop.
    pub errors: Vec<Neo4jError>,
}

impl From<TimeoutError> for Vec<Neo4jError> {
    fn from(value: TimeoutError) -> Self {
        value.errors
    }
}

impl ExponentialBackoff {
    /// Create a new exponential backoff policy with default settings.
    ///
    /// Same as [`ExponentialBackoff::default()`].
    #[inline]
    pub fn new() -> Self {
        Self::default()
    }

    /// Change for how long the policy will retry for, before giving up.
    #[inline]
    pub fn with_max_retry_time(self, max_retry_time: Duration) -> Self {
        Self {
            max_retry_time,
            ..self
        }
    }

    fn max_retries(&self) -> usize {
        /*
        1 * 0.8
        + 1 * 2 * 0.8
        + 1 * 2 * 2 * 0.8
        + ...
        = 1 * 0.8 * (2^0 + 2^1 + 2^2 + ... + 2^x)
        = 1 * 0.8 * (2^(x+1) - 1)

        init * min_jitter * (factor ^ (x + 1) - 1) <= max_retries

        <=> x <= log factor (max_retries / (init * min_jitter) + 1) - 1
         */
        assert!(self.initial_delay > Duration::ZERO);
        assert!(self.max_retry_time > Duration::ZERO);
        let max_time = self.max_retry_time.as_secs_f64();
        let init = self.initial_delay.as_secs_f64();
        let min_jitter = self.factor * self.jitter;
        ((1.0 + max_time / (init * min_jitter)).log(self.factor) - 1.0).ceil() as usize
    }
}

impl Default for ExponentialBackoff {
    #[inline]
    fn default() -> Self {
        Self {
            initial_delay: Duration::from_secs(1),
            max_retry_time: Duration::from_secs(30),
            factor: 2.0,
            jitter: 0.2,
        }
    }
}

impl RetryPolicy for &ExponentialBackoff {
    type Error = RetryError;

    fn execute<R>(&self, mut work: impl FnMut() -> Result<R>) -> StdResult<R, Self::Error> {
        assert!(self.jitter >= 0.0);
        assert!(self.jitter < 1.0);
        let mut time_start = None;
        let mut errors = None;
        let mut current_delay = self.initial_delay.as_secs_f64();
        let mut rng = rand::thread_rng();
        loop {
            let res = work();
            if time_start.is_none() {
                time_start = Some(Instant::now());
            }
            let err = match res {
                Err(e) if e.is_retryable() => e,
                _ => return res.map_err(Into::into),
            };
            if errors.is_none() {
                errors = Some(Vec::with_capacity(self.max_retries()));
            }
            errors.as_mut().unwrap().push(err);
            let time_elapsed = time_start.unwrap().elapsed();
            if time_elapsed > self.max_retry_time {
                return Err(TimeoutError {
                    errors: errors.unwrap(),
                }
                .into());
            }
            let jitter_factor = 1.0 + rng.gen_range(-self.jitter..=self.jitter);
            let jittered_delay = current_delay * jitter_factor;
            warn!(
                "Transaction failed and will be retired in {:.4} seconds: {}",
                jittered_delay,
                errors.as_ref().unwrap().last().unwrap()
            );
            sleep(Duration::try_from_secs_f64(jittered_delay).unwrap_or(Duration::MAX));
            current_delay *= self.factor;
        }
    }
}

impl RetryPolicy for ExponentialBackoff {
    type Error = <&'static ExponentialBackoff as RetryPolicy>::Error;

    fn execute<R>(&self, work: impl FnMut() -> Result<R>) -> StdResult<R, Self::Error> {
        (&self).execute(work)
    }
}
