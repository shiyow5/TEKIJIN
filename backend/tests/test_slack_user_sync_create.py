"""Auto-registering a new colleague from the Slack roster (#406 / #402).

Until now a person could only enter TEKIJIN via ``make seed``, which TRUNCATEs
every table — so adding one new hire destroyed the accumulated knowledge. The
sync reported ``no_matching_employee`` and stopped there, which meant somebody
who joined Slack could never log in at all.

Creation is behind its OWN flag, on top of the sync's. Turning it on means
"membership of this Slack workspace makes you an employee here", which is a much
larger statement than "reconcile the links of people we already know", and it
should not ride along with the smaller one.
"""

from __future__ import annotations

from tekijin.slack.user_sync import SlackMember, plan_user_sync

TEAM = "T_REAL"


def _member(
    slack_user_id: str,
    email: str | None = None,
    *,
    team_id: str = TEAM,
    display_name: str = "",
    deleted: bool = False,
    is_bot: bool = False,
    is_restricted: bool = False,
    is_ultra_restricted: bool = False,
) -> SlackMember:
    return SlackMember(
        slack_user_id=slack_user_id,
        team_id=team_id,
        email=email,
        display_name=display_name or slack_user_id,
        deleted=deleted,
        is_bot=is_bot,
        is_restricted=is_restricted,
        is_ultra_restricted=is_ultra_restricted,
    )


def _plan(members, *, create=True, **kwargs):
    return plan_user_sync(
        members,
        employee_id_by_email=kwargs.get("employee_id_by_email", {}),
        linked_slack_user_by_employee=kwargs.get("linked_slack_user_by_employee", {}),
        employee_by_slack_user=kwargs.get("employee_by_slack_user", {}),
        expected_team_id=kwargs.get("expected_team_id", TEAM),
        admin_email=kwargs.get("admin_email", "admin@tekijin.local"),
        create_missing=create,
        allowed_create_domains=kwargs.get("allowed_domains", ()),
    )


def test_a_slack_member_with_no_employee_row_is_created() -> None:
    plan = _plan([_member("U_NEW", "yamada@x.jp", display_name="山田 花子")])

    assert plan.create == (("yamada@x.jp", "山田 花子", "U_NEW"),)
    # Not linked separately: the link is part of creating them, and the employee
    # id does not exist yet for the planner to name.
    assert plan.link == ()


def test_nothing_is_created_while_the_flag_is_off() -> None:
    """The default. Registering everyone is a bigger statement than reconciling
    the people we already know, so it does not ride along with the sync flag."""

    plan = _plan([_member("U_NEW", "yamada@x.jp")], create=False)

    assert plan.create == ()
    assert plan.skipped["no_matching_employee"] == 1


def test_an_existing_employee_is_linked_not_created() -> None:
    plan = _plan([_member("U_A", "known@x.jp")], employee_id_by_email={"known@x.jp": 5})

    assert plan.create == ()
    assert plan.link == ((5, "U_A"),)


# --------------------------------------------------------------------------- #
# The same rules apply to creation — a new row is a new way in
# --------------------------------------------------------------------------- #
def test_bots_and_guests_are_never_created() -> None:
    plan = _plan(
        [
            _member("U_BOT", "bot@x.jp", is_bot=True),
            _member("U_GUEST", "g@x.jp", is_restricted=True),
            _member("U_SINGLE", "s@x.jp", is_ultra_restricted=True),
            _member("USLACKBOT", "sb@x.jp"),
        ]
    )

    assert plan.create == ()
    assert plan.skipped["not_a_member"] == 4


def test_a_member_from_another_workspace_is_never_created() -> None:
    plan = _plan([_member("U_X", "x@x.jp", team_id="T_OTHER")])

    assert plan.create == ()


def test_the_admin_address_is_never_created() -> None:
    """The admin principal is deliberately not an employee row. Creating one for
    that address from Slack would invent exactly the row it must not have."""

    plan = _plan([_member("U_X", "admin@tekijin.local")])

    assert plan.create == ()
    assert plan.skipped["admin_address"] == 1


def test_a_member_without_an_email_is_never_created() -> None:
    plan = _plan([_member("U_X", None)])

    assert plan.create == ()
    assert plan.skipped["no_email"] == 1


def test_a_deactivated_member_is_not_created() -> None:
    """Someone who has already left should not be brought into existence by the
    run that notices they are gone."""

    plan = _plan([_member("U_GONE", "gone@x.jp", deleted=True)])

    assert plan.create == ()


def test_two_members_claiming_one_new_address_create_nobody() -> None:
    """The fourth-variant rule, applied to creation. Two Slack accounts with the
    same address must not race to become the same new colleague — and since the
    row does not exist yet, whichever won would own it outright."""

    plan = _plan([_member("U_1", "new@x.jp"), _member("U_2", "new@x.jp")])

    assert plan.create == ()
    assert plan.skipped["ambiguous_email"] == 2


def test_the_same_member_listed_twice_is_created_once() -> None:
    plan = _plan([_member("U_1", "new@x.jp"), _member("U_1", "new@x.jp")])

    assert len(plan.create) == 1


def test_a_creation_does_not_block_an_unrelated_link() -> None:
    plan = _plan(
        [_member("U_NEW", "new@x.jp"), _member("U_OLD", "known@x.jp")],
        employee_id_by_email={"known@x.jp": 5},
    )

    assert plan.create == (("new@x.jp", "U_NEW", "U_NEW"),)
    assert plan.link == ((5, "U_OLD"),)


# --------------------------------------------------------------------------- #
# Narrowing who may be brought into existence
# --------------------------------------------------------------------------- #
def test_only_the_configured_domains_may_be_created() -> None:
    """Creation mints an identity, so the address it is keyed on should look like
    a company address. Without this, any workspace member — a contractor, a
    partner, anyone invited once — can be turned into an employee by the address
    on their profile."""

    plan = _plan(
        [
            _member("U_IN", "someone@sample-tekijin.co.jp"),
            _member("U_OUT", "someone@gmail.com"),
        ],
        allowed_domains=("sample-tekijin.co.jp",),
    )

    assert plan.create == (("someone@sample-tekijin.co.jp", "U_IN", "U_IN"),)
    assert plan.skipped["email_domain_not_allowed"] == 1


def test_the_domain_check_is_case_insensitive_and_anchored() -> None:
    """`endswith` on a bare domain would accept `sample-tekijin.co.jp.evil.com`
    — and, worse, `notsample-tekijin.co.jp`."""

    plan = _plan(
        [
            _member("U_A", "a@SAMPLE-TEKIJIN.CO.JP"),
            _member("U_B", "b@sample-tekijin.co.jp.evil.com"),
            _member("U_C", "c@notsample-tekijin.co.jp"),
        ],
        allowed_domains=("sample-tekijin.co.jp",),
    )

    assert [slack_user_id for _, _, slack_user_id in plan.create] == ["U_A"]
    assert plan.skipped["email_domain_not_allowed"] == 2


def test_an_empty_domain_list_allows_any_address() -> None:
    """The default. Restricting is opt-in, like every other switch here, so
    turning creation on does not silently start refusing people."""

    plan = _plan([_member("U_ANY", "someone@anywhere.example")], allowed_domains=())

    assert len(plan.create) == 1
