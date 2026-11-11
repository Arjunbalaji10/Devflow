import { csrfToken, renameTask } from './api.js';

const pendingEdits = new WeakMap();

export function addTaskEditor(document, row, name, task, refresh, fetchRequest) {
    if (!pendingEdits.has(document)) pendingEdits.set(document, new Set());
    const pending = pendingEdits.get(document);
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.id = `edit-task-${task.id}`;
    edit.textContent = 'Edit';
    edit.disabled = pending.has(task.id);
    edit.setAttribute('aria-label', `Edit task: ${task.name}`);
    const form = document.createElement('form');
    form.className = 'task-editor';
    form.hidden = true;
    form.noValidate = true;
    const label = document.createElement('label');
    label.htmlFor = `task-name-${task.id}`;
    label.textContent = 'Task name';
    const input = document.createElement('input');
    input.id = label.htmlFor;
    input.type = 'text';
    input.required = true;
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'primary-button';
    save.textContent = 'Save';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Cancel';
    const feedback = document.createElement('p');
    feedback.id = `edit-feedback-${task.id}`;
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    feedback.setAttribute('aria-atomic', 'true');
    input.setAttribute('aria-describedby', feedback.id);
    form.append(label, input, save, cancel, feedback);
    row.append(edit, form);
    let saving = false;

    edit.addEventListener('click', () => {
        if (pending.has(task.id)) return;
        input.value = task.name;
        input.removeAttribute('aria-invalid');
        feedback.textContent = '';
        form.hidden = false;
        name.hidden = true;
        edit.hidden = true;
        input.focus();
    });
    const close = () => {
        if (saving || pending.has(task.id)) return;
        form.hidden = true;
        name.hidden = false;
        edit.hidden = false;
        edit.focus();
    };
    cancel.addEventListener('click', close);
    input.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(); }
    });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (saving || pending.has(task.id)) return;
        input.removeAttribute('aria-invalid');
        if (!input.value.trim()) {
            feedback.textContent = 'Enter a task name.';
            input.setAttribute('aria-invalid', 'true');
            input.focus();
            return;
        }
        saving = true;
        pending.add(task.id);
        input.disabled = save.disabled = cancel.disabled = true;
        form.setAttribute('aria-busy', 'true');
        feedback.textContent = 'Saving task…';
        try {
            await renameTask(task.id, input.value, fetchRequest, csrfToken(document));
            const refreshed = await refresh();
            document.getElementById('task-feedback').textContent = refreshed
                ? 'Task updated successfully.'
                : 'Task updated, but the list could not refresh. Use Retry loading tasks.';
            const focusTarget = document.getElementById(edit.id)
                || (refreshed ? document.getElementById('task-search') : document.getElementById('retry-tasks'));
            focusTarget.focus();
        } catch (error) {
            feedback.textContent = error.message;
            input.disabled = false;
            input.focus();
        } finally {
            saving = false;
            pending.delete(task.id);
            const currentEdit = document.getElementById(edit.id);
            if (currentEdit) currentEdit.disabled = false;
            input.disabled = save.disabled = cancel.disabled = false;
            form.setAttribute('aria-busy', 'false');
        }
    });
}
