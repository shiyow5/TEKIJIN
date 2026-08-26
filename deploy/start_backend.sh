#!/usr/bin/env bash
# Launch the TEKIJIN backend (uvicorn) in the FOREGROUND — #180 task 2.
#
# Runs `exec uvicorn ...` so a supervisor (systemd `Type=exec`) tracks the real
# PID. Do NOT background or `pkill` here; the supervisor owns the lifecycle. For an
# ad-hoc run without systemd, wrap this yourself: `nohup deploy/start_backend.sh &`.
#
# Paths are overridable via env so the same script works on any host:
#   TEKIJIN_VENV_PY   python to use (default: python3; set to your venv's python,
#                     e.g. /home/team_a/tekijin-bench/.venv/bin/python)
#   TEKIJIN_PORT      listen port (default: 18000)
#   TEKIJIN_BACKEND_DIR  backend source dir (default: <repo>/backend, resolved here)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${TEKIJIN_BACKEND_DIR:-$SCRIPT_DIR/../backend}"
VENV_PY="${TEKIJIN_VENV_PY:-python3}"
PORT="${TEKIJIN_PORT:-18000}"
# Pinned here, not in each caller (#456): the code writes naive timestamps from
# BOTH `datetime.now()` (host TZ) and Postgres `now()` (UTC) depending on the
# table, so a host on Asia/Tokyo — as the DGX is — ends up with `questions` in JST
# and `messages` in UTC inside one database. Containers never hit it (already UTC);
# only bare-metal deploys diverged. Every launch path (nohup, deploy.sh, the
# systemd unit) execs this script, so setting it here is the only way all three
# stay in step. Overridable, but there is no good reason to.
TZ="${TZ:-UTC}"
export TZ

cd "$BACKEND_DIR"
exec env PYTHONPATH=src "$VENV_PY" -m uvicorn tekijin.main:app \
  --host 0.0.0.0 --port "$PORT" --workers 1 --app-dir src
