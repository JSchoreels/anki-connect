# Full ebook load and native FSRS metrics profile

Measured on 20 September 2026 with **夢が覚めるまで**: 45,634 characters,
24,406 token occurrences, 4,289 unique tokens, and 11,736 vocabulary cards.

**The restarted installed Anki still has core `6a504b35`, without
`Collection.card_memory_metrics`.** The add-on uses its compatibility fallback
there. The controlled comparison below uses the rebuilt release core `bb9ff481`
on exported collection copies. Updating the add-on and restarting Anki does not
install the rebuilt Anki core.

## Full reader results

Five alternating pairs per cache condition. Both arms use the same core,
collection snapshot, reader, original 25 ms timer transport, and response fields.
The baseline forces full card statistics; the native arm uses the production
batch-metrics routing. Values are medians; ranges cover all five observations.

| Measurement | Full-statistics baseline | Native batch metrics | Reduction |
| --- | ---: | ---: | ---: |
| Cold: complete word panel | 11.154 s (10.006–12.545) | 8.791 s (8.628–10.987) | **21.2%** |
| Warm: complete word panel | 5.471 s (5.380–5.829) | 3.876 s (3.689–4.405) | **29.2%** |
| Cold: Anki HTTP + response decoding | 2.529 s | 0.875 s | **65.4%** |
| Warm: Anki HTTP + response decoding | 2.427 s | 0.856 s | **64.7%** |

Every paired full-load measurement improved. Individual cold reductions ranged
from 0.416 to 3.698 s; warm reductions ranged from 1.122 to 2.109 s. Cold timings
are noticeably noisier, so the median percentage is a local measurement, not a
precise prediction for every launch.

The reader DOM appeared after a median 0.343 s cold / 0.335 s warm with the native
arm. Importing the EPUB into the disposable origin took 78 ms in the excluded
pilot; the repeated timings start at navigation to the already-imported book.

The actual installed Anki was also measured through a loopback read-only proxy,
with three cold/warm pairs:

| Installed Anki control | Cold | Warm |
| --- | ---: | ---: |
| Complete word panel | 11.814 s (9.318–14.087) | 4.635 s (4.470–4.943) |
| Anki HTTP + response decoding | 1.611 s | 1.489 s |

This control has a different core, live collection, GUI environment, and HTTP
forwarding path. Its output hashes differ from the exported-copy arms, although
token/card counts match and its own hashes are stable. Do not use it as the
baseline for attributing the batch API's speedup; the exact cause of that output
difference was not isolated.

## Where the remaining time goes

### AnkiConnect / Anki core

Seven alternating unprofiled measurements of the reader's five `cardsDetails`
batches, excluding HTTP and JSON encoding:

| Backend path | Median | Range |
| --- | ---: | ---: |
| Full statistics | 1,917.9 ms | 1,913.5–1,950.8 ms |
| Native metrics | 487.4 ms | 483.5–511.9 ms |
| Native metrics + selected-note-field prototype | 268.5 ms | 261.4–279.0 ms |

Native metrics reduce this work by **74.6%**. The selected-field prototype saves
another **218.9 ms / 44.9%** relative to native metrics. That additional result is
a backend measurement; a full-reader improvement for this combination has not
been measured here.

cProfile explains the change (profiled timings include instrumentation overhead):

| Operation | Full-statistics profile | Native profile |
| --- | ---: | ---: |
| Total handler work | 2,241 ms | 700 ms |
| `card_stats_data` | 11,736 calls / 1,512 ms | **0 calls** |
| `card_memory_metrics` | 0 calls | **5 calls / 20 ms** |
| `get_note` | 11,736 calls / 493 ms | 11,736 calls / **455 ms** |
| `getCard` | 11,736 calls / 134 ms | 11,736 calls / 127 ms |

Note loading is now **65%** of the native handler profile. `cardsDetails` loads
complete notes and builds their Python field maps even though the reader only
asks for `Front` and `Front_Kana`. The experimental prefetch selects these fields,
groups by note type, and preserves the existing response construction. Fetching
all note fields in bulk was already found to be counterproductive in the earlier
profile; projection is the useful part.

The seven-request native HTTP run spends a median 549 ms inside the logged
handlers, versus roughly 0.86–0.88 s measured at the browser. The remainder includes
timer/transport delays, CORS, serialization, and browser decoding; it cannot all
be assigned to one cause from these measurements. Encoding the entire 2.66 MB
response alone takes a median 17.6 ms, so JSON encoding is a smaller target.

### Reader / dictionary / rendering

Native-arm median stages:

| Stage | Cold | Warm |
| --- | ---: | ---: |
| Anki fetch and cache rebuild (`warmCache`) | 1.773 s | 1.598 s |
| Initial document tokenization | 1.729 s | Cached |
| Word-panel analysis | 5.717 s | 1.048 s |
| Analysis completion → paint opportunity | 0.826 s | 0.675 s |
| Yomitan requests | **840** | **0** |

Tokenization overlaps the cache rebuild, so these durations must not all be added.
The `warmCache` stage includes the Anki HTTP times from the first table.

Separate diagnostic browser runs, excluded from the timing samples, found:

- Cold: 9,180 `getWordData` calls, 3,184 `getLemmas` calls, 2,455 individual
  `setWordData` calls, plus 49 bulk word-cache writes.
- Warm: **8,143 `getWordData` calls**, 2,211 `getLemmas` calls, 1,483 individual
  `setWordData` calls, plus the same 49 bulk writes, despite no dictionary HTTP.
- Cache-method durations overlap because work is concurrent. Their summed
  durations are not elapsed loading time.
- CPU sampling shows substantial IndexedDB wrapper/transaction work and garbage
  collection. Much browser-native work is attributed only to `(program)`; this
  profile does not resolve layout/paint internals separately.
- Near panel completion, the cold diagnostic records consecutive 163 ms and
  612 ms main-thread long tasks; warm records 180 ms and 593 ms. The panel mounts
  all 4,289 rows. Virtualization is a credible next experiment, but its benefit
  has not been measured by this run.

The code explains why the warm path still refreshes the deck:
`loadCacheFromIndexedDB()` reports `needsRefresh: false`, but
`ensureTokenPanelWordDataPrimed()` calls `runWarmCacheRefreshFlow()` on each new
reader instance. Full word resolution then performs many individual IndexedDB
operations even though batch cache methods already exist.

## Recommended next work

1. **Make the rebuilt Anki core available in the installed app.** This is required
   to receive the measured native-metrics benefit. Keep capability detection and
   the legacy fallback for other API clients / Anki versions.
2. **Project and batch requested note fields in AnkiConnect.** This is the next
   measured backend win: 487 → 269 ms. Keep request-scoped data, input order,
   duplicate/missing-ID behavior, note field order, and error semantics. Promote
   only after the existing projection contract tests and real-book parity checks
   pass against the final implementation.
3. **Batch reader cache access and avoid redundant alias writes.** The existing
   batch APIs and thousands of per-word operations make this a strong frontend
   candidate. Preserve lookup priority, lemma enrichment, and cache expiry.
4. **Virtualize the word panel.** Keep all 4,289 entries in the analysis model,
   but mount only the visible rows; preserve filters, keyboard navigation,
   selection, sentence expansion, and scroll-to-token behavior.
5. **Investigate the forced full-deck refresh on warm loads.** A freshness-aware
   cache/delta mechanism could save part of the 1.6 s refresh stage. Simply
   trusting a TTL risks stale status after grading or as retrievability decays;
   collection changes and time-dependent thresholds must remain correct.

Transport/dispatch work remains a secondary candidate, especially for many small
requests. Increasing one request to encompass the whole deck would trade fewer
round trips for a longer uninterrupted Anki GUI task; it is not automatically a
better default. Further FSRS math tuning has little headroom here: the native
batch calls already total about 20 ms.

## Correctness, reproducibility, and limits

- 26 measured full-reader runs: 20 controlled A/B runs plus 6 live controls.
- Controlled A/B analysis hashes match exactly within each cache condition.
  Cold and warm hashes differ, as in earlier runs of this reader. The valid
  diagnostic runs also reproduce their matching A/B hashes and request counts.
- All runs complete with 24,406 occurrences, 4,289 unique tokens, 11,736 cards,
  and 12,158 extracted vocabulary forms. Every measured run has seven Anki
  requests (version, findCards, five cardsDetails batches) and no recorded API or
  analysis errors. Each has one expected superseded-analysis cancellation.
- Backend response hashes match exactly after excluding only time-varying
  retrievability. Maximum retrievability drift is 0.0000367.
- **155 regression tests passed, 1 intentionally skipped** (the original
  blocking transport's nonblocking-upload assertion).
- The reader uses Vite's development build in an isolated in-app browser origin.
  Browser storage is separate from the user's normal reader. Cold clears reader
  analysis/vocabulary caches; it does not flush OS caches or restart Yomitan.
- The full-load endpoint is analysis completion followed by two animation frames,
  a paint opportunity rather than a compositor presentation timestamp.
- CPU sampling and cache counters ran separately. The CPU recordings include
  idle time after completion; do not interpret their total recording lengths as
  loading time. Two preliminary diagnostic runs were discarded after correcting
  a timing hook that had changed a synchronous cache method's return type.
- Only supported deck export and read-only live AnkiConnect actions were used.
  Backend experiments operated on exported copies. No production optimization
  was added during this profiling pass.

Artifacts: [browser runs, summaries, source hashes](ebook-reader-native.json),
[backend timings and profiles](ebook-reader-native-backend.json),
[legacy cProfile](ebook-reader-native-legacy-profile.txt),
[native cProfile](ebook-reader-native-native-profile.txt),
[selected-field cProfile](ebook-reader-native-native_bulk_notes-profile.txt),
[cold browser CPU profile](ebook-reader-native-cold.cpuprofile),
[warm browser CPU profile](ebook-reader-native-warm.cpuprofile).
