"""Demo internal service behind the ZTNA gateway.

Exposes the surface the attack simulators target: a login form (brute force),
browsable pages (path enumeration) and large downloads (exfiltration).
"""

import os

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="protected-app", docs_url=None, redoc_url=None)

DEMO_USER = os.environ.get("DEMO_USER", "alice")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "correct-horse")
PAGES = {"home", "reports", "wiki", "tickets", "settings"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    links = "".join(f'<li><a href="/pages/{p}">{p}</a></li>' for p in sorted(PAGES))
    return f"<h1>Internal portal</h1><ul>{links}</ul>"


@app.get("/pages/{name}", response_class=HTMLResponse)
def page(name: str) -> str:
    if name not in PAGES:
        raise HTTPException(404)
    return f"<h1>{name}</h1><p>{'lorem ipsum ' * 50}</p>"


ADMIN_SECTIONS = {"users", "audit", "billing"}


@app.get("/admin", response_class=HTMLResponse)
def admin() -> str:
    links = "".join(f'<li><a href="/admin/{s}">{s}</a></li>' for s in sorted(ADMIN_SECTIONS))
    return f"<h1>Admin console</h1><ul>{links}</ul>"


@app.get("/admin/{section}", response_class=HTMLResponse)
def admin_section(section: str) -> str:
    if section not in ADMIN_SECTIONS:
        raise HTTPException(404)
    return f"<h1>admin: {section}</h1>"


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)) -> dict[str, str]:
    if username != DEMO_USER or password != DEMO_PASSWORD:
        raise HTTPException(401, "invalid credentials")
    return {"status": "ok"}


@app.get("/api/items")
def items() -> list[dict[str, int | str]]:
    return [{"id": i, "name": f"item-{i}"} for i in range(20)]


@app.get("/files/{size_kb}")
def download(size_kb: int) -> Response:
    size_kb = max(1, min(size_kb, 50_000))
    return Response(b"\0" * size_kb * 1024, media_type="application/octet-stream")
