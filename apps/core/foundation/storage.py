"""Storage boundary. Interface only.

No concrete backend ships here. Where files live (server volume, device, off-site) and
how they sync is an open product decision, so modules code against this interface and
register a backend when that decision is made.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Optional


class StorageBackend(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, *, content_type: str = "") -> None: ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class StorageNotConfigured(RuntimeError):
    pass


_BACKEND: Optional[StorageBackend] = None


def set_storage(backend: Optional[StorageBackend]) -> None:
    global _BACKEND
    _BACKEND = backend


def get_storage() -> StorageBackend:
    if _BACKEND is None:
        raise StorageNotConfigured("no storage backend has been chosen")
    return _BACKEND
