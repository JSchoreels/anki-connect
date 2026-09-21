# Current FSRS metrics benchmark

Release-mode Anki 26.09b3+fsrs7 core with the new native batch API; production AnkiConnect routing.

## Method

1,000 synthetic Basic cards, 20 Grade Now reviews per card, FSRS-7 (0 parameters) selected through a deck overlay. Medians of 7 alternating full-statistics/native-batch pairs after warm-up.

The temporary collection was created with supported Anki APIs and removed afterward. The current last-review timestamp was set to two days before measurement in both paths to avoid timing noise immediately after grading. This is synthetic scheduling data, not a copied user collection.

Both paths use the same production response-building code. The baseline disables native batch selection to exercise the full-statistics fallback. Note loading and transport optimizations from earlier prototypes are excluded. Comparisons assert identical S/D and other non-time-dependent fields; retrievability allows an absolute difference below 0.0001 for elapsed wall-clock time.

## Results

| Request | Full statistics | Native batch | Reduction |
| --- | ---: | ---: | ---: |
| current S/D | 25.83 ms | 6.92 ms | 73.2% |
| current R/S/D | 26.22 ms | 7.41 ms | 71.8% |
| reader fields and notes | 33.20 ms | 13.94 ms | 58.0% |

These are warm-cache component measurements on 1,000 small synthetic notes. They do not predict full ebook loading time, cold cache behavior, or savings for the real 11,736-card reader request. Raw samples and the add-on source hash are in [card-metrics.json](card-metrics.json).

## Compatibility and verification

- `prop:d` now means difficulty in `cardsInfo`, `cardsDetails`, and `findCards`. The incorrect desired-retention mapping originated in fork commit `446414b16aa3d4ce12d6d4ac1a98d9731e22841a` on 9 May 2026; its correction is the intentional response-value change.
- Builds without the native API retain full-statistics fallback. Reviewed cards without stored state retain the original initialization path. Full Card Info statistics remain unchanged.
- Rust unit suite: 983 passed, 11 ignored (opt-in benchmarks). Native statistics subset: 17 passed.
- Anki Python stats/card checks: 9 passed. Add-on independent checks against the rebuilt core: 155 passed, 1 skipped (the existing blocking transport control).
- Stock Anki 25.9.2: 154 passed, 2 skipped (native API unavailable and blocking transport control). Installed FSRS-7 build: 23 current-metric routing checks passed, 1 native-API case skipped.
- The GUI integration suite requires `pytest_anki`, which is unavailable. Equivalent routing is checked by the independent harness and real synthetic Collection tests; no live user profile is opened.

## Reproduce

Build the optimized core using `rtk proxy env RELEASE=1 ./ninja pylib` in the Anki checkout, then run:

```sh
rtk proxy env PYTHONPATH=/Users/jschoreels/workspace/anki/pylib:/Users/jschoreels/workspace/anki/out/pylib \
  /Users/jschoreels/workspace/anki/out/pyenv/bin/python benchmarks/card_metrics.py
```
