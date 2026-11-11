"""Explicit production settings; start through start_production.py only."""

import os

bind = f"0.0.0.0:{int(os.environ['PORT'])}"
workers = 1
threads = 1
worker_class = "sync"
preload_app = False
reload = False
# Render terminates TLS at its managed ingress and does not publish a stable
# ingress CIDR. Trust its sanitized scheme header only inside Render; keep direct
# and local Gunicorn deployments fail-closed.
forwarded_allow_ips = "*" if os.environ.get("RENDER", "").strip().lower() == "true" else ""
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
accesslog = "-"
errorlog = "-"
