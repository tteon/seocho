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

pub(crate) fn truncate_string(string: &str, start: usize, end: usize) -> &str {
    let mut chars = string.chars();
    for _ in 0..start {
        if chars.next().is_none() {
            break;
        }
    }
    for _ in 0..end {
        if chars.next_back().is_none() {
            break;
        }
    }
    chars.as_str()
}

macro_rules! concat_str {
    ($a:expr, $b:expr) => {{
        const A: &str = $a;
        const B: &str = $b;
        const LEN: usize = A.len() + B.len();
        const BYTES: [u8; LEN] = {
            let mut bytes = [0; LEN];

            let mut i = 0;
            while i < A.len() {
                bytes[i] = A.as_bytes()[i];
                i += 1;
            }

            let mut j = 0;
            while j < B.len() {
                bytes[A.len() + j] = B.as_bytes()[j];
                j += 1;
            }

            bytes
        };

        match std::str::from_utf8(&BYTES) {
            Ok(s) => s,
            Err(_) => unreachable!(),
        }
    }};
}

pub(crate) use concat_str;
