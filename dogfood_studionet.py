#!/usr/bin/env python3
"""
End-to-end dogfood of DisputeArbiterOracle on Studionet with REAL consensus.

Exercises the full write path against live validators:
  1. open_dispute        (deployer=client, a counterparty=respondent)
  2. submit_additional_evidence (as client)
  3. resolve_dispute      (triggers nondet LLM validator consensus)
  4. get_dispute          (read back the resolved verdict)

Evidence URLs are stable public raw files so every validator can fetch them.
"""
import json
import time
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

HERE = Path(__file__).resolve().parent
KEY_PATH = Path.home() / ".genlayer-keys" / "peer-review-oracle.json"
ADDR = "0xC977FB298bfE87457467faDdAce398D4533E98c9"

OUT = HERE / "dogfood_result.json"

# Stable, public evidence URLs (validators can independently fetch these).
EVIDENCE_URLS = [
    "https://raw.githubusercontent.com/genlayerlabs/genlayer-project-boilerplate/main/README.md",
    "https://raw.githubusercontent.com/genlayerlabs/genlayer-project-boilerplate/main/CLAUDE.md",
]

CLIENT_CLAIM = (
    "I hired the freelancer to deliver a responsive landing page by Friday. I paid the "
    "full 500 USDT deposit up front. The deadline passed and no working page was "
    "delivered — only a single static screenshot. I want my deposit refunded."
)
RESPONDENT_CLAIM = (
    "I delivered the landing page on time and sent the link plus a demo video. The "
    "client stopped responding and never confirmed receipt. The work is complete, so "
    "the deposit should be released to me, not refunded."
)

def main() -> int:
    key_data = json.loads(KEY_PATH.read_text())
    account = Account.from_key(key_data["private_key"])
    client = create_client(chain=studionet, account=key_data["private_key"])
    client.local_account = account

    # A distinct counterparty address (respondent). Doesn't need to sign anything —
    # only the client opens the dispute; anyone may trigger resolution.
    respondent_addr = "0x567865452AfC3BDE935532f851D8952eDb6c8a8D"
    respondent_bytes = bytes.fromhex(respondent_addr[2:])

    print("Deployer/client:", account.address)
    print("respondent:", respondent_addr)

    # ── 1. open_dispute ────────────────────────────────────────────────
    print("\n[1] open_dispute ...")
    tx = client.write_contract(
        address=ADDR,
        function_name="open_dispute",
        args=[respondent_bytes, CLIENT_CLAIM, RESPONDENT_CLAIM, EVIDENCE_URLS],
        account=account,
    )
    print("   tx:", tx)
    rc = client.wait_for_transaction_receipt(
        transaction_hash=tx, status=TransactionStatus.ACCEPTED,
        full_transaction=True, retries=40, interval=3)
    print("   status:", rc.get("status_name"), "result:", rc.get("result_name"))

    # ── 2. submit_additional_evidence (as client) ─────────────────────
    print("\n[2] submit_additional_evidence ...")
    tx2 = client.write_contract(
        address=ADDR,
        function_name="submit_additional_evidence",
        args=[1, "https://raw.githubusercontent.com/genlayerlabs/genlayer-project-boilerplate/main/.gitignore"],
        account=account,
    )
    print("   tx:", tx2)
    rc2 = client.wait_for_transaction_receipt(
        transaction_hash=tx2, status=TransactionStatus.ACCEPTED,
        full_transaction=True, retries=40, interval=3)
    print("   status:", rc2.get("status_name"), "result:", rc2.get("result_name"))

    # ── 3. resolve_dispute (REAL nondet LLM consensus) ─────────────────
    print("\n[3] resolve_dispute (live LLM validator consensus — may take a while) ...")
    tx3 = client.write_contract(
        address=ADDR,
        function_name="resolve_dispute",
        args=[1],
        account=account,
    )
    print("   tx:", tx3)
    rc3 = client.wait_for_transaction_receipt(
        transaction_hash=tx3, status=TransactionStatus.ACCEPTED,
        full_transaction=True, retries=60, interval=5)
    print("   status:", rc3.get("status_name"), "result:", rc3.get("result_name"))
    consensus = rc3.get("consensus_data") or {}
    votes = (consensus.get("votes") or {})
    print("   validator votes:", votes)

    # ── 4. get_dispute (read back) ─────────────────────────────────────
    print("\n[4] get_dispute(1) ...")
    time.sleep(2)
    d = client.read_contract(address=ADDR, function_name="get_dispute", args=[1])
    print("   resolved dispute:", json.dumps(d, indent=2, default=str))

    result = {
        "contract_address": ADDR,
        "open_dispute_tx": tx,
        "submit_evidence_tx": tx2,
        "resolve_dispute_tx": tx3,
        "resolve_status": rc3.get("status_name"),
        "resolve_result": rc3.get("result_name"),
        "validator_votes": votes,
        "resolved_dispute": d,
    }
    OUT.write_text(json.dumps(result, indent=2, default=str))
    print("\nSaved ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
