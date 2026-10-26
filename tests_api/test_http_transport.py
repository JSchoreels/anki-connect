"""Socket-level compatibility with unpatched AnkiConnect HTTP proxies."""
import json
import socket

import pytest

from plugin import util
from plugin.contract import SCHEMA
from plugin.protocol import completed
from plugin.web import WebClient, WebServer, format_success_reply
from tests_api.test_cors import response_parts


def exchange(raw, handler):
    server_socket, peer = socket.socketpair()
    server_socket.setblocking(False)
    peer.settimeout(1)
    client = WebClient(server_socket, handler)
    try:
        peer.sendall(raw)
        for _ in range(10):
            if not client.advance():
                break
        assert client.sock is None, 'The response must close its HTTP connection.'
        chunks = []
        while chunk := peer.recv(65536):
            chunks.append(chunk)
        return response_parts(b''.join(chunks))
    finally:
        client.close()
        peer.close()


@pytest.mark.parametrize('version', [2, 6])
@pytest.mark.parametrize('connection', [b'Connection: keep-alive\r\n',
    b'Connection: close\r\ncOnNeCtIoN: keep-alive\r\n',
    b'Connection: keep-alive\r\nConnection: close\r\n'])
def test_proxy_connection_headers_keep_legacy_version_replies(monkeypatch, version, connection):
    monkeypatch.setattr(util, 'setting', util.DEFAULT_CONFIG.__getitem__)
    calls = []
    def dispatch(request):
        calls.append(request)
        return completed(format_success_reply(request['version'], 6))
    server = WebServer(dispatch)
    body = json.dumps({'action': 'version', 'version': version}).encode()
    raw = (b'POST / HTTP/1.1\r\nHost: localhost\r\n' + connection +
           b'Origin: chrome-extension://yomitan\r\nContent-Length: ' +
           str(len(body)).encode() + b'\r\n\r\n' + body)
    status, headers, received = exchange(raw, server.handlerWrapper)
    assert status == b'HTTP/1.1 200 OK'
    assert headers[b'Content-Type'] == b'application/json'
    assert headers[b'Connection'] == b'close'
    assert headers[b'Access-Control-Allow-Origin'] == b'chrome-extension://yomitan'
    assert json.loads(received) == (6 if version == 2 else {'result': 6, 'error': None})
    assert calls == [{'action': 'version', 'version': version, 'params': {}}]


@pytest.mark.parametrize('origin,key,status', [
    (b'https://unknown.example', b'Bearer secret', b'HTTP/1.1 403 Forbidden'),
    (b'http://localhost', b'Bearer wrong', b'HTTP/1.1 401 Unauthorized'),
    (b'http://localhost', b'Bearer secret', b'HTTP/1.1 200 OK'),
])
def test_repeated_connection_fields_preserve_rpc_authentication(monkeypatch, origin, key, status):
    settings = {**util.DEFAULT_CONFIG, 'apiKey': 'secret', 'webCorsOriginList': ['http://localhost']}
    monkeypatch.setattr(util, 'setting', settings.__getitem__)
    calls = []
    server = WebServer(None, lambda request, context: calls.append(request) or completed(
        {'jsonrpc': '2.0', 'id': 1, 'result': None}))
    body = b'{"jsonrpc":"2.0","id":1,"method":"collection.get"}'
    raw = (b'POST /api/v1 HTTP/1.1\r\nConnection: close\r\nConnection: keep-alive\r\n' +
           b'Origin: ' + origin + b'\r\nAuthorization: ' + key + b'\r\nContent-Length: ' +
           str(len(body)).encode() + b'\r\n\r\n' + body)
    actual, headers, received = exchange(raw, server.handlerWrapper)
    assert actual == status
    assert headers[b'Content-Type'] == b'application/json' and headers[b'Connection'] == b'close'
    if status == b'HTTP/1.1 200 OK':
        assert len(calls) == 1
        assert json.loads(received) == {'jsonrpc': '2.0', 'id': 1, 'result': None}
    else:
        assert calls == [] and received == b''


@pytest.mark.parametrize('fields', [
    b'Content-Length: 0\r\nContent-Length: 0\r\n',
    b'Content-Length: 0\r\nContent-Length: 1\r\n',
    b'Content-Length: 0\r\nTransfer-Encoding: chunked\r\n',
    b'Origin: https://unknown.example\r\nOrigin: http://localhost\r\n',
    b'Authorization: Bearer wrong\r\nAuthorization: Bearer secret\r\n',
    b'Host: localhost\r\nHost: other.example\r\n',
])
def test_ambiguous_proxy_requests_remain_rejected(fields):
    calls = []
    status, headers, body = exchange(b'POST / HTTP/1.1\r\n' + fields + b'\r\n',
                                    lambda request: calls.append(request))
    assert status == b'HTTP/1.1 400 Bad Request'
    assert headers[b'Content-Type'] == b'application/json'
    assert headers[b'Connection'] == b'close' and body == b''
    assert calls == []


@pytest.mark.parametrize('raw,reply,status', [
    (b'POST / HTTP/2\r\n\r\n', None, b'HTTP/1.1 400 Bad Request'),
    (b'POST / HTTP/1.1\r\nContent-Length: ' +
     str(SCHEMA['x-limits']['maxRequestBytes'] + 1).encode() + b'\r\n\r\n',
     None, b'HTTP/1.1 413 Payload Too Large'),
    (b'POST / HTTP/1.1\r\nContent-Length: 0\r\n\r\n',
     completed(error=RuntimeError('failed work')), b'HTTP/1.1 500 Internal Server Error'),
])
def test_transport_errors_can_be_forwarded_by_unpatched_proxies(raw, reply, status):
    actual, headers, body = exchange(raw, lambda request: reply)
    assert actual == status and body == b''
    assert headers[b'Content-Type'] == b'application/json'
    assert headers[b'Connection'] == b'close'
