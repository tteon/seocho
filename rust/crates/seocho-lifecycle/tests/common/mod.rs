//! Shared test helpers (no `tempfile` crate offline).
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub type TestResult = Result<(), Box<dyn std::error::Error>>;

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// A directory removed on drop.
pub struct TempDir(PathBuf);

impl TempDir {
    pub fn path(&self) -> &Path {
        &self.0
    }

    pub fn join(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn make_in(base: &Path, tag: &str) -> std::io::Result<TempDir> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let dir = base.join(format!(
        "seocho-lifecycle-{tag}-{}-{nanos}-{n}",
        std::process::id()
    ));
    std::fs::create_dir_all(&dir)?;
    Ok(TempDir(dir))
}

/// A fresh directory under `$TMPDIR`.
pub fn temp_dir(tag: &str) -> std::io::Result<TempDir> {
    make_in(&std::env::temp_dir(), tag)
}

/// A fresh directory on tmpfs when available (fsync-heavy model tests stay
/// fast without weakening the production `synchronous=FULL` setting).
pub fn fast_temp_dir(tag: &str) -> std::io::Result<TempDir> {
    let shm = Path::new("/dev/shm");
    if shm.is_dir() {
        if let Ok(d) = make_in(shm, tag) {
            return Ok(d);
        }
    }
    temp_dir(tag)
}

pub const FP: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
pub const FP2: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
