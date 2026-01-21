# Notes on Creating the liboqs-python Wheel for Pyodide

This guide details the steps to build a working Pyodide wheel for [liboqs-python](https://github.com/open-quantum-safe/liboqs-python), the Python 3 bindings for the Open Quantum Safe liboqs C library.

> [!IMPORTANT]
> liboqs-python is a ctypes wrapper similar to pysodium. The same general approach applies: build the native library for WASM, bundle it in the wheel, and patch the Python code for Pyodide compatibility.

---

## Prerequisites

From the pyodide directory, run:
```bash
./run_docker
```

> [!CAUTION]
> Always use the pyodide Docker container! Building emsdk outside the container (especially on M-series Macs) causes incompatibility errors.

---

## Part 1: Setting Up the Build Environment

```bash
# Install pyodide-build
pip install pyodide-build

# Install the matching xbuildenv (adjust version as needed)
pyodide xbuildenv install 0.29.1

# Set up Emscripten SDK
mkdir emsdk
cd emsdk
git clone https://github.com/emscripten-core/emsdk
cd emsdk

PYODIDE_EMSCRIPTEN_VERSION=$(pyodide config get emscripten_version)
./emsdk install ${PYODIDE_EMSCRIPTEN_VERSION}
./emsdk activate ${PYODIDE_EMSCRIPTEN_VERSION}
source emsdk_env.sh
which emcc  # Verify emcc is available
```

---

## Part 2: Building liboqs for WebAssembly

liboqs uses CMake (unlike libsodium which uses autoconf). You need to cross-compile it for WASM.

### Download and Extract liboqs

```bash
cd /src/packages
wget https://github.com/open-quantum-safe/liboqs/archive/refs/tags/0.15.0.tar.gz
tar -xzf 0.15.0.tar.gz
cd liboqs-0.15.0
```

### Configure with Emscripten CMake

```bash
mkdir build && cd build

emcmake cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON \
  -DOQS_BUILD_ONLY_LIB=ON \
  -DOQS_USE_OPENSSL=OFF \
  -DOQS_USE_AES_OPENSSL=OFF \
  -DOQS_USE_SHA2_OPENSSL=OFF \
  -DOQS_USE_SHA3_OPENSSL=OFF \
  -DOQS_DIST_BUILD=OFF \
  -DOQS_ENABLE_SIG_STFL_LMS=ON \
  -DOQS_ENABLE_SIG_STFL_XMSS=ON \
  -DCMAKE_C_FLAGS="-O3 -DOQS_ALLOW_STFL_KEY_AND_SIG_GEN -DOQS_ALLOW_XMSS_KEY_AND_SIG_GEN -DOQS_ALLOW_LMS_KEY_AND_SIG_GEN" \
  -DCMAKE_INSTALL_PREFIX=$(pwd)/install_dir
```

**Flag explanations:**
- `-DBUILD_SHARED_LIBS=ON` - Build shared library (.so)
- `-DOQS_BUILD_ONLY_LIB=ON` - Skip tests and examples
- `-DOQS_USE_OPENSSL=OFF` - Use internal crypto implementations (no OpenSSL dependency)
- `-DOQS_USE_*_OPENSSL=OFF` - Disable individual OpenSSL usages
- `-DOQS_DIST_BUILD=OFF` - Disable architecture-specific optimizations (pure C)
- `-DOQS_ENABLE_SIG_STFL_*=ON` - Enable stateful signatures (XMSS/LMS)
- `-DOQS_ALLOW_STFL_KEY_AND_SIG_GEN` - Required for correct STFL struct layout
- `-DOQS_ALLOW_XMSS_KEY_AND_SIG_GEN` - Required for XMSS key generation and signing
- `-DOQS_ALLOW_LMS_KEY_AND_SIG_GEN` - Required for LMS key generation and signing

### Build and Install

```bash
emmake make -j$(nproc)
emmake make install
```

### Create Side Module .so

The CMake build may create a static library. Convert to a side module:

```bash
emcc -O3 -s SIDE_MODULE=1 -s ALLOW_MEMORY_GROWTH=1 -o liboqs.so \
  -Wl,--whole-archive install_dir/lib/liboqs.a -Wl,--no-whole-archive
```

> [!NOTE]
> `-s ALLOW_MEMORY_GROWTH=1` enables dynamic memory growth, which may help with larger XMSS key allocations in the browser.

---

## Part 3: Preparing liboqs-python

### Clone liboqs-python

```bash
cd /src/packages
git clone --depth=1 https://github.com/open-quantum-safe/liboqs-python
cd liboqs-python
```

### Bundle liboqs.so with the Package

Copy the compiled `liboqs.so` into the `oqs` package directory:

```bash
cp /src/packages/liboqs-0.15.0/build/liboqs.so oqs/
```

### Modify Library Loading in oqs/oqs.py

The key modifications to `building/pyodide/packages/liboqs-python/oqs/oqs.py` are:

1. **Pyodide Detection**:
```python
import sys
_IS_PYODIDE = sys.platform == "emscripten"
```

2. **Bundled Library Loading**:
```python
def _load_liboqs_pyodide() -> ct.CDLL:
    """Load liboqs.so bundled with the package for Pyodide."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    lib_path = os.path.join(pkg_dir, "liboqs.so")
    try:
        return ct.cdll.LoadLibrary(lib_path)
    except OSError as e:
        msg = f"Could not load bundled liboqs.so from {lib_path}: {e}"
        raise RuntimeError(msg) from e
```

3. **Modified `_load_liboqs()`** to use bundled library on Pyodide:
```python
def _load_liboqs() -> ct.CDLL:
    if _IS_PYODIDE:
        return _load_liboqs_pyodide()
    # ... rest of original logic
```

---

## Part 4: Defining ctypes argtypes

A comprehensive `_define_argtypes()` function has been added to define argument types for all liboqs functions. This is called before `OQS_init()` when running on Pyodide.

The function covers:
- **Core functions**: `OQS_init`, `OQS_version`, `OQS_MEM_cleanse`
- **KEM functions**: `OQS_KEM_*` (new, free, keypair, encaps, decaps, etc.)
- **Signature functions**: `OQS_SIG_*` (new, free, keypair, sign, verify, etc.)
- **Stateful Signature functions**: `OQS_SIG_STFL_*` (all operations)

```python
# Define argtypes for Pyodide before any function calls
if _IS_PYODIDE:
    _define_argtypes()

# liboqs initialization
native().OQS_init()
```

---

## Part 5: Random Number Generation

Unlike libsodium, **liboqs RNG works in Pyodide without patching**.

When built with `-DOQS_USE_OPENSSL=OFF`, liboqs uses `OQS_randombytes_system()` which has two fallback implementations that both work in Pyodide:

| Implementation | Condition | Pyodide Support |
|---------------|-----------|-----------------|
| `getentropy()` | `OQS_HAVE_GETENTROPY` defined | ✅ Emscripten maps to `crypto.getRandomValues()` |
| `/dev/urandom` | Fallback | ✅ Pyodide emulates via `crypto.getRandomValues()` |

Both paths ultimately use the browser's cryptographically secure `crypto.getRandomValues()` API.

---

## Part 6: Updating setup.py / pyproject.toml

Ensure `liboqs.so` is included in the wheel. liboqs-python uses hatchling:

Edit `pyproject.toml`:
```toml
[tool.hatch.build.targets.wheel]
packages = ["oqs"]

[tool.hatch.build.targets.wheel.force-include]
"oqs/liboqs.so" = "oqs/liboqs.so"
```

---

## Part 7: Building the Wheel

```bash
pip install hatchling build

cd /src/packages/liboqs-python
python -m build --wheel -n
```

The wheel will be in `dist/`.

---

## Part 8: Testing in Pyodide

1. Copy the wheel to your KeriWasm/static or Wheels directory
2. Configure pyscript.toml to load the wheel
3. Test using the provided test suite in pyodide/packages/liboqs-python/tests

---

## Troubleshooting

### `TypeError: Invalid argument type in ToBigInt operation`
- Missing or incorrect argtypes definition
- Check which function is failing and add proper argtypes

### `RuntimeError: No oqs shared libraries found`
- Library not bundled in wheel or not in search path
- Verify `liboqs.so` is in the `oqs/` directory before building wheel

### `Module.getRandomValue is not a function`
- RNG implementation issue
- Patch liboqs to use `getentropy()` or override in Python

### Memory/Segfault issues
- Check buffer sizes match between Python and C
- Verify ctypes pointer types are correct

---

## Summary

| Step | Description |
|------|-------------|
| 1 | Set up pyodide Docker + emsdk |
| 2 | Build liboqs with emcmake/emmake |
| 3 | Create liboqs.so side module |
| 4 | Clone liboqs-python, bundle liboqs.so |
| 5 | Patch library loading for Pyodide |
| 6 | Define ctypes argtypes |
| 7 | Handle RNG (getentropy or Python fallback) |
| 8 | Update pyproject.toml for bundling |
| 9 | Build wheel and test |

---

## References

- [Pyodide Building Packages](https://pyodide.org/en/stable/development/building-packages.html)
- [liboqs GitHub](https://github.com/open-quantum-safe/liboqs)
- [liboqs-python GitHub](https://github.com/open-quantum-safe/liboqs-python)
- [liboqs CONFIGURE.md](https://github.com/open-quantum-safe/liboqs/blob/main/CONFIGURE.md)
- [pysodium wheel notes](./pysodium_wheel.md) (for similar patterns)

