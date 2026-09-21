# Real ebook-reader A/B benchmark

**Follow-up:** [profiling with the installed Anki build](ebook-reader-profile.md) tests a new selected-field batching prototype. This earlier report uses stock Anki 25.9.2 and the transport-only prototype.

Book: **夢が覚めるまで (ストレートエッジ)** — 45,634 characters, 24,406 token occurrences, 4,289 unique tokens. Vocabulary deck: 11,736 cards / 12,158 cached word forms.

**Conclusion:** these prototypes do not establish a dependable end-to-end speedup for this book. Anki HTTP time decreased slightly, but complete-reader medians changed by only about 0.2 seconds cold and 0.1 seconds warm, within overlapping run-to-run variation. No measured result differences between backends.

## Five paired runs per cache condition

All values below are medians in seconds. Cold/warm refer to reader vocabulary/token/lemma caches. The EPUB is already imported, as it is when opening `/b?id=1`.

| Measurement | Cache | Current | Prototype | Median reduction |
|---|---|---:|---:|---:|
| Reader DOM available | cold | 0.356 | 0.362 | -1.7% |
| Reader DOM available | warm | 0.407 | 0.375 | 7.8% |
| All word analysis ready | cold | 12.667 | 12.422 | 1.9% |
| All word analysis ready | warm | 7.261 | 7.305 | -0.6% |
| Word panel paint opportunity | cold | 13.360 | 13.169 | 1.4% |
| Word panel paint opportunity | warm | 7.956 | 8.057 | -1.3% |
| Total Anki HTTP + JSON time | cold | 5.330 | 5.161 | 3.2% |
| Total Anki HTTP + JSON time | warm | 5.099 | 4.980 | 2.3% |
| Vocabulary cache refresh | cold | 5.992 | 5.765 | 3.8% |
| Vocabulary cache refresh | warm | 5.859 | 5.795 | 1.1% |
| Document token analysis | cold | 6.144 | 6.121 | 0.4% |
| Document token analysis | warm | 0.977 | 1.011 | -3.5% |

A negative reduction means the prototype median was slower. Stage timings overlap (initial tokenization runs alongside vocabulary warming); do not add every stage together.

| Full word-panel range | Current | Prototype |
|---|---:|---:|
| cold | 12.77–14.05 s | 11.97–17.36 s |
| warm | 7.93–8.64 s | 7.50–8.66 s |

## What this workload actually exercises

- Seven Anki calls per run: one `version`, one `findCards`, and five sequential `cardsDetails` batches (2,500 / 2,500 / 2,500 / 2,500 / 1,736 cards).
- `cardsDetails` requests `prop:r`, `prop:s`, `due`, `queue`, `type`, and note fields `Front` / `Front_Kana`. It still needs per-card FSRS stats; the lazy-stats shortcut does not apply.
- No modification-time calls: the earlier metadata bulk-read speedups do not apply.
- Cold reader caches produce 840 Yomitan requests; warm reader caches produce zero. Cold runs still spend several seconds resolving all token statuses after initial tokenization.
- Opening the token panel refreshes the Anki vocabulary even on warm-cache navigation. The reader already batches card requests, which limits transport savings.
- First EPUB parsing was 89 ms in the corrected setup (337 ms in an earlier setup); these are untimed setup observations, not paired performance comparisons or part of reopening an imported book.

## Compatibility and checks

- All 20 measured browser runs: 24,406 token occurrences, 4,289 unique tokens, 11,736 fetched cards; no captured API/method errors.
- Entire analysis result hashes match across both backends and all five repeats **within each cache condition**, including token frequencies, first-occurrence indices, status, due flags and card IDs.
- Card payload fields match exactly except time-dependent `prop:r`. Maximum observed sequential-call drift: 0.000001967; stability, scheduling, IDs, and note fields matched exactly.
- `rtk proxy python3 -m pytest -q benchmarks`: **112 passed, 1 skipped**. The skip remains the original blocking transport in the nonblocking-upload assertion.
- Real integration exposed missing API metadata on the lazy-stats benchmark function when combined with cached dispatch. Its decorator is now preserved and both dispatch variants have a regression test. Production code was unchanged.

## Method and limits

Both backends use copies of the same deck exported through AnkiConnect with scheduling. The running ebook-reader app is loaded through a separate localhost origin. Only proxy responses receive timing hooks and automatic token-panel opening; neither source checkout nor the installed add-on is changed. Runs alternate current-first / prototype-first; all measured samples, including slower prototype runs, are retained.

- Five samples per arm; normal shared-machine load, no CPU isolation or statistical significance claim.
- Reader caches cleared for cold runs; EPUB already imported, Vite assets/backend OS/Yomitan service may be warm.
- Full token panel opened automatically on navigation through an in-memory proxy response change.
- Paint metric is two animation frames after analysis/hash completion, not a browser compositor measurement.
- Anki library 25.9.2 in separate QCoreApplication processes; live Anki GUI and other add-ons not included.
- App uses default thresholds and beginning-of-book position, not all personal-profile settings.
- Cold/warm result hashes differ in both variants: observed one extra mature word and one fewer unmined/due word after warming; no backend-specific difference.
- Vite hot-reload websocket is unavailable through proxy; app loaded normally and manual navigation was used.

Potential next investigations: the cost of obtaining FSRS metrics for every card, the token panel’s unconditional vocabulary refresh, and cold Yomitan lookups. These are findings, not implemented changes.

Raw samples, request timings, hashes and environment: [ebook-reader.json](ebook-reader.json). Reproduction: [benchmark README](../README.md). Temporary book/deck data is excluded from these artifacts.
