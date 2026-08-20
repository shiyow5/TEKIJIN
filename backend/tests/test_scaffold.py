"""Scaffold test — keeps CI green before real tests exist.

Replace this with real tests once the spec is finalized.
"""

from tekijin import __version__


def test_package_imports() -> None:
    assert isinstance(__version__, str)
