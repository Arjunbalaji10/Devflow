import { csrfToken, setTaskStatus } from './api.js';

// Keep the same task locked even if another action refreshes its DOM row.
const pendingUpdates = new Map();

export function addTaskCompletion(document, row, task, refresh, fetchRequest) {
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `complete-task-${task.id}`;
    checkbox.className = 'task-completion';
    checkbox.checked = pendingUpdates.has(task.id)
        ? pendingUpdates.get(task.id) : task.status === 'completed';
    checkbox.disabled = pendingUpdates.has(task.id);
    checkbox.setAttribute('aria-label', `Completed: ${task.name}`);
    row.append(checkbox);

    checkbox.addEventListener('change', async () => {
        if (pendingUpdates.has(task.id)) {
            checkbox.checked = pendingUpdates.get(task.id);
            return;
        }
        const previous = task.status === 'completed';
        const checked = checkbox.checked;
        const status = checked ? 'completed' : 'pending';
        pendingUpdates.set(task.id, checked);
        checkbox.disabled = true;
        checkbox.setAttribute('aria-busy', 'true');
        const feedback = document.getElementById('task-feedback');
        feedback.textContent = 'Updating task status…';
        try {
            await setTaskStatus(task.id, status, fetchRequest, csrfToken(document));
            const refreshed = await refresh();
            feedback.textContent = refreshed ? `Task marked ${status}.`
                : 'Task status updated, but the list could not refresh. Use Retry loading tasks.';
            const target = document.getElementById(checkbox.id)
                || (refreshed ? document.getElementById('task-search') : document.getElementById('retry-tasks'));
            target.disabled = false;
            target.focus();
        } catch (error) {
            checkbox.checked = previous;
            const current = document.getElementById(checkbox.id);
            if (current) current.checked = previous;
            feedback.textContent = error.message;
        } finally {
            pendingUpdates.delete(task.id);
            checkbox.disabled = false;
            checkbox.setAttribute('aria-busy', 'false');
            const current = document.getElementById(checkbox.id);
            if (current) current.disabled = false;
        }
    });
}
