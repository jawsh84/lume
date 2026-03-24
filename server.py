"""Lume — a lightweight, agent-friendly markdown editor."""

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = FastAPI()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_ROOT = Path.home() / "kb"
CONFIG_DIR = Path.home() / ".lume"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Read config from disk; create default if missing."""
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Default config
    config = {"folders": [str(DEFAULT_ROOT)]}
    save_config(config)
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# WebSocket manager — tracks which clients watch which files
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}  # path -> [ws]

    async def connect(self, ws: WebSocket, path: str):
        await ws.accept()
        self.connections.setdefault(path, []).append(ws)

    def disconnect(self, ws: WebSocket, path: str):
        if path in self.connections:
            self.connections[path] = [c for c in self.connections[path] if c is not ws]

    async def notify(self, path: str):
        for ws in self.connections.get(path, []):
            try:
                await ws.send_json({"type": "reload", "path": path})
            except Exception:
                pass

manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Watchdog — file system watcher that pushes changes over WS
# ---------------------------------------------------------------------------
class ChangeHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def _handle(self, path):
        resolved = str(Path(path).resolve())
        asyncio.run_coroutine_threadsafe(manager.notify(resolved), self.loop)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.dest_path)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

observer = Observer()
_watched: dict[str, object] = {}  # path -> ObservedWatch handle


async def restart_watchers():
    """Re-read config and update filesystem watches."""
    global _watched
    loop = asyncio.get_event_loop()
    handler = ChangeHandler(loop)
    # Remove old watches
    for watch in _watched.values():
        try:
            observer.unschedule(watch)
        except Exception:
            pass
    _watched.clear()
    # Add watches for all configured folders
    for folder in load_config()["folders"]:
        p = Path(folder)
        if p.is_dir():
            _watched[str(p)] = observer.schedule(handler, str(p), recursive=True)


@app.on_event("startup")
async def start_watcher():
    observer.start()
    await restart_watchers()


@app.on_event("shutdown")
async def stop_watcher():
    observer.stop()
    observer.join()

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.get("/api/read")
async def read_file(path: str = Query(...)):
    p = Path(path).resolve()
    if not p.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return JSONResponse({"content": p.read_text(encoding="utf-8"), "path": str(p)})


@app.post("/api/write")
async def write_file(body: dict):
    p = Path(body["path"]).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body["content"], encoding="utf-8")
    return JSONResponse({"ok": True, "path": str(p)})


@app.get("/api/ls")
async def list_dir(path: str = Query(None)):
    if path is None:
        # Virtual root: list all configured folders
        entries = []
        for f in load_config()["folders"]:
            p = Path(f)
            if p.is_dir():
                entries.append({"name": p.name, "path": str(p), "is_dir": True})
        return JSONResponse({"entries": entries, "path": "/", "is_virtual_root": True})

    root = Path(path).resolve()
    if not root.is_dir():
        return JSONResponse({"error": "Not a directory"}, status_code=400)
    entries = []
    try:
        for item in sorted(root.iterdir()):
            if item.name.startswith("."):
                continue
            entries.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
            })
    except PermissionError:
        pass
    return JSONResponse({"entries": entries, "path": str(root)})


# ---------------------------------------------------------------------------
# Config API
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config():
    config = load_config()
    folders = []
    for f in config["folders"]:
        p = Path(f)
        folders.append({"path": str(p), "exists": p.is_dir(), "name": p.name})
    return JSONResponse({"folders": folders})


@app.post("/api/config/folders/add")
async def add_folder(body: dict):
    folder = Path(body["path"]).resolve()
    if not folder.is_dir():
        return JSONResponse({"error": "Path is not a directory"}, status_code=400)
    config = load_config()
    existing = [str(Path(f).resolve()) for f in config["folders"]]
    if str(folder) in existing:
        return JSONResponse({"error": "Folder already added"}, status_code=409)
    config["folders"].append(str(folder))
    save_config(config)
    await restart_watchers()
    return JSONResponse({"ok": True, "folders": config["folders"]})


@app.post("/api/config/folders/remove")
async def remove_folder(body: dict):
    folder = str(Path(body["path"]).resolve())
    config = load_config()
    resolved = [str(Path(f).resolve()) for f in config["folders"]]
    if folder not in resolved:
        return JSONResponse({"error": "Folder not found"}, status_code=404)
    if len(resolved) <= 1:
        return JSONResponse({"error": "Cannot remove the last folder"}, status_code=400)
    idx = resolved.index(folder)
    config["folders"].pop(idx)
    save_config(config)
    await restart_watchers()
    return JSONResponse({"ok": True, "folders": config["folders"]})


# ---------------------------------------------------------------------------
# WebSocket for live reload
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, path: str = Query(...)):
    resolved = str(Path(path).resolve())
    await manager.connect(ws, resolved)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(ws, resolved)


# ---------------------------------------------------------------------------
# Serve frontend
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"

@app.get("/edit")
async def edit_page():
    return HTMLResponse((STATIC_DIR / "index.html").read_text())

@app.get("/settings")
async def settings_page():
    return HTMLResponse((STATIC_DIR / "index.html").read_text())

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
