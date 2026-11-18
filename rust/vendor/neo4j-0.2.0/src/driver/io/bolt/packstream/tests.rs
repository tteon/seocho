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

use std::collections::{HashMap, HashSet};

use rstest::rstest;

use super::super::super::bolt::BoltStructTranslator;
use super::super::bolt5x0::Bolt5x0StructTranslator;
use super::deserialize::{PackStreamDeserializer, PackStreamDeserializerImpl};
use super::error::{PackStreamDeserializeError, PackStreamSerializeError};
use super::serialize::PackStreamSerializerImpl;
use crate::macros::hash_map;
use crate::value;
use crate::value::spatial::{Cartesian2D, Cartesian3D, WGS84_2D, WGS84_3D};
use crate::value::{ValueReceive, ValueSend};

// =============
// Test Decoding
// =============

fn mk_value<V: Into<ValueSend>>(v: V) -> ValueReceive {
    ValueReceive::from(v.into())
}

fn decode_raw(input: Vec<u8>) -> (Result<ValueReceive, PackStreamDeserializeError>, Vec<u8>) {
    let translator = Bolt5x0StructTranslator {};
    let mut reader = input.as_slice();
    let mut deserializer = PackStreamDeserializerImpl::new(&mut reader);
    let result = deserializer.load(&translator);
    let rest = reader.to_vec();
    (result, rest)
}

fn decode(input: Vec<u8>) -> (ValueReceive, Vec<u8>) {
    let (result, rest) = decode_raw(input);
    (result.unwrap(), rest)
}

#[rstest]
#[case(vec![0xC0], ValueReceive::Null)]
fn test_decode_null(#[case] input: Vec<u8>, #[case] output: ValueReceive) {
    dbg!(&input);
    let (result, rest) = decode(input);
    assert_eq!(result, output);
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xC2], ValueReceive::Boolean(false))]
#[case(vec![0xC3], ValueReceive::Boolean(true))]
fn test_decode_bool(#[case] input: Vec<u8>, #[case] output: ValueReceive) {
    dbg!(&input);
    let (result, rest) = decode(input);
    assert_eq!(result, output);
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xF0], ValueReceive::Integer(-16))]
#[case(vec![0xFF], ValueReceive::Integer(-1))]
#[case(vec![0x00], ValueReceive::Integer(0))]
#[case(vec![0x01], ValueReceive::Integer(1))]
#[case(vec![0x7F], ValueReceive::Integer(127))]
#[case(vec![0xC8, 0x80], ValueReceive::Integer(-128))]
#[case(vec![0xC8, 0xD6], ValueReceive::Integer(-42))]
#[case(vec![0xC8, 0x00], ValueReceive::Integer(0))]
#[case(vec![0xC8, 0x2A], ValueReceive::Integer(42))]
#[case(vec![0xC8, 0x7F], ValueReceive::Integer(127))]
#[case(vec![0xC9, 0x80, 0x00], ValueReceive::Integer(-32768))]
#[case(vec![0xC9, 0xFF, 0x80], ValueReceive::Integer(-128))]
#[case(vec![0xC9, 0xFF, 0xD6], ValueReceive::Integer(-42))]
#[case(vec![0xC9, 0x00, 0x00], ValueReceive::Integer(0))]
#[case(vec![0xC9, 0x00, 0x2A], ValueReceive::Integer(42))]
#[case(vec![0xC9, 0x00, 0x7F], ValueReceive::Integer(127))]
#[case(vec![0xC9, 0x7F, 0xFF], ValueReceive::Integer(32767))]
#[case(vec![0xCA, 0x80, 0x00, 0x00, 0x00], ValueReceive::Integer(-2147483648))]
#[case(vec![0xCA, 0xFF, 0xFF, 0x80, 0x00], ValueReceive::Integer(-32768))]
#[case(vec![0xCA, 0xFF, 0xFF, 0xFF, 0x80], ValueReceive::Integer(-128))]
#[case(vec![0xCA, 0xFF, 0xFF, 0xFF, 0xD6], ValueReceive::Integer(-42))]
#[case(vec![0xCA, 0x00, 0x00, 0x00, 0x00], ValueReceive::Integer(0))]
#[case(vec![0xCA, 0x00, 0x00, 0x00, 0x2A], ValueReceive::Integer(42))]
#[case(vec![0xCA, 0x00, 0x00, 0x00, 0x7F], ValueReceive::Integer(127))]
#[case(vec![0xCA, 0x00, 0x00, 0x7F, 0xFF], ValueReceive::Integer(32767))]
#[case(vec![0xCA, 0x7F, 0xFF, 0xFF, 0xFF], ValueReceive::Integer(2147483647))]
#[case(vec![0xCB, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], ValueReceive::Integer(-9223372036854775808))]
#[case(vec![0xCB, 0xFF, 0xFF, 0xFF, 0xFF, 0x80, 0x00, 0x00, 0x00], ValueReceive::Integer(-2147483648))]
#[case(vec![0xCB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x80, 0x00], ValueReceive::Integer(-32768))]
#[case(vec![0xCB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x80], ValueReceive::Integer(-128))]
#[case(vec![0xCB, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xD6], ValueReceive::Integer(-42))]
#[case(vec![0xCB, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], ValueReceive::Integer(0))]
#[case(vec![0xCB, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2A], ValueReceive::Integer(42))]
#[case(vec![0xCB, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F], ValueReceive::Integer(127))]
#[case(vec![0xCB, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xFF], ValueReceive::Integer(32767))]
#[case(vec![0xCB, 0x00, 0x00, 0x00, 0x00, 0x7F, 0xFF, 0xFF, 0xFF], ValueReceive::Integer(2147483647))]
#[case(vec![0xCB, 0x7F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF], ValueReceive::Integer(9223372036854775807))]
fn test_decode_integer(#[case] input: Vec<u8>, #[case] output: ValueReceive) {
    dbg!(&input);
    let (result, rest) = decode(input);
    assert_eq!(result, output);
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xC1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], 0.)]
#[case(vec![0xC1, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], -0.)]
#[case(vec![0xC1, 0x7F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f64::INFINITY)]
#[case(vec![0xC1, 0xFF, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f64::NEG_INFINITY)]
#[case(vec![0xC1, 0x7F, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f64::NAN)]
#[case(vec![0xC1, 0x3F, 0xF3, 0xAE, 0x14, 0x7A, 0xE1, 0x47, 0xAE], 1.23)]
fn test_decode_float(#[case] input: Vec<u8>, #[case] output: f64) {
    dbg!(&input);
    let (result, rest) = decode(input);
    if output.is_nan() {
        match result {
            ValueReceive::Float(result) => assert!(result.is_nan()),
            _ => panic!("output was not float"),
        }
    } else {
        assert_eq!(result, ValueReceive::Float(output));
    }
    assert!(rest.is_empty());
}

fn damn_long_vec(header: Option<Vec<u8>>, size: usize) -> Vec<u8> {
    let mut res = match header {
        Some(mut header) => {
            header.reserve(size);
            header
        }
        None => Vec::with_capacity(size),
    };
    for i in 0..size {
        res.push(i as u8);
    }
    res
}

#[rstest]
#[case(vec![0xCC, 0x00], vec![])]
#[case(vec![0xCC, 0x01, 0x00], vec![0x00])]
#[case(vec![0xCC, 0x01, 0x42], vec![0x42])]
#[case(vec![0xCC, 0x01, 0xFF], vec![0xFF])]
#[case(damn_long_vec(Some(vec![0xCC, 0xFF]), 0xFF), damn_long_vec(None, 0xFF))]
#[case(vec![0xCD, 0x00, 0x00], vec![])]
#[case(vec![0xCD, 0x00, 0x01, 0xFF], vec![0xFF])]
#[case(damn_long_vec(Some(vec![0xCD, 0xFF, 0xFF]), 0xFFFF), damn_long_vec(None, 0xFFFF))]
#[case(vec![0xCE, 0x00, 0x00, 0x00, 0x00], vec![])]
#[case(vec![0xCE, 0x00, 0x00, 0x00, 0x01, 0xFF], vec![0xFF])]
#[case(damn_long_vec(Some(vec![0xCE, 0x00, 0xFE, 0xFF, 0xFF]), 0x00FEFFFF), damn_long_vec(None, 0x00FEFFFF))]
fn test_decode_bytes(#[case] input: Vec<u8>, #[case] output: Vec<u8>) {
    if input.len() < 50 {
        dbg!(&input);
    }
    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::Bytes(output));
    assert!(rest.is_empty());
}

// this test is only feasible with a release build
#[cfg(not(debug_assertions))]
#[rstest]
#[case(damn_long_vec(Some(vec![0xCE, 0x7F, 0xFF, 0xFF, 0xFF]), 0x7FFFFFFF), damn_long_vec(None, 0x7FFFFFFF))]
fn test_decode_max_len_bytes(#[case] input: Vec<u8>, #[case] output: Vec<u8>) {
    println!("bytes of length {}", input.len());

    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::Bytes(output));
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0x80], "")]
#[case(vec![0x81, 0x41], "A")]
#[case(vec![0x84, 0xF0, 0x9F, 0xA4, 0x98], "🤘")]
#[case(vec![0xD0, 0x1A, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C,
            0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A],
       "ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
#[case(vec![0xD0, 0x12, 0x47, 0x72, 0xC3, 0xB6, 0xC3, 0x9F, 0x65, 0x6E, 0x6D, 0x61, 0xC3, 0x9F,
            0x73, 0x74, 0xC3, 0xA4, 0x62, 0x65],
       "Größenmaßstäbe")]
#[case(vec![0xD1, 0x00, 0x01, 0x41], "A")]
#[case(vec![0xD2, 0x00, 0x00, 0x00, 0x01, 0x41], "A")]
fn test_decode_string(#[case] input: Vec<u8>, #[case] output: &str) {
    dbg!(&input);
    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::String(String::from(output)));
    assert!(rest.is_empty());
}

// this test is only feasible with a release build
#[cfg(not(debug_assertions))]
#[rstest]
fn test_decode_max_len_string() {
    let size = 0x7FFFFFFF;

    let mut input = vec![0xD2, 0x7F, 0xFF, 0xFF, 0xFF];
    input.extend(std::iter::repeat(0x41).take(size));
    let mut output = String::with_capacity(size);
    output.extend(std::iter::repeat('A').take(size));

    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::String(output));
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0x90], vec![])]
#[case(vec![0xD4, 0x00], vec![])]
#[case(vec![0xD5, 0x00, 0x00], vec![])]
#[case(vec![0xD6, 0x00, 0x00, 0x00, 0x00], vec![])]
#[case(vec![0x91, 0x01], vec![mk_value(1)])]
#[case(vec![0xD4, 0x01, 0x01], vec![mk_value(1)])]
#[case(vec![0xD4, 0x03, 0x01, 0x02, 0x03], vec![mk_value(1), mk_value(2), mk_value(3)])]
#[case(vec![0xD5, 0x00, 0x01, 0x01], vec![mk_value(1)])]
#[case(vec![0xD6, 0x00, 0x00, 0x00, 0x01, 0x01], vec![mk_value(1)])]
#[case(vec![0x91, 0x91, 0x01], vec![mk_value(value!([1]))])]
#[case(vec![0x93,
            0x01,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x85, 0x74, 0x68, 0x72, 0x65, 0x65],
        vec![mk_value(1), mk_value(2.0), mk_value(String::from("three"))])]
fn test_decode_list(#[case] input: Vec<u8>, #[case] output: Vec<ValueReceive>) {
    dbg!(&input);
    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::List(output));
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xA0], hash_map!())]
#[case(vec![0xA1, 0x81, 0x41, 0x01], hash_map!("A".into() => mk_value(1)))]
#[case(vec![0xA1, 0x83, 0x6F, 0x6E, 0x65, 0x84, 0x65, 0x69, 0x6E, 0x73],
       hash_map!("one".into() => mk_value(String::from("eins"))))]
#[case(vec![0xD8, 0x03, 0x81, 0x41, 0x01, 0x81, 0x42, 0x02, 0x81, 0x41, 0x03],
       hash_map!("A".into() => mk_value(3), "B".into() => mk_value(2)))]
#[case(vec![0xD9, 0x00, 0x03, 0x81, 0x41, 0x01, 0x81, 0x42, 0x02, 0x81, 0x41, 0x03],
       hash_map!("A".into() => mk_value(3), "B".into() => mk_value(2)))]
#[case(vec![0xDA, 0x00, 0x00, 0x00, 0x03, 0x81, 0x41, 0x01, 0x81, 0x42, 0x02, 0x81, 0x41, 0x03],
       hash_map!("A".into() => mk_value(3), "B".into() => mk_value(2)))]
fn test_decode_dict(#[case] input: Vec<u8>, #[case] output: HashMap<String, ValueReceive>) {
    dbg!(&input);
    let (result, rest) = decode(input);

    assert_eq!(result, ValueReceive::Map(output));
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xB3, 0x58, 0xC9, 0x1C, 0x23,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
       mk_value(Cartesian2D::new(1., 2.)))]
#[case(vec![0xB4, 0x59, 0xC9, 0x23, 0xC5,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
       mk_value(Cartesian3D::new(1., 2., 3.)))]
#[case(vec![0xB3, 0x58, 0xC9, 0x10, 0xE6,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
       mk_value(WGS84_2D::new(1., 2.)))]
#[case(vec![0xB4, 0x59, 0xC9, 0x13, 0x73,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
       mk_value(WGS84_3D::new(1., 2., 3.)))]
// TODO: all other struct types
fn test_decode_struct(#[case] input: Vec<u8>, #[case] output: ValueReceive) {
    dbg!(&input);
    let (result, rest) = decode(input);

    assert_eq!(result, output);
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![0xB0, 0xFF])]
#[case(vec![0xB1, 0xFF, 0xC3])]
fn test_decode_unknown_struct(#[case] input: Vec<u8>) {
    dbg!(&input);
    let (result, rest) = decode(input);

    assert!(matches!(result, ValueReceive::BrokenValue(_)));
    assert!(rest.is_empty());
}

#[rstest]
#[case(vec![], "no marker found")]
// TODO: cover all error cases
fn test_decode_error(#[case] input: Vec<u8>, #[case] error: &'static str) {
    dbg!(&input);
    let (result, rest) = decode_raw(input);

    result.expect_err("expected to fail");

    dbg!(error);
    let message = error.to_string();
    assert!(message.to_lowercase().contains(error));
    assert!(rest.is_empty());
}

// =============
// Test Encoding
// =============

fn encode_raw(input: &ValueSend) -> Result<Vec<u8>, PackStreamSerializeError> {
    let translator = Bolt5x0StructTranslator {};
    let mut output = Vec::new();
    let mut serializer = PackStreamSerializerImpl::new(&mut output);
    translator.serialize(&mut serializer, input)?;
    Ok(output)
}

fn encode(input: &ValueSend) -> Vec<u8> {
    encode_raw(input).unwrap()
}

#[rstest]
#[case(ValueSend::Null, vec![0xC0])]
fn test_encode_null(#[case] input: ValueSend, #[case] output: Vec<u8>) {
    let result = encode(&input);

    assert_eq!(result, output);
}

#[rstest]
#[case(false, vec![0xC2])]
#[case(true, vec![0xC3])]
fn test_encode_bool(#[case] input: bool, #[case] output: Vec<u8>) {
    let result = encode(&ValueSend::Boolean(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(-16, vec![0xF0])]
#[case(-1, vec![0xFF])]
#[case(0, vec![0x00])]
#[case(1, vec![0x01])]
#[case(127, vec![0x7F])]
#[case(-128, vec![0xC8, 0x80])]
#[case(-32768, vec![0xC9, 0x80, 0x00])]
#[case(32767, vec![0xC9, 0x7F, 0xFF])]
#[case(-2147483648, vec![0xCA, 0x80, 0x00, 0x00, 0x00])]
#[case(2147483647, vec![0xCA, 0x7F, 0xFF, 0xFF, 0xFF])]
#[case(-9223372036854775808i64, vec![0xCB, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(9223372036854775807i64, vec![0xCB, 0x7F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])]
fn test_encode_integer(#[case] input: i64, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&ValueSend::Integer(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(0., vec![0xC1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(-0., vec![0xC1, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(f64::INFINITY, vec![0xC1, 0x7F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(f64::NEG_INFINITY, vec![0xC1, 0xFF, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(f64::NAN, vec![0xC1, 0x7F, 0xF8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(1.23, vec![0xC1, 0x3F, 0xF3, 0xAE, 0x14, 0x7A, 0xE1, 0x47, 0xAE])]
fn test_encode_float(#[case] input: f64, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&ValueSend::Float(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(vec![], vec![0xCC, 0x00])]
#[case(vec![0x00], vec![0xCC, 0x01, 0x00])]
#[case(vec![0x42], vec![0xCC, 0x01, 0x42])]
#[case(vec![0xFF], vec![0xCC, 0x01, 0xFF])]
#[case(damn_long_vec(None, 0xFF), damn_long_vec(Some(vec![0xCC, 0xFF]), 0xFF))]
#[case(damn_long_vec(None, 0xFFFF), damn_long_vec(Some(vec![0xCD, 0xFF, 0xFF]), 0xFFFF))]
#[case(damn_long_vec(None, 0x00FEFFFF), damn_long_vec(Some(vec![0xCE, 0x00, 0xFE, 0xFF, 0xFF]), 0x00FEFFFF))]
fn test_encode_bytes(#[case] input: Vec<u8>, #[case] output: Vec<u8>) {
    if input.len() < 50 {
        dbg!(&input);
    } else {
        println!("bytes of length {}", input.len());
    }
    let result = encode(&ValueSend::Bytes(input));

    assert_eq!(result, output);
}

// this test is only feasible with a release build
#[cfg(not(debug_assertions))]
#[rstest]
#[case(damn_long_vec(None, 0x7FFFFFFF), damn_long_vec(Some(vec![0xCE, 0x7F, 0xFF, 0xFF, 0xFF]), 0x7FFFFFFF))]
fn test_encode_max_len_bytes(#[case] input: Vec<u8>, #[case] output: Vec<u8>) {
    println!("bytes of length {}", input.len());

    let result = encode(&ValueSend::Bytes(input));

    assert_eq!(result, output);
}

#[rstest]
#[case("", vec![0x80])]
#[case("A", vec![0x81, 0x41])]
#[case("🤘", vec![0x84, 0xF0, 0x9F, 0xA4, 0x98])]
#[case("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
       vec![0xD0, 0x1A, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C,
            0x4D, 0x4E, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A])]
#[case("Größenmaßstäbe",
       vec![0xD0, 0x12, 0x47, 0x72, 0xC3, 0xB6, 0xC3, 0x9F, 0x65, 0x6E, 0x6D, 0x61, 0xC3, 0x9F,
            0x73, 0x74, 0xC3, 0xA4, 0x62, 0x65])]
fn test_encode_string(#[case] input: &str, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&ValueSend::String(String::from(input)));

    assert_eq!(result, output);
}

#[rstest]
#[case(0xFF, vec![0xD0, 0xFF])]
#[case(0xFFFF, vec![0xD1, 0xFF, 0xFF])]
#[case(0x10000, vec![0xD2, 0x00, 0x01, 0x00, 0x00])]
fn test_encode_long_string(#[case] size: usize, #[case] mut header: Vec<u8>) {
    header.extend(std::iter::repeat(0x41).take(size));
    let output = header;
    let mut input = String::with_capacity(size);
    input.extend(std::iter::repeat('A').take(size));

    let result = encode(&ValueSend::String(input));

    assert_eq!(result, output);
}

// this test is only feasible with a release build
#[cfg(not(debug_assertions))]
#[rstest]
fn test_encode_max_len_string() {
    let size = 0x7FFFFFFF;

    let mut output = vec![0xD2, 0x7F, 0xFF, 0xFF, 0xFF];
    output.extend(std::iter::repeat(0x41).take(size));
    let mut input = String::with_capacity(size);
    input.extend(std::iter::repeat('A').take(size));

    let result = encode(&ValueSend::String(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(vec![], vec![0x90])]
#[case(vec![1.into()], vec![0x91, 0x01])]
#[case(vec![1.into(), 2.into(), 3.into()], vec![0x93, 0x01, 0x02, 0x03])]
#[case(vec![vec![ValueSend::Integer(1)].into()], vec![0x91, 0x91, 0x01])]
#[case(vec![1.into(), 2.0.into(), String::from("three").into()],
       vec![0x93,
            0x01,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x85, 0x74, 0x68, 0x72, 0x65, 0x65])]
fn test_encode_list(#[case] input: Vec<ValueSend>, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&ValueSend::List(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(0xFF, vec![0xD4, 0xFF])]
#[case(0xFFFF, vec![0xD5, 0xFF, 0xFF])]
#[case(0x10000, vec![0xD6, 0x00, 0x01, 0x00, 0x00])]
fn test_encode_long_list(#[case] size: usize, #[case] mut header: Vec<u8>) {
    header.extend(std::iter::repeat(0x01).take(size));
    let output = header;
    let mut input = Vec::with_capacity(size);
    input.extend(std::iter::repeat_with(|| ValueSend::Integer(1)).take(size));

    let result = encode(&ValueSend::List(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(hash_map!(), vec![0xA0])]
#[case(hash_map!("A".into() => 1.into()), vec![0xA1, 0x81, 0x41, 0x01])]
#[case(hash_map!(String::from("one") => String::from("eins").into()),
       vec![0xA1, 0x83, 0x6F, 0x6E, 0x65, 0x84, 0x65, 0x69, 0x6E, 0x73])]
fn test_encode_dict(#[case] input: HashMap<String, ValueSend>, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&ValueSend::Map(input));

    assert_eq!(result, output);
}

#[rstest]
#[case(0xFF, vec![0xD8, 0xFF])]
#[case(0xFFFF, vec![0xD9, 0xFF, 0xFF])]
#[case(0x10000, vec![0xDA, 0x00, 0x01, 0x00, 0x00])]
fn test_encode_long_dict(#[case] size: usize, #[case] header: Vec<u8>) {
    let mut output = HashSet::with_capacity(size);
    let mut input = HashMap::with_capacity(size);
    let mut bytes = Vec::with_capacity(6);
    for i in 0..size {
        let key = format!("{:04X}", i);
        let value = i % 100;
        input.insert(key.clone(), ValueSend::Integer(value as i64));

        bytes.clear();
        bytes.push(0x84);
        bytes.extend(key.as_bytes());
        bytes.extend(u8::to_be_bytes(value as u8));
        assert_eq!(bytes.len(), 6);
        assert!(output.insert(bytes.clone()));
    }

    let result = encode(&ValueSend::Map(input));

    assert_eq!(result[0..header.len()], header);
    let rest = &result[header.len()..];
    for bytes in rest.chunks(6) {
        output.take(bytes).unwrap();
    }
}

#[rstest]
#[case(Cartesian2D::new(1., 2.).into(),
       vec![0xB3, 0x58, 0xC9, 0x1C, 0x23,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(Cartesian3D::new(1., 2., 3.).into(),
       vec![0xB4, 0x59, 0xC9, 0x23, 0xC5,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(WGS84_2D::new(1., 2.).into(),
       vec![0xB3, 0x58, 0xC9, 0x10, 0xE6,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
#[case(WGS84_3D::new(1., 2., 3.).into(),
       vec![0xB4, 0x59, 0xC9, 0x13, 0x73,
            0xC1, 0x3F, 0xF0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0xC1, 0x40, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])]
// TODO: all other struct types
fn test_encode_struct(#[case] input: ValueSend, #[case] output: Vec<u8>) {
    dbg!(&input);

    let result = encode(&input);

    assert_eq!(result, output);
}
