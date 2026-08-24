# Direct-mode unit tests for DisputeArbiterOracle.
#
# Run with:
#   /home/ubuntu/genlayer-escrow-app/.venv/bin/python -m pytest tests/ -v --tb=short
#
# Deterministic storage/logic paths run in-memory (no Docker/network). The
# non-deterministic resolve_dispute path is exercised end-to-end with mocked
# web (evidence URLs) and LLM (exec_prompt) responses via gltest direct-mode
# cheatcodes. All revert-path tests use pytest.raises(AssertionError) because
# the contract reverts with `assert` (vm.expect_revert is deprecated/broken).

import json
import sys
import pytest

from gltest.direct.pytest_plugin import (  # noqa: F401  (imports fixtures)
    direct_vm,
    direct_deploy,
    direct_alice,
    direct_bob,
    direct_charlie,
    direct_owner,
    direct_accounts,
)


CLIENT_CLAIM = "I paid the full deposit but the freelancer never delivered the logo."
RESPONDENT_CLAIM = "I delivered two drafts on time; the client ghosted and refused to review."
EVIDENCE = ["https://example.com/invoice", "https://example.com/chat-log"]


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/DisputeArbiterOracle.py", sdk_version="v0.2.16")


def _warp(direct_vm, iso):
    """vm.warp + propagate to the loaded contract's cached gl.message_raw['datetime'].

    Skill pitfall: vm.warp() only updates vm._datetime (used for fresh stdin at
    deploy). Contracts that read gl.message_raw['datetime'] mid-test keep seeing
    the deploy-time value unless we also patch the module-level cache.
    """
    direct_vm.warp(iso)
    sys.modules["genlayer.gl"].message_raw["datetime"] = iso


def _open(vm, contract, client, respondent, evidence=None):
    vm.sender = client
    return int(contract.open_dispute(
        respondent,
        CLIENT_CLAIM,
        RESPONDENT_CLAIM,
        evidence if evidence is not None else EVIDENCE,
    ))


def _mock_evidence_and_llm(direct_vm, verdict):
    """Mock the evidence fetches + the arbitrator LLM for resolve_dispute."""
    direct_vm.mock_web(
        r"example\.com/.*",
        {"method": "GET", "status": 200,
         "body": "Documented evidence: payment receipt and message transcript.",
         "headers": {}},
    )
    direct_vm.mock_llm(".*neutral, impartial dispute arbitrator.*", json.dumps(verdict))


def _client_win():
    return {"winner": "client", "refund_percentage": 100,
            "reasoning_summary": "Payment receipt confirms deposit; no delivery proof from respondent."}


# --------------------------------------------------------------------------- #
# 1. Opening a dispute successfully
# --------------------------------------------------------------------------- #
def test_open_dispute_success(contract, direct_vm, direct_alice, direct_bob):
    _warp(direct_vm, "2026-08-24T00:00:00.000000Z")
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    assert dispute_id == 1

    d = contract.get_dispute(dispute_id)
    assert d.status == "open"
    assert d.verdict == ""
    assert d.client_claim == CLIENT_CLAIM
    assert d.respondent_claim == RESPONDENT_CLAIM
    assert int(d.refund_percentage) == 0
    assert int(d.created_at) == 1787529600  # 2026-08-24T00:00:00Z
    assert int(d.resolved_at) == 0
    assert len(d.evidence_urls) == 2
    assert d.evidence_urls[0] == EVIDENCE[0]
    # client/respondent stored as raw 20-byte addresses
    assert d.client == direct_alice.as_bytes
    assert d.respondent == direct_bob.as_bytes


# --------------------------------------------------------------------------- #
# 2. Rejecting a dispute with empty evidence_urls
# --------------------------------------------------------------------------- #
def test_open_dispute_empty_evidence_rejected(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    with pytest.raises(AssertionError):
        contract.open_dispute(direct_bob, CLIENT_CLAIM, RESPONDENT_CLAIM, [])


# --------------------------------------------------------------------------- #
# 3. Rejecting a dispute where respondent == client
# --------------------------------------------------------------------------- #
def test_open_dispute_self_respondent_rejected(contract, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    with pytest.raises(AssertionError):
        contract.open_dispute(direct_alice, CLIENT_CLAIM, RESPONDENT_CLAIM, EVIDENCE)


# --------------------------------------------------------------------------- #
# 4. Submitting evidence as client AND as respondent (both allowed)
# --------------------------------------------------------------------------- #
def test_submit_evidence_by_both_parties(contract, direct_vm, direct_alice, direct_bob):
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    assert len(contract.get_dispute(dispute_id).evidence_urls) == 2

    # Client adds evidence.
    direct_vm.sender = direct_alice
    contract.submit_additional_evidence(dispute_id, "https://example.com/extra-client")
    # Respondent adds evidence.
    direct_vm.sender = direct_bob
    contract.submit_additional_evidence(dispute_id, "https://example.com/extra-respondent")

    d = contract.get_dispute(dispute_id)
    assert len(d.evidence_urls) == 4
    assert d.evidence_urls[2] == "https://example.com/extra-client"
    assert d.evidence_urls[3] == "https://example.com/extra-respondent"


# --------------------------------------------------------------------------- #
# 5. Rejecting evidence submission from a third party
# --------------------------------------------------------------------------- #
def test_submit_evidence_third_party_rejected(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    direct_vm.sender = direct_charlie  # not client, not respondent
    with pytest.raises(AssertionError):
        contract.submit_additional_evidence(dispute_id, "https://example.com/intruder")


# --------------------------------------------------------------------------- #
# 6. Resolving a dispute: verdict / refund_percentage / status change
# --------------------------------------------------------------------------- #
def test_resolve_dispute_client_wins(contract, direct_vm, direct_alice, direct_bob):
    _warp(direct_vm, "2026-08-24T00:00:00.000000Z")
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)

    _warp(direct_vm, "2026-08-24T06:00:00.000000Z")
    _mock_evidence_and_llm(direct_vm, _client_win())
    result = json.loads(contract.resolve_dispute(dispute_id))

    assert result["winner"] == "client"
    assert result["refund_percentage"] == 100

    d = contract.get_dispute(dispute_id)
    assert d.status == "resolved"
    assert d.verdict == "client"
    assert int(d.refund_percentage) == 100
    assert int(d.resolved_at) == 1787529600 + 6 * 3600
    assert "Payment receipt" in d.reasoning_summary


# --------------------------------------------------------------------------- #
# 7. Resolving with a split verdict + partial refund
# --------------------------------------------------------------------------- #
def test_resolve_dispute_split(contract, direct_vm, direct_alice, direct_bob):
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    _mock_evidence_and_llm(direct_vm, {
        "winner": "split", "refund_percentage": 50,
        "reasoning_summary": "Both parties partially performed; equitable split."})
    result = json.loads(contract.resolve_dispute(dispute_id))
    assert result["winner"] == "split"
    assert result["refund_percentage"] == 50

    d = contract.get_dispute(dispute_id)
    assert d.status == "resolved"
    assert d.verdict == "split"
    assert int(d.refund_percentage) == 50


# --------------------------------------------------------------------------- #
# 8. Rejecting a second resolve_dispute on an already-resolved dispute
# --------------------------------------------------------------------------- #
def test_resolve_twice_rejected(contract, direct_vm, direct_alice, direct_bob):
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    _mock_evidence_and_llm(direct_vm, _client_win())
    contract.resolve_dispute(dispute_id)
    assert contract.get_dispute(dispute_id).status == "resolved"

    with pytest.raises(AssertionError):
        contract.resolve_dispute(dispute_id)


# --------------------------------------------------------------------------- #
# 9. Rejecting evidence submission after resolution
# --------------------------------------------------------------------------- #
def test_submit_evidence_after_resolve_rejected(contract, direct_vm, direct_alice, direct_bob):
    dispute_id = _open(direct_vm, contract, direct_alice, direct_bob)
    _mock_evidence_and_llm(direct_vm, _client_win())
    contract.resolve_dispute(dispute_id)

    direct_vm.sender = direct_alice
    with pytest.raises(AssertionError):
        contract.submit_additional_evidence(dispute_id, "https://example.com/late")


# --------------------------------------------------------------------------- #
# 10. get_dispute on a nonexistent id reverts
# --------------------------------------------------------------------------- #
def test_get_dispute_not_found(contract, direct_vm):
    with pytest.raises(AssertionError):
        contract.get_dispute(999)


# --------------------------------------------------------------------------- #
# 11. get_disputes_by_party returns correct dispute ids
# --------------------------------------------------------------------------- #
def test_get_disputes_by_party(contract, direct_vm, direct_alice, direct_bob, direct_charlie, direct_accounts):
    # alice vs bob  -> id 1
    _open(direct_vm, contract, direct_alice, direct_bob)
    # charlie vs alice -> id 2 (alice is respondent)
    _open(direct_vm, contract, direct_charlie, direct_alice)
    # bob vs charlie -> id 3 (alice not involved)
    _open(direct_vm, contract, direct_bob, direct_charlie)

    alice_ids = sorted(int(i) for i in contract.get_disputes_by_party(direct_alice))
    assert alice_ids == [1, 2]

    bob_ids = sorted(int(i) for i in contract.get_disputes_by_party(direct_bob))
    assert bob_ids == [1, 3]

    charlie_ids = sorted(int(i) for i in contract.get_disputes_by_party(direct_charlie))
    assert charlie_ids == [2, 3]

    # An uninvolved party gets an empty list.
    direct_vm.sender = direct_alice
    nobody = direct_accounts[9]
    assert contract.get_disputes_by_party(nobody) == []


# --------------------------------------------------------------------------- #
# 12. Multiple disputes get sequential ids
# --------------------------------------------------------------------------- #
def test_dispute_ids_sequential(contract, direct_vm, direct_alice, direct_bob):
    id1 = _open(direct_vm, contract, direct_alice, direct_bob)
    id2 = _open(direct_vm, contract, direct_bob, direct_alice)
    id3 = _open(direct_vm, contract, direct_alice, direct_bob)
    assert (id1, id2, id3) == (1, 2, 3)
