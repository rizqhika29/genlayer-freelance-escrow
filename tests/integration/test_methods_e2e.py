"""Per-method integration tests for every public method, on studionet with the
wallet network.

The flow suites cover the happy path and the guards; this file touches each
remaining method once on the real network, including the GEN transfer in
``withdraw()`` that direct mode cannot simulate.
"""

import json

import pytest

from gltest import get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_succeeded

CONTRACT = "FreelanceEscrow"
GEN = 10 ** 18

URL = "https://example.com/"
PAGE = "<html><body><h1>Example Domain</h1></body></html>"

T0 = "2030-01-01T00:00:00Z"
T0_UNIX = 1_893_456_000
DEADLINE = T0_UNIX + 7 * 24 * 3600

JOB_TITLE = "Landing page"
JOB_SPEC = "Build a landing page."
CRITERION = "The page shows the text Example Domain"

APPROVE = json.dumps(
    {
        "criteria": [{"id": 1, "met": True, "evidence": "Example Domain"}],
        "score": 90,
        "reason": "The page shows the text.",
    }
)
REJECT = json.dumps(
    {
        "criteria": [{"id": 1, "met": False, "evidence": "not shown"}],
        "score": 10,
        "reason": "The page does not show it.",
    }
)


def _context(answer):
    factory = get_validator_factory()
    validators = factory.batch_create_mock_validators(
        count=5,
        mock_llm_response={"nondet_exec_prompt": {"impartial adjudicator": answer}},
        mock_web_response={
            "nondet_web_request": {URL: {"method": "GET", "status": 200, "body": PAGE}}
        },
    )
    return {
        "validators": [v.to_dict() for v in validators],
        "genvm_datetime": T0,
    }


@pytest.fixture(scope="module")
def ctx():
    return _context(APPROVE)


@pytest.fixture(scope="module")
def reject_ctx():
    return _context(REJECT)


def deploy(ctx, freelancer):
    return get_contract_factory(CONTRACT).deploy(
        args=[JOB_TITLE, JOB_SPEC, freelancer.address],
        transaction_context=ctx,
    )


def add_and_fund(ctx, contract):
    tx = contract.add_milestone(
        args=["Hero", CRITERION, GEN, DEADLINE]
    ).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    tx = contract.fund_milestone(args=[0]).transact(value=GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)


def same_address(value, expected):
    return str(value).lower() == expected.lower()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def test_every_view_answers(ctx, default_account, accounts):
    contract = deploy(ctx, accounts[1])

    assert same_address(contract.get_client().call(transaction_context=ctx), default_account.address)
    assert same_address(contract.get_freelancer().call(transaction_context=ctx), accounts[1].address)
    assert contract.get_job_title().call(transaction_context=ctx) == JOB_TITLE
    assert contract.get_job_spec().call(transaction_context=ctx) == JOB_SPEC
    assert contract.get_milestone_count().call(transaction_context=ctx) == 0
    assert contract.get_funded_total().call(transaction_context=ctx) == 0
    assert contract.get_released_total().call(transaction_context=ctx) == 0
    assert contract.get_withdrawn_total().call(transaction_context=ctx) == 0
    assert contract.get_balance().call(transaction_context=ctx) == 0
    assert contract.get_my_owed().call(transaction_context=ctx) == 0
    assert contract.get_owed(args=[accounts[1].address]).call(transaction_context=ctx) == 0
    assert contract.get_now().call(transaction_context=ctx) > 0


def test_milestone_views(ctx, default_account, accounts):
    contract = deploy(ctx, accounts[1])
    add_and_fund(ctx, contract)

    assert contract.get_milestone_title(args=[0]).call(transaction_context=ctx) == "Hero"
    assert contract.get_milestone_criteria(args=[0]).call(transaction_context=ctx) == CRITERION
    assert contract.get_milestone_amount(args=[0]).call(transaction_context=ctx) == GEN
    assert contract.get_milestone_state(args=[0]).call(transaction_context=ctx) == "funded"
    assert contract.get_milestone_attempts(args=[0]).call(transaction_context=ctx) == 0
    assert contract.get_milestone_attempts_left(args=[0]).call(transaction_context=ctx) == 3
    assert contract.get_milestone_delivery_deadline(args=[0]).call(transaction_context=ctx) == DEADLINE
    assert contract.get_recovery_opens_at(args=[0]).call(transaction_context=ctx) > DEADLINE
    assert contract.get_milestone_score(args=[0]).call(transaction_context=ctx) == 0
    assert contract.get_milestone_reason(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_milestone_checks(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_milestone_deliverable_url(args=[0]).call(transaction_context=ctx) == ""
    assert contract.get_milestone_note(args=[0]).call(transaction_context=ctx) == ""


# ---------------------------------------------------------------------------
# Writes that the flow suite does not reach
# ---------------------------------------------------------------------------

def test_extend_deadline(ctx, default_account, accounts):
    contract = deploy(ctx, accounts[1])
    add_and_fund(ctx, contract)

    tx = contract.extend_deadline(args=[0, DEADLINE + 86400]).transact(
        transaction_context=ctx
    )
    assert tx_execution_succeeded(tx)
    assert contract.get_milestone_delivery_deadline(args=[0]).call(
        transaction_context=ctx
    ) == DEADLINE + 86400


def test_three_rejections_then_refund(ctx, reject_ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(reject_ctx, freelancer)
    add_and_fund(reject_ctx, contract)

    freelancer_contract = contract.connect(freelancer)
    for _ in range(3):
        tx = freelancer_contract.submit_deliverable(args=[0, URL, "done"]).transact(
            transaction_context=reject_ctx
        )
        assert tx_execution_succeeded(tx)
        tx = contract.judge_milestone(args=[0]).transact(transaction_context=reject_ctx)
        assert tx_execution_succeeded(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=reject_ctx) == "failed"
    assert contract.get_milestone_attempts(args=[0]).call(transaction_context=reject_ctx) == 3

    tx = contract.refund_milestone(args=[0]).transact(transaction_context=reject_ctx)
    assert tx_execution_succeeded(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=reject_ctx) == "refunded"
    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=reject_ctx
    ) == GEN


def test_withdraw_moves_the_gen(ctx, default_account, accounts):
    """The one hop direct mode cannot check: a real GEN transfer."""
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    tx = contract.connect(freelancer).submit_deliverable(args=[0, URL, "done"]).transact(
        transaction_context=ctx
    )
    assert tx_execution_succeeded(tx)

    tx = contract.judge_milestone(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)
    assert contract.get_owed(args=[freelancer.address]).call(transaction_context=ctx) == GEN

    tx = contract.connect(freelancer).withdraw().transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    assert contract.get_owed(args=[freelancer.address]).call(transaction_context=ctx) == 0
    assert contract.get_withdrawn_total().call(transaction_context=ctx) == GEN
