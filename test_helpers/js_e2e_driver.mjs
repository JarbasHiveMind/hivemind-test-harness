#!/usr/bin/env node
/**
 * @file js_e2e_driver.mjs
 * @description Node.js E2E test driver for HiveMind JavaScript client.
 *
 * Uses the actual JarbasHiveMind client from HiveMind-js to connect, perform
 * the full V1 handshake (password-based key derivation), send an encrypted
 * utterance, and exit.
 *
 * Usage:
 *   node js_e2e_driver.mjs <hub_url> <name> <key> <password> <utterance>
 *
 * Example:
 *   node js_e2e_driver.mjs ws://127.0.0.1:42987/ js-sat js-key js-password "hello"
 */

import { createRequire } from 'module';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Polyfill globalThis.WebSocket for Node.js (hivemind.js expects browser WebSocket)
const require = createRequire(import.meta.url);
const WebSocket = require('ws');
globalThis.WebSocket = WebSocket;

// Load the actual HiveMind JS client
const hivemindPath = resolve(__dirname, '../../HiveMind-js/static/js/hivemind.js');
const { JarbasHiveMind } = require(hivemindPath);

const args = process.argv.slice(2);
if (args.length < 5) {
    console.error('Usage: js_e2e_driver.mjs <hub_url> <name> <key> <password> <utterance>');
    process.exit(1);
}

const [hubUrl, name, key, password, utterance] = args;
const timeout = 15000;

async function main() {
    const client = new JarbasHiveMind();

    // Parse URL to extract host and port
    const url = new URL(hubUrl);
    const host = url.hostname;
    const port = parseInt(url.port, 10) || 5678;

    console.log(`[*] Connecting to ${host}:${port} as ${name}`);

    // Override onHiveConnected to know when handshake is done
    const connectedPromise = new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Handshake timeout')), timeout);
        client.onHiveConnected = () => {
            clearTimeout(timer);
            console.log('[+] Handshake complete, connected');
            resolve();
        };
        client.onHiveDisconnected = () => {
            clearTimeout(timer);
            reject(new Error('Disconnected during handshake'));
        };
    });

    // Connect (triggers HELLO → HANDSHAKE → key derivation → encrypted HELLO)
    client.connect(host, port, name, key, password);

    // Wait for handshake to complete
    await connectedPromise;

    // Send utterance
    console.log(`[*] Sending utterance: "${utterance}"`);
    await client.sendUtterance(utterance);

    // Brief wait for message to be processed
    await new Promise(r => setTimeout(r, 1000));

    console.log('[+] Test PASSED: Utterance sent successfully');
    client.ws.close();
    process.exit(0);
}

main().catch(err => {
    console.error('[E] Test FAILED:', err.message);
    process.exit(1);
});
