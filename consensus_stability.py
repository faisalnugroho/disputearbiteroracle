#!/usr/bin/env python3
"""Consensus-stability check: open + resolve 2 more disputes on Studionet."""
import json
from pathlib import Path
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

HERE = Path(__file__).resolve().parent
KEY_PATH = Path.home() / ".genlayer-keys" / "peer-review-oracle.json"
ADDR = "0xC977FB298bfE87457467faDdAce398D4533E98c9"

EVIDENCE = [
    "https://raw.githubusercontent.com/genlayerlabs/genlayer-project-boilerplate/main/README.md",
]

key_data = json.loads(KEY_PATH.read_text())
account = Account.from_key(key_data["private_key"])
client = create_client(chain=studionet, account=key_data["private_key"])
client.local_account = account

respondent_bytes = bytes.fromhex("567865452AfC3BDE935532f851D8952eDb6c8a8D")

results = []
for i in range(2):
    claim_c = f"Cycle {i}: client says the deliverable was late and incomplete; wants refund."
    claim_r = f"Cycle {i}: respondent says work was delivered on time and accepted."
    tx = client.write_contract(address=ADDR, function_name="open_dispute",
                               args=[respondent_bytes, claim_c, claim_r, EVIDENCE],
                               account=account)
    client.wait_for_transaction_receipt(transaction_hash=tx,
        status=TransactionStatus.ACCEPTED, full_transaction=True, retries=40, interval=3)

    # dispute ids are sequential: 1 already used, so these are 2 and 3
    did = 2 + i
    tx3 = client.write_contract(address=ADDR, function_name="resolve_dispute",
                                args=[did], account=account)
    rc3 = client.wait_for_transaction_receipt(transaction_hash=tx3,
        status=TransactionStatus.ACCEPTED, full_transaction=True, retries=60, interval=5)
    votes = (rc3.get("consensus_data") or {}).get("votes") or {}
    d = client.read_contract(address=ADDR, function_name="get_dispute", args=[did])
    verdict = d.get("verdict") if isinstance(d, dict) else None
    results.append({"dispute_id": did, "status": rc3.get("status_name"),
                    "result": rc3.get("result_name"), "votes": votes,
                    "verdict": verdict})
    print(f"[cycle {i}] dispute {did}: {rc3.get('status_name')} / {rc3.get('result_name')} "
          f"-> verdict={verdict}")

print("\nAll cycles:", json.dumps(results, indent=2, default=str))
