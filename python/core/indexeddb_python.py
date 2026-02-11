# -*- encoding: utf-8 -*-
"""
IndexedDB provides:
- Lexicographic key ordering (like LMDB) for encoded byte keys
- O(log n) cursor positioning
- O(1) cursor iteration
- ACID transactions within a single database

Key differences from LMDB:
- All operations are async (transactions are async)
- No explicit dupsort flag (emulated with compound keys - IoSet pattern)
- Uses IDBKeyRange for range queries
- Transactions auto-close when event loop returns to browser
- Keys are encoded from raw bytes and preserve byte-order semantics

Memory Safety:
- All Pyodide proxies are properly destroyed after use
- Use try/finally blocks to ensure cleanup

Usage:
    from indexeddb_keripy import IndexedDBer

    db = await IndexedDBer.open(name="keri-wallet", stores=["evts", "kels", "pdes"])
    await db.setVal("evts", b"key", b"value")
    val = await db.getVal("evts", b"key")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from enum import Enum

# Pyodide/PyScript browser environment imports
try:
    from js import indexedDB, IDBKeyRange, console
    from pyodide.ffi import create_proxy, to_js
except ImportError:
    indexedDB = None
    IDBKeyRange = None
    console = None
    create_proxy = None
    to_js = None


# =============================================================================
# EXCEPTIONS
# =============================================================================


class IndexedDBError(Exception):
    """Base exception for IndexedDB operations."""

    pass


class DatabaseNotOpenError(IndexedDBError):
    """Database has not been opened."""

    pass


class TransactionAbortedError(IndexedDBError):
    """Transaction was aborted."""

    pass


class KeyExistsError(IndexedDBError):
    """Key already exists (for putVal with overwrite=False)."""

    pass


class KeyNotFoundError(IndexedDBError):
    """Key does not exist."""

    pass


class DatabaseBlockedError(IndexedDBError):
    """Database upgrade blocked by another tab."""

    pass


class TransactionTimeoutError(IndexedDBError):
    """Transaction timed out - likely due to long async operations during iteration."""

    pass


class KeyEncodingError(IndexedDBError):
    """Key encoding/decoding failed."""

    pass


class IndexedDBRequestError(IndexedDBError):
    """Request-level error with optional DOMException name."""

    def __init__(self, message: str, *, name: Optional[str] = None):
        super().__init__(message)
        self.name = name


def _normalize_key_bytes(key: bytes | str | memoryview) -> bytes:
    """Normalize a key to raw bytes (UTF-8 for str)."""
    if isinstance(key, memoryview):
        return bytes(key)
    if isinstance(key, bytes):
        return key
    if isinstance(key, str):
        return key.encode("utf-8")
    raise TypeError(f"Unsupported key type: {type(key)}")


def _normalize_sep_bytes(sep: bytes | str) -> bytes:
    """Normalize separator to bytes (UTF-8 for str)."""
    if isinstance(sep, bytes):
        return sep
    if isinstance(sep, str):
        return sep.encode("utf-8")
    raise TypeError(f"Unsupported separator type: {type(sep)}")


def _coerce_store_name(store: Any) -> str:
    """Normalize a subdb/object-store handle to a string name."""
    if isinstance(store, str):
        return store
    if isinstance(store, memoryview):
        store = bytes(store)
    if isinstance(store, bytes):
        return store.decode("utf-8")
    if hasattr(store, "decode"):
        try:
            return store.decode("utf-8")
        except Exception:
            pass
    raise TypeError(f"Unsupported store handle type: {type(store)}")


def _is_js_null(value: Any) -> bool:
    """Return True if value represents JS null/undefined in Pyodide."""
    if value is None:
        return True
    tname = type(value).__name__
    if tname in ("JsNull", "JsUndefined"):
        return True
    try:
        return str(value) == "null"
    except Exception:
        return False


def _to_js_bytes(data: bytes) -> Any:
    """
    Convert Python bytes to JS Uint8Array for IndexedDB storage.

    Uses Pyodide's to_js() with buffer protocol for efficient zero-copy
    conversion when possible.
    """
    if to_js is None:
        raise IndexedDBError("Pyodide environment not available")
    return to_js(data)


def _from_js_bytes(js_array: Any) -> bytes:
    """
    Convert JS Uint8Array/ArrayBuffer back to Python bytes.

    Handles both Uint8Array and raw ArrayBuffer returns from IndexedDB.
    """
    if _is_js_null(js_array):
        return None

    # Try Pyodide's to_py() first (most efficient)
    if hasattr(js_array, "to_py"):
        result = js_array.to_py()
        if hasattr(result, "tobytes"):
            return result.tobytes()
        return bytes(result)

    # Fallback for raw iteration
    return bytes(js_array)


def _key_to_idb(key: bytes | str | memoryview) -> str:
    """
    Encode raw key bytes into an IndexedDB string key.

    Hex encoding provides:
    - reversible mapping for arbitrary bytes
    - deterministic lexicographic ordering aligned with byte ordering
    - no separator collisions in compound-key formats
    """
    key_bytes = _normalize_key_bytes(key)
    return key_bytes.hex()


# IoSet/IoDup Constants (matching dbing.py)
SuffixSize = 32  # does not include trailing separator
MaxSuffix = int("f" * SuffixSize, 16)

ProemSize = 32  # does not include trailing separator
MaxProem = int("f" * ProemSize, 16)
_IODUP_PROEM_SEP = b"."
# Separator for Vals-family compound keys: key_hex + '\x00' + val_hex
_VALS_SEP = "\x00"
_METADATA_STORE = "__metadata__"
_VERSION_KEY = _key_to_idb(b"__version__")


def suffix(key: bytes, ion: int, *, sep: bytes = b".") -> bytes:
    """
    Returns:
       iokey (bytes): actual DB key after concatenating suffix as hex version
       of insertion ordering ordinal int ion using separator sep.

    Parameters:
        key (bytes): apparent effective database key (unsuffixed)
        ion (int): insertion ordering ordinal for set of vals
        sep (bytes): separator character(s) for concatenating suffix
    """
    if isinstance(key, memoryview):
        key = bytes(key)
    elif hasattr(key, "encode"):
        key = key.encode("utf-8")
    if hasattr(sep, "encode"):
        sep = sep.encode("utf-8")
    ion_bytes = b"%032x" % ion
    return sep.join((key, ion_bytes))


def unsuffix(iokey: bytes, *, sep: bytes = b".") -> tuple[bytes, int]:
    """
    Returns:
       result (tuple): (key, ion) by splitting iokey at rightmost separator sep
            strip off suffix, where key is bytes apparent effective DB key and
            ion is the insertion ordering int converted from stripped hex suffix

    Parameters:
        iokey (bytes): actual database key with suffix
        sep (bytes): separator character(s) for concatenating suffix
    """
    if isinstance(iokey, memoryview):
        iokey = bytes(iokey)
    elif hasattr(iokey, "encode"):
        iokey = iokey.encode("utf-8")
    if hasattr(sep, "encode"):
        sep = sep.encode("utf-8")
    key, ion_hex = iokey.rsplit(sep=sep, maxsplit=1)
    ion = int(ion_hex, 16)
    return (key, ion)


def _iodup_join(key: bytes | str | memoryview, proem: int) -> bytes:
    """Return IoDup key bytes as key + b'.' + proem_hex."""
    key = _normalize_key_bytes(key)
    return _IODUP_PROEM_SEP.join((key, b"%032x" % proem))


def _iodup_split(iodup_key: bytes | str | memoryview) -> tuple[bytes, int]:
    """Split IoDup key bytes into (key, proem)."""
    return unsuffix(iodup_key, sep=_IODUP_PROEM_SEP)


def _key_from_idb(idb_key: Any) -> bytes:
    """
    Decode IndexedDB key back to bytes.
    """
    if isinstance(idb_key, str):
        if len(idb_key) % 2 == 0 and all(
            ch in "0123456789abcdefABCDEF" for ch in idb_key
        ):
            return bytes.fromhex(idb_key)
        return idb_key.encode("latin-1")
    else:
        return _from_js_bytes(idb_key)


async def _await_request(request: Any) -> Any:
    """
    Convert IDBRequest to Python awaitable with proper proxy cleanup.

    IndexedDB operations return IDBRequest objects. This helper wraps them
    in a Future that resolves when onsuccess fires.

    IMPORTANT: All proxies are destroyed in the finally block to prevent
    memory leaks in long-running browser sessions.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    success_proxy = None
    error_proxy = None

    def on_success(event):
        if not future.done():
            future.set_result(event.target.result)

    def on_error(event):
        if not future.done():
            error = event.target.error
            name = getattr(error, "name", None)
            error_msg = str(error) if error else "Unknown error"
            future.set_exception(IndexedDBRequestError(error_msg, name=name))

    try:
        success_proxy = create_proxy(on_success)
        error_proxy = create_proxy(on_error)

        request.onsuccess = success_proxy
        request.onerror = error_proxy

        result = await future
        return None if _is_js_null(result) else result
    finally:
        # Always cleanup proxies to prevent memory leaks
        if success_proxy is not None:
            success_proxy.destroy()
        if error_proxy is not None:
            error_proxy.destroy()


async def _await_transaction(tx: Any) -> bool:
    """
    Wait for an IndexedDB transaction to complete with proper proxy cleanup.

    Transactions auto-commit when all requests complete and no new requests
    are made. This awaits the oncomplete event for explicit confirmation.

    IMPORTANT: All proxies are destroyed in the finally block.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    complete_proxy = None
    error_proxy = None
    abort_proxy = None

    def on_complete(event):
        if not future.done():
            future.set_result(True)

    def on_error(event):
        if not future.done():
            error = event.target.error
            name = getattr(error, "name", None)
            error_msg = str(error) if error else "Transaction error"
            future.set_exception(IndexedDBRequestError(error_msg, name=name))

    def on_abort(event):
        if not future.done():
            future.set_exception(TransactionAbortedError("Transaction aborted"))

    try:
        complete_proxy = create_proxy(on_complete)
        error_proxy = create_proxy(on_error)
        abort_proxy = create_proxy(on_abort)

        tx.oncomplete = complete_proxy
        tx.onerror = error_proxy
        tx.onabort = abort_proxy

        return await future
    finally:
        if complete_proxy is not None:
            complete_proxy.destroy()
        if error_proxy is not None:
            error_proxy.destroy()
        if abort_proxy is not None:
            abort_proxy.destroy()


async def _walk_cursor(
    request: Any, on_item: callable, on_done: Optional[callable] = None
) -> None:
    """
    Walk an IndexedDB cursor without yielding between steps.

    This is the core pattern for transaction safety. IndexedDB transactions
    auto-close when the event loop yields back to the browser (i.e. on any
    `await` that isn't an IDB request within the same transaction). By
    handling all cursor steps in synchronous onsuccess callbacks, we keep
    the transaction alive for the entire walk.

    The optional on_done callback fires after the cursor is exhausted
    (cursor becomes null). Because on_done runs inside the same onsuccess
    handler, it can issue further store operations (put, delete) that are
    still within the live transaction. This enables the read-then-write
    pattern: on_item collects data, on_done writes based on that data —
    all in one transaction.

    Args:
        request: IDBRequest from store.openCursor()
        on_item: Called for each cursor position. Return True to continue,
                 False to stop early.
        on_done: Called after cursor exhaustion (or after on_item returns
                 False). Runs within the same transaction — safe to call
                 store.put() / store.delete() here.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    success_proxy = None
    error_proxy = None

    def on_success(event):
        if future.done():
            return
        cursor = event.target.result
        if _is_js_null(cursor):
            try:
                if on_done is not None:
                    on_done()
                future.set_result(True)
            except Exception as e:
                future.set_exception(e)
            return
        try:
            should_continue = on_item(cursor)
        except Exception as e:
            future.set_exception(e)
            return
        if should_continue:
            cursor.continue_()
        else:
            future.set_result(True)

    def on_error(event):
        if future.done():
            return
        error = event.target.error
        name = getattr(error, "name", None)
        error_msg = str(error) if error else "Cursor error"
        future.set_exception(IndexedDBRequestError(error_msg, name=name))

    try:
        success_proxy = create_proxy(on_success)
        error_proxy = create_proxy(on_error)
        request.onsuccess = success_proxy
        request.onerror = error_proxy
        await future
    finally:
        if success_proxy is not None:
            success_proxy.destroy()
        if error_proxy is not None:
            error_proxy.destroy()


async def _await_many_requests(requests: list[Any], on_result: callable) -> None:
    """
    Await multiple IDBRequests scheduled in the same transaction.

    on_result(index, result) is called for each request result.
    """
    if not requests:
        return

    loop = asyncio.get_event_loop()
    future = loop.create_future()
    remaining = len(requests)
    proxies = []

    def make_handlers(index: int):
        def on_success(event):
            nonlocal remaining
            if future.done():
                return
            try:
                result = event.target.result
                on_result(index, None if _is_js_null(result) else result)
            except Exception as e:
                future.set_exception(e)
                return
            remaining -= 1
            if remaining == 0:
                future.set_result(True)

        def on_error(event):
            if future.done():
                return
            error = event.target.error
            name = getattr(error, "name", None)
            error_msg = str(error) if error else "Request error"
            future.set_exception(IndexedDBRequestError(error_msg, name=name))

        return on_success, on_error

    try:
        for i, request in enumerate(requests):
            on_success, on_error = make_handlers(i)
            success_proxy = create_proxy(on_success)
            error_proxy = create_proxy(on_error)
            proxies.extend([success_proxy, error_proxy])
            request.onsuccess = success_proxy
            request.onerror = error_proxy
        await future
    finally:
        for proxy in proxies:
            proxy.destroy()


class IDBTransactionMode(Enum):
    """IndexedDB transaction modes."""

    READONLY = "readonly"
    READWRITE = "readwrite"


class IDBTransaction:
    """
    Async context manager for IndexedDB transactions.

    Transactions in IndexedDB are atomic - either all operations succeed or
    none do. The transaction auto-commits when the context exits without error.

    Usage:
        async with IDBTransaction(db, ['store1'], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore('store1')
            store.put(value, key)
        # Transaction auto-commits here if no errors

    Equivalent to LMDB's:
        with self.env.begin(db=db, write=True) as txn:
            txn.put(key, val)

    WARNING: Do not await external operations (network calls, etc.) within the
    transaction context. IndexedDB transactions auto-close when the event loop
    returns to the browser for too long.
    """

    def __init__(
        self,
        db: Any,
        store_names: list[str],
        mode: IDBTransactionMode = IDBTransactionMode.READONLY,
    ):
        """
        Args:
            db: IDBDatabase instance
            store_names: Object stores to include in transaction
            mode: READONLY or READWRITE
        """
        self.db = db
        self.store_names = store_names
        self.mode = mode
        self.tx = None

    async def __aenter__(self) -> Any:
        self.tx = self.db.transaction(self.store_names, self.mode.value)
        return self.tx

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self.tx is not None:
            # Wait for transaction to complete (durability guarantee)
            try:
                await _await_transaction(self.tx)
            except TransactionAbortedError:
                # Transaction may have already completed/aborted
                pass
        # If exception occurred, transaction will auto-abort
        return False


async def open_database(
    name: str,
    stores: list[str],
    version: int = 1,
    on_blocked: Optional[callable] = None,
) -> Any:
    """
    Open IndexedDB database with specified object stores.

    Equivalent to:
        LMDBer.__init__(name=name)
        env.open_db(b'store1')
        env.open_db(b'store2')

    Args:
        name: Database name (like LMDBer's name parameter)
        stores: List of object store names (like LMDB sub-databases)
        version: Schema version (increment when adding new stores)
        on_blocked: Optional callback when upgrade is blocked by another tab

    Returns:
        IDBDatabase instance

    Raises:
        DatabaseBlockedError: If upgrade is blocked and no handler provided
        IndexedDBError: On other database errors
    """
    if indexedDB is None:
        raise IndexedDBError(
            "IndexedDB not available - not running in browser environment"
        )
    stores = [_coerce_store_name(store) for store in stores]

    loop = asyncio.get_event_loop()
    future = loop.create_future()

    # Track all proxies for cleanup
    proxies = []

    def on_upgrade(event):
        """Called when database is created or version increases."""
        db = event.target.result
        for store_name in list(stores) + [_METADATA_STORE]:
            names = db.objectStoreNames
            exists = (
                names.contains(store_name)
                if hasattr(names, "contains")
                else (store_name in names)
            )
            if not exists:
                # Keys are stored in sorted order (B-tree)
                db.createObjectStore(store_name)

    def on_blocked_handler(event):
        """Called when another tab has the DB open at an older version."""
        if on_blocked:
            on_blocked(event)
        else:
            if console:
                console.warn(f"Database '{name}' upgrade blocked by another tab")
            if not future.done():
                future.set_exception(
                    DatabaseBlockedError(
                        f"Database '{name}' upgrade blocked - close other tabs using this database"
                    )
                )

    def on_success(event):
        if not future.done():
            future.set_result(event.target.result)

    def on_error(event):
        if not future.done():
            error_msg = (
                str(event.target.error)
                if event.target.error
                else "Failed to open database"
            )
            future.set_exception(IndexedDBError(error_msg))

    try:
        request = indexedDB.open(name, version)

        upgrade_proxy = create_proxy(on_upgrade)
        blocked_proxy = create_proxy(on_blocked_handler)
        success_proxy = create_proxy(on_success)
        error_proxy = create_proxy(on_error)

        proxies.extend([upgrade_proxy, blocked_proxy, success_proxy, error_proxy])

        request.onupgradeneeded = upgrade_proxy
        request.onblocked = blocked_proxy
        request.onsuccess = success_proxy
        request.onerror = error_proxy

        db = await future
        names = db.objectStoreNames
        exists = (
            names.contains(_METADATA_STORE)
            if hasattr(names, "contains")
            else (_METADATA_STORE in names)
        )
        if not exists:
            # Migrate legacy DBs created before metadata store existed.
            db.close()
            return await open_database(name, stores, version + 1, on_blocked)
        return db
    finally:
        for proxy in proxies:
            proxy.destroy()


async def deleteDatabase(name: str) -> bool:
    """
    Delete an entire IndexedDB database.

    WARNING: This is destructive and cannot be undone.

    Args:
        name: Database name

    Returns:
        True on success
    """
    if indexedDB is None:
        raise IndexedDBError("IndexedDB not available")

    request = indexedDB.deleteDatabase(name)
    await _await_request(request)
    return True


@dataclass
class IndexedDBer:
    """
    LMDBer-compatible interface for IndexedDB.

    Provides a class-based wrapper around the functional API that more closely
    matches keripy's LMDBer interface.

    Usage:
        db = await IndexedDBer.open(
            name="keri-wallet",
            stores=["evts", "kels", "pdes", "ooes", "dels", "ldes"]
        )

        await db.setVal("evts", key, val)
        val = await db.getVal("evts", key)

        # Iterate with prefix
        for key, val in await db.getTopItemIter("evts", top=b"BD"):
            process(key, val)

        db.close()
    """

    name: str
    db: Any = None
    stores: list[str] = None
    _version: Optional[str] = None
    readonly: bool = False

    @classmethod
    async def open(
        cls,
        name: str,
        stores: list[str],
        version: int = 1,
        on_blocked: Optional[callable] = None,
    ) -> "IndexedDBer":
        """
        Open or create an IndexedDB database.

        Args:
            name: Database name
            stores: List of object store names (sub-databases)
            version: Schema version (increment when adding stores)
            on_blocked: Optional callback for blocked upgrades

        Returns:
            IndexedDBer instance
        """
        db = await open_database(name, stores, version, on_blocked)
        instance = cls(name=name, db=db, stores=stores)
        instance._version = await instance.getVer()
        instance.readonly = False
        return instance

    def close(self, clear: bool = False):
        """Close the database connection. If clear, request DB deletion."""
        db_name = self.name
        if self.db is not None:
            self.db.close()
            self.db = None
        if clear and indexedDB is not None:
            try:
                indexedDB.deleteDatabase(db_name)
            except Exception:
                pass

    async def reopen(self, readonly: bool = False, **kwa) -> bool:
        """Close and reopen database, mirroring LMDBer.reopen lifecycle."""
        stores = kwa.pop("stores", self.stores)
        if stores is None:
            raise DatabaseNotOpenError(
                f"Database '{self.name}' has no configured stores"
            )
        stores = [_coerce_store_name(store) for store in stores]

        on_blocked = kwa.pop("on_blocked", None)
        clear = bool(kwa.pop("clear", False))
        current_version = (
            getattr(self.db, "version", None) if self.db is not None else None
        )
        version = kwa.pop(
            "version", current_version if current_version is not None else 1
        )

        self.close(clear=False)
        if clear:
            await deleteDatabase(self.name)

        self.db = await open_database(self.name, stores, int(version), on_blocked)
        self.stores = stores
        self.readonly = True if readonly else False
        self._version = await self.getVer()
        return self.db is not None

    @property
    def version(self):
        """Cached database semver string, or None when unset."""
        return self._version

    @version.setter
    def version(self, val):
        if hasattr(val, "decode"):
            val = val.decode("utf-8")
        self._version = val

    async def getVer(self) -> Optional[str]:
        """
        Return semver string stored in metadata, or None if missing.
        """
        async with IDBTransaction(
            self.db, [_METADATA_STORE], IDBTransactionMode.READONLY
        ) as tx:
            store = tx.objectStore(_METADATA_STORE)
            version = await _await_request(store.get(_VERSION_KEY))
            if version is None:
                self._version = None
                return None
            raw = _from_js_bytes(version)
            self._version = raw.decode("utf-8") if raw is not None else None
        return self._version

    async def setVer(self, val: str | bytes) -> bool:
        """
        Persist semver string in metadata.
        """
        if hasattr(val, "decode"):
            val = val.decode("utf-8")
        self._version = val
        if hasattr(val, "encode"):
            val = val.encode("utf-8")
        idb_val = _to_js_bytes(val)

        async with IDBTransaction(
            self.db, [_METADATA_STORE], IDBTransactionMode.READWRITE
        ) as tx:
            store = tx.objectStore(_METADATA_STORE)
            await _await_request(store.put(idb_val, _VERSION_KEY))
        return True

    # Basic operations
    async def putVal(self, db: str, key: bytes, val: bytes) -> bool:
        """
        LMDBer.putVal equivalent - insert if key does not exist (overwrite=False).

        Uses IndexedDB's store.add() which throws ConstraintError on duplicate
        keys, providing atomic check-and-insert without a separate read step.
        This avoids the TransactionInactiveError that would occur if we did
        ``await store.get()`` then ``store.put()`` (the await yields to the
        event loop, closing the transaction before the put).

        Val family store — one value per raw byte key.

        Args:
            db: Object store name (must be a Val-family store)
            key: Key bytes (must be non-empty)
            val: Value bytes

        Returns:
            True if inserted, False if key already exists

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        idb_key = _key_to_idb(key)
        idb_val = _to_js_bytes(val)

        try:
            async with IDBTransaction(
                self.db, [db], IDBTransactionMode.READWRITE
            ) as tx:
                store = tx.objectStore(db)
                await _await_request(store.add(idb_val, idb_key))
            return True
        except IndexedDBRequestError as e:
            if e.name == "ConstraintError" or "ConstraintError" in str(e):
                return False
            raise

    async def setVal(self, db: str, key: bytes, val: bytes) -> bool:
        """
        LMDBer.setVal equivalent - upsert (insert or overwrite).

        Uses store.put() which unconditionally writes. Unlike putVal, this
        overwrites any existing value at the key.

        Val family store — one value per raw byte key.

        Args:
            db: Object store name (must be a Val-family store)
            key: Key bytes (must be non-empty)
            val: Value bytes

        Returns:
            True always (operation always succeeds or raises)

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        idb_key = _key_to_idb(key)
        idb_val = _to_js_bytes(val)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            await _await_request(store.put(idb_val, idb_key))
        return True

    async def getVal(self, db: str, key: bytes) -> Optional[bytes]:
        """
        LMDBer.getVal equivalent - retrieve value by key.

        Args:
            db: Object store name
            key: Key bytes

        Returns:
            Value bytes if found, None otherwise

        Raises:
            KeyError: If key is empty
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        idb_key = _key_to_idb(key)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            result = await _await_request(store.get(idb_key))
            return _from_js_bytes(result) if result is not None else None

    async def delVal(self, db: str, key: bytes) -> bool:
        """
        LMDBer.delVal equivalent - delete key.

        Uses cursor-based delete via _walk_cursor rather than a get-then-delete
        pattern. A naive ``await store.get(); store.delete()`` would fail with
        TransactionInactiveError because the await yields to the event loop,
        auto-closing the transaction before the delete executes.

        Args:
            db: Object store name (must be a Val-family store)
            key: Key bytes (must be non-empty)

        Returns:
            True if key existed and was deleted, False if key didn't exist

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        idb_key = _key_to_idb(key)

        deleted = False
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            request = store.openCursor(idb_key)

            def on_item(cursor):
                nonlocal deleted
                cursor.delete()
                deleted = True
                return False

            await _walk_cursor(request, on_item)
        return deleted

    # Count operations
    async def cnt(self, db: str) -> int:
        """
        Count all entries in an object store.

        Equivalent to LMDBer.cnt. Fast metadata operation using
        IndexedDB's count().
        """
        db = _coerce_store_name(db)
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            return await _await_request(store.count())

    async def cntOnVals(
        self, db: str, key: bytes = b"", on: int = 0, sep: bytes = b"."
    ) -> int:
        """
        LMDBer.cntOnVals equivalent - count ordinal values for a key prefix.

        Uses store.count(keyRange) for efficiency — O(log n) metadata
        operation, avoids reading all entries. Counts entries matching
        pattern: key + sep + ordinal_hex (32 hex digit ordinals).

        On-family store. Note: this is distinct from cntVals which counts
        dupsort duplicates (Vals-family). The functions were separated because
        they operate on different compound key formats.

        Args:
            db: Object store name (On-family store)
            key: Key prefix
            sep: Separator between key and ordinal (default b'.')

        Returns:
            Count of ordinal entries for the key
        """
        if key:
            key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        count = 0
        start = _key_to_idb(suffix(key, on, sep=sep)) if key else None

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            request = store.openCursor(
                IDBKeyRange.lowerBound(start) if start is not None else None
            )

            def on_item(cursor):
                nonlocal count
                try:
                    ckey, _ = unsuffix(_key_from_idb(cursor.key), sep=sep)
                except ValueError:
                    return False  # not an ordinal key in this family
                if key and ckey != key:
                    return False
                count += 1
                return True

            await _walk_cursor(request, on_item)
        return count

    # Iteration (buffered)
    async def getTopItemIter(
        self, db: str, top: bytes = b""
    ) -> list[tuple[bytes, bytes]]:
        """
        LMDBer.getTopItemIter equivalent - get all items with key prefix.

        BUFFERED VERSION: Collects all results before returning to avoid
        transaction timeout issues with async generators.

        Uses IDBKeyRange.bound() to efficiently seek to prefix range.
        IndexedDB cursors iterate in lexicographic key order (B-tree property).

        LMDB equivalent:
            with self.env.begin(self.db=self.db, write=False) as txn:
                cursor = txn.cursor()
                if cursor.set_range(top):
                    for key, val in cursor.iternext():
                        if not key.startswith(top):
                            break
                        yield (key, val)

        Args:
            db: Object store name
            top: Key prefix to match (empty for all keys)

        Returns:
            List of (key, value) tuples matching the prefix
        """
        results = []

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)

            if top:
                prefix_str = _key_to_idb(top)
                # Range from prefix (inclusive) to prefix + '\uffff' (exclusive)
                # '\uffff' is highest Unicode char, capturing all prefix matches
                key_range = IDBKeyRange.bound(
                    prefix_str, prefix_str + "\uffff", False, True
                )
                request = store.openCursor(key_range)
            else:
                request = store.openCursor()

            def on_item(cursor):
                key = _key_from_idb(cursor.key)
                val = _from_js_bytes(cursor.value)
                results.append((key, val))
                return True

            await _walk_cursor(request, on_item)

        return results

    async def getOnItemIter(
        self, db: str, key: bytes = b"", on: int = 0, sep: bytes = b"."
    ) -> list[tuple[bytes, int, bytes]]:
        """
        LMDBer.getOnItemIter equivalent - get (key, ordinal, value) tuples.

        Returns a buffered list (not an async generator) for transaction safety.
        IndexedDB transactions auto-close when the event loop yields, so an
        async generator that yields between cursor steps would fail with
        TransactionInactiveError. Instead, all results are collected within
        a single _walk_cursor pass and returned as a list.

        Parses On-family compound keys (key + sep + ordinal_hex) using
        rsplit(sep, 1) to extract the prefix and ordinal.

        Args:
            db: Object store name (On-family store)
            key: Key prefix (empty for all keys in the store)
            on: Starting ordinal (default 0)
            sep: Separator (default b'.')

        Returns:
            List of (prefix_key, ordinal, value) tuples in key order
        """
        results = []
        if key:
            key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        tx = self.db.transaction([db], "readonly")
        store = tx.objectStore(db)

        if key:
            start_key = _key_to_idb(suffix(key, on, sep=sep))
            request = store.openCursor(IDBKeyRange.lowerBound(start_key))
        else:
            request = store.openCursor()

        def on_item(cursor):
            try:
                prefix, ordinal = unsuffix(_key_from_idb(cursor.key), sep=sep)
            except ValueError:
                return True
            if key and prefix != key:
                return False
            val = _from_js_bytes(cursor.value)
            results.append((prefix, ordinal, val))
            return True

        await _walk_cursor(request, on_item)

        return results

    # Ordinal operations
    async def appendOnVal(
        self, db: str, key: bytes, val: bytes, sep: bytes = b"."
    ) -> int:
        """
        LMDBer.appendOnVal equivalent - append value with next ordinal.

        On-family store — keys are structured as:
            key + sep + ordinal_hex  (32 hex digit ordinal)
        Example: b"mykey.00000000000000000000000000000000"
                 b"mykey.00000000000000000000000000000001"

        Opens a reverse cursor (direction='prev') within the key's ordinal
        range to find the max existing ordinal in O(log n), then writes at
        max + 1. Uses _walk_cursor with on_item (existing entries) and on_done
        (empty store case) so the read and write happen in the same transaction.

        32 hex digits = 128 bits, supporting up to 2^128 ordinals per key.

        Args:
            db: Object store name (On-family store)
            key: Key prefix
            val: Value to append
            sep: Separator between key and ordinal (default b'.')

        Returns:
            The ordinal number assigned to the new entry (0-indexed)

        """
        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)
        db = _coerce_store_name(db)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)

            # Range from key+sep+00...00 to key+sep+ff...ff
            min_key = _key_to_idb(suffix(key, 0, sep=sep))
            max_key = _key_to_idb(suffix(key, MaxSuffix, sep=sep))
            key_range = IDBKeyRange.bound(min_key, max_key, False, False)

            request = store.openCursor(key_range, "prev")
            on = 0  # Default ordinal if no existing entries
            idb_val = _to_js_bytes(val)

            def on_item(cursor):
                nonlocal on
                try:
                    ckey, cn = unsuffix(_key_from_idb(cursor.key), sep=sep)
                    if ckey == key:
                        on = cn + 1
                except ValueError:
                    pass
                new_key = _key_to_idb(suffix(key, on, sep=sep))
                store.put(idb_val, new_key)
                return False

            def on_done():
                new_key = _key_to_idb(suffix(key, on, sep=sep))
                store.put(idb_val, new_key)

            await _walk_cursor(request, on_item, on_done)

        return on

    async def getOnVal(
        self, db: str, key: bytes, on: int = 0, *, sep: bytes = b"."
    ) -> Optional[bytes]:
        """
        LMDBer.getOnVal equivalent - get value at specific ordinal.

        Constructs key as: prefix.NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN and retrieves value.

        Args:
            db: Object store name
            key: Key prefix
            on: Ordinal number (0-indexed)
            sep: Separator (default b'.')

        Returns:
            Value bytes if found, None otherwise
        """
        key = _normalize_key_bytes(key) if key else b""
        sep = _normalize_sep_bytes(sep)
        db = _coerce_store_name(db)
        onkey = suffix(key, on, sep=sep) if key else key
        idb_key = _key_to_idb(onkey)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            result = await _await_request(store.get(idb_key))
            return _from_js_bytes(result) if result is not None else None

    async def setOnVal(
        self, db: str, key: bytes, on: int = 0, val: bytes = b"", *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.setOnVal equivalent - set value at specific ordinal (overwrites).

        Uses store.put() which unconditionally writes. This is the overwriting
        counterpart to putOnVal. On-family store — the compound key is
        key + sep + on_hex.

        Args:
            db: Object store name (On-family store)
            key: Key prefix
            on: Ordinal number
            val: Value bytes
            sep: Separator (default b'.')

        Returns:
            True on success
        """
        key = _normalize_key_bytes(key) if key else b""
        sep = _normalize_sep_bytes(sep)
        db = _coerce_store_name(db)
        onkey = suffix(key, on, sep=sep) if key else key
        idb_key = _key_to_idb(onkey)
        idb_val = _to_js_bytes(val)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            await _await_request(store.put(idb_val, idb_key))
        return True

    async def delOnVal(
        self, db: str, key: bytes, on: int = 0, *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.delOnVal equivalent - delete value at specific ordinal.

        Uses cursor-based delete via _walk_cursor (same pattern as delVal)
        to avoid TransactionInactiveError. On-family store — the compound
        key is key + sep + on_hex.

        Args:
            db: Object store name (On-family store)
            key: Key prefix
            on: Ordinal number
            sep: Separator (default b'.')

        Returns:
            True if deleted, False if not found
        """
        key = _normalize_key_bytes(key) if key else b""
        sep = _normalize_sep_bytes(sep)
        db = _coerce_store_name(db)
        onkey = suffix(key, on, sep=sep) if key else key
        idb_key = _key_to_idb(onkey)

        deleted = False
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            request = store.openCursor(idb_key)

            def on_item(cursor):
                nonlocal deleted
                cursor.delete()
                deleted = True
                return False

            await _walk_cursor(request, on_item)
        return deleted

    async def putOnVal(
        self, db: str, key: bytes, on: int = 0, val: bytes = b"", *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.putOnVal equivalent - write val at onkey, does NOT overwrite.

        Constructs onkey = suffix(key, on, sep=sep) and delegates to putVal
        which uses store.add() for atomic check-and-insert (overwrite=False).
        This is the non-overwriting counterpart to setOnVal.

        On-family store — the compound key is key + sep + on_hex.

        Args:
            db: Object store name (On-family store)
            key: Key prefix
            on: Ordinal number
            val: Value bytes
            sep: Separator (default b'.')

        Returns:
            True if written, False if onkey already exists
        """
        key = _normalize_key_bytes(key) if key else b""
        sep = _normalize_sep_bytes(sep)
        onkey = suffix(key, on, sep=sep) if key else key
        return await self.putVal(db, onkey, val)

    async def getOnValIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getOnValIter equivalent - yields values only (not tuples).

        Wraps getOnItemIter and strips the (key, on) components, returning
        only the value from each triple. Returns a buffered list for
        transaction safety (see getOnItemIter docstring).

        Args:
            db: Object store name (On-family store)
            key: Key prefix (empty for all keys)
            on: Starting ordinal (default 0)
            sep: Separator (default b'.')

        Returns:
            List of values in ordinal order

        """
        items = await self.getOnItemIter(db, key, on, sep=sep)
        return [val for _k, _o, val in items]

    async def delTopVal(self, db: str, top: bytes = b"") -> bool:
        """
        LMDBer.delTopVal equivalent - delete all entries matching prefix.

        Deletes ALL entries in the key branch matching prefix ``top``.
        The name "Top" refers to the top-level key prefix, not "top entry" —
        this deletes the entire branch, not just the highest ordinal.

        If top is empty, deletes everything in the store.
        Works for both Val and IoDup stores (matches LMDBer: "Works for both
        dupsort==False and dupsort==True").

        Uses a cursor walk to delete entries one-by-one within a single
        READWRITE transaction.

        Args:
            db: Object store name
            top: Key prefix to match (empty = delete all)

        Returns:
            True if anything deleted, False otherwise
        """
        deleted = False

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)

            if top:
                prefix_str = _key_to_idb(top)
                key_range = IDBKeyRange.bound(
                    prefix_str, prefix_str + "\uffff", False, True
                )
                request = store.openCursor(key_range)
            else:
                request = store.openCursor()

            def on_item(cursor):
                nonlocal deleted
                cursor.delete()
                deleted = True
                return True

            await _walk_cursor(request, on_item)

        return deleted

    # Batch operations
    async def getAllVals(
        self, db: str, keys: list[bytes]
    ) -> dict[bytes, Optional[bytes]]:
        """
        Batch get multiple keys in a single transaction.

        More efficient than individual getVal calls for multiple keys.

        Args:
            db: Object store name
            keys: List of keys to retrieve

        Returns:
            Dictionary mapping keys to values (None if not found)
        """
        results = {}

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            idb_keys = [_key_to_idb(key) for key in keys]
            requests = [store.get(idb_key) for idb_key in idb_keys]

            def on_result(index, result):
                key = keys[index]
                results[key] = _from_js_bytes(result) if result is not None else None

            await _await_many_requests(requests, on_result)

        return results

    async def setAllVals(self, db: str, items: list[tuple[bytes, bytes]]) -> bool:
        """
        Batch set multiple key-value pairs in a single transaction.

        Atomic: either all succeed or none do.

        Args:
            db: Object store name
            items: List of (key, value) tuples

        Returns:
            True on success
        """
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)

            for key, val in items:
                idb_key = _key_to_idb(key)
                idb_val = _to_js_bytes(val)
                store.put(idb_val, idb_key)

        return True

    async def delAllVals(self, db: str, keys: list[bytes]) -> int:
        """
        Batch delete multiple keys in a single transaction.

        Args:
            db: Object store name
            keys: List of keys to delete

        Returns:
            Count of keys that existed and were deleted
        """
        deleted = 0

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            idb_keys = [_key_to_idb(key) for key in keys]
            requests = [store.get(idb_key) for idb_key in idb_keys]

            def on_result(index, result):
                nonlocal deleted
                if result is not None:
                    store.delete(idb_keys[index])
                    deleted += 1

            await _await_many_requests(requests, on_result)

        return deleted

    # Clear / delete
    async def clearStore(self, db: str) -> bool:
        """
        Clear all entries from an object store.

        Args:
            db: Object store name

        Returns:
            True on success
        """
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            await _await_request(store.clear())
        return True

    # ==========================================================================
    # IOSET OPERATIONS - Insertion Ordered Set
    # ==========================================================================

    async def putIoSetVals(
        self, db: str, key: bytes, vals: list[bytes], *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.putIoSetVals equivalent - add vals to insertion ordered set.

        Additive: adds each val that is not already in the set. Does NOT clear
        existing values (contrast with setIoSetVals which replaces).

        IoSet-family store — compound key format: key + sep + ordinal_hex.
        Uses _walk_cursor with on_item to collect existing values and find
        max ordinal, then on_done to batch-write new values — all in a single
        READWRITE transaction.

        Args:
            db: Object store name (IoSet-family store)
            key: Apparent effective key
            vals: Values to add to set
            sep: Separator between key and ordinal (default b'.')

        Returns:
            True if any value was added, False if all were duplicates
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        # First, get existing values and find max ion
        existing_vals = set()
        ion = 0

        iokey_start = suffix(key, 0, sep=sep)
        idb_start = _key_to_idb(iokey_start)

        # Build prefix bound for range query
        key_prefix = _key_to_idb(key + sep)
        idb_end = key_prefix + "\uffff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            # Collect existing values and find max ion
            def on_item(cursor):
                nonlocal ion
                idb_key = cursor.key
                ckey, cion = unsuffix(_key_from_idb(idb_key), sep=sep)
                if ckey == key:
                    existing_vals.add(bytes(_from_js_bytes(cursor.value)))
                    ion = max(ion, cion + 1)
                return True

            result = False

            def on_done():
                nonlocal result
                vals_to_add = [v for v in vals if v not in existing_vals]
                for i, val in enumerate(vals_to_add):
                    iokey = suffix(key, ion + i, sep=sep)
                    idb_key = _key_to_idb(iokey)
                    idb_val = _to_js_bytes(val)
                    store.put(idb_val, idb_key)
                    result = True

            await _walk_cursor(request, on_item, on_done)

        return result

    async def addIoSetVal(
        self, db: str, key: bytes, val: bytes, *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.addIoSetVal equivalent - idempotently add val to set.

        Walks existing entries via _walk_cursor to check for duplicates and
        find the max ordinal. If val is not found, writes it at max+1 in the
        on_done callback — all within a single READWRITE transaction to avoid
        TransactionInactiveError.

        IoSet-family store — compound key format: key + sep + ordinal_hex.

        Args:
            db: Object store name (IoSet-family store)
            key: Apparent effective key
            val: Value to add
            sep: Separator between key and ordinal (default b'.')

        Returns:
            True if val was added, False if already in set
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        # Check if val exists and find max ion
        ion = 0

        iokey_start = suffix(key, 0, sep=sep)
        idb_start = _key_to_idb(iokey_start)
        key_prefix = _key_to_idb(key + sep)
        idb_end = key_prefix + "\uffff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            found_dup = False

            def on_item(cursor):
                nonlocal ion, found_dup
                idb_key = cursor.key
                ckey, cion = unsuffix(_key_from_idb(idb_key), sep=sep)
                if ckey == key:
                    cval = bytes(_from_js_bytes(cursor.value))
                    if cval == val:
                        found_dup = True
                        return False
                    ion = max(ion, cion + 1)
                return True

            def on_done():
                if found_dup:
                    return
                iokey = suffix(key, ion, sep=sep)
                idb_key = _key_to_idb(iokey)
                idb_val = _to_js_bytes(val)
                store.put(idb_val, idb_key)

            await _walk_cursor(request, on_item, on_done)
            if found_dup:
                return False  # Already in set

        return True

    async def setIoSetVals(
        self, db: str, key: bytes, vals: list[bytes], *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.setIoSetVals equivalent - replace all vals at key.

        Destructive: erases all existing vals at key, then writes unique vals
        as a fresh insertion ordered set. Contrast with putIoSetVals which is
        additive and preserves existing entries.

        Uses two transactions: one for delIoSetVals, one for the writes.
        Deduplicates input vals client-side before writing.

        IoSet-family store — compound key format: key + sep + ordinal_hex.

        Args:
            db: Object store name (IoSet-family store)
            key: Apparent effective key
            vals: Values to set (duplicates within list are removed)
            sep: Separator between key and ordinal (default b'.')

        Returns:
            True if any values were written
        """
        await self.delIoSetVals(db, key, sep=sep)
        if not vals:
            return False

        # Add unique vals
        seen = set()
        unique_vals = []
        for v in vals:
            if v not in seen:
                seen.add(v)
                unique_vals.append(v)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            for i, val in enumerate(unique_vals):
                iokey = suffix(key, i, sep=sep)
                idb_key = _key_to_idb(iokey)
                idb_val = _to_js_bytes(val)
                store.put(idb_val, idb_key)

        return True

    async def getIoSetVals(
        self, db: str, key: bytes, *, ion: int = 0, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getIoSetVals equivalent - get all vals in set.

        Returns the insertion ordered list of values at same apparent effective key.

        Args:
            db: Object store name
            key: Apparent effective key
            ion: Starting ordinal value (default 0)
            sep: Separator between key and ordinal (default b'.')

        Returns:
            List of values in insertion order
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        vals = []
        iokey_start = suffix(key, ion, sep=sep)
        idb_start = _key_to_idb(iokey_start)
        key_prefix = _key_to_idb(key + sep)
        idb_end = key_prefix + "\uffff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            def on_item(cursor):
                idb_key = cursor.key
                ckey, _ = unsuffix(_key_from_idb(idb_key), sep=sep)
                if ckey != key:
                    return False
                val = _from_js_bytes(cursor.value)
                vals.append(val)
                return True

            await _walk_cursor(request, on_item)

        return vals

    async def getIoSetValsIter(
        self, db: str, key: bytes, *, ion: int = 0, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getIoSetValsIter equivalent - buffered iteration.

        Returns list (not async generator) for transaction safety — IndexedDB
        transactions auto-close when the event loop yields, so an async
        generator that awaits between yields would fail. Delegates to
        getIoSetVals.
        """
        return await self.getIoSetVals(db, key, ion=ion, sep=sep)

    async def getIoSetValLast(
        self, db: str, key: bytes, *, sep: bytes = b"."
    ) -> Optional[bytes]:
        """
        LMDBer.getIoSetValLast equivalent - get last added value.

        Returns the last added value at apparent effective key, or None if empty.

        Args:
            db: Object store name
            key: Apparent effective key
            sep: Separator between key and ordinal (default b'.')

        Returns:
            Last value in set or None
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        # Query from max suffix backwards to find last entry
        iokey_max = suffix(key, MaxSuffix, sep=sep)
        idb_max = _key_to_idb(iokey_max)
        iokey_start = suffix(key, 0, sep=sep)
        idb_start = _key_to_idb(iokey_start)

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_max, False, False)
            # Open cursor in reverse (prev) direction to get last entry first
            request = store.openCursor(key_range, "prev")

            cursor = await _await_request(request)
            if cursor is None:
                return None

            # Check if this key belongs to our effective key
            idb_key = cursor.key
            ckey, _ = unsuffix(_key_from_idb(idb_key), sep=sep)
            if ckey != key:
                return None

            return _from_js_bytes(cursor.value)

    async def cntIoSetVals(self, db: str, key: bytes, *, sep: bytes = b".") -> int:
        """
        LMDBer.cntIoSetVals equivalent - count values in set.

        Args:
            db: Object store name
            key: Apparent effective key
            sep: Separator between key and ordinal (default b'.')

        Returns:
            Count of values in set
        """
        vals = await self.getIoSetVals(db, key, sep=sep)
        return len(vals)

    async def delIoSetVals(self, db: str, key: bytes, *, sep: bytes = b".") -> bool:
        """
        LMDBer.delIoSetVals equivalent - delete all vals at key.

        Deletes all values at apparent effective key.

        Args:
            db: Object store name
            key: Apparent effective key
            sep: Separator between key and ordinal (default b'.')

        Returns:
            True if any values were deleted, False otherwise
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        result = False
        iokey_start = suffix(key, 0, sep=sep)
        idb_start = _key_to_idb(iokey_start)
        key_prefix = _key_to_idb(key + sep)
        idb_end = key_prefix + "\uffff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            def on_item(cursor):
                nonlocal result
                idb_key = cursor.key
                ckey, _ = unsuffix(_key_from_idb(idb_key), sep=sep)
                if ckey != key:
                    return False
                cursor.delete()
                result = True
                return True

            await _walk_cursor(request, on_item)

        return result

    async def delIoSetVal(
        self, db: str, key: bytes, val: bytes, *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.delIoSetVal equivalent - delete specific val from set.

        Deletes val at apparent effective key if exists.
        Requires linear search over set of values.

        Args:
            db: Object store name
            key: Apparent effective key
            val: Value to delete
            sep: Separator between key and ordinal (default b'.')

        Returns:
            True if val was deleted, False if not found
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        iokey_start = suffix(key, 0, sep=sep)
        idb_start = _key_to_idb(iokey_start)
        key_prefix = _key_to_idb(key + sep)
        idb_end = key_prefix + "\uffff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            deleted = False

            def on_item(cursor):
                nonlocal deleted
                idb_key = cursor.key
                ckey, _ = unsuffix(_key_from_idb(idb_key), sep=sep)
                if ckey != key:
                    return False
                cval = _from_js_bytes(cursor.value)
                if cval == val:
                    cursor.delete()
                    deleted = True
                    return False
                return True

            await _walk_cursor(request, on_item)

        return deleted

    async def getTopIoSetItemIter(
        self, db: str, top: bytes = b"", *, sep: bytes = b"."
    ) -> list[tuple[bytes, bytes]]:
        """
        LMDBer.getTopIoSetItemIter equivalent - iterate over branch with suffix stripped.

        Returns (key, val) tuples where key is apparent key with hidden
        insertion ordering suffix removed.

        Args:
            db: Object store name
            top: Key prefix to match (empty for all)
            sep: Separator between key and ordinal (default b'.')

        Returns:
            List of (apparent_key, val) tuples
        """
        items = await self.getTopItemIter(db, top)
        results = []
        for iokey, val in items:
            try:
                key, _ = unsuffix(iokey, sep=sep)
                results.append((key, val))
            except ValueError:
                # Key doesn't have valid suffix, skip
                continue
        return results

    # ==========================================================================
    # VALS OPERATIONS - dupsort=True emulation
    # ==========================================================================

    async def putVals(self, db: str, key: bytes, vals: list[bytes]) -> bool:
        """
        LMDBer.putVals equivalent - add unique values to existing duplicates at key.

        Additive: adds each val that is not already a duplicate at this key.
        Does NOT clear existing values (contrast with setIoSetVals which
        replaces). This matches LMDB's dupsort=True put behavior where
        txn.put(key, val, dupdata=True) adds without removing existing dups.

        Vals-family store — compound key format: key_hex + '\\x00' + val.hex().
        The hex encoding preserves byte-level lexicographic
        ordering so IDB key ordering matches LMDB's dupsort ordering.

        Uses _walk_cursor with on_item to collect existing value hex strings,
        then on_done to batch-write new values — all in a single READWRITE
        transaction.

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)
            vals: Values to add

        Returns:
            True always (matching LMDBer: "Apparently always returns True.")

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP
        idb_start = idb_key_prefix
        idb_end = idb_key_prefix + "\xff"

        existing_hex = set()

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, True)
            request = store.openCursor(key_range)

            def on_item(cursor):
                idb_key = cursor.key
                val_hex = idb_key[len(idb_key_prefix) :]
                existing_hex.add(val_hex)
                return True

            def on_done():
                for val in vals:
                    vhex = val.hex()
                    if vhex not in existing_hex:
                        compound_key = idb_key_prefix + vhex
                        idb_val = _to_js_bytes(val)
                        store.put(idb_val, compound_key)
                        existing_hex.add(vhex)

            await _walk_cursor(request, on_item, on_done)

        return True

    async def addVal(self, db: str, key: bytes, val: bytes) -> bool:
        """
        LMDBer.addVal equivalent - add single value if not already a duplicate.

        Vals-family store — compound key format: key_hex + '\\x00' + val.hex().

        Uses cursor-based check-and-write via _walk_cursor to avoid
        TransactionInactiveError. A naive ``await store.get(compound_key)``
        then ``store.put()`` would fail because the await yields to the event
        loop, auto-closing the IDB transaction before the put executes.
        Instead, we open a cursor on IDBKeyRange.only(compound_key): on_item
        fires if the key exists (dup found, return False), on_done fires if
        not found (writes the new entry, return True).

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)
            val: Value to add

        Returns:
            True if added, False if duplicate exists

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP
        compound_key = idb_key_prefix + val.hex()

        found = False

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.only(compound_key)
            request = store.openCursor(key_range)

            def on_item(cursor):
                nonlocal found
                found = True
                return False  # stop — dup exists

            def on_done():
                if not found:
                    idb_val = _to_js_bytes(val)
                    store.put(idb_val, compound_key)

            await _walk_cursor(request, on_item, on_done)

        return not found

    async def getVals(self, db: str, key: bytes) -> list[bytes]:
        """
        LMDBer.getVals equivalent - return all values at key, sorted lexicographically.

        Vals-family store — walks compound keys key\\x00val_hex via _walk_cursor.
        Because val.hex() preserves byte-level lexicographic ordering, the IDB
        cursor returns values in the same order as LMDB's dupsort. Returns a
        buffered list for transaction safety.

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)

        Returns:
            List of values sorted lexicographically (byte order), or [] if none

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP
        idb_start = idb_key_prefix
        idb_end = idb_key_prefix + "\xff"

        vals = []
        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, True)
            request = store.openCursor(key_range)

            def on_item(cursor):
                val = _from_js_bytes(cursor.value)
                vals.append(val)
                return True

            await _walk_cursor(request, on_item)

        return vals

    async def getValLast(self, db: str, key: bytes) -> Optional[bytes]:
        """
        LMDBer.getValLast equivalent - return last (largest) dup value.

        Opens a reverse cursor (direction='prev') within the key's compound
        key range to find the entry with the highest hex-encoded value in
        O(log n). Because hex encoding preserves byte order, this is the
        lexicographically largest value, matching LMDB's cursor.last_dup().

        Vals-family store — compound key format: key_hex + '\\x00' + val.hex().

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)

        Returns:
            Lexicographically largest value, or None if no entries

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP
        idb_start = idb_key_prefix
        idb_end = idb_key_prefix + "\xff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, True)
            request = store.openCursor(key_range, "prev")

            cursor = await _await_request(request)
            if cursor is None:
                return None

            return _from_js_bytes(cursor.value)

    async def getValsIter(self, db: str, key: bytes) -> list[bytes]:
        """
        LMDBer.getValsIter equivalent - iterate dup values for a single key.

        Returns a buffered list (not async generator) for transaction safety —
        IndexedDB transactions auto-close when the event loop yields, so an
        async generator that awaits between yields would fail. Delegates to
        getVals.

        Iterates values for ONE key only (matches LMDB's cursor.iternext_dup()
        which stays within one key), not cross-key.

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)

        Returns:
            List of values sorted lexicographically (byte order)

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        return await self.getVals(db, key)

    async def cntVals(self, db: str, key: bytes) -> int:
        """
        LMDBer.cntVals equivalent - count dup values at a single key.

        Uses store.count(keyRange) on the compound key range for efficiency —
        avoids reading all values just to count them. This is an O(log n)
        metadata operation in IndexedDB.

        Vals-family store — compound key format: key_hex + '\\x00' + val.hex().

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)

        Returns:
            Count of duplicate values at this key

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP
        idb_start = idb_key_prefix
        idb_end = idb_key_prefix + "\xff"

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, True)
            return await _await_request(store.count(key_range))

    async def delVals(self, db: str, key: bytes, val: bytes = b"") -> bool:
        """
        LMDBer.delVals equivalent - delete dups at key.

        Two modes:
        - val is empty: deletes ALL dups at key by walking the compound key
          range with _walk_cursor, calling cursor.delete() on each.
        - val is non-empty: deletes the specific dup by opening a cursor on
          IDBKeyRange.only(compound_key). Uses cursor-based delete (not
          ``await store.get()`` then ``store.delete()``) to avoid
          TransactionInactiveError — the await would yield to the event loop
          and auto-close the transaction before the delete executes.

        Vals-family store — compound key format: key_hex + '\\x00' + val.hex().

        Args:
            db: Object store name (Vals-family store)
            key: Database key (must be non-empty)
            val: Specific value to delete (empty = delete all dups at key)

        Returns:
            True if anything deleted, False otherwise

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )

        idb_key_prefix = _key_to_idb(key) + _VALS_SEP

        if val:
            # Delete specific dup
            compound_key = idb_key_prefix + val.hex()
            deleted = False
            async with IDBTransaction(
                self.db, [db], IDBTransactionMode.READWRITE
            ) as tx:
                store = tx.objectStore(db)
                key_range = IDBKeyRange.only(compound_key)
                request = store.openCursor(key_range)

                def on_item(cursor):
                    nonlocal deleted
                    cursor.delete()
                    deleted = True
                    return False  # stop — only one match possible

                await _walk_cursor(request, on_item)
            return deleted
        else:
            # Delete all dups
            idb_start = idb_key_prefix
            idb_end = idb_key_prefix + "\xff"
            deleted = False

            async with IDBTransaction(
                self.db, [db], IDBTransactionMode.READWRITE
            ) as tx:
                store = tx.objectStore(db)
                key_range = IDBKeyRange.bound(idb_start, idb_end, False, True)
                request = store.openCursor(key_range)

                def on_item(cursor):
                    nonlocal deleted
                    cursor.delete()
                    deleted = True
                    return True

                await _walk_cursor(request, on_item)

            return deleted

    # ==========================================================================
    # IODUP OPERATIONS - Insertion Ordered Duplicate (proem in value)
    # ==========================================================================

    async def addIoDupVal(self, db: str, key: bytes, val: bytes) -> bool:
        """
        LMDBer.addIoDupVal equivalent - add val with proem to ordered duplicates.

        IoDup-family store — compound key format: key + '.' + proem_hex (32
        hex chars). The proem provides insertion ordering. Value bytes are
        stored as-is (no proem in value — that's the LMDB convention; here
        the proem is in the key).

        Uses _walk_cursor to scan existing entries in a single READWRITE
        transaction: on_item checks for duplicates and tracks max proem,
        on_done writes the new entry if no dup was found. This avoids the
        TransactionInactiveError that would occur with an await-based
        get-then-put pattern.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)
            val: Value to add

        Returns:
            True if val was added, False if already exists

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        ion = 0
        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            found_dup = False

            def on_item(cursor):
                nonlocal ion, found_dup
                cval = _from_js_bytes(cursor.value)
                if cval == val:
                    found_dup = True
                    return False
                try:
                    _, cion = _iodup_split(_key_from_idb(cursor.key))
                    ion = max(ion, cion + 1)
                except ValueError:
                    pass
                return True

            def on_done():
                if found_dup:
                    return
                proem_key = _key_to_idb(_iodup_join(key, ion))
                idb_val = _to_js_bytes(val)
                store.put(idb_val, proem_key)

            await _walk_cursor(request, on_item, on_done)
            if found_dup:
                return False

        return True

    async def putIoDupVals(self, db: str, key: bytes, vals: list[bytes]) -> bool:
        """
        LMDBer.putIoDupVals equivalent - add multiple vals with proems.

        Additive: adds each val that is not already a duplicate. Does NOT
        clear existing values.

        IoDup-family store — compound key format: key + '.' + proem_hex.

        Uses a single READWRITE transaction for all values via _walk_cursor:
        on_item collects existing values into a dedup set and tracks max proem,
        on_done batch-writes all new values with sequential proem numbers.
        This avoids the N-transaction performance bug where each value opened
        its own transaction.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)
            vals: Values to add

        Returns:
            True if at least one value was added, False otherwise

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        ion = 0
        existing_vals = set()
        result = False

        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            def on_item(cursor):
                nonlocal ion
                cval = _from_js_bytes(cursor.value)
                existing_vals.add(bytes(cval))
                try:
                    _, cion = _iodup_split(_key_from_idb(cursor.key))
                    ion = max(ion, cion + 1)
                except ValueError:
                    pass
                return True

            def on_done():
                nonlocal ion, result
                for val in vals:
                    if val not in existing_vals:
                        proem_key = _key_to_idb(_iodup_join(key, ion))
                        idb_val = _to_js_bytes(val)
                        store.put(idb_val, proem_key)
                        existing_vals.add(val)
                        ion += 1
                        result = True

            await _walk_cursor(request, on_item, on_done)

        return result

    async def getIoDupVals(self, db: str, key: bytes) -> list[bytes]:
        """
        LMDBer.getIoDupVals equivalent - get all duplicate values.

        IoDup-family store — walks compound keys key.proem_hex in forward
        order via _walk_cursor, returning values in insertion order (proem
        order). Returns a buffered list for transaction safety.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)

        Returns:
            List of values in insertion (proem) order

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        vals = []
        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            def on_item(cursor):
                val = _from_js_bytes(cursor.value)
                vals.append(val)
                return True

            await _walk_cursor(request, on_item)

        return vals

    async def getIoDupValsIter(self, db: str, key: bytes) -> list[bytes]:
        """
        LMDBer.getIoDupValsIter equivalent - buffered iteration.

        Returns list (not async generator) for transaction safety — IndexedDB
        transactions auto-close when the event loop yields. Delegates to
        getIoDupVals.
        """
        return await self.getIoDupVals(db, key)

    async def getIoDupValLast(self, db: str, key: bytes) -> Optional[bytes]:
        """
        LMDBer.getIoDupValLast equivalent - get last duplicate value.

        Opens a reverse cursor (direction='prev') within the key's proem
        range to find the entry with the highest proem in O(log n). The
        highest proem is the most recently inserted value.

        IoDup-family store — compound key format: key + '.' + proem_hex.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)

        Returns:
            Last (most recently inserted) value, or None if no entries

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range, "prev")  # Reverse direction

            cursor = await _await_request(request)
            if cursor is None:
                return None

            return _from_js_bytes(cursor.value)

    async def cntIoDupVals(self, db: str, key: bytes) -> int:
        """
        LMDBer.cntIoDupVals equivalent - count duplicate values at key.

        Delegates to getIoDupVals and returns len(). Could be optimized with
        store.count(keyRange) if performance matters.
        """
        vals = await self.getIoDupVals(db, key)
        return len(vals)

    async def delIoDupVals(self, db: str, key: bytes) -> bool:
        """
        LMDBer.delIoDupVals equivalent - delete all duplicates at key.

        Walks all compound keys key.proem_hex via _walk_cursor and calls
        cursor.delete() on each — all within a single READWRITE transaction.

        IoDup-family store — compound key format: key + '.' + proem_hex.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)

        Returns:
            True if any were deleted, False if none existed

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        result = False
        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            def on_item(cursor):
                nonlocal result
                cursor.delete()
                result = True
                return True

            await _walk_cursor(request, on_item)

        return result

    async def delIoDupVal(self, db: str, key: bytes, val: bytes) -> bool:
        """
        LMDBer.delIoDupVal equivalent - delete specific duplicate value.

        Linear search via _walk_cursor: reads each value in the key's proem
        range and calls cursor.delete() on the matching entry. Stops after
        the first match (values are unique within an IoDup key).

        IoDup-family store — compound key format: key + '.' + proem_hex.

        Args:
            db: Object store name (IoDup-family store)
            key: Database key (must be non-empty)
            val: Value to delete

        Returns:
            True if deleted, False if not found

        Raises:
            KeyError: If key is empty (matches LMDB BadValsizeError)
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        key = _normalize_key_bytes(key)
        idb_start = _key_to_idb(_iodup_join(key, 0))
        idb_end = _key_to_idb(_iodup_join(key, MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            request = store.openCursor(key_range)

            deleted = False

            def on_item(cursor):
                nonlocal deleted
                cval = _from_js_bytes(cursor.value)
                if cval == val:
                    cursor.delete()
                    deleted = True
                    return False
                return True

            await _walk_cursor(request, on_item)

        return deleted

    async def getTopIoDupItemIter(
        self, db: str, top: bytes = b""
    ) -> list[tuple[bytes, bytes]]:
        """
        LMDBer.getTopIoDupItemIter equivalent - iterate over IoDup items in branch.

        Delegates to getTopItemIter for the raw cursor walk, then strips the
        proem suffix from each compound key (key.proem_hex → key) using
        rsplit('.', 1). Returns (actual_key, val) tuples with proem removed.

        IoDup-family store — compound key format: key + '.' + proem_hex.

        Args:
            db: Object store name (IoDup-family store)
            top: Key prefix to match (empty for all)

        Returns:
            List of (actual_key, val) tuples with proem stripped from keys
        """
        items = await self.getTopItemIter(db, top)
        results = []
        for iokey, val in items:
            try:
                actual_key, _ = _iodup_split(iokey)
                results.append((actual_key, val))
            except ValueError:
                continue
        return results

    # ==========================================================================
    # ONIODUP OPERATIONS - Ordinal key + IoDup
    # ==========================================================================

    async def addOnIoDupVal(
        self, db: str, key: bytes, on: int = 0, val: bytes = b"", *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.addOnIoDupVal equivalent - add IoDup value at ordinal key.

        OnIoDup-family store — compound key format: key + sep + on_hex + '.' + proem_hex.
        Constructs onkey = suffix(key, on, sep=sep), then delegates to
        addIoDupVal which handles dedup and proem assignment.
        """
        onkey = suffix(key, on, sep=sep)
        return await self.addIoDupVal(db, onkey, val)

    async def appendOnIoDupVal(
        self, db: str, key: bytes, val: bytes, *, sep: bytes = b"."
    ) -> int:
        """
        LMDBer.appendOnIoDupVal equivalent - append IoDup at next ordinal.

        OnIoDup-family store — compound key format: key + sep + on_hex + '.' + proem_hex.
        Opens a reverse cursor to find the max existing ordinal, then writes
        val at ordinal max+1 with proem 0.

        Key parsing uses double unsuffix: the compound key has three segments
        (key, on_hex, proem_hex) separated by sep. Two rsplit(sep, 1) calls
        via unsuffix() correctly handle keys that themselves contain the
        separator character (e.g. a key with '.' in it).

        Returns:
            The ordinal number assigned
        """
        key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        # First find max on for this key prefix
        on = 0
        range_start = _key_to_idb(_iodup_join(suffix(key, 0, sep=sep), 0))
        range_end = _key_to_idb(_iodup_join(suffix(key, MaxSuffix, sep=sep), MaxProem))

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
            store = tx.objectStore(db)
            key_range = IDBKeyRange.bound(range_start, range_end, False, False)
            request = store.openCursor(key_range, "prev")

            idb_val = _to_js_bytes(val)

            def on_item(cursor):
                nonlocal on
                key_on_proem = _key_from_idb(cursor.key)
                try:
                    key_on, _ = _iodup_split(key_on_proem)
                    prefix, ordinal = unsuffix(key_on, sep=sep)
                    if prefix == key:
                        on = max(on, ordinal + 1)
                except ValueError:
                    pass
                proem_key = _key_to_idb(_iodup_join(suffix(key, on, sep=sep), 0))
                store.put(idb_val, proem_key)
                return False

            def on_done():
                proem_key = _key_to_idb(_iodup_join(suffix(key, on, sep=sep), 0))
                store.put(idb_val, proem_key)

            await _walk_cursor(request, on_item, on_done)

        return on

    async def delOnIoDupVals(
        self, db: str, key: bytes, on: int = 0, *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.delOnIoDupVals equivalent - delete all IoDups at ordinal key.

        Constructs onkey = suffix(key, on, sep=sep), then delegates to
        delIoDupVals which walks and deletes all proem entries at that onkey.
        """
        onkey = suffix(key, on, sep=sep)
        return await self.delIoDupVals(db, onkey)

    async def delOnIoDupVal(
        self, db: str, key: bytes, on: int = 0, val: bytes = b"", *, sep: bytes = b"."
    ) -> bool:
        """
        LMDBer.delOnIoDupVal equivalent - delete specific IoDup at ordinal key.

        Constructs onkey = suffix(key, on, sep=sep), then delegates to
        delIoDupVal which does a linear cursor search for the matching value.
        """
        onkey = suffix(key, on, sep=sep)
        return await self.delIoDupVal(db, onkey, val)

    async def getOnIoDupValIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getOnIoDupValIter equivalent - iterate over IoDup values at ordinals.

        Wraps getOnIoDupItemIter and strips the (key, on) components, returning
        only the values. Returns a buffered list for transaction safety.

        OnIoDup-family store — compound key format: key + sep + on_hex + '.' + proem_hex.
        """
        vals = []
        items = await self.getOnIoDupItemIter(db, key, on, sep=sep)
        for _, _, val in items:
            vals.append(val)
        return vals

    async def getOnIoDupItemIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[tuple[bytes, int, bytes]]:
        """
        LMDBer.getOnIoDupItemIter equivalent - iterate over (key, on, val) tuples.

        OnIoDup-family store — compound key format: key + sep + on_hex + '.' + proem_hex.
        Parses each compound key using double unsuffix: two rsplit(sep, 1)
        calls via unsuffix() to extract (key, ordinal, proem). This correctly
        handles keys that themselves contain the separator character.

        Returns a buffered list of (key_prefix, ordinal, value) triples for
        transaction safety. All entries for a given (key, ordinal) are returned
        in proem (insertion) order.
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        if key:
            key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        results = []

        if key:
            onkey_start = suffix(key, on, sep=sep)
            idb_start = _key_to_idb(_iodup_join(onkey_start, 0))
            idb_end = _key_to_idb(
                _iodup_join(suffix(key, MaxSuffix, sep=sep), MaxProem)
            )
        else:
            idb_start = None
            idb_end = None

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)

            if idb_start and idb_end:
                key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            else:
                key_range = None

            request = store.openCursor(key_range)

            def on_item(cursor):
                key_on_proem = _key_from_idb(cursor.key)
                try:
                    key_on, _ = _iodup_split(key_on_proem)
                    prefix, ordinal = unsuffix(key_on, sep=sep)
                    if key and prefix != key:
                        return False
                    val = _from_js_bytes(cursor.value)
                    results.append((prefix, ordinal, val))
                except ValueError:
                    pass
                return True

            await _walk_cursor(request, on_item)

        return results

    async def getOnIoDupLastValIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getOnIoDupLastValIter equivalent - get last IoDup val at each ordinal.

        Wraps getOnIoDupLastItemIter and strips the (key, on) components,
        returning only the last value at each ordinal. Returns a buffered
        list for transaction safety.
        """
        vals = []
        items = await self.getOnIoDupLastItemIter(db, key, on, sep=sep)
        for _, _, val in items:
            vals.append(val)
        return vals

    async def getOnIoDupLastItemIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[tuple[bytes, int, bytes]]:
        """
        LMDBer.getOnIoDupLastItemIter equivalent - get last IoDup at each ordinal.

        Delegates to getOnIoDupItemIter to get all items, then groups by
        (key, ordinal) and takes the last value in each group (the one with
        the highest proem, i.e. most recently inserted). Returns results
        sorted by (key, ordinal).

        OnIoDup-family store — compound key format: key + sep + on_hex + '.' + proem_hex.
        """
        # Get all items grouped by ordinal, take last of each group
        all_items = await self.getOnIoDupItemIter(db, key, on, sep=sep)

        # Group by (key, on) and take last
        groups = {}
        for k, o, v in all_items:
            groups[(k, o)] = v  # Last one wins

        results = [(k, o, v) for (k, o), v in sorted(groups.items())]
        return results

    async def getOnIoDupItemBackIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[tuple[bytes, int, bytes]]:
        """
        LMDBer.getOnIoDupItemBackIter equivalent - iterate backwards.

        Returns iterator going backwards of triples (key, on, val), of insertion
        ordered items at each key over all ordinal numbered keys. Backwards means
        decreasing numerical value of duplicate proem, for each on, decreasing
        numerical value on for each key, and decreasing lexicographic order.

        Args:
            db: Object store name
            key: key prefix (empty for whole self.db)
            on: ordinal number at which to start (iterates backwards from here)
            sep: separator character

        Returns:
            List of (key_prefix, ordinal, value) triples in reverse order
        """
        if indexedDB is None:
            raise IndexedDBError("IndexedDB not available")

        results = []

        if key:
            key = _normalize_key_bytes(key)
        sep = _normalize_sep_bytes(sep)

        if key:
            # Start from the specified on and go backwards
            onkey_start = suffix(key, on, sep=sep)
            idb_start = _key_to_idb(_iodup_join(suffix(key, 0, sep=sep), 0))
            idb_end = _key_to_idb(_iodup_join(onkey_start, MaxProem))
        else:
            idb_start = None
            idb_end = None

        async with IDBTransaction(self.db, [db], IDBTransactionMode.READONLY) as tx:
            store = tx.objectStore(db)

            if idb_start and idb_end:
                key_range = IDBKeyRange.bound(idb_start, idb_end, False, False)
            else:
                key_range = None

            # Open cursor in reverse (prev) direction
            request = store.openCursor(key_range, "prev")

            def on_item(cursor):
                key_on_proem = _key_from_idb(cursor.key)
                try:
                    key_on, _ = _iodup_split(key_on_proem)
                    prefix, ordinal = unsuffix(key_on, sep=sep)
                    if key and prefix != key:
                        return False
                    val = _from_js_bytes(cursor.value)
                    results.append((prefix, ordinal, val))
                except ValueError:
                    pass
                return True

            await _walk_cursor(request, on_item)

        return results

    async def getOnIoDupValBackIter(
        self, db: str, key: bytes = b"", on: int = 0, *, sep: bytes = b"."
    ) -> list[bytes]:
        """
        LMDBer.getOnIoDupValBackIter equivalent - iterate backwards, values only.

        Returns iterator going backwards of values of insertion ordered items.

        Args:
            db: Object store name
            key: key prefix (empty for whole self.db)
            on: ordinal number at which to start
            sep: separator character

        Returns:
            List of values in reverse order
        """
        vals = []
        items = await self.getOnIoDupItemBackIter(db, key, on, sep=sep)
        for _, _, val in items:
            vals.append(val)
        return vals


# =============================================================================
# DUPSORT EMULATION NOTES
# =============================================================================
"""
LMDB's dupsort=True allows multiple values per key, sorted lexicographically.
IndexedDB doesn't have native dupsort, but keripy's patterns map naturally:

1. IoSet Pattern (compound keys) - Already used in keripy
   Key: prefix.00000000 = value1
   Key: prefix.00000001 = value2
   
   This is what appendOnVal/getOnVal implement.

2. IoDup Pattern (value prefixing)
   Key: prefix
   Value: 00000000.actual_value
   
   The proem (ordinal prefix) provides insertion ordering.

For keripy browser wallet, the IoSet pattern (compound keys) is preferred since:
- It's what keripy already uses extensively
- Works naturally with IndexedDB's key ordering
- Enables efficient range queries with IDBKeyRange
- No need to parse/sort values client-side
"""
