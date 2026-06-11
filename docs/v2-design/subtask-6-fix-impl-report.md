# Subtask 6 Fix Impl Report — Peer-Review Fixes (Round 2)

## Knowledge Consulted
- `docs/v2-design/subtask-6-impl-report.md` — what was built last round, exact line references
- `.agent_context/sessions/1781106672-6469-2465b2ba685c/peer-review-subtask-6-impl-report-sidecar.json` — 2 findings verbatim
- `docs/v2-design/subtask-6-emit-fix-spec.md` — Amended Approach A binding spec
- `docs/v2-design/plan-v2-final-state-gap-closure.md` lines 45-71 — C-04..C-07 acceptance criteria

## Changes

### AGENT REPO — Finding 1 (fence-only emission)

`conversational-search/src/conversational_search/agent/graph.py` — 3 emit sites changed:

- **line 1707** (`_handle_gift_advisor_turn1`): changed `_emit_lbjson_block(_lbjson_fence, ...)` to `_emit_lbjson_block(f"```lbjson\n{takeover_json}\n```", ...)`. `_lbjson_fence` (opener+fence) is preserved unchanged as `final_message` content.
- **line 1820** (`_handle_advice_turn1`): identical change — fence-only `f"```lbjson\n{takeover_json}\n```"` emitted; `_lbjson_fence` unchanged for `final_message`.
- **line 1925** (`_handle_comparison_turn`): `_emit_lbjson_block(f"```lbjson\n{comparison_json}\n```", ...)`. Both `_COMPARISON_MODE_SHIFT_NOTE` and the LLM-produced opener are stripped from the custom event payload; they were already streamed via `messages/partial`. `_lbjson_fence` (full opener+fence) unchanged for `final_message`.

No changes to `custom_events.py` — the fix is entirely at the call sites.

### PROXY REPO — Finding 2 (robust messages/complete suppression)

`conversational-search/conversational-proxy/app/clients/langgraph_client.py` — 3 additions:

- **line 253**: initialise `lbjson_block_forwarded: bool = False` alongside `forwarded_deflection_texts`.
- **line 402**: set `lbjson_block_forwarded = True` inside the `if isinstance(block_text, str) and block_text:` block, after the logger.debug, when the custom event is successfully forwarded.
- **line 470**: in the A1 guard (messages/complete path), added `if lbjson_block_forwarded and "```lbjson" in complete_text: continue` immediately after the existing `forwarded_deflection_texts` check. The fence-marker check is robust regardless of opener-vs-fence mismatch: it triggers whenever a complete message carries an lbjson fence AND the custom event already delivered it.

## Build
NOT RUN (Python — no separate compile step; syntax validated implicitly by pytest import)

## Tests

**Agent suite:** `593 passed, 4 warnings` — `poetry run pytest -q` from `conversational-search/`. Identical baseline to prior round; no regressions.

**Proxy custom-event suite:** `31 passed, 471 deselected` — `ENV=test poetry run pytest -q tests/unit/ -k "custom_event or lbjson or langgraph"` from `conversational-proxy/`. All 5 existing kind handlers plus lbjson_block branch pass.

## Verification

### Gift — cache MISS, opener ONCE, fence ONCE

Cache eviction: `DELETE 0` / count 0 confirmed before run.

SSE `data:` lines (file: `live-subtask6-fix-gift-20260610T211829.txt`):
- Opener streamed as 13 individual token chunks: `"I"`, `"'d love"`, `" to help you find the perfect gift for"`, `" your dad —"`, `" let"`, `"'s"`, `" figure"`, `" out what he"`, `"'d"`, `" truly"`, `" love"`, `"!"`.
- lbjson fence delivered as a single `data:` line (fence-only, no preceding opener text):
  ```
  data: "```lbjson\n{\"shape\": \"chat_takeover\", \"mode\": \"gift_advisor\", ...chips with style=anchored_category_chip...}\n```"
  ```
- `__meta__.cache.status = "MISS"` ✓, `mode = gift_advisor` ✓, `shape = chat_takeover` ✓, `anchored_category_chip` chips ✓.
- `grep -c '```lbjson'` = 1 (fence appears exactly once). Opener tokens appear as individual streaming partials; no second delivery of opener text. Duplication regression: RESOLVED.

### Comparison — cache MISS, text lines ONCE, fence ONCE

Cache eviction: `DELETE 0` / count 0 confirmed before run.

SSE `data:` lines (file: `live-subtask6-fix-comparison-20260610T211852.txt`):
- Opener streamed as individual tokens: `"Both"`, `" brands"`, `" offer"`, `" distinct"`, `" t"`, `"onal characters"`, ... (18 tokens ending with `" choice."`).
- lbjson fence delivered as a single `data:` line (fence-only):
  ```
  data: "```lbjson\n{\"shape\": \"side_by_side_comparison\", \"mode\": \"comparison\", \"mode_shift_note\": \"comparison detected, swapping side-by-side for this turn\", ..., \"columns\": [{\"slot\": \"left\", \"label\": \"Fender\",...}, {\"slot\": \"right\", \"label\": \"Yamaha guitars\",...}]}\n```"
  ```
- `__meta__.cache.status = "MISS"` ✓, `mode = comparison` ✓, `shape = side_by_side_comparison` ✓, Fender left / Yamaha guitars right ✓.
- `grep -c '```lbjson'` = 1. No text-line duplication. `_COMPARISON_MODE_SHIFT_NOTE` appears inside the fence JSON as `mode_shift_note` field — it was not streamed as a separate token by the LLM (the LLM generated a different opener sentence); stripping it from the custom event payload was correct. Duplication regression: RESOLVED.

**Not exercised, and why:**
- Advice turn-1 live test: wired identically to gift handler (same fence-only pattern, same `takeover_json` variable); C-04..C-07 acceptance is gift+comparison only.

## Notes

1. **Comparison mode-shift note:** `_COMPARISON_MODE_SHIFT_NOTE` is prepended to `_lbjson_fence` (the `final_message` content) but the LLM's actual streamed output for this query was a different opener sentence — the mode-shift note was NOT streamed as a free-text token in this run. It is embedded in the fence JSON as `mode_shift_note`. Stripping it from the custom event is safe: if it were ever streamed as a prefix, that would be double-delivery (the finding's concern); the fence JSON already carries it for FE rendering.

2. **lbjson_block_forwarded vs forwarded_deflection_texts:** A naive `forwarded_deflection_texts.add(fence_only_string)` would fail to match the `final_message` content (which is opener+fence). The `lbjson_block_forwarded` boolean flag + fence-marker substring check is the correct discriminant and does not require exact-string matching.

3. **Per-repo changed file list (for C-21 separate-commit step):**
   - **Agent repo** (`conversational-search/`): `src/conversational_search/agent/graph.py`
   - **Proxy repo** (`conversational-search/conversational-proxy/`): `app/clients/langgraph_client.py`

Findings emission self-check: 0 new discoveries beyond the 2 fixed findings (both resolved by code changes; no novel constraints encountered). 0 emissions.

Peer-review: applies

Completeness-risk: none — the change set is mechanically enumerable (3 named call sites in graph.py, 3 lines in langgraph_client.py); no meaning-bound enumeration required.

---

## Round 3 revisions (peer-review)

### Finding 1 disposition — IMPORTANT (blanket continue drops opener on complete-only path)

**RESOLVED.** The reviewer's scenario (`lbx.lbjson_block` custom event → `messages/complete` id='msg-1' content='Opener text\n```lbjson...'` for an unseen msg_id) previously hit the blanket `continue` at `langgraph_client.py:470-471`, dropping the whole message including the opener.

Fix (`langgraph_client.py` lines 465-481 post-round-3): instead of `continue`, we now:
1. Strip only the ` ```lbjson...``` ` block from `complete_text` using `re.sub(r"```lbjson.*?```", "", ..., flags=re.DOTALL).strip()`.
2. If the residual is empty (fence-only message, no opener) → `continue` as before.
3. Otherwise rewrite `chunk.data[0]["content"] = stripped` so the downstream text-extraction path at ~line 561 sees only the opener, not the fence.

The existing `seen_partial_msg_ids` guard (line 447) still handles the streaming case (msg_id seen → `continue` before we even reach the lbjson strip), so the opener is NOT re-yielded when it was already streamed via partials. The lbjson fence is NOT re-delivered because the fence was stripped from `chunk.data[0]["content"]`. Net: fence once, opener once on all paths.

`re` added to the import block (line 4).

### Finding 2 disposition — ADVISORY (proxy test overstated; lbjson_block branch uncovered)

**RESOLVED.** Two new unit tests added in `test_stream_result.py` under `TestLbjsonBlockWireContract`:
- `test_lbjson_block_streaming_dedup_fence_not_doubled_opener_not_re_yielded`: covers the streaming case (partial seen → complete suppressed; fence not doubled).
- `test_lbjson_block_complete_only_opener_preserved_fence_not_doubled`: covers the reviewer's exact complete-only scenario (unseen msg_id → opener preserved, fence not doubled).

Proxy custom-event suite now reports **33 passed** (was 31).

### Changes (Round 3)

- `conversational-search/conversational-proxy/app/clients/langgraph_client.py` line 4: added `import re`.
- `conversational-search/conversational-proxy/app/clients/langgraph_client.py` lines 465-481: replaced blanket `continue` with surgical fence-strip + content rewrite; residual empty → still `continue`.
- `conversational-search/conversational-proxy/tests/unit/test_stream_result.py`: added `make_lbjson_block_chunk` helper and `TestLbjsonBlockWireContract` class with 2 tests (streaming dedup case + complete-only opener-preserved case).

No changes to agent repo (graph.py round-2 fence-only emit stays as-is).

### Tests (Round 3)

**Proxy custom-event suite:** `33 passed, 471 deselected` — `ENV=test poetry run pytest -q tests/unit/ -k "custom_event or lbjson or langgraph"` from `conversational-proxy/`. Up from 31; both new tests green.

**Agent suite:** `593 passed, 4 warnings` — `poetry run pytest -q` from `conversational-search/`. No regressions.

### Verification (Round 3)

**GIFT — `a gift for my dad`** (file: `live-subtask6-r3-gift.txt`):
- Cache eviction: `DELETE 0` / count 0 confirmed.
- `__meta__.cache.status = "MISS"` ✓
- lbjson fence count: `grep -c '```lbjson'` = **1** ✓
- Fence `data:` line: `data: "` ` ```lbjson\n{"shape": "chat_takeover", "mode": "gift_advisor", ..., "chips": [...anchored_category_chip...]}` `\n` ` ``` "`
- Opener streamed as individual partial tokens: `"I"`, `"'d love"`, `" to help you find the perfect gift for"`, `" your dad —"`, ... `"!"` — no second delivery of opener on complete path ✓

**COMPARISON — `compare Fender vs Yamaha guitars`** (file: `live-subtask6-r3-comparison.txt`):
- Cache eviction: `DELETE 0` / count 0 confirmed.
- `__meta__.cache.status = "MISS"` ✓
- lbjson fence count: `grep -c '```lbjson'` = **1** ✓
- Fence `data:` line begins: `data: "` ` ```lbjson\n{"shape": "side_by_side_comparison", "mode": "comparison", "mode_shift_note": "comparison detected...", ...columns: [Fender, Yamaha guitars]...}` `\n` ` ``` "`
- Opener streamed as individual partial tokens: `"Both"`, `" brands"`, `" offer"`, ... `" choice."` — no re-delivery ✓

**Per-repo changed file list (for C-21 separate-commit step, updated):**
- **Agent repo** (`conversational-search/`): `src/conversational_search/agent/graph.py` (round-2 only; unchanged this round)
- **Proxy repo** (`conversational-search/conversational-proxy/`): `app/clients/langgraph_client.py`, `tests/unit/test_stream_result.py`

Peer-review: applies

Completeness-risk: none — two findings, both dispositioned with exact file+line citations; the change set is mechanically enumerable.
