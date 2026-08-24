"""The authenticated principal — who a request is acting as.

A ``Principal`` is either a regular USER (a seeded employee, ``employee_id`` set,
``is_admin=False``) or the ADMIN (a settings-configured account that is NOT a DB
employee, ``employee_id=None``, ``is_admin=True``). Admin has no employee id
because it never acts as itself — it impersonates a chosen employee via the demo
switcher, so its own id is never used as an ``asker_id``/``responder_id``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Immutable identity resolved from a validated access token."""

    employee_id: int | None
    name: str
    dept: str | None
    is_admin: bool

    def may_act_as(self, employee_id: int) -> bool:
        """True when this principal may act on behalf of ``employee_id``.

        Admin may act as anyone (the demo impersonation); a regular user may only
        act as themselves. This is the single rule the ``asker_id``/``responder_id``
        endpoints enforce.
        """

        return self.is_admin or self.employee_id == employee_id
