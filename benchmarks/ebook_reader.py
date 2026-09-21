"""Serve an isolated, instrumented copy of the running ebook-reader for A/B tests.

Only the benchmark proxy's responses are instrumented. Neither repository's
runtime source nor the installed add-on is changed. Collection input must be an
exported snapshot, never an active profile. See README.md for the workflow.
"""

import argparse
import hashlib
import http.client
import json
import multiprocessing
import shutil
import sys
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema

from performance import EventClient, cached_validation, install_dispatch_cache
from performance import load_code, make_lazy_details, new_ac

HERE = Path(__file__).resolve().parent


def backend(pipe, variant, collection_path, origin, log_path, prototype):
    from anki.collection import Collection
    from PyQt6.QtCore import QCoreApplication, QSocketNotifier, QTimer

    col = Collection(collection_path)
    ns = load_code()
    ns["util"].setting.__self__["webCorsOriginList"] = [origin]
    ac = new_ac(ns)
    ac.collection = lambda: col
    native_comparison = prototype in {'native-metrics', 'native-details'}
    if native_comparison:
        assert hasattr(col, 'card_memory_metrics'), 'Native comparison requires the rebuilt Anki core'
        if variant == 'current' or prototype == 'native-metrics':
            ac._cardsDetailsBatch = lambda *args: None
        if variant == 'current' and prototype == 'native-metrics':
            ac._cardMetrics = lambda cards, fields: {}
        if prototype == 'native-details':
            assert hasattr(col, 'card_details'), 'Card details comparison requires the rebuilt core'
    elif variant == "prototype":
        if prototype == 'bulk-notes':
            from profile_reader import make_prefetched_details
            ac.cardsDetails = types.MethodType(make_prefetched_details(ns), ac)
        else:
            ac.cardsDetails = types.MethodType(make_lazy_details(ns), ac)
        install_dispatch_cache(ac, ns)
        ns["jsonschema"] = types.SimpleNamespace(
            validate=cached_validation(ns["request_schema"]),
            ValidationError=jsonschema.ValidationError,
        )

    def handler(request):
        # The reader benchmark is read-only even if an accidental UI action occurs.
        if request.get("action") not in {"version", "findCards", "cardsDetails"}:
            return {"result": None, "error": "Action disabled in read-only benchmark"}
        start = time.perf_counter()
        result = ac.handler(request)
        row = {"time": time.time(), "action": request["action"],
               "cards": len(request.get("params", {}).get("cards", [])),
               "handler_ms": (time.perf_counter() - start) * 1000,
               "error": result.get("error")}
        with open(log_path, "a") as log:
            log.write(json.dumps(row) + "\n")
        return result

    app = QCoreApplication([])
    server = ns["WebServer"](handler)
    server.listen()
    clients = set()
    if variant == "current" or native_comparison:
        timer = QTimer()
        timer.timeout.connect(server.advance)
        timer.start(25)
    else:
        def accept(*_):
            while True:
                try:
                    sock, _ = server.sock.accept()
                except BlockingIOError:
                    return
                sock.setblocking(False)
                clients.add(EventClient(sock, server.handlerWrapper,
                                        ns["WebRequest"], QSocketNotifier, clients))
        notifier = QSocketNotifier(server.sock.fileno(), QSocketNotifier.Type.Read)
        notifier.activated.connect(accept)
    pipe.send(server.sock.getsockname()[1])
    pipe.close()
    try:
        app.exec()
    finally:
        col.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--epub", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--vite-port", type=int, default=5173)
    parser.add_argument('--baseline-vite-port', type=int, help='Frozen original reader for frontend comparisons')
    parser.add_argument("--prototype", choices=['transport', 'bulk-notes', 'native-metrics', 'native-details'], default='transport')
    parser.add_argument('--live-url', help='Optional live AnkiConnect URL for a separately labeled control')
    parser.add_argument('--live-only', action='store_true', help='Use only the read-only live proxy; no collection copy required')
    parser.add_argument('--anki-packages', type=Path,
                        help='Optional installed Anki app_packages directory (use matching Python ABI)')
    args = parser.parse_args()
    if args.live_only and not args.live_url:
        parser.error('--live-only requires --live-url')
    if not args.live_only and not args.snapshot:
        parser.error('--snapshot is required unless --live-only is used')
    if args.anki_packages:
        sys.path.insert(0, str(args.anki_packages))
    args.output.mkdir(parents=True, exist_ok=True)
    config = {}
    live = urlsplit(args.live_url) if args.live_url else None
    if live:
        assert live.scheme == 'http' and live.hostname in {'localhost', '127.0.0.1'}, 'Live control must be loopback'

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, content_type="application/json", status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == '/__bench/live' and live:
                body = self.rfile.read(int(self.headers['Content-Length']))
                request = json.loads(body)
                if request.get('action') not in {'version', 'findCards', 'cardsDetails'}:
                    return self.respond(b'{"result":null,"error":"Action disabled in read-only benchmark"}')
                connection = http.client.HTTPConnection(live.hostname, live.port or 80, timeout=60)
                try:
                    connection.request('POST', '/', body, {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    return self.respond(response.read(), 'application/json', response.status)
                finally:
                    connection.close()
            if self.path != "/__bench/result":
                return self.respond(b"not found", "text/plain", 404)
            row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with (args.output / "browser-runs.jsonl").open("a") as out:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.respond(b"{}")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/__bench/config":
                return self.respond(json.dumps(config).encode())
            if path == "/__bench/book.epub":
                return self.respond(args.epub.read_bytes(), "application/epub+zip")
            if path in {"/__bench/instrument.js", "/__bench/setup.js"}:
                name = "ebook_instrument.js" if path.endswith("instrument.js") else "ebook_setup.js"
                return self.respond((HERE / name).read_bytes(), "text/javascript")
            if path == "/__bench/setup":
                return self.respond(b'<!doctype html><title>Ebook benchmark setup</title>'
                                    b'<h1>Ebook benchmark setup</h1><pre id="status">Preparing...</pre>'
                                    b'<a id="load-book" hidden href="/b?id=1">Load book and token panel</a>'
                                    b'<script type="module" src="/__bench/setup.js"></script>', "text/html")
            original_reader = 'ebook-benchmark-reader=original' in self.headers.get('Cookie', '')
            vite_port = (args.baseline_vite_port if original_reader else None) or args.vite_port
            connection = http.client.HTTPConnection("127.0.0.1", vite_port, timeout=60)
            try:
                connection.request("GET", self.path, headers={"Host": f"localhost:{vite_port}"})
                response = connection.getresponse()
                body = response.read()
                content_type = response.getheader("Content-Type", "application/octet-stream")
                if "text/html" in content_type:
                    body = body.replace(b"<head>", b'<head><script src="/__bench/instrument.js"></script>', 1)
                if path.endswith("/functions/anki/color-book-content.ts"):
                    body += b'\nwindow.__ebookBench?.wrap(BookContentColoring);\n'
                # Equivalent to opening the token panel immediately on navigation.
                # This change is only in the benchmark proxy, equally for both arms.
                if path == "/src/routes/b/+page.svelte":
                    assert b"let showTokenPanel = false;" in body
                    body = body.replace(b"let showTokenPanel = false;", b"let showTokenPanel = true;", 1)
                self.respond(body, content_type, response.status)
            finally:
                connection.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    origin = f"http://127.0.0.1:{server.server_port}"
    if live:
        config['live'] = origin + '/__bench/live'
    processes = []
    ctx = multiprocessing.get_context("spawn")
    try:
        for variant in (() if args.live_only else ("current", "prototype")):
            target = args.output / f"{variant}.anki2"
            shutil.copy2(args.snapshot, target)
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(target=backend, args=(child, variant, str(target), origin,
                                                       str(args.output / f"{variant}-requests.jsonl"),
                                                       args.prototype))
            process.start()
            child.close()
            processes.append(process)
            assert parent.poll(30), f"{variant} did not start"
            config[variant] = f"http://127.0.0.1:{parent.recv()}"
            parent.close()
        config.update(origin=origin, epub_name=args.epub.name,
                      prototype_kind=args.prototype,
                      epub_sha256=hashlib.sha256(args.epub.read_bytes()).hexdigest())
        if not args.live_only:
            from anki import buildinfo
            config.update(anki_version=buildinfo.version, anki_buildhash=buildinfo.buildhash)
        else:
            config['backend'] = 'live AnkiConnect (version verified separately)'
        (args.output / "config.json").write_text(json.dumps(config, indent=2))
        print(json.dumps(config), flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        for process in processes:
            process.terminate()
            process.join(10)


if __name__ == "__main__":
    main()
