"""Event-driven HTTP transport on real Qt socket notifiers.

The server runs in a subprocess with its own QCoreApplication and no
housekeeping timer, so every reply proves that socket readiness and completion
callbacks alone drive reads and writes.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SERVER = r'''
import sys
from PyQt6.QtCore import QCoreApplication, QSocketNotifier, QTimer
from plugin import util, web
from plugin.protocol import Deferred

config = dict(util.DEFAULT_CONFIG, webBindPort=0)
util.setting = config.__getitem__
app = QCoreApplication([])
idle = []

def handler(request):
    action, params = request['action'], request.get('params', {})
    if action == 'later':
        pending = Deferred()
        QTimer.singleShot(params['delayMs'], lambda: pending.resolve({'result': 'later', 'error': None}))
        return pending
    if action == 'quit':
        QTimer.singleShot(0, app.quit)
    if action == 'stats':
        return {'result': {'idle': len(idle), 'clients': len(server.clients)}, 'error': None}
    return {'result': len(params.get('pad', '')), 'error': None}

server = web.WebServer(handler)
server.listen(QSocketNotifier)
server.onIdle = lambda: idle.append(True)
print(server.sock.getsockname()[1], flush=True)
app.exec()
server.close()
'''


def request_bytes(action, **params):
    body = json.dumps({'action': action, 'version': 6, 'params': params}).encode()
    return b'POST / HTTP/1.1\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body


def read_reply(sock):
    chunks = []
    while chunk := sock.recv(65536):
        chunks.append(chunk)
    head, body = b''.join(chunks).split(b'\r\n\r\n', 1)
    assert head.startswith(b'HTTP/1.1 200 OK'), head
    return json.loads(body)


@pytest.fixture
def port():
    process = subprocess.Popen([sys.executable, '-c', SERVER], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        if not line:
            pytest.fail('Event server failed to start: ' + process.stderr.read())
        yield int(line)
        call(int(line), 'quit')
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


def connect(port):
    sock = socket.create_connection(('127.0.0.1', port), timeout=10)
    return sock


def call(port, action, **params):
    with connect(port) as sock:
        sock.sendall(request_bytes(action, **params))
        return read_reply(sock)


def test_notifiers_deliver_replies_without_a_poll_timer(port):
    assert call(port, 'echo', pad='abc') == {'result': 3, 'error': None}
    # A deferred completion writes its reply as soon as it resolves.
    started = time.monotonic()
    assert call(port, 'later', delayMs=200) == {'result': 'later', 'error': None}
    assert time.monotonic() - started >= 0.2


def test_large_fragmented_body_is_parsed_once_complete(port):
    pad = 'x' * (4 * 1048576)
    raw = request_bytes('echo', pad=pad)
    with connect(port) as sock:
        # Split inside the header terminator and the body.
        head_end = raw.index(b'\r\n\r\n')
        for piece in (raw[:head_end + 2], raw[head_end + 2:head_end + 1000], raw[head_end + 1000:]):
            sock.sendall(piece)
        assert read_reply(sock) == {'result': len(pad), 'error': None}


def test_stalled_upload_does_not_block_other_connections(port):
    raw = request_bytes('echo', pad='y' * 100000)
    stalled = connect(port)
    try:
        stalled.sendall(raw[:-10])
        # Another connection is served while the first upload is incomplete.
        assert call(port, 'echo', pad='z') == {'result': 1, 'error': None}
        stalled.sendall(raw[-10:])
        assert read_reply(stalled) == {'result': 100000, 'error': None}
    finally:
        stalled.close()


def test_idle_hook_runs_when_last_connection_closes(port):
    first = call(port, 'stats')['result']
    second = call(port, 'stats')['result']
    # The stats connection itself is open while stats are collected.
    assert first['clients'] == second['clients'] == 1
    assert second['idle'] == first['idle'] + 1


def test_peer_disconnect_before_request_releases_connection(port):
    with connect(port) as sock:
        sock.sendall(b'POST / HTTP/1.1\r\nContent-Len')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if call(port, 'stats')['result']['clients'] == 1:
            break
        time.sleep(0.05)
    assert call(port, 'stats')['result']['clients'] == 1
