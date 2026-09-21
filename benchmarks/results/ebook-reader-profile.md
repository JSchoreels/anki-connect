# AnkiConnect profiling: real reader workload

**Recommendation:** prefetch only the requested note fields, using request-scoped batches. This gave an 11.2% backend improvement without changing the API response shape or replacing Anki’s FSRS calculations. Do not batch entire note objects: that experiment was 5.1% slower.

All results below use the **installed Anki 26.09b3+fsrs7 build 6a504b35**, imported directly from `/Applications/Anki.app` using a compatible Python 3.13 interpreter. The earlier [transport-only reader benchmark](ebook-reader.md) used stock Anki 25.9.2. Those absolute timings and FSRS values should not be substituted for this installed build. Both arms here use the same installed build and exported deck snapshot.

## Where the backend time goes

One cProfile run over the same five 2,500-card batches (last batch 1,736):

| Component | Calls | Profiled time | Share |
|---|---:|---:|---:|
| FSRS card statistics | 11,736 | 1.729 s | 67.4% |
| Note loading | 11,736 | 0.555 s | 21.6% |
| Card loading | 11,736 | 0.172 s | 6.7% |

Total profiled request computation: 2.566 s. These cumulative rows are distinct stages; remaining time is response construction and Python overhead. JSON serialization outside profiling took a median 20.1 ms for 2,663,475 response bytes.

`card_stats_data()` invokes native `card_stats`, which constructs a complete card statistics response including review-log history and historical memory states. The reader only requests current `prop:r`, `prop:s`, scheduling fields and two note fields. Python profiling identifies the native call as the hotspot; it does not split time inside Rust into individual history, configuration, and formula operations.

The selected cards’ notes contain 35 fields totaling **122,042,605 bytes**. Original note loading transfers and builds all fields although the response needs only `Front` / `Front_Kana`. Fetching the full notes in batches simply moves that large payload through the SQL/JSON bridge and was slower. Selecting the requested fields inside Anki avoids most of that transfer.

## Tested AnkiConnect-only prototype

- Read note IDs and note types in bounded batches, then project only requested fields with Anki’s native `field_at_index()` helper.
- Group by note type so different field ordering is handled correctly. Parameter counts remain bounded to 999 including field-index parameters.
- Reuse the existing validation, card loading, per-card FSRS call and response construction. Preserve input order, duplicate IDs, field names/order metadata, null metrics and error handling covered by tests.
- Keep data only for the current request; later note edits are immediately visible. No persisted schema, configuration, client call, or API response changes.

Backend-only seven alternating pairs, unprofiled, including all five batches:

| Variant | Median | Range |
|---|---:|---:|
| Current | 2.166 s | 2.136–2.255 s |
| Selected-field prefetch | 1.923 s | 1.896–2.005 s |

Every selected-field sample was faster than every current sample in this run. Stable payload fields matched exactly over all 11,736 cards. Maximum observed time-dependent retrievability drift was 0.000001014 between sequential calls; the prototype uses the same original native stats call.

## Actual ebook-reader verification

Book: **夢が覚めるまで**, 45,634 characters, 24,406 token occurrences, 4,289 unique tokens. Three alternating pairs for each cache condition, after two excluded pilots. The prototype arm includes selected-field prefetching plus the previous event-driven transport, incremental parser, and validation/dispatch caches.

| Measurement | Cache | Current median | Prototype median | Reduction |
|---|---|---:|---:|---:|
| Anki requests + JSON parsing | cold | 2.544 s | 2.023 s | 20.5% |
| Anki requests + JSON parsing | warm | 2.399 s | 1.941 s | 19.1% |
| Full word analysis ready | cold | 9.302 s | 8.798 s | 5.4% |
| Full word analysis ready | warm | 4.561 s | 4.163 s | 8.7% |
| Word panel paint opportunity | cold | 9.970 s | 9.477 s | 4.9% |
| Word panel paint opportunity | warm | 5.333 s | 4.951 s | 7.2% |

Warm panel loads improved in all three pairs. Cold runs remain noisy (one prototype run was slower); cold medians alone do not establish a dependable user-visible gain. All samples, including the slower run, are retained.

All 12 measured runs completed without captured API/method errors. The complete analysis hash matches across both variants and repeats within each cache condition. Cold and warm hashes differ in the reader itself, equally across variants. The panel was also checked through the browser UI.

## Bigger opportunity and compatibility boundary

The largest remaining cost is the complete FSRS/card-statistics call. A dedicated native batch metrics operation returning current retrievability, stability and desired retention could avoid building review history. AnkiConnect could use it when available and fall back to `card_stats_data()` otherwise, retaining its current API for clients. **This needs an Anki backend capability; it has not been implemented here.**

The installed FSRS-7 build uses full memory state (including both stability traces and difficulty). Its scalar retrievability compatibility helper explicitly assumes a default difficulty and equal traces; the older SQL scalar shortcut also does not reproduce the full model. Replacing current metrics with either would violate the compatibility goal. Reading only cached stability/retention helps requests that do not need retrievability, but this reader requests it.

For comparison, the [official Anki 25.09.2 card-stats source](https://github.com/ankitects/anki/blob/25.09.2/rslib/src/stats/card.rs) also constructs historical memory states. Its [SQL helper](https://github.com/ankitects/anki/blob/25.09.2/rslib/src/storage/sqlite.rs) has version-specific time/fallback semantics. Neither should be substituted across custom Anki versions without a verified native compatibility path.

## Verification and limits

- **131 passed, 1 skipped** on stock Anki 25.9.2 / Python 3.12.
- **131 passed, 1 skipped** on installed Anki 26.09b3+fsrs7 / Python 3.13.
- The skip is the existing blocking transport’s unsupported nonblocking-upload assertion.
- New tests cover selective field projection, HTML/Unicode/empty fields, different note-type field orders, unknown/duplicate field names, duplicate/string/missing card IDs, no stale data across requests, SQL batch bounds, dispatch registration, and preserved FSRS calls.
- Initial installed-build test invocation from stdin could not spawn Qt test subprocesses; rerunning as `python -m pytest` with the installed package path passed.
- No live collection database was opened. A supported deck export supplied disposable copies. The live reader and installed add-on were not modified.
- Browser measurements use a local Vite proxy, automatic token-panel opening, default thresholds, and starting book position. Cold means cleared reader caches, not cold OS/backend/Yomitan caches.
- The paint metric is a double-animation-frame opportunity after analysis and hashing, not compositor timing.
- The result is a benchmark prototype. The older supported-Anki version matrix and broader GUI integration suite still need validation before production promotion.

Raw profiles: [current](ebook-reader-current-profile.txt), [selected fields](ebook-reader-bulk_notes-profile.txt). Full measurements and result hashes: [JSON](ebook-reader-profile.json). Reproduction: [README](../README.md).
