"""Small, dependency-free protections for the single-worker public demo."""

import math
import threading
import time

from flask import jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge


class ApiRateLimiter:
    """Two fixed windows shared by all visitors, with bounded memory use."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.windows = {}
        self.lock = threading.Lock()

    def allow(self, bucket, limit, seconds):
        with self.lock:
            now = self.clock()
            start, count = self.windows.get(bucket, (now, 0))
            if now - start >= seconds:
                start, count = now, 0
            if count >= limit:
                return False, max(1, math.ceil(seconds - (now - start)))
            self.windows[bucket] = (start, count + 1)
            return True, 0


def configure_security(app):
    app.config.update(
        MAX_CONTENT_LENGTH=16 * 1024,
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_READS=300,
        RATE_LIMIT_WRITES=60,
        RATE_LIMIT_WINDOW=60,
    )
    limiter = ApiRateLimiter()
    app.extensions["devflow_rate_limiter"] = limiter

    @app.before_request
    def protect_request():
        maximum = app.config["MAX_CONTENT_LENGTH"]
        if request.content_length is not None and request.content_length > maximum:
            raise RequestEntityTooLarge()
        if not request.path.startswith("/api/"):
            return None
        if app.config["RATE_LIMIT_ENABLED"] and request.method != "OPTIONS":
            bucket = "reads" if request.method in ("GET", "HEAD") else "writes"
            limit = app.config[f"RATE_LIMIT_{bucket.upper()}"]
            allowed, retry = limiter.allow(bucket, limit, app.config["RATE_LIMIT_WINDOW"])
            if not allowed:
                response = jsonify({"error": "Too many requests. Please try again later."})
                response.status_code = 429
                response.headers["Retry-After"] = str(retry)
                response.headers["Cache-Control"] = "no-store"
                return response
        # Enforce the limit for streamed API bodies and methods that ignore bodies.
        # Read one extra byte to detect over-limit terminated streams; Werkzeug
        # can otherwise stop exactly at the limit without identifying truncation.
        request.max_content_length = maximum + 1
        try:
            if len(request.get_data(cache=True)) > maximum:
                raise RequestEntityTooLarge()
        finally:
            request.max_content_length = maximum
        return None

    @app.errorhandler(413)
    def oversized_request(error):
        return jsonify({"error": "Request body exceeds the 16 KiB limit"}), 413

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
