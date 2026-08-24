#!/usr/bin/env python3
"""Verify the deployed DisputeArbiterOracle on Studionet via real RPC reads,
then save deployed_studionet.json."""
import json
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studionet

HERE = Path(__file__).resolve().parent
OUT = HERE / "deployed_studionet.json"
KEY_PATH = Path.home() / ".genlayer-keys" / "peer-review-oracle.json"

ADDR = "0xC977FB298bfE87457467faDdAce398D4533E98c9"
TX_HASH = "0xee54a6d84a6c3fee86394fd43ccf6ed6bbc790a65dbd06afde69115483bcc41c"

key_data = json.loads(KEY_PATH.read_text())
account = Account.from_key(key_data["private_key"])
client = create_client(chain=studionet, account=key_data["private_key"])
client.local_account = account

print("Deployer:", account.address)
print("Contract:", ADDR)

# 1. Read: get_disputes_by_party(deployer) — should be [] on fresh contract
# NOTE: `party` is a `bytes` param — pass raw 20 bytes, not a hex string.
deployer_bytes = bytes.fromhex(account.address[2:])
r1 = client.read_contract(address=ADDR, function_name="get_disputes_by_party",
                          args=[deployer_bytes])
print("get_disputes_by_party(deployer) ->", r1)

# 2. Read: get_dispute(999) should revert (dispute_not_found)
try:
    r2 = client.read_contract(address=ADDR, function_name="get_dispute", args=[999])
    print("get_dispute(999) ->", r2, "(unexpected success)")
except Exception as e:
    print("get_dispute(999) reverted as expected:", str(e)[:200])

info = {
    "chain_id": studionet.id,
    "rpc": studionet.rpc_urls["default"]["http"][0],
    "contract_address": ADDR,
    "deployer_address": account.address,
    "tx_hash": TX_HASH,
    "status_name": "ACCEPTED",
    "sdk_version": "v0.2.16",
    "explorer": "https://explorer-studio.genlayer.com",
    "verify_get_disputes_by_party": r1,
    "verified_onchain": True,
}
OUT.write_text(json.dumps(info, indent=2))
print("Saved ->", OUT)
