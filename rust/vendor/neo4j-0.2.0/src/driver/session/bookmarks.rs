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

use std::collections::HashSet;
use std::error::Error as StdError;
use std::fmt::Debug;
use std::ops::{Add, AddAssign, Sub, SubAssign};
use std::result::Result as StdResult;
use std::sync::Arc;

use parking_lot::RwLock;

use crate::error_::{Neo4jError, Result, UserCallbackError};

// imports for docs
#[allow(unused)]
use crate::driver::ExecuteQueryBuilder;
#[allow(unused)]
use crate::session::SessionConfig;

type BoxError = Box<dyn StdError + Send + Sync>;

/// Container for bookmarks that can be used to build a [causal chain](crate#causal-consistency).
///
/// For easier joining and manipulating of multiple causal chains, bookmarks implement [`Add`],
/// and [`Sub`] on both owned (`Bookmarks`) and borrowed values (`&Bookmarks`).
///
/// # Example
/// ```
/// use std::collections::HashSet;
///
/// use neo4j::bookmarks::Bookmarks;
///
/// fn create_bookmarks<const N: usize>(raw: [&str; N]) -> Bookmarks {
///     Bookmarks::from_raw(raw.into_iter().map(String::from))
/// }
///
/// fn assert_bookmarks<const N: usize>(bookmarks: &Bookmarks, raw: [&str; N]) {
///     assert_eq!(bookmarks.raw().collect::<HashSet<_>>(), HashSet::from(raw));
/// }
///
/// let bm1 = create_bookmarks(["a", "b"]);
/// let mut bm2 = create_bookmarks(["b", "c"]);
///
/// assert_bookmarks(&(bm1 + &bm2), ["a", "b", "c"]);
///
/// bm2 -= create_bookmarks(["a", "c"]);
/// assert_bookmarks(&bm2, ["b"]);
/// ```
#[derive(Debug, Clone, Default)]
#[cfg_attr(test, derive(PartialEq, Eq))]
pub struct Bookmarks {
    bookmarks: HashSet<Arc<String>>,
}

impl Bookmarks {
    /// Creates a new [`Bookmarks`] instance from the given raw bookmarks.
    ///
    /// This method is mainly intended for testing and deserialization.
    pub fn from_raw(raw: impl IntoIterator<Item = String>) -> Self {
        Bookmarks {
            bookmarks: raw.into_iter().map(Arc::new).collect(),
        }
    }

    /// Return the count of contained bookmarks.
    pub fn len(&self) -> usize {
        self.bookmarks.len()
    }

    /// Returns `true` if this [`Bookmarks`] contains no bookmarks.
    pub fn is_empty(&self) -> bool {
        self.bookmarks.is_empty()
    }

    /// Turn these [`Bookmarks`] into an iterator over the raw contained bookmarks.
    ///
    /// This method is mainly intended for testing and serialization.
    ///
    /// # Implementation Detail
    /// Bookmarks are internally stored as [`Arc`]s.
    /// Cloning the string value of bookmarks will be performed if and only if the bookmark has more
    /// than one strong reference.
    pub fn into_raw(self) -> impl Iterator<Item = String> {
        self.bookmarks
            .into_iter()
            .map(|bm| Arc::try_unwrap(bm).unwrap_or_else(|bm| String::from(&*bm)))
    }

    /// Return an iterator over the raw contained bookmarks.
    pub fn raw(&self) -> impl Iterator<Item = &str> {
        self.bookmarks.iter().map(|bm| bm.as_str())
    }
}

impl Add for Bookmarks {
    type Output = Bookmarks;

    fn add(mut self, mut rhs: Self) -> Self::Output {
        if self.bookmarks.len() < rhs.bookmarks.len() {
            std::mem::swap(&mut self.bookmarks, &mut rhs.bookmarks)
        }
        self.bookmarks.extend(rhs.bookmarks);
        self
    }
}

impl Add<&Bookmarks> for Bookmarks {
    type Output = Bookmarks;

    fn add(mut self, rhs: &Bookmarks) -> Self::Output {
        self.bookmarks.extend(rhs.bookmarks.to_owned());
        self
    }
}

impl Add<Bookmarks> for &Bookmarks {
    type Output = Bookmarks;

    fn add(self, mut rhs: Bookmarks) -> Self::Output {
        rhs.bookmarks.extend(self.bookmarks.to_owned());
        rhs
    }
}

impl Add<&Bookmarks> for &Bookmarks {
    type Output = Bookmarks;

    fn add(self, rhs: &Bookmarks) -> Self::Output {
        #[allow(clippy::suspicious_arithmetic_impl)]
        Bookmarks {
            bookmarks: &self.bookmarks | &rhs.bookmarks,
        }
    }
}

impl AddAssign<Bookmarks> for Bookmarks {
    fn add_assign(&mut self, mut rhs: Bookmarks) {
        if self.bookmarks.len() < rhs.bookmarks.len() {
            std::mem::swap(&mut self.bookmarks, &mut rhs.bookmarks)
        }
        self.bookmarks.extend(rhs.bookmarks);
    }
}

impl AddAssign<&Bookmarks> for Bookmarks {
    fn add_assign(&mut self, rhs: &Bookmarks) {
        self.bookmarks.extend(rhs.bookmarks.to_owned());
    }
}

impl Sub for Bookmarks {
    type Output = Bookmarks;

    fn sub(mut self, mut rhs: Self) -> Self::Output {
        if self.bookmarks.len() < rhs.bookmarks.len() {
            std::mem::swap(&mut self.bookmarks, &mut rhs.bookmarks)
        }
        for bm in rhs.bookmarks {
            self.bookmarks.remove(&bm);
        }
        self
    }
}

impl Sub<&Bookmarks> for Bookmarks {
    type Output = Bookmarks;

    fn sub(mut self, rhs: &Bookmarks) -> Self::Output {
        for bm in &rhs.bookmarks {
            self.bookmarks.remove(bm);
        }
        self
    }
}

impl Sub<Bookmarks> for &Bookmarks {
    type Output = Bookmarks;

    fn sub(self, mut rhs: Bookmarks) -> Self::Output {
        for bm in &self.bookmarks {
            rhs.bookmarks.remove(bm);
        }
        rhs
    }
}

impl Sub<&Bookmarks> for &Bookmarks {
    type Output = Bookmarks;

    fn sub(self, rhs: &Bookmarks) -> Self::Output {
        #[allow(clippy::suspicious_arithmetic_impl)]
        Bookmarks {
            bookmarks: self
                .bookmarks
                .difference(&rhs.bookmarks)
                .map(Arc::clone)
                .collect(),
        }
    }
}

impl SubAssign<Bookmarks> for Bookmarks {
    fn sub_assign(&mut self, mut rhs: Bookmarks) {
        if self.bookmarks.len() < rhs.bookmarks.len() {
            std::mem::swap(&mut self.bookmarks, &mut rhs.bookmarks)
        }
        for bm in rhs.bookmarks {
            self.bookmarks.remove(&bm);
        }
    }
}

impl SubAssign<&Bookmarks> for Bookmarks {
    fn sub_assign(&mut self, rhs: &Bookmarks) {
        for bm in &rhs.bookmarks {
            self.bookmarks.remove(bm);
        }
    }
}

/// A bookmark manager tracks bookmarks for automatic [causal chaining](crate#causal-consistency).
///
/// All transactions sharing the same bookmarks will be part of the same causal chain.
///
/// It may be used with [`ExecuteQueryBuilder::with_bookmark_manager()`] or
/// [`SessionConfig::with_bookmark_manager()`].
///
/// **⚠️ WARNING**:  
/// Any bookmark manager implementation must not interact with the driver it is used with to avoid
/// deadlocks.
///
/// Pre-defined bookmark manager implementations are available in [`bookmark_managers#functions`].
pub trait BookmarkManager: Debug + Send + Sync {
    /// Provide the bookmarks to be used for the next transaction.
    ///
    /// Before staring work configured with with bookmark manager, the driver will call this method
    /// to get the bookmarks to.
    ///
    /// If the method fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::BookmarkManagerGet`].
    fn get_bookmarks(&self) -> StdResult<Arc<Bookmarks>, BoxError>;

    /// Update the bookmarks the manager tracks.
    ///
    /// After a work configured with the bookmark manager has finished, the driver will call this
    /// method to update the bookmarks the manager tracks.
    /// `previous` contains the bookmarks the manager returned from the last call to
    /// [`BookmarkManager::get_bookmarks()`], `new` contains the bookmarks the driver received from
    /// the server.
    ///
    /// If the method fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::BookmarkManagerUpdate`].
    fn update_bookmarks(
        &self,
        previous: Arc<Bookmarks>,
        new: Arc<Bookmarks>,
    ) -> StdResult<(), BoxError>;
}

pub mod bookmark_managers {
    use super::*;

    type DefaultSupplier = fn() -> StdResult<Arc<Bookmarks>, BoxError>;
    type DefaultConsumer = fn(Arc<Bookmarks>) -> StdResult<(), BoxError>;

    /// Can be used to pass [`None`] to [`with_callbacks()`] `supplier`.
    pub const NONE_SUPPLIER: Option<DefaultSupplier> = None;
    /// Can be used to pass [`None`] to [`with_callbacks()`] `consumer`.
    pub const NONE_CONSUMER: Option<DefaultConsumer> = None;

    /// A basic [`BookmarkManager`] implementation.
    pub fn simple(initial_bookmarks: Option<Arc<Bookmarks>>) -> impl BookmarkManager {
        Neo4jBookmarkManager {
            bookmarks: RwLock::new(initial_bookmarks.unwrap_or_default()),
            supplier: NONE_SUPPLIER,
            consumer: NONE_CONSUMER,
        }
    }

    /// A basic [`BookmarkManager`] implementation that provides hooks for injecting additional
    /// bookmarks (`supplier`) and performing additional actions when the bookmarks are updated
    /// (`consumer`).
    ///
    /// The `supplier` is called before everytime [`BookmarkManager::get_bookmarks()`] is called.
    /// The bookmarks returned by the `supplier` are returned in addition to the bookmarks the
    /// manager tracks on its own.
    /// If the `supplier` fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::BookmarkManagerGet`].  
    /// N.B., the supplied bookmarks are not added to the manager's bookmarks. So they won't be
    /// returned by the next call to [`BookmarkManager::get_bookmarks()`], unless the supplier keeps
    /// returning them.  
    ///
    /// The `consumer` is called everytime [`BookmarkManager::update_bookmarks()`] is called.
    /// It receives the bookmarks as the manager tracks them after the update.
    /// If the `consumer` fails, the driver will return [`Neo4jError::UserCallback`] with
    /// [`UserCallbackError::BookmarkManagerUpdate`].
    ///
    /// **⚠️ WARNING**:  
    /// Neither the `supplier` nor the `consumer` may interact with the driver the manager is used
    /// with to avoid deadlocks.
    ///
    /// # Example
    /// ```
    /// use std::collections::HashSet;
    /// use std::sync::{Arc, Mutex};
    ///
    /// use neo4j::bookmarks::{bookmark_managers, BookmarkManager, Bookmarks};
    ///
    /// let supplier_called: Arc<Mutex<bool>> = Default::default();
    /// let supplier = {
    ///     let supplier_called = Arc::clone(&supplier_called);
    ///     move || {
    ///         *supplier_called.lock().unwrap() = true;
    ///         Ok(Arc::new(Bookmarks::from_raw(
    ///             ["supplied", "bookmarks"].into_iter().map(String::from),
    ///         )))
    ///     }
    /// };
    ///
    /// let consumer_args: Arc<Mutex<Option<Arc<Bookmarks>>>> = Default::default();
    /// let consumer = {
    ///     let consumer_args = Arc::clone(&consumer_args);
    ///     move |bookmarks: Arc<Bookmarks>| {
    ///         *consumer_args.lock().unwrap() = Some(bookmarks);
    ///         Ok(())
    ///     }
    /// };
    ///
    /// let initial_bookmarks = Arc::new(Bookmarks::from_raw(
    ///     ["initial", "bookmarks"].into_iter().map(String::from),
    /// ));
    ///
    /// let manager = bookmark_managers::with_callbacks(
    ///     Some(Arc::clone(&initial_bookmarks)),
    ///     Some(supplier),
    ///     Some(consumer),
    /// );
    ///
    /// assert!(!*supplier_called.lock().unwrap());
    /// let bookmarks = manager.get_bookmarks().unwrap();
    /// assert!(*supplier_called.lock().unwrap());
    /// assert_eq!(
    ///     bookmarks.raw().collect::<HashSet<_>>(),
    ///     HashSet::from(["initial", "supplied", "bookmarks"])
    /// );
    ///
    /// assert!(consumer_args.lock().unwrap().is_none());
    /// let new_bookmarks = Arc::new(Bookmarks::from_raw(
    ///     vec!["new", "bookmarks"].into_iter().map(String::from),
    /// ));
    /// manager
    ///     .update_bookmarks(initial_bookmarks, new_bookmarks)
    ///     .unwrap();
    /// assert_eq!(
    ///     consumer_args
    ///         .lock()
    ///         .unwrap()
    ///         .as_ref()
    ///         .unwrap()
    ///         .raw()
    ///         .collect::<HashSet<_>>(),
    ///     HashSet::from(["new", "bookmarks"])
    /// );
    /// ```
    pub fn with_callbacks<SF, CF>(
        initial_bookmarks: Option<Arc<Bookmarks>>,
        supplier: Option<SF>,
        consumer: Option<CF>,
    ) -> impl BookmarkManager
    where
        SF: Fn() -> StdResult<Arc<Bookmarks>, BoxError> + Send + Sync + 'static,
        CF: Fn(Arc<Bookmarks>) -> StdResult<(), BoxError> + Send + Sync + 'static,
    {
        Neo4jBookmarkManager {
            bookmarks: RwLock::new(initial_bookmarks.unwrap_or_default()),
            supplier,
            consumer,
        }
    }

    pub(crate) fn get_bookmarks(manager: &'_ dyn BookmarkManager) -> Result<Arc<Bookmarks>> {
        manager
            .get_bookmarks()
            .map_err(|err| Neo4jError::UserCallback {
                error: UserCallbackError::BookmarkManagerGet(err),
            })
    }

    pub(crate) fn update_bookmarks(
        manager: &'_ dyn BookmarkManager,
        previous: Arc<Bookmarks>,
        new: Arc<Bookmarks>,
    ) -> Result<()> {
        manager
            .update_bookmarks(previous, new)
            .map_err(|err| Neo4jError::UserCallback {
                error: UserCallbackError::BookmarkManagerUpdate(err),
            })
    }

    struct Neo4jBookmarkManager<SF, CF> {
        bookmarks: RwLock<Arc<Bookmarks>>,
        supplier: Option<SF>,
        consumer: Option<CF>,
    }

    impl<SF, CF> Debug for Neo4jBookmarkManager<SF, CF> {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.debug_struct("Neo4jBookmarkManager")
                .field("bookmarks", &self.bookmarks)
                .field("supplier", &self.supplier.as_ref().map(|_| "..."))
                .field("consumer", &self.consumer.as_ref().map(|_| "..."))
                .finish()
        }
    }

    impl<SF, CF> BookmarkManager for Neo4jBookmarkManager<SF, CF>
    where
        SF: Fn() -> StdResult<Arc<Bookmarks>, BoxError> + Send + Sync + 'static,
        CF: Fn(Arc<Bookmarks>) -> StdResult<(), BoxError> + Send + Sync + 'static,
    {
        fn get_bookmarks(&self) -> StdResult<Arc<Bookmarks>, BoxError> {
            let mut bookmarks = {
                let bookmarks_lock = self.bookmarks.read();
                bookmarks_lock.clone()
            };
            if let Some(supplier) = &self.supplier {
                let supplied_bookmarks = supplier()?;
                bookmarks = Arc::new(&*bookmarks + &*supplied_bookmarks);
            }
            Ok(bookmarks)
        }

        fn update_bookmarks(
            &self,
            previous: Arc<Bookmarks>,
            new: Arc<Bookmarks>,
        ) -> StdResult<(), BoxError> {
            if new.is_empty() {
                return Ok(());
            }
            let mut bookmarks_lock = self.bookmarks.write();
            *bookmarks_lock = Arc::new((**bookmarks_lock).clone() - &*previous + &*new);
            if let Some(consumer) = &self.consumer {
                let bookmarks = bookmarks_lock.clone();
                drop(bookmarks_lock);
                consumer(bookmarks)?;
            }
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rstest::*;

    fn bms(bookmarks: Vec<&str>) -> Bookmarks {
        Bookmarks::from_raw(bookmarks.into_iter().map(String::from))
    }

    #[rstest]
    fn bookmarks_add(#[values(true, false)] as_ref: bool) {
        let bm1 = bms(vec!["a", "b"]);
        let bm2 = bms(vec!["b", "c"]);
        let bm3 = bms(vec![]);
        let bm4 = bms(vec!["d"]);

        let bm_sum = if as_ref {
            &bm1 + bm2 + &bm3 + (&bm4 + &bm4)
        } else {
            bm1 + bm2 + bm3 + bm4
        };

        assert_eq!(bm_sum, bms(vec!["a", "b", "c", "d"]));
    }

    #[rstest]
    fn bookmarks_add_assign(#[values(true, false)] as_ref: bool) {
        let mut bm1 = bms(vec!["a", "b"]);
        let bm2 = bms(vec!["b", "c"]);

        if as_ref {
            let bm1_ref = &mut bm1;
            *bm1_ref += bm2;
        } else {
            bm1 += bm2
        }

        assert_eq!(bm1, bms(vec!["a", "b", "c"]));
    }
}
