/**
 * CRYPTO WORKER - libsodium.js for Ed25519 + Blake2b
 * Receives requests from Agent Worker via MessageChannel
 */

importScripts('../js/sodium.js');

let port = null;
let keypair = null;

self.onmessage = async function (e) {
    if (e.data.type === 'init') {
        port = e.data.port;
        port.onmessage = handleRequest;

        await sodium.ready;
        keypair = sodium.crypto_sign_keypair();

        self.postMessage({ type: 'ready' });
        self.postMessage({ type: 'log', msg: 'libsodium initialized' });
    }
};

function handleRequest(e) {
    const { id, op, data } = e.data;

    try {
        let result;
        const msg = typeof data.message === 'string'
            ? sodium.from_string(data.message)
            : new Uint8Array(data.message);

        switch (op) {
            case 'hash':
                const hash = sodium.crypto_generichash(32, msg);
                result = { hash: sodium.to_hex(hash) };
                break;

            case 'sign':
                const sig = sodium.crypto_sign_detached(msg, keypair.privateKey);
                result = {
                    signature: sodium.to_hex(sig),
                    publicKey: sodium.to_hex(keypair.publicKey)
                };
                break;

            case 'verify':
                const valid = sodium.crypto_sign_verify_detached(
                    sodium.from_hex(data.signature),
                    msg,
                    sodium.from_hex(data.publicKey)
                );
                result = { valid };
                break;

            case 'ping':
                result = { pong: true };
                break;

            default:
                throw new Error(`Unknown op: ${op}`);
        }

        port.postMessage({ id, result });
    } catch (err) {
        port.postMessage({ id, error: err.message });
    }
}
