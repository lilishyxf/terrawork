# M1.3 Acceptance: NPC Execution + Sandbox + Tool Calling

**Status**: Accepted on 2026-06-16
**Scope**: M1.3-1 → M1.3-4 (commits 766832c → 9f341b9)

## Sub-milestones

| ID | Commit | Deliverable |
|---|---|---|
| M1.3-1 | 766832c | guide_assign schema (ADR-011) + m13 fixture + contract tests |
| M1.3-2 | 142c272 | worktree lifecycle + read/write/bash tools + Windows fixes |
| M1.3-3 | af15679 | merchant role + assembleContext + scripted executor (INV-1~7 offline) |
| M1.3-4a | 018c240 | complete_with_tools facade + mock stub |
| M1.3-4b | 9f341b9 | LLM iterative path + DeepSeek live (INV-1~7 live) |

## Invariant Status

| INV | Description | Scripted (M1.3-3) | Live DeepSeek (M1.3-4) |
|---|---|---|---|
| 1 | Runtime: worktree created with .git | ✓ | ✓ |
| 2 | Tool events paired via parent_event_id | ✓ | ✓ |
| 3 | Parent chain strictly backward | ✓ | ✓ |
| 4 | Agent field consistent | ✓ | ✓ |
| 5 | Tool within role whitelist | ✓ | ✓ |
| 6 | All schemas pass | ✓ | ✓ |
| 7 | Completion signal (review_request links to guide_assign) | ✓ | ✓ |

## DeepSeek Live Metrics

- Model: deepseek/deepseek-chat (via LiteLLM `complete_with_tools`)
- Task (live, self-contained): implement `login.py` with Python stdlib, self-verify via `python -c` assertion
- Observed trajectory (representative run): brief environment recon (`echo`, `python --version`) → one `write login.py` → `bash python -c "...assert..."` self-verification returning exit_code 0 (`OK`) → no further tool calls → completion signal → review_request
- Tools invoked: read/bash (recon) + write ×1 (login.py) + bash ×2-3 (self-verify); ~6-9 tool calls total
- Iterations: converged well under max_iterations=20 (no RuntimeError)
- Latency: ~45s per live run (full DeepSeek round-trips × iterations)
- Token usage: not separately surfaced by current facade — see live test logs / LiteLLM response if needed
- Generated `login.py` quality: hashlib SHA-256, no plaintext password constants, correct credential logic (honored task_card boundaries)

## Known Limitations

- **LLM trajectory non-determinism**: live test validates structural invariants (INV-1~7), not exact reference_output match. Different runs may invoke different tools in different orders. This is by design (we validate engine correctness, not LLM behavior).
- **Two task fixtures, two purposes**: `m13_merchant_login_impl.json` (npm/TS) is the *scripted* reference standard (validates event-stream structure offline, never actually runs npm). The *live* test uses a separate inline Python self-verifying task because a live LLM needs an environment-matched task that can actually converge — a bare worktree has no npm toolchain, so the npm task could never reach a green self-check. Engine correctness is validated either way.
- **max_iterations cap (10 default, 20 in live test)**: hitting cap raises RuntimeError. No graceful degradation / retry / HITL escalation yet — deferred to M2.
- **bash denylist is regex-based**: defense-in-depth against LLM accidents, not adversarial containment. Acceptable for M1.x trust model (user runs locally; sandbox is per-NPC worktree isolation). OS-level isolation deferred to M2 (ADR-012) + future ADR-013-candidate.
- **Verification not yet executed authoritatively**: review_request is emitted but no verifier consumes task_card.verification[] commands yet. The merchant's own bash self-check is dev feedback, NOT the verification gate. M1.4 closes this loop.
- **ADR-013 implementation gap**: `domain` and `specialty` frontmatter fields are defined (ADR-013) but the role loader does not yet consume them; tool whitelist by domain is not enforced. Deferred to M2 with assignment routing.
- **Test marker convention not unified** — ✅ **Closed (c87fc98)**: registered the `live` marker in root `pytest.ini` and tagged m12 `test_real_guide_satisfies_invariants_per_provider` with `@pytest.mark.live`. True offline runs now use `-m "not live"` (replacing the interim `-k "not real_guide and not live"`); the PytestUnknownMarkWarning is cleared.

## Next: M1.4 — Verification Condition Executor

- Verifier role file (e.g. `roles/demolitionist.md` per Terraria 爆破专家 mapping)
- Verifier reads task_card.verification[], executes commands (machine_verifiable) or escalates (hitl_escalation), emits verify_run events
- This closes M1 full loop: Guide → assigns → NPC builds → verifier checks → Guide arbitrates (commit or revert/retry)
