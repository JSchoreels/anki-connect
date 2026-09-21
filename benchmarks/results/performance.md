# AnkiConnect performance prototypes

Measured 2026-09-20T13:55:05.940249+00:00

Environment: macOS-26.7-arm64-arm-64bit; Python 3.12.6; anki 25.9.2; jsonschema 4.24.0; PyQt6 6.9.1.

## Method

Current methods are loaded from repository source without add-on startup. Candidate code exists only in the benchmark. Runtime files and the installed add-on are unchanged. Collection benchmarks use a fresh temporary collection with 10,000 synthetic Basic notes and 10,000 new cards, populated only through Anki APIs. Metadata queries are read-only and run through Anki's database bridge. The temporary collection is closed and removed afterward; no user profile is opened.

Component results are medians of seven timed batches (21 for the FSRS control), alternating current/prototype order after an equality check warms both paths. Input IDs are shuffled deterministically. Raw samples and source hashes are recorded in performance.json. These are warm-cache measurements.

HTTP benchmarks use real TCP loopback, one new connection per request, a separate process with QCoreApplication, the original 25 ms QTimer and backlog 5. Event/socket prototypes keep the same request/response handlers and connection-closing behavior. event_socket changes only transport; event_all additionally reuses the schema validator and caches action resolution. Every handler runs serially on that process's Qt thread. This is not the full Anki GUI.

## Component results

| Operation | Items / payload bytes | Current ms | Prototype ms | Speedup |
|---|---:|---:|---:|---:|
| schema validation | — | 0.5262 | 0.0080 | 65.83× |
| action dispatch (version) | — | 0.0707 | 0.0006 | 123.57× |
| multi dispatch | 1000 | 70.1481 | 0.5627 | 124.66× |
| incremental request parsing | 1024 | 0.0031 | 0.0022 | 1.39× |
| incremental request parsing | 65536 | 0.1637 | 0.0128 | 12.75× |
| incremental request parsing | 262144 | 1.5507 | 0.0345 | 44.95× |
| incremental request parsing | 1048576 | 24.0970 | 0.1518 | 158.71× |
| incremental request parsing | 4194304 | 1433.5068 | 1.6467 | 870.53× |
| cardsModTime bulk read | 1 | 0.0057 | 0.0078 | 0.74× |
| notesModTime bulk read | 1 | 0.0074 | 0.0072 | 1.04× |
| cardsDetails basic fields (skip stats) | 1 | 0.0154 | 0.0065 | 2.38× |
| cardsModTime bulk read | 100 | 0.4127 | 0.1310 | 3.15× |
| notesModTime bulk read | 100 | 0.5473 | 0.1378 | 3.97× |
| cardsDetails basic fields (skip stats) | 100 | 1.3547 | 0.4715 | 2.87× |
| cardsModTime bulk read | 1000 | 4.3375 | 1.6166 | 2.68× |
| notesModTime bulk read | 1000 | 5.5581 | 1.4744 | 3.77× |
| cardsDetails basic fields (skip stats) | 1000 | 13.6250 | 4.5572 | 2.99× |
| cardsModTime bulk read | 10000 | 42.5490 | 14.4707 | 2.94× |
| notesModTime bulk read | 10000 | 56.2302 | 15.1297 | 3.72× |
| cardsDetails basic fields (skip stats) | 10000 | 138.0954 | 45.8535 | 3.01× |
| cardsDetails FSRS fields (control) | 1000 | 13.4385 | 13.3877 | 1.00× |

## HTTP results

| Workload | Variant | Samples | p50 ms | p95 ms |
|---|---|---:|---:|---:|
| sequential version | current | 100 | 25.058 | 37.459 |
| 4 concurrent version requests | current | 60 | 64.701 | 101.997 |
| 1 MiB HTTP request | current | 15 | 49.105 | 51.948 |
| 1000-action HTTP multi | current | 10 | 104.204 | 111.918 |
| version while another upload is stalled | current | 5 | 208.744 | 213.266 |
| sequential version | event_socket | 100 | 0.961 | 1.164 |
| 4 concurrent version requests | event_socket | 60 | 2.285 | 3.403 |
| 1 MiB HTTP request | event_socket | 15 | 4.696 | 5.008 |
| 1000-action HTTP multi | event_socket | 10 | 72.164 | 73.030 |
| version while another upload is stalled | event_socket | 5 | 4.284 | 5.378 |
| sequential version | event_all | 100 | 0.198 | 0.298 |
| 4 concurrent version requests | event_all | 60 | 0.357 | 0.547 |
| 1 MiB HTTP request | event_all | 15 | 3.932 | 4.301 |
| 1000-action HTTP multi | event_all | 10 | 1.565 | 2.438 |
| version while another upload is stalled | event_all | 5 | 0.910 | 1.541 |

## Compatibility checks

92 differential checks passed. These compare values or exact exception strings, including empty inputs, missing IDs, duplicates, input order across the 999-ID chunk boundary, unusual ID types, fields, API versions and aliases, nested multi responses, authentication failures, schema errors, fragmented parsing, and selected exact HTTP responses including CORS/OPTIONS.

## Limits

These prototypes are not a production compatibility certification. The full repository pytest suite was not run (pytest_anki is absent). Tests cover this installed Anki and Qt version, not all supported versions/platforms. The collection contains new cards without review history, so results do not predict all FSRS or large-history workloads. No cold-disk, actual GUI load, media download, write-batching, or installed add-on benchmark is included. Dispatch numbers use repeated version actions and warmed lookup tables. The 1 MiB HTTP payload is inert JSON padding, isolating upload/parse cost rather than media operations. A stalled-upload case completes the other request's final byte after a controlled 200 ms delay. HTTP variants run consecutively and p95 is descriptive, not a confidence bound.

Rerun from the repository root: `rtk proxy python3 benchmarks/performance.py`.
