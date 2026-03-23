"""Lume — a lightweight, agent-friendly markdown editor."""

import asyncio
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

    def on_modified(self, event):
        if event.is_directory:
            return
        path = str(Path(event.src_path).resolve())
        asyncio.run_coroutine_threadsafe(manager.notify(path), self.loop)

observer = Observer()

@app.on_event("startup")
async def start_watcher():
    loop = asyncio.get_event_loop()
    handler = ChangeHandler(loop)
    # Watch home directory broadly; watchdog is efficient with inotify/kqueue
    observer.schedule(handler, str(Path.home()), recursive=True)
    observer.start()

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
    root = Path(path).resolve() if path else DEFAULT_ROOT
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

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
