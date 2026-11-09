export async function getTasks(fetchRequest = globalThis.fetch) {
    let response;
    try {
        response = await fetchRequest('/api/tasks', {
            headers: { Accept: 'application/json' },
            cache: 'no-store',
        });
    } catch {
        throw new Error('Unable to connect. Check your connection and try again.');
    }
    if (!response.ok) {
        throw new Error('Unable to load tasks. Please try again.');
    }

    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error('The server returned an invalid task response. Please try again.');
    }
    const valid = data && data.success === true && Array.isArray(data.tasks)
        && data.tasks.every(task => task && Number.isSafeInteger(task.id) && task.id > 0
            && typeof task.name === 'string' && task.name.trim().length > 0
            && ['pending', 'completed'].includes(task.status));
    if (!valid || new Set(data.tasks.map(task => task.id)).size !== data.tasks.length) {
        throw new Error('The server returned an invalid task response. Please try again.');
    }
    return data.tasks;
}

export async function createTask(name, fetchRequest = globalThis.fetch) {
    const trimmedName = name.trim();
    if (!trimmedName) throw new Error('Enter a task name.');
    let response;
    try {
        response = await fetchRequest('/api/tasks', {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: trimmedName }),
        });
    } catch {
        throw new Error('Could not confirm creation. Check the task list before trying again.');
    }
    if (!response.ok) throw new Error('Unable to create task. Please try again.');
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error('Could not confirm creation. Check the task list before trying again.');
    }
    if (!data || data.success !== true || !data.task
        || !Number.isSafeInteger(data.task.id) || data.task.id <= 0
        || data.task.name !== trimmedName || data.task.status !== 'pending') {
        throw new Error('Could not confirm creation. Check the task list before trying again.');
    }
    return data.task;
}

export async function renameTask(id, name, fetchRequest = globalThis.fetch) {
    const trimmedName = name.trim();
    if (!trimmedName) throw new Error('Enter a task name.');
    let response;
    try {
        response = await fetchRequest(`/api/tasks/${id}`, {
            method: 'PATCH',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: trimmedName }),
        });
    } catch {
        throw new Error('Could not confirm the update. Check the task list before trying again.');
    }
    if (!response.ok) throw new Error('Unable to save task. Please try again.');
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error('Could not confirm the update. Check the task list before trying again.');
    }
    if (!data || data.success !== true || !data.task || data.task.id !== id
        || data.task.name !== trimmedName || !['pending', 'completed'].includes(data.task.status)) {
        throw new Error('Could not confirm the update. Check the task list before trying again.');
    }
    return data.task;
}

export async function setTaskStatus(id, status, fetchRequest = globalThis.fetch) {
    if (!['pending', 'completed'].includes(status)) throw new Error('Invalid task status.');
    let response;
    try {
        response = await fetchRequest(`/api/tasks/${id}`, {
            method: 'PATCH',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({ status }),
        });
    } catch {
        throw new Error('Could not confirm the status update. Reload tasks before trying again.');
    }
    if (!response.ok) throw new Error('Unable to update status. Please try again.');
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error('Could not confirm the status update. Reload tasks before trying again.');
    }
    if (!data || data.success !== true || !data.task || data.task.id !== id
        || data.task.status !== status || typeof data.task.name !== 'string' || !data.task.name.trim()) {
        throw new Error('Could not confirm the status update. Reload tasks before trying again.');
    }
    return data.task;
}

export async function deleteTask(id, fetchRequest = globalThis.fetch) {
    let response;
    try {
        response = await fetchRequest(`/api/tasks/${id}`, {
            method: 'DELETE',
            headers: { Accept: 'application/json' },
        });
    } catch {
        throw new Error('Could not confirm deletion. Reload tasks before trying again.');
    }
    if (response.status === 404) throw new Error('Task no longer exists. Reload tasks to update the list.');
    if (!response.ok) throw new Error('Unable to delete task. Please try again.');
    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error('Could not confirm deletion. Reload tasks before trying again.');
    }
    if (!data || data.success !== true || !data.deleted_task || data.deleted_task.id !== id
        || typeof data.deleted_task.name !== 'string' || !data.deleted_task.name.trim()
        || !['pending', 'completed'].includes(data.deleted_task.status)) {
        throw new Error('Could not confirm deletion. Reload tasks before trying again.');
    }
    return data.deleted_task;
}
