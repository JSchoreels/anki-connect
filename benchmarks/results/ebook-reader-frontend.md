# Ebook-reader cache batching and virtual token panel

Measured on 2026-09-21 with **夢が覚めるまで** and the running updated Anki core.
The reader now batches word-cache access and mounts only a window of token rows.
All **24,406 occurrences and 4,289 unique words** are still analyzed.

## Baseline first, then alternating comparison

Before editing production reader code, five cold/warm pairs against live Anki
completed without errors: median full token-panel loading was **8.01 s cold**
and **3.32 s warm**. A frozen copy of that reader was retained.

After implementation and verification, five alternating original/optimized
pairs were measured, each including cold and warm loads: **20 measured loads**.
Both readers used the same browser origin, EPUB, settings, and running Anki core
through a read-only HTTP proxy. Separate Vite servers supplied the original and
changed reader code. Compilation/import pilots were excluded. No builds or tests
ran during measured loads.

Use the following paired comparison for the improvement figures. Do not mix its
results with the earlier baseline session: the live environment varied between
and within sessions.

| Measurement | Original | Optimized | Median reduction |
|---|---:|---:|---:|
| Warm complete panel | 4.155 s | 2.950 s | **29.0%** |
| Cold complete panel | 10.902 s | 9.214 s | **15.5%, noisy** |
| Warm token/status analysis | 1.014 s | 0.752 s | 25.9% |
| Warm analysis-to-paint opportunity | 0.769 s | 0.017 s | 97.8% |
| Cold analysis-to-paint opportunity | 0.724 s | 0.014 s | 98.0% |
| Mounted rows at completion | 4,289 | 39 | Complete data retained |

Warm full-load ranges were **3.090–4.739 s original**, **2.474–3.024 s optimized**.
All five optimized warm runs were faster than their paired originals, by
0.141–1.715 s (median paired saving: 1.280 s).

Cold ranges were **8.306–11.762 s original**, **7.416–11.005 s optimized**.
Two of five optimized cold runs were slower than their paired originals. The
median paired saving was 0.757 s. Cold analysis itself did not improve in this
sample (6.352 s original versus 6.717 s optimized). The lower cold total median
should therefore be treated as an exploratory result, not a stable promised gain.

Full-panel timing starts at navigation and ends after completed analysis, result
hashing, and two animation frames. This is a paint opportunity, not a compositor
presentation timestamp or a pure rendering measurement. Stage medians do not
sum to the full-load median. The large test viewport was 5120 × 2880 CSS pixels
at devicePixelRatio 0.5; smaller viewports mount fewer rows.

## What changed

The shared word resolver bulk-loads words and cached lemmas through existing
IndexedDB APIs. It retains candidate order, status priority, dictionary
enrichment, missing-word handling, and alias persistence. Alias writes are
collected per existing group of 25 tokens. The lookup maps live only for one
resolution batch, so later calls read current data. No cache schema, dependency,
AnkiConnect API, or cache-refresh policy was changed.

The token panel keeps the complete sorted/filtered model but renders a measured
window with an overscan buffer. It measures variable row heights, including
expanded sentences, scrolls to offscreen selections by their model position,
retains a focused row when scrolling, and exposes full list positions/counts.

Browser verification also found that the existing horizontal writing-mode value
contained literal quotes, which made it invalid. In vertical books the panel
inherited vertical writing. The value is now valid `horizontal-tb`, as intended
by the original style. The measured UI improvement includes this correction;
virtualization was not isolated from that layout fix.

## Operation counts and remaining work

Separate warm diagnostic runs, excluded from timing samples, recorded:

| Cache method | Original calls | Optimized calls |
|---|---:|---:|
| getWordData | 8,094 | 0 |
| getLemmas | 2,167 | 0 |
| setWordData | 1,439 | 0 |
| getWordDataBatch | 0 | 18 |
| getLemmasBatch | 1 | 19 |
| setWordDataBatch | 49 | 221 |

The batch-write totals include the unchanged 49 warm-cache writes. This replaces
11,700 individual word/lemma reads and word writes with bounded transactions.
Dictionary cache access remains: 733 getTermEntries and 728 setLemmas calls in
both diagnostics. Cold dictionary enrichment and the existing full-deck refresh
remain candidates for a later experiment; neither was changed here.

## Correctness and verification

- All twenty measured loads completed without errors, with exactly matching full
  analysis hashes between readers within each cache condition. Diagnostics match
  those hashes too. The pre-existing cold/warm hash difference remains.
- **57 reader tests passed**. New cases cover scalar/batch parity, lookup
  priority, alias persistence, current values after edits, expired lemmas,
  operation counts, variable-height windows, filters, offscreen selection,
  sentence content, focus retention, and horizontal panel styling.
- The two panel tests were rerun after the final style correction and passed.
- The final production build passed. ESLint reported zero errors in changed
  TypeScript files; the existing configuration ignores Svelte files.
- Svelte/type checking reports **50 errors and 4 warnings in both original and
  changed checkouts**. Their normalized error sets match exactly. These include
  existing Svelte 4/5 dependency-type conflicts and unrelated application errors;
  the overall type check is not green.
- Real-browser checks verified filtering, non-overlapping expanded rows,
  reaching token 4,289, and retaining the focused button with a bounded DOM.

The running Anki process was verified to map the local release native library;
its SHA-256 matches the prior native benchmark build. Unrelated reader checkout
changes were preserved. No live collection database files were accessed.

[Full measurements, diagnostics, source hashes, and verification metadata](ebook-reader-frontend.json).
Temporary reader copies, servers, and benchmark browser storage are cleaned up
after saving these results. Production reader changes remain in the ebook-reader
checkout and its build output.
