# Anki-Connect architecture

Anki-Connect runs inside Anki Desktop 26.08 or later. It serves JSON-RPC 2.0 at
`POST /api/v1` and the legacy action protocol at `/`, on `127.0.0.1:8765` by
default. The canonical contract is [API_V1.md](API_V1.md) and
[api-v1.schema.json](api-v1.schema.json). The legacy API still reports version 6.

| Module | Responsibility |
| --- | --- |
| `plugin/__init__.py` | Version gate, startup, settings, permissions, exact legacy signatures, and asynchronous legacy dispatch. |
| `plugin/web.py` | Nonblocking sockets, path routing, strict JSON, HTTP limits, CORS, authentication, and deferred replies. |
| `plugin/protocol.py` | Schema validation, typed errors, callback completion, operation records and cancellation. |
| `plugin/runtime.py` | Qt admission, collection/session pins, serialized native work, completion notifications, ordered commands and tokens/cursors. |
| `plugin/services.py` | Native collection, scheduler, note-type, preset, media, package, statistics and undo services. |
| `plugin/gui.py` | Browser, editor, reviewer, import, profile and sync adapters on Qt's main thread. |
| `plugin/extended.py` | Extended legacy card selection, batched metrics/details, immediate grading, and ordered new-card repositioning; called by the legacy adapter inside serialized collection tasks. |
| `plugin/legacy.py` | Argument translation, result projection, compatibility failures and attachment orchestration. |
| `plugin/media.py` | Request-owned media backups, rollback and interrupted-request recovery. |
| `plugin/contract.py` | Generated bundled schema and legacy target map. |
| `plugin/edit.py` | Custom note editor and session-local history. |

## Execution and completion

Add-on startup registers the custom editor and unload hooks, opens optional
metadata logging, and starts the listener. Importing as `plugin` in tests skips
automatic startup. Qt socket notifiers on Qt's main thread accept connections
and read each socket until it would block; request headers are parsed once and
the body accumulates in one buffer. The reply is written as soon as its
completion resolves, and a write notifier resumes only partial writes. A
retained low-frequency timer (`apiPollInterval`, default 1000 ms) enforces the
10-second idle timeout and also advances sockets as a fallback; it does not
gate request latency. Each connection closes after its reply drains, and the
exit check runs when the last connection closes.

Legacy envelope validation and operation-result validation reuse cached
validators. Parameter preparation copies only the top-level object and schema
defaults; services treat nested request values as read-only.

Repeated `Connection` headers are combined as a list for compatibility with older
HTTP proxies. Other duplicate headers remain rejected, including `Content-Length`,
`Origin`, and `Authorization`; transfer-encoded requests remain unsupported.
Every reply advertises `Connection: close`. Transport errors (400, 413, and 500)
also include `Content-Type: application/json` with their existing empty bodies,
so proxies that require a content type can forward the error without crashing.

After the listener starts, Anki-Connect uses Anki's macOS helper to suppress App
Nap for the process lifetime, including profile switches. The native helper
retains the activity token and allows idle system sleep. Other platforms skip
suppression. A helper failure is reported to the console without stopping the
listener.

Admission captures the active collection and opaque session on Qt's main thread.
GUI operations stay there. Collection tasks use Anki's task manager with its
serialized collection access and background-operation lifecycle counter. File
reads/downloads use background tasks without collection access. Completion callbacks
publish native changes through `on_op_finished` before resolving API replies.
Failed operations also publish changes from successful prefixes.

```mermaid
flowchart LR
    Client -->|HTTP| Transport[web.py]
    Transport --> Dispatcher[protocol.py]
    Dispatcher --> Admission[runtime.py on Qt]
    Admission --> Native[services.py serialized collection work]
    Admission --> GUI[gui.py on Qt]
    Native --> Completion[Qt completion and native notifications]
    GUI --> Completion
    Completion -->|deferred reply or operation result| Transport
```

Completed methods reply after work and native notifications. Interactive/long
methods return operation handles; poll `operations.get` for final results.
`api.execute` waits for each final outcome and continues after item errors.
Cancellation between commands preserves completed outcomes in error details.
Running work is cancellable only where a native cancellation callback is wired.
Disconnecting a socket does not cancel accepted work.

Legacy standalone GUI actions preserve accepted-return timing where documented.
Legacy `multi` waits for each final outcome, including nested batches. It preflights
the full request tree for malformed containers, nesting depth and final exit
ordering. Neither command form creates a transaction across items. Exit begins
only after outstanding socket replies drain.
Legacy batches preserve unbounded action counts for whole-vault Obsidian syncs;
the HTTP request size limit still applies. The v1 batch count limit is separate.
Legacy envelopes and arguments are normalized before admission guards: harmless
surplus parameters, null optional defaults, decimal strings, boolean spellings,
and single-item lists are handled at the compatibility boundary. Note fields,
tags, and attachment targets share legacy normalization across creation and writes.
V1 schemas remain strict; authentication and batch preflight rules are unchanged.

## Sessions and editor coordination

Sessions change on collection identity changes, profile unload, temporary collection
close/full sync and adapter-controlled reopen. Tokens, cursors, editor registrations
and custom editor history are invalidated. Collection calls pin their admission
session and recheck it before execution; lifecycle transitions reject competing work.
Queued GUI calls recheck active collection work when they start.

Note-field and note-scoped tag updates synchronize one affected open editor.
The adapter pauses input, waits for webview readiness and pending field/tag saves,
then admits the serialized collection write. It reloads the editor before restoring
input and completing the reply. Supplied API properties take precedence; pending
edits in other fields and omitted tags are preserved. Classic/custom editors and
the Svelte browser editor use the same callback-based lifecycle without nested
event loops. Concurrent updates to that editor return `busy`; a failed save or a
changed editor/session does not admit the write.
If the native write finishes but the webview cannot refresh, `ioError` includes
`writeCompleted` and the editing widget stays disabled until the window is reopened,
so a later typing save cannot overwrite the API update with stale content.

Other writes, all-note tag replacements, unsupported editor integrations and
multiple affected editor windows retain the conservative `editorConflict` guard.
Saving one window can reload another, so those windows must be closed before the
write. Add drafts retain explicit update methods and reject content replacement
or type changes when a draft already contains unsaved content.

## Native writes and media

Services use native note-type conversion, deck moves, scheduler states/answers and
coordinated preset update requests. Atomic bulk note creation preflights before a
native bulk transaction; ordinary bulk work returns independent item outcomes.
Undo support is declared per method; grouped undo is distinct from atomicity.
Advanced history import is an explicit database transaction, clears undo and
requests native cache/window refresh. Template usage uses an adapter mirroring
Anki's token grammar because the parsed tree is not exposed to Python.

Media acquisition finishes before replacement. Replacement writes use a journal
outside the media directory, backing up originals before native trash/write.
Failure restores request-owned files; ownership conflicts preserve unrelated
changes and retain the journal for recovery. Legacy note attachments use the same
journal around collection changes. Database and filesystem writes do not share
an atomic transaction. Recovery conservatively keeps media referenced by notes
when interruption makes collection commit status uncertain.

## Access, configuration and validation

Existing settings and default fallbacks remain in `plugin/config.json` and
`plugin/util.py`. Legacy requests use body `key`; v1 uses Bearer authentication.
Permission methods alone bypass key checks and receive origin context from the
transport. Origin filtering and authentication are independent. Routine logs
contain method/ID/timing/error metadata, excluding credentials and note/media bodies.

`api.describe` reports adapter presence and checked native dependencies. Availability
is a capability probe, not proof of integration testing; method-specific state and
unsupported subfeatures can still produce explicit errors.

`tests_api/` is the default suite and uses disposable native collections, with
controlled Qt admission for ordering tests. Client request flows cover Yomitan,
Obsidian_to_Anki, LunaTranslator, asbplayer, GameSentenceMiner (GSM) and Jiten.
Real GUI checks require a working
display and temporary profiles; sync requires a disposable test account. Run `make test` to execute unit tests and all GUI scenarios for both protocols against
the latest Anki version listed in `tools/test.sh`, using isolated cached environments.
On Windows, `.\tools\test.ps1` uses the same default in PowerShell.
Use `make test TEST_ARGS=--all-versions` or `.\tools\test.ps1 -AllVersions` to run the full matrix.
Use `make test-unit` (PowerShell: `-NoUI`) to skip GUI scenarios when a display is unavailable.
The public references are [API v1](API_V1.md) and [legacy API](API_LEGACY.md).
The [coverage inventory](API_TEST_COVERAGE.md) distinguishes direct service,
controlled admission, and real GUI evidence for each public method. Both test
runners validate its registry coverage and source references; known testing gaps
are explicitly listed and remain work to do.

`tools/build_api_contract.py` bundles the source contract, including the
`x-legacyTargets` registry used for legacy capability checks. Use `--check` to
verify generated output without writing files. `make package` runs `tools/package.sh`
and the Python ZIP builder, `tools/package_addon.py`, without cleaning the checkout.

## Extended contracts

The legacy facade retains the fork's action names and parameters. `Legacy` routes
extended card requests to `ExtendedCards`, bound to the admitted collection service;
HTTP execution therefore shares upstream session pinning, API-key/origin checks,
background serialization, and post-operation notifications. Scheduling writes pass
native change records to `Services.change`, invalidating predictions and notifying
Anki on the main thread after completion. No extended write bypasses admission.

`cardsInfo(fields=...)` keeps both documented uses: `prop:*` selectors request
additional current FSRS metrics, while nonempty card-record selectors use upstream
projection. An empty list retains the fork's full-response behavior. `noteFields`
and `retrieved_info_mode` select the extended response contract. Native batch
results are request-local; ordering, duplicates, missing-card handling, and fallback
initialization remain covered by regression tests.

Reviewer answer display also respects native render readiness on FSRS7 builds.
It waits through Anki's question-ready hook, revalidates the captured token, and
removes callbacks on success, reviewer exit, stale state or a bounded timeout.
This uses callback completion rather than a nested event loop.
