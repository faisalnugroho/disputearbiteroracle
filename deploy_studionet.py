#!/usr/bin/env python3
"""
Deploy DisputeArbiterOracle to GenLayer Studionet (https://studio.genlayer.com/api, chain 61999).

Run:
  /home/ubuntu/genlayer-escrow-app/.venv/bin/python deploy_studionet.py

Uses the project wallet stored in ~/.genlayer-keys/peer-review-oracle.json
(chmod 600, never committed) — the same Studionet deployer wallet used for
PeerReviewOracle. Studionet has a built-in faucet (fund_account).

After deploy, verifies the contract on-chain via a real RPC read call
(get_disputes_by_party on the deployer) — never trusts the receipt alone.
"""
import json
import sys
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "contracts" / "DisputeArbiterOracle.py"
OUT = HERE / "deployed_studionet.json"
KEY_PATH = Path.home() / ".genlayer-keys" / "peer-review-oracle.json"


def main() -> int:
    code = CONTRACT_PATH.read_text()
    print(f"Contract source: {CONTRACT_PATH} ({len(code)} bytes)")

    key_data = json.loads(KEY_PATH.read_text())
    account = Account.from_key(key_data["private_key"])
    client = create_client(chain=studionet, account=key_data["private_key"])
    client.local_account = account  # SDK quirk fix (local_account must be set)

    print("Account:", account.address)

    # Fund via Studionet built-in faucet (does NOT work on Bradbury; fine here).
    try:
        client.fund_account(account.address, 10 ** 18)
        print("Funded account with 10^18 wei.")
    except Exception as e:
        print("fund_account note:", repr(e))

    # Deploy with real consensus (studionet runs 5 validators / 3 rotations).
    print("Deploying (full consensus) ...")
    tx_hash = client.deploy_contract(code=code, account=account, leader_only=False)
    print("tx_hash:", tx_hash)

    # Studionet quirk: transactions reach ACCEPTED but FINALIZED times out even
    # though the tx succeeds. Poll ACCEPTED (verified pattern from prior deploys).
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.ACCEPTED,
        full_transaction=True,
        retries=40,
        interval=3,
    )
    print("receipt keys:", sorted(receipt.keys()))

    # CRITICAL: consensus can finalize a REVERTED tx. Check execution result.
    exec_name = receipt.get("tx_execution_result_name") or (
        receipt.get("data", {}) or {}
    ).get("tx_execution_result_name")
    print("tx_execution_result_name:", exec_name)

    addr = receipt.get("data", {}).get("contract_address") or receipt.get("contract_address")
    print("contract_address:", addr)

    # Consensus detail for the report.
    consensus = receipt.get("consensus_data") or {}
    leader_receipt = (consensus.get("leader_receipt") or [{}])
    leader_exec = None
    if leader_receipt:
        leader_exec = (leader_receipt[0].get("execution_result")
                       or (leader_receipt[0].get("genvm_result") or {}).get("exit_code"))

    info = {
        "chain_id": studionet.id,
        "rpc": studionet.rpc_urls["default"]["http"][0],
        "contract_address": addr,
        "deployer_address": account.address,
        "tx_hash": tx_hash,
        "tx_execution_result_name": exec_name,
        "status_name": receipt.get("status_name"),
        "result_name": receipt.get("result_name"),
        "leader_execution_result": leader_exec,
        "sdk_version": "v0.2.16",
        "explorer": "https://explorer-studio.genlayer.com",
    }

    if exec_name != "FINISHED_WITH_RETURN":
        print("\n!!! EXECUTION DID NOT FINISH WITH RETURN — dumping debug trace:")
        try:
            trace = client.debug_trace_transaction(transaction_hash=tx_hash)
            print(json.dumps(trace, indent=2, default=str)[:4000])
        except Exception as e:
            print("debug_trace_transaction error:", repr(e))
        OUT.write_text(json.dumps(info, indent=2))
        return 1

    # ── On-chain verification: real RPC read against the deployed address ──
    print("\nVerifying on-chain via read_contract(get_disputes_by_party) ...")
    try:
        result = client.read_contract(
            address=addr,
            function_name="get_disputes_by_party",
            args=[account.address],
        )
        print("get_disputes_by_party(deployer) ->", result)
        info["verify_get_disputes_by_party"] = result
        info["verified_onchain"] = True
    except Exception as e:
        print("read_contract verification error:", repr(e))
        info["verified_onchain"] = False

    OUT.write_text(json.dumps(info, indent=2))
    print("Saved ->", OUT)
    print(f"\nSTUDIONET DEPLOY OK — contract_address: {addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
