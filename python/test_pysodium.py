import asyncio
import datetime
import sys

import js  # type: ignore
from pyscript import document


def log(msg: str, css_class: str = "info"):
    """Append a message to the output div."""
    output = document.querySelector("#output")
    time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    output.innerHTML += f'<span class="{css_class}">[{time}] {msg}</span>\n'


def clear_output():
    """Clear the output div."""
    document.querySelector("#output").innerHTML = ""


class OutputWriter:
    """Write print() output into the results panel."""

    def __init__(self, css_class: str = "info"):
        self.css_class = css_class
        self._buffer = ""

    def write(self, text: str):
        if not text:
            return
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                log(line, self.css_class)

    def flush(self):
        if self._buffer:
            log(self._buffer, self.css_class)
            self._buffer = ""


def _run_suite():
    log("----------------------------------------------------------------")
    log("Starting Pysodium Shim Verification")
    log("----------------------------------------------------------------")

    try:
        import pysodium
        log("SUCCESS: Imported pysodium", "success")
    except ImportError as exc:
        log(f"FAIL: Could not import pysodium: {exc}", "fail")
        raise

    def assert_eq(a, b, name):
        if a == b:
            log(f"PASS: {name}", "success")
        else:
            log(f"FAIL: {name} | Expected {b}, got {a}", "fail")

    def assert_len(a, length, name):
        if len(a) == length:
            log(f"PASS: {name} (len={length})", "success")
        else:
            log(f"FAIL: {name} | Expected len {length}, got {len(a)}", "fail")

    # 1. Constants used in keripy
    assert_eq(pysodium.crypto_sign_SEEDBYTES, 32, "crypto_sign_SEEDBYTES")
    assert_eq(pysodium.crypto_pwhash_SALTBYTES, 16, "crypto_pwhash_SALTBYTES")
    assert_eq(pysodium.crypto_pwhash_ALG_ARGON2ID13, 2, "crypto_pwhash_ALG_ARGON2ID13")
    assert_eq(pysodium.crypto_pwhash_OPSLIMIT_MIN, 1, "crypto_pwhash_OPSLIMIT_MIN")
    assert_eq(pysodium.crypto_pwhash_OPSLIMIT_INTERACTIVE, 2, "crypto_pwhash_OPSLIMIT_INTERACTIVE")
    assert_eq(pysodium.crypto_pwhash_OPSLIMIT_MODERATE, 3, "crypto_pwhash_OPSLIMIT_MODERATE")
    assert_eq(pysodium.crypto_pwhash_OPSLIMIT_SENSITIVE, 4, "crypto_pwhash_OPSLIMIT_SENSITIVE")
    assert_eq(pysodium.crypto_pwhash_MEMLIMIT_MIN, 8192, "crypto_pwhash_MEMLIMIT_MIN")
    assert_eq(pysodium.crypto_pwhash_MEMLIMIT_INTERACTIVE, 67108864, "crypto_pwhash_MEMLIMIT_INTERACTIVE")
    assert_eq(pysodium.crypto_pwhash_MEMLIMIT_MODERATE, 268435456, "crypto_pwhash_MEMLIMIT_MODERATE")
    assert_eq(pysodium.crypto_pwhash_MEMLIMIT_SENSITIVE, 1073741824, "crypto_pwhash_MEMLIMIT_SENSITIVE")

    # 2. Random bytes
    rnd = pysodium.randombytes(32)
    assert_len(rnd, 32, "randombytes(32)")
    log(f"Random: {rnd.hex()[:16]}...")

    # 3. Sign Keypair
    vk, sk = pysodium.crypto_sign_seed_keypair(rnd)
    assert_len(vk, 32, "Verify Key length")
    assert_len(sk, 64, "Sign Key length")

    # 4. Sign and Verify
    msg = b"Hello KeriWasm!"
    sig = pysodium.crypto_sign_detached(msg, sk)
    assert_len(sig, 64, "Signature length")

    # DEBUG: show the actual data being passed
    log(f"DEBUG: sig len={len(sig)}, msg len={len(msg)}, vk len={len(vk)}")
    log(f"DEBUG: sig[:8]={sig[:8].hex()}, vk[:8]={vk[:8].hex()}")
    
    # pysodium.crypto_sign_verify_detached returns None on success, raises ValueError on failure
    try:
        pysodium.crypto_sign_verify_detached(sig, msg, vk)
        log("PASS: Verify valid signature", "success")
    except ValueError as e:
        log(f"FAIL: Verify valid signature raised ValueError", "fail")

    # Tamper - should raise ValueError
    try:
        pysodium.crypto_sign_verify_detached(sig, b"Tampered Message", vk)
        log("FAIL: Verify tampered message should have raised ValueError", "fail")
    except ValueError:
        log("PASS: Verify tampered message fails (ValueError)", "success")

    # 5. Box / Sealed Box
    # Convert Sign Keys to Box Keys
    box_pk = pysodium.crypto_sign_pk_to_box_pk(vk)
    box_sk = pysodium.crypto_sign_sk_to_box_sk(sk)
    assert_len(box_pk, 32, "Box Public Key length")
    assert_len(box_sk, 32, "Box Secret Key length")

    # Encrypt
    ciphertext = pysodium.crypto_box_seal(msg, box_pk)
    # Sealed box overhead is 48 bytes (Nonce + Poly1305 MAC)
    assert_len(ciphertext, len(msg) + 48, "Sealed box length")

    # Decrypt
    plaintext = pysodium.crypto_box_seal_open(ciphertext, box_pk, box_sk)
    assert_eq(plaintext, msg, "Sealed Box Roundtrip")

    # 6. Curve25519 base (used for key derivation)
    base_pk = pysodium.crypto_scalarmult_curve25519_base(box_sk)
    assert_len(base_pk, 32, "crypto_scalarmult_curve25519_base length")

    # 7. Password Hashing (Argon2)
    log("Testing crypto_pwhash (Argon2)...")
    salt = pysodium.randombytes(pysodium.crypto_pwhash_SALTBYTES)
    pwd_hash = pysodium.crypto_pwhash(
        32,
        b"password",
        salt,
        pysodium.crypto_pwhash_OPSLIMIT_MIN,
        pysodium.crypto_pwhash_MEMLIMIT_MIN,
        pysodium.crypto_pwhash_ALG_ARGON2ID13,
    )
    assert_len(pwd_hash, 32, "pwhash output length")
    log(f"Argon2 Hash: {pwd_hash.hex()}")

    log("----------------------------------------------------------------")
    log("ALL TESTS COMPLETED")
    log("----------------------------------------------------------------")


async def _run_pysodium_tests():
    clear_output()
    # await js.sodium.ready
    log("Starting pysodium tests...", "info")

    stdout = sys.stdout
    stderr = sys.stderr
    sys.stdout = OutputWriter("info")
    sys.stderr = OutputWriter("fail")

    try:
        _run_suite()
    except Exception as exc:
        log(f"Pysodium tests failed: {exc}", "fail")
        raise
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = stdout
        sys.stderr = stderr


def run_pysodium_tests(event):
    asyncio.ensure_future(_run_pysodium_tests())
