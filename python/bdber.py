# -*- encoding: utf-8 -*-
"""
keri.wasm.bdber module

Browser-safe plain-value DBer backed by PyScript storage.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

try:
    from pyscript import storage
except ImportError:  # pragma: no cover - browser-only runtime
    storage = None

from sortedcontainers import SortedDict  # ty:ignore[unresolved-import]


_RECORDS_KEY = "__records__"


@dataclass
class SubDb:
    """
    One declared browser-backed subdb.

    Attributes:
        name: Logical store name used by wrappers, for example "bags.".
        namespace: Backing storage namespace, for example "wallet:bags.".
        handle: PyScript storage handle bound to namespace.
        dupsort: First in-process dupsort flag latched by env.open_db(...).
        dirty: True when items differs from the last flushed payload.
        opened: True after the first env.open_db(...).
        items: Live ordered ``bytes -> bytes`` map used by sync CRUD methods.
    """

    name: str
    namespace: str
    handle: Any
    dupsort: bool = False
    dirty: bool = False
    opened: bool = False
    items: SortedDict[bytes, bytes] = field(default_factory=SortedDict)

    def flags(self) -> dict[str, bool]:
        """Return the subdb flags used by upstream wrapper tests."""
        return {"dupsort": self.dupsort}


class BdEnv:
    """Minimal named-subdb opener used by upstream wrappers."""

    def __init__(self, owner: "Bdber"):
        self.owner = owner

    def open_db(self, key: bytes | str, dupsort: bool = False) -> SubDb:
        """
        Open a preconfigured named subdb handle.

        Parameters:
            key: Subdb name as bytes or UTF-8 text.
            dupsort: Requested duplicate flag. Only applied on the first
                in-process open for the requested store.

        Returns:
            The stable `SubDb` handle for the requested store.

        Raises:
            KeyError: If the store was not declared when the DBer was opened.
        """
        name = self.owner._storify(key)
        if name not in self.owner._stores:
            raise KeyError(f"Store not configured in Bdber: {name}")
        subdb = self.owner._stores[name]
        if not subdb.opened:
            subdb.dupsort = bool(dupsort)
            subdb.opened = True
        return subdb


class Bdber:
    """
    Storage-backed plain-value DBer.

    Attributes:
        name: Base namespace prefix shared by all declared stores.
        env: Sync open_db(...) adapter used by upstream wrappers.
        _stores: Authoritative mapping of store name to SubDb.
        stores: Declared store names exposed for inspection and tests.
    """

    def __init__(self, *, name: str, stores: dict[str, SubDb]):
        self.name = name
        self.env = BdEnv(self)
        self._stores = stores
        self.stores = list(stores)

    @classmethod
    async def open(
        cls,
        name: str,
        stores: list[str],
        *,
        clear: bool = False,
        storageOpener: Callable[[str], Awaitable[Any]] | None = None,
    ) -> "Bdber":
        """
        Open a storage-backed Bdber instance with a fixed set of stores.

        Parameters:
            name: Base namespace used to derive per-store persistence names.
            stores: Declared subdb names available through `env.open_db`.
            clear: When `True`, reset all persisted store payloads before
                loading them into memory.
            storageOpener: Async callable that returns a storage handle for a
                namespace. Defaults to `pyscript.storage`.

        Returns:
            A storage-backed `Bdber` ready for sync CRUD and async `flush()`.

        Raises:
            RuntimeError: If no storage opener is available.
        """
        opener = storageOpener if storageOpener is not None else storage
        if opener is None:
            raise RuntimeError("pyscript.storage is unavailable in this environment")

        opened: dict[str, SubDb] = {}
        for store_name in [cls._storify(store) for store in stores]:
            namespace = f"{name}:{store_name}"
            handle = await opener(namespace)
            if clear:
                handle[_RECORDS_KEY] = "{}"
                await handle.sync()
            items = SortedDict(_deserialize_records(handle.get(_RECORDS_KEY)))
            opened[store_name] = SubDb(name=store_name, namespace=namespace, handle=handle, items=items)

        return cls(name=name, stores=opened)

    @staticmethod
    def _storify(key: bytes | str) -> str:
        if isinstance(key, str):
            return key
        if isinstance(key, bytes):
            return key.decode("utf-8")
        raise TypeError(f"Unsupported store handle type: {type(key)}")

    @staticmethod
    def _keyify(key: bytes | str | memoryview) -> bytes:
        if isinstance(key, memoryview):
            return bytes(key)
        if isinstance(key, bytes):
            return key
        if isinstance(key, str):
            return key.encode("utf-8")
        raise TypeError(f"Unsupported key type: {type(key)}")

    @staticmethod
    def _valify(val: bytes | str | memoryview) -> bytes:
        if isinstance(val, memoryview):
            return bytes(val)
        if isinstance(val, bytes):
            return val
        if isinstance(val, str):
            return val.encode("utf-8")
        raise TypeError(f"Unsupported value type: {type(val)}")

    async def flush(self) -> int:
        """
        Persist dirty stores to their backing storage handles.

        Returns:
            The number of stores whose serialized payload was synced.
        """
        count = 0
        for subdb in self._stores.values():
            if not subdb.dirty:
                continue
            subdb.handle[_RECORDS_KEY] = _serialize_records(subdb.items)
            await subdb.handle.sync()
            subdb.dirty = False
            count += 1
        return count

    def putVal(self, db: SubDb, key: bytes, val: bytes) -> bool:
        """
        Insert `val` at `key` without overwriting an existing value.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            key: Exact bytes key within the subdb keyspace.
            val: Serialized bytes value to store.

        Returns:
            `True` when the value is inserted. `False` when `key` already exists.

        Raises:
            KeyError: If `key` is empty.
        """
        key = self._keyify(key)
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        if key in db.items:
            return False

        db.items[key] = self._valify(val)
        db.dirty = True
        return True

    def setVal(self, db: SubDb, key: bytes, val: bytes) -> bool:
        """
        Insert or overwrite `val` at `key`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            key: Exact bytes key within the subdb keyspace.
            val: Serialized bytes value to store.

        Returns:
            `True` after the write succeeds.

        Raises:
            KeyError: If `key` is empty.
        """
        key = self._keyify(key)
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        db.items[key] = self._valify(val)
        db.dirty = True
        return True

    def getVal(self, db: SubDb, key: bytes) -> bytes | None:
        """
        Return the stored value at `key`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            key: Exact bytes key within the subdb keyspace.

        Returns:
            Stored bytes value, or `None` when `key` is missing.

        Raises:
            KeyError: If `key` is empty.
        """
        key = self._keyify(key)
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        return db.items.get(key)

    def delVal(self, db: SubDb, key: bytes) -> bool:
        """
        Delete the exact entry at `key`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            key: Exact bytes key within the subdb keyspace.

        Returns:
            `True` when an entry existed and was deleted. `False` otherwise.

        Raises:
            KeyError: If `key` is empty.
        """
        key = self._keyify(key)
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        if key not in db.items:
            return False

        del db.items[key]
        db.dirty = True
        return True

    def getTopItemIter(self, db: SubDb, top: bytes = b"") -> Iterator[tuple[bytes, bytes]]:
        """
        Iterate over `(key, val)` pairs whose keys start with `top`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            top: Prefix bytes used to select a branch of the keyspace. Empty
                prefix yields the entire subdb in lexical order.

        Returns:
            Iterator of `(key, val)` tuples in lexical key order.
        """
        prefix = self._keyify(top) if top else b""

        if not prefix:
            for key, val in db.items.items():
                yield key, val
            return

        for key in db.items.irange(minimum=prefix):
            if not key.startswith(prefix):
                break
            yield key, db.items[key]

    def delTop(self, db: SubDb, top: bytes = b"") -> bool:
        """
        Delete all entries whose keys start with `top`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.
            top: Prefix bytes used to select the branch to delete. Empty prefix
                deletes the whole subdb.

        Returns:
            `True` when at least one entry is deleted. `False` when nothing
            matched the requested prefix.
        """
        prefix = self._keyify(top) if top else b""

        if not prefix:
            if not db.items:
                return False
            db.items.clear()
            db.dirty = True
            return True

        doomed = [key for key, _ in self.getTopItemIter(db=db, top=prefix)]
        if not doomed:
            return False

        for key in doomed:
            del db.items[key]
        db.dirty = True
        return True

    def cntAll(self, db: SubDb) -> int:
        """
        Count all values stored in `db`.

        Parameters:
            db: Named subdb handle returned by `env.open_db`.

        Returns:
            Total number of stored entries.
        """
        return len(db.items)


def _serialize_records(records: dict[bytes, bytes] | SortedDict[bytes, bytes]) -> str:
    return json.dumps({key.hex(): val.hex() for key, val in records.items()}, sort_keys=True)


def _deserialize_records(raw: Any) -> dict[bytes, bytes]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        payload = json.loads(raw)
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise TypeError(f"Unsupported persisted record payload type: {type(raw)}")

    return {
        bytes.fromhex(str(key_hex)): bytes.fromhex(str(val_hex))
        for key_hex, val_hex in payload.items()
    }
