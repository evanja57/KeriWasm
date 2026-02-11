# -*- encoding: utf-8 -*-
"""
test_indexeddb.py - Browser-based tests for IndexedDB backend.

This module provides comprehensive tests for the IndexedDB implementation that
mirrors keripy's LMDBer interface. Tests are designed to run in a browser
environment via PyScript.

Usage:
    Load this module and call run_indexeddb_tests() to execute all tests.
    Results are logged to the browser console and DOM.
"""

from __future__ import annotations

import asyncio

# Import the IndexedDB module
from core.indexeddb_python import (
    IndexedDBer,
    deleteDatabase,
    suffix,
    unsuffix,
)


# =============================================================================
# TEST RESULTS TRACKING
# =============================================================================


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


# =============================================================================
# HELPER FUNCTION TESTS
# =============================================================================


def test_suffix():
    """Test suffix() helper function."""
    result = suffix(b"key", 0)
    assert result == b"key.00000000000000000000000000000000", f"Got {result!r}"

    result = suffix(b"key", 1)
    assert result == b"key.00000000000000000000000000000001", f"Got {result!r}"

    result = suffix(b"key", 255)
    assert result == b"key.000000000000000000000000000000ff", f"Got {result!r}"

    # Custom separator
    result = suffix(b"key", 1, sep=b"|")
    assert result == b"key|00000000000000000000000000000001", f"Got {result!r}"


def test_unsuffix():
    """Test unsuffix() helper function."""
    key, ion = unsuffix(b"key.00000000000000000000000000000000")
    assert key == b"key", f"Key: {key!r}"
    assert ion == 0, f"Ion: {ion}"

    key, ion = unsuffix(b"key.00000000000000000000000000000001")
    assert key == b"key", f"Key: {key!r}"
    assert ion == 1, f"Ion: {ion}"

    key, ion = unsuffix(b"key.000000000000000000000000000000ff")
    assert key == b"key", f"Key: {key!r}"
    assert ion == 255, f"Ion: {ion}"

    # Custom separator
    key, ion = unsuffix(b"key|00000000000000000000000000000001", sep=b"|")
    assert key == b"key", f"Key: {key!r}"
    assert ion == 1, f"Ion: {ion}"


def test_suffix_unsuffix_roundtrip():
    """Test suffix/unsuffix roundtrip encoding/decoding."""
    for key_in in [b"A", b"ABC", b"pre.fix"]:
        for ion_in in [0, 1, 255, 65535, 2**64]:
            iokey = suffix(key_in, ion_in)
            key_out, ion_out = unsuffix(iokey)
            assert key_out == key_in, f"Key mismatch: {key_out!r} != {key_in!r}"
            assert ion_out == ion_in, f"Ion mismatch: {ion_out} != {ion_in}"


def test_suffix_as_onKey():
    """Verify suffix() produces same output as keripy's onKey() for representative inputs."""
    # keripy onKey(pre=b'A', on=0) -> b'A.00000000000000000000000000000000'
    assert suffix(b"A", 0) == b"A.00000000000000000000000000000000"
    assert suffix(b"A", 3) == b"A.00000000000000000000000000000003"


def test_unsuffix_as_splitOnKey():
    """Verify unsuffix() matches keripy's splitOnKey() behavior."""
    key, on = unsuffix(b"A.00000000000000000000000000000003")
    assert key == b"A"
    assert on == 3


def test_suffix_custom_sep():
    """Verify suffix/unsuffix with custom separator (matching keripy test)."""
    result = suffix(b"key", 1, sep=b"|")
    assert result == b"key|00000000000000000000000000000001"

    key, ion = unsuffix(result, sep=b"|")
    assert key == b"key"
    assert ion == 1

    # Roundtrip with different sep
    for sep in [b"|", b"/", b"_"]:
        iokey = suffix(b"test", 42, sep=sep)
        k, i = unsuffix(iokey, sep=sep)
        assert k == b"test"
        assert i == 42


# =============================================================================
# BASIC OPERATIONS TESTS
# =============================================================================


async def test_basic_put_get(dber, store):
    """Test basic putVal/getVal operations."""
    key = b"test_basic_key"
    val = b"test_basic_value"

    result = await dber.putVal(store, key, val)
    assert result == True, f"putVal returned {result}"

    retrieved = await dber.getVal(store, key)
    assert retrieved == val, f"Got {retrieved!r}, expected {val!r}"

    # putVal should fail on existing key
    result = await dber.putVal(store, key, b"other")
    assert result == False, f"putVal on existing key returned {result}"

    # Value should be unchanged
    retrieved = await dber.getVal(store, key)
    assert retrieved == val, f"Got {retrieved!r}, expected {val!r}"


async def test_set_overwrite(dber, store):
    """Test setVal overwrites existing values."""
    key = b"test_set_key"
    val1 = b"value1"
    val2 = b"value2"

    await dber.setVal(store, key, val1)
    retrieved = await dber.getVal(store, key)
    assert retrieved == val1

    await dber.setVal(store, key, val2)
    retrieved = await dber.getVal(store, key)
    assert retrieved == val2, f"Got {retrieved!r}, expected {val2!r}"


async def test_del(dber, store):
    """Test delVal operation."""
    key = b"test_del_key"
    val = b"to_delete"

    await dber.setVal(store, key, val)
    assert await dber.getVal(store, key) == val

    result = await dber.delVal(store, key)
    assert result == True, f"delVal returned {result}"

    assert await dber.getVal(store, key) is None


async def test_non_utf8_key_roundtrip(dber, store):
    """Test that arbitrary byte keys round-trip correctly."""
    bad_key = b"\xff\x00\x10"
    val = b"value"
    await dber.setVal(store, bad_key, val)
    assert await dber.getVal(store, bad_key) == val
    assert await dber.delVal(store, bad_key) is True


async def test_prefix_iteration(dber, store):
    """Test getTopItemIter with prefix matching."""
    # Clear store first
    items = await dber.getTopItemIter(store)
    for k, v in items:
        await dber.delVal(store, k)

    await dber.setVal(store, b"ABC.001", b"val1")
    await dber.setVal(store, b"ABC.002", b"val2")
    await dber.setVal(store, b"ABD.001", b"val3")
    await dber.setVal(store, b"XYZ.001", b"val4")

    items = await dber.getTopItemIter(store, b"ABC")
    keys = [k for k, v in items]
    assert len(keys) == 2, f"Expected 2 items, got {len(keys)}"
    assert b"ABC.001" in keys
    assert b"ABC.002" in keys


async def test_indexeddber_db_kw_compat(dber, store):
    """Test IndexedDBer methods accept keripy-style db=<store> keyword."""
    key = b"class_db_kw_key"
    val = b"class_db_kw_val"

    # Ensure known baseline for cnt() assertions
    await dber.delVal(db=store, key=key)
    baseline = await dber.cnt(db=store)

    assert await dber.setVal(db=store, key=key, val=val) is True
    assert await dber.getVal(db=store, key=key) == val
    assert await dber.cnt(db=store) == baseline + 1
    assert await dber.delVal(db=store, key=key) is True
    assert await dber.cnt(db=store) == baseline


# =============================================================================
# ORDINAL OPERATIONS TESTS
# =============================================================================


async def test_append_on_val(dber, store):
    """Test appendOnVal appends with incrementing ordinals."""
    key = b"test_append_key"

    on0 = await dber.appendOnVal(store, key, b"first")
    assert on0 == 0, f"First append returned {on0}"

    on1 = await dber.appendOnVal(store, key, b"second")
    assert on1 == 1, f"Second append returned {on1}"

    on2 = await dber.appendOnVal(store, key, b"third")
    assert on2 == 2, f"Third append returned {on2}"

    val = await dber.getOnVal(store, key, 0)
    assert val == b"first", f"Got {val!r}"

    val = await dber.getOnVal(store, key, 1)
    assert val == b"second", f"Got {val!r}"

    val = await dber.getOnVal(store, key, 2)
    assert val == b"third", f"Got {val!r}"


async def test_get_on_item_iter(dber, store):
    """Test getOnItemIter returns (key, on, val) triples."""
    key = b"test_iter_key"

    await dber.appendOnVal(store, key, b"a")
    await dber.appendOnVal(store, key, b"b")
    await dber.appendOnVal(store, key, b"c")

    items = await dber.getOnItemIter(store, key)
    assert len(items) >= 3, f"Expected at least 3 items, got {len(items)}"

    ordinals = [on for k, on, v in items if k == key]
    assert 0 in ordinals
    assert 1 in ordinals
    assert 2 in ordinals


async def test_putOnVal_no_overwrite(dber, store):
    """Test putOnVal returns False if onkey exists (overwrite=False)."""
    key = b"test_putOnVal_key"
    val1 = b"original"
    val2 = b"replacement"

    # First put should succeed
    result = await dber.putOnVal(store, key, 0, val=val1)
    assert result == True, f"First putOnVal returned {result}"

    # Verify value
    retrieved = await dber.getOnVal(store, key, 0)
    assert retrieved == val1, f"Got {retrieved!r}"

    # Second put at same ordinal should fail (no overwrite)
    result = await dber.putOnVal(store, key, 0, val=val2)
    assert result == False, f"Duplicate putOnVal returned {result}"

    # Value should be unchanged
    retrieved = await dber.getOnVal(store, key, 0)
    assert retrieved == val1, f"Value changed to {retrieved!r}"

    # setOnVal SHOULD overwrite
    result = await dber.setOnVal(store, key, 0, val2)
    assert result == True
    retrieved = await dber.getOnVal(store, key, 0)
    assert retrieved == val2, f"setOnVal didn't overwrite, got {retrieved!r}"

    # Clean up
    await dber.delOnVal(store, key, 0)


async def test_getOnValIter_vals_only(dber, store):
    """Test getOnValIter yields values only, not tuples."""
    key = b"test_onvaliter_key"

    await dber.appendOnVal(store, key, b"alpha")
    await dber.appendOnVal(store, key, b"beta")
    await dber.appendOnVal(store, key, b"gamma")

    vals = await dber.getOnValIter(store, key)
    assert len(vals) >= 3, f"Expected at least 3 vals, got {len(vals)}"
    # Should be values only, not tuples
    for v in vals:
        assert isinstance(v, bytes), f"Expected bytes, got {type(v)}"
    assert b"alpha" in vals
    assert b"beta" in vals
    assert b"gamma" in vals


async def test_cntOnVals(dber, store):
    """Test cntOnVals counts ordinal entries (renamed from cntVals)."""
    key = b"test_cntOnVals_key"

    await dber.appendOnVal(store, key, b"v1")
    await dber.appendOnVal(store, key, b"v2")
    await dber.appendOnVal(store, key, b"v3")

    cnt = await dber.cntOnVals(store, key)
    assert cnt == 3, f"Expected 3, got {cnt}"

    cnt_from_1 = await dber.cntOnVals(store, key, on=1)
    assert cnt_from_1 == 2, f"Expected 2 from on=1, got {cnt_from_1}"

    cnt_all = await dber.cntOnVals(store, key=b"")
    assert cnt_all >= 3, f"Expected at least 3 for whole-store count, got {cnt_all}"


async def test_getOnValIter_empty_key(dber, store):
    """Test getOnValIter supports key=b'' (whole-store replay)."""
    key_a = b"empty_iter_A"
    key_b = b"empty_iter_B"
    val_a = b"vA"
    val_b = b"vB"

    await dber.appendOnVal(store, key_a, val_a)
    await dber.appendOnVal(store, key_b, val_b)

    vals = await dber.getOnValIter(store, key=b"")
    assert val_a in vals, f"Missing {val_a!r} in whole-store replay"
    assert val_b in vals, f"Missing {val_b!r} in whole-store replay"


async def test_delTopVal_branch(dber, store):
    """Test delTopVal deletes ALL keys with prefix."""
    # Insert several keys with a common prefix
    await dber.setVal(store, b"DEL_TOP_A1", b"v1")
    await dber.setVal(store, b"DEL_TOP_A2", b"v2")
    await dber.setVal(store, b"DEL_TOP_B1", b"v3")
    await dber.setVal(store, b"OTHER_KEY", b"v4")

    # Delete all keys starting with DEL_TOP_A
    result = await dber.delTopVal(store, b"DEL_TOP_A")
    assert result == True, f"delTopVal returned {result}"

    # DEL_TOP_A keys should be gone
    assert await dber.getVal(store, b"DEL_TOP_A1") is None
    assert await dber.getVal(store, b"DEL_TOP_A2") is None

    # Other keys should remain
    assert await dber.getVal(store, b"DEL_TOP_B1") == b"v3"
    assert await dber.getVal(store, b"OTHER_KEY") == b"v4"

    # Clean up
    await dber.delVal(store, b"DEL_TOP_B1")
    await dber.delVal(store, b"OTHER_KEY")


# =============================================================================
# IOSET OPERATIONS TESTS
# =============================================================================


async def test_ioset_put_get(dber, store):
    """Test IoSet put/get operations."""
    key = b"ioset_key"
    vals = [b"z", b"m", b"x", b"a"]

    await dber.delIoSetVals(store, key)

    result = await dber.putIoSetVals(store, key, vals)
    assert result == True, f"putIoSetVals returned {result}"

    retrieved = await dber.getIoSetVals(store, key)
    assert retrieved == vals, f"Got {retrieved}, expected {vals}"


async def test_ioset_no_duplicates(dber, store):
    """Test IoSet rejects duplicates."""
    key = b"ioset_nodup"

    await dber.delIoSetVals(store, key)
    await dber.putIoSetVals(store, key, [b"a", b"b"])

    result = await dber.addIoSetVal(store, key, b"a")
    assert result == False, f"addIoSetVal duplicate returned {result}"

    result = await dber.addIoSetVal(store, key, b"c")
    assert result == True, f"addIoSetVal new returned {result}"

    vals = await dber.getIoSetVals(store, key)
    assert vals == [b"a", b"b", b"c"], f"Got {vals}"


async def test_ioset_val_last(dber, store):
    """Test getIoSetValLast returns last inserted value."""
    key = b"ioset_last"

    await dber.delIoSetVals(store, key)

    last = await dber.getIoSetValLast(store, key)
    assert last is None, f"Empty set last returned {last}"

    await dber.putIoSetVals(store, key, [b"first", b"second", b"third"])

    last = await dber.getIoSetValLast(store, key)
    assert last == b"third", f"Got {last!r}"


async def test_ioset_count_delete(dber, store):
    """Test IoSet count and delete operations."""
    key = b"ioset_cnt"

    await dber.delIoSetVals(store, key)

    cnt = await dber.cntIoSetVals(store, key)
    assert cnt == 0, f"Empty count: {cnt}"

    await dber.putIoSetVals(store, key, [b"a", b"b", b"c"])

    cnt = await dber.cntIoSetVals(store, key)
    assert cnt == 3, f"Count: {cnt}"

    result = await dber.delIoSetVal(store, key, b"b")
    assert result == True

    cnt = await dber.cntIoSetVals(store, key)
    assert cnt == 2, f"Count after del: {cnt}"

    vals = await dber.getIoSetVals(store, key)
    assert b"b" not in vals, f"'b' still in {vals}"
    assert b"a" in vals
    assert b"c" in vals


async def test_ioset_set_replaces(dber, store):
    """Test setIoSetVals replaces all values."""
    key = b"ioset_set"

    await dber.delIoSetVals(store, key)
    await dber.putIoSetVals(store, key, [b"old1", b"old2"])

    await dber.setIoSetVals(store, key, [b"new1", b"new2", b"new3"])

    vals = await dber.getIoSetVals(store, key)
    assert vals == [b"new1", b"new2", b"new3"], f"Got {vals}"


# =============================================================================
# IODUP OPERATIONS TESTS
# =============================================================================


async def test_iodup_add_get(dber, store):
    """Test IoDup add/get operations."""
    key = b"iodup_key"

    await dber.delIoDupVals(store, key)

    result = await dber.addIoDupVal(store, key, b"val1")
    assert result == True

    result = await dber.addIoDupVal(store, key, b"val2")
    assert result == True

    # Duplicate should fail
    result = await dber.addIoDupVal(store, key, b"val1")
    assert result == False

    vals = await dber.getIoDupVals(store, key)
    assert len(vals) == 2, f"Got {len(vals)} vals"
    assert b"val1" in vals
    assert b"val2" in vals


async def test_iodup_last(dber, store):
    """Test getIoDupValLast."""
    key = b"iodup_last"

    await dber.delIoDupVals(store, key)

    await dber.addIoDupVal(store, key, b"first")
    await dber.addIoDupVal(store, key, b"second")
    await dber.addIoDupVal(store, key, b"third")

    last = await dber.getIoDupValLast(store, key)
    assert last == b"third", f"Got {last!r}"


async def test_putIoDupVals_single_transaction(dber, store):
    """Verify putIoDupVals uses single transaction (Phase 1a fix)."""
    key = b"iodup_batch"

    await dber.delIoDupVals(store, key)

    vals = [b"alpha", b"beta", b"gamma", b"delta"]
    result = await dber.putIoDupVals(store, key, vals)
    assert result == True

    retrieved = await dber.getIoDupVals(store, key)
    assert len(retrieved) == 4, f"Expected 4, got {len(retrieved)}"
    for v in vals:
        assert v in retrieved, f"{v!r} not in {retrieved}"

    # Adding duplicates should not create more entries
    result = await dber.putIoDupVals(store, key, [b"alpha", b"beta"])
    assert result == False

    retrieved = await dber.getIoDupVals(store, key)
    assert len(retrieved) == 4, f"Dups created: expected 4, got {len(retrieved)}"


async def test_ioDup_proem_ordering(dber, store):
    """Test insertion order is preserved via proem numbering."""
    key = b"iodup_order"

    await dber.delIoDupVals(store, key)

    vals = [b"z", b"m", b"x", b"a"]
    await dber.putIoDupVals(store, key, vals)

    retrieved = await dber.getIoDupVals(store, key)
    assert retrieved == vals, f"Order not preserved: got {retrieved}, expected {vals}"


# =============================================================================
# VALS OPERATIONS TESTS (dupsort=True emulation)
# =============================================================================


async def test_putVals_additive(dber, store):
    """Test putVals adds to existing dups, does NOT replace."""
    key = b"vals_add"

    await dber.delVals(store, key)

    # All-new vals
    result = await dber.putVals(store, key, [b"z", b"m", b"x", b"a"])
    assert result == True

    retrieved = await dber.getVals(store, key)
    # getVals returns sorted lexicographically
    assert retrieved == [b"a", b"m", b"x", b"z"], f"Got {retrieved}"

    # All-duplicate vals
    result = await dber.putVals(store, key, [b"z", b"m"])
    assert result == True  # Always returns True

    retrieved = await dber.getVals(store, key)
    assert retrieved == [b"a", b"m", b"x", b"z"], f"Dups changed: {retrieved}"

    # Mixed new+dup vals
    result = await dber.putVals(store, key, [b"a", b"b", b"z"])
    assert result == True

    retrieved = await dber.getVals(store, key)
    assert retrieved == [b"a", b"b", b"m", b"x", b"z"], f"Mixed: {retrieved}"

    # Empty vals list
    result = await dber.putVals(store, key, [])
    assert result == True

    retrieved = await dber.getVals(store, key)
    assert retrieved == [b"a", b"b", b"m", b"x", b"z"], (
        f"Empty list changed: {retrieved}"
    )


async def test_addVal_dedup(dber, store):
    """Test addVal deduplicates."""
    key = b"vals_dedup"

    await dber.delVals(store, key)

    result = await dber.addVal(store, key, b"hello")
    assert result == True, f"First add returned {result}"

    result = await dber.addVal(store, key, b"hello")
    assert result == False, f"Dup add returned {result}"

    result = await dber.addVal(store, key, b"world")
    assert result == True, f"New add returned {result}"

    vals = await dber.getVals(store, key)
    assert len(vals) == 2, f"Expected 2, got {len(vals)}"


async def test_getVals_sorted(dber, store):
    """Test getVals returns values in lexicographic order."""
    key = b"vals_sorted"

    await dber.delVals(store, key)

    await dber.putVals(store, key, [b"z", b"m", b"x", b"a"])

    vals = await dber.getVals(store, key)
    assert vals == [b"a", b"m", b"x", b"z"], f"Not sorted: {vals}"

    # Empty key returns empty
    vals2 = await dber.getVals(store, b"nonexistent_key")
    assert vals2 == [], f"Nonexistent key returned {vals2}"


async def test_getValLast(dber, store):
    """Test getValLast returns last (largest) dup value."""
    key = b"vals_last"

    await dber.delVals(store, key)

    last = await dber.getValLast(store, key)
    assert last is None, f"Empty getValLast returned {last!r}"

    await dber.putVals(store, key, [b"z", b"m", b"x", b"a"])

    last = await dber.getValLast(store, key)
    assert last == b"z", f"Got {last!r}, expected b'z'"


async def test_getValsIter(dber, store):
    """Test getValsIter iterates dup values for a single key."""
    key = b"vals_iter"

    await dber.delVals(store, key)

    await dber.putVals(store, key, [b"c", b"a", b"b"])

    vals = await dber.getValsIter(store, key)
    assert vals == [b"a", b"b", b"c"], f"Got {vals}"


async def test_cntVals_dup(dber, store):
    """Test cntVals counts duplicates for a key."""
    key = b"vals_cnt"

    await dber.delVals(store, key)

    cnt = await dber.cntVals(store, key)
    assert cnt == 0, f"Empty count: {cnt}"

    await dber.putVals(store, key, [b"a", b"b", b"c", b"d"])

    cnt = await dber.cntVals(store, key)
    assert cnt == 4, f"Count: {cnt}"


async def test_delVals_all(dber, store):
    """Test delVals with no val deletes all dups at key."""
    key = b"vals_delall"

    await dber.delVals(store, key)

    await dber.putVals(store, key, [b"a", b"b", b"c"])

    result = await dber.delVals(store, key)
    assert result == True

    vals = await dber.getVals(store, key)
    assert vals == [], f"After del all: {vals}"

    # Deleting from empty should return False
    result = await dber.delVals(store, key)
    assert result == False


async def test_delVals_single(dber, store):
    """Test delVals with specific val deletes only that dup."""
    key = b"vals_delsingle"

    await dber.delVals(store, key)

    await dber.putVals(store, key, [b"a", b"b", b"c"])

    result = await dber.delVals(store, key, b"b")
    assert result == True

    vals = await dber.getVals(store, key)
    assert b"b" not in vals, f"b still in {vals}"
    assert len(vals) == 2, f"Expected 2, got {len(vals)}"

    # Deleting nonexistent val should return False
    result = await dber.delVals(store, key, b"nonexistent")
    assert result == False


async def test_vals_ordering(dber, store):
    """Verify hex-encoded ordering matches LMDB byte-level dupsort."""
    key = b"vals_order"

    await dber.delVals(store, key)

    # Various byte values that test ordering
    vals_in = [b"\x00", b"\x01", b"\x0a", b"\x0f", b"\x10", b"\xff"]
    await dber.putVals(store, key, vals_in)

    vals_out = await dber.getVals(store, key)
    # hex encoding: 00, 01, 0a, 0f, 10, ff - lexicographic order matches byte order
    assert vals_out == sorted(vals_in), f"Ordering mismatch: {vals_out}"


# =============================================================================
# ONIODUP OPERATIONS TESTS
# =============================================================================


async def test_addOnIoDupVal(dber, store):
    """Test addOnIoDupVal - add IoDup at ordinal."""
    key = b"oniodup_add"

    # Clean up
    await dber.delOnIoDupVals(store, key, 0)

    result = await dber.addOnIoDupVal(store, key, 0, val=b"echo")
    assert result == True

    result = await dber.addOnIoDupVal(store, key, 0, val=b"bravo")
    assert result == True

    # Duplicate should fail
    result = await dber.addOnIoDupVal(store, key, 0, val=b"echo")
    assert result == False


async def test_appendOnIoDupVal(dber, store):
    """Test appendOnIoDupVal - auto-ordinal append."""
    key = b"oniodup_append"

    on0 = await dber.appendOnIoDupVal(store, key, b"first")
    assert on0 == 0, f"First append returned {on0}"

    on1 = await dber.appendOnIoDupVal(store, key, b"second")
    assert on1 == 1, f"Second append returned {on1}"


async def test_delOnIoDupVals(dber, store):
    """Test delOnIoDupVals - delete all IoDups at ordinal."""
    key = b"oniodup_delall"

    await dber.addOnIoDupVal(store, key, 0, val=b"v1")
    await dber.addOnIoDupVal(store, key, 0, val=b"v2")

    result = await dber.delOnIoDupVals(store, key, 0)
    assert result == True

    vals = await dber.getOnIoDupValIter(store, key, 0)
    assert vals == [], f"Expected no vals at on=0 after delete, got {vals!r}"


async def test_delOnIoDupVal(dber, store):
    """Test delOnIoDupVal - delete specific val at ordinal."""
    key = b"oniodup_delone"

    await dber.delOnIoDupVals(store, key, 0)

    await dber.addOnIoDupVal(store, key, 0, val=b"keep")
    await dber.addOnIoDupVal(store, key, 0, val=b"remove")

    result = await dber.delOnIoDupVal(store, key, 0, val=b"remove")
    assert result == True

    result = await dber.delOnIoDupVal(store, key, 0, val=b"nonexistent")
    assert result == False


async def test_getOnIoDupItemIter(dber, store):
    """Test getOnIoDupItemIter - iterate (key, on, val) items."""
    key = b"oniodup_iter"

    # Set up data at multiple ordinals
    await dber.delOnIoDupVals(store, key, 0)
    await dber.delOnIoDupVals(store, key, 1)

    await dber.addOnIoDupVal(store, key, 0, val=b"echo")
    await dber.addOnIoDupVal(store, key, 0, val=b"bravo")
    await dber.addOnIoDupVal(store, key, 1, val=b"sue")
    await dber.addOnIoDupVal(store, key, 1, val=b"bob")

    items = await dber.getOnIoDupItemIter(store, key)
    assert len(items) == 4, f"Expected 4 items, got {len(items)}"

    # Check structure: each item is (key_prefix, ordinal, val)
    for k, on, val in items:
        assert isinstance(k, bytes)
        assert isinstance(on, int)
        assert isinstance(val, bytes)


async def test_getOnIoDupValIter(dber, store):
    """Test getOnIoDupValIter - iterate values only."""
    key = b"oniodup_valiter"

    await dber.delOnIoDupVals(store, key, 0)
    await dber.addOnIoDupVal(store, key, 0, val=b"alpha")
    await dber.addOnIoDupVal(store, key, 0, val=b"beta")

    vals = await dber.getOnIoDupValIter(store, key)
    assert len(vals) >= 2, f"Expected at least 2 vals, got {len(vals)}"
    assert b"alpha" in vals
    assert b"beta" in vals


async def test_getOnIoDupLastItemIter(dber, store):
    """Test getOnIoDupLastItemIter - last IoDup at each ordinal."""
    key = b"oniodup_lastitem"

    await dber.delOnIoDupVals(store, key, 0)
    await dber.delOnIoDupVals(store, key, 1)
    await dber.delOnIoDupVals(store, key, 2)

    # sn=0: echo, bravo -> last is bravo
    await dber.addOnIoDupVal(store, key, 0, val=b"echo")
    await dber.addOnIoDupVal(store, key, 0, val=b"bravo")

    # sn=1: sue, bob, val, zoe -> last is zoe
    await dber.addOnIoDupVal(store, key, 1, val=b"sue")
    await dber.addOnIoDupVal(store, key, 1, val=b"bob")
    await dber.addOnIoDupVal(store, key, 1, val=b"val")
    await dber.addOnIoDupVal(store, key, 1, val=b"zoe")

    # sn=2: fish, bat, snail -> last is snail
    await dber.addOnIoDupVal(store, key, 2, val=b"fish")
    await dber.addOnIoDupVal(store, key, 2, val=b"bat")
    await dber.addOnIoDupVal(store, key, 2, val=b"snail")

    items = await dber.getOnIoDupLastItemIter(store, key)
    assert len(items) == 3, f"Expected 3 last items, got {len(items)}: {items}"

    # Last values at each ordinal
    last_vals = [val for _k, _on, val in items]
    assert last_vals == [b"bravo", b"zoe", b"snail"], f"Last vals: {last_vals}"


async def test_getOnIoDupLastValIter(dber, store):
    """Test getOnIoDupLastValIter - last val at each ordinal."""
    key = b"oniodup_lastval"

    await dber.delOnIoDupVals(store, key, 0)
    await dber.delOnIoDupVals(store, key, 1)

    await dber.addOnIoDupVal(store, key, 0, val=b"first")
    await dber.addOnIoDupVal(store, key, 0, val=b"second")
    await dber.addOnIoDupVal(store, key, 1, val=b"third")

    vals = await dber.getOnIoDupLastValIter(store, key)
    assert len(vals) == 2, f"Expected 2, got {len(vals)}"
    assert vals == [b"second", b"third"], f"Got {vals}"


async def test_getOnIoDupItemBackIter(dber, store):
    """Test backward iteration (matching keripy test_dbing.py lines 825-928)."""
    key = b"oniodup_back"

    # Clean up
    for sn in range(3):
        await dber.delOnIoDupVals(store, key, sn)

    # sn=0: echo, bravo
    await dber.addOnIoDupVal(store, key, 0, val=b"echo")
    await dber.addOnIoDupVal(store, key, 0, val=b"bravo")

    # sn=1: sue, bob, val, zoe
    await dber.addOnIoDupVal(store, key, 1, val=b"sue")
    await dber.addOnIoDupVal(store, key, 1, val=b"bob")
    await dber.addOnIoDupVal(store, key, 1, val=b"val")
    await dber.addOnIoDupVal(store, key, 1, val=b"zoe")

    # sn=2: fish, bat, snail
    await dber.addOnIoDupVal(store, key, 2, val=b"fish")
    await dber.addOnIoDupVal(store, key, 2, val=b"bat")
    await dber.addOnIoDupVal(store, key, 2, val=b"snail")

    # Backward from on=3 (beyond end) should get everything in reverse
    items = await dber.getOnIoDupItemBackIter(store, key, on=3)
    vals = [val for _k, _on, val in items]
    expected = [
        b"snail",
        b"bat",
        b"fish",  # sn=2 reversed
        b"zoe",
        b"val",
        b"bob",
        b"sue",  # sn=1 reversed
        b"bravo",
        b"echo",  # sn=0 reversed
    ]
    assert vals == expected, (
        f"BackIter from on=3:\n  got:      {vals}\n  expected: {expected}"
    )

    # Backward from on=0 should get only sn=0
    items0 = await dber.getOnIoDupItemBackIter(store, key, on=0)
    vals0 = [val for _k, _on, val in items0]
    expected0 = [b"bravo", b"echo"]
    assert vals0 == expected0, f"BackIter from on=0: got {vals0}, expected {expected0}"


async def test_getOnIoDupValBackIter(dber, store):
    """Test backward values only."""
    key = b"oniodup_backval"

    await dber.delOnIoDupVals(store, key, 0)
    await dber.addOnIoDupVal(store, key, 0, val=b"first")
    await dber.addOnIoDupVal(store, key, 0, val=b"second")

    vals = await dber.getOnIoDupValBackIter(store, key, on=0)
    assert len(vals) == 2, f"Expected 2, got {len(vals)}"
    # Should be in reverse insertion order
    assert vals == [b"second", b"first"], f"Got {vals}"


async def test_onIoDup_multi_key_multi_ordinal(dber, store):
    """Comprehensive test with preA/preB, 3 ordinals each."""
    preA = b"preA"
    preB = b"preB"

    # Clean up
    for sn in range(3):
        await dber.delOnIoDupVals(store, preA, sn)
        await dber.delOnIoDupVals(store, preB, sn)

    # preA data
    valsA0 = [b"echo", b"bravo"]
    valsA1 = [b"sue", b"bob", b"val", b"zoe"]
    valsA2 = [b"fish", b"bat", b"snail"]

    for v in valsA0:
        await dber.addOnIoDupVal(store, preA, 0, val=v)
    for v in valsA1:
        await dber.addOnIoDupVal(store, preA, 1, val=v)
    for v in valsA2:
        await dber.addOnIoDupVal(store, preA, 2, val=v)

    # preB data
    valsB0 = [b"gamma", b"beta"]
    valsB1 = [b"mary", b"peter", b"john", b"paul"]
    valsB2 = [b"dog", b"cat", b"bird"]

    for v in valsB0:
        await dber.addOnIoDupVal(store, preB, 0, val=v)
    for v in valsB1:
        await dber.addOnIoDupVal(store, preB, 1, val=v)
    for v in valsB2:
        await dber.addOnIoDupVal(store, preB, 2, val=v)

    # Forward iteration for preA
    itemsA = await dber.getOnIoDupItemIter(store, preA)
    assert len(itemsA) == 9, f"preA items: {len(itemsA)}"

    # Forward iteration for preB
    itemsB = await dber.getOnIoDupItemIter(store, preB)
    assert len(itemsB) == 9, f"preB items: {len(itemsB)}"

    # Last items for preA
    lastA = await dber.getOnIoDupLastItemIter(store, preA)
    last_valsA = [v for _k, _o, v in lastA]
    assert last_valsA == [b"bravo", b"zoe", b"snail"], f"preA last: {last_valsA}"

    # Last items for preB
    lastB = await dber.getOnIoDupLastItemIter(store, preB)
    last_valsB = [v for _k, _o, v in lastB]
    assert last_valsB == [b"beta", b"paul", b"bird"], f"preB last: {last_valsB}"


async def test_onIoDup_dot_in_key(dber, store):
    """Regression: keys containing '.' parse correctly via double-unsuffix."""
    # Key that contains dots - this would break with split('.') but works with rsplit
    key = b"pre.fix.key"

    await dber.delOnIoDupVals(store, key, 0)

    await dber.addOnIoDupVal(store, key, 0, val=b"value1")
    await dber.addOnIoDupVal(store, key, 0, val=b"value2")

    items = await dber.getOnIoDupItemIter(store, key)
    assert len(items) == 2, f"Expected 2 items, got {len(items)}: {items}"

    # Verify the key prefix is correctly parsed
    for k, on, val in items:
        assert k == key, f"Key parsed wrong: got {k!r}, expected {key!r}"
        assert on == 0, f"Ordinal parsed wrong: got {on}"

    vals = [v for _k, _o, v in items]
    assert b"value1" in vals
    assert b"value2" in vals

    # Test backward iteration with dotted key
    back_items = await dber.getOnIoDupItemBackIter(store, key, on=0)
    assert len(back_items) == 2, f"Back iter: expected 2, got {len(back_items)}"
    for k, on, val in back_items:
        assert k == key, f"BackIter key wrong: {k!r}"


async def test_onIoDup_custom_sep(dber, store):
    """OnIoDup operations support non-default separator."""
    key = b"sep_key"
    sep = b"|"

    await dber.delOnIoDupVals(store, key, 0, sep=sep)
    await dber.addOnIoDupVal(store, key, 0, val=b"first", sep=sep)
    await dber.addOnIoDupVal(store, key, 0, val=b"second", sep=sep)

    items = await dber.getOnIoDupItemIter(store, key, sep=sep)
    assert items == [(key, 0, b"first"), (key, 0, b"second")], (
        f"Unexpected items: {items}"
    )

    back_items = await dber.getOnIoDupItemBackIter(store, key, on=0, sep=sep)
    assert back_items[0][2] == b"second", f"Unexpected reverse order: {back_items}"


# =============================================================================
# TOPIODUP TESTS
# =============================================================================


async def test_getTopIoDupItemIter_basic(dber, store):
    """Test getTopIoDupItemIter iterates all IoDup items."""
    keyA = b"topiodup_A"
    keyB = b"topiodup_B"

    await dber.delIoDupVals(store, keyA)
    await dber.delIoDupVals(store, keyB)

    await dber.addIoDupVal(store, keyA, b"v1")
    await dber.addIoDupVal(store, keyA, b"v2")
    await dber.addIoDupVal(store, keyB, b"v3")

    items = await dber.getTopIoDupItemIter(store, b"topiodup_")
    assert len(items) == 3, f"Expected 3, got {len(items)}"


async def test_getTopIoDupItemIter_with_top(dber, store):
    """Test getTopIoDupItemIter with prefix bound."""
    keyA = b"toptestA"
    keyB = b"toptestB"

    await dber.delIoDupVals(store, keyA)
    await dber.delIoDupVals(store, keyB)

    await dber.addIoDupVal(store, keyA, b"va")
    await dber.addIoDupVal(store, keyB, b"vb")

    # Only get keyA items
    items = await dber.getTopIoDupItemIter(store, b"toptestA")
    keys = [k for k, v in items]
    assert all(k == keyA for k in keys), f"Got keys outside prefix: {keys}"


# =============================================================================
# EMPTY KEY HANDLING TESTS
# =============================================================================


async def test_empty_key_val_raises_KeyError(dber, store):
    """Test putVal/setVal/getVal/delVal all raise KeyError for empty key."""
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
            pass  # expected


async def test_empty_key_ioset_ok(dber, store):
    """Test IoSet operations succeed with empty key (matching keripy)."""
    empty_key = b""
    some_value = b"ioset_val"

    # These should NOT raise
    await dber.putIoSetVals(store, empty_key, [some_value])
    vals = await dber.getIoSetVals(store, empty_key)
    assert some_value in vals, f"IoSet empty key: {vals}"
    await dber.delIoSetVals(store, empty_key)


async def test_empty_key_vals_raises_KeyError(dber, store):
    """Test all Vals operations raise KeyError for empty key."""
    empty_key = b""
    some_value = b"foo"

    for name, coro_fn in [
        ("putVals", lambda: dber.putVals(store, empty_key, [some_value])),
        ("addVal", lambda: dber.addVal(store, empty_key, some_value)),
        ("getVals", lambda: dber.getVals(store, empty_key)),
        ("getValLast", lambda: dber.getValLast(store, empty_key)),
        ("getValsIter", lambda: dber.getValsIter(store, empty_key)),
        ("cntVals", lambda: dber.cntVals(store, empty_key)),
        ("delVals", lambda: dber.delVals(store, empty_key)),
    ]:
        try:
            await coro_fn()
            assert False, f"{name} should have raised KeyError for empty key"
        except KeyError:
            pass  # expected


async def test_empty_key_iodup_raises_KeyError(dber, store):
    """Test all IoDup operations raise KeyError for empty key."""
    empty_key = b""
    some_value = b"foo"

    for name, coro_fn in [
        ("putIoDupVals", lambda: dber.putIoDupVals(store, empty_key, [some_value])),
        ("addIoDupVal", lambda: dber.addIoDupVal(store, empty_key, some_value)),
        ("getIoDupVals", lambda: dber.getIoDupVals(store, empty_key)),
        ("getIoDupValsIter", lambda: dber.getIoDupValsIter(store, empty_key)),
        ("getIoDupValLast", lambda: dber.getIoDupValLast(store, empty_key)),
        ("cntIoDupVals", lambda: dber.cntIoDupVals(store, empty_key)),
        ("delIoDupVals", lambda: dber.delIoDupVals(store, empty_key)),
        ("delIoDupVal", lambda: dber.delIoDupVal(store, empty_key, some_value)),
    ]:
        try:
            await coro_fn()
            assert False, f"{name} should have raised KeyError for empty key"
        except KeyError:
            pass  # expected


# =============================================================================
# TEST RUNNER
# =============================================================================


async def run_all_tests():
    """Run all IndexedDB tests."""
    results = AsyncTestResults()

    print("=" * 64)
    print("IndexedDB Backend Tests")
    print("=" * 64)
    print()

    # Run sync helper tests
    print("Helper Functions")
    print("-" * 32)

    for name, func in [
        ("test_suffix", test_suffix),
        ("test_unsuffix", test_unsuffix),
        ("test_suffix_unsuffix_roundtrip", test_suffix_unsuffix_roundtrip),
        ("test_suffix_as_onKey", test_suffix_as_onKey),
        ("test_unsuffix_as_splitOnKey", test_unsuffix_as_splitOnKey),
        ("test_suffix_custom_sep", test_suffix_custom_sep),
    ]:
        try:
            func()
            results.record_pass(name)
        except AssertionError as e:
            results.record_fail(name, str(e))
        except Exception as e:
            results.record_error(name, f"{type(e).__name__}: {e}")

    # Setup database for async tests
    print()
    print("Setting up test database...")

    try:
        try:
            await deleteDatabase("test_indexeddb")
        except:
            pass

        dber = await IndexedDBer.open(
            "test_indexeddb",
            [
                "test_store",
                "ioset_store",
                "iodup_store",
                "ordinal_store",
                "vals_store",
                "oniodup_store",
                "empty_store",
            ],
            version=1,
        )
        print("Database opened successfully")

    except Exception as e:
        print(f"ERROR: Failed to open database: {e}")
        results.record_error("database_setup", str(e))
        results.print_summary()
        return results

    # Run async tests
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
                ("test_indexeddber_db_kw_compat", test_indexeddber_db_kw_compat),
            ],
        ),
        (
            "Ordinal Operations",
            "ordinal_store",
            [
                ("test_append_on_val", test_append_on_val),
                ("test_get_on_item_iter", test_get_on_item_iter),
                ("test_putOnVal_no_overwrite", test_putOnVal_no_overwrite),
                ("test_getOnValIter_vals_only", test_getOnValIter_vals_only),
                ("test_cntOnVals", test_cntOnVals),
                ("test_getOnValIter_empty_key", test_getOnValIter_empty_key),
                ("test_delTopVal_branch", test_delTopVal_branch),
            ],
        ),
        (
            "IoSet Operations",
            "ioset_store",
            [
                ("test_ioset_put_get", test_ioset_put_get),
                ("test_ioset_no_duplicates", test_ioset_no_duplicates),
                ("test_ioset_val_last", test_ioset_val_last),
                ("test_ioset_count_delete", test_ioset_count_delete),
                ("test_ioset_set_replaces", test_ioset_set_replaces),
            ],
        ),
        (
            "IoDup Operations",
            "iodup_store",
            [
                ("test_iodup_add_get", test_iodup_add_get),
                ("test_iodup_last", test_iodup_last),
                (
                    "test_putIoDupVals_single_transaction",
                    test_putIoDupVals_single_transaction,
                ),
                ("test_ioDup_proem_ordering", test_ioDup_proem_ordering),
            ],
        ),
        (
            "Vals Operations (dupsort)",
            "vals_store",
            [
                ("test_putVals_additive", test_putVals_additive),
                ("test_addVal_dedup", test_addVal_dedup),
                ("test_getVals_sorted", test_getVals_sorted),
                ("test_getValLast", test_getValLast),
                ("test_getValsIter", test_getValsIter),
                ("test_cntVals_dup", test_cntVals_dup),
                ("test_delVals_all", test_delVals_all),
                ("test_delVals_single", test_delVals_single),
                ("test_vals_ordering", test_vals_ordering),
            ],
        ),
        (
            "OnIoDup Operations",
            "oniodup_store",
            [
                ("test_addOnIoDupVal", test_addOnIoDupVal),
                ("test_appendOnIoDupVal", test_appendOnIoDupVal),
                ("test_delOnIoDupVals", test_delOnIoDupVals),
                ("test_delOnIoDupVal", test_delOnIoDupVal),
                ("test_getOnIoDupItemIter", test_getOnIoDupItemIter),
                ("test_getOnIoDupValIter", test_getOnIoDupValIter),
                ("test_getOnIoDupLastItemIter", test_getOnIoDupLastItemIter),
                ("test_getOnIoDupLastValIter", test_getOnIoDupLastValIter),
                ("test_getOnIoDupItemBackIter", test_getOnIoDupItemBackIter),
                ("test_getOnIoDupValBackIter", test_getOnIoDupValBackIter),
                (
                    "test_onIoDup_multi_key_multi_ordinal",
                    test_onIoDup_multi_key_multi_ordinal,
                ),
                ("test_onIoDup_dot_in_key", test_onIoDup_dot_in_key),
                ("test_onIoDup_custom_sep", test_onIoDup_custom_sep),
            ],
        ),
        (
            "TopIoDup Operations",
            "iodup_store",
            [
                ("test_getTopIoDupItemIter_basic", test_getTopIoDupItemIter_basic),
                (
                    "test_getTopIoDupItemIter_with_top",
                    test_getTopIoDupItemIter_with_top,
                ),
            ],
        ),
        (
            "Empty Key Handling",
            "empty_store",
            [
                (
                    "test_empty_key_val_raises_KeyError",
                    test_empty_key_val_raises_KeyError,
                ),
                ("test_empty_key_ioset_ok", test_empty_key_ioset_ok),
                (
                    "test_empty_key_vals_raises_KeyError",
                    test_empty_key_vals_raises_KeyError,
                ),
                (
                    "test_empty_key_iodup_raises_KeyError",
                    test_empty_key_iodup_raises_KeyError,
                ),
            ],
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

    # Cleanup
    print()
    print("Cleaning up...")
    try:
        dber.close()
        await deleteDatabase("test_indexeddb")
        print("Cleanup complete")
    except Exception as e:
        print(f"Cleanup warning: {e}")

    # Print summary
    print()
    results.print_summary()

    return results


# Convenience function to run from browser console
def run_indexeddb_tests():
    """Entry point to run all IndexedDB tests. Returns a coroutine."""
    return run_all_tests()


# For direct execution in Pyodide
if __name__ == "__main__":
    import asyncio

    asyncio.ensure_future(run_all_tests())
