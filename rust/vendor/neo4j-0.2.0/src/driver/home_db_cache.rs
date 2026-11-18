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

use itertools::Itertools;
use log::debug;
use parking_lot::Mutex;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::mem;
use std::sync::Arc;
use std::time::Instant;

use super::auth::AuthToken;
use crate::value::spatial;
use crate::{value, ValueSend};

#[derive(Debug)]
pub(super) struct HomeDbCache {
    cache: Mutex<HashMap<HomeDbCacheKey, HomeDbCacheEntry>>,
    config: HomeDbCacheConfig,
}

#[derive(Debug, Copy, Clone)]
struct HomeDbCacheConfig {
    max_size: usize,
    prune_size: usize,
}

impl Default for HomeDbCache {
    fn default() -> Self {
        Self::new(1000)
    }
}

impl HomeDbCache {
    pub(super) fn new(max_size: usize) -> Self {
        let max_size_f64 = max_size as f64;
        let mut prune_size = (0.01 * max_size_f64 * max_size_f64.ln()) as usize;
        prune_size = usize::min(prune_size, max_size);
        if prune_size == 0 && max_size > 0 {
            prune_size = 1; // ensure at least one entry is pruned
        }
        HomeDbCache {
            cache: Mutex::new(HashMap::with_capacity(max_size)),
            config: HomeDbCacheConfig {
                max_size,
                prune_size,
            },
        }
    }

    pub(super) fn get(&self, key: &HomeDbCacheKey) -> Option<Arc<String>> {
        let mut lock = self.cache.lock();
        let cache: &mut HashMap<HomeDbCacheKey, HomeDbCacheEntry> = &mut lock;
        let res = cache.get_mut(key).map(|entry| {
            entry.last_used = Instant::now();
            Arc::clone(&entry.database)
        });
        debug!(
            "Getting home database cache for key: {} -> {:?}",
            key.log_str(),
            res.as_deref(),
        );
        res
    }

    pub(super) fn update(&self, key: HomeDbCacheKey, database: Arc<String>) {
        let mut lock = self.cache.lock();
        debug!(
            "Updating home database cache for key: {} -> {:?}",
            key.log_str(),
            database.as_str(),
        );
        let cache: &mut HashMap<HomeDbCacheKey, HomeDbCacheEntry> = &mut lock;
        let previous_val = cache.insert(
            key,
            HomeDbCacheEntry {
                database,
                last_used: Instant::now(),
            },
        );
        if previous_val.is_none() {
            // cache grew, prune if necessary
            Self::prune(cache, self.config);
        }
    }

    fn prune(cache: &mut HashMap<HomeDbCacheKey, HomeDbCacheEntry>, config: HomeDbCacheConfig) {
        if cache.len() <= config.max_size {
            return;
        }
        debug!(
            "Pruning home database cache to size: {}",
            config.max_size - config.prune_size
        );
        let new_cache = mem::take(cache);
        *cache = new_cache
            .into_iter()
            .sorted_by(|(_, v1), (_, v2)| v2.last_used.cmp(&v1.last_used))
            .take(config.max_size - config.prune_size)
            .collect();
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(super) enum HomeDbCacheKey {
    DriverUser,
    FixedUser(Arc<String>),
    SessionAuth(SessionAuthKey),
}

impl HomeDbCacheKey {
    fn log_str(&self) -> String {
        match self {
            HomeDbCacheKey::DriverUser | HomeDbCacheKey::FixedUser(_) => format!("{:?}", self),
            HomeDbCacheKey::SessionAuth(SessionAuthKey(auth)) => {
                let mut auth: AuthToken = (**auth).clone();
                auth.data
                    .get_mut("credentials")
                    .map(|c| *c = value!("**********"));
                format!("SessionAuth({:?})", auth.data)
            }
        }
    }
}

#[derive(Debug, Clone)]
pub(super) struct SessionAuthKey(Arc<AuthToken>);

impl PartialEq for SessionAuthKey {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
            || self.0.data.len() == other.0.data.len()
                && self
                    .0
                    .data
                    .iter()
                    .sorted_by(|(k1, _), (k2, _)| k1.cmp(k2))
                    .zip(other.0.data.iter().sorted_by(|(k1, _), (k2, _)| k1.cmp(k2)))
                    .all(|((k1, v1), (k2, v2))| k1 == k2 && v1.eq_data(v2))
    }
}

impl Eq for SessionAuthKey {}

impl Hash for SessionAuthKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.0
            .data
            .iter()
            .sorted_by(|(k1, _), (k2, _)| k1.cmp(k2))
            .for_each(|(k, v)| {
                k.hash(state);
                Self::hash(v, state);
            });
    }
}

impl SessionAuthKey {
    fn hash(v: &ValueSend, state: &mut impl Hasher) {
        match v {
            ValueSend::Null => state.write_usize(0),
            ValueSend::Boolean(v) => v.hash(state),
            ValueSend::Integer(v) => v.hash(state),
            ValueSend::Float(v) => v.to_bits().hash(state),
            ValueSend::Bytes(v) => v.hash(state),
            ValueSend::String(v) => v.hash(state),
            ValueSend::List(v) => v.iter().for_each(|v| Self::hash(v, state)),
            ValueSend::Map(v) => {
                v.iter()
                    .sorted_by(|(k1, _), (k2, _)| k1.cmp(k2))
                    .for_each(|(k, v)| {
                        k.hash(state);
                        Self::hash(v, state);
                    });
            }
            ValueSend::Cartesian2D(spatial::Cartesian2D { srid, coordinates }) => {
                srid.hash(state);
                coordinates
                    .iter()
                    .map(|v| v.to_bits())
                    .for_each(|v| v.hash(state));
            }
            ValueSend::Cartesian3D(spatial::Cartesian3D { srid, coordinates }) => {
                srid.hash(state);
                coordinates
                    .iter()
                    .map(|v| v.to_bits())
                    .for_each(|v| v.hash(state));
            }
            ValueSend::WGS84_2D(spatial::WGS84_2D { srid, coordinates }) => {
                srid.hash(state);
                coordinates
                    .iter()
                    .map(|v| v.to_bits())
                    .for_each(|v| v.hash(state));
            }
            ValueSend::WGS84_3D(spatial::WGS84_3D { srid, coordinates }) => {
                srid.hash(state);
                coordinates
                    .iter()
                    .map(|v| v.to_bits())
                    .for_each(|v| v.hash(state));
            }
            ValueSend::Duration(v) => v.hash(state),
            ValueSend::LocalTime(v) => v.hash(state),
            ValueSend::Time(v) => v.hash(state),
            ValueSend::Date(v) => v.hash(state),
            ValueSend::LocalDateTime(v) => v.hash(state),
            ValueSend::DateTime(v) => v.hash(state),
            ValueSend::DateTimeFixed(v) => v.hash(state),
        }
    }
}

impl HomeDbCacheKey {
    pub(super) fn new(user: Option<&Arc<String>>, session_auth: Option<&Arc<AuthToken>>) -> Self {
        fn get_basic_auth_principal(auth: &AuthToken) -> Option<&str> {
            let scheme = auth.data.get("scheme")?.as_string()?.as_str();
            if scheme != "basic" {
                return None;
            }
            Some(auth.data.get("principal")?.as_string()?.as_str())
        }

        match (user, session_auth) {
            (Some(user), _) => HomeDbCacheKey::FixedUser(Arc::clone(user)),
            (None, Some(auth)) => match get_basic_auth_principal(auth) {
                Some(user) => HomeDbCacheKey::FixedUser(Arc::new(user.to_string())),
                None => HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::clone(auth))),
            },
            (None, None) => HomeDbCacheKey::DriverUser,
        }
    }
}

#[derive(Debug, Clone)]
struct HomeDbCacheEntry {
    database: Arc<String>,
    last_used: Instant,
}

#[cfg(test)]
mod test {
    use rstest::*;

    use crate::value::time;
    use crate::value_map;

    use super::*;

    #[rstest]
    #[case(HashMap::new(), HashMap::new())]
    #[case(
        value_map!({
            "list": [1, 1.5, ValueSend::Null, "string", true],
            "principal": "user",
            "map": value_map!({
                "nested": value_map!({
                    "key": "value",
                    "when": time::LocalDateTime::new(
                        time::Date::from_ymd_opt(2021, 1, 1).unwrap(),
                        time::LocalTime::from_hms_opt(12, 0, 0).unwrap(),
                    ),
                }),
                "point": spatial::Cartesian2D::new(1.0, 2.0),
                "key": "value",
            }),
            "nan": ValueSend::Float(f64::NAN),
            "foo": "bar",
        }),
        value_map!({
            "foo": "bar",
            "principal": "user",
            "nan": ValueSend::Float(f64::NAN),
            "list": [1, 1.5, ValueSend::Null, "string", true],
            "map": value_map!({
                "key": "value",
                "nested": value_map!({
                    "key": "value",
                    "when": time::LocalDateTime::new(
                        time::Date::from_ymd_opt(2021, 1, 1).unwrap(),
                        time::LocalTime::from_hms_opt(12, 0, 0).unwrap(),
                    ),
                }),
                "point": spatial::Cartesian2D::new(1.0, 2.0),
            }),
        })
    )]
    fn test_cache_key_equality(
        #[case] a: HashMap<String, ValueSend>,
        #[case] b: HashMap<String, ValueSend>,
    ) {
        let auth1 = Arc::new(AuthToken { data: a });
        let auth2 = Arc::new(AuthToken { data: b });
        let key1 = HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::clone(&auth1)));
        let key2 = HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::clone(&auth2)));
        #[allow(clippy::eq_op)] // we're explicitly testing the equality implementation here
        {
            assert_eq!(key1, key1);
            assert_eq!(key2, key2);
        }
        assert_eq!(key1, key2);
        assert_eq!(key2, key1);

        let mut hasher1 = std::collections::hash_map::DefaultHasher::new();
        let mut hasher2 = std::collections::hash_map::DefaultHasher::new();
        key1.hash(&mut hasher1);
        key2.hash(&mut hasher2);
        assert_eq!(hasher1.finish(), hasher2.finish());
    }

    #[rstest]
    #[case(value_map!({"principal": "user"}), value_map!({"principal": "admin"}))]
    #[case(value_map!({"int": 1}), value_map!({"int": 2}))]
    #[case(value_map!({"int": 1}), value_map!({"int": 1.0}))]
    #[case(value_map!({"zero": 0.0}), value_map!({"zero": -0.0}))]
    #[case(value_map!({"large": f64::INFINITY}), value_map!({"large": f64::NEG_INFINITY}))]
    #[case(value_map!({"nan": f64::NAN}), value_map!({"nan": -f64::NAN}))]
    #[case(value_map!({"int": 1}), value_map!({"int": "1"}))]
    #[case(value_map!({"list": [1, 2]}), value_map!({"list": [2, 1]}))]
    fn test_cache_key_inequality(
        #[case] a: HashMap<String, ValueSend>,
        #[case] b: HashMap<String, ValueSend>,
    ) {
        let auth1 = Arc::new(AuthToken { data: a });
        let auth2 = Arc::new(AuthToken { data: b });
        let key1 = HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::clone(&auth1)));
        let key2 = HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::clone(&auth2)));
        assert_ne!(key1, key2);
    }

    fn fixed_user_key(user: &str) -> HomeDbCacheKey {
        HomeDbCacheKey::FixedUser(Arc::new(user.to_string()))
    }

    fn auth_basic(principal: &str) -> AuthToken {
        AuthToken {
            data: value_map!({
                "scheme": "basic",
                "principal": principal,
                "credentials": "password",
            }),
        }
    }

    fn any_auth_key() -> HomeDbCacheKey {
        HomeDbCacheKey::SessionAuth(SessionAuthKey(Arc::new(AuthToken {
            data: Default::default(),
        })))
    }

    #[rstest]
    #[case(None, None, HomeDbCacheKey::DriverUser)]
    #[case(Some("user"), None, fixed_user_key("user"))]
    #[case(Some("user"), Some(auth_basic("user2")), fixed_user_key("user"))]
    #[case(
        None,
        Some(AuthToken::new_basic_auth("user2", "password")),
        fixed_user_key("user2")
    )]
    #[case(
        None,
        Some(AuthToken::new_basic_auth_with_realm("user2", "password", "my-realm")),
        fixed_user_key("user2")
    )]
    #[case(None, Some(AuthToken::new_basic_auth("", "empty")), fixed_user_key(""))]
    #[case(None, Some(AuthToken::new_none_auth()), any_auth_key())]
    #[case(None, Some(AuthToken::new_bearer_auth("token123")), any_auth_key())]
    #[case(None, Some(AuthToken::new_kerberos_auth("token123")), any_auth_key())]
    #[case(
        None,
        Some(AuthToken::new_custom_auth(None, None, None, None, None)),
        any_auth_key()
    )]
    #[case(
        None,
        Some(AuthToken::new_custom_auth(
            Some("principal".into()),
            Some("credentials".into()),
            Some("realm".into()),
            Some("scheme".into()),
            Some(value_map!({"key": "value"})),
        )),
        any_auth_key()
    )]
    fn test_cache_key_new(
        #[case] user: Option<&str>,
        #[case] session_auth: Option<AuthToken>,
        #[case] expected: HomeDbCacheKey,
    ) {
        let user = user.map(String::from).map(Arc::new);
        let session_auth = session_auth.map(Arc::new);
        let expected = match expected {
            HomeDbCacheKey::SessionAuth(_) => HomeDbCacheKey::SessionAuth(SessionAuthKey(
                Arc::clone(session_auth.as_ref().unwrap()),
            )),
            _ => expected,
        };
        assert_eq!(
            HomeDbCacheKey::new(user.as_ref(), session_auth.as_ref()),
            expected
        );
    }

    #[rstest]
    #[case(0, 0)]
    #[case(1, 1)]
    #[case(5, 1)]
    #[case(50, 1)]
    #[case(60, 2)]
    #[case(100, 4)]
    #[case(200, 10)]
    #[case(1_000, 69)]
    #[case(10_000, 921)]
    #[case(100_000, 11_512)]
    #[case(1_000_000, 138_155)]
    fn test_cache_pruning_size(#[case] max_size: usize, #[case] expected: usize) {
        let cache = HomeDbCache::new(max_size);
        assert_eq!(cache.config.prune_size, expected);
    }

    #[test]
    fn test_pruning() {
        const SIZE: usize = 200;
        const PRUNE_SIZE: usize = 10;
        let cache = HomeDbCache::new(SIZE);
        // sanity check
        assert_eq!(cache.config.prune_size, PRUNE_SIZE);

        let users: Vec<_> = (0..=SIZE).map(|i| Arc::new(format!("user{i}"))).collect();
        let keys: Vec<_> = (0..=SIZE)
            .map(|i| HomeDbCacheKey::new(Some(&users[i]), None))
            .collect();
        let entries: Vec<_> = (0..=SIZE).map(|i| Arc::new(format!("db{i}"))).collect();

        // WHEN: cache is filled to the max
        for i in 0..SIZE {
            cache.update(keys[i].clone(), Arc::clone(&entries[i]));
        }
        // THEN: no entry has been removed
        for i in 0..SIZE {
            assert_eq!(cache.get(&keys[i]), Some(Arc::clone(&entries[i])));
        }

        // WHEN: The oldest entry is touched
        cache.get(&keys[0]);
        // AND: cache is filled with one more entry
        cache.update(keys[SIZE].clone(), Arc::clone(&entries[SIZE]));
        // THEN: the oldest PRUNE_SIZE entries (2nd to (PRUNE_SIZE + 1)th) are pruned
        for key in keys.iter().skip(1).take(PRUNE_SIZE) {
            assert_eq!(cache.get(key), None);
        }
        // AND: the rest of the entries are still in the cache
        assert_eq!(cache.get(&keys[0]), Some(Arc::clone(&entries[0])));
        for i in PRUNE_SIZE + 2..=SIZE {
            assert_eq!(cache.get(&keys[i]), Some(Arc::clone(&entries[i])));
        }
    }
}
