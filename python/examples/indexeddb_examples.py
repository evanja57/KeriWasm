# -*- encoding: utf-8 -*-
"""
indexeddb_examples.py - IndexedDB ↔ LMDBer Method Mapping Examples

This module documents how keripy's LMDBer methods would map to IndexedDB
when running in a browser via Pyodide/PyScript.

IndexedDB uses B-trees internally, providing:
- Lexicographic key ordering (like LMDB)
- O(log n) cursor positioning
- O(1) cursor iteration

Key differences from LMDB:
- All operations are async (transactions are async)
- No explicit dupsort flag (emulate with compound keys or IoSet pattern)
- Uses IDBKeyRange for range queries

DOCUMENTATION ONLY - shows the patterns for a future IndexedDBer implementation.
"""

import asyncio
from typing import AsyncIterator

# These imports only work in PyScript/Pyodide browser environment
try:
    from js import indexedDB, IDBKeyRange, Uint8Array
    from pyodide.ffi import create_proxy

    HAS_INDEXEDDB = True
except ImportError:
    HAS_INDEXEDDB = False
    indexedDB = None
    IDBKeyRange = None
    Uint8Array = None
    create_proxy = None


# =============================================================================
# HELPERS
# =============================================================================


def _to_js_bytes(data: bytes) -> "Uint8Array":
    """Convert Python bytes to JS Uint8Array for IndexedDB storage."""
    return Uint8Array.new(list(data))


def _from_js_bytes(js_array) -> bytes:
    """Convert JS Uint8Array back to Python bytes."""
    return bytes(js_array)


async def _await_request(request) -> any:
    """
    Convert IDBRequest to Python awaitable.

    IndexedDB operations return IDBRequest objects. This helper wraps them
    in a Future that resolves when onsuccess fires.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    def on_success(event):
        if not future.done():
            future.set_result(event.target.result)

    def on_error(event):
        if not future.done():
            future.set_exception(Exception(str(event.target.error)))

    request.onsuccess = create_proxy(on_success)
    request.onerror = create_proxy(on_error)

    return await future


async def _await_transaction(tx) -> bool:
    """
    Wait for an IndexedDB transaction to complete.

    Transactions auto-commit when all requests complete and no new requests
    are made. This awaits the oncomplete event for explicit confirmation.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    def on_complete(event):
        if not future.done():
            future.set_result(True)

    def on_error(event):
        if not future.done():
            future.set_exception(Exception(str(event.target.error)))

    def on_abort(event):
        if not future.done():
            future.set_exception(Exception("Transaction aborted"))

    tx.oncomplete = create_proxy(on_complete)
    tx.onerror = create_proxy(on_error)
    tx.onabort = create_proxy(on_abort)

    return await future


# =============================================================================
# DATABASE SETUP
# =============================================================================


async def open_database(name: str, stores: list[str], version: int = 1):
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

    Returns:
        IDBDatabase instance
    """
    request = indexedDB.open(name, version)

    def on_upgrade(event):
        """Called when database is created or version increases."""
        db = event.target.result
        for store_name in stores:
            if store_name not in db.objectStoreNames:
                # Keys are stored in sorted order (B-tree)
                db.createObjectStore(store_name)

    request.onupgradeneeded = create_proxy(on_upgrade)

    return await _await_request(request)


# =============================================================================
# TRANSACTION CONTEXT MANAGER (async with)
# =============================================================================


class IDBTransaction:
    """
    Async context manager for IndexedDB transactions.

    This is the "async with" pattern Sam asked about. Transactions in IndexedDB
    are atomic - either all operations succeed or none do.

    Usage:
        async with IDBTransaction(db, ['store1'], 'readwrite') as tx:
            store = tx.objectStore('store1')
            store.put(value, key)
        # Transaction auto-commits here if no errors

    Equivalent to LMDB's:
        with self.env.begin(db=db, write=True) as txn:
            txn.put(key, val)
    """

    def __init__(self, db, store_names: list[str], mode: str = "readonly"):
        """
        Args:
            db: IDBDatabase instance
            store_names: Object stores to include in transaction
            mode: 'readonly' or 'readwrite'
        """
        self.db = db
        self.store_names = store_names
        self.mode = mode
        self.tx = None

    async def __aenter__(self):
        self.tx = self.db.transaction(self.store_names, self.mode)
        return self.tx

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None and self.tx is not None:
            # Wait for transaction to complete (durability guarantee)
            await _await_transaction(self.tx)
        # If exception occurred, transaction will auto-abort
        return False


# =============================================================================
# BASIC OPERATIONS (LMDBer equivalents)
# =============================================================================


async def putVal(db, store_name: str, key: bytes, val: bytes) -> bool:
    """
    LMDBer.putVal equivalent - insert if key does not exist.

    LMDB:
        with self.env.begin(db=db, write=True) as txn:
            return txn.put(key, val, overwrite=False)

    Returns:
        True if inserted, False if key already exists
    """
    async with IDBTransaction(db, [store_name], "readwrite") as tx:
        store = tx.objectStore(store_name)

        # Check if key exists first
        existing = await _await_request(store.get(_to_js_bytes(key)))
        if existing is not None:
            return False

        store.put(_to_js_bytes(val), _to_js_bytes(key))

    return True


async def setVal(db, store_name: str, key: bytes, val: bytes) -> bool:
    """
    LMDBer.setVal equivalent - upsert (insert or overwrite).

    LMDB:
        with self.env.begin(db=db, write=True) as txn:
            return txn.put(key, val)  # overwrite=True is default
    """
    async with IDBTransaction(db, [store_name], "readwrite") as tx:
        store = tx.objectStore(store_name)
        store.put(_to_js_bytes(val), _to_js_bytes(key))
    return True


async def getVal(db, store_name: str, key: bytes) -> bytes | None:
    """
    LMDBer.getVal equivalent - retrieve value by key.

    LMDB:
        with self.env.begin(db=db, write=False) as txn:
            return txn.get(key)
    """
    async with IDBTransaction(db, [store_name], "readonly") as tx:
        store = tx.objectStore(store_name)
        result = await _await_request(store.get(_to_js_bytes(key)))
        return _from_js_bytes(result) if result is not None else None


async def delVal(db, store_name: str, key: bytes) -> bool:
    """
    LMDBer.delVal equivalent - delete key.

    LMDB:
        with self.env.begin(db=db, write=True) as txn:
            return txn.delete(key)
    """
    async with IDBTransaction(db, [store_name], "readwrite") as tx:
        store = tx.objectStore(store_name)

        # Check if key exists
        existing = await _await_request(store.get(_to_js_bytes(key)))
        if existing is None:
            return False

        store.delete(_to_js_bytes(key))
    return True


# =============================================================================
# PREFIX ITERATION (getTopItemIter)
# =============================================================================


async def getTopItemIter(
    db, store_name: str, top: bytes = b""
) -> AsyncIterator[tuple[bytes, bytes]]:
    """
    LMDBer.getTopItemIter equivalent - iterate keys starting with prefix.

    Uses IDBKeyRange.bound() to efficiently seek to prefix range.
    IndexedDB cursors iterate in lexicographic key order (B-tree property).

    LMDB:
        with self.env.begin(db=db, write=False) as txn:
            cursor = txn.cursor()
            if cursor.set_range(top):
                for key, val in cursor.iternext():
                    if not key.startswith(top):
                        break
                    yield (key, val)

    IndexedDB equivalent uses IDBKeyRange for the prefix match:
        range = IDBKeyRange.bound(prefix, prefix + '\\uffff')

    The '\\uffff' suffix creates an upper bound that captures all keys
    starting with the prefix (since \\uffff is the highest Unicode char).
    """
    tx = db.transaction([store_name], "readonly")
    store = tx.objectStore(store_name)

    if top:
        # Create range for prefix matching
        # Lower bound: prefix (inclusive)
        # Upper bound: prefix + '\uffff' (exclusive) - captures all prefix matches
        prefix_str = top.decode("utf-8")
        key_range = IDBKeyRange.bound(
            prefix_str,
            prefix_str + "\uffff",
            False,  # includeLower
            True,  # excludeUpper
        )
        request = store.openCursor(key_range)
    else:
        # No prefix - iterate all keys
        request = store.openCursor()

    while True:
        cursor = await _await_request(request)
        if cursor is None:
            break

        # Convert JS types back to Python bytes
        key = cursor.key
        if isinstance(key, str):
            key = key.encode("utf-8")
        else:
            key = _from_js_bytes(key)

        val = _from_js_bytes(cursor.value)

        yield (key, val)

        # Advance cursor to next item
        cursor.continue_()


# =============================================================================
# ORDERED APPEND (appendOnVal)
# =============================================================================


async def appendOnVal(
    db, store_name: str, key: bytes, val: bytes, sep: bytes = b"."
) -> int:
    """
    LMDBer.appendOnVal equivalent - append value with next ordinal.

    Keys are structured as: prefix.NNNNNNNN (32 hex digits)
    Example: b"mykey.00000000", b"mykey.00000001", etc.

    This finds the max existing ordinal for the key prefix and inserts
    at max + 1.

    LMDB uses cursor.set_range() with max ordinal and walks backward.
    IndexedDB equivalent: openCursor(range, 'prev') for reverse iteration.

    Returns:
        The ordinal number assigned to the new entry
    """
    async with IDBTransaction(db, [store_name], "readwrite") as tx:
        store = tx.objectStore(store_name)

        key_str = key.decode("utf-8")
        sep_str = sep.decode("utf-8")

        # Create range from key.00000000 to key.ffffffff
        min_key = f"{key_str}{sep_str}{'0' * 32}"
        max_key = f"{key_str}{sep_str}{'f' * 32}"

        key_range = IDBKeyRange.bound(min_key, max_key)

        # Open cursor in REVERSE direction to find max
        request = store.openCursor(key_range, "prev")

        on = 0  # Default ordinal if no existing entries
        cursor = await _await_request(request)

        if cursor is not None:
            cursor_key = cursor.key
            # Parse ordinal from existing key
            if sep_str in cursor_key:
                _, ordinal_hex = cursor_key.rsplit(sep_str, 1)
                try:
                    on = int(ordinal_hex, 16) + 1
                except ValueError:
                    pass  # Malformed key, use default

        # Write with new ordinal (32 hex digits, zero-padded)
        new_key = f"{key_str}{sep_str}{on:032x}"
        store.put(_to_js_bytes(val), new_key)

    return on


# =============================================================================
# ORDINAL KEY OPERATIONS (getOnVal, setOnVal, etc.)
# =============================================================================


async def getOnVal(
    db, store_name: str, key: bytes, on: int = 0, sep: bytes = b"."
) -> bytes | None:
    """
    LMDBer.getOnVal equivalent - get value at specific ordinal.

    Constructs key as: prefix.NNNNNNNN and retrieves value.
    """
    key_str = key.decode("utf-8")
    sep_str = sep.decode("utf-8")
    onkey = f"{key_str}{sep_str}{on:032x}"

    async with IDBTransaction(db, [store_name], "readonly") as tx:
        store = tx.objectStore(store_name)
        result = await _await_request(store.get(onkey))
        return _from_js_bytes(result) if result is not None else None


async def getOnItemIter(
    db, store_name: str, key: bytes = b"", on: int = 0, sep: bytes = b"."
) -> AsyncIterator[tuple[bytes, int, bytes]]:
    """
    LMDBer.getOnItemIter equivalent - iterate (key, ordinal, value) tuples.

    Yields tuples of (prefix_key, ordinal_number, value) for all entries
    matching the key prefix starting from ordinal `on`.
    """
    key_str = key.decode("utf-8") if key else ""
    sep_str = sep.decode("utf-8")

    # Start from key.on
    if key_str:
        start_key = f"{key_str}{sep_str}{on:032x}"
    else:
        start_key = ""

    tx = db.transaction([store_name], "readonly")
    store = tx.objectStore(store_name)

    if start_key:
        key_range = IDBKeyRange.lowerBound(start_key)
        request = store.openCursor(key_range)
    else:
        request = store.openCursor()

    while True:
        cursor = await _await_request(request)
        if cursor is None:
            break

        cursor_key = cursor.key

        # Parse key into prefix and ordinal
        if sep_str not in cursor_key:
            cursor.continue_()
            continue

        prefix, ordinal_hex = cursor_key.rsplit(sep_str, 1)

        # Check prefix match
        if key_str and prefix != key_str:
            if prefix > key_str:
                break  # Past our prefix range
            cursor.continue_()
            continue

        try:
            ordinal = int(ordinal_hex, 16)
        except ValueError:
            cursor.continue_()
            continue

        val = _from_js_bytes(cursor.value)
        yield (prefix.encode("utf-8"), ordinal, val)

        cursor.continue_()


# =============================================================================
# DUPSORT EMULATION
# =============================================================================
"""
LMDB's dupsort=True allows multiple values per key, sorted lexicographically.
IndexedDB doesn't have native dupsort, but we can emulate it using:

Option 1: Compound keys (recommended for IoSet pattern)
    Key: prefix.00000000 = value1
    Key: prefix.00000001 = value2
    
    This is what IoSetSuber already does.

Option 2: Store values as arrays
    Key: prefix = [value1, value2, value3]
    
    Simpler but loses per-value ordering control.

Option 3: Value prefixing (IoDup pattern)  
    Value: 00000000.actual_value
    
    Proem prefix provides insertion ordering within a key.

For keripy, the existing IoSet and IoDup patterns map naturally to IndexedDB
since they don't rely on dupsort - they use key suffixing instead.
"""


# =============================================================================
# EXAMPLE USAGE (for documentation)
# =============================================================================


async def example_workflow():
    """
    Example showing a complete workflow with IndexedDB.

    This demonstrates what a refactored keripy database layer would look like.
    """
    # 1. Open database with object stores (sub-DBs)
    db = await open_database(
        name="keri-example",
        stores=["evts", "kels", "pdes"],  # Like LMDB sub-databases
        version=1,
    )

    # 2. Basic put/get operations
    key = b"BDg3H7Sr-eES0XWXiO8nvMxW6mD_1LxLeE1nuiZxhGp4"
    val = b'{"v":"KERI10JSON000000_","t":"icp"}'

    await setVal(db, "evts", key, val)
    retrieved = await getVal(db, "evts", key)
    assert retrieved == val

    # 3. Prefix iteration (like getTopItemIter)
    prefix = b"BDg3H7Sr"
    async for k, v in getTopItemIter(db, "evts", top=prefix):
        print(f"Found: {k} -> {v}")

    # 4. Ordered append (like appendOnVal)
    ordinal = await appendOnVal(db, "kels", b"BDg3H7Sr", b"digest1")
    print(f"Appended at ordinal {ordinal}")  # 0

    ordinal = await appendOnVal(db, "kels", b"BDg3H7Sr", b"digest2")
    print(f"Appended at ordinal {ordinal}")  # 1

    # 5. Iterate ordered entries
    async for pre, on, val in getOnItemIter(db, "kels", key=b"BDg3H7Sr"):
        print(f"KEL entry: {pre}.{on:08x} -> {val}")

    return db
