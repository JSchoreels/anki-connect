"""Compare the pinned pre-rewrite baseline with isolated performance prototypes.

Run: rtk proxy python3 benchmarks/performance.py
Requires the locally installed anki, jsonschema, and PyQt6 packages.
Does not import/launch the add-on, open a user profile, or modify runtime code.
All collection writes use supported Anki APIs in a new temporary collection.
"""

import ast
import concurrent.futures
import copy
import hashlib
import importlib.metadata
import inspect
import itertools
import json
import multiprocessing
import platform
import random
import select
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
ROWS = []
CHECKS = []


BASELINE_REVISION = '6f0d1bef6d66a3162868d1d25a3a9a6d317099a1'


def baseline_source(path):
    return subprocess.check_output(
        ['git', '-C', str(ROOT), 'show', BASELINE_REVISION + ':' + path], text=True)


def load_code(production_cards=False):
    """Execute original classes without plugin startup or an Anki GUI."""
    util_tree = ast.parse(baseline_source("plugin/util.py"))
    keep = [node for node in util_tree.body if isinstance(node, ast.FunctionDef)
            and node.name in ("api", "cardQuestion", "cardAnswer")]
    util_ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "plugin/util.py", "exec"), util_ns)
    config = dict(apiKey=None, apiVersion=6, webBacklog=5,
                  webBindAddress="127.0.0.1", webBindPort=0,
                  webCorsOrigin=None, webCorsOriginList=["http://localhost"])
    util = types.SimpleNamespace(**util_ns, setting=config.get, batched=itertools.batched)
    tree = ast.parse(baseline_source("plugin/web.py"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    ns = dict(json=json, jsonschema=jsonschema, select=select, socket=socket, util=util)
    exec(compile(tree, "plugin/web.py", "exec"), ns)
    ns["web"] = types.SimpleNamespace(WebServer=ns["WebServer"])
    from anki.errors import NotFoundError
    ns.update(inspect=inspect, Card=object, Note=object, NotFoundError=NotFoundError,
              anki_version=(25, 9, 2))
    tree = ast.parse(baseline_source("plugin/__init__.py"))
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                and node.name == "AnkiConnect")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "plugin/__init__.py", "exec"), ns)
    if production_cards:
        from plugin.extended import ExtendedCards
        # Keep the isolated benchmark facade/transport; exercise actual runtime card code.
        for name, method in vars(ExtendedCards).items():
            if callable(method) and name not in ('__init__', 'collection', 'getCard', 'deckNameFromId'):
                if not name.startswith('_'):
                    method = ns['util'].api()(method)
                setattr(ns['AnkiConnect'], name, method)
    return ns


def new_ac(ns):
    return ns["AnkiConnect"]()


def cached_validation(schema):
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    validator = cls(schema)

    def validate(instance, unused_schema=None):
        # Match jsonschema.validate's error selection, not just acceptance.
        error = jsonschema.exceptions.best_match(validator.iter_errors(instance))
        if error is not None:
            raise error
    return validate


def install_dispatch_cache(ac, ns):
    # Cache method names, looking up the bound method at call time.
    methods = [(name, tuple(getattr(method, "versions", ())))
               for name, method in inspect.getmembers(ac, predicate=inspect.ismethod)
               if getattr(method, "api", False)]
    by_version = {}
    original = ac.handler

    def handler(self, request):
        name = request.get("action", "")
        version = request.get("version", 4)
        if not isinstance(version, int) or not isinstance(name, str):
            return original(request)
        self.logEvent("request", request)
        try:
            if request.get("key") != ns["util"].setting("apiKey") and name != "requestPermission":
                raise Exception("valid api key must be provided")
            if version not in by_version:
                table = {}
                for method_name, versions in methods:
                    last_version, last_name = 0, None
                    for api_version, api_name in versions:
                        if last_version < api_version <= version:
                            last_version, last_name = api_version, api_name
                    if last_name is None and last_version == 0:
                        last_name = method_name
                    if last_name is not None:
                        table.setdefault(last_name, method_name)
                by_version[version] = table
            method_name = by_version[version].get(name)
            if method_name is None:
                raise Exception("unsupported action")
            result = getattr(self, method_name)(**request.get("params", {}))
            reply = ns["format_success_reply"](version, result)
        except Exception as error:
            reply = ns["format_exception_reply"](version, error)
        self.logEvent("reply", reply)
        return reply
    ac.handler = types.MethodType(handler, ac)


def outcome(fn):
    try:
        return ("ok", fn())
    except Exception as error:
        return ("error", type(error).__name__, str(error))


def equal(label, old, new):
    a, b = outcome(old), outcome(new)
    assert a == b, (label, a, b)
    CHECKS.append(label)


def compare(name, old, new, *, iterations=1, repeats=7, size=None):
    equal(name, old, new)
    samples = {"current": [], "prototype": []}
    functions = {"current": old, "prototype": new}
    for repeat in range(repeats):
        order = ("current", "prototype") if repeat % 2 == 0 else ("prototype", "current")
        for variant in order:
            start = time.perf_counter_ns()
            for _ in range(iterations):
                functions[variant]()
            samples[variant].append((time.perf_counter_ns() - start) / iterations / 1e6)
    old_ms, new_ms = (statistics.median(samples[key]) for key in ("current", "prototype"))
    row = dict(name=name, size=size, current_ms=old_ms, prototype_ms=new_ms,
               speedup=old_ms / new_ms, iterations=iterations, repeats=repeats, samples_ms=samples)
    ROWS.append(row)
    print(f"{name} n={size}: {old_ms:.4f} -> {new_ms:.4f} ms ({old_ms/new_ms:.2f}x)", flush=True)


class IncrementalParser:
    """Prototype: scan headers once and grow one mutable body buffer."""
    def __init__(self, request_class):
        self.request_class = request_class
        self.data = bytearray()
        self.scan_from = 0
        self.total = None

    def feed(self, chunk):
        self.data.extend(chunk)
        if self.total is None:
            end = self.data.find(b"\r\n\r\n", self.scan_from)
            if end < 0:
                self.scan_from = max(0, len(self.data) - 3)
                return None
            lines = bytes(self.data[:end]).split(b"\r\n")
            self.method = lines[0].split(b" ")[0].upper()
            self.headers = {}
            for line in lines[1:]:
                pair = line.split(b": ")
                self.headers[pair[0].lower()] = pair[1] if len(pair) > 1 else None
            self.body_start = end + 4
            self.total = self.body_start + int(self.headers.get(b"content-length", 0))
        if len(self.data) < self.total:
            return None
        return self.request_class(self.method, self.headers,
                                  bytes(memoryview(self.data)[self.body_start:self.total]))


def request_bytes(payload):
    body = json.dumps(payload).encode()
    return b"POST / HTTP/1.1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


def micro_benchmarks(ns):
    schema = ns["request_schema"]
    reused = cached_validation(schema)
    for i, request in enumerate([{}, [], None, 1, {"action": ""}, {"action": 1},
                                 {"action": "version", "version": True},
                                 {"action": "version", "version": "6", "params": []},
                                 {"action": "version", "unknown": 42}]):
        equal(f"schema edge {i}", lambda: jsonschema.validate(request, schema), lambda: reused(request))
    request = {"action": "version", "version": 6}
    compare("schema validation", lambda: jsonschema.validate(request, schema),
            lambda: reused(request), iterations=500)
    old, new = new_ac(ns), new_ac(ns)
    install_dispatch_cache(new, ns)
    requests = [dict(action="version"), dict(action="version", version=6),
                dict(action="missing", version=6), dict(action="version", version=6, key="wrong"),
                dict(action="version", version=6, params={"unexpected": True}),
                dict(action="multi", version=6, params={"actions": [
                    dict(action="version"), dict(action="version", version=6), dict(action="missing")]})]
    for i, request in enumerate(requests):
        equal(f"dispatch edge {i}", lambda: old.handler(request), lambda: new.handler(request))
    alias_old, alias_new = new_ac(ns), new_ac(ns)
    @ns["util"].api((5, "previousName"), (6, "currentName"))
    def renamed(self):
        return "alias result"
    for instance in (alias_old, alias_new):
        instance.renamed = types.MethodType(renamed, instance)
    install_dispatch_cache(alias_new, ns)
    for version in (0, 4, 5, 6, 7):
        for name in ("renamed", "previousName", "currentName"):
            request = dict(action=name, version=version)
            equal(f"dispatch alias {name} v{version}", lambda: alias_old.handler(request),
                  lambda: alias_new.handler(request))
    request = {"action": "version", "version": 6}
    compare("action dispatch (version)", lambda: old.handler(request), lambda: new.handler(request), iterations=1000)
    request = dict(action="multi", version=6, params={"actions": [dict(action="version", version=6)] * 1000})
    compare("multi dispatch", lambda: old.handler(request), lambda: new.handler(request), size=1000)
    for size in (1024, 65536, 262144, 1048576, 4194304):
        payload = request_bytes(dict(action="version", version=6, padding="x" * size))
        chunks = [payload[i:i+1024] for i in range(0, len(payload), 1024)]

        def current():
            client = ns["WebClient"](None, None)
            data = b""
            for chunk in chunks:
                data += chunk
                request, _ = client.parseRequest(data)
            return (request.method, request.headers, request.body)

        def proposed():
            parser = IncrementalParser(ns["WebRequest"])
            for chunk in chunks:
                request = parser.feed(chunk)
            return (request.method, request.headers, request.body)
        compare("incremental request parsing", current, proposed, size=size)
    # Header delimiters crossing chunk boundaries, OPTIONS, GET, UTF-8, mixed case.
    for data in (b"GET / HTTP/1.1\r\n\r\n", b"OPTIONS / HTTP/1.1\r\nOrigin: http://localhost\r\n\r\n",
                 request_bytes(dict(action="version", padding="\u732b"))):
        original, _ = ns["WebClient"](None, None).parseRequest(data)
        for chunk_size in (1, 2, 3, 7, 1024):
            parser = IncrementalParser(ns["WebRequest"])
            for start in range(0, len(data), chunk_size):
                parsed = parser.feed(data[start:start+chunk_size])
            assert vars(original) == vars(parsed)
            CHECKS.append(f"parser edge {len(data)} bytes / {chunk_size} byte chunks")


def make_lazy_details(ns):
    tree = ast.parse(baseline_source("plugin/__init__.py"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AnkiConnect")
    fn = copy.deepcopy(next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "cardsDetails"))
    # Retain API metadata so the prototype also works through action dispatch.
    # Preserve the entire original function, guarding only the stats call.
    class GuardStats(ast.NodeTransformer):
        def visit_Assign(self, node):
            if any(isinstance(target, ast.Name) and target.id == "stats" for target in node.targets):
                return ast.If(test=ast.Name(id="needs_stats", ctx=ast.Load()), body=[node], orelse=[])
            return node
    fn = GuardStats().visit(fn)
    for index, statement in enumerate(fn.body):
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "result" for target in statement.targets
        ):
            fn.body[index:index] = ast.parse(
                "needs_stats = any(field in ('prop:r', 'prop:s', 'prop:d') for field in requested_fields or ())"
            ).body
            break
    tree = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    candidate = dict(ns)
    exec(compile(tree, "benchmark_lazy_stats", "exec"), candidate)
    return candidate["cardsDetails"]


def bulk_mod(col, table, ids, key, fallback):
    # Keep Anki's existing handling of unusual JSON values and oversized IDs.
    if any(type(identifier) is not int or not -(2**63) <= identifier < 2**63 for identifier in ids):
        return fallback(ids)
    found = {}
    for batch in itertools.batched(dict.fromkeys(ids), 999):
        placeholders = ",".join("?" for _ in batch)
        # Read only; the temporary collection was populated through Anki APIs.
        found.update(col.db.all(f"select id, mod from {table} where id in ({placeholders})", *batch))
    return [{key: identifier, "mod": found[identifier]} if identifier in found else {} for identifier in ids]


def collection_benchmarks(ns):
    from anki.collection import AddNoteRequest, Collection
    with tempfile.TemporaryDirectory(prefix="ankiconnect-benchmark-", dir="/private/tmp") as directory:
        col = Collection(str(Path(directory) / "synthetic.anki2"))
        try:
            model = col.models.by_name("Basic")
            requests = []
            for i in range(10000):
                note = col.new_note(model)
                note["Front"] = f"Synthetic benchmark note {i}: " + "x" * 80
                note["Back"] = "Synthetic answer " + "y" * 160
                note.tags = ["benchmark", f"group{i%10}"]
                requests.append(AddNoteRequest(note, 1))
            col.add_notes(requests)
            cards = list(col.find_cards(""))
            notes = [request.note.id for request in requests]
            assert len(cards) == len(notes) == 10000
            old = new_ac(ns)
            old.collection = lambda: col
            new = new_ac(ns)
            new.collection = lambda: col
            new.cardsDetails = types.MethodType(make_lazy_details(ns), new)
            print("Created isolated 10,000-note / 10,000-card Anki collection.", flush=True)
            for ids in ([], [cards[2], cards[0], cards[2], 1], cards[:999] + [cards[0], 1, cards[1]]):
                equal(f"cardsModTime order/duplicates/missing n={len(ids)}",
                      lambda: old.cardsModTime(ids), lambda: bulk_mod(col, "cards", ids, "cardId", old.cardsModTime))
            for ids in ([], [notes[2], notes[0], notes[2], 1], notes[:999] + [notes[0], 1, notes[1]]):
                equal(f"notesModTime order/duplicates/missing n={len(ids)}",
                      lambda: old.notesModTime(ids), lambda: bulk_mod(col, "notes", ids, "noteId", old.notesModTime))
            for unusual in ([str(cards[0])], [None], [False], [1.1], [2**80]):
                equal(f"cardsModTime unusual input {unusual}", lambda: old.cardsModTime(unusual),
                      lambda: bulk_mod(col, "cards", unusual, "cardId", old.cardsModTime))
                equal(f"notesModTime unusual input {unusual}", lambda: old.notesModTime(unusual),
                      lambda: bulk_mod(col, "notes", unusual, "noteId", old.notesModTime))
            for fields in (None, [], ["due", "queue", "type", "interval", "reps"], ["prop:r"],
                           ["prop:r", "prop:s", "prop:d", "due"], ["invalid"]):
                equal(f"cardsDetails field selection {fields}",
                      lambda: old.cardsDetails(cards[:3], fields=fields),
                      lambda: new.cardsDetails(cards[:3], fields=fields))
            equal("cardsDetails missing ID exception", lambda: old.cardsDetails([1], fields=["due"]),
                  lambda: new.cardsDetails([1], fields=["due"]))
            equal("cardsDetails selected note field", lambda: old.cardsDetails(cards[:3], fields=["due"], noteFields=["Front"]),
                  lambda: new.cardsDetails(cards[:3], fields=["due"], noteFields=["Front"]))
            rng = random.Random(812)
            rng.shuffle(cards)
            rng.shuffle(notes)
            for count in (1, 100, 1000, 10000):
                cids, nids = cards[:count], notes[:count]
                compare("cardsModTime bulk read", lambda: old.cardsModTime(cids),
                        lambda: bulk_mod(col, "cards", cids, "cardId", old.cardsModTime), size=count)
                compare("notesModTime bulk read", lambda: old.notesModTime(nids),
                        lambda: bulk_mod(col, "notes", nids, "noteId", old.notesModTime), size=count)
                compare("cardsDetails basic fields (skip stats)",
                        lambda: old.cardsDetails(cids, fields=["due", "queue", "interval"]),
                        lambda: new.cardsDetails(cids, fields=["due", "queue", "interval"]), size=count)
            cids = cards[:1000]
            compare("cardsDetails FSRS fields (control)", lambda: old.cardsDetails(cids, fields=["prop:r", "prop:s"]),
                    lambda: new.cardsDetails(cids, fields=["prop:r", "prop:s"]), size=1000, repeats=21)
        finally:
            col.close()


class EventClient:
    """Benchmark transport, injectable for deterministic socket regression tests."""
    def __init__(self, sock, handler, request_class, notifier_class, clients):
        self.sock = sock
        self.handler = handler
        self.clients = clients
        self.parser = IncrementalParser(request_class)
        self.reader = notifier_class(sock.fileno(), notifier_class.Type.Read)
        self.writer = notifier_class(sock.fileno(), notifier_class.Type.Write)
        self.writer.setEnabled(False)
        self.reader.activated.connect(self.read)
        self.writer.activated.connect(self.write)
        self.output = b""
        self.offset = 0

    def read(self, *_):
        try:
            for _ in range(16):
                chunk = self.sock.recv(65536)
                if not chunk:
                    self.close()
                    return
                request = self.parser.feed(chunk)
                if request is not None:
                    self.reader.setEnabled(False)
                    self.output = self.handler(request)
                    self.write()
                    return
        except BlockingIOError:
            pass
        except (ConnectionError, OSError):
            self.close()

    def write(self, *_):
        try:
            self.offset += self.sock.send(memoryview(self.output)[self.offset:])
            if self.offset == len(self.output):
                self.close()
            else:
                self.writer.setEnabled(True)
        except BlockingIOError:
            self.writer.setEnabled(True)
        except (ConnectionError, OSError):
            self.close()

    def close(self):
        self.reader.setEnabled(False)
        self.writer.setEnabled(False)
        self.reader.deleteLater()
        self.writer.deleteLater()
        self.sock.close()
        self.clients.discard(self)


def server_process(pipe, variant):
    from PyQt6.QtCore import QCoreApplication, QSocketNotifier, QTimer
    ns = load_code()
    ac = new_ac(ns)
    if variant == "event_all":
        install_dispatch_cache(ac, ns)
        ns["jsonschema"] = types.SimpleNamespace(validate=cached_validation(ns["request_schema"]),
                                                ValidationError=jsonschema.ValidationError)
    app = QCoreApplication([])
    server = ns["WebServer"](ac.handler)
    server.listen()

    clients = set()
    if variant == "current":
        timer = QTimer()
        timer.timeout.connect(server.advance)
        timer.start(25)
    else:
        def accept(*_):
            while True:
                try:
                    sock, _ = server.sock.accept()
                except BlockingIOError:
                    break
                sock.setblocking(False)
                clients.add(EventClient(sock, server.handlerWrapper, ns["WebRequest"], QSocketNotifier, clients))
        notifier = QSocketNotifier(server.sock.fileno(), QSocketNotifier.Type.Read)
        notifier.activated.connect(accept)
    pipe.send(server.sock.getsockname()[1])
    pipe.close()
    app.exec()


def http_call(port, payload=None, raw=None):
    payload = payload or dict(action="version", version=6)
    start = time.perf_counter_ns()
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(raw if raw is not None else request_bytes(payload))
        chunks = []
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    elapsed = (time.perf_counter_ns() - start) / 1e6
    response = b"".join(chunks)
    return elapsed, response


def percentile(values, percentile):
    values = sorted(values)
    return values[max(0, int(len(values) * percentile + 0.999999) - 1)]


def transport_benchmarks():
    results = {}
    ctx = multiprocessing.get_context("spawn")
    for variant in ("current", "event_socket", "event_all"):
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(target=server_process, args=(child, variant))
        process.start()
        child.close()
        try:
            assert parent.poll(30), "Test server did not start"
            port = parent.recv()
            cases = [dict(action="version"), dict(action="version", version=6),
                     dict(action="missing", version=6), dict(action="version", version=6, params={"unexpected": 1}),
                     dict(action="multi", version=6, params={"actions": [dict(action="version"), dict(action="missing")]})]
            responses = [http_call(port, case)[1] for case in cases]
            responses += [http_call(port, raw=raw)[1] for raw in (
                b"GET / HTTP/1.1\r\n\r\n",
                b"POST / HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}",
                b"OPTIONS / HTTP/1.1\r\nOrigin: http://localhost\r\nAccess-Control-Request-Private-Network: true\r\n\r\n",
                b"POST / HTTP/1.1\r\nOrigin: https://disallowed.example\r\nContent-Length: 20\r\n\r\n{\"action\":\"version\"}")]
            if variant == "current":
                original_responses = responses
            else:
                assert responses == original_responses
                CHECKS.append(f"HTTP exact bytes: nine success/error/version/CORS/OPTIONS cases {variant}")
            sequential = [http_call(port)[0] for _ in range(100)]
            burst = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                for _ in range(15):
                    gate = threading.Barrier(4)
                    def call_together(_):
                        gate.wait()
                        return http_call(port)[0]
                    burst.extend(executor.map(call_together, range(4)))
            uploads = [http_call(port, dict(action="version", version=6, padding="x" * 1048576))[0] for _ in range(15)]
            multi = [http_call(port, dict(action="multi", version=6,
                                         params={"actions": [dict(action="version", version=6)] * 1000}))[0]
                     for _ in range(10)]
            fragmented = []
            for _ in range(5):
                data = request_bytes(dict(action="version", version=6))
                with socket.create_connection(("127.0.0.1", port), timeout=5) as slow:
                    slow.sendall(data[:-1])
                    # Let the original server enter recv(), then issue an unrelated request.
                    time.sleep(0.04)
                    finish = threading.Timer(0.20, lambda: slow.sendall(data[-1:]))
                    finish.start()
                    fragmented.append(http_call(port)[0])
                    finish.join()
                    while slow.recv(1024):
                        pass
            results[variant] = {}
            for label, samples in (("sequential version", sequential), ("4 concurrent version requests", burst),
                                   ("1 MiB HTTP request", uploads), ("1000-action HTTP multi", multi),
                                   ("version while another upload is stalled", fragmented)):
                summary = dict(p50_ms=statistics.median(samples), p95_ms=percentile(samples, .95),
                               count=len(samples), samples_ms=samples)
                results[variant][label] = summary
                print(f"HTTP {variant} / {label}: p50 {summary['p50_ms']:.3f}, p95 {summary['p95_ms']:.3f} ms", flush=True)
        finally:
            parent.close()
            process.terminate()
            process.join(10)
    return results


def save_report(transport):
    RESULTS.mkdir(exist_ok=True)
    data = dict(timestamp=datetime.now(timezone.utc).isoformat(), python=sys.version,
                platform=platform.platform(), packages={name: importlib.metadata.version(name)
                for name in ("anki", "jsonschema", "PyQt6")},
                source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ("plugin/web.py", "plugin/util.py", "plugin/__init__.py")},
                comparisons=ROWS, transport=transport, equivalence_checks=CHECKS)
    (RESULTS / "performance.json").write_text(json.dumps(data, indent=2) + "\n")
    lines = ["# AnkiConnect performance prototypes", "", f"Measured {data['timestamp']}", "",
             f"Environment: {data['platform']}; Python {platform.python_version()}; "
             + "; ".join(f"{key} {value}" for key, value in data["packages"].items()) + ".", "",
             "## Method", "",
             "Current methods are loaded from repository source without add-on startup. Candidate code exists only "
             "in the benchmark. Runtime files and the installed add-on are unchanged. Collection benchmarks use "
             "a fresh temporary collection with 10,000 synthetic Basic notes and 10,000 new cards, populated only "
             "through Anki APIs. Metadata queries are read-only and run through Anki's database bridge. "
             "The temporary collection is closed and removed afterward; no user profile is opened.", "",
             "Component results are medians of seven timed batches (21 for the FSRS control), alternating current/prototype order after "
             "an equality check warms both paths. Input IDs are shuffled deterministically. Raw samples and source "
             "hashes are recorded in performance.json. These are warm-cache measurements.", "",
             "HTTP benchmarks use real TCP loopback, one new connection per request, a separate process with "
             "QCoreApplication, the original 25 ms QTimer and backlog 5. Event/socket prototypes keep the same "
             "request/response handlers and connection-closing behavior. event_socket changes only transport; "
             "event_all additionally reuses the schema validator and caches action resolution. Every handler "
             "runs serially on that process's Qt thread. This is not the full Anki GUI.", "",
             "## Component results", "", "| Operation | Items / payload bytes | Current ms | Prototype ms | Speedup |",
             "|---|---:|---:|---:|---:|"]
    for row in ROWS:
        lines.append(f"| {row['name']} | {row['size'] or '—'} | {row['current_ms']:.4f} | {row['prototype_ms']:.4f} | {row['speedup']:.2f}× |")
    lines += ["", "## HTTP results", "", "| Workload | Variant | Samples | p50 ms | p95 ms |", "|---|---|---:|---:|---:|"]
    for variant, cases in transport.items():
        for name, row in cases.items():
            lines.append(f"| {name} | {variant} | {row['count']} | {row['p50_ms']:.3f} | {row['p95_ms']:.3f} |")
    lines += ["", "## Compatibility checks", "", f"{len(CHECKS)} differential checks passed. These compare values "
              "or exact exception strings, including empty inputs, missing IDs, duplicates, input order across "
              "the 999-ID chunk boundary, unusual ID types, fields, API versions and aliases, nested multi responses, authentication failures, "
              "schema errors, fragmented parsing, and selected exact HTTP responses including CORS/OPTIONS.", "",
              "## Limits", "", "These prototypes are not a production compatibility certification. The full "
              "repository pytest suite was not run (pytest_anki is absent). Tests cover this installed Anki and "
              "Qt version, not all supported versions/platforms. The collection contains new cards without review "
              "history, so results do not predict all FSRS or large-history workloads. No cold-disk, actual GUI "
              "load, media download, write-batching, or installed add-on benchmark is included. Dispatch numbers "
              "use repeated version actions and warmed lookup tables. The 1 MiB HTTP payload is inert JSON "
              "padding, isolating upload/parse cost rather than media operations. A stalled-upload case completes "
              "the other request's final byte after a controlled 200 ms delay. HTTP variants run consecutively "
              "and p95 is descriptive, not a confidence bound.", "",
              "Rerun from the repository root: `rtk proxy python3 benchmarks/performance.py`.", ""]
    (RESULTS / "performance.md").write_text("\n".join(lines))
    print(f"Saved {RESULTS / 'performance.md'}", flush=True)


if __name__ == "__main__":
    namespace = load_code()
    micro_benchmarks(namespace)
    collection_benchmarks(namespace)
    transport_results = transport_benchmarks()
    save_report(transport_results)
