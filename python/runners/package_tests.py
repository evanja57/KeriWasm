"""
Package verification tests for PyScript/Pyodide environment.
Each test exercises a basic function from the imported package.
"""

from core import ui_log

try:
    from pyscript import document
except ImportError:
    document = None


def log(msg: str, css_class: str = "info"):
    """Emit a structured log entry."""
    ui_log.emit(msg, css_class)


def result(name: str, passed: bool, detail: str = ""):
    """Log a test result."""
    status = "PASS" if passed else "FAIL"
    css_class = "success" if passed else "fail"
    detail_str = f" - {detail}" if detail else ""
    log(f"{status}: {name}{detail_str}", css_class)
    return passed


def run_all_tests():
    """Run all package tests and return (passed, failed) counts."""
    passed = 0
    failed = 0

    # --- blake3 ---
    try:
        import blake3

        h = blake3.blake3(b"test").hexdigest()
        if result("blake3", len(h) == 64, f"hash={h[:16]}..."):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("blake3", False, str(e))
        failed += 1

    # --- jsonschema ---
    try:
        import jsonschema

        schema = {"type": "string"}
        jsonschema.validate("hello", schema)
        if result("jsonschema", True, "validate() worked"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("jsonschema", False, str(e))
        failed += 1

    # --- msgpack ---
    try:
        import msgpack

        data = {"key": "value", "num": 42}
        packed = msgpack.packb(data)
        unpacked = msgpack.unpackb(packed)
        if result("msgpack", unpacked == data, "pack/unpack roundtrip"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("msgpack", False, str(e))
        failed += 1

    # --- multidict ---
    try:
        from multidict import MultiDict

        md = MultiDict([("a", 1), ("a", 2)])
        if result("multidict", md.getall("a") == [1, 2], "MultiDict works"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("multidict", False, str(e))
        failed += 1

    # --- pyyaml ---
    try:
        import yaml

        data = yaml.safe_load("key: value\nnum: 42")
        if result("pyyaml", data == {"key": "value", "num": 42}, "safe_load works"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("pyyaml", False, str(e))
        failed += 1

    # --- cryptography (Fernet) ---
    try:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        f = Fernet(key)
        msg = b"secret"
        decrypted = f.decrypt(f.encrypt(msg))
        if result("cryptography", decrypted == msg, "Fernet encrypt/decrypt"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("cryptography", False, str(e))
        failed += 1

    # --- multicommand ---
    try:
        import multicommand

        if result(
            "multicommand", hasattr(multicommand, "create_parser"), "module loaded"
        ):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("multicommand", False, str(e))
        failed += 1

    # --- hjson ---
    try:
        import hjson

        data = hjson.loads("key: value\nnum: 42")
        if result("hjson", data["key"] == "value", "parse hjson"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("hjson", False, str(e))
        failed += 1

    # --- apispec ---
    try:
        from apispec import APISpec

        spec = APISpec(title="Test", version="1.0.0", openapi_version="3.0.0")
        if result(
            "apispec", spec.to_dict()["info"]["title"] == "Test", "APISpec created"
        ):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("apispec", False, str(e))
        failed += 1

    # --- mnemonic ---
    try:
        from mnemonic import Mnemonic

        m = Mnemonic("english")
        words = m.generate(128)
        if result("mnemonic", len(words.split()) == 12, "12-word phrase generated"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("mnemonic", False, str(e))
        failed += 1

    # --- prettytable ---
    try:
        from prettytable import PrettyTable

        t = PrettyTable(["Name", "Age"])
        t.add_row(["Alice", 30])
        output = t.get_string()
        if result("prettytable", "Alice" in output, "table rendered"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("prettytable", False, str(e))
        failed += 1

    # --- http-sfv ---
    try:
        import http_sfv

        item = http_sfv.Item()
        item.parse(b"42")
        if result("http-sfv", item.value == 42, "parse integer"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("http-sfv", False, str(e))
        failed += 1

    # --- semver ---
    try:
        import semver

        v = semver.Version.parse("1.2.3")
        if result("semver", v.major == 1 and v.minor == 2, "parse version"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("semver", False, str(e))
        failed += 1

    # --- qrcode ---
    try:
        import qrcode

        qr = qrcode.QRCode(version=1)
        qr.add_data("test")
        qr.make(fit=True)
        if result("qrcode", qr.data_list is not None, "QR code created"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("qrcode", False, str(e))
        failed += 1

    # --- ordered-set ---
    try:
        from ordered_set import OrderedSet

        s = OrderedSet([3, 1, 2, 1])
        if result("ordered-set", list(s) == [3, 1, 2], "preserves order, dedupes"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("ordered-set", False, str(e))
        failed += 1

    # --- cbor2 ---
    try:
        import cbor2

        data = {"key": "value", "num": 42}
        encoded = cbor2.dumps(data)
        decoded = cbor2.loads(encoded)
        if result("cbor2", decoded == data, "encode/decode roundtrip"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("cbor2", False, str(e))
        failed += 1

    # --- setuptools ---
    try:
        import setuptools

        if result("setuptools", hasattr(setuptools, "setup"), "module loaded"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("setuptools", False, str(e))
        failed += 1

    # --- wheel ---
    try:
        import wheel

        if result("wheel", hasattr(wheel, "__version__"), f"v{wheel.__version__}"):
            passed += 1
        else:
            failed += 1
    except Exception as e:
        result("wheel", False, str(e))
        failed += 1

    return passed, failed


def clear_output():
    """Clear the active output sink."""
    ui_log.clear()


def run_tests(event):
    """Run all package tests (button handler)."""
    clear_output()
    log("Starting package tests...", "info")
    log("-----------------------------")

    passed, failed = run_all_tests()

    log("-----------------------------")
    summary_class = "success" if failed == 0 else "fail"
    log(f"SUMMARY: {passed} passed, {failed} failed", summary_class)


def hash_input(event):
    """Hash the input text using blake3 (button handler)."""
    import blake3

    clear_output()
    if document is None:
        log("No document available for hash_input()", "fail")
        return
    input_el = document.querySelector("#input")
    if input_el is None:
        log("No #input element found for hash_input()", "fail")
        return
    text = input_el.value
    log(f'Input: "{text}"')

    h = blake3.blake3(text.encode("utf-8"))
    hex_digest = h.hexdigest()

    log(f"Blake3: {hex_digest}", "success")


# Intentionally no side effects on import.
