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

use std::cmp;
use std::fmt::{Debug, Formatter};
use std::io::{self, Read};
use std::ops::Deref;
use std::thread::panicking;

use log::{error, log_enabled, trace, Level};
use usize_cast::IntoUsize;

use crate::util::truncate_string;

#[derive(Debug)]
pub(crate) struct Chunker<'a, T: Deref<Target = [u8]>> {
    buffers: &'a [T],
    buffer_start: usize,
    chunk_size_left: u16,
    ended: bool,
}

impl<'a, T: Deref<Target = [u8]>> Chunker<'a, T> {
    pub(crate) fn new(buf: &'a [T]) -> Self {
        Chunker {
            buffers: buf,
            buffer_start: 0,
            chunk_size_left: 0,
            ended: false,
        }
    }
}

impl<'a, T: Deref<Target = [u8]>> Iterator for Chunker<'a, T> {
    type Item = Chunk<'a>;

    fn next(&mut self) -> Option<Self::Item> {
        if !self.ended {
            while let Some(true) = self.buffers.first().map(|b| b.len() == 0) {
                self.buffers = &self.buffers[1..];
            }
            if !self.buffers.is_empty() {
                if self.chunk_size_left > 0 {
                    let buffer_len = cmp::min(
                        self.buffers[0].len() - self.buffer_start,
                        self.chunk_size_left.into_usize(),
                    );
                    let buffer_end = self.buffer_start + buffer_len;
                    let chunk = &self.buffers[0][self.buffer_start..buffer_end];
                    self.chunk_size_left -= buffer_len as u16;
                    self.buffer_start = buffer_end;
                    if self.buffer_start == self.buffers[0].len() {
                        self.buffers = &self.buffers[1..];
                        self.buffer_start = 0;
                    }
                    Some(Chunk::Buffer(chunk))
                } else {
                    let mut size = (self.buffers[0].len() - self.buffer_start)
                        .try_into()
                        .unwrap_or(u16::MAX);
                    size = size.saturating_add(
                        self.buffers[1..]
                            .iter()
                            .map(|b| b.len().try_into().unwrap_or(u16::MAX))
                            .reduce(|acc, x| acc.saturating_add(x))
                            .unwrap_or_default(),
                    );
                    self.chunk_size_left = size;
                    let size = size.to_be_bytes();
                    Some(Chunk::Size(size))
                }
            } else {
                self.ended = true;
                Some(Chunk::Size([0, 0])) // terminate message with empty chunk
            }
        } else {
            None
        }
    }
}

pub(crate) enum Chunk<'a> {
    Buffer(&'a [u8]),
    Size([u8; 2]),
}

impl Deref for Chunk<'_> {
    type Target = [u8];

    fn deref(&self) -> &Self::Target {
        match self {
            Chunk::Buffer(buf) => {
                trace!("C: <RAW> {:02X?}", buf);
                buf
            }
            Chunk::Size(size) => {
                trace!("C: <RAW> {:02X?}", size);
                size
            }
        }
    }
}

pub(crate) struct Dechunker<R: Read> {
    reader: R,
    chunk_size: usize,
    broken: bool,
    chunk_log_raw: Option<String>,
}

impl<R: Read> Dechunker<R> {
    pub(crate) fn new(reader: R) -> Self {
        let chunk_log_raw = match log_enabled!(Level::Trace) {
            true => Some(String::new()),
            false => None,
        };
        Self {
            reader,
            chunk_size: 0,
            broken: false,
            chunk_log_raw,
        }
    }

    fn error_wrap<T: Debug>(&mut self, res: io::Result<T>) -> io::Result<T> {
        if res.is_err() {
            self.broken = true;
        }
        res
    }
}

impl<R: Read> Read for Dechunker<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.broken {
            panic!("attempted to read from a broken dechunker");
        }
        while self.chunk_size == 0 {
            let mut size_buf = [0; 2];
            let res = self.reader.read_exact(&mut size_buf);
            self.error_wrap(res)?;
            self.chunk_size = u16::from_be_bytes(size_buf).into_usize();
            if log_enabled!(Level::Trace) {
                let log_raw = self.chunk_log_raw.as_mut().unwrap();
                if !log_raw.is_empty() {
                    trace!("{}]", log_raw);
                    log_raw.clear();
                }
                if self.chunk_size > 0 {
                    log_raw.push_str(&format!(
                        "S: <RAW> [{}",
                        truncate_string(&format!("{:02X?}", &size_buf), 1, 1)
                    ));
                } else {
                    trace!("S: <RAW> {:02X?}", &size_buf);
                }
            }
        }
        let new_buf_size = cmp::min(buf.len(), self.chunk_size);
        let buf = &mut buf[..new_buf_size];
        let res = self.reader.read_exact(buf).map(|_| new_buf_size);
        if log_enabled!(Level::Trace) && res.is_ok() {
            let log_raw = self.chunk_log_raw.as_mut().unwrap();
            log_raw.push_str(", ");
            log_raw.push_str(truncate_string(&format!("{:02X?}", buf), 1, 1));
        }
        self.chunk_size -= new_buf_size;
        self.error_wrap(res)
    }
}

impl<R: Read> Debug for Dechunker<R> {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Dechunker")
            .field("reader", &"...")
            .field("chunk_size", &self.chunk_size)
            .field("broken", &self.broken)
            .field("chunk_log_raw", &self.chunk_log_raw)
            .finish()
    }
}

impl<R: Read> Drop for Dechunker<R> {
    fn drop(&mut self) {
        if log_enabled!(Level::Trace) {
            let log_raw = self.chunk_log_raw.as_mut().unwrap();
            if !log_raw.is_empty() {
                trace!("{}]", log_raw);
            }
        }
        if self.chunk_size > 0 && !self.broken {
            match panicking() {
                false => panic!("attempted to drop a dechunker with an unfinished chunk: {self:?}"),
                true => {
                    eprintln!(
                        "attempted to drop a dechunker with an unfinished chunk \
                         while panicking: {self:?}"
                    );
                    error!(
                        "attempted to drop a dechunker with an unfinished chunk \
                         while panicking: {self:?}"
                    )
                }
            }
        }
    }
}
