"""
test_loaders.py - Functions for loading tests from test modules.

This module contains pure functions that load tests from specific test modules
(liboqs KEM, SIG, STFL) and return them as test queue entries. The actual test
execution is handled by TestRunnerDoer.
"""

from typing import List, Tuple, Any, Callable, Optional

# Type alias for test queue entries
TestEntry = Tuple[str, Optional[Callable[..., Any]], Tuple[Any, ...]]


def load_kem_tests() -> List[TestEntry]:
    """
    Load KEM tests from test_kem module.
    
    Returns:
        List of (name, func, args) tuples ready for TestRunnerDoer.
    """
    tests: List[TestEntry] = []
    
    try:
        import test_kem
        
        # Add section marker
        tests.append(("=== KEM TESTS ===", None, ()))
        
        # Generator tests - expand them
        for item in test_kem.test_correctness():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"kem.{func.__name__}", func, tuple(args)))
        
        for item in test_kem.test_seed_generation():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"kem.{func.__name__}", func, tuple(args)))
        
        for item in test_kem.test_wrong_ciphertext():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"kem.{func.__name__}", func, tuple(args)))
        
        # Simple tests
        tests.append(("kem.test_not_supported", test_kem.test_not_supported, ()))
        tests.append(("kem.test_not_enabled", test_kem.test_not_enabled, ()))
        tests.append(("kem.test_python_attributes", test_kem.test_python_attributes, ()))
        
    except Exception as e:
        # Add error marker so runner can report it
        tests.append((f"ERROR loading test_kem: {e}", None, ()))
    
    return tests


def load_sig_tests() -> List[TestEntry]:
    """
    Load signature tests from test_sig module.
    
    Returns:
        List of (name, func, args) tuples ready for TestRunnerDoer.
    """
    tests: List[TestEntry] = []
    
    try:
        import test_sig
        
        tests.append(("=== SIGNATURE TESTS ===", None, ()))
        
        for item in test_sig.test_correctness():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"sig.{func.__name__}", func, tuple(args)))
        
        for item in test_sig.test_correctness_with_ctx_str():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"sig.{func.__name__}", func, tuple(args)))
        
        for item in test_sig.test_wrong_message():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"sig.{func.__name__}", func, tuple(args)))
        
        for item in test_sig.test_wrong_signature():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"sig.{func.__name__}", func, tuple(args)))
        
        for item in test_sig.test_wrong_public_key():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"sig.{func.__name__}", func, tuple(args)))
        
        tests.append(("sig.test_sig_with_ctx_support_detection", test_sig.test_sig_with_ctx_support_detection, ()))
        tests.append(("sig.test_not_supported", test_sig.test_not_supported, ()))
        tests.append(("sig.test_not_enabled", test_sig.test_not_enabled, ()))
        tests.append(("sig.test_python_attributes", test_sig.test_python_attributes, ()))
        
    except Exception as e:
        tests.append((f"ERROR loading test_sig: {e}", None, ()))
    
    return tests


def load_stfl_sig_tests() -> List[TestEntry]:
    """
    Load stateful signature tests from test_stfl_sig module.
    
    Returns:
        List of (name, func, args) tuples ready for TestRunnerDoer.
    """
    tests: List[TestEntry] = []
    
    try:
        import test_stfl_sig
        
        tests.append(("=== STATEFUL SIGNATURE TESTS ===", None, ()))
        
        for item in test_stfl_sig.test_correctness():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"stfl.{func.__name__}", func, tuple(args)))
        
        for item in test_stfl_sig.test_wrong_message():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"stfl.{func.__name__}", func, tuple(args)))
        
        for item in test_stfl_sig.test_wrong_signature():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"stfl.{func.__name__}", func, tuple(args)))
        
        for item in test_stfl_sig.test_wrong_public_key():
            if isinstance(item, tuple) and len(item) >= 2:
                func, *args = item
                tests.append((f"stfl.{func.__name__}", func, tuple(args)))
        
        tests.append(("stfl.test_not_supported", test_stfl_sig.test_not_supported, ()))
        tests.append(("stfl.test_not_enabled", test_stfl_sig.test_not_enabled, ()))
        tests.append(("stfl.test_python_attributes", test_stfl_sig.test_python_attributes, ()))
        
    except Exception as e:
        tests.append((f"ERROR loading test_stfl_sig: {e}", None, ()))
    
    return tests


def load_all_liboqs_tests() -> List[TestEntry]:
    """
    Load all liboqs tests (KEM, SIG, STFL).
    
    Returns:
        Combined list of all test entries.
    """
    tests: List[TestEntry] = []
    tests.extend(load_kem_tests())
    tests.extend(load_sig_tests())
    tests.extend(load_stfl_sig_tests())
    return tests


def load_blake3_tests() -> List[TestEntry]:
    """
    Load blake3 tests from test_blake3 module.
    
    Returns:
        List of (name, func, args) tuples ready for TestRunnerDoer.
    """
    tests: List[TestEntry] = []
    
    try:
        import test_blake3
        
        tests.append(("=== BLAKE3 TESTS ===", None, ()))
        
        # test_blake3 has an ALL_TESTS list of test functions
        for test_func in test_blake3.ALL_TESTS:
            tests.append((f"blake3.{test_func.__name__}", test_func, ()))
        
    except Exception as e:
        tests.append((f"ERROR loading test_blake3: {e}", None, ()))
    
    return tests


def load_all_blake3_tests() -> List[TestEntry]:
    """
    Load all blake3 tests.
    
    Returns:
        Combined list of all blake3 test entries.
    """
    return load_blake3_tests()
