// todo: serde fun

// use super::Value;
// use serde::de::{Deserialize, Deserializer, EnumAccess, MapAccess, SeqAccess, Visitor};
// use std::collections::HashMap;
// use std::error::Error;
// use std::fmt;
//
// impl<'de> Deserialize<'de> for Value {
//     #[inline]
//     fn deserialize<D>(deserializer: D) -> Result<Value, D::Error>
//     where
//         D: Deserializer<'de>,
//     {
//         struct ValueVisitor;
//
//         impl<'de> Visitor<'de> for ValueVisitor {
//             type Value = Value;
//
//             fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
//                 formatter.write_str("a Neo4j compatible types")
//             }
//
//             fn visit_bool<E>(self, v: bool) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Boolean(v))
//             }
//
//             fn visit_i8<E>(self, v: i8) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_i16<E>(self, v: i16) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_i32<E>(self, v: i32) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_i64<E>(self, v: i64) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_u8<E>(self, v: u8) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_u16<E>(self, v: u16) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_u32<E>(self, v: u32) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Integer(v.into()))
//             }
//
//             fn visit_f32<E>(self, v: f32) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Float(v.into()))
//             }
//
//             fn visit_f64<E>(self, v: f64) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Float(v.into()))
//             }
//
//             fn visit_char<E>(self, v: char) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::String(v.into()))
//             }
//
//             fn visit_str<E>(self, v: &str) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::String(v.into()))
//             }
//
//             fn visit_borrowed_str<E>(self, v: &'de str) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_string<E>(self, v: String) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_bytes<E>(self, v: &[u8]) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_borrowed_bytes<E>(self, v: &'de [u8]) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_byte_buf<E>(self, v: Vec<u8>) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_none<E>(self) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
//             where
//                 D: Deserializer<'de>,
//             {
//                 Deserialize::deserialize(deserializer)
//             }
//
//             fn visit_unit<E>(self) -> Result<Self::Value, E>
//             where
//                 E: Error,
//             {
//                 Ok(Value::Null)
//             }
//
//             fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
//             where
//                 A: SeqAccess<'de>,
//             {
//                 let mut vec = Vec::new();
//
//                 while let Some(v) = seq.next_element()? {
//                     vec.push(v)
//                 }
//
//                 Ok(Value::List(vec))
//             }
//
//             fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
//             where
//                 A: MapAccess<'de>,
//             {
//                 // TODO: limit to string keys
//                 let mut dict = HashMap::new();
//                 while let Some((key, value)) = map.next_entry()? {
//                     dict.insert(key, value);
//                 }
//                 Ok(Value::Dictionary(dict))
//             }
//         }
//
//         deserializer.deserialize_any(ValueVisitor)
//     }
// }
