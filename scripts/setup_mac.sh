#!/usr/bin/env bash
# One-time NEXUS setup for macOS (Apple Silicon). Safe to run again at any time.
#   ./scripts/setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
stop() { printf "\n\033[1;33m%s\033[0m\n" "$*"; exit 1; }

# 1. Python 3.10 or newer (macOS's built-in python3 is too old).
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || stop "Python 3.10+ is needed. Install it with Homebrew:  brew install python@3.12
(no Homebrew? see https://brew.sh, or use the installer from https://www.python.org/downloads/), then run this script again."
say "Using $($PY --version)"

# 2. Virtual environment + Python packages (NEXUS + local Whisper).
[ -d .venv ] || "$PY" -m venv .venv
say "Installing Python packages…"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-local.txt

# 3. Settings file.
if [ ! -f .env ]; then cp .env.example .env; say "Created .env from .env.example"; fi
.venv/bin/python scripts/upgrade_env.py
MODEL="$(grep -E '^NEXUS_OLLAMA_MODEL=' .env | cut -d= -f2- | tr -d '[:space:]')"
MODEL="${MODEL:-gemma3:12b}"

# 4. Ollama (the local model runner).
command -v ollama >/dev/null 2>&1 || stop "Ollama isn't installed yet.
Download it from https://ollama.com/download,
open the Ollama app once, then run this script again."
curl -fsS -m 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || stop "Ollama is installed but not running.
Open the Ollama app (it lives in the menu bar), then run this script again."
say "Downloading the local model $MODEL (one time, several GB)…"
ollama pull "$MODEL"
EMBED="$(grep -E '^NEXUS_EMBEDDING_MODEL=' .env | cut -d= -f2- | tr -d '[:space:]')"
EMBED="${EMBED:-nomic-embed-text}"
say "Downloading the search-by-meaning model $EMBED (one time, about 0.3 GB)…"
ollama pull "$EMBED"

# 5. Whisper speech model (downloads once, then works offline).
say "Preparing Whisper for voice notes (one-time download)…"
.venv/bin/python -c "from app.services.transcription import LocalWhisperProvider as W; W().load(); print('Whisper ready')"

# 6. A NEXUS icon on the Desktop and in the Dock (optional extra; never fail setup over it).
./scripts/make_mac_app.sh || echo "(Couldn't create the NEXUS icon; you can still start NEXUS from Terminal.)"

say "All set. Click the NEXUS icon, or start NEXUS from Terminal with:"
echo "    source .venv/bin/activate && python run.py"
echo "then open http://localhost:8000"
