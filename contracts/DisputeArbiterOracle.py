# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
from genlayer import *


# ── Deterministic ISO-8601 → Unix epoch conversion ─────────────────────
# SDK v0.2.16 has NO gl.nondet.timestamp(). The node-assigned, non-manipulable
# transaction time is gl.message_raw["datetime"] (ISO-8601 str). We convert it
# to epoch with pure integer arithmetic (no datetime module, no floats) so every
# validator computes the identical value. Pattern verified live on Studionet (GVO).

def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 for a civil date (Howard Hinnant's algorithm)."""
    y = y - (1 if m <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _iso_to_epoch(dt: str) -> int:
    """Parse an ISO-8601 UTC timestamp (as emitted by GenVM, e.g.
    "2026-08-24T12:00:00.123456Z") into Unix epoch seconds."""
    s = dt.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "+" in s[10:]:
        s = s.split("+", 1)[0]
    date_part, _, time_part = s.partition("T")
    y, mo, d = [int(x) for x in date_part.split("-")]
    time_part = time_part.split(".", 1)[0]
    hh, mm, ss = [int(x) for x in time_part.split(":")]
    return _days_from_civil(y, mo, d) * 86400 + hh * 3600 + mm * 60 + ss


def _to_bytes(addr) -> bytes:
    """Normalize an Address (or bytes) to raw 20 bytes for storage/comparison."""
    if hasattr(addr, "as_bytes"):
        return addr.as_bytes
    return bytes(addr)


@allow_storage
@dataclass
class Dispute:
    dispute_id: u256
    client: bytes
    respondent: bytes
    client_claim: str
    respondent_claim: str
    evidence_urls: DynArray[str]
    status: str
    verdict: str
    refund_percentage: bigint
    reasoning_summary: str
    created_at: bigint
    resolved_at: bigint


class DisputeArbiterOracle(gl.Contract):
    """
    Decentralized two-party dispute resolution using GenLayer's non-deterministic
    LLM validator consensus instead of rigid on-chain rules.

    A client (e.g. freelance client, marketplace buyer) opens a dispute against a
    respondent (freelancer, seller), supplying their claim, the respondent's claim,
    and public evidence URLs. Each GenLayer validator independently fetches the
    evidence, reasons over both claims, and produces a structured verdict
    (winner + refund_percentage + reasoning). GenLayer's consensus mechanism
    resolves the final on-chain outcome — no single centralized arbitrator, no
    brittle if/else rules. The verdict and refund split are recorded on-chain so
    downstream escrow/marketplace contracts can act on a neutral, consensus-backed
    decision.
    """

    disputes: TreeMap[u256, Dispute]
    next_dispute_id: u256

    def __init__(self):
        self.disputes = TreeMap()
        self.next_dispute_id = u256(1)

    def _now_epoch(self) -> int:
        return _iso_to_epoch(gl.message_raw["datetime"])

    # ─────────────────────────── public write ───────────────────────────

    @gl.public.write
    def open_dispute(
        self,
        respondent: bytes,
        client_claim: str,
        respondent_claim: str,
        evidence_urls: list,
    ) -> int:
        """
        Open a new dispute. The caller (gl.message.sender_address) becomes the
        `client`. Validates that respondent != client and that at least one
        evidence URL is supplied. Returns the new dispute_id.
        """
        client_b = _to_bytes(gl.message.sender_address)
        respondent_b = _to_bytes(respondent)

        assert respondent_b != client_b, "respondent_cannot_be_client"
        assert len(evidence_urls) >= 1, "evidence_urls_required"

        dispute_id = self.next_dispute_id
        d = Dispute(
            dispute_id=dispute_id,
            client=client_b,
            respondent=respondent_b,
            client_claim=client_claim,
            respondent_claim=respondent_claim,
            evidence_urls=[str(u) for u in evidence_urls],
            status="open",
            verdict="",
            refund_percentage=bigint(0),
            reasoning_summary="",
            created_at=bigint(self._now_epoch()),
            resolved_at=bigint(0),
        )
        self.disputes[dispute_id] = d
        self.next_dispute_id = dispute_id + u256(1)
        return int(dispute_id)

    @gl.public.write
    def submit_additional_evidence(self, dispute_id: int, url: str) -> None:
        """
        Append an evidence URL to an open dispute. Only the client or the
        respondent may add evidence, and only while the dispute is still open.
        """
        d = self.disputes.get(u256(dispute_id))
        assert d is not None, "dispute_not_found"

        sender_b = _to_bytes(gl.message.sender_address)
        assert sender_b == d.client or sender_b == d.respondent, "not_a_party"
        assert d.status == "open", "dispute_already_resolved"

        d.evidence_urls.append(str(url))

    @gl.public.write
    def resolve_dispute(self, dispute_id: int) -> str:
        """
        Trigger LLM validator consensus to resolve an open dispute. Callable
        exactly once per dispute (must be status == "open"). Each validator
        independently fetches the evidence URLs, reasons over both claims, and
        returns a structured verdict. The equivalence principle compares the
        decision fields (winner exact, refund within tolerance) — not the free-text
        reasoning — so independent validators reach consensus without leaking each
        other's rationale. Stores verdict, refund_percentage, reasoning_summary,
        and resolved_at on-chain.
        """
        d = self.disputes.get(u256(dispute_id))
        assert d is not None, "dispute_not_found"
        assert d.status == "open", "dispute_already_resolved"

        # Copy consensus-critical inputs to memory before the nondet block.
        client_claim = d.client_claim
        respondent_claim = d.respondent_claim
        evidence_urls = [u for u in d.evidence_urls]

        def leader_fn() -> str:
            # Step 1: each validator independently gathers evidence from the URLs.
            evidence_text = ""
            fetched = 0
            for url in evidence_urls:
                try:
                    resp = gl.nondet.web.get(url)
                    body = resp.body
                    if isinstance(body, bytes):
                        body = body.decode("utf-8", errors="ignore")
                    evidence_text += "\n--- EVIDENCE: " + url + " ---\n" + body[:2000]
                    fetched += 1
                except Exception:
                    evidence_text += "\n--- EVIDENCE: " + url + " ---\n[fetch failed]\n"

            if fetched == 0:
                fetch_note = (
                    "NOTE: No evidence URL could be fetched. Reason from the two "
                    "claims alone and say so in your reasoning."
                )
            else:
                fetch_note = (
                    "NOTE: " + str(fetched) + " of " + str(len(evidence_urls)) +
                    " evidence sources were fetched. Weigh them against the claims."
                )

            # Step 2: LLM reasons over claims + evidence to a structured verdict.
            prompt = (
                "You are a neutral, impartial dispute arbitrator on GenLayer.\n"
                "Two parties disagree. Review both claims and the evidence, then "
                "decide fairly.\n\n"
                "CLIENT CLAIM:\n" + client_claim + "\n\n"
                "RESPONDENT CLAIM:\n" + respondent_claim + "\n\n"
                "EVIDENCE:\n" + evidence_text + "\n\n"
                + fetch_note + "\n\n"
                "DECISION RULES:\n"
                "- winner is 'client' if the client's position is better supported "
                "by the evidence; 'respondent' if the respondent's is; 'split' if "
                "both have substantial merit or the evidence is inconclusive.\n"
                "- refund_percentage is the share (0-100) of the disputed payment "
                "that should be returned to the CLIENT. If the client fully wins, "
                "use 100. If the respondent fully wins, use 0. For a split, use a "
                "proportionate value (e.g. 50).\n"
                "- reasoning_summary must be a short justification, under 200 "
                "characters.\n\n"
                "Return ONLY valid JSON with exactly these keys:\n"
                '{"winner": "client" or "respondent" or "split", '
                '"refund_percentage": <integer 0-100>, '
                '"reasoning_summary": "<short justification>"}'
            )
            from genlayer.gl.nondet import exec_prompt
            raw = exec_prompt(prompt, response_format="json")
            v = raw if isinstance(raw, dict) else json.loads(raw)

            winner = str(v.get("winner", "split")).strip().lower()
            if winner not in ("client", "respondent", "split"):
                winner = "split"
            try:
                rp = int(v.get("refund_percentage", 50))
            except Exception:
                rp = 50
            if rp < 0:
                rp = 0
            if rp > 100:
                rp = 100
            summary = str(v.get("reasoning_summary", ""))[:200]

            return json.dumps(
                {"winner": winner, "refund_percentage": rp,
                 "reasoning_summary": summary},
                sort_keys=True,
            )

        def validator_fn(leaders_res) -> bool:
            # Equivalence principle (partial field matching + numeric tolerance):
            # each validator re-runs leader_fn INDEPENDENTLY and compares only the
            # decision fields. Free-text reasoning is NOT compared (it naturally
            # varies), and no validator's rationale leaks into another's.
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            mine = json.loads(leader_fn())
            leader = json.loads(leaders_res.calldata)
            try:
                refund_ok = abs(
                    int(leader.get("refund_percentage", 50))
                    - int(mine.get("refund_percentage", 50))
                ) <= 10
            except Exception:
                refund_ok = False
            return (
                leader.get("winner") == mine.get("winner")
                and refund_ok
            )

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        verdict = json.loads(result)

        d.status = "resolved"
        d.verdict = verdict.get("winner", "split")
        d.refund_percentage = bigint(int(verdict.get("refund_percentage", 50)))
        d.reasoning_summary = str(verdict.get("reasoning_summary", ""))[:200]
        d.resolved_at = bigint(self._now_epoch())

        return result

    # ─────────────────────────── public view ────────────────────────────

    @gl.public.view
    def get_dispute(self, dispute_id: int) -> Dispute:
        """Return the full Dispute record for a given dispute_id."""
        d = self.disputes.get(u256(dispute_id))
        assert d is not None, "dispute_not_found"
        return d

    @gl.public.view
    def get_disputes_by_party(self, party: bytes) -> list:
        """Return the list of dispute_ids where `party` is client or respondent."""
        party_b = _to_bytes(party)
        out = []
        for _id, d in self.disputes.items():
            if d.client == party_b or d.respondent == party_b:
                out.append(int(d.dispute_id))
        return out
