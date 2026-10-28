# Industry context and system-health monitor

The `/industries` page is a generated checkpoint, not an independently maintained dashboard. Its checked-in data file is rebuilt from the Ledger Pakistan planning and health artifacts:

```bash
cd apps/corpus-browser
npm run sync:industry-packs
npm run lint
npm run build
node --test tests/industry-packs.test.mjs
```

Set `LEDGER_INDUSTRY_PACK_SUMMARY` and `LEDGER_INDUSTRY_PACK_HEALTH` only when the source artifacts live outside the default sibling `ledger-pakistan` checkout.

## Two health layers

Do not collapse collection health and execution health into one percentage.

- Collection health covers the official URL boundary, discovery evidence, access, downloaded holdings, date metadata, and durable refresh evidence.
- Execution health covers the bounded selection queue, page assets, local extraction, OCR needs, indexing, and retrieval readiness.

The existing `artifacts/11_industry_pack_health/health.json` asset and parse percentages use all pack-linked downloaded holdings as their denominator. The approved initial execution boundary is instead the unique selection in `artifacts/13_industry_pack_plans/parse_queue.jsonl`. Until a queue-aware asset and parse runner writes selected-document coverage, the page must show execution as `not started`, not as a failed full-corpus parse.

## Artifact contract

| Artifact | What it proves |
|---|---|
| `artifacts/01_authoritative_download/catalog.json` | Cumulative first-party identities discovered by collection |
| `artifacts/01_authoritative_download/manifest.json` | Original files actually held, with hashes and provenance |
| `artifacts/01_authoritative_metadata/last_applied_audit.json` | Date basis, confidence, conflicts, and unresolved dates |
| `artifacts/11_industry_pack_health/health.json` | Deterministic pack-by-pack stage health |
| `artifacts/13_industry_pack_plans/summary.json` | Industry operating models, selected feeds, and spend boundary |
| `artifacts/13_industry_pack_plans/parse_queue.jsonl` | Documents approved for the first parse pass |
| `artifacts/13_industry_pack_plans/metadata_queue.jsonl` | Documents monitored until an event or query promotes them |
| `artifacts/02_authoritative_assets/manifest.json` | Page PDFs, thumbnails, and object-store coverage |
| `artifacts/03_authoritative_parse/manifest.json` | Searchable pages, OCR needs, and extraction errors |

Folder contents and file modification times do not prove a successful refresh. Scheduler telemetry remains `unknown` until the scheduler records planned time, start, finish, exit code, run ID, and the downloader report used by the health gate.

## Current review

- The URL boundary is defined for all 23 direct packs.
- Discovery is fully green for 9 packs and partial for 14.
- Access has 4 red packs, mainly because the shared OGRA dependency remains unavailable and real-estate operating data is not yet proven.
- Reliable document-year coverage is below the current threshold for consumer goods, healthcare, and sugar.
- Assets and parsing have not started against the bounded selected queue.
- The highest-leverage acquisition gaps are OGRA across 8 industries, NTC trade remedies across 5, and SECP regulatory updates across 5.
- Five packs should not receive broad parsing spend until product, transport-mode, or exposure mappings exist: chemicals, glass and ceramics, consumer goods, transport and logistics, and industrial products.

## Next gate

The next execution milestone is a queue-aware first pass that records, for every selected document, requested pages, pages produced, extraction outcome, OCR count, metadata revisions, and timestamps. The HTML page should then derive asset and parse coverage from that selected-document ledger rather than from the entire downloaded corpus.
