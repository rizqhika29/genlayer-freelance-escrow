"""Direct mode tests for contracts/freelance_escrow.py.

Web pages and LLM verdicts are mocked. The tests cover the milestone state
machine, the access rules, and the leader/validator comparison.

Payouts are pulled: ``judge_milestone``/``refund_milestone`` credit a balance
inside the contract and ``withdraw`` sends the GEN out. Direct mode has no chain
layer, so that last hop is skipped.
"""

import pytest

from tests.direct.conftest import to_hex

CONTRACT = "contracts/freelance_escrow.py"
GEN = 10 ** 18

FREELANCER = "0x" + "22" * 20
DELIVERABLE_URL = "https://example.com/demo"
DELIVERABLE_PAGE = (
    "<html><body><h1>Hero headline</h1><p>Subheadline produk.</p>"
    "<a class='cta' href='#kontak'>Hubungi kami</a></body></html>"
)

APPROVE_ANSWER = (
    '{"verdict": "approve", "score": 92, '
    '"reason": "Headline, subheadline, dan tombol CTA terlihat di halaman."}'
)
REJECT_ANSWER = (
    '{"verdict": "reject", "score": 20, '
    '"reason": "Halaman tidak memuat daftar harga."}'
)


def deploy_escrow(direct_vm, direct_deploy, sender):
    direct_vm.sender = sender
    return direct_deploy(
        CONTRACT,
        "Landing page produk",
        "Buat landing page dengan section hero, daftar harga, dan form kontak.",
        FREELANCER,
    )


def add_and_fund(contract, direct_vm, amount=GEN):
    contract.add_milestone(
        "Section hero",
        "Ada headline, subheadline, dan tombol CTA yang jelas.",
        amount,
    )
    direct_vm.value = amount
    contract.fund_milestone(0)
    direct_vm.value = 0


def act_as_freelancer(direct_vm):
    from genlayer.py.types import Address

    direct_vm.sender = Address(FREELANCER)


def mock_judgement(direct_vm, llm_answer, page=DELIVERABLE_PAGE):
    direct_vm.mock_web(r"example\.com/demo", {"status": 200, "body": page})
    direct_vm.mock_llm(r"impartial adjudicator", llm_answer)


def submit(contract, note="Section hero sudah jadi"):
    contract.submit_deliverable(0, DELIVERABLE_URL, note)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def test_deploy_stores_job_details(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    assert contract.get_job_title() == "Landing page produk"
    assert "landing page" in contract.get_job_spec()
    assert to_hex(contract.get_client()) == to_hex(direct_alice)
    assert to_hex(contract.get_freelancer()) == FREELANCER
    assert contract.get_milestone_count() == 0
    assert contract.get_funded_total() == 0
    assert contract.get_withdrawn_total() == 0


def test_add_milestone_starts_in_draft(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    index = contract.add_milestone("Section hero", "Ada headline dan CTA", GEN)

    assert index == 0
    assert contract.get_milestone_count() == 1
    assert contract.get_milestone_title(0) == "Section hero"
    assert contract.get_milestone_amount(0) == GEN
    assert contract.get_milestone_state(0) == "draft"
    assert contract.get_milestone_attempts_left(0) == 3


def test_fund_milestone_moves_it_to_funded(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    assert contract.get_milestone_state(0) == "funded"
    assert contract.get_funded_total() == GEN


def test_fund_with_wrong_amount_reverts(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    contract.add_milestone("Section hero", "Ada headline dan CTA", GEN)

    direct_vm.value = GEN - 1
    with direct_vm.expect_revert("send exactly the milestone amount"):
        contract.fund_milestone(0)
    direct_vm.value = 0


def test_only_client_can_add_milestone(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)

    act_as_freelancer(direct_vm)

    with direct_vm.expect_revert("only the client can add milestones"):
        contract.add_milestone("Section hero", "Ada headline dan CTA", GEN)


def test_only_freelancer_can_submit(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    with direct_vm.expect_revert("only the freelancer can submit work"):
        submit(contract)


# ---------------------------------------------------------------------------
# Delivery and adjudication
# ---------------------------------------------------------------------------

def test_freelancer_submits_deliverable(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract, note="Versi pertama")

    assert contract.get_milestone_state(0) == "submitted"
    assert contract.get_milestone_attempts(0) == 1
    assert contract.get_milestone_attempts_left(0) == 2
    assert contract.get_milestone_deliverable_url(0) == DELIVERABLE_URL
    assert contract.get_milestone_note(0) == "Versi pertama"


def test_judge_rejects_and_asks_for_revision(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, REJECT_ANSWER)
    direct_vm.sender = direct_alice
    verdict = contract.judge_milestone(0)

    assert verdict == "revision_required"
    assert contract.get_milestone_state(0) == "revision_required"
    assert contract.get_milestone_score(0) == 20
    assert contract.get_milestone_reason(0) != ""
    assert contract.get_released_total() == 0


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


def test_judge_approves_and_credits_the_freelancer(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    mock_judgement(direct_vm, APPROVE_ANSWER)
    direct_vm.sender = direct_alice
    verdict = contract.judge_milestone(0)

    assert verdict == "approved"
    assert contract.get_milestone_state(0) == "approved"
    assert contract.get_milestone_score(0) == 92
    assert contract.get_released_total() == GEN
    # The freelancer now has a claim on the escrow, not a transfer yet.
    assert contract.get_owed(FREELANCER) == GEN
    assert contract.get_withdrawn_total() == 0


def test_client_refunds_a_failed_milestone(direct_vm, direct_deploy, direct_alice):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    mock_judgement(direct_vm, REJECT_ANSWER)
    for _ in range(3):
        submit(contract)
        contract.judge_milestone(0)

    direct_vm.sender = direct_alice
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


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def test_validator_disagrees_when_verdict_differs(
    direct_vm, direct_deploy, direct_alice
):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    # The leader rejects, the validator re-runs and approves: no consensus.
    mock_judgement(direct_vm, REJECT_ANSWER)
    contract.judge_milestone(0)

    direct_vm.clear_mocks()
    mock_judgement(direct_vm, APPROVE_ANSWER)

    assert direct_vm.run_validator() is False


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


def test_outsider_cannot_judge(direct_vm, direct_deploy, direct_alice, direct_charlie):
    contract = deploy_escrow(direct_vm, direct_deploy, direct_alice)
    add_and_fund(contract, direct_vm)

    act_as_freelancer(direct_vm)
    submit(contract)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("only the client or the freelancer"):
        contract.judge_milestone(0)
