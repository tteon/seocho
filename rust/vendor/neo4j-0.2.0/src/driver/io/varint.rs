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

use std::io::{Error as IoError, Read, Write};

use thiserror::Error;

pub(super) fn read_var_int(mut read: impl Read) -> Result<u64, ReadVarIntError> {
    let mut current_byte = [0u8; 1];
    let mut current_shift = 0u8;
    let mut res = 0u64;
    loop {
        read.read_exact(&mut current_byte)
            .map_err(ReadVarIntError::Io)?;
        let part_u8 = current_byte[0] & 0x7F;
        let part = part_u8 as u64;
        let continue_flag = current_byte[0] & 0x80;

        let allowed_bits = 64u8.saturating_sub(current_shift);
        if allowed_bits < 7 {
            let forbidden_bit_mask = 0xFFu8 << allowed_bits;
            if part_u8 & forbidden_bit_mask != 0 {
                return Err(ReadVarIntError::TooBig);
            }
        }
        if allowed_bits != 0 {
            res |= part << current_shift;
            current_shift += 7;
        }
        if continue_flag == 0 {
            break;
        }
    }
    Ok(res)
}

pub(super) fn write_var_int<W: Write>(mut write: W, mut val: u64) -> Result<(), IoError> {
    loop {
        let mut byte = (val & 0x7F) as u8;
        val >>= 7;
        if val != 0 {
            byte |= 0x80;
        }
        write.write_all(&[byte])?;
        if val == 0 {
            break;
        }
    }
    Ok(())
}

#[derive(Debug, Error)]
pub(super) enum ReadVarIntError {
    #[error("IO error: {0}")]
    Io(IoError),
    #[error("VarInt overflow")]
    TooBig,
}

impl From<IoError> for ReadVarIntError {
    fn from(err: IoError) -> Self {
        ReadVarIntError::Io(err)
    }
}

#[cfg(test)]
mod test {
    use super::*;

    use rstest::rstest;

    #[rstest]
    #[case(&[0x00], 0, 0)]
    #[case(&[0x80, 0x00], 0, 0)] // some 0 padding
    // 0 padding that exceeds the max bits
    #[case(&[0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x00], 0, 0)]
    #[case(&[0x01], 1, 0)]
    #[case(&[0x81, 0x00], 1, 0)] // some 0 padding
    // 0 padding that exceeds the max bits
    #[case(&[0x81, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x00], 1, 0)]
    #[case(&[0xFF, 0x01], 0xFF, 0)]
    #[case(&[0xFF, 0x7F], 0x3FFF, 0)]
    #[case(&[0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F], i64::MAX as u64, 0)]
    #[case(&[0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01], u64::MAX, 0)]
    #[case(&[0x01, 0x00], 1, 1)]
    #[case(&[0x01, 0xFF], 1, 1)]
    #[case(&[0xFF, 0x7F, 0x00], 0x3FFF, 1)]
    #[case(&[0xFF, 0x7F, 0xFF, 0xFF], 0x3FFF, 2)]
    fn test_read_var_int(#[case] input: &[u8], #[case] expected: u64, #[case] remaining: usize) {
        let mut read = make_read(input);
        let res = read_var_int(&mut read).unwrap();
        assert_eq!(res, expected);

        let mut remaining_read = Vec::with_capacity(remaining);
        read.read_to_end(&mut remaining_read).unwrap();
        assert_eq!(remaining_read.len(), remaining);
    }

    #[rstest]
    #[case(
        make_read_err(IoError::new(std::io::ErrorKind::Other, "Some error")),
        ReadVarIntError::Io(IoError::new(std::io::ErrorKind::Other, "Some error"))
    )]
    #[case(
        make_read(&[0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x02]),
        ReadVarIntError::TooBig
    )]
    fn test_read_var_int_err(#[case] input: impl Read, #[case] expected: ReadVarIntError) {
        let res = read_var_int(input).unwrap_err();
        assert_eq!(res.to_string(), expected.to_string());
    }

    #[rstest]
    #[case(0, &[0x00])]
    #[case(1, &[0x01])]
    #[case(0xFF, &[0xFF, 0x01])]
    #[case(0x3FFF, &[0xFF, 0x7F])]
    #[case(i64::MAX as u64, &[0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F])]
    #[case(u64::MAX, &[0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01])]
    fn test_write_var_int(#[case] input: u64, #[case] expected: &[u8]) {
        let mut buf = Vec::new();
        write_var_int(&mut buf, input).unwrap();
        assert_eq!(buf, expected);
    }

    // Helper functions

    fn make_read(input: &[u8]) -> impl Read {
        std::io::Cursor::new(Vec::from(input))
    }

    fn make_read_err(err: IoError) -> impl Read {
        struct ErrRead {
            err: Option<IoError>,
        }

        impl Read for ErrRead {
            fn read(&mut self, _: &mut [u8]) -> Result<usize, IoError> {
                Err(self.err.take().expect("Should not be called again"))
            }
        }

        ErrRead { err: Some(err) }
    }
}
