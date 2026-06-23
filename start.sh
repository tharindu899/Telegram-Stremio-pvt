#!/usr/bin/env bash
set -e

# Do not run `uv run` at runtime. `uv run` can re-sync packages on every
# Hugging Face restart, which slows startup and may download dev tools.
PYTHON_BIN="/app/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python"
fi

exec "$PYTHON_BIN" -m Backend
