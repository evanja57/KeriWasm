# -*- encoding: utf-8 -*-
"""
test_indexeddb.py - Browser-based tests for IndexedDB core API.

Core API surface covered in this file:
- putVal / setVal / getVal / delVal
- cnt / cntAll
- getTopItemIter
- db=<store> keyword compatibility
"""

from __future__ import annotations

import asyncio

from indexeddb_python import IndexedDBer, deleteDatabase


class AsyncTestResults:
    """Tracks test pass/fail counts for async tests."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = 0
        self.failures = []
        self.error_list = []

    def record_pass(self, name: str):
        self.passed += 1
        print(f"  PASS: {name}")

    def record_fail(self, name: str, msg: str):
        self.failed += 1
        self.failures.append((name, msg))
        print(f"  FAIL: {name}")
        print(f"    AssertionError: {msg}")

    def record_error(self, name: str, msg: str):
        self.errors += 1
        self.error_list.append((name, msg))
        print(f"  ERROR: {name}")
        print(f"    {msg}")

    def print_summary(self):
        total = self.passed + self.failed + self.errors
        print("=" * 64)
        print("TEST SUMMARY")
        print("=" * 64)
        print(f"Total:  {total}")
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")
        print(f"Errors: {self.errors}")

        if self.failed == 0 and self.errors == 0:
            print("-" * 64)
            print("ALL TESTS PASSED!")
        else:
            if self.failures:
                print("-" * 64)
                print("FAILURES:")
                for name, msg in self.failures:
                    print(f"  {name}: {msg}")
            if self.error_list:
                print("-" * 64)
                print("ERRORS:")
                for name, msg in self.error_list:
                    print(f"  {name}: {msg}")


async def test_basic_put_get(dber, store):
    """Test putVal/getVal + no-overwrite behavior."""
    key = b"test_basic_key"
    val = b"test_basic_value"

    result = await dber.putVal(store, key, val)
    assert result is True, f"putVal returned {result}"

    retrieved = await dber.getVal(store, key)
    assert retrieved == val, f"Got {retrieved!r}, expected {val!r}"

    result = await dber.putVal(store, key, b"other")
    assert result is False, f"putVal on existing key returned {result}"

    retrieved = await dber.getVal(store, key)
    assert retrieved == val, f"Got {retrieved!r}, expected {val!r}"


async def test_set_overwrite(dber, store):
    """Test setVal overwrites existing values."""
    key = b"test_set_key"
    val1 = b"value1"
    val2 = b"value2"

    await dber.setVal(store, key, val1)
    assert await dber.getVal(store, key) == val1

    await dber.setVal(store, key, val2)
    assert await dber.getVal(store, key) == val2


async def test_del(dber, store):
    """Test delVal removes key/value."""
    key = b"test_del_key"
    val = b"to_delete"

    await dber.setVal(store, key, val)
    assert await dber.getVal(store, key) == val

    result = await dber.delVal(store, key)
    assert result is True, f"delVal returned {result}"
    assert await dber.getVal(store, key) is None


async def test_non_utf8_key_roundtrip(dber, store):
    """Test arbitrary byte-key roundtrip."""
    bad_key = b"\xff\x00\x10"
    val = b"value"

    await dber.setVal(store, bad_key, val)
    assert await dber.getVal(store, bad_key) == val
    assert await dber.delVal(store, bad_key) is True


async def test_prefix_iteration(dber, store):
    """Test getTopItemIter with prefix matching."""
    for key, _ in await dber.getTopItemIter(store):
        await dber.delVal(store, key)

    await dber.setVal(store, b"ABC.001", b"val1")
    await dber.setVal(store, b"ABC.002", b"val2")
    await dber.setVal(store, b"ABD.001", b"val3")
    await dber.setVal(store, b"XYZ.001", b"val4")

    items = await dber.getTopItemIter(store, b"ABC")
    keys = [k for k, _ in items]

    assert len(keys) == 2, f"Expected 2 items, got {len(keys)}"
    assert b"ABC.001" in keys
    assert b"ABC.002" in keys


async def test_cntAll_alias(dber, store):
    """Test cntAll remains an alias of cnt."""
    await dber.setVal(store, b"cntall_alias_key", b"v")
    assert await dber.cntAll(store) == await dber.cnt(store)
    await dber.delVal(store, b"cntall_alias_key")


async def test_indexeddber_db_kw_compat(dber, store):
    """Test db=<store> keyword compatibility for LMDBer parity."""
    key = b"class_db_kw_key"
    val = b"class_db_kw_val"

    assert await dber.setVal(db=store, key=key, val=val) is True
    assert await dber.getVal(db=store, key=key) == val
    assert await dber.cntAll(db=store) == await dber.cnt(db=store)
    assert await dber.delVal(db=store, key=key) is True


async def test_empty_key_val_raises_KeyError(dber, store):
    """Test core Val operations reject empty keys."""
    empty_key = b""
    some_value = b"foo"

    for name, coro_fn in [
        ("putVal", lambda: dber.putVal(store, empty_key, some_value)),
        ("setVal", lambda: dber.setVal(store, empty_key, some_value)),
        ("getVal", lambda: dber.getVal(store, empty_key)),
        ("delVal", lambda: dber.delVal(store, empty_key)),
    ]:
        try:
            await coro_fn()
            assert False, f"{name} should have raised KeyError for empty key"
        except KeyError:
            pass


async def run_all_tests():
    """Run all IndexedDB core tests."""
    results = AsyncTestResults()

    print("=" * 64)
    print("IndexedDB Core API Tests")
    print("=" * 64)
    print()

    print("Setting up test database...")
    try:
        try:
            await deleteDatabase("test_indexeddb")
        except Exception:
            pass

        dber = await IndexedDBer.open(
            "test_indexeddb",
            ["test_store", "empty_store"],
            version=1,
        )
        print("Database opened successfully")
    except Exception as e:
        print(f"ERROR: Failed to open database: {e}")
        results.record_error("database_setup", str(e))
        results.print_summary()
        return results

    test_sections = [
        (
            "Basic Operations",
            "test_store",
            [
                ("test_basic_put_get", test_basic_put_get),
                ("test_set_overwrite", test_set_overwrite),
                ("test_del", test_del),
                ("test_non_utf8_key_roundtrip", test_non_utf8_key_roundtrip),
                ("test_prefix_iteration", test_prefix_iteration),
                ("test_cntAll_alias", test_cntAll_alias),
                ("test_indexeddber_db_kw_compat", test_indexeddber_db_kw_compat),
            ],
        ),
        (
            "Empty Key Handling",
            "empty_store",
            [("test_empty_key_val_raises_KeyError", test_empty_key_val_raises_KeyError)],
        ),
    ]

    for section_name, store, tests in test_sections:
        print()
        print(section_name)
        print("-" * 32)
        for name, func in tests:
            try:
                await func(dber, store)
                results.record_pass(name)
            except AssertionError as e:
                results.record_fail(name, str(e))
            except Exception as e:
                results.record_error(name, f"{type(e).__name__}: {e}")

    print()
    print("Cleaning up...")
    try:
        dber.close()
        await deleteDatabase("test_indexeddb")
        print("Cleanup complete")
    except Exception as e:
        print(f"Cleanup warning: {e}")

    print()
    results.print_summary()
    return results


def run_indexeddb_tests():
    """Entry point to run core tests. Returns a coroutine."""
    return run_all_tests()


if __name__ == "__main__":
    asyncio.ensure_future(run_all_tests())
