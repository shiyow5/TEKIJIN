"""The pgvector image must be pinned to one explicit version everywhere (#101).

``pgvector/pgvector:pg16`` is a FLOATING tag: it follows whatever pgvector release
is current for PostgreSQL 16. That matters here because the schema is version
sensitive in one direction that fails loudly and one that fails silently:

* ``halfvec`` (needed before an ANN index can be built at 2048 dimensions — the
  whole point of #101) requires pgvector **0.7+**. The local test server is
  ``pgserver`` with 0.6.2, so the floor has to be checked somewhere, and CI is the
  only place that runs a version new enough to matter.
* An upstream bump arriving on its own is exactly the shape of breakage nobody
  attributes to the right cause: no commit in this repository changes, and the
  failure lands on whoever pushes next.

So this asserts what a human cannot see by reading one file: that CI and local
development ask for the SAME explicit version, and that it clears the floor.

If this fails after a deliberate upgrade, change ``MINIMUM_VERSION`` or the tag in
both files — never just one.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.yml"
TEST_WORKFLOW = REPO / ".github" / "workflows" / "test.yml"

# pgvector 0.7.0 introduced `halfvec`; #101 cannot start below it.
MINIMUM_VERSION = (0, 7, 0)

_IMAGE = re.compile(r"image:\s*pgvector/pgvector:(?P<tag>\S+)")
_PINNED_TAG = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)-pg\d+$")


def _tags(path: Path) -> list[str]:
    return _IMAGE.findall(path.read_text(encoding="utf-8"))


def _version(tag: str) -> tuple[int, int, int]:
    m = _PINNED_TAG.match(tag)
    assert m is not None, (
        f"pgvector image tag {tag!r} is not pinned to an explicit version. "
        "A floating tag (e.g. 'pg16') silently changes the database under CI."
    )
    return (int(m["major"]), int(m["minor"]), int(m["patch"]))


def test_every_pgvector_image_is_pinned_to_an_explicit_version() -> None:
    found = {path.name: _tags(path) for path in (COMPOSE, TEST_WORKFLOW)}
    assert all(found.values()), f"no pgvector image found in {found}"
    for name, tags in found.items():
        for tag in tags:
            assert _PINNED_TAG.match(tag), (
                f"{name} uses the floating tag pgvector/pgvector:{tag}. "
                "Pin it to a full version (e.g. '0.8.6-pg16')."
            )


def test_ci_and_local_development_ask_for_the_same_pgvector() -> None:
    """A drift here reproduces as "works on my machine" and nothing else."""

    compose = set(_tags(COMPOSE))
    workflow = set(_tags(TEST_WORKFLOW))
    assert compose == workflow, (
        f"docker-compose.yml pins {sorted(compose)} but "
        f".github/workflows/test.yml pins {sorted(workflow)}"
    )


def test_a_compose_only_change_actually_runs_this_test() -> None:
    """Otherwise the guard above is unreachable exactly when it is needed.

    ``docker-compose.yml`` holds one of the two pins, so editing only that file is
    the most likely way to break the pair. If it is missing from either the
    workflow trigger or the paths-filter, such a PR reports all-green having run
    nothing — the shape #309 found with a Makefile-only PR.
    """

    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(workflow)
    # `on:` parses as the boolean True in YAML 1.1, which is why this is not "on".
    triggers = parsed[True]
    for event in ("pull_request", "push"):
        assert "docker-compose.yml" in triggers[event]["paths"], (
            f"a compose-only change does not start the Test workflow on {event}"
        )

    filters = parsed["jobs"]["changes"]["steps"][1]["with"]["filters"]
    backend = yaml.safe_load(filters)["backend"]
    assert "docker-compose.yml" in backend, (
        "the Test workflow starts but the Backend Test job is skipped, so this "
        "file is never executed for a compose-only change"
    )


def test_the_pinned_pgvector_supports_halfvec() -> None:
    """#101 migrates four vector columns to ``halfvec``, which needs 0.7+."""

    for tag in _tags(COMPOSE):
        assert _version(tag) >= MINIMUM_VERSION, (
            f"pgvector {tag} predates halfvec (needs >= "
            f"{'.'.join(str(p) for p in MINIMUM_VERSION)})"
        )
