"""Every committed way of starting the API must run exactly ONE uvicorn worker (#76).

The session dispatch registry is in-process: ``ApiService`` keeps each session's
pending input and its TTL stamp in a plain dict guarded by a lock, and says so
(``api/service.py``: "This still requires the API to run SINGLE worker"). With two
workers, a run started on worker A and a follow-up input posted to worker B are
different dicts — the second worker sees no queued run, so the reply is a 404 or a
silently dropped interrupt. Nothing raises; sessions just stop working for some
fraction of requests. Making that safe means a durable (or sticky) queue, which is
the open half of #76 — until then, the count is load-bearing.

``--workers`` is the part that is easy to get wrong by NOT writing it: uvicorn's own
help says it "Defaults to the $WEB_CONCURRENCY environment variable if available,
or 1". The container reads the repo-root ``.env`` wholesale (``env_file`` in
docker-compose.yml), so a ``WEB_CONCURRENCY`` sitting in someone's untracked .env —
a common Gunicorn/Heroku habit — silently forks the API. An explicit ``--workers 1``
on the command line beats the environment, which is why every launch path carries it
rather than relying on the default.

``--reload`` counts as pinned: uvicorn rejects it together with ``--workers``, and a
reloader runs a single server process.

SCOPE — every committed file that starts the API:

* ``Makefile`` (``run-backend`` / ``serve`` / ``serve-prod``)
* ``deploy/start_backend.sh`` — the one path systemd AND deploy.sh both exec
* ``backend/Dockerfile`` — docker-compose's backend service (no ``command:`` override)

If a real multi-worker deployment lands, this test is the thing to delete — in the
same change that makes the queue durable, not before.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TEST_WORKFLOW = REPO / ".github" / "workflows" / "test.yml"
LAUNCH_FILES = (
    REPO / "Makefile",
    REPO / "deploy" / "start_backend.sh",
    REPO / "backend" / "Dockerfile",
)

# The app target every launch names. `pkill -f 'uvicorn tekijin.main:app'` in
# deploy.sh matches this text too but starts nothing, so callers that kill are
# filtered out below rather than pattern-matched here.
_APP = "uvicorn tekijin.main:app"
_WORKERS = re.compile(r"--workers[= ]+(\d+)")


def _launch_commands(path: Path) -> list[str]:
    """Logical lines in ``path`` that START the API.

    Normalised first so one rule covers three syntaxes: shell continuations are
    joined, and Docker's exec form (``CMD ["python", "-m", "uvicorn", ...]``) is
    flattened by dropping the JSON punctuation — otherwise the argv-array launch,
    the one path that was actually missing its pin, reads as "no launch here".
    """

    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    text = re.sub(r"""["'\[\],]""", " ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [
        line for line in lines if _APP in line and "pkill" not in line and not line.startswith("#")
    ]


def test_every_launch_path_is_covered() -> None:
    """The scope list above is real: each named file still starts the API.

    Without this, a file that stops launching (renamed target, rewritten script)
    would leave the assertions below vacuously green over an empty list.
    """

    for path in LAUNCH_FILES:
        assert path.exists(), f"{path} is gone — update this test's SCOPE"
        assert _launch_commands(path), f"{path} no longer starts uvicorn — update SCOPE"


def test_no_launch_path_runs_more_than_one_worker() -> None:
    for path in LAUNCH_FILES:
        for command in _launch_commands(path):
            found = _WORKERS.search(command)
            if found:
                assert found.group(1) == "1", (
                    f"{path.relative_to(REPO)} starts {found.group(1)} workers: {command.strip()}\n"
                    "The dispatch registry is in-process (#76) — a second worker loses sessions."
                )
            else:
                assert "--reload" in command, (
                    f"{path.relative_to(REPO)} starts uvicorn without pinning the worker count: "
                    f"{command.strip()}\n"
                    "Uvicorn then defaults to $WEB_CONCURRENCY, which the container inherits from "
                    ".env — pass --workers 1 explicitly."
                )


def _covers(pattern: str, path: str) -> bool:
    """Does a workflow `paths:` entry select ``path``? (`dir/**` or an exact file.)"""

    return pattern == path or (pattern.endswith("/**") and path.startswith(pattern[:-2]))


def test_editing_only_a_launch_path_still_runs_these_tests() -> None:
    """Otherwise the pin above is unreachable exactly when it is needed.

    "Bump --workers for throughput" is a deploy-script-only PR. Before #76 added
    ``deploy/**``, such a PR started no workflow at all: the guard would sit in the
    repository, green, while the change it exists to stop merged past it. A file has
    to both START the Test workflow and SURVIVE the paths-filter — missing from the
    filter, the run happens, every job skips, and it reports green having tested
    nothing (the #309 shape).

    Derived from ``LAUNCH_FILES`` rather than a hardcoded list, so extending SCOPE
    without extending CI fails here instead of silently going unwatched.
    """

    parsed = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))
    # `on:` parses as the boolean True under YAML 1.1, which is why this is not "on".
    triggers = parsed[True]
    steps = parsed["jobs"]["changes"]["steps"]
    # By id, not position — inserting a step ahead of it must not break this.
    filter_step = next(step for step in steps if step.get("id") == "filter")
    backend_filter = yaml.safe_load(filter_step["with"]["filters"])["backend"]

    for launch in LAUNCH_FILES:
        path = launch.relative_to(REPO).as_posix()
        for event in ("pull_request", "push"):
            patterns = triggers[event]["paths"]
            assert any(_covers(p, path) for p in patterns), (
                f"a {path}-only change does not start the Test workflow on {event}"
            )
        assert any(_covers(p, path) for p in backend_filter), (
            f"the Test workflow starts for a {path}-only change but the Backend Test "
            f"job is skipped, so the worker pin is never checked"
        )
