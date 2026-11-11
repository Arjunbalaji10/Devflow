import { csrfToken, deleteTask } from './api.js';

const pendingDeletions = new Set();

export function addTaskDeletion(document, row, task, refresh, fetchRequest,
    confirmDelete = message => globalThis.confirm(message)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.id = `delete-task-${task.id}`;
    button.className = 'delete-task';
    button.textContent = 'Delete';
    button.setAttribute('aria-label', `Delete task: ${task.name}`);
    button.disabled = pendingDeletions.has(task.id);
    row.append(button);

    button.addEventListener('click', async () => {
        if (pendingDeletions.has(task.id)) return;
        // Native confirmation treats the task name as text and supports keyboard use.
        if (!confirmDelete(`Delete task "${task.name}"? This cannot be undone.`)) return;
        pendingDeletions.add(task.id);
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        const feedback = document.getElementById('task-feedback');
        feedback.textContent = 'Deleting task…';
        try {
            await deleteTask(task.id, fetchRequest, csrfToken(document));
            const refreshed = await refresh();
            feedback.textContent = refreshed ? 'Task deleted successfully.'
                : 'Task deleted, but the list could not refresh. Use Retry loading tasks.';
            const target = refreshed ? document.getElementById('task-name')
                : document.getElementById('retry-tasks');
            target.focus();
        } catch (error) {
            feedback.textContent = error.message;
            button.disabled = false;
            const current = document.getElementById(button.id) || button;
            current.disabled = false;
            current.focus();
        } finally {
            pendingDeletions.delete(task.id);
            button.disabled = false;
            button.setAttribute('aria-busy', 'false');
            const current = document.getElementById(button.id);
            if (current) current.disabled = false;
        }
    });
}
