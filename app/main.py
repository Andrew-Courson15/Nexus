"""NEXUS — FastAPI application."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import BASE_DIR, get_settings
from app.database import init_db
from app.routes import art, backups, entries, graph, lock, offline, organize, projects, search, share, system, uploads
from app.services.backup import runner as backup_runner
from app.services.processing import processing_queue

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    processing_queue.start()
    processing_queue.recover()  # finish work interrupted by the last shutdown
    backup_runner.start_schedule()  # automatic backups (NEXUS_BACKUP_EVERY_DAYS)
    yield
    backup_runner.stop()
    processing_queue.stop()


app = FastAPI(title="NEXUS", version=__version__, lifespan=lifespan,
              description="Private, local-first creative knowledge archive.")
# Only answer to local host names (guards against DNS-rebinding from web pages).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_hosts)

from app.services import lock as passcode_lock  # noqa: E402


@app.middleware("http")
async def require_passcode(request: Request, call_next):
    """With a passcode set, other devices must unlock before reading or changing anything."""
    path = request.url.path
    if (path.startswith("/api/") and path not in passcode_lock.OPEN_PATHS and passcode_lock.enabled()
            and not passcode_lock.is_local(request.client.host if request.client else None, request.headers)
            and not passcode_lock.valid_token(request.cookies.get(passcode_lock.COOKIE))):
        return JSONResponse({"detail": "NEXUS is locked. Enter the passcode to unlock this device.", "locked": True},
                            status_code=401)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

for module in (entries, organize, projects, search, uploads, system, art, offline, backups, share, graph, lock):
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
