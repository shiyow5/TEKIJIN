"""The directory sync decides WHO gets linked — so it is the third place where
"two halves from different people" could open up (#406 step 3).

The OAuth flow was fixed three times for exactly that class of bug. This sync
writes the same `slack_links` table WITHOUT any human in the loop, so every rule
that stops one person's Slack identity landing on another person's row has to be
re-established here from scratch. These tests are the statement of those rules.

The planner is deliberately pure: it takes the Slack member list and the current
DB state and returns what it WOULD do. That makes the security rules assertable
without a database, a network, or a scheduler.
"""

from __future__ import annotations

from tekijin.slack.user_sync import SlackMember, parse_members, plan_user_sync

TEAM = "T_REAL"


def _member(
    slack_user_id: str,
    email: str | None = None,
    *,
    team_id: str = TEAM,
    deleted: bool = False,
    is_bot: bool = False,
    is_restricted: bool = False,
    is_ultra_restricted: bool = False,
) -> SlackMember:
    return SlackMember(
        slack_user_id=slack_user_id,
        team_id=team_id,
        email=email,
        display_name=slack_user_id,
        deleted=deleted,
        is_bot=is_bot,
        is_restricted=is_restricted,
        is_ultra_restricted=is_ultra_restricted,
    )


def _plan(members, **kwargs):
    return plan_user_sync(
        members,
        employee_id_by_email=kwargs.get("employee_id_by_email", {"a@x.jp": 1}),
        linked_slack_user_by_employee=kwargs.get("linked_slack_user_by_employee", {}),
        employee_by_slack_user=kwargs.get("employee_by_slack_user", {}),
        expected_team_id=kwargs.get("expected_team_id", TEAM),
        admin_email=kwargs.get("admin_email", "admin@tekijin.local"),
    )


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #
def test_an_unlinked_employee_is_linked_to_the_matching_slack_account() -> None:
    plan = _plan([_member("U_A", "a@x.jp")])

    assert plan.link == ((1, "U_A"),)
    assert plan.unlink == ()


def test_email_matching_ignores_case_and_surrounding_space() -> None:
    plan = _plan([_member("U_A", "  A@X.JP  ")])

    assert plan.link == ((1, "U_A"),)


def test_an_already_correctly_linked_employee_produces_no_write() -> None:
    plan = _plan(
        [_member("U_A", "a@x.jp")],
        linked_slack_user_by_employee={1: "U_A"},
        employee_by_slack_user={"U_A": 1},
    )

    assert plan.link == ()
    assert plan.unlink == ()


# --------------------------------------------------------------------------- #
# "Two halves from different people" — the class that shipped three times
# --------------------------------------------------------------------------- #
def test_an_existing_link_is_never_overwritten_by_the_sync() -> None:
    """Employee 1 is already linked to U_OLD; Slack says their email is U_NEW's.

    Overwriting here would be a silent account takeover: whoever can get an
    address into a Slack profile would inherit an existing employee's identity —
    and with Slack login enabled, their session. A human re-link is a deliberate
    act with a bearer token behind it; this sync has no such consent, so it must
    leave the row alone and say so.
    """

    plan = _plan(
        [_member("U_NEW", "a@x.jp")],
        linked_slack_user_by_employee={1: "U_OLD"},
        employee_by_slack_user={"U_OLD": 1},
    )

    assert plan.link == ()
    assert plan.unlink == ()
    assert plan.skipped["already_linked_to_another_slack_account"] == 1


def test_a_slack_account_already_linked_to_someone_else_is_not_relinked() -> None:
    """The mirror direction. U_A belongs to employee 2; Slack now reports that
    address on employee 1. Following it would move one person's Slack identity
    onto another person's row — exactly round 1 of the OAuth bug, automated."""

    plan = _plan(
        [_member("U_A", "a@x.jp")],
        employee_by_slack_user={"U_A": 2},
    )

    assert plan.link == ()
    assert plan.skipped["slack_account_belongs_to_another_employee"] == 1


def test_a_member_from_another_workspace_is_ignored() -> None:
    plan = _plan([_member("U_A", "a@x.jp", team_id="T_OTHER")])

    assert plan.link == ()
    assert plan.skipped["foreign_workspace"] == 1


def test_the_admin_address_is_never_linked_from_slack() -> None:
    """The admin principal is deliberately not an employee row. If one ever
    exists with that address, a Slack member claiming it must not inherit it."""

    plan = _plan(
        [_member("U_A", "admin@tekijin.local")],
        employee_id_by_email={"admin@tekijin.local": 1},
    )

    assert plan.link == ()
    assert plan.skipped["admin_address"] == 1


# --------------------------------------------------------------------------- #
# Who is not a colleague
# --------------------------------------------------------------------------- #
def test_bots_guests_and_slackbot_are_excluded() -> None:
    members = [
        _member("U_BOT", "bot@x.jp", is_bot=True),
        _member("U_GUEST", "g@x.jp", is_restricted=True),
        _member("U_SINGLE", "s@x.jp", is_ultra_restricted=True),
        _member("USLACKBOT", "slackbot@x.jp"),
    ]
    plan = _plan(
        members,
        employee_id_by_email={
            "bot@x.jp": 1,
            "g@x.jp": 2,
            "s@x.jp": 3,
            "slackbot@x.jp": 4,
        },
    )

    assert plan.link == ()
    assert plan.skipped["not_a_member"] == 4


def test_a_member_without_an_email_cannot_be_matched() -> None:
    """`users:read.email` may not be granted. Without an address there is no
    join key at all, and guessing by display name would be exactly the kind of
    fuzzy identity match this table must never contain."""

    plan = _plan([_member("U_A", None)])

    assert plan.link == ()
    assert plan.skipped["no_email"] == 1


def test_a_slack_member_with_no_employee_row_is_reported_not_invented() -> None:
    plan = _plan([_member("U_A", "stranger@x.jp")])

    assert plan.link == ()
    assert plan.skipped["no_matching_employee"] == 1


# --------------------------------------------------------------------------- #
# Departures
# --------------------------------------------------------------------------- #
def test_a_deactivated_slack_account_unlinks_its_employee() -> None:
    """Deletion in Slack is the departure signal (#406). Unlinking stops Slack
    login immediately. The employee ROW stays — questions and answers reference
    it, and deleting it would tear history out of the product."""

    plan = _plan(
        [_member("U_A", "a@x.jp", deleted=True)],
        linked_slack_user_by_employee={1: "U_A"},
        employee_by_slack_user={"U_A": 1},
    )

    assert plan.unlink == (1,)
    assert plan.link == ()


def test_a_deactivated_account_that_was_never_linked_is_a_no_op() -> None:
    plan = _plan([_member("U_A", "a@x.jp", deleted=True)])

    assert plan.unlink == ()
    assert plan.link == ()


def test_a_deactivated_account_does_not_unlink_a_DIFFERENT_employees_row() -> None:
    """U_A is deleted in Slack but the row it would touch belongs to employee 2
    via a different Slack account. Unlinking by email rather than by the Slack
    id would cut off an active colleague."""

    plan = _plan(
        [_member("U_A", "a@x.jp", deleted=True)],
        employee_id_by_email={"a@x.jp": 1},
        linked_slack_user_by_employee={1: "U_OTHER"},
        employee_by_slack_user={"U_OTHER": 1},
    )

    assert plan.unlink == ()


def test_a_member_missing_from_the_list_entirely_is_not_unlinked() -> None:
    """A truncated page or a transient API error must not read as "everyone
    left". Only an explicit `deleted: true` unlinks — absence never does."""

    plan = _plan(
        [],
        linked_slack_user_by_employee={1: "U_A"},
        employee_by_slack_user={"U_A": 1},
    )

    assert plan.unlink == ()


# --------------------------------------------------------------------------- #
# Parsing Slack's payload
# --------------------------------------------------------------------------- #
def test_parse_members_reads_the_fields_slack_actually_sends() -> None:
    parsed = parse_members(
        [
            {
                "id": "U_A",
                "team_id": TEAM,
                "deleted": False,
                "is_bot": False,
                "is_restricted": False,
                "is_ultra_restricted": False,
                "profile": {"email": "a@x.jp", "real_name": "社員A"},
            }
        ]
    )

    assert parsed == [
        SlackMember(
            slack_user_id="U_A",
            team_id=TEAM,
            email="a@x.jp",
            display_name="社員A",
            deleted=False,
            is_bot=False,
            is_restricted=False,
            is_ultra_restricted=False,
        )
    ]


def test_parse_members_tolerates_a_missing_profile_block() -> None:
    parsed = parse_members([{"id": "U_A", "team_id": TEAM}])

    assert parsed[0].email is None
    assert parsed[0].deleted is False


def test_parse_members_drops_an_entry_with_no_id() -> None:
    assert parse_members([{"team_id": TEAM, "profile": {"email": "a@x.jp"}}]) == []


# --------------------------------------------------------------------------- #
# The same class, a fourth time: both halves inside ONE run
# --------------------------------------------------------------------------- #
# The rules above all consult the database snapshot taken before the run. That
# closes the cross-run case and misses the within-run one: two members of the
# same `users.list` response resolving to the same employee both pass "not
# already linked", because nothing recorded the first one's claim.
#
# In a workspace without SSO/SCIM, `profile.email` is free text the member sets
# themselves. So this is reachable: put a colleague's address in your own Slack
# profile and wait for the next sync.
#
# The policy is to link NEITHER. Taking the first would just mean an attacker
# has to sort earlier, and there is no way from here to tell which of two
# claimants is the real one — that is a question for a human.
def test_two_slack_accounts_claiming_one_employee_link_neither() -> None:
    plan = _plan([_member("U_VICTIM", "a@x.jp"), _member("U_ATTACKER", "a@x.jp")])

    assert plan.link == ()
    assert plan.skipped["ambiguous_email"] == 2


def test_the_collision_is_caught_through_case_and_whitespace_variants() -> None:
    """`_normalise` folds case and strips space, so the two must collide AFTER
    normalisation — checking the raw strings would miss `A@X.JP `."""

    plan = _plan([_member("U_VICTIM", "a@x.jp"), _member("U_ATTACKER", "  A@X.JP ")])

    assert plan.link == ()
    assert plan.skipped["ambiguous_email"] == 2


def test_a_third_claimant_does_not_rescue_the_first_two() -> None:
    plan = _plan([_member("U_1", "a@x.jp"), _member("U_2", "a@x.jp"), _member("U_3", "a@x.jp")])

    assert plan.link == ()
    assert plan.skipped["ambiguous_email"] == 3


def test_one_slack_account_listed_twice_is_not_treated_as_a_conflict() -> None:
    """A duplicated entry for the SAME account is a quirk of the payload, not two
    claimants — collapse it rather than refusing the legitimate link."""

    plan = _plan([_member("U_A", "a@x.jp"), _member("U_A", "a@x.jp")])

    assert plan.link == ((1, "U_A"),)


def test_one_slack_account_claiming_two_employees_links_neither() -> None:
    """The mirror: the same Slack id arriving twice under different addresses.
    Applying both would hit the unique constraint on `slack_user_id` and abort
    the whole batch — including departure unlinks that must not be delayed."""

    plan = _plan(
        [_member("U_A", "a@x.jp"), _member("U_A", "b@x.jp")],
        employee_id_by_email={"a@x.jp": 1, "b@x.jp": 2},
    )

    assert plan.link == ()
    assert plan.skipped["ambiguous_slack_account"] == 2


def test_an_unrelated_colleague_in_the_same_run_is_still_linked() -> None:
    """One ambiguous pair must not cost everyone else their sync."""

    plan = _plan(
        [
            _member("U_VICTIM", "a@x.jp"),
            _member("U_ATTACKER", "a@x.jp"),
            _member("U_FINE", "c@x.jp"),
        ],
        employee_id_by_email={"a@x.jp": 1, "c@x.jp": 2},
    )

    assert plan.link == ((2, "U_FINE"),)
    assert plan.skipped["ambiguous_email"] == 2


def test_a_departure_still_happens_even_when_another_pair_is_ambiguous() -> None:
    """Unlinks are the security-relevant half: they cut off login. An ambiguous
    link elsewhere in the payload must not hold them up."""

    plan = _plan(
        [
            _member("U_1", "a@x.jp"),
            _member("U_2", "a@x.jp"),
            _member("U_GONE", "c@x.jp", deleted=True),
        ],
        employee_id_by_email={"a@x.jp": 1, "c@x.jp": 2},
        linked_slack_user_by_employee={2: "U_GONE"},
        employee_by_slack_user={"U_GONE": 2},
    )

    assert plan.link == ()
    assert plan.unlink == (2,)


def test_a_departure_listed_twice_is_reported_once() -> None:
    """The applier tolerates the repeat (the second delete is a no-op), but the
    COUNT is what an operator reads. "unlinked: 2" for one departure says two
    colleagues left, which is the sort of number someone acts on."""

    plan = _plan(
        [
            _member("U_GONE", "a@x.jp", deleted=True),
            _member("U_GONE", "a@x.jp", deleted=True),
        ],
        linked_slack_user_by_employee={1: "U_GONE"},
        employee_by_slack_user={"U_GONE": 1},
    )

    assert plan.unlink == (1,)
