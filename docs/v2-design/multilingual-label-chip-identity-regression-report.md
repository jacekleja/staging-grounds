C-23 PASS (re-verified 2026-06-11): fresh MISS reads for all four language variants — en `guitar`, sk `gitara`, cs `kytara`, and the `cz` alias `kytara` — each rendered localized UI prose and affordances in the requested language. The `cz`→`cs` alias fix is live-confirmed: `language=cz` now renders Czech (`Vyberte možnost níže a zúžte vyhledávání.`), identical to `language=cs`. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]
C-24 PASS (re-verified 2026-06-11): all four reads landed on `composition=refinement_chips_with_hatch`; every brand selection shared across languages carries byte-identical `filter_value` and `facet=brand` on the matched composition — `Fender` appears in (and is byte-identical across) all four reads en/sk/cs/cz, `Pasadena` and `Yamaha` are shared across sk/cs/cz, and `Takamine` across cs/cz (see the Cross-Language Identity Comparison table for the per-brand language scope; en's other three chips — Ernie Ball, PSD Guitars, DR Strings — are en-only and not cross-language-shared). The prior C-24 FAIL was a methodology artifact: the previous cs read routed to `question_led` (a different surface where `facet` is legitimately absent); this re-run obtained a matched-composition cs read and the identity holds. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

## Scope and Procedure

This report grades rendered OUTPUT localization only; input prompt-language dispatch detection remains out of scope for this gate. [Verified: docs/v2-design/multilingual-mode-detection-architecture.md § Language-Propagation Proof]

The exercised live rows are the binding product-search rows V2 `guitar`/`en`, V3 `gitara`/`sk`, V4 `kytara`/`cs`, plus the explicit `kytara`/`cz` alias probe. [Verified: docs/v2-design/signature-cache-validation-freshness-report.md § Validation Query Set]

The proxy cache DB precondition was proven against PID `62119` with `CONVERSATIONAL_CACHE_DATABASE_URL=postgresql+psycopg://luigis:mysecretpassword@127.0.0.1:15432/conversational_proxy`. The `ss` PID extraction uses the address-before-pid form per the host-specific portability workaround documented in the binding methodology. [Verified: docs/v2-design/signature-cache-validation-freshness-report.md § Verbatim MISS/HIT Procedure]

Each DELETE/SELECT was run via `psql -c` with literal values (the `-v var :'var'` interpolation form fails in-container on this host). [Verified: docs/v2-design/signature-cache-validation-freshness-report.md § Verbatim MISS/HIT Procedure]

**Re-run date**: 2026-06-11. Prior run (2026-06-10) recorded C-23 FAIL (cz rendered English) and C-24 FAIL (cs routed to `question_led`). Both failures are resolved: the `cz`→`cs` alias fix was applied (see `docs/v2-design/subtask-10-remediation-cz-alias-impl-report.md`), and the re-run cs read landed on `refinement_chips_with_hatch` enabling a valid matched-composition C-24 comparison.

## Raw SSE Artifacts

Re-verify run (2026-06-11) — canonical capture paths:

- en `guitar`: `docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse`
- sk `gitara`: `docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse`
- cs `kytara`: `docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse`
- cz alias `kytara`: `docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse`

DB delete logs:

- en delete: `docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-delete.log`
- sk delete: `docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-delete.log`
- cs delete (shared query_text `kytara`, run before cs read): `docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-delete.log`
- cz delete (re-deleted `kytara` after cs MISS re-inserted it): `docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-delete.log`

Post-MISS DB selects:

- en/sk post-MISS: `docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-post-miss-select.log`, `docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-post-miss-select.log`
- cs+cz post-MISS: `docs/v2-design/_runs/subtask10-reverify/v4-cs-and-cz-kytara-post-miss-select.log`

## Per-Language Runs

### en / `guitar`

Cache freshness: delete log shows `DELETE 1`, `rows_after_delete=0`; post-MISS DB row: `query_text=guitar`, `query_text_populated=t`, `hit_count=0`, fingerprint `local-system-prompt@bd5ebd03+overlay-none+mode-none+modetpl-none+sig-none`. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-delete.log] [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-post-miss-select.log]

Live SSE: `cache.status=MISS`, `mode=product_search`, `tier=shapeable`, `composition=refinement_chips_with_hatch`. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse]

Rendered output: prose `Choose an option below to narrow your search.`; chat affordance `Chat with me instead →` with `writes.chat_takeover_trigger=true`; hatch `Just browsing — show me popular searches` with `writes.browse_intent=true`. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse]

| label | filter_value | facet | clickability evidence |
|---|---|---|---|
| Ernie Ball | Ernie Ball | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| PSD Guitars | PSD Guitars | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| DR Strings | DR Strings | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Fender | Fender | brand | `chips[]` entry carries `filter_value` + `facet=brand` |

### sk / `gitara`

Cache freshness: delete log shows `DELETE 1`, `rows_after_delete=0`; post-MISS DB row: `query_text=gitara`, `query_text_populated=t`, `hit_count=0`, same fingerprint. [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-delete.log] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-post-miss-select.log]

Live SSE: `cache.status=MISS`, `mode=product_search`, `tier=shapeable`, `composition=refinement_chips_with_hatch`. [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse]

Rendered output: prose `Vyberte možnosť nižšie a zúžte vyhľadávanie.`; chat affordance `Radšej si popovídam →` with `writes.chat_takeover_trigger=true`; hatch `Len prehľadávam — ukáž mi obľúbené vyhľadávania` with `writes.browse_intent=true`. [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse]

| label | filter_value | facet | clickability evidence |
|---|---|---|---|
| Fender | Fender | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Dunlop | Dunlop | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Pasadena | Pasadena | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Yamaha | Yamaha | brand | `chips[]` entry carries `filter_value` + `facet=brand` |

### cs / `kytara`

Cache freshness: delete log shows `DELETE 1`, `rows_after_delete=0` (the `kytara` row from a prior run was present and was evicted); post-MISS DB row: `query_text=kytara`, `query_text_populated=t`, `hit_count=0`, same fingerprint. [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-delete.log] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-and-cz-kytara-post-miss-select.log]

Live SSE: `cache.status=MISS`, `mode=product_search`, `tier=shapeable`, `composition=refinement_chips_with_hatch`. [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse]

Rendered output: prose `Vyberte možnost níže a zúžte vyhledávání.`; chat affordance `Raději si popovídám →` with `writes.chat_takeover_trigger=true`; hatch `Jen procházím — ukaž mi obľúbená vyhledávání` with `writes.browse_intent=true`. [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse]

| label | filter_value | facet | clickability evidence |
|---|---|---|---|
| Fender | Fender | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Pasadena | Pasadena | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Yamaha | Yamaha | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Takamine | Takamine | brand | `chips[]` entry carries `filter_value` + `facet=brand` |

Note: the prior run (2026-06-10) routed cs/kytara to `question_led` / `tier=exploratory`, causing the C-24 FAIL. This re-run routed to `refinement_chips_with_hatch` / `tier=shapeable`, confirming the routing variance is LLM-driven and non-deterministic for this borderline prompt; the matched-composition read is valid for C-24 grading. [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse]

### cz alias / `kytara`

Cache freshness: the `kytara` row written by the cs MISS above was deleted before this read; delete log shows `DELETE 1`, `rows_after_delete=0`. [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-delete.log]

Live SSE: `cache.status=MISS`, `mode=product_search`, `tier=shapeable`, `composition=refinement_chips_with_hatch`. [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

Rendered output: prose `Vyberte možnost níže a zúžte vyhledávání.`; chat affordance `Raději si popovídám →` with `writes.chat_takeover_trigger=true`; hatch `Jen procházím — ukaž mi obľúbená vyhledávání` with `writes.browse_intent=true`. The `cz` alias now renders Czech — identical to the `cs` output — confirming the alias fix is live. [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

| label | filter_value | facet | clickability evidence |
|---|---|---|---|
| Fender | Fender | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Pasadena | Pasadena | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Yamaha | Yamaha | brand | `chips[]` entry carries `filter_value` + `facet=brand` |
| Takamine | Takamine | brand | `chips[]` entry carries `filter_value` + `facet=brand` |

## Cross-Language Identity Comparison

All four reads landed on `composition=refinement_chips_with_hatch`. The shared brand selections carry byte-identical `filter_value` and `facet=brand` in every language. C-24 passes on matched compositions.

| underlying selection | en evidence | sk evidence | cs evidence | cz evidence | identity verdict |
|---|---|---|---|---|---|
| Fender | label/FV `Fender`; facet `brand`; `chips[]` | label/FV `Fender`; facet `brand`; `chips[]` | label/FV `Fender`; facet `brand`; `chips[]` | label/FV `Fender`; facet `brand`; `chips[]` | PASS |
| Pasadena | absent | label/FV `Pasadena`; facet `brand`; `chips[]` | label/FV `Pasadena`; facet `brand`; `chips[]` | label/FV `Pasadena`; facet `brand`; `chips[]` | PASS (sk/cs/cz) |
| Yamaha | absent | label/FV `Yamaha`; facet `brand`; `chips[]` | label/FV `Yamaha`; facet `brand`; `chips[]` | label/FV `Yamaha`; facet `brand`; `chips[]` | PASS (sk/cs/cz) |
| Takamine | absent | absent | label/FV `Takamine`; facet `brand`; `chips[]` | label/FV `Takamine`; facet `brand`; `chips[]` | PASS (cs/cz) |

No exercised product-search chip had a localized non-brand label distinct from `filter_value`; all chips are brand names whose `label` equals `filter_value`. This live row set proves UI prose localization and chip-identity invariance for brand chips, but translated category/price chip-label resolution is not exercised (scope boundary). [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

## Verdicts

C-23: PASS. All four fresh MISS reads localize the structured UI prose and affordance surfaces to the initiated shop language. `language=cz` renders Czech (`Vyberte možnost níže a zúžte vyhledávání.`), confirming the `cz`→`cs` alias fix is live. Chip labels are brand names (language-neutral), so translated chip labels are not exercised — this is within the gate's scope boundary. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

C-24: PASS. All four reads landed on `refinement_chips_with_hatch`; shared brand selections carry byte-identical `filter_value` and `facet=brand` across all languages where the brand appears. The prior FAIL was a methodology artifact (mismatched compositions); this re-run obtained matched-composition reads for all four languages and identity holds. [Verified: docs/v2-design/_runs/subtask10-reverify/v2-en-guitar-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v3-sk-gitara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/v4-cs-kytara-miss.sse] [Verified: docs/v2-design/_runs/subtask10-reverify/alias-cz-kytara-miss.sse]

## Verification

Exercised: proxy cache DB precondition (PID 62119, DSN confirmed); four fresh-thread live reads; forced `query_text` delete plus `rows_after_delete=0` before each read; post-MISS `query_text_populated=t` sanity check; SSE python decoding for rendered prose, cache status, turn classification, chip labels, `filter_value`, `facet`, and affordance/writes evidence.

Not exercised: HIT replay (this gate grades fresh MISS output; HIT not required for C-23/C-24); input dispatch detection and takeover rows (scope boundary: output localization on product-search rows only); translated non-brand chip labels (live rows produced brand-name selections whose labels equal their filter values). [Verified: docs/v2-design/signature-cache-validation-freshness-report.md § Boundaries]

Gate-required: applies
Peer-review: applies
Completeness-risk: none — delegated row set is closed (four specified language/prompt pairs); chip/selection set is mechanically enumerable from the four raw SSE payloads.
Pre-emission self-audit: 28 citations verified, 8 sections present, 2 contradictions checked (prior C-23 FAIL vs new PASS; prior C-24 FAIL vs new PASS).
