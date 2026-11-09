import test from 'node:test';
import assert from 'node:assert/strict';
import { createTask, deleteTask, getTasks, renameTask, setTaskStatus } from '../static/js/api.js';
import { loadTasks, setupTaskCreation, setupTaskFilters } from '../static/js/app.js';

// Minimal DOM test double: HTML assignment fails so unsafe rendering is caught.
class Element {
    children = [];
    attributes = {};
    textContent = '';
    hidden = false;
    value = '';
    listeners = {};
    addEventListener(type, callback) { this.listeners[type] = callback; }
    focus() { this.focused = true; }
    constructor(tag = 'div') { this.tag = tag; }
    set innerHTML(value) { throw new Error('HTML injection is forbidden'); }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this.textContent = ''; }
}

function makeDocument() {
    const elements = Object.fromEntries([
        'task-list', 'task-feedback', 'retry-tasks', 'total-count', 'pending-count', 'completed-count',
        'task-form', 'task-fields', 'task-name', 'add-task', 'create-feedback',
        'task-filters', 'task-search', 'filter-all', 'filter-pending', 'filter-completed', 'clear-search',
    ].map(id => [id, new Element()]));
    return {
        getElementById: id => elements[id],
        createElement: tag => new Element(tag),
    };
}

const submit = doc => doc.getElementById('task-form').listeners.submit({ preventDefault() {} });
const createdTask = { id: 4, name: 'Build dashboard', status: 'pending' };
const createdReply = async () => ({ ok: true, json: async () => ({ success: true, task: createdTask }) });

test('creation trims input, sends POST JSON, refreshes rows and counts, and announces success', async () => {
    const doc = makeDocument();
    const calls = [];
    setupTaskCreation(doc, async (url, options) => {
        calls.push([url, options]);
        return options.method === 'POST' ? createdReply() : reply([createdTask])();
    });
    assert.equal(doc.getElementById('task-fields').disabled, false);
    doc.getElementById('task-name').value = '  Build dashboard  ';
    await submit(doc);
    assert.equal(calls.length, 2);
    assert.equal(calls[0][0], '/api/tasks');
    assert.equal(calls[0][1].method, 'POST');
    assert.equal(calls[0][1].headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(calls[0][1].body), { name: 'Build dashboard' });
    assert.equal(calls[1][1].method ?? 'GET', 'GET');
    assert.deepEqual(countValues(doc), ['1', '1', '0']);
    assert.equal(doc.getElementById('task-list').children[0].children[0].children[0].textContent, 'Build dashboard');
    assert.equal(doc.getElementById('task-name').value, '');
    assert.equal(doc.getElementById('task-name').focused, true);
    assert.equal(doc.getElementById('create-feedback').textContent, 'Task created successfully.');
});

for (const value of ['', '   ', '\t\n']) {
    test(`rejects blank input ${JSON.stringify(value)} without sending requests`, async () => {
        const doc = makeDocument();
        let calls = 0;
        setupTaskCreation(doc, async () => { calls++; });
        doc.getElementById('task-name').value = value;
        await submit(doc);
        assert.equal(calls, 0);
        assert.equal(doc.getElementById('task-name').attributes['aria-invalid'], 'true');
        assert.equal(doc.getElementById('task-name').focused, true);
        assert.equal(doc.getElementById('create-feedback').textContent, 'Enter a task name.');
        await assert.rejects(createTask(value, async () => { calls++; }), /Enter a task name/);
        assert.equal(calls, 0);
    });
}

test('duplicate submits are ignored throughout POST and subsequent refresh', async () => {
    const doc = makeDocument();
    let resolvePost, resolveGet;
    const post = new Promise(resolve => { resolvePost = resolve; });
    const get = new Promise(resolve => { resolveGet = resolve; });
    let posts = 0;
    let refreshStarted;
    const started = new Promise(resolve => { refreshStarted = resolve; });
    setupTaskCreation(doc, async (_, options) => {
        if (options.method === 'POST') { posts++; return post; }
        refreshStarted();
        return get;
    });
    doc.getElementById('task-name').value = createdTask.name;
    const saving = submit(doc);
    await submit(doc);
    assert.equal(posts, 1);
    assert.equal(doc.getElementById('task-fields').disabled, true);
    assert.equal(doc.getElementById('task-form').attributes['aria-busy'], 'true');
    resolvePost(await createdReply());
    await started;
    await submit(doc);
    assert.equal(posts, 1);
    assert.equal(doc.getElementById('task-fields').disabled, true);
    resolveGet(await reply([createdTask])());
    await saving;
    assert.equal(doc.getElementById('task-fields').disabled, false);
    assert.equal(doc.getElementById('task-form').attributes['aria-busy'], 'false');
});

for (const [name, fetchRequest] of [
    ['HTTP error', async () => ({ ok: false })],
    ['network error', async () => { throw Error('internal details'); }],
    ['invalid JSON', async () => ({ ok: true, json: async () => { throw SyntaxError(); } })],
    ['malformed success', async () => ({ ok: true, json: async () => ({ success: true, task: {} }) })],
]) {
    test(`creation ${name} preserves input and unlocks the form`, async () => {
        const doc = makeDocument();
        let calls = 0;
        setupTaskCreation(doc, (...args) => { calls++; return fetchRequest(...args); });
        doc.getElementById('task-name').value = '  Build dashboard  ';
        await submit(doc);
        assert.equal(calls, 1);
        assert.equal(doc.getElementById('task-name').value, '  Build dashboard  ');
        assert.equal(doc.getElementById('task-fields').disabled, false);
        assert.match(doc.getElementById('create-feedback').textContent, /try|trying again/);
        assert.doesNotMatch(doc.getElementById('create-feedback').textContent, /internal details/);
    });
}

test('successful POST with failed refresh is not reported as a failed creation', async () => {
    const doc = makeDocument();
    setupTaskCreation(doc, async (_, options) => options.method === 'POST' ? createdReply() : { ok: false });
    doc.getElementById('task-name').value = createdTask.name;
    await submit(doc);
    assert.equal(doc.getElementById('task-name').value, '');
    assert.match(doc.getElementById('create-feedback').textContent, /Task created, but/);
    assert.equal(doc.getElementById('retry-tasks').hidden, false);
});
const reply = tasks => async () => ({ ok: true, json: async () => ({ success: true, tasks }) });
const countValues = doc => ['total-count', 'pending-count', 'completed-count']
    .map(id => doc.getElementById(id).textContent);

const sampleTasks = Object.freeze([
    Object.freeze({ id: 1, name: 'Learn Python', status: 'pending' }),
    Object.freeze({ id: 2, name: 'Learn SQL', status: 'completed' }),
    Object.freeze({ id: 3, name: 'Build dashboard', status: 'pending' }),
]);
const visibleNames = doc => {
    const content = doc.getElementById('task-list').children[0];
    return content?.tag === 'ul' ? content.children.map(row => row.children[0].textContent) : [];
};
const filterBy = (doc, status) => doc.getElementById(`filter-${status}`).listeners.click();
const searchFor = (doc, value) => {
    doc.getElementById('task-search').value = value;
    doc.getElementById('task-search').listeners.input();
};

for (const [status, expected] of [
    ['all', ['Learn Python', 'Learn SQL', 'Build dashboard']],
    ['pending', ['Learn Python', 'Build dashboard']],
    ['completed', ['Learn SQL']],
]) {
    test(`${status} filter renders matching tasks without changing all-task counts`, async () => {
        const doc = makeDocument();
        setupTaskFilters(doc);
        let requests = 0;
        await loadTasks(doc, async () => { requests++; return reply(sampleTasks)(); });
        filterBy(doc, status);
        assert.deepEqual(visibleNames(doc), expected);
        assert.deepEqual(countValues(doc), ['3', '2', '1']);
        assert.equal(doc.getElementById(`filter-${status}`).attributes['aria-pressed'], 'true');
        assert.equal(requests, 1);
        filterBy(doc, 'all');
        assert.deepEqual(visibleNames(doc), sampleTasks.map(task => task.name));
    });
}

test('search ignores case and surrounding whitespace and combines with status', async () => {
    const doc = makeDocument();
    setupTaskFilters(doc);
    await loadTasks(doc, reply(sampleTasks));
    searchFor(doc, '  LEARN  ');
    assert.deepEqual(visibleNames(doc), ['Learn Python', 'Learn SQL']);
    filterBy(doc, 'completed');
    assert.deepEqual(visibleNames(doc), ['Learn SQL']);
    filterBy(doc, 'pending');
    assert.deepEqual(visibleNames(doc), ['Learn Python']);
    assert.deepEqual(countValues(doc), ['3', '2', '1']);
    assert.match(doc.getElementById('task-feedback').textContent, /Showing 1 of 3/);
    assert.equal(doc.getElementById('filter-completed').attributes['aria-pressed'], 'false');
});

test('no-results message and clear search preserve the selected status', async () => {
    const doc = makeDocument();
    setupTaskFilters(doc);
    await loadTasks(doc, reply(sampleTasks));
    filterBy(doc, 'completed');
    searchFor(doc, '<script>not a task</script>');
    assert.deepEqual(visibleNames(doc), []);
    assert.match(doc.getElementById('task-list').children[0].textContent, /No matching tasks/);
    assert.deepEqual(countValues(doc), ['3', '2', '1']);
    doc.getElementById('clear-search').listeners.click();
    assert.equal(doc.getElementById('task-search').value, '');
    assert.equal(doc.getElementById('task-search').focused, true);
    assert.deepEqual(visibleNames(doc), ['Learn SQL']);
    assert.equal(doc.getElementById('filter-completed').attributes['aria-pressed'], 'true');
});

test('filter changes during loading apply to the eventual collection', async () => {
    const doc = makeDocument();
    setupTaskFilters(doc);
    let resolve;
    const pending = new Promise(done => { resolve = done; });
    const loading = loadTasks(doc, () => pending);
    filterBy(doc, 'completed');
    searchFor(doc, 'sql');
    resolve(await reply(sampleTasks)());
    await loading;
    assert.deepEqual(visibleNames(doc), ['Learn SQL']);
});

for (const staleFailure of [false, true]) {
    test(`older ${staleFailure ? 'failed' : 'successful'} GET cannot overwrite a newer task collection`, async () => {
        const doc = makeDocument();
        let resolve;
        const pending = new Promise(done => { resolve = done; });
        const oldLoad = loadTasks(doc, () => pending);
        await loadTasks(doc, reply([{ id: 1, name: 'Latest', status: 'completed' }]));
        resolve(staleFailure ? { ok: false } : await reply(sampleTasks)());
        await oldLoad;
        assert.deepEqual(visibleNames(doc), ['Latest']);
        assert.deepEqual(countValues(doc), ['1', '0', '1']);
        assert.equal(doc.getElementById('retry-tasks').hidden, true);
        assert.equal(doc.getElementById('task-feedback').textContent, '1 task loaded.');
    });
}

test('an old GET finishing during a newer GET cannot end its loading state', async () => {
    const doc = makeDocument();
    let resolveOld, resolveNew;
    const oldLoad = loadTasks(doc, () => new Promise(done => { resolveOld = done; }));
    const newLoad = loadTasks(doc, () => new Promise(done => { resolveNew = done; }));
    resolveOld(await reply(sampleTasks)());
    await oldLoad;
    assert.equal(doc.getElementById('task-list').attributes['aria-busy'], 'true');
    assert.deepEqual(countValues(doc), ['—', '—', '—']);
    resolveNew(await reply([])());
    await newLoad;
});

test('filtering during an edit save cannot create a duplicate PATCH', async () => {
    const doc = makeDocument();
    setupTaskFilters(doc);
    let resolve;
    const pending = new Promise(done => { resolve = done; });
    let patches = 0;
    const task = { id: 96, name: 'Original', status: 'pending' };
    await loadTasks(doc, async (_, options) => {
        if (options.method === 'PATCH') { patches++; return pending; }
        return reply([task])();
    });
    const first = doc.getElementById('task-list').children[0].children[0];
    first.children[2].listeners.click();
    first.children[3].children[1].value = 'Renamed';
    const saving = first.children[3].listeners.submit({ preventDefault() {} });
    filterBy(doc, 'pending');
    const second = doc.getElementById('task-list').children[0].children[0];
    const editDisabled = second.children[2].disabled;
    second.children[2].listeners.click();
    second.children[3].children[1].value = 'Duplicate';
    const duplicate = second.children[3].listeners.submit({ preventDefault() {} });
    // Resolve before asserting so a broken implementation cannot leave a pending lock behind.
    const count = patches;
    resolve({ ok: true, json: async () => ({ success: true, task: { ...task, name: 'Renamed' } }) });
    await Promise.all([saving, duplicate]);
    assert.equal(editDisabled, true);
    assert.equal(count, 1);
});

async function filteredCrudContext() {
    const doc = makeDocument();
    let tasks = sampleTasks.map(task => ({ ...task }));
    let nextId = 4;
    const fetchRequest = async (url, options) => {
        if (options.method === 'POST') {
            const task = { id: nextId++, name: JSON.parse(options.body).name, status: 'pending' };
            tasks.push(task);
            return { ok: true, json: async () => ({ success: true, task }) };
        }
        const id = Number(url.split('/').at(-1));
        if (options.method === 'PATCH') {
            const task = tasks.find(task => task.id === id);
            Object.assign(task, JSON.parse(options.body));
            return { ok: true, json: async () => ({ success: true, task }) };
        }
        if (options.method === 'DELETE') {
            const deleted_task = tasks.find(task => task.id === id);
            tasks = tasks.filter(task => task.id !== id);
            return { ok: true, json: async () => ({ success: true, deleted_task }) };
        }
        return reply(tasks.map(task => ({ ...task })))();
    };
    setupTaskFilters(doc);
    setupTaskCreation(doc, fetchRequest);
    await loadTasks(doc, fetchRequest);
    filterBy(doc, 'pending');
    searchFor(doc, 'learn');
    return doc;
}

test('creation refresh retains active search and status and updates global counts', async () => {
    const doc = await filteredCrudContext();
    doc.getElementById('task-name').value = 'Learn JavaScript';
    await submit(doc);
    assert.deepEqual(visibleNames(doc), ['Learn Python', 'Learn JavaScript']);
    assert.deepEqual(countValues(doc), ['4', '3', '1']);
    filterBy(doc, 'completed');
    doc.getElementById('task-name').value = 'Learn CSS';
    await submit(doc);
    assert.deepEqual(visibleNames(doc), ['Learn SQL']);
    assert.deepEqual(countValues(doc), ['5', '4', '1']);
});

test('rename that stops matching removes only the filtered row and keeps counts', async () => {
    const doc = await filteredCrudContext();
    const row = doc.getElementById('task-list').children[0].children[0];
    row.children[2].listeners.click();
    const form = row.children[3];
    form.children[1].value = 'Practice Python';
    await form.listeners.submit({ preventDefault() {} });
    assert.deepEqual(visibleNames(doc), []);
    assert.deepEqual(countValues(doc), ['3', '2', '1']);
    assert.equal(doc.getElementById('task-search').focused, true);
    doc.getElementById('clear-search').listeners.click();
    assert.deepEqual(visibleNames(doc), ['Practice Python', 'Build dashboard']);
});

test('completion moves task out of active pending filter while preserving search', async () => {
    const doc = await filteredCrudContext();
    const row = doc.getElementById('task-list').children[0].children[0];
    const checkbox = row.children.find(child => child.type === 'checkbox');
    checkbox.checked = true;
    await checkbox.listeners.change();
    assert.deepEqual(visibleNames(doc), []);
    assert.deepEqual(countValues(doc), ['3', '1', '2']);
    assert.equal(doc.getElementById('task-search').focused, true);
    filterBy(doc, 'completed');
    assert.deepEqual(visibleNames(doc), ['Learn Python', 'Learn SQL']);
});

test('deletion retains active filters and updates full-collection counts', async () => {
    await withConfirmation(true, async () => {
        const doc = await filteredCrudContext();
        const row = doc.getElementById('task-list').children[0].children[0];
        await row.children.find(child => child.className === 'delete-task').listeners.click();
        assert.deepEqual(visibleNames(doc), []);
        assert.deepEqual(countValues(doc), ['2', '1', '1']);
        filterBy(doc, 'completed');
        assert.deepEqual(visibleNames(doc), ['Learn SQL']);
        searchFor(doc, '');
        filterBy(doc, 'all');
        assert.deepEqual(visibleNames(doc), ['Learn SQL', 'Build dashboard']);
    });
});

async function withConfirmation(confirmed, run) {
    const original = globalThis.confirm;
    const prompts = [];
    globalThis.confirm = message => { prompts.push(message); return confirmed; };
    try { await run(prompts); } finally {
        if (original === undefined) delete globalThis.confirm;
        else globalThis.confirm = original;
    }
}

async function deletionContext(deleteResponse, failRefresh = false) {
    const doc = makeDocument();
    const task = { id: 83, name: '<img src=x onerror=alert(1)>', status: 'completed' };
    const other = { id: 84, name: 'Keep this task', status: 'pending' };
    let deleted = false;
    const calls = [];
    const fetchRequest = async (url, options) => {
        calls.push([url, options]);
        if (options.method === 'DELETE') {
            const response = deleteResponse ? await deleteResponse(task)
                : { ok: true, json: async () => ({ success: true, deleted_task: task }) };
            if (response.ok) deleted = true;
            return response;
        }
        if (deleted && failRefresh) return { ok: false };
        return reply(deleted ? [other] : [task, other])();
    };
    await loadTasks(doc, fetchRequest);
    const row = doc.getElementById('task-list').children[0].children[0];
    const button = row.children.find(child => child.className === 'delete-task');
    return { doc, row, button, calls, fetchRequest, click: () => button.listeners.click() };
}

test('confirmed deletion uses DELETE and refreshes rows, counts and success feedback', async () => {
    await withConfirmation(true, async prompts => {
        const ctx = await deletionContext();
        assert.equal(ctx.button.attributes['aria-label'], 'Delete task: <img src=x onerror=alert(1)>');
        await ctx.click();
        assert.equal(prompts.length, 1);
        assert.match(prompts[0], /cannot be undone/);
        assert.equal(ctx.calls[1][0], '/api/tasks/83');
        assert.equal(ctx.calls[1][1].method, 'DELETE');
        assert.equal(ctx.calls[1][1].body, undefined);
        assert.equal(ctx.calls.length, 3);
        assert.deepEqual(countValues(ctx.doc), ['1', '1', '0']);
        const rows = ctx.doc.getElementById('task-list').children[0].children;
        assert.equal(rows.length, 1);
        assert.equal(rows[0].children[0].textContent, 'Keep this task');
        assert.equal(ctx.doc.getElementById('task-feedback').textContent, 'Task deleted successfully.');
        assert.equal(ctx.doc.getElementById('task-name').focused, true);
    });
});

test('cancelled confirmation leaves task, counts and API untouched', async () => {
    await withConfirmation(false, async prompts => {
        const ctx = await deletionContext();
        const before = countValues(ctx.doc);
        await ctx.click();
        assert.equal(prompts.length, 1);
        assert.equal(ctx.calls.length, 1);
        assert.deepEqual(countValues(ctx.doc), before);
        assert.equal(ctx.doc.getElementById('task-list').children[0].children[0], ctx.row);
        assert.equal(ctx.button.disabled, false);
    });
});

for (const [name, response, message] of [
    ['HTTP failure', async () => ({ ok: false, status: 500 }), /Unable to delete/],
    ['network failure', async () => { throw Error('internal details'); }, /Could not confirm/],
    ['nonexistent task', async () => ({ ok: false, status: 404 }), /no longer exists/],
    ['invalid JSON', async () => ({ ok: true, json: async () => { throw SyntaxError(); } }), /Could not confirm/],
    ['invalid response', async () => ({ ok: true, json: async () => ({ success: true, deleted_task: {} }) }), /Could not confirm/],
]) {
    test(`deletion ${name} keeps the task and restores its button`, async () => {
        await withConfirmation(true, async () => {
            const ctx = await deletionContext(response);
            await ctx.click();
            assert.equal(ctx.calls.length, 2);
            assert.equal(ctx.doc.getElementById('task-list').children[0].children[0], ctx.row);
            assert.equal(ctx.row.children[0].textContent, '<img src=x onerror=alert(1)>');
            assert.deepEqual(ctx.row.children[0].children, []);
            assert.deepEqual(countValues(ctx.doc), ['2', '1', '1']);
            assert.equal(ctx.button.disabled, false);
            assert.equal(ctx.button.focused, true);
            assert.match(ctx.doc.getElementById('task-feedback').textContent, message);
            assert.doesNotMatch(ctx.doc.getElementById('task-feedback').textContent, /internal details/);
        });
    });
}

test('pending deletion preserves the row and blocks duplicates across row refreshes', async () => {
    await withConfirmation(true, async prompts => {
        let resolve;
        const pending = new Promise(done => { resolve = done; });
        const ctx = await deletionContext(() => pending);
        const deleting = ctx.click();
        assert.equal(ctx.button.disabled, true);
        assert.equal(ctx.doc.getElementById('task-list').children[0].children[0], ctx.row);
        await ctx.click();
        await loadTasks(ctx.doc, ctx.fetchRequest);
        const row = ctx.doc.getElementById('task-list').children[0].children[0];
        const newButton = row.children.find(child => child.className === 'delete-task');
        assert.equal(newButton.disabled, true);
        await newButton.listeners.click();
        assert.equal(prompts.length, 1);
        assert.equal(ctx.calls.filter(([, options]) => options.method === 'DELETE').length, 1);
        resolve({ ok: true, json: async () => ({ success: true,
            deleted_task: { id: 83, name: '<img src=x onerror=alert(1)>', status: 'completed' } }) });
        await deleting;
        assert.equal(ctx.button.attributes['aria-busy'], 'false');
        assert.deepEqual(countValues(ctx.doc), ['1', '1', '0']);
    });
});

test('confirmed deletion with failed refresh offers retry without reporting deletion failure', async () => {
    await withConfirmation(true, async () => {
        const ctx = await deletionContext(undefined, true);
        await ctx.click();
        assert.match(ctx.doc.getElementById('task-feedback').textContent, /Task deleted, but/);
        assert.equal(ctx.doc.getElementById('retry-tasks').hidden, false);
        assert.equal(ctx.doc.getElementById('retry-tasks').focused, true);
    });
});

test('deletion rejects a mismatched task ID in the response', async () => {
    await assert.rejects(deleteTask(83, async () => ({ ok: true, json: async () => ({
        success: true, deleted_task: { id: 84, name: 'Other task', status: 'pending' },
    }) })), /Could not confirm deletion/);
});

async function completionContext(status, patchResponse) {
    const doc = makeDocument();
    let task = { id: 42, name: '<img src=x onerror=alert(1)>', status };
    const calls = [];
    const fetchRequest = async (url, options) => {
        calls.push([url, options]);
        if (options.method === 'PATCH') {
            const updated = { ...task, status: JSON.parse(options.body).status };
            const response = patchResponse ? await patchResponse(updated)
                : { ok: true, json: async () => ({ success: true, task: updated }) };
            if (response.ok) task = updated;
            return response;
        }
        return reply([task])();
    };
    await loadTasks(doc, fetchRequest);
    const row = doc.getElementById('task-list').children[0].children[0];
    const checkbox = row.children.find(child => child.type === 'checkbox');
    return { doc, row, checkbox, calls, fetchRequest,
        toggle: () => checkbox.listeners.change() };
}

for (const initial of ['pending', 'completed']) {
    test(`completion toggles ${initial} with status-only PATCH and updates counts`, async () => {
        const ctx = await completionContext(initial);
        const target = initial === 'pending' ? 'completed' : 'pending';
        assert.equal(ctx.checkbox.checked, initial === 'completed');
        assert.match(ctx.checkbox.attributes['aria-label'], /Completed:/);
        ctx.checkbox.checked = target === 'completed';
        await ctx.toggle();
        assert.equal(ctx.calls[1][0], '/api/tasks/42');
        assert.equal(ctx.calls[1][1].method, 'PATCH');
        assert.deepEqual(JSON.parse(ctx.calls[1][1].body), { status: target });
        assert.equal(ctx.calls.length, 3);
        assert.deepEqual(countValues(ctx.doc), target === 'completed' ? ['1', '0', '1'] : ['1', '1', '0']);
        const row = ctx.doc.getElementById('task-list').children[0].children[0];
        assert.equal(row.children[0].textContent, '<img src=x onerror=alert(1)>');
        assert.deepEqual(row.children[0].children, []);
        assert.equal(row.children.find(child => child.type === 'checkbox').checked, target === 'completed');
        assert.equal(ctx.doc.getElementById('task-feedback').textContent, `Task marked ${target}.`);
    });
    for (const [failure, response] of [
        ['HTTP failure', async () => ({ ok: false })],
        ['network failure', async () => { throw Error('internal details'); }],
        ['invalid JSON', async () => ({ ok: true, json: async () => { throw SyntaxError(); } })],
        ['malformed task', async () => ({ ok: true, json: async () => ({ success: true, task: {} }) })],
    ]) {
        test(`${initial} completion ${failure} restores checkbox and leaves counts unchanged`, async () => {
            const ctx = await completionContext(initial, response);
            const before = countValues(ctx.doc);
            ctx.checkbox.checked = initial !== 'completed';
            await ctx.toggle();
            assert.equal(ctx.checkbox.checked, initial === 'completed');
            assert.equal(ctx.checkbox.disabled, false);
            assert.deepEqual(countValues(ctx.doc), before);
            assert.equal(ctx.calls.length, 2);
            assert.match(ctx.doc.getElementById('task-feedback').textContent, /try|trying/);
            assert.doesNotMatch(ctx.doc.getElementById('task-feedback').textContent, /internal details/);
        });
    }
}

test('same-task completion requests remain locked across a row refresh', async () => {
    let resolve;
    const pending = new Promise(done => { resolve = done; });
    const ctx = await completionContext('pending', () => pending);
    ctx.checkbox.checked = true;
    const saving = ctx.toggle();
    assert.equal(ctx.checkbox.disabled, true);
    assert.equal(ctx.checkbox.attributes['aria-busy'], 'true');
    await ctx.toggle();
    await loadTasks(ctx.doc, ctx.fetchRequest);
    const row = ctx.doc.getElementById('task-list').children[0].children[0];
    const newCheckbox = row.children.find(child => child.type === 'checkbox');
    assert.equal(newCheckbox.disabled, true);
    await newCheckbox.listeners.change();
    assert.equal(ctx.calls.filter(([, options]) => options.method === 'PATCH').length, 1);
    resolve({ ok: true, json: async () => ({ success: true,
        task: { id: 42, name: '<img src=x onerror=alert(1)>', status: 'completed' } }) });
    await saving;
    assert.equal(ctx.checkbox.disabled, false);
    assert.deepEqual(countValues(ctx.doc), ['1', '0', '1']);
});

test('completion API rejects invalid statuses without requesting', async () => {
    await assert.rejects(setTaskStatus(42, 'unknown', () => { throw Error('must not request'); }), /Invalid task status/);
});

async function editingContext(fetchPatch = async task => ({ ok: true, json: async () => ({ success: true, task }) })) {
    const doc = makeDocument();
    let task = { id: 1, name: 'Original task', status: 'completed' };
    const calls = [];
    const fetchRequest = async (url, options) => {
        calls.push([url, options]);
        if (options.method === 'PATCH') {
            const updated = { ...task, name: JSON.parse(options.body).name };
            const response = await fetchPatch(updated);
            if (response.ok) task = updated;
            return response;
        }
        return reply([task])();
    };
    await loadTasks(doc, fetchRequest);
    const row = doc.getElementById('task-list').children[0].children[0];
    const [name, status, edit, form] = row.children;
    const [label, input, save, cancel, feedback] = form.children;
    edit.listeners.click();
    return { doc, calls, name, status, edit, form, label, input, save, cancel, feedback,
        submit: () => form.listeners.submit({ preventDefault() {} }) };
}

test('inline editing PATCHes only a trimmed name and preserves completion counts', async () => {
    const ctx = await editingContext();
    assert.equal(ctx.input.value, 'Original task');
    assert.equal(ctx.label.htmlFor, ctx.input.id);
    assert.equal(ctx.input.attributes['aria-describedby'], ctx.feedback.id);
    assert.equal(ctx.form.hidden, false);
    ctx.input.value = '  Renamed <script>alert(1)</script>  ';
    await ctx.submit();
    const patch = ctx.calls.find(([, options]) => options.method === 'PATCH');
    assert.equal(patch[0], '/api/tasks/1');
    assert.deepEqual(JSON.parse(patch[1].body), { name: 'Renamed <script>alert(1)</script>' });
    assert.equal(patch[1].headers['Content-Type'], 'application/json');
    assert.equal(ctx.calls.length, 3);
    assert.deepEqual(countValues(ctx.doc), ['1', '0', '1']);
    const row = ctx.doc.getElementById('task-list').children[0].children[0];
    assert.equal(row.children[0].textContent, 'Renamed <script>alert(1)</script>');
    assert.deepEqual(row.children[0].children, []);
    assert.equal(row.children[1].textContent, 'Completed');
    assert.equal(ctx.doc.getElementById('task-feedback').textContent, 'Task updated successfully.');
});

for (const value of ['', '  ', '\t\n']) {
    test(`editing rejects blank name ${JSON.stringify(value)}`, async () => {
        const ctx = await editingContext();
        ctx.input.value = value;
        await ctx.submit();
        assert.equal(ctx.calls.length, 1);
        assert.equal(ctx.input.attributes['aria-invalid'], 'true');
        assert.equal(ctx.feedback.textContent, 'Enter a task name.');
        assert.equal(ctx.name.textContent, 'Original task');
        await assert.rejects(renameTask(1, value), /Enter a task name/);
    });
}

test('Cancel and Escape restore the original display without requests', async () => {
    const ctx = await editingContext();
    ctx.input.value = 'Discarded edit';
    ctx.cancel.listeners.click();
    assert.equal(ctx.form.hidden, true);
    assert.equal(ctx.name.hidden, false);
    assert.equal(ctx.name.textContent, 'Original task');
    assert.equal(ctx.edit.focused, true);
    ctx.edit.listeners.click();
    assert.equal(ctx.input.value, 'Original task');
    ctx.input.listeners.keydown({ key: 'Escape', preventDefault() {} });
    assert.equal(ctx.form.hidden, true);
    assert.equal(ctx.calls.length, 1);
});

for (const [name, response] of [
    ['HTTP failure', async () => ({ ok: false })],
    ['network failure', async () => { throw Error('internal details'); }],
    ['malformed JSON', async () => ({ ok: true, json: async () => { throw SyntaxError(); } })],
    ['malformed response', async () => ({ ok: true, json: async () => ({ success: true, task: {} }) })],
]) {
    test(`editing ${name} preserves original task and draft`, async () => {
        const ctx = await editingContext(response);
        ctx.input.value = 'Draft';
        await ctx.submit();
        assert.equal(ctx.name.textContent, 'Original task');
        assert.equal(ctx.status.textContent, 'Completed');
        assert.equal(ctx.input.value, 'Draft');
        assert.equal(ctx.form.hidden, false);
        assert.equal(ctx.save.disabled, false);
        assert.equal(ctx.cancel.disabled, false);
        assert.match(ctx.feedback.textContent, /try|trying/);
        assert.doesNotMatch(ctx.feedback.textContent, /internal details/);
        assert.equal(ctx.calls.length, 2);
    });
}

test('duplicate saves and cancellation are blocked during a pending PATCH', async () => {
    let resolve;
    const pending = new Promise(done => { resolve = done; });
    const ctx = await editingContext(() => pending);
    ctx.input.value = 'Renamed';
    const saving = ctx.submit();
    await ctx.submit();
    ctx.cancel.listeners.click();
    assert.equal(ctx.calls.filter(([, options]) => options.method === 'PATCH').length, 1);
    assert.equal(ctx.save.disabled, true);
    assert.equal(ctx.cancel.disabled, true);
    assert.equal(ctx.form.hidden, false);
    resolve({ ok: true, json: async () => ({ success: true,
        task: { id: 1, name: 'Renamed', status: 'completed' } }) });
    await saving;
    assert.equal(ctx.form.attributes['aria-busy'], 'false');
});

test('saved edit with failed refresh reports success and offers a list retry', async () => {
    const doc = makeDocument();
    let reads = 0;
    const original = { id: 1, name: 'Original', status: 'completed' };
    await loadTasks(doc, async (_, options) => {
        if (options.method === 'PATCH') {
            return { ok: true, json: async () => ({ success: true,
                task: { ...original, name: 'Renamed' } }) };
        }
        reads++;
        return reads === 1 ? reply([original])() : { ok: false };
    });
    const row = doc.getElementById('task-list').children[0].children[0];
    row.children[2].listeners.click();
    const form = row.children[3];
    form.children[1].value = 'Renamed';
    await form.listeners.submit({ preventDefault() {} });
    assert.match(doc.getElementById('task-feedback').textContent, /Task updated, but/);
    assert.equal(doc.getElementById('retry-tasks').hidden, false);
    assert.equal(doc.getElementById('retry-tasks').focused, true);
});

test('GET request uses the existing same-origin contract', async () => {
    const tasks = [{ id: 1, name: 'Learn', status: 'pending' }];
    const result = await getTasks(async (url, options) => {
        assert.equal(url, '/api/tasks');
        assert.equal(options.method ?? 'GET', 'GET');
        assert.equal(options.headers.Accept, 'application/json');
        return { ok: true, json: async () => ({ success: true, tasks }) };
    });
    assert.deepEqual(result, tasks);
});

test('loading state is visible while a request is unresolved', async () => {
    const doc = makeDocument();
    let resolve;
    const request = new Promise(done => { resolve = done; });
    const loading = loadTasks(doc, () => request);
    assert.equal(doc.getElementById('task-list').attributes['aria-busy'], 'true');
    assert.match(doc.getElementById('task-feedback').textContent, /Loading/);
    assert.deepEqual(countValues(doc), ['—', '—', '—']);
    assert.equal(doc.getElementById('retry-tasks').hidden, true);
    resolve({ ok: true, json: async () => ({ success: true, tasks: [] }) });
    await loading;
    assert.equal(doc.getElementById('task-list').attributes['aria-busy'], 'false');
});

test('renders names as literal text and computes all summary counts', async () => {
    const doc = makeDocument();
    const malicious = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    await loadTasks(doc, reply([
        { id: 1, name: malicious, status: 'pending' },
        { id: 2, name: 'Finished', status: 'completed' },
        { id: 3, name: 'Next', status: 'pending' },
    ]));
    const rows = doc.getElementById('task-list').children[0];
    assert.equal(rows.tag, 'ul');
    assert.equal(rows.children.length, 3);
    assert.equal(rows.children[0].children[0].textContent, malicious);
    assert.deepEqual(rows.children[0].children[0].children, []);
    assert.equal(rows.children[1].children[1].textContent, 'Completed');
    assert.deepEqual(countValues(doc), ['3', '2', '1']);
    assert.equal(doc.getElementById('total-count').attributes['aria-label'], undefined);
    assert.equal(doc.getElementById('task-feedback').textContent, '3 tasks loaded.');
});

test('empty responses show a genuine empty state and zero counts', async () => {
    const doc = makeDocument();
    await loadTasks(doc, reply([]));
    assert.deepEqual(countValues(doc), ['0', '0', '0']);
    assert.match(doc.getElementById('task-list').children[0].textContent, /No tasks yet/);
    assert.equal(doc.getElementById('retry-tasks').hidden, true);
});

const failures = [
    ['HTTP failure', async () => ({ ok: false, json: async () => { throw Error('must not parse'); } })],
    ['network failure', async () => { throw new Error('private server details'); }],
    ['invalid JSON', async () => ({ ok: true, json: async () => { throw new SyntaxError(); } })],
    ...[null, {}, { success: false, tasks: [] }, { success: true, tasks: {} },
        { success: true, tasks: [null] },
        ...[{ id: '1', name: 'Task', status: 'pending' },
            { id: 1, name: ' ', status: 'pending' },
            { id: 1, name: 123, status: 'pending' },
            { id: 1, name: 'Task', status: 'unknown' }].map(task => ({ success: true, tasks: [task] })),
        { success: true, tasks: [1, 2].map(() => ({ id: 1, name: 'Task', status: 'pending' })) },
    ].map((body, index) => [`malformed body ${index}`, async () => ({ ok: true, json: async () => body })]),
];
for (const [name, fetchRequest] of failures) {
    test(`${name} clears stale rows, resets counts, and offers retry`, async () => {
        const doc = makeDocument();
        await loadTasks(doc, reply([{ id: 1, name: 'Old', status: 'pending' }]));
        await loadTasks(doc, fetchRequest);
        assert.deepEqual(doc.getElementById('task-list').children, []);
        assert.deepEqual(countValues(doc), ['—', '—', '—']);
        assert.equal(doc.getElementById('retry-tasks').hidden, false);
        assert.equal(doc.getElementById('task-list').attributes['aria-busy'], 'false');
        assert.match(doc.getElementById('task-feedback').textContent, /try again/i);
        assert.doesNotMatch(doc.getElementById('task-feedback').textContent, /private server/);
        await loadTasks(doc, reply([]));
        assert.equal(doc.getElementById('retry-tasks').hidden, true);
        assert.deepEqual(countValues(doc), ['0', '0', '0']);
    });
}
