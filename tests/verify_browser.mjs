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
const password = process.env.DEVFLOW_BROWSER_PASSWORD;
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
const pageErrors = [];
const consoleErrors = [];
const browserLogErrors = [];
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
        if (message.method === 'Runtime.exceptionThrown') pageErrors.push(message.params);
        if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error' && !probeStarted) {
            consoleErrors.push(message.params);
        }
        if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error' && !probeStarted) {
            const entry = message.params.entry;
            if (entry.source === 'security') unexpectedSecurityErrors.push(entry);
            else if (!(entry.source === 'network' && /\b401\b/.test(entry.text))) browserLogErrors.push(entry);
        }
    });
    await command('Page.enable');
    await command('Runtime.enable');
    await command('Log.enable');
    await command('Network.enable');
    await command('Page.addScriptToEvaluateOnNewDocument', { source:
        "window.__cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.__cspViolations.push(e.violatedDirective));" });
    await command('Page.navigate', { url: dashboard });
    await waitFor(() => evaluate("Boolean(document.getElementById('password') || document.getElementById('task-list'))"));
    assert.equal(await evaluate("Boolean(document.getElementById('password'))"), true,
        'The disposable browser server must enable the access gate');
    assert(password, 'Set DEVFLOW_BROWSER_PASSWORD for an access-protected local server');
    assert.equal(await evaluate("document.querySelector('input[name=\"_csrf_token\"]')?.value.length > 20"), true);

    const unauthenticated = await evaluate("fetch('/api/tasks', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then(async response => ({ status: response.status, body: await response.json() }))");
    assert.deepEqual(unauthenticated, { status: 401, body: { error: 'Authentication required' } });
    console.log('PASS: unauthenticated browser API access is rejected before login');

    const invalidPassword = `${password}-invalid`;
    await evaluate(`document.getElementById('password').value = ${JSON.stringify(invalidPassword)}; document.querySelector('form').requestSubmit();`);
    await waitFor(() => evaluate("document.getElementById('login-error')?.textContent === 'Invalid password.'"));
    assert.equal(await evaluate("Boolean(document.getElementById('task-list'))"), false);
    console.log('PASS: invalid password is rejected without entering the dashboard');

    await evaluate(`document.getElementById('password').value = ${JSON.stringify(password)}; document.querySelector('form').requestSubmit();`);
    await waitFor(() => evaluate("document.querySelectorAll('.task-row').length === 3 && document.getElementById('total-count').textContent === '3'"));
    assert.equal(await evaluate("document.querySelector('meta[name=\"csrf-token\"]')?.content.length > 20"), true);
    assert.equal(await evaluate("getComputedStyle(document.body).margin"), '0px');
    assert.equal(await evaluate("document.getElementById('task-fields').disabled"), false);
    assert.deepEqual(await evaluate('window.__cspViolations'), []);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(browserLogErrors, []);
    assert.deepEqual(unexpectedSecurityErrors, []);
    console.log('PASS: login, authenticated dashboard, modules, stylesheet, CSRF, and initial counts');

    const createdName = '<img src=x onerror=alert(1)>';
    const updatedName = 'Browser-updated task';
    await evaluate(`document.getElementById('task-name').value = ${JSON.stringify(createdName)}; document.getElementById('task-form').requestSubmit();`);
    await waitFor(() => evaluate("document.getElementById('total-count').textContent === '4' && document.getElementById('create-feedback').textContent === 'Task created successfully.'"));
    assert.equal(await evaluate("document.querySelectorAll('#task-list img, #task-list script').length"), 0);
    assert.equal(await evaluate(`[...document.querySelectorAll('.task-name')].some(node => node.textContent === ${JSON.stringify(createdName)})`), true);

    await evaluate(`{
        const row = [...document.querySelectorAll('.task-row')].find(item => item.querySelector('.task-name')?.textContent === ${JSON.stringify(createdName)});
        [...row.querySelectorAll('button')].find(button => button.textContent === 'Edit').click();
        const input = row.querySelector('.task-editor input');
        input.value = ${JSON.stringify(updatedName)};
        row.querySelector('.task-editor').requestSubmit();
    }`);
    await waitFor(() => evaluate(`[...document.querySelectorAll('.task-name')].some(node => node.textContent === ${JSON.stringify(updatedName)})`));
    assert.equal(await evaluate("document.getElementById('total-count').textContent"), '4');

    await evaluate(`{
        window.confirm = () => true;
        const row = [...document.querySelectorAll('.task-row')].find(item => item.querySelector('.task-name')?.textContent === ${JSON.stringify(updatedName)});
        row.querySelector('.delete-task').click();
    }`);
    await waitFor(() => evaluate(`document.getElementById('total-count').textContent === '3' && ![...document.querySelectorAll('.task-name')].some(node => node.textContent === ${JSON.stringify(updatedName)})`));
    assert.deepEqual(await evaluate('window.__cspViolations'), []);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(browserLogErrors, []);
    console.log('PASS: browser create, update, delete, refresh, and literal XSS rendering');

    await evaluate("document.querySelector('.logout-form').requestSubmit()");
    await waitFor(() => evaluate("Boolean(document.getElementById('password'))"));
    const afterLogout = await evaluate("fetch('/api/tasks', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then(response => response.status)");
    assert.equal(afterLogout, 401);
    console.log('PASS: logout clears access and the API returns 401');

    await evaluate(`document.getElementById('password').value = ${JSON.stringify(password)}; document.querySelector('form').requestSubmit();`);
    await waitFor(() => evaluate("document.querySelectorAll('.task-row').length === 3"));
    const cookies = (await command('Network.getCookies', { urls: [dashboard] })).cookies;
    const sessionCookie = cookies.find(cookie => cookie.name.endsWith('devflow_session'));
    assert(sessionCookie, 'Authenticated session cookie was not created');
    assert.equal(sessionCookie.httpOnly, true);
    assert.equal(sessionCookie.secure, true);
    assert(sessionCookie.expires > Date.now() / 1000);
    await command('Network.deleteCookies', {
        name: sessionCookie.name, domain: sessionCookie.domain, path: sessionCookie.path,
    });
    const expiredStatus = await evaluate("fetch('/api/tasks', { headers: { Accept: 'application/json' }, cache: 'no-store' }).then(response => response.status)");
    assert.equal(expiredStatus, 401);
    await command('Page.navigate', { url: dashboard });
    await waitFor(() => evaluate("Boolean(document.getElementById('password'))"));
    console.log('PASS: expired session cookie fails closed and redirects back to login');

    await evaluate(`document.getElementById('password').value = ${JSON.stringify(password)}; document.querySelector('form').requestSubmit();`);
    await waitFor(() => evaluate("document.querySelectorAll('.task-row').length === 3"));
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(browserLogErrors, []);
    assert.deepEqual(unexpectedSecurityErrors, []);
    assert.deepEqual(await evaluate('window.__cspViolations'), []);

    probeStarted = true;
    await evaluate("const probe = document.createElement('script'); probe.textContent = 'window.__inlineProbeExecuted = true'; document.body.append(probe);");
    await waitFor(() => evaluate('window.__cspViolations.length > 0'));
    assert.equal(await evaluate('window.__inlineProbeExecuted === true'), false);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(consoleErrors, []);
    assert.deepEqual(browserLogErrors, []);
    console.log('PASS: CSP blocks inline script; no unexpected console, page, or security errors');
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
