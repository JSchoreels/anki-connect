"""Deterministic socket tests plus bounded Qt/TCP integration smoke tests."""

import json
import multiprocessing
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import performance as bench


@pytest.fixture
def client(namespace):
    sock = Mock()
    sock.fileno.return_value = 123
    handler = Mock(return_value=b"response body")
    notifiers = Mock(Type=SimpleNamespace(Read="read", Write="write"), side_effect=lambda *_: Mock())
    clients = set()
    result = bench.EventClient(sock, handler, namespace["WebRequest"], notifiers, clients)
    clients.add(result)
    return result


def assert_closed(client):
    assert client not in client.clients
    client.sock.close.assert_called_once()
    client.reader.setEnabled.assert_called_with(False)
    client.writer.setEnabled.assert_called_with(False)
    client.reader.deleteLater.assert_called_once()
    client.writer.deleteLater.assert_called_once()


def test_fragmented_request_survives_would_block_and_runs_once(client):
    raw = bench.request_bytes({"action": "version", "version": 6})
    client.sock.recv.side_effect = [raw[:-1], BlockingIOError(), raw[-1:]]
    client.sock.send.side_effect = lambda data: len(data)
    client.read()
    client.handler.assert_not_called()
    client.sock.close.assert_not_called()
    client.read()
    assert json.loads(client.handler.call_args.args[0].body) == {"action": "version", "version": 6}
    client.handler.assert_called_once()
    assert_closed(client)


def test_partial_writes_and_backpressure_preserve_every_response_byte(client):
    client.sock.recv.return_value = bench.request_bytes({"action": "version"})
    sent = bytearray()
    limits = iter([3, None, 2, 100])
    def send(data):
        limit = next(limits)
        if limit is None:
            raise BlockingIOError()
        accepted = bytes(data[:limit])
        sent.extend(accepted)
        return len(accepted)
    client.sock.send.side_effect = send
    client.read()
    assert sent == b"res"
    client.sock.close.assert_not_called()
    client.writer.setEnabled.assert_called_with(True)
    client.write()  # Would-block must not lose the unsent tail.
    assert sent == b"res"
    client.write()
    assert sent == b"respo"
    client.write()
    assert sent == b"response body"
    client.handler.assert_called_once()
    assert_closed(client)


@pytest.mark.parametrize("received", [b"", ConnectionResetError(), OSError("closed")])
def test_disconnected_read_releases_socket_and_notifiers(client, received):
    client.sock.recv.side_effect = [received]
    client.read()
    client.handler.assert_not_called()
    assert_closed(client)


def test_disconnected_write_releases_socket_and_notifiers(client):
    client.sock.recv.return_value = bench.request_bytes({"action": "version"})
    client.sock.send.side_effect = BrokenPipeError()
    client.read()
    assert_closed(client)


def test_busy_upload_yields_with_request_still_incomplete(client):
    # More readable bytes than one callback's budget; no clocks/sleeps involved.
    client.sock.recv.side_effect = [b"POST / HTTP/1.1\r\nContent-Length: 100000\r\n\r\n"] + [b"x"] * 32
    client.read()
    assert client.sock.recv.call_count < 33
    client.handler.assert_not_called()
    client.sock.close.assert_not_called()
    assert client in client.clients


@pytest.fixture(params=["current", "cached"], ids=["current", "cached"])
def wrapper(namespace, request):
    ac = bench.new_ac(namespace)
    if request.param == "cached":
        bench.install_dispatch_cache(ac, namespace)
        namespace["jsonschema"] = SimpleNamespace(
            validate=bench.cached_validation(namespace["request_schema"]),
            ValidationError=bench.jsonschema.ValidationError)
    return namespace["WebServer"](ac.handler)


def unpack_response(raw):
    headers, body = raw.split(b"\r\n\r\n", 1)
    lines = headers.split(b"\r\n")
    parsed = dict(line.split(b": ", 1) for line in lines[1:])
    if b"Content-Length" in parsed:
        assert int(parsed[b"Content-Length"]) == len(body)
    return lines[0], parsed, body


def test_wrapper_returns_legacy_and_modern_shapes(namespace, wrapper):
    for payload, expected in [({"action": "version"}, 6),
                              ({"action": "version", "version": 6}, {"result": 6, "error": None})]:
        request = namespace["WebRequest"](b"POST", {}, json.dumps(payload).encode())
        status, headers, body = unpack_response(wrapper.handlerWrapper(request))
        assert status == b"HTTP/1.1 200 OK"
        assert headers[b"Content-Type"] == b"application/json"
        assert json.loads(body) == expected


@pytest.mark.parametrize("body, error", [
    (b"{}", "'action' is a required property"),
    (b'{"action": 1}', "1 is not of type 'string'"),
    (b"[]", "is not of type 'object'"),
    (b"{bad", "Expecting property name"),
    (b"\xff", "can't decode"),
])
def test_wrapper_keeps_validation_errors(namespace, wrapper, body, error):
    request = namespace["WebRequest"](b"POST", {}, body)
    status, _, response = unpack_response(wrapper.handlerWrapper(request))
    assert status == b"HTTP/1.1 200 OK"
    assert json.loads(response)["result"] is None
    assert error in json.loads(response)["error"]


@pytest.mark.parametrize("body", [b'{"action": "version"}', b"{bad"])
def test_disallowed_origin_remains_forbidden(namespace, wrapper, body):
    request = namespace["WebRequest"](b"POST", {b"origin": b"https://disallowed.example"}, body)
    status, _, body = unpack_response(wrapper.handlerWrapper(request))
    assert status == b"HTTP/1.1 403 Forbidden"
    assert body == b""


def test_options_keeps_private_network_cors_headers(namespace, wrapper):
    request = namespace["WebRequest"](b"OPTIONS", {
        b"origin": b"http://localhost", b"access-control-request-private-network": b"true"}, b"")
    status, headers, body = unpack_response(wrapper.handlerWrapper(request))
    assert status == b"HTTP/1.1 200 OK"
    assert headers[b"Access-Control-Allow-Origin"] == b"http://localhost"
    assert headers[b"Access-Control-Allow-Private-Network"] == b"true"
    assert body == b""


def test_empty_http_body_keeps_version_banner(namespace, wrapper):
    request = namespace["WebRequest"](b"GET", {}, b"")
    _, _, body = unpack_response(wrapper.handlerWrapper(request))
    assert json.loads(body) == {"apiVersion": "AnkiConnect v.6"}


@pytest.fixture(scope="module", params=["current", "event_socket", "event_all"])
def server(request):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=bench.server_process, args=(child, request.param))
    process.start()
    child.close()
    try:
        assert parent.poll(20), "Isolated Qt server failed to start"
        yield request.param, parent.recv()
    finally:
        parent.close()
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)


def test_real_qt_server_keeps_nested_multi_response(server):
    _, port = server
    _, response = bench.http_call(port, {"action": "multi", "version": 6, "params": {"actions": [
        {"action": "version"}, {"action": "version", "version": 6}, {"action": "missing"}]}})
    status, _, body = unpack_response(response)
    assert status == b"HTTP/1.1 200 OK"
    assert json.loads(body) == {"result": [6, {"result": 6, "error": None},
                                           {"result": None, "error": "unsupported action"}], "error": None}


def test_real_qt_server_handles_large_body_and_next_connection(server):
    _, port = server
    for padding in ("猫" * 350000, ""):
        _, response = bench.http_call(port, {"action": "version", "version": 6, "padding": padding})
        _, _, body = unpack_response(response)
        assert json.loads(body) == {"result": 6, "error": None}


def test_event_server_can_reply_before_another_upload_finishes(server):
    variant, port = server
    if variant == "current":
        pytest.skip("The original blocking transport intentionally lacks this improvement")
    raw = bench.request_bytes({"action": "version", "version": 6})
    with socket.create_connection(("127.0.0.1", port), timeout=5) as slow:
        slow.sendall(raw[:-1])
        # A correctness dependency: fast request must finish BEFORE slow's last byte.
        # The timeout is only a hang guard, not a performance threshold.
        with socket.create_connection(("127.0.0.1", port), timeout=3) as fast:
            fast.sendall(raw)
            response = bytearray()
            while chunk := fast.recv(1024):
                response.extend(chunk)
        assert json.loads(unpack_response(bytes(response))[2]) == {"result": 6, "error": None}
        slow.sendall(raw[-1:])
        response = bytearray()
        while chunk := slow.recv(1024):
            response.extend(chunk)
        assert json.loads(unpack_response(bytes(response))[2]) == {"result": 6, "error": None}
