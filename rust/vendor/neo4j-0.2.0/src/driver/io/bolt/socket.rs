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

use std::io::{BufReader, BufWriter, IoSlice, IoSliceMut, Read, Result as IoResult, Write};
use std::net::TcpStream;
use std::sync::Arc;

use rustls::{ClientConfig, ClientConnection, StreamOwned};
use rustls_pki_types::ServerName;

use crate::error_::{Neo4jError, Result};

#[derive(Debug)]
pub(crate) enum BufTcpStream {
    Buffered {
        read: BufReader<TcpStream>,
        write: BufWriter<TcpStream>,
    },
    Unbuffered(TcpStream),
}

impl BufTcpStream {
    pub(super) fn new(socket: &TcpStream, buffered: bool) -> Result<Self> {
        match buffered {
            true => {
                let read = BufReader::new(Neo4jError::wrap_connect(socket.try_clone())?);
                let write = BufWriter::new(Neo4jError::wrap_connect(socket.try_clone())?);
                Ok(Self::Buffered { read, write })
            }
            false => {
                let socket = Neo4jError::wrap_connect(socket.try_clone())?;
                Ok(Self::Unbuffered(socket))
            }
        }
    }
}

impl Read for BufTcpStream {
    #[inline]
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { read, .. } => read.read(buf),
            BufTcpStream::Unbuffered(read) => read.read(buf),
        }
    }

    #[inline]
    fn read_vectored(&mut self, bufs: &mut [IoSliceMut<'_>]) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { read, .. } => read.read_vectored(bufs),
            BufTcpStream::Unbuffered(read) => read.read_vectored(bufs),
        }
    }

    #[inline]
    fn read_to_end(&mut self, buf: &mut Vec<u8>) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { read, .. } => read.read_to_end(buf),
            BufTcpStream::Unbuffered(read) => read.read_to_end(buf),
        }
    }

    #[inline]
    fn read_to_string(&mut self, buf: &mut String) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { read, .. } => read.read_to_string(buf),
            BufTcpStream::Unbuffered(read) => read.read_to_string(buf),
        }
    }

    #[inline]
    fn read_exact(&mut self, buf: &mut [u8]) -> IoResult<()> {
        match self {
            BufTcpStream::Buffered { read, .. } => read.read_exact(buf),
            BufTcpStream::Unbuffered(read) => read.read_exact(buf),
        }
    }
}

impl Write for BufTcpStream {
    #[inline]
    fn write(&mut self, buf: &[u8]) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { write, .. } => write.write(buf),
            BufTcpStream::Unbuffered(write) => write.write(buf),
        }
    }

    #[inline]
    fn write_vectored(&mut self, bufs: &[IoSlice<'_>]) -> IoResult<usize> {
        match self {
            BufTcpStream::Buffered { write, .. } => write.write_vectored(bufs),
            BufTcpStream::Unbuffered(write) => write.write_vectored(bufs),
        }
    }

    #[inline]
    fn flush(&mut self) -> IoResult<()> {
        match self {
            BufTcpStream::Buffered { write, .. } => write.flush(),
            BufTcpStream::Unbuffered(write) => write.flush(),
        }
    }

    #[inline]
    fn write_all(&mut self, buf: &[u8]) -> IoResult<()> {
        match self {
            BufTcpStream::Buffered { write, .. } => write.write_all(buf),
            BufTcpStream::Unbuffered(write) => write.write_all(buf),
        }
    }
}

#[derive(Debug)]
pub(crate) enum Socket<T: Read + Write> {
    Plain(T),
    Tls(Box<StreamOwned<ClientConnection, T>>),
}

impl<T: Read + Write> Socket<T> {
    pub(super) fn new(
        io: T,
        host_name: &str,
        tls_config: Option<Arc<ClientConfig>>,
    ) -> Result<Self> {
        Ok(match tls_config {
            None => Self::Plain(io),
            Some(tls_config) => {
                let host_name = ServerName::try_from(host_name)
                    .map_err(|e| Neo4jError::InvalidConfig {
                        message: format!("tls refused hostname {host_name}: {e}"),
                    })?
                    .to_owned();
                let connection = ClientConnection::new(tls_config, host_name).map_err(|e| {
                    Neo4jError::InvalidConfig {
                        message: format!("failed to initialize tls stream: {e}"),
                    }
                })?;
                Self::Tls(Box::new(StreamOwned::new(connection, io)))
            }
        })
    }
}

impl<T: Read + Write> Read for Socket<T> {
    #[inline]
    fn read(&mut self, buf: &mut [u8]) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.read(buf),
            Socket::Tls(io) => io.read(buf),
        }
    }

    #[inline]
    fn read_vectored(&mut self, bufs: &mut [IoSliceMut<'_>]) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.read_vectored(bufs),
            Socket::Tls(io) => io.read_vectored(bufs),
        }
    }

    #[inline]
    fn read_to_end(&mut self, buf: &mut Vec<u8>) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.read_to_end(buf),
            Socket::Tls(io) => io.read_to_end(buf),
        }
    }

    #[inline]
    fn read_to_string(&mut self, buf: &mut String) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.read_to_string(buf),
            Socket::Tls(io) => io.read_to_string(buf),
        }
    }

    #[inline]
    fn read_exact(&mut self, buf: &mut [u8]) -> IoResult<()> {
        match self {
            Socket::Plain(io) => io.read_exact(buf),
            Socket::Tls(io) => io.read_exact(buf),
        }
    }
}

impl<T: Read + Write> Write for Socket<T> {
    #[inline]
    fn write(&mut self, buf: &[u8]) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.write(buf),
            Socket::Tls(io) => io.write(buf),
        }
    }

    #[inline]
    fn write_vectored(&mut self, bufs: &[IoSlice<'_>]) -> IoResult<usize> {
        match self {
            Socket::Plain(io) => io.write_vectored(bufs),
            Socket::Tls(io) => io.write_vectored(bufs),
        }
    }

    #[inline]
    fn flush(&mut self) -> IoResult<()> {
        match self {
            Socket::Plain(io) => io.flush(),
            Socket::Tls(io) => io.flush(),
        }
    }

    #[inline]
    fn write_all(&mut self, buf: &[u8]) -> IoResult<()> {
        match self {
            Socket::Plain(io) => io.write_all(buf),
            Socket::Tls(io) => io.write_all(buf),
        }
    }
}
