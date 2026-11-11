import { createTask, csrfToken, getTasks } from './api.js';
import { addTaskEditor } from './editing.js';
import { addTaskCompletion } from './completion.js';
import { addTaskDeletion } from './deletion.js';

const dashboards = new WeakMap();

function dashboardState(document) {
    if (!dashboards.has(document)) dashboards.set(document, { tasks: null, filter: 'all', search: '', request: 0 });
    return dashboards.get(document);
}

export function setupTaskFilters(document) {
    const state = dashboardState(document);
    const search = document.getElementById('task-search');
    const buttons = ['all', 'pending', 'completed'].map(value => document.getElementById(`filter-${value}`));
    buttons.forEach((button, index) => button.addEventListener('click', () => {
        state.filter = ['all', 'pending', 'completed'][index];
        buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
        renderTasks(document);
    }));
    search.addEventListener('input', () => {
        state.search = search.value;
        renderTasks(document);
    });
    document.getElementById('clear-search').addEventListener('click', () => {
        search.value = state.search = '';
        renderTasks(document);
        search.focus();
    });
}

function renderTasks(document) {
    const state = dashboardState(document);
    if (state.tasks === null) return;
    const list = document.getElementById('task-list');
    const feedback = document.getElementById('task-feedback');
    const tasks = state.tasks;
    const query = state.search.trim().toLowerCase();
    const visible = tasks.filter(task => (state.filter === 'all' || task.status === state.filter)
        && task.name.toLowerCase().includes(query));
    list.replaceChildren();
    if (!visible.length) {
        const empty = document.createElement('p');
        empty.className = 'workspace-state';
        empty.textContent = tasks.length ? 'No matching tasks. Try another search or status filter.'
            : 'No tasks yet. Your next idea starts here.';
        list.append(empty);
        feedback.textContent = tasks.length ? `No matching tasks out of ${tasks.length}.` : 'No tasks found.';
        return;
    }
    const rows = document.createElement('ul');
    rows.className = 'task-rows';
    for (const task of visible) {
        const row = document.createElement('li');
        row.className = 'task-row';
        const name = document.createElement('span');
        name.className = 'task-name';
        name.textContent = task.name;
        const status = document.createElement('span');
        status.className = `task-status task-status-${task.status}`;
        status.textContent = task.status === 'completed' ? 'Completed' : 'Pending';
        row.append(name, status);
        const refresh = () => loadTasks(document, state.fetchRequest);
        addTaskEditor(document, row, name, task, refresh, state.fetchRequest);
        addTaskCompletion(document, row, task, refresh, state.fetchRequest);
        addTaskDeletion(document, row, task, refresh, state.fetchRequest);
        rows.append(row);
    }
    list.append(rows);
    feedback.textContent = state.filter === 'all' && !query
        ? `${tasks.length} ${tasks.length === 1 ? 'task' : 'tasks'} loaded.`
        : `Showing ${visible.length} of ${tasks.length} tasks.`;
}

export async function loadTasks(document, fetchRequest = globalThis.fetch) {
    const state = dashboardState(document);
    const request = ++state.request;
    state.tasks = null;
    state.fetchRequest = fetchRequest;
    const filters = document.getElementById('task-filters');
    if (filters) filters.disabled = true;
    const list = document.getElementById('task-list');
    const feedback = document.getElementById('task-feedback');
    const retry = document.getElementById('retry-tasks');
    const counts = ['total-count', 'pending-count', 'completed-count']
        .map(id => document.getElementById(id));
    list.setAttribute('aria-busy', 'true');
    list.replaceChildren();
    feedback.textContent = 'Loading tasks…';
    retry.hidden = true;
    counts.forEach(count => {
        count.textContent = '—';
        count.setAttribute('aria-label', 'Not available');
    });

    try {
        const tasks = await getTasks(fetchRequest);
        if (request !== state.request) return false;
        const values = [tasks.length, tasks.filter(task => task.status === 'pending').length,
            tasks.filter(task => task.status === 'completed').length];
        counts.forEach((count, index) => {
            count.textContent = String(values[index]);
            count.removeAttribute('aria-label');
        });
        state.tasks = tasks;
        renderTasks(document);
        if (filters) filters.disabled = false;
        return true;
    } catch (error) {
        if (request !== state.request) return false;
        feedback.textContent = error.message;
        retry.hidden = false;
        return false;
    } finally {
        if (request === state.request) list.setAttribute('aria-busy', 'false');
    }
}

export function setupTaskCreation(document, fetchRequest = globalThis.fetch) {
    const form = document.getElementById('task-form');
    const fields = document.getElementById('task-fields');
    const input = document.getElementById('task-name');
    const button = document.getElementById('add-task');
    const feedback = document.getElementById('create-feedback');
    let submitting = false;
    fields.disabled = false;

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (submitting) return;
        input.removeAttribute('aria-invalid');
        if (!input.value.trim()) {
            feedback.textContent = 'Enter a task name.';
            input.setAttribute('aria-invalid', 'true');
            input.focus();
            return;
        }
        submitting = true;
        fields.disabled = true;
        form.setAttribute('aria-busy', 'true');
        button.textContent = 'Adding…';
        feedback.textContent = 'Adding task…';
        try {
            await createTask(input.value, fetchRequest, csrfToken(document));
            input.value = '';
            const refreshed = await loadTasks(document, fetchRequest);
            feedback.textContent = refreshed ? 'Task created successfully.'
                : 'Task created, but the list could not refresh. Use Retry loading tasks to refresh it.';
        } catch (error) {
            feedback.textContent = error.message;
        } finally {
            submitting = false;
            fields.disabled = false;
            form.setAttribute('aria-busy', 'false');
            button.textContent = 'Add task';
            input.focus();
        }
    });
}

if (typeof document !== 'undefined') {
    setupTaskFilters(document);
    setupTaskCreation(document);
    document.getElementById('retry-tasks').addEventListener('click', () => loadTasks(document));
    loadTasks(document);
}
