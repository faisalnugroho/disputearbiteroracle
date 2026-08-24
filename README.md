# DisputeArbiterOracle

Decentralized two-party dispute resolution on **GenLayer** using non-deterministic
LLM validator consensus instead of rigid on-chain rules.

Built for the **GenLayer Builder Portal — Projects track** (Contribution Type:
Builder > Projects).

## What it does

Two parties in a commercial disagreement (freelance client vs. freelancer,
marketplace buyer vs. seller) often need a neutral arbitrator — but a human
arbitrator is slow, expensive, and centralized. A normal smart contract can't
help either, because the outcome requires *judgment*: interpreting claims,
weighing evidence, resolving ambiguity.

**DisputeArbiterOracle** fills that gap:

1. The **client** opens a dispute against a **respondent**, supplying both
   parties' claims and public evidence URLs.
2. Either party may append more evidence while the dispute is open.
3. Anyone can trigger `resolve_dispute`. Each GenLayer validator
   **independently** fetches the evidence URLs, has its LLM reason over both
   claims, and produces a structured verdict:
   `winner` (`client` | `respondent` | `split`), `refund_percentage` (0–100),
   and a short `reasoning_summary`.
4. GenLayer's consensus mechanism (equivalence principle) resolves the final
   on-chain outcome. Validators compare only the decision fields (winner exact,
   refund within ±10 tolerance) — never the free-text reasoning — so
   independent validators reach agreement without leaking each other's
   rationale.
5. The verdict, refund split, and reasoning are recorded on-chain so downstream
   escrow/marketplace contracts can act on a neutral, consensus-backed decision.

## Why this is meaningful LLM use

- **A normal smart contract cannot do this** — it requires interpreting
  unstructured claims and evidence, not comparing numbers.
- **A human would need to think** — "which party's position is better supported
  by the evidence?"
- **Validators independently verify** — every validator fetches the same public
  evidence URLs and re-derives the judgment; the leader's work is checkable.
- **Real on-chain consequence** — the verdict and refund percentage are stored
  on-chain and change contract state (`open` → `resolved`), ready for escrow
  contracts to act on.

## Contract

`contracts/DisputeArbiterOracle.py` — GenLayer Intelligent Contract
(SDK `py-genlayer` v0.2.16).

### Storage

`disputes: TreeMap[u256, Dispute]` where `Dispute` is an `@allow_storage`
dataclass:

| field | type | notes |
|---|---|---|
| `dispute_id` | `u256` | sequential |
| `client` | `bytes` | captured from `gl.message.sender_address` (never caller-supplied) |
| `respondent` | `bytes` | |
| `client_claim` | `str` | |
| `respondent_claim` | `str` | |
| `evidence_urls` | `DynArray[str]` | appendable while open |
| `status` | `str` | `open` \| `resolved` |
| `verdict` | `str` | `""` \| `client` \| `respondent` \| `split` |
| `refund_percentage` | `bigint` | 0–100, share returned to client |
| `reasoning_summary` | `str` | < 200 chars |
| `created_at` | `bigint` | epoch, from node-assigned `gl.message_raw["datetime"]` |
| `resolved_at` | `bigint` | epoch |

### Public write methods

- `open_dispute(respondent, client_claim, respondent_claim, evidence_urls) -> int`
  — caller becomes `client`; reverts if `respondent == client` or evidence is empty.
- `submit_additional_evidence(dispute_id, url)` — only client/respondent, only while open.
- `resolve_dispute(dispute_id) -> str` — triggers nondet LLM consensus; callable once.

### Public view methods

- `get_dispute(dispute_id) -> Dispute`
- `get_disputes_by_party(party) -> list[int]`

### Timestamps

SDK v0.2.16 has **no** `gl.nondet.timestamp()`. The node-assigned,
non-manipulable transaction time is `gl.message_raw["datetime"]` (ISO-8601).
It is converted to epoch with pure integer arithmetic (Howard Hinnant
`_days_from_civil`) so every validator computes the identical value — no
`datetime` module, no floats.

## Testing

Direct-mode unit tests via `gltest` (in-memory, no Docker/network). The
non-deterministic path is exercised end-to-end with mocked web + LLM responses.

```bash
/home/ubuntu/genlayer-escrow-app/.venv/bin/python -m pytest tests/ -v
```

**12/12 passing**, covering: open success, empty-evidence revert, self-respondent
revert, evidence submission by both parties, third-party evidence revert,
resolve (client win + split), double-resolve revert, post-resolve evidence
revert, get_dispute not-found revert, get_disputes_by_party, sequential ids.

All revert-path tests use `pytest.raises(AssertionError)` (the contract reverts
with `assert`; `vm.expect_revert` is deprecated/broken).

### Lint

```bash
GENVMROOT=/tmp/genvmroot genvm-lint check --json contracts/DisputeArbiterOracle.py
```

Clean: `{"ok": true, "lint": {"ok": true, "passed": 3}, "validate": {"ok": true, ...}}`

## Deployment

Deployed to **GenLayer Studionet** (chain 61999, `https://studio.genlayer.com/api`)
with full validator consensus.

- **Contract address:** `0xC977FB298bfE87457467faDdAce398D4533E98c9`
- **Explorer:** https://explorer-studio.genlayer.com
- **Deploy tx:** `0xee54a6d84a6c3fee86394fd43ccf6ed6bbc790a65dbd06afde69115483bcc41c`

Verified on-chain via real RPC reads (`get_disputes_by_party` → `[]`,
`get_dispute(999)` → revert) and a full live dogfood: `open_dispute` →
`submit_additional_evidence` → `resolve_dispute` (real LLM validator consensus,
MAJORITY_AGREE) → `get_dispute` returned the resolved verdict.

## Files

- `contracts/DisputeArbiterOracle.py` — the Intelligent Contract
- `tests/test_dispute_arbiter_oracle.py` — 12 direct-mode unit tests
- `deploy_studionet.py` — Studionet deploy script (genlayer-py)
- `verify_studionet.py` — on-chain read verification
- `dogfood_studionet.py` — end-to-end live consensus dogfood
- `deployed_studionet.json` — deployment record
- `dogfood_result.json` — live dogfood result

## License

MIT
