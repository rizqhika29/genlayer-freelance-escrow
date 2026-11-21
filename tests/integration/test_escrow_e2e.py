"""Integration tests for FreelanceEscrow on GenLayer Studio (studionet).

These run against a real network: real deploy, real transactions, real consensus
pipeline. The validators are mocked (deterministic web + LLM answers) so the
tests assert the contract's behaviour, not the model's opinion, and they cost no
LLM tokens.

The datetime is pinned with ``genvm_datetime`` so the deadline and recovery
windows are reproducible instead of depending on wall-clock time.

Note on the client API: a contract method accepts only ``args``; the caller is
selected with ``contract.connect(account)`` and the transaction context is passed
to ``.transact()`` / ``.call()``.
"""

import json

import pytest

from gltest import get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_failed, tx_execution_succeeded

CONTRACT = "FreelanceEscrow"
GEN = 10 ** 18

URL = "https://example.com/"
PAGE = "<html><body><h1>Example Domain</h1></body></html>"

T0 = "2030-01-01T00:00:00Z"
T0_UNIX = 1_893_456_000                     # 2030-01-01T00:00:00Z
DELIVERY_DEADLINE = T0_UNIX + 7 * 24 * 3600
AFTER_RECOVERY = "2030-03-01T00:00:00Z"     # past deadline + 14 days

APPROVE_ANSWER = json.dumps(
    {
        "criteria": [{"id": 1, "met": True, "evidence": "Example Domain"}],
        "score": 90,
        "reason": "The page shows the text Example Domain.",
    }
)


@pytest.fixture(scope="module")
def ctx():
    factory = get_validator_factory()
    validators = factory.batch_create_mock_validators(
        count=5,
        mock_llm_response={"nondet_exec_prompt": {"impartial adjudicator": APPROVE_ANSWER}},
        mock_web_response={
            "nondet_web_request": {
                URL: {"method": "GET", "status": 200, "body": PAGE},
            }
        },
    )
    return {
        "validators": [v.to_dict() for v in validators],
        "genvm_datetime": T0,
    }


def at_time(ctx, iso):
    """Same mocked environment, different pinned transaction datetime."""
    shifted = dict(ctx)
    shifted["genvm_datetime"] = iso
    return shifted


def deploy(ctx, freelancer):
    factory = get_contract_factory(CONTRACT)
    return factory.deploy(
        args=["Landing page", "Build a simple landing page.", freelancer.address],
        transaction_context=ctx,
    )


def add_and_fund(ctx, contract):
    tx = contract.add_milestone(
        args=["Hero", "The page shows the text Example Domain", GEN, DELIVERY_DEADLINE]
    ).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    tx = contract.fund_milestone(args=[0]).transact(value=GEN, transaction_context=ctx)
    assert tx_execution_succeeded(tx)


def submit(ctx, contract, freelancer, note="done"):
    return contract.connect(freelancer).submit_deliverable(
        args=[0, URL, note]
    ).transact(transaction_context=ctx)


# ---------------------------------------------------------------------------
# End to end: delivery, consensus, credit
# ---------------------------------------------------------------------------

def test_approval_flow_pays_the_freelancer(ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    assert tx_execution_succeeded(submit(ctx, contract, freelancer))

    tx = contract.judge_milestone(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=ctx) == "approved"
    assert contract.get_milestone_checks(args=[0]).call(transaction_context=ctx) == "1=1;"
    assert contract.get_owed(args=[freelancer.address]).call(transaction_context=ctx) == GEN
    assert contract.get_funded_total().call(transaction_context=ctx) == GEN
    assert contract.get_balance().call(transaction_context=ctx) >= GEN


# ---------------------------------------------------------------------------
# Adversarial: callers who should not be able to act
# ---------------------------------------------------------------------------

def test_outsider_cannot_submit_or_judge(ctx, default_account, accounts):
    freelancer = accounts[1]
    outsider = accounts[2]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    tx = submit(ctx, contract, outsider, note="sneaking in")
    assert tx_execution_failed(tx)

    tx = contract.connect(outsider).judge_milestone(args=[0]).transact(
        transaction_context=ctx
    )
    assert tx_execution_failed(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=ctx) == "funded"


def test_double_submit_is_rejected(ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    assert tx_execution_succeeded(submit(ctx, contract, freelancer, note="first"))
    assert tx_execution_failed(submit(ctx, contract, freelancer, note="second"))

    assert contract.get_milestone_attempts(args=[0]).call(transaction_context=ctx) == 1
    assert contract.get_milestone_note(args=[0]).call(transaction_context=ctx) == "pertama"


def test_double_judge_is_rejected(ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    assert tx_execution_succeeded(submit(ctx, contract, freelancer))

    tx = contract.judge_milestone(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_succeeded(tx)

    tx = contract.judge_milestone(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_failed(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=ctx) == "approved"


def test_recovery_before_the_window_is_rejected(ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    tx = contract.recover_milestone(args=[0]).transact(transaction_context=ctx)
    assert tx_execution_failed(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=ctx) == "funded"


def test_recovery_after_the_window_refunds_the_client(ctx, default_account, accounts):
    freelancer = accounts[1]
    contract = deploy(ctx, freelancer)
    add_and_fund(ctx, contract)

    late = at_time(ctx, AFTER_RECOVERY)
    tx = contract.recover_milestone(args=[0]).transact(transaction_context=late)
    assert tx_execution_succeeded(tx)

    assert contract.get_milestone_state(args=[0]).call(transaction_context=late) == "expired"
    assert contract.get_owed(args=[default_account.address]).call(
        transaction_context=late
    ) == GEN
    assert contract.get_owed(args=[freelancer.address]).call(transaction_context=late) == 0
