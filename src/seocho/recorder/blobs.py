from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class BlobStore:
    def __init__(self, directory: Path, *, read_only: bool = False) -> None:
        self.directory = directory
        if not read_only:
            directory.mkdir(parents=True, exist_ok=True)

    def path(self, key: str, domain: str = 'inference') -> Path:
        if domain not in ('inference', 'evaluation') or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid blob domain/hash')
        return self.directory / domain / key[:2] / key

    def put(self, body: bytes, domain: str = 'inference') -> str:
        key = digest(body)
        target = self.path(key, domain)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name('.' + uuid4().hex)
            try:
                with temporary.open('xb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        if digest(target.read_bytes()) != key:
            raise ValueError('Blob corruption')
        return key

    def get(self, key: str, domain: str = 'inference') -> bytes:
        body = self.path(key, domain).read_bytes()
        if digest(body) != key:
            raise ValueError('Blob corruption')
        return body
