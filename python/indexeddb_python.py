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
    if hasattr(js_array, 'to_py'):
        result = js_array.to_py()
        if hasattr(result, 'tobytes'):
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


# Metadata constants
_METADATA_STORE = "__metadata__"
_VERSION_KEY = _key_to_idb(b"__version__")


def _key_from_idb(idb_key: Any) -> bytes:
    """
    Decode IndexedDB key back to bytes.
    """
    if isinstance(idb_key, str):
        if (len(idb_key) % 2 == 0
                and all(ch in "0123456789abcdefABCDEF" for ch in idb_key)):
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


async def _walk_cursor(request: Any, on_item: callable, on_done: Optional[callable] = None) -> None:
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


class IDBTransactionMode(Enum):
    """IndexedDB transaction modes."""
    READONLY = 'readonly'
    READWRITE = 'readwrite'


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
        mode: IDBTransactionMode = IDBTransactionMode.READONLY
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
    on_blocked: Optional[callable] = None
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
        raise IndexedDBError("IndexedDB not available - not running in browser environment")
    stores = [_coerce_store_name(store) for store in stores]
    
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    
    # Track all proxies for cleanup
    proxies = []
    
    def on_upgrade(event):
        """Called when database is created or version increases."""
        db = event.target.result
        for store_name in (list(stores) + [_METADATA_STORE]):
            names = db.objectStoreNames
            exists = names.contains(store_name) if hasattr(names, "contains") else (store_name in names)
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
            error_msg = str(event.target.error) if event.target.error else "Failed to open database"
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
        exists = names.contains(_METADATA_STORE) if hasattr(names, "contains") else (_METADATA_STORE in names)
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
        on_blocked: Optional[callable] = None
    ) -> 'IndexedDBer':
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
            raise DatabaseNotOpenError(f"Database '{self.name}' has no configured stores")
        stores = [_coerce_store_name(store) for store in stores]

        on_blocked = kwa.pop("on_blocked", None)
        clear = bool(kwa.pop("clear", False))
        current_version = getattr(self.db, "version", None) if self.db is not None else None
        version = kwa.pop("version", current_version if current_version is not None else 1)

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
        async with IDBTransaction(self.db, [_METADATA_STORE], IDBTransactionMode.READONLY) as tx:
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

        async with IDBTransaction(self.db, [_METADATA_STORE], IDBTransactionMode.READWRITE) as tx:
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
            raise KeyError(f"Key: `{key}` is either empty, too big (for lmdb), "
                            "or wrong DUPFIXED size. ref) lmdb.BadValsizeError")
        idb_key = _key_to_idb(key)
        idb_val = _to_js_bytes(val)

        try:
            async with IDBTransaction(self.db, [db], IDBTransactionMode.READWRITE) as tx:
                store = tx.objectStore(db)
                await _await_request(store.add(idb_val, idb_key))
            return True
        except IndexedDBRequestError as e:
            if e.name == "ConstraintError" or 'ConstraintError' in str(e):
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
            raise KeyError(f"Key: `{key}` is either empty, too big (for lmdb), "
                            "or wrong DUPFIXED size. ref) lmdb.BadValsizeError")
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
            raise KeyError(f"Key: `{key}` is either empty, too big (for lmdb), "
                            "or wrong DUPFIXED size. ref) lmdb.BadValsizeError")
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
            raise KeyError(f"Key: `{key}` is either empty, too big (for lmdb), "
                            "or wrong DUPFIXED size. ref) lmdb.BadValsizeError")
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

    # Iteration (buffered)
    async def getTopItemIter(self, db: str, top: bytes = b'') -> list[tuple[bytes, bytes]]:
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
                key_range = IDBKeyRange.bound(prefix_str, prefix_str + '\uffff', False, True)
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
