"""Every database we create must ask for one explicit pgvector version (#101).

``pgvector/pgvector:pg16`` is a FLOATING tag: it follows whatever pgvector release
is current for PostgreSQL 16. That matters here in two ways:

* ``halfvec`` (needed before an ANN index can be built at 2048 dimensions — the
  point of #101) requires pgvector **0.7+**. The local test server is ``pgserver``,
  which bundles 0.6.2 and has no ``halfvec`` at all, so the floor can only be held
  where a real image is named.
* An upstream release arriving on its own is the shape of breakage nobody
  attributes correctly: no commit in this repository changes, and the failure lands
  on whoever pushes next.

So this asserts what no single file shows: that **every** place we start a Postgres
names the same explicit version, and that it clears the floor.

SCOPE — the three files below are the ones that CREATE a database:

* ``docker-compose.yml`` — local development
* ``.github/workflows/test.yml`` — CI
* ``docs/gpu-server-setup.md`` — the shared DGX instance, i.e. the only one holding
  real data, and the one an ANN index would actually be built on

``docs/benchmarks/e2e.md`` is deliberately NOT checked. Its line 20 records which
image a past measurement ran against; pinning it would falsify the record. Its
throwaway bench command is pinned for consistency but is not load-bearing.

A docs-only edit does not run this test (``docs/**`` is in no workflow's ``paths:``),
so the DGX line is guarded on any backend/compose/workflow change, not on its own.

If this fails after a deliberate upgrade, change the tag in ALL of them — never one.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
TEST_WORKFLOW = REPO / ".github" / "workflows" / "test.yml"
DGX_SETUP = REPO / "docs" / "gpu-server-setup.md"
PINNED_EVERYWHERE = (COMPOSE, TEST_WORKFLOW, DGX_SETUP)

# pgvector 0.7.0 (2024-04-29) introduced `halfvec`; #101 cannot start below it.
MINIMUM_VERSION = (0, 7, 0)

# Accepts every form that IS a pin, so that tightening one never reads as breaking
# it: an optional quote, a `:tag`, a `@sha256:` digest, or both.
_REFERENCE = re.compile(r"""pgvector/pgvector(?P<ref>[:@][^\s"'`]+)""")
# `0.8.6-pg16`, and the distro variants upstream publishes for every release
# (`-bookworm` / `-trixie`). A bare digest reference is a pin too — a stronger one.
_PINNED_TAG = re.compile(r"^:(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)-pg\d+(?:-[a-z]+)?$")
_DIGEST = re.compile(r"^(?::[^\s@]+)?@sha256:[0-9a-f]{64}$")


def _references(path: Path) -> list[str]:
    return [m.group("ref") for m in _REFERENCE.finditer(path.read_text(encoding="utf-8"))]


def _is_pinned(ref: str) -> bool:
    return bool(_PINNED_TAG.match(ref) or _DIGEST.match(ref))


def test_every_pgvector_reference_is_pinned() -> None:
    for path in PINNED_EVERYWHERE:
        refs = _references(path)
        assert refs, f"no pgvector image found in {path.relative_to(REPO)}"
        for ref in refs:
            assert _is_pinned(ref), (
                f"{path.relative_to(REPO)} uses pgvector/pgvector{ref}, which floats. "
                "Pin a full version (e.g. ':0.8.6-pg16') or a @sha256: digest."
            )


def test_local_ci_and_the_dgx_all_ask_for_the_same_pgvector() -> None:
    """A drift here reproduces as "works on my machine" and nothing else."""

    seen = {path.relative_to(REPO).as_posix(): set(_references(path)) for path in PINNED_EVERYWHERE}
    distinct = set().union(*seen.values())
    assert len(distinct) == 1, f"pgvector versions disagree: {seen}"


def test_the_pinned_pgvector_supports_halfvec() -> None:
    """#101 migrates four vector columns to ``halfvec``, which needs 0.7+."""

    for path in PINNED_EVERYWHERE:
        for ref in _references(path):
            m = _PINNED_TAG.match(ref)
            if m is None:
                continue  # a digest pin names no version; the equality test covers it
            version = (int(m["major"]), int(m["minor"]), int(m["patch"]))
            assert version >= MINIMUM_VERSION, (
                f"{path.relative_to(REPO)} pins pgvector {version}, which predates "
                f"halfvec (needs >= {'.'.join(str(p) for p in MINIMUM_VERSION)})"
            )


def _filter_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["changes"]["steps"]
    # By id, not position: inserting a step ahead of it must not turn this into a
    # KeyError three assertions away from the thing it is checking.
    return next(step for step in steps if step.get("id") == "filter")


def test_editing_only_compose_or_the_makefile_still_runs_the_backend_tests() -> None:
    """Otherwise the guards above are unreachable exactly when they are needed.

    ``docker-compose.yml`` holds one of the pins, so editing only that file is the
    most likely way to break the set. ``Makefile`` defines ``test-backend`` itself.
    Either one has to both START this workflow and SURVIVE the paths-filter — if it
    is missing from the filter the workflow runs, skips every job, and reports green
    having tested nothing. That is the shape #309 found, and it was still open for
    ``Makefile`` in this file.
    """

    parsed = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))
    # `on:` parses as the boolean True under YAML 1.1, which is why this is not "on".
    triggers = parsed[True]
    backend_filter = yaml.safe_load(_filter_step(parsed)["with"]["filters"])["backend"]

    for path in ("docker-compose.yml", "Makefile"):
        for event in ("pull_request", "push"):
            assert path in triggers[event]["paths"], (
                f"a {path}-only change does not start the Test workflow on {event}"
            )
        assert path in backend_filter, (
            f"the Test workflow starts for a {path}-only change but the Backend Test "
            "job is skipped, so nothing is actually run"
        )
