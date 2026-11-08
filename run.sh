#!/bin/sh
# Starts Highlights to Flashcards in the browser. Needs uv: https://docs.astral.sh/uv/
set -e
cd "$(dirname "$0")"
exec uv run --quiet python run.py "$@"
