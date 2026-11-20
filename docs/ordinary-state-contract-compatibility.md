# Ordinary Worker State source compatibility

The ordinary result-owner, amendment, model-identity and production write
readiness consumers support exactly these production State source contracts:

| Contract | Schema tree | Scripts tree |
| --- | --- | --- |
| `e2dd6314f04df175966ff105709b855053949d97` | `2e83ec14e70ca6dd4bba1518c8a2ca28ee76be94` | `cf03bcfa56de6381f2d66a00b2547d4d9b46b0df` |
| `351b1c409290516bfa379fc857908908e1d08932` | `f7efe29098882c33bba25b5dbbeba34bd981205d` | `1f0173a9cf24d00e139c0c346500a2deef5c2453` |

The closed tables also bind the exact README blob and docs tree. The live root
is read once and must match exactly one complete table entry before checking
that entry's reviewed anchor is an ancestor. Mixed roots, unknown code,
incomplete trees, duplicate entries, wrong modes and unrelated ancestry fail
closed. Data-only descendants retain their reviewed code identity. Proof
caches bind the repository, exact head and complete reviewed table; the proof
uses the same number of GitHub subrequests as the previous single-contract
implementation. Staging remains pinned to `f410468baefe0eb1e91ec131ded313f0ffad45f0`.

Ordinary API configuration retains the legacy capability floor because these
ordinary event and view protocols are unchanged by the archive extension.
The separate source proof establishes exact code compatibility; the old API
floor does not waive unknown source changes or enable any new feature.

Authenticated production `POST /readyz` proves branch protection and the
current exact source, submits a non-forced same-commit write probe, and checks
protection and head again. Its existing closed response carries the selected
contract and event-schema SHA-256 from that same proof. Legacy uses schema
`c7993f8038a4937659ea2bd40fce8b97100c6f63bb1565605d841b67091f8a9e`;
the archive-capable contract uses
`2b16ce492d30e80cd219f0432f93b4b0177d0560911d771f423c91d8724468a6`.
No State event is appended by this write probe.

New target rollback qualifications explicitly declare both exact source
contracts in `supported_state_contracts`, while retaining the legacy
`state_main_commit` capability floor. Their callback digest covers the actual
Worker implementation. The rollback workflow chooses the live source only
from the target's declared map, then independently verifies the exact anchor,
root entries, event schema and protected-head ancestry before mutation.
Older target qualifications retain their original single-contract admission;
the current validator does not grant archive compatibility to an old Worker.
An old rollback target therefore fails closed against the new State code.

Normal promotion validates its first production write-readiness response
against the current target qualification. Later lease smoke uses the exact
contract/schema pair from that validated response; it cannot silently accept
a different State contract during finalization. Existing lease expiry, CAS,
credential, replay and publication guards remain in force.

This change prepares consumers for a coordinated transition. It performs no
State activation, qualification event, source recovery, replay, deployment or
credential change. Keep the exact State anchor in merge history and ensure
all required consumers are ready before advancing State code. Historical
replay activation remains governed by its separate reviewed packets.
