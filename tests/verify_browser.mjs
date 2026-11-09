// Real Chromium CSP check. Uses an existing browser and Node's built-in CDP client.
// Usage: node tests/verify_browser.mjs <browser-executable> <local-dashboard-url>
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const [browser, dashboard] = process.argv.slice(2);
assert(browser && dashboard, 'Supply an installed Chromium browser and local dashboard URL');
const url = new URL(dashboard);
assert(['localhost', '127.0.0.1'].includes(url.hostname), 'Use a disposable local server only');
const profile = await mkdtemp(join(tmpdir(), 'devflow-browser-'));
const processHandle = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
let socket;
let sequence = 0;
const pending = new Map();
const exceptions = [];
let probeStarted = false;
const unexpectedSecurityErrors = [];

async function waitFor(check) {
    for (let attempt = 0; attempt < 100; attempt++) {
        try { if (await check()) return; } catch { /* Browser/page may still be starting. */ }
        await delay(100);
    }
    throw new Error('Timed out waiting for browser verification');
}

function command(method, params = {}) {
    const id = ++sequence;
    return new Promise((resolveResult, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(Error(`CDP timeout: ${method}`)); }, 10000);
        pending.set(id, { resolveResult, reject, timer });
        socket.send(JSON.stringify({ id, method, params }));
    });
}

async function evaluate(expression) {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert(!result.exceptionDetails, 'Browser evaluation failed');
    return result.result.value;
}

try {
    let port;
    await waitFor(async () => {
        port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]);
        return port > 0;
    });
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolveOpen, reject) => {
        socket.addEventListener('open', resolveOpen, { once: true });
        socket.addEventListener('error', reject, { once: true });
    });
    socket.addEventListener('message', event => {
        const message = JSON.parse(event.data);
        if (message.id && pending.has(message.id)) {
            const item = pending.get(message.id);
            clearTimeout(item.timer);
            pending.delete(message.id);
            if (message.error) item.reject(Error(message.error.message));
            else item.resolveResult(message.result);
        }
        if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params);
        if (message.method === 'Log.entryAdded' && message.params.entry.source === 'security' && !probeStarted) {
            unexpectedSecurityErrors.push(message.params.entry.level);
        }
    });
    await command('Page.enable');
    await command('Runtime.enable');
    await command('Log.enable');
    await command('Page.addScriptToEvaluateOnNewDocument', { source:
        "window.__cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.__cspViolations.push(e.violatedDirective));" });
    await command('Page.navigate', { url: dashboard });
    await waitFor(() => evaluate("document.querySelectorAll('.task-row').length === 3 && document.getElementById('total-count').textContent === '3'"));
    assert.equal(await evaluate("getComputedStyle(document.body).margin"), '0px');
    assert.equal(await evaluate("document.getElementById('task-fields').disabled"), false);
    assert.deepEqual(await evaluate('window.__cspViolations'), []);
    assert.deepEqual(exceptions, []);
    assert.deepEqual(unexpectedSecurityErrors, []);
    console.log('PASS: actual dashboard, module imports, stylesheet, API loading, and counts under CSP');

    await evaluate(`document.getElementById('task-name').value = '<img src=x onerror=alert(1)>'; document.getElementById('task-form').requestSubmit();`);
    await waitFor(() => evaluate("document.getElementById('total-count').textContent === '4' && document.getElementById('create-feedback').textContent === 'Task created successfully.'"));
    assert.equal(await evaluate("document.querySelectorAll('#task-list img, #task-list script').length"), 0);
    assert.equal(await evaluate("[...document.querySelectorAll('.task-name')].some(node => node.textContent === '<img src=x onerror=alert(1)>')"), true);
    assert.deepEqual(await evaluate('window.__cspViolations'), []);
    assert.deepEqual(exceptions, []);
    console.log('PASS: real form submission, same-origin POST/refresh, and literal XSS payload rendering');

    probeStarted = true;
    await evaluate("const probe = document.createElement('script'); probe.textContent = 'window.__inlineProbeExecuted = true'; document.body.append(probe);");
    await waitFor(() => evaluate('window.__cspViolations.length > 0'));
    assert.equal(await evaluate('window.__inlineProbeExecuted === true'), false);
    console.log('PASS: browser actively blocks an inline-script probe; CSP was not weakened');
} finally {
    if (socket?.readyState === WebSocket.OPEN) {
        try { await command('Browser.close'); } catch { /* Closing disconnects CDP. */ }
        socket.close();
    }
    processHandle.kill();
    await delay(500);
    // Restrict recursive cleanup to this freshly created temporary browser profile.
    assert.equal(dirname(resolve(profile)), resolve(tmpdir()));
    assert(basename(profile).startsWith('devflow-browser-'));
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
