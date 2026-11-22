# FreelanceEscrow — GenLayer Intelligent Contract

[![GenVM CI](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml/badge.svg)](https://github.com/rizqhika29/genlayer-freelance-escrow/actions/workflows/ci.yml)

Milestone escrow for freelance work. The question "does this deliverable meet the
acceptance criteria?" is not answered by a human arbiter but by GenLayer's
validator set: the contract fetches the deliverable page from the web, asks an
LLM to judge **every acceptance criterion separately**, and each validator
re-runs the same block and compares that judgement.

## Deployed contract (studionet)

| | |
|---|---|
| Network | studionet (chain id 61999) |
| RPC | https://studio.genlayer.com/api |
| Contract address | **`0xceCb2f0867ffB07bB400605321BF9d839a12c9EF`** |
| Deployer | `0xABa75FaB2Fa28B176C48aD99aeB833C09A5e15B9` |
| Deployed at | 2026-11-21T08:36:18Z |
| Source | `contracts/freelance_escrow.py` |
| Source sha256 | `085e9cfba9065bdd4e0ae0251c4e3f9d230b3e70d734e80d78b2ec1c7224fb1a` |
| Deployment record | [`deployments/freelance-escrow.studionet.md`](deployments/freelance-escrow.studionet.md) (JSON with the full receipt: [`deployments/freelance-escrow.studionet.json`](deployments/freelance-escrow.studionet.json)) |

The record is written by `tests/deploy/` when CI deploys from the wallet in the
`ACCOUNT_PRIVATE_KEY_1` secret, so the address above belongs to that wallet.

Dependency header used by the contract:

```py
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

## Flow

1. The client deploys the contract with `job_title`, `job_spec` and the
   freelancer address.
2. The client adds a milestone: `add_milestone(title, criteria, amount,
   delivery_deadline)`. Criteria are written one per line; the contract splits
   them into numbered items 1..N (at most 12).
3. The client funds the milestone: `fund_milestone(index)` with exactly `amount`
   GEN attached (payable, the amount must match).
4. The freelancer submits a link to the work before the deadline:
   `submit_deliverable(index, url, note)`.
5. The client or the freelancer calls `judge_milestone(index)`.
6. Approve credits the amount to the freelancer inside the contract. Reject lets
   the freelancer resubmit (at most 3 attempts); after the last rejection the
   client takes the money back with `refund_milestone(index)`.
7. Whoever is owed GEN calls `withdraw()` to receive it. Payouts are pulled, not
   pushed, so a failing transfer can be retried.

## How consensus works

```py
def leader_fn():
    rendered = gl.nondet.web.render(deliverable_url, mode="html")
    raw = gl.nondet.exec_prompt(prompt, response_format="json")
    return _parse_judgement(raw, item_count)     # {"verdict","checks","score","reason"}

def validator_fn(leaders_res) -> bool:
    own = leader_fn()                            # the validator re-runs it itself
    return (leader.get("checks") == own.get("checks")
            and leader.get("verdict") == own.get("verdict")
            and abs(leader.score - own.score) <= 20)
```

- **Every criterion must be judged.** The model has to return exactly one entry
  `{"id": n, "met": true|false}` per criterion. A missing, duplicate or
  out-of-range id is rejected as `[LLM_ERROR]` and the leader is rotated.
- **The verdict is not taken from the model.** `approve` only when every
  criterion is `met`, computed inside the contract. The model cannot pass a
  failed criterion.
- **What is compared:** the `checks` vector (`"1=1;2=0;"`) must match exactly, so
  validators have to agree on each criterion. The score is diagnostic with a
  20-point tolerance; the `reason` text is stored for display only and is never
  fed back into a later decision.
- **Not allowed inside the non-deterministic block:** writing storage, calling
  other contracts, or emitting messages. Everything is copied into plain locals
  first.
- **Leader errors:** `_handle_leader_error` separates deterministic errors (must
  match), transient errors `[TRANSIENT]` (both failing counts as agreement) and
  LLM errors `[LLM_ERROR]` (always disagree, forcing a new leader).
- **Prompt injection:** the page and the freelancer note are wrapped in
  `<<<DELIVERABLE ... DELIVERABLE>>>` with an explicit rule that the content is
  data, never instructions.

## API

Writes:

- `add_milestone(title: str, criteria: str, amount: u256, delivery_deadline: u256) -> u32`
  — client only; the deadline is unix seconds and must be in the future.
- `extend_deadline(index: u32, new_deadline: u256) -> None` — client only.
- `fund_milestone(index: u32) -> None` — payable, must send exactly `amount`.
- `submit_deliverable(index: u32, deliverable_url: str, note: str) -> None` — freelancer only, before the deadline.
- `judge_milestone(index: u32) -> str` — client or freelancer; returns
  `"approved"`, `"revision_required"` or `"failed"`.
- `refund_milestone(index: u32) -> None` — client only, for a `failed` milestone.
- `recover_milestone(index: u32) -> None` — anyone, after deadline + 14 days.
- `withdraw() -> u256` — sends the caller everything the escrow owes them.

Views:

- `get_client()`, `get_freelancer()`, `get_job_title()`, `get_job_spec()`
- `get_funded_total()`, `get_released_total()`, `get_withdrawn_total()`, `get_balance()`
- `get_my_owed()`, `get_owed(who)` — balance ready to withdraw
- `get_milestone_count()`
- `get_milestone_title/criteria/amount/state/deliverable_url/note/attempts/score/reason/checks(index)`
- `get_milestone_attempts_left(index)`, `get_milestone_delivery_deadline(index)`,
  `get_recovery_opens_at(index)`, `get_now()`

Milestone state: `draft` → `funded` → `submitted` → (`approved` |
`revision_required` → … | `failed` → `refunded`), plus `expired` for recovery.

## Security notes

- **Double submit / double payout.** Every transition is state guarded: submit
  only from `funded`/`revision_required`, judge only from `submitted`, refund
  only from `failed`, recovery only from `funded`/`submitted`/`revision_required`.
  A balance is credited once per milestone and `withdraw()` zeroes it before
  sending GEN (pull payment), so a failed transfer cannot pay twice.
- **Outside callers.** `add_milestone`, `fund_milestone`, `extend_deadline` and
  `refund_milestone` are client only; `submit_deliverable` is freelancer only;
  `judge_milestone` is open to the client and the freelancer (the freelancer can
  trigger a verdict, so the client cannot stall by never judging).
  `recover_milestone` is intentionally open to anyone because its amounts are
  deterministic, not discretionary, and it is what prevents locked funds.
- **Funds cannot stay locked.** If adjudication never reaches consensus (dead
  source) or the freelancer never delivers, `recover_milestone` opens after
  `delivery_deadline + 14 days`: nothing delivered → client gets 100%; the last
  verdict rejected the work → client gets 100%; work delivered but never judged →
  split evenly.
- **Input limits.** Title 200, brief 8,000, criteria 2,000 (max 12 lines), note
  2,000, URL 500 characters.
- **No floats.** Scores and amounts are integers; there is no `/` division that
  could crash the VM.

## Repository layout

```
contracts/freelance_escrow.py      the contract
tests/direct/                      unit tests (mocked web + LLM)
tests/integration/                 end-to-end and adversarial tests on studionet
tests/deploy/                      real deployment that records the evidence
deployments/                       committed deployment record (address, receipt)
scripts/write_env.py               builds .env for the wallet network
gltest.config.yaml                 keyless studionet config
gltest.wallet.config.yaml          wallet template (copied in by CI when the secret exists)
.github/workflows/ci.yml           lint, unit, integration and deploy jobs
```

## Tests

Three layers, all run by CI.

### 1. Direct mode (unit tests)

`pytest tests/direct -v` — the contract runs directly in Python, no Docker and no
network, about 1 second for 25 tests.

| Test | What it checks |
|---|---|
| `test_deploy_stores_job_details` | deployment stores client, freelancer, title, brief; counters start at zero |
| `test_add_milestone_starts_in_draft` | a new milestone is `draft`, stores amount, deadline and the recovery time |
| `test_add_milestone_rejects_a_past_deadline` | a deadline in the past is rejected |
| `test_fund_milestone_moves_it_to_funded` | funding moves `draft` → `funded` and adds to `funded_total` |
| `test_fund_with_wrong_amount_reverts` | paying a different amount than `amount` reverts |
| `test_only_client_can_add_milestone` | the freelancer cannot add milestones |
| `test_only_freelancer_can_submit` | the client cannot submit work |
| `test_client_can_extend_the_deadline` | the client can move the deadline forward, not backwards |
| `test_freelancer_submits_deliverable` | submission stores url + note, increments attempts, state → `submitted` |
| `test_submit_after_the_deadline_reverts` | submitting after the deadline is rejected |
| `test_judge_approves_every_criterion_and_credits_the_freelancer` | all criteria met → `approved`, `checks == "1=1;2=1;"`, balance credited |
| `test_judge_rejects_when_a_criterion_is_not_met` | one failed criterion → `revision_required`, `checks == "1=1;2=0;"` |
| `test_verdict_is_derived_from_the_criteria_not_the_score` | a low score with all criteria met still approves (the model cannot veto) |
| `test_three_rejections_mark_the_milestone_failed` | three rejections exhaust the attempts and the milestone becomes `failed` |
| `test_client_refunds_a_failed_milestone` | refund after `failed` credits the client |
| `test_refund_requires_a_failed_milestone` | refunding a non-failed milestone reverts |
| `test_outsider_cannot_judge` | a third account cannot trigger a judgement |
| `test_recover_before_the_window_reverts` | recovery before deadline + 14 days reverts |
| `test_recover_refunds_an_undelivered_milestone` | nothing delivered → the client gets 100% |
| `test_recover_splits_work_that_was_never_judged` | delivered but never judged → 50/50 split |
| `test_recover_refunds_after_the_last_rejection` | last verdict rejected → the client gets 100% |
| `test_recover_cannot_touch_a_settled_milestone` | an approved milestone cannot be recovered |
| `test_validator_agrees_with_identical_mocks` | identical web + LLM answers → validator accepts |
| `test_validator_disagrees_when_a_criterion_flips` | one flipped criterion → validator rejects |
| `test_withdraw_without_a_balance_reverts` | nothing owed → `withdraw()` reverts |
| `test_withdraw_sends_gen` (skipped) | the GEN transfer itself; direct mode has no chain layer, so this is checked on studionet |

Cheatcodes used: `direct_vm.sender`, `direct_vm.value`, `direct_vm.warp`,
`direct_vm.mock_web`, `direct_vm.mock_llm`, `direct_vm.expect_revert`,
`direct_vm.run_validator()`.

### 2. Integration and adversarial (studionet)

`gltest tests/integration -v --network studionet` — real deploy, real
transactions, real consensus pipeline (about 5 minutes).

| Test | What it checks |
|---|---|
| `test_approval_flow_pays_the_freelancer` | deploy → add milestone → fund → submit → judge → state `approved`, `checks == "1=1;"`, freelancer owed the amount |
| `test_outsider_cannot_submit_or_judge` | a third account cannot submit or judge; the milestone stays `funded` |
| `test_double_submit_is_rejected` | a second submission fails; attempts stay at 1 and the first note is kept |
| `test_double_judge_is_rejected` | judging twice fails; the first verdict stands |
| `test_recovery_before_the_window_is_rejected` | recovery before deadline + 14 days fails on chain |
| `test_recovery_after_the_window_refunds_the_client` | recovery after the window expires the milestone and credits the client |

The suite mocks the validators (`batch_create_mock_validators` with deterministic
web + LLM answers), so results are reproducible and cost no LLM tokens, while
still going through the full consensus pipeline. Each transaction pins
`genvm_datetime`, so deadlines and the recovery window do not depend on the wall
clock.

Two config files:

- `gltest.config.yaml` — used as-is; accounts are generated by the SDK, no key
  needed, so anyone who clones the repo can run the suite.
- `gltest.wallet.config.yaml` — the `studionet_wallet` template (chain id 61999)
  that reads three accounts from `.env`. CI copies it over `gltest.config.yaml`
  and writes `.env` through `scripts/write_env.py` only when the secret exists:
  the client is your wallet (`ACCOUNT_PRIVATE_KEY_1`), while the freelancer and
  the outsider are deterministic child keys derived from it, so one secret is
  enough for the three-party tests.

### 3. Method coverage (studionet)

`tests/integration/test_methods_e2e.py` calls every remaining public method once
on the real network with the wallet, including the GEN transfer that direct mode
cannot simulate.

| Test | What it checks |
|---|---|
| `test_every_view_answers` | client, freelancer, title, brief, counters, balance, owed and `get_now` all answer on a fresh contract |
| `test_milestone_views` | title, criteria, amount, state, attempts, attempts left, deadline, recovery time, score, reason, checks, url and note for one milestone |
| `test_extend_deadline` | the client moves the deadline and the new value is stored |
| `test_three_rejections_then_refund` | three rejected submissions end in `failed`, then `refund_milestone` credits the client |
| `test_withdraw_moves_the_gen` | approval credits the freelancer, `withdraw()` sends the GEN, the owed balance returns to zero and `withdrawn_total` records it |

### 4. Deployment test

`gltest tests/deploy -v --network studionet_wallet` — deploys the contract from
the configured wallet and writes `deployments/freelance-escrow.studionet.json`
and `.md` containing the address, deployer, chain id, timestamp, source sha256
and the full deploy receipt. CI commits that record, which is the deployment
evidence linked in the GenLayer portal submission.

## CI

`.github/workflows/ci.yml` runs on every push, every pull request, and on demand:

| Job | What it does |
|---|---|
| `lint` | `genvm-lint check` plus `genvm-lint schema`, uploading the ABI as an artifact |
| `test` | `pytest tests/direct -v` |
| `integration` | `gltest tests/integration --network studionet_wallet` when the secret exists, otherwise the keyless `studionet` |
| `deploy` | manual (`workflow_dispatch`) real deployment from the wallet, then commits `deployments/` |

The key never enters the repo or the chat: put it in *Settings → Secrets and
variables → Actions* as `ACCOUNT_PRIVATE_KEY_1` (the hex private key of your
studionet wallet). `.env` is git-ignored. For Bradbury, add a network entry that
uses the same `${ACCOUNT_PRIVATE_KEY_1}`.

## Deploy yourself

**Studio** — open [studio.genlayer.com](https://studio.genlayer.com), paste the
contract, deploy with `job_title`, `job_spec` and the freelancer address (hex).

**CLI** — `genlayer init && genlayer up` for a local Studio, or use `gltest` for
the integration suite.

## Notes

- The deliverable page is fetched with `gl.nondet.web.render(url, mode="html")`
  and truncated to 12,000 characters. JavaScript-heavy sites may render
  incompletely; point the contract at a static page, a JSON API or a raw file.
- Time comes from the transaction timestamp (`datetime.now(timezone.utc)`), the
  same value on every validator, not the host clock.
- GEN is sent to EOAs with `@gl.evm.contract_interface` + `emit_transfer` (only
  executed on finalization). Amounts are in wei (1 GEN = 10^18 wei).
- Redeploying creates a new address and fresh state; changing the source does not
  change an already deployed contract.
