# Notes on creating the pysodium wheel

Clone the pyodide repo:
```
git clone https://github.com/pyodide/pyodide.git
cd pyodide
```

### Inside of pyodide directory
`./run_docker`

#### [setting up the build env](https://pyodide.org/en/stable/development/building-packages.html):
```
pip install pyodide-build
```
If targeting a differerent version of pyodide, just change the version number.

```
pyodide xbuildenv install 0.29.1
```

```
mkdir emsdk
cd emsdk
git clone https://github.com/emscripten-core/emsdk
cd emsdk

PYODIDE_EMSCRIPTEN_VERSION=$(pyodide config get emscripten_version)
./emsdk install ${PYODIDE_EMSCRIPTEN_VERSION}
./emsdk activate ${PYODIDE_EMSCRIPTEN_VERSION}
source emsdk_env.sh
which emcc
```


## Building libsodium
I gave myself a light aneurysm trying to build libsodium using pyodide-build then I remembered I'm not building a python wheel so I can just run the build script manually to make the .so file.

Anyway here are the commands.

```
cd packages
wget https://github.com/jedisct1/libsodium/releases/download/1.0.18-RELEASE/libsodium-1.0.18.tar.gz
tar -xzf libsodium-1.0.18.tar.gz
cd libsodium-1.0.18

emconfigure ./configure \
  --host=wasm32-unknown-emscripten \
  --prefix=$(pwd)/install_dir \
  --disable-ssp \
  --disable-asm \
  --without-pthreads \
  --enable-shared
emmake make -j$(nproc)
emmake make install
emcc -O3 -s SIDE_MODULE=1 -o libsodium.so \
  -Wl,--whole-archive install_dir/lib/libsodium.a -Wl,--no-whole-archive
```
For whatever reason it wasn't building the shared module, so I just forced it to with that last command.

`--host=wasm32-unknown-emscripten` is needed to explicitly target wasm

`--prefix=$(pwd)/install_dir` is needed to install to the correct location

`--disable-ssp` disables stack canaries, as wasm handles memory different than native binaries

`--disable-asm` makes it portable c code, not some specific native asm (i.e. x86, arm, etc.)

`--without-pthreads` disables pthreads, allowing for easier out of the box usage

`--enable-shared` is needed to enable shared libraries (.so)

## Making pysodium compatable

The main issue came from the randombytes implementation in libsodium. When loaded as a side module (as is done with the ctypes.cdll.LoadLibrary), it does not succeed when trying to call the getrandom function. I fixed this by patching the custom libsodium.so that I ship with the pysodium wheel. Essentially, I just used getentropy() to get random bytes. This works and is cryptographically secure.

https://github.com/emscripten-core/emscripten/pull/12240 details how getrandom is implemented in emscripten. It calls `getRandomDevice()` which then calls to [`crypto.getRandomValues()`](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues). This function is a psudeo-random number generator that is cryptographically secure.

The other change I had to make was to create a `_define_argtypes()` function in `__init__.py`. This function is used to define ctypes arguments so that the python types passed to wasm are correctly translated.


## Finally: python3 -m build --wheel -n

I imported pysodium's `test_pysodium.py` into the pyscript/pyodide script and it passes all of the tests.

## Headaches I had:
- At first I tried to install and run this on my computer, but this ended up causes a bunch of versioning errors between packages. I then chose to use the ./run_docker script that pyodide so kindly provides. This then led to the following error:
- My emsdk had been downloaded outside of the docker container on my M4 mac. This causes a slew of incompatability errors that I finally fixed by starting fresh and just running this guide from the top.