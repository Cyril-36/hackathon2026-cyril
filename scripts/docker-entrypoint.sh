#!/usr/bin/env bash
# Dispatcher for the Docker image.
#
#   docker run --rm shopwave-agent              # default CLI run (rules mode)
#   docker run --rm shopwave-agent web          # launch the FastAPI web UI
#   docker run --rm shopwave-agent test         # run the pytest suite
#   docker run --rm shopwave-agent <anything>   # pass-through (e.g. python run.py --mode rules --chaos 30)
#
# The default path is CLI-only so `docker run shopwave-agent` stays identical
# to the verifier path — the web UI is opt-in via the `web` subcommand.
set -euo pipefail

case "${1:-}" in
  "")
    exec python run.py --mode rules --chaos 0
    ;;
  web)
    shift
    exec uvicorn app.server:app --host 0.0.0.0 --port 8787 "$@"
    ;;
  test)
    shift
    exec python -m pytest "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
