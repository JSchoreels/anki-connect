# Compact card details: ebook benchmark

Measured on 2026-09-20 with **夢が覚めるまで**. Combining card loading, current
FSRS metrics, and selected note fields reduces the five backend batches from
**490 ms to 192 ms (61%)** compared with the previous native-metrics path.
Full warm reader loading improves from **3.51 s to 3.22 s (8.4%)**. Cold loading
remains **7.81 s**: tokenization overlaps Anki loading and hides the backend gain.

## Implementation and compatibility

Anki's new `Collection.card_details()` RPC loads each requested card once and
reuses those cards for current metrics. Request-local note and note-type maps
avoid duplicate work. The storage helper borrows the stored field text and
allocates only selected strings; it still reads the stored field blob. Unselected
note fields never cross into Python. No full review-history statistics are needed
on the normal optimized path.

AnkiConnect detects this capability inside existing `cardsDetails` calls;
`findCards` also benefits through its existing delegation. Request and response
formats, field order, card order, duplicates, raw note text, and `prop:d` as
difficulty are preserved. No client changes are required. Missing core support,
incomplete card/note results, or reviewed cards needing legacy memory-state
initialization use the original path. Backend errors still propagate. Plain
scheduling-field requests skip FSRS calculations, and omitted note fields skip
note reads. There is no cache across requests and no raw SQL in the add-on.

## Method

The actual ebook-reader development server was loaded in an isolated browser
origin. Each backend opened its own copy of a supported AnkiConnect deck export
of `Japan::1. Vocabulary`. The reader requested `Front` / `Front_Kana` and its
usual scheduling/FSRS fields. The reader checkout was unchanged.

Both arms used the same rebuilt Anki core and original 25 ms timer transport.
`current` disabled only the combined details getter, retaining native batch FSRS
metrics; `prototype` used the new production getter. Five alternating pairs,
each with cold and warm reader caches, produced **20 measured full loads**.
Pilots were excluded. Cold means cleared reader vocabulary, lemma, tokenization,
and frequency caches; application assets, OS caches, and Yomitan may be warm.

Every run completed with **24,406 token occurrences and 4,289 unique words**.
The Anki workload contained 11,736 cards and 12,158 vocabulary forms. Each load
made seven Anki requests: version, findCards, and five cardsDetails batches.
Cold runs made 840 Yomitan requests; warm runs made none. Analysis hashes matched
between variants within each cache condition, and no benchmark errors occurred.
Cold and warm hashes differ in the same pre-existing way in both arms.

## Full reader results

Medians with observed min–max ranges, in milliseconds. Full panel time includes
complete analysis and a subsequent double-animation-frame paint opportunity.
Anki HTTP time sums request durations and must not be added to total load time.

| Measurement | Previous native metrics | Combined getter | Reduction |
|---|---:|---:|---:|
| Cold full panel | 7,811 (7,746–8,027) | 7,810 (7,589–7,929) | No meaningful change |
| Warm full panel | 3,511 (3,411–3,575) | 3,217 (3,055–3,318) | 8.4% |
| Cold Anki HTTP | 830 (802–867) | 482 (472–518) | 41.9% |
| Warm Anki HTTP | 809 (792–842) | 501 (480–569) | 38.0% |
| Cold cache warming | 1,566 (1,524–1,590) | 1,187 (1,184–1,286) | 24.2% |
| Warm cache warming | 1,470 (1,449–1,498) | 1,149 (1,125–1,223) | 21.8% |

Book DOM availability remained around 0.30 seconds. These are paired development
server measurements, not a promise of identical installed-app timings. Compare
these arms with each other; earlier reports used different runtime conditions.

## Backend timing and profile

Seven alternating unprofiled repetitions measured all five cardsDetails batches:

| Backend path | Median |
|---|---:|
| Original full statistics | 1,950 ms |
| Previous native metrics | 490 ms |
| Native metrics plus earlier note-projection prototype | 269 ms |
| New combined production getter | 192 ms |

The combined getter ranged from 188 to 217 ms; the previous native-metrics path
ranged from 486 to 509 ms. Stable response values hashed identically across all
four variants. Maximum retrievability drift from elapsed wall time was 0.00002885.
JSON encoding took 17.7 ms for the 2,663,798-byte response.

The diagnostic cProfile run took 217 ms, including five native `card_details`
calls totaling 145 ms and about 40 ms of Python response assembly self time.
It made **zero Python per-card getCard/get_note calls** and no separate
card_memory_metrics RPC. The native command accounted for 142 ms; Python cProfile
cannot break down its internal Rust cost.

The remaining larger opportunity for cold loads is the reader/Yomitan path.
Further backend work could target native field extraction or response assembly,
but this profile does not establish which internal Rust operation dominates.
Transport changes are a separate experiment; both arms here kept it unchanged.

## Verification and availability

- Rebuilt Anki core: `just check` passed, including the new Rust/Python cases.
- Add-on regression suite on that core: **170 passed, 1 skipped**.
- Same suite on stock Anki: **163 passed, 8 skipped**; native-only cases skipped.
- Installed older core targeted tests: **32 passed, 7 skipped**.
- Tests cover response parity, selection semantics, ordering, duplicate/missing
  IDs, legacy fallback/errors, fresh reads after edits, and operation counts.

The add-on package was rebuilt and its runtime files synced and verified.
**The optimized path also requires the updated Anki core.** The currently
installed `/Applications/Anki.app` core (`6a504b35`) lacks these APIs and will use
the compatible fallback. Restarting that older app alone does not enable this
speedup. The new core is built locally, not installed as a replacement app.

The tested core reports `26.09b3+fsrs7` / `bb9ff481`; uncommitted source changes
mean that build identifier alone is insufficient to reproduce it. The saved JSON
includes source hashes and the exact release native-library hash.

Artifacts: [all measurements and source hashes](ebook-reader-card-details.json),
[previous native-metrics profile](ebook-reader-card-details-native-profile.txt),
and [combined getter profile](ebook-reader-card-details-native_details-profile.txt).
Private exported collections and disposable browser storage are removed after
measurement; only aggregate counts, timings, and hashes are retained here.
