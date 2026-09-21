# Token panel loading breakdown

Checked on 2026-09-21 using the twenty completed real-book loads in
[the compact getter benchmark](ebook-reader-card-details.json), the earlier
[browser diagnostics](ebook-reader-native.json), and the reader's current source.
These are a breakdown of existing measurements, not a new browser run.

The full-load measurement already includes opening the token panel and completing
the entire book's analysis: **24,406 occurrences, 4,289 unique word rows**.
It starts at navigation, not at a later click on the panel button.

## Warm loading

Medians across five loads per variant, in milliseconds:

| Stage | Previous native metrics | Combined getter |
|---|---:|---:|
| Initial IndexedDB cache load | 17 | 17 |
| Anki cache refresh, including fetching and storing words | 1,470 | 1,149 |
| Document token analysis / status resolution | 884 | 880 |
| Analysis completion to panel paint opportunity | 703 | 719 |
| Navigation to complete panel paint opportunity | 3,511 | 3,217 |

Stage medians do not add up to the total: some work overlaps, there are gaps
between stages, and each median may come from a different run. The final delay
is calculated per run before taking its median. It includes Svelte updates,
browser work, benchmark hashing, and animation-frame scheduling; it is not an
isolated measurement of rendering or a compositor presentation timestamp.

The backend improvement reaches the panel through the cache-refresh stage.
Status resolution and the final display delay are essentially unchanged. With
the combined getter, the final delay ranges from **630 to 777 ms** on warm loads.
Cold loads have a **637 ms** median final delay (626–725 ms), and still take
**7.81 seconds** overall. Cold document analysis takes **5.22 seconds**, versus
**0.88 seconds** warm; cold runs make 840 Yomitan requests, warm runs make none.
Across all ten cold runs, those requests comprise **21 tokenize calls and 819
termEntries calls**. All 819 dictionary calls start after document analysis begins.
The remaining cold bottleneck should therefore be investigated as dictionary
enrichment and word resolution, rather than attributed to tokenization alone.

## Where to improve the panel

### Render only visible rows

`book-reader-token-panel.svelte` renders an unbounded keyed `each sortedEntries`
inside its scrolling list. All 4,289 entries mount even though only a small
fraction are visible. Each row has nested elements, badges, and event handlers.
The earlier diagnostic observed consecutive main-thread tasks of **180 ms and
593 ms** near warm panel completion (cold: 163 ms and 612 ms). CPU sampling did
not separate browser layout/paint from other native work, so these tasks cannot
be attributed entirely to row rendering from the existing trace.

Virtualization is the strongest panel-specific experiment: retain the complete
analysis and filter counts, but render visible rows plus a small buffer. Preserve
the current sort/filter semantics, variable height sentence expansion, selection,
and scroll-to-token behavior. Its actual improvement still needs a paired test.

### Batch the status-resolution cache operations

`analyzeDocumentText()` resolves groups of 250 tokens, but the lookup path still
performs many individual IndexedDB reads and writes. The earlier warm diagnostic
recorded **8,143 getWordData**, **2,211 getLemmas**, and **1,483 setWordData** calls
across the reader load, despite making no Yomitan HTTP requests. These are whole
load counts, not counts isolated to the panel. Their overlapping durations must
not be summed as wall time.

The cache service already exposes batch word/lemma reads and word writes. Using
those while preserving alias priority, lemma resolution, freshness, and expiry
is a candidate for reducing the remaining 0.88-second analysis stage.

The resolver currently waits for each fixed group of 25 token lookups to finish
before starting the next group. A bounded queue with the same concurrency could
avoid waiting for one slow token while other slots are idle. Sharing in-flight
dictionary lookups for identical tokens and avoiding repeated alias writes are
additional candidates. The trace does not establish how many network requests
are duplicates, so no reduction in the 819 requests is assumed. Preserve lookup
priority and resulting statuses, and benchmark the scheduling change separately.

### Treat warm refresh avoidance separately

On warm runs, the initial cache reports `needsRefresh: false`, yet the panel's
`ensureTokenPanelWordDataPrimed()` invokes the full refresh flow on a new reader
instance. That explains why a cached book still fetches the deck. Avoiding it
could help, but requires explicit freshness handling for reviews and FSRS
retrievability changes; simply skipping the refresh can show stale statuses.

## Opening again within the same reader instance

Source inspection shows that closing the panel destroys its UI component but
retains `tokenPanelResult`. Reopening with the same analysis key returns early
from analysis and reuses that result. The list component still mounts again, so
virtualization should also help this interaction. Click-to-open/reopen latency
has **not** been measured separately by the navigation benchmark.

No ebook-reader or Anki production code was changed for this inspection.
