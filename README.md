# Lume

A beautiful, lightweight markdown editor with dark mode. Opens any `.md` file on your system in a clean WYSIWYG editor, with live-reload when external processes (like AI agents) modify the file on disk.

## Features

- **WYSIWYG editing** — Milkdown (ProseMirror-based) with CommonMark + GFM support
- **Live reload** — WebSocket-powered; if an external process writes to the file, the editor updates automatically
- **File browser** — sidebar for navigating directories (default root: `~/kb/`)
- **Dark mode** — follows OS preference, with manual toggle in the sidebar
- **Clickable links** — Cmd/Ctrl+click to open hyperlinks in a new tab
- **Keyboard shortcuts** — Cmd/Ctrl+S to save, Cmd/Ctrl+B to toggle sidebar
- **Mobile-friendly** — responsive layout with collapsible sidebar
- **Minimal** — no database, no auth, no build step to run

## Quick start

```bash
# Clone
git clone https://github.com/jawsh84/lume.git
cd lume

# Install Python deps
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Install JS deps and bundle
npm install
npx esbuild src/editor.js --bundle --format=esm --outfile=static/editor.bundle.js --minify

# Run
uvicorn server:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` or navigate directly to a file:

```
http://localhost:8080/edit?path=/path/to/your/file.md
```

## Stack

- **Backend:** FastAPI + watchdog (Python)
- **Frontend:** Milkdown (ProseMirror), vanilla JS/CSS
- **Live reload:** watchdog file watcher → WebSocket → browser

## License

MIT
