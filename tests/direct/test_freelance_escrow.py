"""Direct mode tests for contracts/freelance_escrow.py.

Web pages and LLM verdicts are mocked. The tests cover the milestone state
machine, the access rules, the per-criterion consensus, the deadline and
recovery guards, and the leader/validator comparison.

Payouts are pulled: ``judge_milestone``/``refund_milestone``/``recover_milestone``
credit a balance inside the contract and ``withdraw`` sends the GEN out. Direct
mode has no chain layer, so that last hop is skipped.
"""

import pytest

from tests.direct.conftest import to_hex

CONTRACT = "contracts/freelance_escrow.py"
GEN = 10 ** 18

FREELANCER = "0x" + "22" * 20
DELIVERABLE_URL = "https://example.com/demo"
DELIVERABLE_PAGE = (
    "<html><body><h1>Hero headline</h1><p>Product subheadline.</p>"
    "<a class='cta' href='#contact'>Contact us</a></body></html>"
)

# Two acceptance criteria, one per line: the contract judges each of them.
CRITERIA = "A clear headline\nA call-to-action button"

DELIVERY_DEADLINE = 2_000_000_000          # 2033-05-18, after the test clock
AFTER_DEADLINE = "2033-07-01T00:00:00Z"    # past deadline + recovery grace

APPROVE_ANSWER = (
    '{"criteria": [{"id": 1, "met": true, "evidence": "Hero headline"},'
    ' {"id": 2, "met": true, "evidence": "Contact us"}],'
    ' "score": 92, "reason": "Both criteria are visible on the page."}'
)
REJECT_ANSWER = (
    '{"criteria": [{"id": 1, "met": true, "evidence": "Hero headline"},'
    ' {"id": 2, "met": false, "evidence": "no button"}],'
    ' "score": 20, "reason": "The call-to-action button is missing."}'
)
FLIPPED_ANSWER = (
    '{"criteria": [{"id": 1, "met": false, "evidence": "no headline"},'
    ' {"id": 2, "met": true, "evidence": "Contact us"}],'
    ' "score": 40, "reason": "The headline is missing."}'
)
LOW_SCORE_ALL_MET = (
    '{"criteria": [{"id": 1, "met": true}, {"id": 2, "met": true}],'
    ' "score": 5, "reason": "Complete, but the subjective score is low."}'
)


def deploy_escrow(direct_vm, direct_deploy, sender):
    direct_vm.sender = sender
    return direct_deploy(
        CONTRACT,
        "Product landing page",
        "Build a landing page with a hero section, pricing, and a contact form.",
        FREELANCER,
    )


def add_and_fund(contract, direct_vm, amount=GEN):
    contract.add_milestone("Hero section", CRITERIA, amount, DELIVERY_DEADLINE)
    direct_vm.value = amount
    contract.fund_milestone(0)
    direct_vm.value = 0


def act_as_freelancer(direct_vm):
    from genlayer.py.types import Address

    direct_vm.sender = Address(FREELANCER)


def act_as_client(direct_vm, client):
    direct_vm.sender = client


def mock_judgement(direct_vm, llm_answer, page=DELIVERABLE_PAGE):
    direct_vm.mock_web(r"example\.com/demo", {"status": 200, "body": page})
    direct_vm.mock_llm(r"impartial adjudicator", llm_answer)


def submit(contract, note="Hero section is done"):
    contract.submit_deliverable(0, DELIVERABLE_URL, note)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def test_deploy_stores_job_details(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    assert contract.get_job_title() == "Product landing page"
    assert "landing page" in contract.get_job_spec()
    assert to_hex(contract.get_client()) == to_hex(direct_alice)
    assert to_hex(contract.get_freelancer()) == FREELANCER
    assert contract.get_milestone_count() == 0
    assert contract.get_funded_total() == 0
    assert contract.get_withdrawn_total() == 0


def test_add_milestone_starts_in_draft(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    index = contract.add_milestone("Hero section", CRITERIA, GEN, DELIVERY_DEADLINE)

    assert index == 0
    assert contract.get_milestone_count() == 1
    assert contract.get_milestone_title(0) == "Hero section"
    assert contract.get_milestone_amount(0) == GEN
    assert contract.get_milestone_state(0) == "draft"
    assert contract.get_milestone_delivery_deadline(0) == DELIVERY_DEADLINE
    assert contract.get_recovery_opens_at(0) > DELIVERY_DEADLINE
    assert contract.get_milestone_attempts_left(0) == 3


def test_add_milestone_rejects_a_past_deadline(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    with direct_vm.expect_revert("delivery deadline must be in the future"):
        contract.add_milestone("Hero section", CRITERIA, GEN, 1_000_000_000)


def test_fund_milestone_moves_it_to_funded(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    assert contract.get_milestone_state(0) == "funded"
    assert contract.get_funded_total() == GEN


def test_fund_with_wrong_amount_reverts(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    contract.add_milestone("Hero section", CRITERIA, GEN, DELIVERY_DEADLINE)

    direct_vm.value = GEN - 1
    with direct_vm.expect_revert("send exactly the milestone amount"):
        contract.fund_milestone(0)
    direct_vm.value = 0


def test_only_client_can_add_milestone(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    act_as_freelancer(direct_vm)

    with direct_vm.expect_revert("only the client can add milestones"):
        contract.add_milestone("Hero section", CRITERIA, GEN, DELIVERY_DEADLINE)


def test_only_freelancer_can_submit(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    with direct_vm.expect_revert("only the freelancer can submit work"):
        submit(contract)


def test_client_can_extend_the_deadline(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    contract.extend_deadline(0, DELIVERY_DEADLINE + 86400)

    assert contract.get_milestone_delivery_deadline(0) == DELIVERY_DEADLINE + 86400

    with direct_vm.expect_revert("must be later than the current one"):
        contract.extend_deadline(0, DELIVERY_DEADLINE)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def test_freelancer_submits_deliverable(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract, note="First version")

    assert contract.get_milestone_state(0) == "submitted"
    assert contract.get_milestone_attempts(0) == 1
    assert contract.get_milestone_attempts_left(0) == 2
    assert contract.get_milestone_deliverable_url(0) == DELIVERABLE_URL
    assert contract.get_milestone_note(0) == "First version"


def test_submit_after_the_deadline_reverts(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    direct_vm.warp(AFTER_DEADLINE)
    act_as_freelancer(direct_vm)

    with direct_vm.expect_revert("the delivery deadline has passed"):
        submit(contract)


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------

def test_judge_approves_every_criterion_and_credits_the_freelancer(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, APPROVE_ANSWER)
    act_as_client(direct_vm, direct_alice)
    verdict = contract.judge_milestone(0)

    assert verdict == "approved"
    assert contract.get_milestone_state(0) == "approved"
    assert contract.get_milestone_checks(0) == "1=1;2=1;"
    assert contract.get_milestone_score(0) == 92
    assert contract.get_released_total() == GEN
    # The freelancer now has a claim on the escrow, not a transfer yet.
    assert contract.get_owed(FREELANCER) == GEN
    assert contract.get_withdrawn_total() == 0


def test_judge_rejects_when_a_criterion_is_not_met(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, REJECT_ANSWER)
    act_as_client(direct_vm, direct_alice)
    verdict = contract.judge_milestone(0)

    assert verdict == "revision_required"
    assert contract.get_milestone_state(0) == "revision_required"
    assert contract.get_milestone_checks(0) == "1=1;2=0;"
    assert contract.get_milestone_reason(0) != ""
    assert contract.get_released_total() == 0


def test_verdict_is_derived_from_the_criteria_not_the_score(
    direct_vm, direct_deploy, direct_alice
):
    """A low score cannot reject work whose criteria are all met."""
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, LOW_SCORE_ALL_MET)
    act_as_client(direct_vm, direct_alice)

    assert contract.judge_milestone(0) == "approved"
    assert contract.get_milestone_checks(0) == "1=1;2=1;"
    assert contract.get_milestone_score(0) == 5


def test_three_rejections_mark_the_milestone_failed(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    mock_judgement(direct_vm, REJECT_ANSWER)

    for _ in range(3):
        submit(contract)
        contract.judge_milestone(0)

    assert contract.get_milestone_state(0) == "failed"
    assert contract.get_milestone_attempts(0) == 3
    assert contract.get_milestone_attempts_left(0) == 0
    assert contract.get_my_owed() == 0

    # A failed milestone leaves the submission window entirely.
    with direct_vm.expect_revert("milestone is not open for a submission"):
        submit(contract)


def test_client_refunds_a_failed_milestone(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    mock_judgement(direct_vm, REJECT_ANSWER)
    for _ in range(3):
        submit(contract)
        contract.judge_milestone(0)

    act_as_client(direct_vm, direct_alice)
    contract.refund_milestone(0)

    assert contract.get_milestone_state(0) == "refunded"
    assert contract.get_owed(to_hex(direct_alice)) == GEN


def test_refund_requires_a_failed_milestone(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    with direct_vm.expect_revert("only a failed milestone can be refunded"):
        contract.refund_milestone(0)


def test_outsider_cannot_judge(direct_vm, direct_deploy, direct_alice, direct_charlie):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("only the client or the freelancer"):
        contract.judge_milestone(0)


# ---------------------------------------------------------------------------
# Recovery: funded GEN can never stay locked
# ---------------------------------------------------------------------------

def test_recover_before_the_window_reverts(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    with direct_vm.expect_revert("the recovery window has not opened yet"):
        contract.recover_milestone(0)


def test_recover_refunds_an_undelivered_milestone(
    direct_vm, direct_deploy, direct_alice, direct_charlie
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    direct_vm.warp(AFTER_DEADLINE)
    direct_vm.sender = direct_charlie          # anyone may trigger recovery
    contract.recover_milestone(0)

    assert contract.get_milestone_state(0) == "expired"
    assert contract.get_owed(to_hex(direct_alice)) == GEN
    assert contract.get_owed(FREELANCER) == 0


def test_recover_splits_work_that_was_never_judged(
    direct_vm, direct_deploy, direct_alice, direct_charlie
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    direct_vm.warp(AFTER_DEADLINE)
    direct_vm.sender = direct_charlie
    contract.recover_milestone(0)

    assert contract.get_milestone_state(0) == "expired"
    assert contract.get_owed(FREELANCER) == GEN // 2
    assert contract.get_owed(to_hex(direct_alice)) == GEN - GEN // 2


def test_recover_refunds_after_the_last_rejection(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)
    mock_judgement(direct_vm, REJECT_ANSWER)
    contract.judge_milestone(0)

    direct_vm.warp(AFTER_DEADLINE)
    contract.recover_milestone(0)

    assert contract.get_milestone_state(0) == "expired"
    assert contract.get_owed(to_hex(direct_alice)) == GEN


def test_recover_cannot_touch_a_settled_milestone(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)
    mock_judgement(direct_vm, APPROVE_ANSWER)
    contract.judge_milestone(0)

    direct_vm.warp(AFTER_DEADLINE)

    with direct_vm.expect_revert("milestone is already settled"):
        contract.recover_milestone(0)


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def test_validator_agrees_with_identical_mocks(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, APPROVE_ANSWER)
    contract.judge_milestone(0)

    assert direct_vm.run_validator() is True


def test_validator_disagrees_when_a_criterion_flips(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, APPROVE_ANSWER)
    contract.judge_milestone(0)

    # Same page, different checklist: no consensus.
    direct_vm.clear_mocks()
    mock_judgement(direct_vm, FLIPPED_ANSWER)

    assert direct_vm.run_validator() is False


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------

def test_withdraw_without_a_balance_reverts(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    with direct_vm.expect_revert("nothing to withdraw"):
        contract.withdraw()


@pytest.mark.skip(
    reason="direct mode has no chain layer: balances and external messages are "
    "not emulated, so the transfer hop can only be checked on Studio or testnet"
)
def test_withdraw_sends_gen(direct_vm, direct_deploy, direct_alice):  # pragma: no cover
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)
    mock_judgement(direct_vm, APPROVE_ANSWER)
    contract.judge_milestone(0)

    assert contract.withdraw() == GEN
