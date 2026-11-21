"""Deploy FreelanceEscrow with the configured wallet and record the evidence.

Run by CI with the ``studionet_wallet`` network, so the deployer is the wallet
in the ACCOUNT_PRIVATE_KEY_1 secret and the address belongs to it. The JSON and
markdown files land in ``deployments/`` and are committed back to the repo, so
the portal submission can point at a real deployment instead of a claim.

The freelancer is a fixed placeholder account: a real job would come from the
client and freelancer agreeing on their own addresses.
"""

import hashlib
import json
import pathlib
from datetime import datetime, timezone

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.utils import extract_contract_address

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "freelance_escrow.py"
OUT = ROOT / "deployments"
NAME = "freelance-escrow"

FREELANCER = "0x" + "22" * 20
JOB_TITLE = "Landing page produk"
JOB_SPEC = "Buat landing page sederhana dengan section hero dan form kontak."


def _receipt_json(receipt):
    try:
        return json.loads(json.dumps(receipt, default=str))
    except (TypeError, ValueError):
        return {"repr": str(receipt)}


def test_deploy_and_record(default_account):
    source = CONTRACT.read_bytes()
    digest = hashlib.sha256(source).hexdigest()

    receipt = get_contract_factory("FreelanceEscrow").deploy_contract_tx(
        args=[JOB_TITLE, JOB_SPEC, FREELANCER]
    )
    assert tx_execution_succeeded(receipt)

    address = extract_contract_address(receipt)
    assert address

    record = {
        "contract": "FreelanceEscrow",
        "source": "contracts/freelance_escrow.py",
        "source_sha256": digest,
        "network": "studionet",
        "chain_id": 61999,
        "rpc": "https://studio.genlayer.com/api",
        "address": address,
        "deployer": default_account.address,
        "deployed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "constructor_args": {
            "job_title": JOB_TITLE,
            "job_spec": JOB_SPEC,
            "freelancer": FREELANCER,
        },
        "deploy_receipt": _receipt_json(receipt),
    }

    OUT.mkdir(exist_ok=True)
    (OUT / (NAME + ".studionet.json")).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    (OUT / (NAME + ".studionet.md")).write_text(
        "# FreelanceEscrow — studionet deployment\n\n"
        "| | |\n|---|---|\n"
        "| Contract | `FreelanceEscrow` |\n"
        "| Address | `" + address + "` |\n"
        "| Network | studionet (chain id 61999) |\n"
        "| Deployer | `" + default_account.address + "` |\n"
        "| Deployed at | " + record["deployed_at"] + " |\n"
        "| Source | `contracts/freelance_escrow.py` |\n"
        "| Source sha256 | `" + digest + "` |\n"
    )
