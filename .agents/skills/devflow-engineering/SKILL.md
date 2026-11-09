---
name: devflow-engineering
description: Develop, debug, review, and test the DevFlow Flask and SQLite task application, including its REST API and frontend integration. Use for engineering work in the DevFlow project.
---

# DevFlow Engineering

## Inspect before changing

- Read applicable repository instructions, inspect the current project structure, and read the relevant application code and tests before editing.
- Identify the requested behavior, existing conventions, API contracts, and database effects. Inspect existing changes when version control is available; preserve the user's work.
- Keep work within the request. Never modify unrelated files or introduce incidental refactors, formatting changes, or dependencies.
- For inspection or planning requests, return findings without changing files.

## Project context

Verify this snapshot against the current source rather than treating it as permanently accurate:

- `app.py` contains the Flask application, REST routes, SQLite initialization, request-scoped connections, and teardown.
- `devflow.db` is the persistent application database. Do not use it for automated tests or reset it to simplify development.
- The `tasks` table has an auto-incrementing integer `id`, a nonblank trimmed `name`, and a `status` restricted to `pending` or `completed` (default `pending`). Initialization seeds examples only for a new database.
- `GET /api/tasks` lists tasks, `POST /api/tasks` creates them, and `PATCH` and `DELETE /api/tasks/<int:task_id>` update and delete them. Preserve response fields, status codes, validation, and persistence unless changing them is part of the request.
- PATCH permits partial updates and an empty object as a no-op. Invalid ID formats can return Flask's HTML 404; nonexistent integer IDs return a JSON error.
- `/` currently returns a welcome message. Do not replace existing route behavior incidentally when adding a frontend.
- `tests/test_app.py` contains the `test_context` fixture. It redirects SQLite connections to a temporary database, initializes the schema, and restores Flask configuration afterward.
- `oop_practice.py` is a standalone exercise, not an application component.
- The recorded baseline is 39 passing pytest cases. This is historical context, not proof of current results or a fixed limit on test count.
- Frontend development is the next milestone. A Flask-served HTML/CSS/vanilla JavaScript frontend is the proposed lightweight direction; inspect the current implementation and user requirements before choosing or changing architecture.

## Implement focused changes

- Preserve working functionality and follow existing naming, formatting, route, transaction, and test conventions.
- Write maintainable, modular code with clear responsibilities. Prefer small helpers where they remove meaningful duplication; avoid unnecessary frameworks, abstractions, and dependencies.
- Keep database operations parameterized and transactional. Preserve request connection cleanup and committed persistence across later requests.
- For frontend work, separate markup, styling, API requests, and interaction logic as complexity warrants. Prefer same-origin relative API URLs when Flask serves the UI.
- Review security and input validation for the affected behavior: validate JSON shape, field types, nonblank names, permitted statuses, and task IDs on the server. Failed validation must not partially persist changes.
- Render task names as text or escaped template content. Handle unsuccessful HTTP responses, non-JSON errors, and network failures without exposing internal exception details.
- Assess additional protections such as authorization or CSRF when the requested feature introduces authentication, cookies, or other relevant exposure; do not expand an ordinary feature into an unrelated security overhaul.

## Test and verify

- Add meaningful tests for new functionality and regression coverage for bug fixes. Cover success, relevant validation and failure paths, and persistence when data changes.
- Reuse `test_context` and temporary SQLite databases. For persistence assertions, use the fixture's original `sqlite_connect` with the temporary database path to read through a fresh connection; verify subsequent API reads when appropriate.
- Do not weaken existing assertions or remove tests to obtain a passing run.
- After modifications, run the complete suite from the project root using the existing environment:

  ```powershell
  .\venv\Scripts\python.exe -m pytest -q
  ```

- If the environment or command has changed, inspect the available setup and use its equivalent. Report any execution blocker explicitly.
- Never claim tests pass without executing them. Report the command, actual counts and failures, and any checks not performed. Historical results and inspection alone do not establish a passing suite.
- For frontend changes, also check relevant browser interactions, responsive layout, keyboard access, and reload persistence when browser tooling is available. Distinguish browser checks from pytest coverage.
- Review the final changes for scope, unintended API or database changes, and accidental changes to unrelated files.

## Handoff

Explain what changed and why, summarize executed verification and material limitations, and provide one concrete next development step. Do not implement that next step unless it is already within the user's request.
