/**
 * AGENT WORKER - Pyodide (Python WASM) with crypto bridge
 * Communicates with Crypto Worker via MessageChannel
 */

importScripts('https://cdn.jsdelivr.net/pyodide/v0.29.0/full/pyodide.js');

let pyodide = null;
let cryptoPort = null;
const pending = new Map();

self.onmessage = async function(e) {
    const { type, port, message } = e.data;
    
    if (type === 'init') {
        cryptoPort = port;
        cryptoPort.onmessage = handleCryptoResponse;
        await initPyodide();
    } 
    else if (type === 'test') {
        await runCryptoTest(message);
    }
};

async function initPyodide() {
    try {
        self.postMessage({ type: 'log', msg: 'Loading Pyodide...' });
        
        pyodide = await loadPyodide({
            indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.29.0/full/',
            stdout: (text) => self.postMessage({ type: 'log', msg: `[Py stdout] ${text}` }),
            stderr: (text) => self.postMessage({ type: 'log', msg: `[Py stderr] ${text}` })
        });
        
        // Expose crypto bridge to Python
        self.callCrypto = callCrypto;
        
        await pyodide.runPythonAsync(`
import js

async def crypto_roundtrip(message):
    """Full roundtrip: Python -> JS -> Crypto Worker -> JS -> Python"""
    
    # 1. Hash the message
    hash_result = await js.callCrypto('hash', {'message': message})
    hash_hex = hash_result.to_py()['hash']
    
    # 2. Sign the message
    sign_result = await js.callCrypto('sign', {'message': message})
    sign_data = sign_result.to_py()
    
    # 3. Verify the signature
    verify_result = await js.callCrypto('verify', {
        'message': message,
        'signature': sign_data['signature'],
        'publicKey': sign_data['publicKey']
    })
    verified = verify_result.to_py()['valid']
    
    return {
        'message': message,
        'hash': hash_hex,
        'signature': sign_data['signature'],
        'verified': verified
    }

js.crypto_roundtrip = crypto_roundtrip
print("Python crypto bridge ready!")
`);
        
        self.postMessage({ type: 'ready' });
    } catch (err) {
        self.postMessage({ type: 'error', msg: err.message });
    }
}

function callCrypto(op, data) {
    return new Promise((resolve, reject) => {
        const id = crypto.randomUUID();
        pending.set(id, { resolve, reject });
        // Convert Pyodide proxy to JS object if needed
        const plainData = (data && typeof data.toJs === 'function') 
            ? data.toJs({ dict_converter: Object.fromEntries })
            : data;
        cryptoPort.postMessage({ id, op, data: plainData });
    });
}

function handleCryptoResponse(e) {
    const { id, result, error } = e.data;
    const p = pending.get(id);
    if (p) {
        pending.delete(id);
        error ? p.reject(new Error(error)) : p.resolve(result);
    }
}

async function runCryptoTest(message) {
    try {
        pyodide.globals.set('test_message', message);
        
        const result = await pyodide.runPythonAsync(`
crypto_roundtrip(test_message)
`);
        
        self.postMessage({ type: 'result', result: result.toJs({ dict_converter: Object.fromEntries }) });
    } catch (err) {
        self.postMessage({ type: 'error', msg: err.message });
    }
}
