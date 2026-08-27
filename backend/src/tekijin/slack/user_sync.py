"""Decide what a Slack directory sync would change, without changing anything.

#406 step 3 makes the Slack workspace the source of truth for who exists. That
means this module writes the same ``slack_links`` table the OAuth flow writes —
but with **no human in the loop**. The OAuth flow shipped the same authorization
bug three times ("the two halves come from different people"), and every one of
those was caught by a person asking "who consented to this?". Here nobody
consents to anything, so the rules have to be structural.

The planner is pure on purpose: it takes Slack's member list plus the current DB
state and returns what it *would* do. No session, no network, no clock. That way
the security rules below are assertable directly, and the part that touches the
database has nothing left to decide.

The rules, and why each one exists:

* **An existing link is never overwritten.** Re-linking is a deliberate act
  behind a bearer token. A sync has no such consent, so a changed address in a
  Slack profile must not move an employee's identity — with Slack login enabled
  that would hand over their session.
* **A Slack account already linked elsewhere is never re-pointed.** The mirror
  of the same rule, and literally round 1 of the OAuth bug run on a schedule.
* **Only ``deleted: true`` unlinks.** Absence from the list never does: a
  truncated page or a failed request would otherwise read as "everyone left".
* **Unlink is keyed on the Slack id, not the address.** The row that gets cut
  must be the row that actually holds the departing account.
* **No email, no match.** Falling back to display names would put fuzzy identity
  matching into an authentication path.
* **The admin address is never linked.** The admin principal is deliberately not
  an employee row; nothing from Slack may inherit it.
* **An employee claimed twice in one run is linked to neither.** Every rule above
  is checked against the database as it was BEFORE the run, which closes the
  cross-run case and misses the within-run one: two members of the same
  ``users.list`` response resolving to one employee would both pass "not already
  linked". Without SSO/SCIM a Slack member sets ``profile.email`` themselves, so
  that is reachable by putting a colleague's address in your own profile. Taking
  the first claimant would only mean an attacker has to sort earlier, and nothing
  here can tell which claimant is genuine — so an ambiguous employee is left
  alone and reported. The same holds for one Slack account claiming two
  employees, which would additionally violate the unique constraint on
  ``slack_user_id`` and abort the batch, delaying the departure unlinks in it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

_SLACKBOT_ID = "USLACKBOT"


@dataclass(frozen=True)
class SlackMember:
    """One entry from ``users.list``, reduced to what the join needs."""

    slack_user_id: str
    team_id: str
    email: str | None
    display_name: str
    deleted: bool
    is_bot: bool
    is_restricted: bool
    is_ultra_restricted: bool

    @property
    def is_colleague(self) -> bool:
        """Whether this member is a person who could hold an employee row.

        Bots have no employee behind them, and guests (``is_restricted`` /
        ``is_ultra_restricted``) are outside the company by definition — with
        Slack login enabled, linking one would grant an outsider a session.
        """

        return not (
            self.is_bot
            or self.is_restricted
            or self.is_ultra_restricted
            or self.slack_user_id == _SLACKBOT_ID
        )


@dataclass(frozen=True)
class SyncPlan:
    """What a sync would do. ``skipped`` counts why nothing happened."""

    link: tuple[tuple[int, str], ...] = ()
    unlink: tuple[int, ...] = ()
    # (email, display_name, slack_user_id) for colleagues who have no employee
    # row yet. Separate from ``link`` because the employee id does not exist for
    # the planner to name — creating the row and attaching the Slack account are
    # one act, carried out together.
    create: tuple[tuple[str, str, str], ...] = ()
    skipped: Mapping[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.link and not self.unlink and not self.create


def parse_members(raw: Iterable[Mapping[str, object]]) -> list[SlackMember]:
    """Read Slack's ``members`` array, tolerating fields it may omit.

    An entry with no ``id`` is dropped rather than defaulted: an empty id would
    become a join key that matches nothing useful and could collide with itself.
    """

    members: list[SlackMember] = []
    for entry in raw:
        slack_user_id = entry.get("id")
        if not isinstance(slack_user_id, str) or not slack_user_id:
            continue
        profile = entry.get("profile")
        profile = profile if isinstance(profile, Mapping) else {}
        email = profile.get("email")
        name = profile.get("real_name") or profile.get("display_name") or slack_user_id
        team_id = entry.get("team_id")
        members.append(
            SlackMember(
                slack_user_id=slack_user_id,
                team_id=team_id if isinstance(team_id, str) else "",
                email=email if isinstance(email, str) and email else None,
                display_name=name if isinstance(name, str) else slack_user_id,
                deleted=bool(entry.get("deleted")),
                is_bot=bool(entry.get("is_bot")),
                is_restricted=bool(entry.get("is_restricted")),
                is_ultra_restricted=bool(entry.get("is_ultra_restricted")),
            )
        )
    return members


def _normalise(email: str | None) -> str | None:
    if not email:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def plan_user_sync(
    members: Iterable[SlackMember],
    *,
    employee_id_by_email: Mapping[str, int],
    linked_slack_user_by_employee: Mapping[int, str],
    employee_by_slack_user: Mapping[str, int],
    expected_team_id: str,
    admin_email: str,
    create_missing: bool = False,
    allowed_create_domains: Sequence[str] = (),
) -> SyncPlan:
    """Work out the links to add and the links to cut. Writes nothing.

    ``expected_team_id`` is required rather than optional: unlike the read-time
    filter in ``data/slack_links.py`` — which tolerates a blank setting so rows
    predating the setting still resolve — a blank workspace here would let any
    workspace's member list drive writes. A blank value therefore matches no one.
    """

    by_email = {
        normalised: employee_id
        for email, employee_id in employee_id_by_email.items()
        if (normalised := _normalise(email)) is not None
    }
    admin = _normalise(admin_email)

    candidates: list[tuple[int, str]] = []
    new_people: list[tuple[str, str, str]] = []
    unlink: list[int] = []
    skipped: Counter[str] = Counter()

    for member in members:
        if not expected_team_id or member.team_id != expected_team_id:
            skipped["foreign_workspace"] += 1
            continue

        # Departure first: a deleted account is still a bot/guest sometimes, and
        # cutting its link matters more than classifying it.
        if member.deleted:
            employee_id = employee_by_slack_user.get(member.slack_user_id)
            if employee_id is not None:
                unlink.append(employee_id)
            continue

        if not member.is_colleague:
            skipped["not_a_member"] += 1
            continue

        email = _normalise(member.email)
        if email is None:
            skipped["no_email"] += 1
            continue
        if admin is not None and email == admin:
            skipped["admin_address"] += 1
            continue

        employee_id = by_email.get(email)
        if employee_id is None:
            if create_missing:
                if not _domain_allowed(email, allowed_create_domains):
                    skipped["email_domain_not_allowed"] += 1
                    continue
                new_people.append((email, member.display_name, member.slack_user_id))
            else:
                skipped["no_matching_employee"] += 1
            continue

        owner = employee_by_slack_user.get(member.slack_user_id)
        if owner is not None and owner != employee_id:
            skipped["slack_account_belongs_to_another_employee"] += 1
            continue

        existing = linked_slack_user_by_employee.get(employee_id)
        if existing == member.slack_user_id:
            continue
        if existing is not None:
            skipped["already_linked_to_another_slack_account"] += 1
            continue

        candidates.append((employee_id, member.slack_user_id))

    link = _drop_ambiguous(candidates, skipped)
    # De-duplicated because the count is what an operator reads: "unlinked: 2"
    # for one departure listed twice says two colleagues left. The applier
    # tolerates the repeat, so this is about the report, not the write.
    return SyncPlan(
        link=link,
        unlink=tuple(dict.fromkeys(unlink)),
        create=_drop_contested_new_people(new_people, skipped),
        skipped=dict(skipped),
    )


def _drop_ambiguous(
    candidates: list[tuple[int, str]], skipped: Counter[str]
) -> tuple[tuple[int, str], ...]:
    """Keep only the pairs where both halves are claimed exactly once this run.

    An identical pair listed twice is a quirk of the payload, not two claimants,
    so it collapses to one rather than disqualifying a legitimate link. Anything
    genuinely contested is dropped and counted — one ambiguous pair must not cost
    the rest of the workspace its sync, and it must never hold up an unlink,
    which is the half that cuts off login.
    """

    unique = list(dict.fromkeys(candidates))
    employees = Counter(employee_id for employee_id, _ in unique)
    slack_users = Counter(slack_user_id for _, slack_user_id in unique)

    kept: list[tuple[int, str]] = []
    for employee_id, slack_user_id in unique:
        if employees[employee_id] > 1:
            skipped["ambiguous_email"] += 1
        elif slack_users[slack_user_id] > 1:
            skipped["ambiguous_slack_account"] += 1
        else:
            kept.append((employee_id, slack_user_id))
    return tuple(kept)


def _drop_contested_new_people(
    new_people: list[tuple[str, str, str]], skipped: Counter[str]
) -> tuple[tuple[str, str, str], ...]:
    """Keep only the addresses exactly one Slack account is claiming.

    The same rule as :func:`_drop_ambiguous`, and it matters more here: the
    employee row does not exist yet, so whichever claimant won would not be
    taking a share of an existing identity — they would own the new one
    outright, and the real colleague would arrive to find themselves occupied.
    """

    unique = list(dict.fromkeys(new_people))
    per_email = Counter(email for email, _, _ in unique)
    per_account = Counter(slack_user_id for _, _, slack_user_id in unique)

    kept: list[tuple[str, str, str]] = []
    for email, display_name, slack_user_id in unique:
        if per_email[email] > 1:
            skipped["ambiguous_email"] += 1
        elif per_account[slack_user_id] > 1:
            skipped["ambiguous_slack_account"] += 1
        else:
            kept.append((email, display_name, slack_user_id))
    return tuple(kept)


def _domain_allowed(email: str, allowed: Sequence[str]) -> bool:
    """Whether ``email`` sits in one of the configured company domains.

    Creation mints an identity, so the address it is keyed on should look like a
    company address — otherwise any workspace member (a contractor, a partner,
    anyone invited once) becomes an employee by virtue of the address on their
    profile. An empty list allows everything, matching every other switch here:
    restricting is opt-in, so enabling creation does not silently start refusing
    people.

    Compared on the part after the LAST ``@`` and anchored, not by ``endswith``
    on the whole address — ``endswith("corp.jp")`` would accept both
    ``someone@corp.jp.evil.com`` and ``someone@notcorp.jp``.
    """

    if not allowed:
        return True
    _, _, domain = email.rpartition("@")
    domain = domain.strip().lower()
    return any(domain == candidate.strip().lower().lstrip("@") for candidate in allowed)
