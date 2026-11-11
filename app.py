import psycopg

from flask import Flask, jsonify, redirect, render_template, request, url_for
from access import (authenticated, clear_session, configure_access, csrf_token,
                    establish_session, valid_csrf_token, verify_password)
from database import get_db, init_app
from security import configure_security

app = Flask(__name__)
init_app(app)
configure_access(app)
configure_security(app)


@app.errorhandler(psycopg.Error)
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
    with connection.transaction():
        cursor = connection.execute(
            "INSERT INTO tasks (name, status) VALUES (%s, %s) RETURNING id, name, status",
            (name.strip(), "pending"),
        )
        new_task = cursor.fetchone()

    return jsonify({"success": True, "task": new_task}), 201


# PATCH: Update an existing task
@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def update_task(task_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    connection = get_db()
    with connection.transaction():
        # Lock the row before reading to keep partial updates atomic.
        row = connection.execute(
            "SELECT id, name, status FROM tasks WHERE id = %s FOR UPDATE", (task_id,)
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
            "UPDATE tasks SET name = %s, status = %s WHERE id = %s",
            (task["name"], task["status"], task_id),
        )

    return jsonify({"success": True, "task": task}), 200


# DELETE: Delete an existing task
@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    connection = get_db()
    with connection.transaction():
        row = connection.execute(
            "SELECT id, name, status FROM tasks WHERE id = %s FOR UPDATE", (task_id,)
        ).fetchone()
        if row is None:
            return jsonify({"error": "Task not found"}), 404
        deleted_task = dict(row)
        connection.execute("DELETE FROM tasks WHERE id = %s", (task_id,))

    return jsonify({"success": True, "deleted_task": deleted_task}), 200


@app.route("/")
def home():
    return "Welcome to DevFlow API!"


@app.route("/login", methods=["GET", "POST"])
def login():
    if not app.config["ACCESS_ENABLED"] or authenticated():
        return redirect(url_for("dashboard"))
    error = None
    retry_after = None
    status = 200
    if request.method == "POST":
        if not valid_csrf_token(request.form.get("_csrf_token")):
            error = "The login form expired. Please try again."
            status = 403
        else:
            limiter = app.extensions["devflow_login_limiter"]
            retry_after = limiter.retry_after()
            if retry_after:
                error = "Too many login attempts. Please try again later."
                status = 429
            elif verify_password(request.form.get("password")):
                limiter.reset()
                establish_session()
                return redirect(url_for("dashboard"), code=303)
            else:
                limiter.record_failure()
                error = "Invalid password."
                status = 401
    response = app.make_response((render_template(
        "login.html", csrf_token=csrf_token(), error=error
    ), status))
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


@app.post("/logout")
def logout():
    if app.config["ACCESS_ENABLED"]:
        if not authenticated():
            return redirect(url_for("login"), code=303)
        if not valid_csrf_token(request.form.get("_csrf_token")):
            return jsonify({"error": "Invalid CSRF token"}), 403
        clear_session()
    return redirect(url_for("login"), code=303)


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", csrf_token=csrf_token(),
                           access_enabled=app.config["ACCESS_ENABLED"])


if __name__ == "__main__":
    app.run()
