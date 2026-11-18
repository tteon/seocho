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

use std::collections::HashMap;
use std::error::Error;
use std::io::Read;

use super::error::PackStreamDeserializeError;
use crate::driver::io::bolt::BoltStructTranslator;
use crate::value::ValueReceive;

pub(crate) trait PackStreamDeserializer {
    type Error: Error;

    fn make_error(reason: String) -> Self::Error;

    fn load<B: BoltStructTranslator>(&mut self, bolt: &B) -> Result<ValueReceive, Self::Error>;
}

pub(crate) struct PackStreamDeserializerImpl<'a, R> {
    reader: &'a mut R,
}

impl<'a, R: Read + 'a> PackStreamDeserializerImpl<'a, R> {
    pub fn new(reader: &'a mut R) -> PackStreamDeserializerImpl<'a, R> {
        PackStreamDeserializerImpl { reader }
    }

    fn decode_i8(reader: &mut impl Read) -> Result<i8, PackStreamDeserializeError> {
        let mut buffer = [0; 1];
        reader.read_exact(&mut buffer)?;
        let value = i8::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_i16(reader: &mut impl Read) -> Result<i16, PackStreamDeserializeError> {
        let mut buffer = [0; 2];
        reader.read_exact(&mut buffer)?;
        let value = i16::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_i32(reader: &mut impl Read) -> Result<i32, PackStreamDeserializeError> {
        let mut buffer = [0; 4];
        reader.read_exact(&mut buffer)?;
        let value = i32::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_i64(reader: &mut impl Read) -> Result<i64, PackStreamDeserializeError> {
        let mut buffer = [0; 8];
        reader.read_exact(&mut buffer)?;
        let value = i64::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_f64(reader: &mut impl Read) -> Result<f64, PackStreamDeserializeError> {
        let mut buffer = [0; 8];
        reader.read_exact(&mut buffer)?;
        let value = f64::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_u8(reader: &mut impl Read) -> Result<u8, PackStreamDeserializeError> {
        let mut buffer = [0; 1];
        reader.read_exact(&mut buffer)?;
        let value = u8::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_u16(reader: &mut impl Read) -> Result<u16, PackStreamDeserializeError> {
        let mut buffer = [0; 2];
        reader.read_exact(&mut buffer)?;
        let value = u16::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_u32(reader: &mut impl Read) -> Result<u32, PackStreamDeserializeError> {
        let mut buffer = [0; 4];
        reader.read_exact(&mut buffer)?;
        let value = u32::from_be_bytes(buffer);
        Ok(value)
    }

    fn decode_bytes(
        reader: &mut impl Read,
        size: usize,
    ) -> Result<Vec<u8>, PackStreamDeserializeError> {
        let mut bytes = vec![0; size];
        reader.read_exact(bytes.as_mut_slice())?;
        Ok(bytes)
    }

    fn decode_string(
        reader: &mut impl Read,
        size: usize,
    ) -> Result<String, PackStreamDeserializeError> {
        let bytes = Self::decode_bytes(reader, size)?;
        Ok(String::from_utf8_lossy(bytes.as_slice()).into_owned())
    }

    fn decode_list<D: PackStreamDeserializer, B: BoltStructTranslator>(
        deserializer: &mut D,
        bolt: &B,
        size: usize,
    ) -> Result<Vec<ValueReceive>, D::Error> {
        let mut list = Vec::with_capacity(size);
        for _ in 0..size {
            list.push(deserializer.load(bolt)?);
        }
        Ok(list)
    }

    fn decode_dict<D: PackStreamDeserializer, B: BoltStructTranslator>(
        deserializer: &mut D,
        bolt: &B,
        size: usize,
    ) -> Result<HashMap<String, ValueReceive>, D::Error> {
        let mut dict = HashMap::with_capacity(size);
        for _ in 0..size {
            let key = deserializer.load(bolt)?.try_into_string().map_err(|v| {
                D::make_error(format!("expected dict key to be a string, found {v:?}"))
            })?;
            let value = deserializer.load(bolt)?;
            dict.insert(key, value);
        }
        Ok(dict)
    }
}

impl<R: Read> PackStreamDeserializerImpl<'_, R> {
    fn read_marker(&mut self) -> Result<u8, PackStreamDeserializeError> {
        let mut marker = [0; 1];
        self.reader.read_exact(&mut marker)?;
        Ok(marker[0])
    }

    fn load_any<B: BoltStructTranslator>(
        &mut self,
        marker: u8,
        bolt: &B,
    ) -> Result<ValueReceive, PackStreamDeserializeError> {
        if marker == 0xC0 {
            Ok(ValueReceive::Null)
        } else if marker == 0xC2 {
            Ok(ValueReceive::Boolean(false))
        } else if marker == 0xC3 {
            Ok(ValueReceive::Boolean(true))
        } else if 0xF0 <= marker || marker <= 0x7F {
            Ok(ValueReceive::Integer(i8::from_be_bytes([marker]).into()))
        } else if marker == 0xC8 {
            Ok(ValueReceive::Integer(Self::decode_i8(self.reader)?.into()))
        } else if marker == 0xC9 {
            Ok(ValueReceive::Integer(Self::decode_i16(self.reader)?.into()))
        } else if marker == 0xCA {
            Ok(ValueReceive::Integer(Self::decode_i32(self.reader)?.into()))
        } else if marker == 0xCB {
            Ok(ValueReceive::Integer(Self::decode_i64(self.reader)?))
        } else if marker == 0xC1 {
            Ok(ValueReceive::Float(Self::decode_f64(self.reader)?))
        } else if marker == 0xCC {
            let size = Self::decode_u8(self.reader)?;
            Ok(ValueReceive::Bytes(Self::decode_bytes(
                self.reader,
                size.into(),
            )?))
        } else if marker == 0xCD {
            let size = Self::decode_u16(self.reader)?;
            Ok(ValueReceive::Bytes(Self::decode_bytes(
                self.reader,
                size.into(),
            )?))
        } else if marker == 0xCE {
            if usize::BITS < 32 {
                Err("server wants to send more dict elements than are addressable".into())
            } else {
                let size = Self::decode_u32(self.reader)?;
                let bytes = Self::decode_bytes(self.reader, size as usize)?;
                Ok(ValueReceive::Bytes(bytes))
            }
        } else if (0x80..=0x8F).contains(&marker) {
            let size = marker - 0x80;
            let string = Self::decode_string(self.reader, size.into())?;
            Ok(ValueReceive::String(string))
        } else if marker == 0xD0 {
            let size = Self::decode_u8(self.reader)?;
            let string = Self::decode_string(self.reader, size.into())?;
            Ok(ValueReceive::String(string))
        } else if marker == 0xD1 {
            let size = Self::decode_u16(self.reader)?;
            let string = Self::decode_string(self.reader, size.into())?;
            Ok(ValueReceive::String(string))
        } else if marker == 0xD2 {
            if usize::BITS < 32 {
                return Err("server wants to send more string bytes than are addressable".into());
            }
            let size = Self::decode_u32(self.reader)?;
            let string = Self::decode_string(self.reader, size as usize)?;
            Ok(ValueReceive::String(string))
        } else if (0x90..=0x9F).contains(&marker) {
            let size = marker - 0x90;
            let list = Self::decode_list(self, bolt, size.into())?;
            Ok(ValueReceive::List(list))
        } else if marker == 0xD4 {
            let size = Self::decode_u8(self.reader)?;
            let list = Self::decode_list(self, bolt, size.into())?;
            Ok(ValueReceive::List(list))
        } else if marker == 0xD5 {
            let size = Self::decode_u16(self.reader)?;
            let list = Self::decode_list(self, bolt, size.into())?;
            Ok(ValueReceive::List(list))
        } else if marker == 0xD6 {
            if usize::BITS < 32 {
                return Err("server wants to send more string bytes than are addressable".into());
            }
            let size = Self::decode_u32(self.reader)?;
            let list = Self::decode_list(self, bolt, size as usize)?;
            Ok(ValueReceive::List(list))
        } else if (0xA0..=0xAF).contains(&marker) {
            let size = marker - 0xA0;
            let dict = Self::decode_dict(self, bolt, size.into())?;
            Ok(ValueReceive::Map(dict))
        } else if marker == 0xD8 {
            let size = Self::decode_u8(self.reader)?;
            let dict = Self::decode_dict(self, bolt, size.into())?;
            Ok(ValueReceive::Map(dict))
        } else if marker == 0xD9 {
            let size = Self::decode_u16(self.reader)?;
            let dict = Self::decode_dict(self, bolt, size.into())?;
            Ok(ValueReceive::Map(dict))
        } else if marker == 0xDA {
            if usize::BITS < 32 {
                return Err("server wants to send more dict elements than are addressable".into());
            }
            let size = Self::decode_u32(self.reader)?;
            let dict = Self::decode_dict(self, bolt, size as usize)?;
            Ok(ValueReceive::Map(dict))
        } else if (0xB0..=0xBF).contains(&marker) {
            let size = marker - 0xB0;
            let tag = Self::decode_u8(self.reader)?;
            let fields = Self::decode_list(self, bolt, size.into())?;
            Ok(bolt.deserialize_struct(tag, fields))
        } else {
            Err(PackStreamDeserializeError::protocol_violation(format!(
                "unknown marker {marker}"
            )))
        }
    }
}

impl<R: Read> PackStreamDeserializer for PackStreamDeserializerImpl<'_, R> {
    type Error = PackStreamDeserializeError;

    fn make_error(reason: String) -> Self::Error {
        PackStreamDeserializeError::protocol_violation(reason)
    }

    fn load<B: BoltStructTranslator>(&mut self, bolt: &B) -> Result<ValueReceive, Self::Error> {
        let marker = self.read_marker()?;
        self.load_any(marker, bolt)
    }
}
