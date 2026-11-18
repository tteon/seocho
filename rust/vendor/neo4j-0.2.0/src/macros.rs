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

// heavily inspired by [serde_json]'s `json!` macro
// [serde_json]: https://github.com/serde-rs/json

// imports for docs
#[allow(unused)]
use crate::value::ValueSend;
#[allow(unused)]
use std::collections::HashMap;

#[cfg(test)]
macro_rules! hash_map {
    () => {std::collections::HashMap::new()};
    ( $($key:expr => $value:expr),* $(,)? ) => {
        {
            let mut m = std::collections::HashMap::with_capacity(hash_map!(_capacity($($value),*)));
            $(
                m.insert($key, $value);
            )*
            m
        }
    };
    ( _capacity() ) => (0usize);
    ( _capacity($x:tt) ) => (1usize);
    ( _capacity($x:tt, $($xs:tt),*) ) => (1usize + hash_map!(_capacity($($xs),*)));
}

#[cfg(test)]
pub(crate) use hash_map;

/// Short notation for creating a [`ValueSend`].
///
/// # Examples
///
/// Special values:
/// ```
/// use neo4j::{value, ValueSend};
///
/// // null
/// assert_eq!(ValueSend::Null, value!(null));
///
/// // true, false
/// assert_eq!(ValueSend::Boolean(true), value!(true));
/// assert_eq!(ValueSend::Boolean(false), value!(false));
/// ```
///
/// Any value that implements [`Into<ValueSend>`] (see also [`ValueSend`]):
/// ```
/// use neo4j::{value, ValueSend};
///
/// // integers
/// assert_eq!(ValueSend::Integer(1), value!(1));
///
/// // floats
/// assert_eq!(ValueSend::Float(1.234), value!(1.234));
///
/// // strings
/// assert_eq!(ValueSend::String(String::from("foo")), value!("foo"));
/// let foo = String::from("foo");
/// assert_eq!(ValueSend::String(foo.clone()), value!(foo));
///
/// // spatial types
/// use neo4j::value::spatial::Cartesian2D;
///
/// assert_eq!(
///     ValueSend::Cartesian2D(Cartesian2D::new(1., 2.)),
///     value!(Cartesian2D::new(1., 2.))
/// )
/// ```
///
/// Create [`ValueSend::Bytes`]:
/// ```
/// use neo4j::{value, ValueSend};
///
/// assert_eq!(ValueSend::Bytes(vec![1, 2, 3]), value!(bytes(1, 2, 3)));
/// ```
///
/// Create a [`ValueSend::List`]:
/// ```
/// use neo4j::{value, ValueSend};
///
/// assert_eq!(
///     ValueSend::List(vec![
///         ValueSend::Integer(1),
///         ValueSend::Float(2.),
///         ValueSend::Null
///     ]),
///     value!([1, 2., null])
/// );
/// ```
///
/// Create a [`ValueSend::Map`]:
/// ```
/// use std::collections::HashMap;
/// use neo4j::{value, ValueSend};
///
/// let mut map = HashMap::new();
/// map.insert(String::from("foo"), ValueSend::Integer(1));
/// map.insert(String::from("bar"), ValueSend::Null);
/// map.insert(String::from("baz"), ValueSend::List(vec![ValueSend::Integer(1)]));
/// let map = map;
///
/// assert_eq!(
///     ValueSend::Map(map),
///     value!({"foo": 1, "bar": null, "baz": [1]})
/// );
/// ```
#[macro_export(local_inner_macros)]
macro_rules! value {
    // Hide distracting implementation details from the generated rustdoc.
    ($($value:tt)+) => {
        __value_internal!($($value)+)
    };
}

/// Short notation for creating a [`HashMap<String, neo4j::ValueSend>`].
///
/// This macro can be useful, for example, for specifying query parameters.
///
/// The format is either `value_map!()` for an empty map:
/// ```
/// use std::collections::HashMap;
///
/// use neo4j::{value_map, ValueSend};
///
/// assert_eq!(HashMap::new(), value_map!());
/// ```
/// or `value_map!({"key": value, ...})` where value is anything accepted by [`value!`]:
/// ```
/// use std::collections::HashMap;
///
/// use neo4j::{value_map, ValueSend};
///
/// let map =  {
///     let mut map = HashMap::new();
///     map.insert(String::from("foo"), ValueSend::Integer(1));
///     map.insert(String::from("bar"), ValueSend::Null);
///     map.insert(String::from("baz"), ValueSend::List(vec![ValueSend::Integer(1)]));
///     map
/// };
///
/// assert_eq!(
///     map,
///     value_map!({"foo": 1, "bar": null, "baz": [1]})
/// );
/// ```
#[macro_export(local_inner_macros)]
macro_rules! value_map {
    ($(,)?) => {
        std::collections::HashMap::<String, $crate::ValueSend>::new()
    };

    ({ $($tt:tt)+ }) => {
        {
            let mut map = std::collections::HashMap::<String, $crate::ValueSend>::new();
            __value_internal!(@map map () ($($tt)+) ($($tt)+));
            map
        }
    };
}

#[macro_export]
#[doc(hidden)]
macro_rules! __value_internal {
    //////////////////////////////////////////////////////////////////////////
    // TT muncher for parsing the inside of a list [...].
    // Produces a ValueSend::List(vec![...]) of the elements.
    //
    // Must be invoked as: __value_internal!(@list [] $($tt)*)
    //////////////////////////////////////////////////////////////////////////

    // Done with trailing comma.
    (@list [$($elems:expr,)*]) => {
        vec![$($elems,)*]
    };

    // Done without trailing comma.
    (@list [$($elems:expr),*]) => {
        vec![$($elems),*]
    };

    // Next element is `null`.
    (@list [$($elems:expr,)*] null $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!(null)] $($rest)*)
    };

    // Next element is a list.
    (@list [$($elems:expr,)*] [$($array:tt)*] $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!([$($array)*])] $($rest)*)
    };

    // Next element is a map.
    (@list [$($elems:expr,)*] {$($map:tt)*} $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!({$($map)*})] $($rest)*)
    };

    // Next element are bytes.
    (@list [$($elems:expr,)*] bytes($($bytes:tt)*) $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!(bytes($($bytes)*))] $($rest)*)
    };

    // Next element is an expression followed by comma.
    (@list [$($elems:expr,)*] $next:expr, $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!($next),] $($rest)*)
    };

    // Last element is an expression with no trailing comma.
    (@list [$($elems:expr,)*] $last:expr) => {
        $crate::__value_internal!(@list [$($elems,)* $crate::__value_internal!($last)])
    };

    // Comma after the most recent element.
    (@list [$($elems:expr),*] , $($rest:tt)*) => {
        $crate::__value_internal!(@list [$($elems,)*] $($rest)*)
    };

    // Unexpected token after most recent element.
    (@list [$($elems:expr),*] $unexpected:tt $($rest:tt)*) => {
        $crate::__value_unexpected!($unexpected)
    };

    //////////////////////////////////////////////////////////////////////////
    // TT muncher for parsing the inside of an map {...}. Each entry is
    // inserted into the given map variable.
    //
    // Must be invoked as: __value_internal!(@map $map () ($($tt)*) ($($tt)*))
    //
    // We require two copies of the input tokens so that we can match on one
    // copy and trigger errors on the other copy.
    //////////////////////////////////////////////////////////////////////////

    // Done.
    (@map $map:ident () () ()) => {};

    // Insert the current entry followed by trailing comma.
    (@map $map:ident [$($key:tt)+] ($value:expr) , $($rest:tt)*) => {
        let _ = $map.insert(($($key)+).into(), $value);
        $crate::__value_internal!(@map $map () ($($rest)*) ($($rest)*));
    };

    // Current entry followed by unexpected token.
    (@map $map:ident [$($key:tt)+] ($value:expr) $unexpected:tt $($rest:tt)*) => {
        $crate::__value_unexpected!($unexpected);
    };

    // Insert the last entry without trailing comma.
    (@map $map:ident [$($key:tt)+] ($value:expr)) => {
        let _ = $map.insert(($($key)+).into(), $value);
    };

    // Next value is `null`.
    (@map $map:ident ($($key:tt)+) (: null $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!(null)) $($rest)*);
    };

    // Next value is a list.
    (@map $map:ident ($($key:tt)+) (: [$($array:tt)*] $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!([$($array)*])) $($rest)*);
    };

    // Next value is a map.
    (@map $map:ident ($($key:tt)+) (: {$($mapp:tt)*} $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!({$($mapp)*})) $($rest)*);
    };

    // Next value are bytes.
    (@map $map:ident ($($key:tt)+) (: bytes( $($bytes:tt)* ) $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!(bytes($($bytes)*))) $($rest)*);
    };

    // Next value is an expression followed by comma.
    (@map $map:ident ($($key:tt)+) (: $value:expr , $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!($value)) , $($rest)*);
    };

    // Last value is an expression with no trailing comma.
    (@map $map:ident ($($key:tt)+) (: $value:expr) $copy:tt) => {
        $crate::__value_internal!(@map $map [$($key)+] ($crate::__value_internal!($value)));
    };

    // Missing value for last entry. Trigger a reasonable error message.
    (@map $map:ident ($($key:tt)+) (:) $copy:tt) => {
        // "unexpected end of macro invocation"
        $crate::__value_internal!();
    };

    // Missing colon and value for last entry. Trigger a reasonable error
    // message.
    (@map $map:ident ($($key:tt)+) () $copy:tt) => {
        // "unexpected end of macro invocation"
        $crate::__value_internal!();
    };

    // Misplaced colon. Trigger a reasonable error message.
    (@map $map:ident () (: $($rest:tt)*) ($colon:tt $($copy:tt)*)) => {
        // Takes no arguments so "no rules expected the token `:`".
        $crate::__value_unexpected!($colon);
    };

    // Found a comma inside a key. Trigger a reasonable error message.
    (@map $map:ident ($($key:tt)*) (, $($rest:tt)*) ($comma:tt $($copy:tt)*)) => {
        // Takes no arguments so "no rules expected the token `,`".
        $crate::__value_unexpected!($comma);
    };

    // Key is fully parenthesized. This avoids clippy double_parens false
    // positives because the parenthesization may be necessary here.
    (@map $map:ident () (($key:expr) : $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map ($key) (: $($rest)*) (: $($rest)*));
    };

    // Refuse to absorb colon token into key expression.
    (@map $map:ident ($($key:tt)*) (: $($unexpected:tt)+) $copy:tt) => {
        $crate::value_expect_expr_comma!($($unexpected)+);
    };

    // Munch a token into the current key.
    (@map $map:ident ($($key:tt)*) ($tt:tt $($rest:tt)*) $copy:tt) => {
        $crate::__value_internal!(@map $map ($($key)* $tt) ($($rest)*) ($($rest)*));
    };

    //////////////////////////////////////////////////////////////////////////
    // The main implementation.
    //
    // Must be invoked as: __value_internal!($($value)+)
    //////////////////////////////////////////////////////////////////////////

    (null) => {
        $crate::ValueSend::Null
    };

    ([]) => {
        $crate::ValueSend::List(vec![])
    };

    ([ $($tt:tt)+ ]) => {
        $crate::ValueSend::List($crate::__value_internal!(@list [] $($tt)+))
    };

    ({$(,)?}) => {
        $crate::ValueSend::Map(std::collections::HashMap::new())
    };

    ({ $($tt:tt)+ }) => {
        $crate::ValueSend::Map({
            let mut map = std::collections::HashMap::new();
            $crate::__value_internal!(@map map () ($($tt)+) ($($tt)+));
            map
        })
    };

    (bytes()) => {
        $crate::ValueSend::Bytes(vec![])
    };

    (bytes( $($tt:tt),+ $(,)?)) => {
        $crate::ValueSend::Bytes(vec![$($tt),+])
    };

    // Any Serialize type: numbers, strings, struct literals, variables etc.
    // Must be below every other rule.
    ($other:expr) => {
        $other.into()
    };
}

#[macro_export]
#[doc(hidden)]
macro_rules! __value_unexpected {
    () => {};
}

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use crate::value::spatial::*;
    use crate::value::ValueSend;

    #[test]
    fn test_null() {
        assert_eq!(value!(null), ValueSend::Null)
    }

    #[rstest]
    #[case(value!(true), ValueSend::Boolean(true))]
    #[case(value!(false), ValueSend::Boolean(false))]
    fn test_boolean(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!(1), ValueSend::Integer(1))]
    #[case(value!(-1), ValueSend::Integer(-1))]
    #[case(value!(1u8), ValueSend::Integer(1))]
    #[case(value!(1i8), ValueSend::Integer(1))]
    #[case(value!(-1i8), ValueSend::Integer(-1))]
    #[case(value!(1u16), ValueSend::Integer(1))]
    #[case(value!(1i16), ValueSend::Integer(1))]
    #[case(value!(-1i16), ValueSend::Integer(-1))]
    #[case(value!(1u32), ValueSend::Integer(1))]
    #[case(value!(1i32), ValueSend::Integer(1))]
    #[case(value!(-1i32), ValueSend::Integer(-1))]
    #[case(value!(1i64), ValueSend::Integer(1))]
    #[case(value!(-1i64), ValueSend::Integer(-1))]
    #[case(value!(i64::MAX), ValueSend::Integer(i64::MAX))]
    #[case(value!(i64::MIN), ValueSend::Integer(i64::MIN))]
    fn test_int(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!(1.0f32), ValueSend::Float(1.))]
    #[case(value!(-1.0f32), ValueSend::Float(-1.))]
    #[case(value!(1.0f64), ValueSend::Float(1.))]
    #[case(value!(-1.0f64), ValueSend::Float(-1.))]
    #[case(value!(f32::NAN), ValueSend::Float(f32::NAN.into()))]
    #[case(value!(f32::MIN), ValueSend::Float(f32::MIN.into()))]
    #[case(value!(f32::MAX), ValueSend::Float(f32::MAX.into()))]
    #[case(value!(f64::NAN), ValueSend::Float(f64::NAN))]
    #[case(value!(f64::MIN), ValueSend::Float(f64::MIN))]
    #[case(value!(f64::MAX), ValueSend::Float(f64::MAX))]
    fn test_float(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        let ValueSend::Float(input) = input else {
            panic!("input is not float but {:?}", input);
        };
        let ValueSend::Float(output) = output else {
            panic!("output is not float but {:?}", output);
        };

        if input.is_nan() {
            assert!(output.is_nan())
        } else {
            assert_eq!(input, output);
        }
    }

    #[rstest]
    #[case(value!(bytes()), ValueSend::Bytes(vec![]))]
    #[case(value!(bytes(1)), ValueSend::Bytes(vec![1]))]
    #[case(value!(bytes(1,)), ValueSend::Bytes(vec![1]))]
    fn test_bytes(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!(""), ValueSend::String("".into()))]
    #[case(value!("foo"), ValueSend::String("foo".into()))]
    #[case(value!("foo bar"), ValueSend::String("foo bar".into()))]
    #[case(value!(String::from("")), ValueSend::String("".into()))]
    #[case(value!(String::from("foo")), ValueSend::String("foo".into()))]
    #[case(value!(String::from("foo bar")), ValueSend::String("foo bar".into()))]
    fn test_string(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!([]), ValueSend::List(vec![]))]
    #[case(value!([,]), ValueSend::List(vec![]))]
    #[case(value!([null]), ValueSend::List(vec![ValueSend::Null]))]
    #[case(value!([null,]), ValueSend::List(vec![ValueSend::Null]))]
    #[case(value!([true]), ValueSend::List(vec![ValueSend::Boolean(true)]))]
    #[case(value!([true,]), ValueSend::List(vec![ValueSend::Boolean(true)]))]
    #[case(value!([false]), ValueSend::List(vec![ValueSend::Boolean(false)]))]
    #[case(value!([false,]), ValueSend::List(vec![ValueSend::Boolean(false)]))]
    #[case(value!([1]), ValueSend::List(vec![ValueSend::Integer(1)]))]
    #[case(value!([1,]), ValueSend::List(vec![ValueSend::Integer(1)]))]
    #[case(value!([1.]), ValueSend::List(vec![ValueSend::Float(1.)]))]
    #[case(value!([1.,]), ValueSend::List(vec![ValueSend::Float(1.)]))]
    #[case(value!([bytes()]), ValueSend::List(vec![ValueSend::Bytes(vec![])]))]
    #[case(value!([bytes(1, 2, 3)]), ValueSend::List(vec![ValueSend::Bytes(vec![1, 2, 3])]))]
    #[case(value!([bytes(1, 2, 3,)]), ValueSend::List(vec![ValueSend::Bytes(vec![1, 2, 3])]))]
    #[case(
        value!([1, [2], 3]),
        ValueSend::List(vec![
            ValueSend::Integer(1),
            ValueSend::List(vec![ValueSend::Integer(2)]),
            ValueSend::Integer(3),
        ])
    )]
    #[case(
        value!([null, 1, 2., {"foo": "bar"}, bytes(1)]),
        ValueSend::List(vec![
            ValueSend::Null,
            ValueSend::Integer(1),
            ValueSend::Float(2.0),
            ValueSend::Map(hash_map!("foo".into() => ValueSend::String("bar".into()))),
            ValueSend::Bytes(vec![1]),
        ])
    )]
    fn test_list(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!({}), ValueSend::Map(hash_map!()))]
    #[case(value!({,}), ValueSend::Map(hash_map!()))]
    #[case(value!({"a": null}), ValueSend::Map(hash_map!("a".into() => ValueSend::Null)))]
    #[case(value!({"a": null,}), ValueSend::Map(hash_map!("a".into() => ValueSend::Null)))]
    #[case(value!({"a": true}), ValueSend::Map(hash_map!("a".into() => ValueSend::Boolean(true))))]
    #[case(value!({"a": false}), ValueSend::Map(hash_map!("a".into() => ValueSend::Boolean(false))))]
    #[case(value!({"a": 1}), ValueSend::Map(hash_map!("a".into() => ValueSend::Integer(1))))]
    #[case(value!({"a": 1,}), ValueSend::Map(hash_map!("a".into() => ValueSend::Integer(1))))]
    #[case(
        value!({"a": 1, "b": 1.}), 
        ValueSend::Map(hash_map!(
            "a".into() => ValueSend::Integer(1),
            "b".into() => ValueSend::Float(1.),
        ))
    )]
    #[case(
        value!({"a": 1, "b": 1., "c": [1, "foo", null], "d": {"bar": false}}), 
        ValueSend::Map(hash_map!(
            "a".into() => ValueSend::Integer(1),
            "b".into() => ValueSend::Float(1.),
            "c".into() => ValueSend::List(vec![
                ValueSend::Integer(1), ValueSend::String("foo".into()), ValueSend::Null,
            ]),
            "d".into() => ValueSend::Map(hash_map!("bar".into() => ValueSend::Boolean(false))),
        ))
    )]
    fn test_map(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }

    #[rstest]
    #[case(value!(
        Cartesian2D::new(1., 2.)),
        ValueSend::Cartesian2D(Cartesian2D::new(1., 2.))
    )]
    #[case(value!(
        Cartesian3D::new(1., 2., 3.)),
        ValueSend::Cartesian3D(Cartesian3D::new(1., 2., 3.))
    )]
    #[case(value!(
        WGS84_2D::new(1., 2.)),
        ValueSend::WGS84_2D(WGS84_2D::new(1., 2.))
    )]
    #[case(value!(
        WGS84_3D::new(1., 2., 3.)),
        ValueSend::WGS84_3D(WGS84_3D::new(1., 2., 3.))
    )]
    fn test_structs(#[case] input: ValueSend, #[case] output: ValueSend) {
        dbg!(&input, &output);
        assert_eq!(input, output);
    }
}
