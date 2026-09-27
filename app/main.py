"""NEXUS — FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, get_settings
from app.database import init_db
from app.routes import art, entries, projects, search, system, uploads
from app.services.processing import processing_queue

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    processing_queue.start()
    processing_queue.recover()  # finish work interrupted by the last shutdown
    yield
    processing_queue.stop()


app = FastAPI(title="NEXUS", version="0.5.1", lifespan=lifespan,
              description="Private, local-first creative knowledge archive.")
# Only answer to local host names (guards against DNS-rebinding from web pages).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_hosts)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

for module in (entries, projects, search, uploads, system, art):
    app.include_router(module.router)


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Served from the root so it can control the whole app (phone offline support)."""
    body = (BASE_DIR / "static" / "sw.js").read_text().replace("__VERSION__", app.version)
    return Response(body, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": app.version})
