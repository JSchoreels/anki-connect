# Performance and regression checks

Transport and note-prefetch prototypes live in `performance.py` and
`profile_reader.py`. Current-metric routing and lazy statistics selection are
now in the production add-on; `test_card_metrics.py` exercises those methods
directly, including legacy fallback and the `prop:d` difficulty correction.
Transport prototypes use the pinned pre-rewrite Git revision `6f0d1be` as their
historical baseline. Metric/detail tests load the current `plugin/extended.py`
operations into that isolated facade; real rewritten dispatch and notifications
are covered by `tests_api/test_extended.py`. A Git checkout containing that
revision is required for the historical benchmarks.
Historical reports describe the code at their recorded source hashes.

Run the unit tests:

```sh
rtk proxy python3 -m pytest -q benchmarks/test_performance.py
```

Run all regression checks, including socket unit tests and Qt/TCP smoke tests:

```sh
rtk proxy python3 -m pytest -q benchmarks
```

The suite uses the existing pytest, Anki, jsonschema, and PyQt6 dependencies. It
does not require `pytest_anki`, launch Anki, or open a user profile.
The note-prefetch contract tests create disposable synthetic collections through
Anki's supported APIs. The Qt smoke tests start local servers on ephemeral ports.
The original transport is excluded from the new nonblocking-upload assertion
because it is known to block until the other upload completes.
The native metric integration case is skipped on Anki builds without the new
`Collection.card_memory_metrics` API. To exercise it with the local rebuilt core:

```sh
rtk proxy env PYTHONPATH=/Users/jschoreels/workspace/anki/pylib:/Users/jschoreels/workspace/anki/out/pylib \
  /Users/jschoreels/workspace/anki/out/pyenv/bin/python -m pytest -q benchmarks
```

Assertions pin response values and errors as well as input ordering, duplicate
and missing IDs, API versions, authentication, CORS, fragmented requests, and
partial writes. Operation-count assertions protect the intended performance
improvements without flaky elapsed-time thresholds.

For timing measurements (separate from regression checks):

```sh
rtk proxy python3 benchmarks/performance.py
```

The benchmark creates and removes a temporary synthetic Anki collection. Results
are saved under `results/`. Passing these checks does not replace the existing
Anki GUI integration suite or testing across supported Anki/Qt versions before
promoting prototypes to production.

## Real ebook-reader benchmark

`ebook_reader.py` starts two read-only Qt/Anki backends and a loopback proxy of an
already-running ebook-reader Vite server. Each backend opens its own copy of an
exported deck snapshot. The proxy supplies a separate origin, so its browser
settings, imported book, reading position, and caches are separate from the user's
reader. It instruments the actual reader's methods and opens its full token panel
on navigation. The ebook-reader checkout and installed Anki add-on are unchanged.

Inputs: the reader running on port 5173, Yomitan on port 19633, an EPUB, and a
snapshot extracted from an AnkiConnect `exportPackage` export with scheduling.
Use a supported Anki export; never pass a live profile database. The recorded run
exported `Japan::1. Vocabulary`, extracted `collection.anki21` from the legacy APKG
as `snapshot.anki2`, and used the existing Anki 25.9.2 Python package to open copies.
The export's media is unnecessary for this benchmark. Private export/snapshot
files belong in a temporary directory, not `results/` or git.

Example, after preparing those local inputs:

```sh
rtk proxy python3 benchmarks/ebook_reader.py \
  --snapshot /private/tmp/ebook-benchmark/snapshot.anki2 \
  --epub '/Users/jschoreels/SynologyDrive/Ebooks/Japan/Light Novel/夢が覚めるまで.epub' \
  --output /private/tmp/ebook-benchmark/run
```

Open the printed origin followed by
`/__bench/setup?variant=current&cache=cold&run=1`. Once ready, follow **Load book
and token panel**. Completion appends a row to `browser-runs.jsonl`. Repeat setup
with `cache=warm`, then repeat both with `variant=prototype`. Alternate which
variant runs first across five pairs. Exclude separately labelled pilot runs.
Navigate through setup before every run; a direct reader reload does not clear
caches or change the variant. `cold` means empty reader vocabulary, lemma,
tokenization, and frequency caches; app assets, the imported book, backend OS
caches, and the Yomitan service can remain warm.

Settings are fixed to deck `Japan::1. Vocabulary`, fields `Front` / `Front_Kana`,
combined coloring, and default reader thresholds. Only `version`, `findCards`, and
`cardsDetails` are allowed by the disposable backends. The prototype combines
event-driven transport, incremental parsing, cached validation/dispatch, and lazy
card stats; the reader requests FSRS stats, so skipping those stats does not apply.
Modification-time batching is not exercised by this workflow.

Recorded timings include reader DOM availability, complete token analysis, a
subsequent double-animation-frame paint opportunity, cache warming, and individual
API request/JSON decoding time. Full analysis output is hashed without saving
vocabulary text. Compare hashes within the same cache condition: the reader itself
has a small cold/warm status difference that occurs identically with both backends.
FSRS retrievability also changes slightly with wall-clock time, so card-level parity
checks compare stable fields exactly and report the floating-point drift separately.

The saved [real-book results](results/ebook-reader.md) include all measured runs,
ranges, limits, and the conclusion. These are isolated development-server timings,
not measurements of the installed Anki GUI or a production reader build. Ctrl+C
stops the local benchmark servers; remove the disposable browser origin's storage
and temporary export/snapshot files when finished.

## Profiling the installed Anki build

`profile_reader.py` profiles the reader's five `cardsDetails` batches and compares
the original method with selected-note-field prefetching. It writes cProfile
artifacts, seven alternating timing pairs, and payload parity checks under the
chosen temporary output directory. Unlike fetching entire notes, this prototype
selects only the requested fields with Anki's `field_at_index()` SQL function,
groups reads by note type, bounds SQL parameter counts, and retains the original
card loading, FSRS calls, validation, ordering, and response construction.
All caches are request scoped. It is a benchmark prototype, not installed code.

Use an interpreter compatible with the installed app's Python ABI. For the
installed Python 3.13 / Anki 26.09b3+fsrs7 build used here:

```sh
rtk proxy /Users/jschoreels/workspace/anki/out/pyenv/bin/python benchmarks/profile_reader.py \
  --anki-packages /Applications/Anki.app/Contents/Resources/app_packages \
  --snapshot /private/tmp/ebook-benchmark/snapshot.anki2 \
  --output /private/tmp/ebook-benchmark/profile
```

For the full reader, use that interpreter and the same `--anki-packages` option
with `ebook_reader.py`, plus `--prototype bulk-notes`. The comparison arm combines
selected-field prefetching with the earlier transport/dispatch/validation work.
Stock Anki 25.9.2 and the installed FSRS-7 build produce different metric values;
compare each prototype against a baseline using the **same Anki build**.
See [the installed-build profile and results](results/ebook-reader-profile.md).

## Native batch metrics comparison

Run `ebook_reader.py --prototype native-metrics` with a rebuilt Anki core that
provides `Collection.card_memory_metrics`. Both isolated servers use that same
core and the original 25 ms timer transport. `current` forces the full-statistics
fallback; `prototype` uses production metric routing. This isolates the batch
metrics change from other performance prototypes. Optional
`--live-url http://127.0.0.1:8765` adds a read-only live control, forwarded through
the benchmark proxy. It is labeled separately because its core, collection and
GUI environment can differ from the paired comparison.

Open `/__bench/setup?suite=native` to run five alternating pairs, with each cold
load followed by a warm load, then three cold/warm live-control pairs if enabled.
The final page displays `Benchmark complete`. Individual setup URLs still work.
Use run names starting with `profile` to collect diagnostic cache-method counts;
keep those instrumented runs separate from timing samples. Method duration sums
can overlap and must not be interpreted as additive wall-clock costs.

Add `--native-metrics` to `profile_reader.py` to compare forced full statistics,
production native metrics, and native metrics plus selected-note-field prefetching.
It checks stable payload equality and reports retrievability time drift.
See [the native metrics ebook benchmark](results/ebook-reader-native.md).

Use `--prototype native-details` to compare the earlier native-metrics path with
production compact card details and selected note fields. The baseline disables
only the combined details getter; both arms retain batch FSRS metrics and the same
transport. `profile_reader.py --native-metrics` includes this fourth variant when
the core supports it. `test_card_details.py` checks response parity, legacy errors,
fresh reads, and elimination of per-card loads on the optimized path.
See [the compact card-details benchmark](results/ebook-reader-card-details.md)
for the combined getter's backend profile and twenty full reader loads.
The [token panel breakdown](results/ebook-reader-token-panel.md) separates cache
refresh, status resolution, and the delay after analysis in those same loads.

## Reader changes against a running core

Use `--live-only --live-url http://127.0.0.1:8765` to test the running Anki GUI
through the read-only proxy without exporting a collection or starting synthetic
backends. Omit `--snapshot`; supply the usual `--epub` and `--output`. Verify the
running core separately, since AnkiConnect's `version` is the API version.
Open `/__bench/setup?suite=live&label=baseline` for five cold/warm baseline pairs
before editing reader code.

For an alternating frontend comparison, serve a frozen original reader on a
second Vite port, including its assets and dependencies, and pass
`--baseline-vite-port 5174`. Open `/__bench/setup?suite=frontend` for five
alternating original/optimized pairs, each with cold and warm loads. Both use
the same live Anki proxy. The setup cookie selects which Vite server supplies
modules; each run resets panel filters and sort order on the disposable origin.
No code, tests, or builds should run concurrently with measured loads. Exclude
pilot/diagnostic runs, compare full analysis hashes, and record mounted row counts
separately from the number of analyzed words. Collection edits and time-dependent
status changes can affect a live comparison and must be reported if observed.
See [the reader optimization results](results/ebook-reader-frontend.md) for the
new-core baseline, alternating comparison, correctness checks, and limitations.
