import sqlite3
from pathlib import Path
import os

from flask import Flask, g, jsonify, render_template, request
from security import configure_security

app = Flask(__name__)
app.config["DATABASE"] = Path(
    os.environ.get("DEVFLOW_DATABASE") or Path(__file__).resolve().with_name("devflow.db")
)
configure_security(app)


def init_db():
    """Initialize atomically; seed only when creating the tasks table."""
    path = Path(app.config["DATABASE"])
    connection = sqlite3.connect(path)
    try:
        with connection:
            # Serialize first startup so concurrent imports cannot seed twice.
            connection.execute("BEGIN IMMEDIATE")
            has_tasks = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone() is not None
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed'))
                )
            """)
            if not has_tasks:
                connection.executemany(
                    "INSERT INTO tasks (id, name, status) VALUES (?, ?, ?)",
                    [
                        (1, "Learn Python", "completed"),
                        (2, "Understand APIs", "pending"),
                        (3, "Learn SQL", "pending"),
                    ],
                )
    finally:
        connection.close()


def get_db():
    """Reuse one connection during a request."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.errorhandler(sqlite3.Error)
def database_error(error):
    return jsonify({"error": "Database operation failed"}), 500


@app.errorhandler(500)
def internal_server_error(error):
    original_exception = getattr(error, "original_exception", None)
    if original_exception is not None:
        app.logger.error("Unhandled exception", exc_info=original_exception)
    return jsonify({"error": "Internal server error"}), 500


# GET: Retrieve all tasks
@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    rows = get_db().execute("SELECT id, name, status FROM tasks ORDER BY id").fetchall()
    return jsonify({"success": True, "tasks": [dict(row) for row in rows]}), 200


# POST: Create a new task
@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "Task name is required"}), 400

    connection = get_db()
    with connection:
        cursor = connection.execute(
            "INSERT INTO tasks (name, status) VALUES (?, ?)",
            (name.strip(), "pending"),
        )
        new_task = {"id": cursor.lastrowid, "name": name.strip(), "status": "pending"}

    return jsonify({"success": True, "task": new_task}), 201


# PATCH: Update an existing task
@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def update_task(task_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    connection = get_db()
    with connection:
        # Start the transaction before reading to keep read/update atomic.
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, name, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "Task not found"}), 404
        task = dict(row)

        if "name" in data:
            if not isinstance(data["name"], str) or not data["name"].strip():
                return jsonify({"error": "Invalid task name"}), 400
            task["name"] = data["name"].strip()

        if "status" in data:
            if data["status"] not in ["pending", "completed"]:
                return jsonify({"error": "Invalid status"}), 400
            task["status"] = data["status"]

        connection.execute(
            "UPDATE tasks SET name = ?, status = ? WHERE id = ?",
            (task["name"], task["status"], task_id),
        )

    return jsonify({"success": True, "task": task}), 200


# DELETE: Delete an existing task
@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    connection = get_db()
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, name, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "Task not found"}), 404
        deleted_task = dict(row)
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    return jsonify({"success": True, "deleted_task": deleted_task}), 200


@app.route("/")
def home():
    return "Welcome to DevFlow API!"


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# Initialize for both direct execution and Flask/WSGI imports.
init_db()

if __name__ == "__main__":
    app.run()
