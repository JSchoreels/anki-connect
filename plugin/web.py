"""Nonblocking HTTP transport for the legacy and JSON-RPC endpoints."""
import json
import socket
import time
import jsonschema
from . import util
from .protocol import Deferred, completed, ApiError, Dispatcher
from .contract import SCHEMA


class HttpError(ValueError):
    def __init__(self, status):
        self.status = status


def _http_error_response(status):
    return ('HTTP/1.1 ' + status + '\r\nContent-Type: application/json\r\n'
            'Connection: close\r\nContent-Length: 0\r\n\r\n').encode()


class WebRequest:
    def __init__(self, method, headers, body, path=b'/'):
        self.method, self.headers, self.body, self.path = method, headers, body, path


MAX_HEADER_BYTES = 65536


def parse_head(head):
    """Parse request line and headers; return (method, path, headers, body size)."""
    if len(head) > MAX_HEADER_BYTES:
        raise ValueError('Headers too large')
    lines = bytes(head).split(b'\r\n')
    method, path, version = lines[0].split(b' ')
    if version not in (b'HTTP/1.0', b'HTTP/1.1'):
        raise ValueError('Unsupported HTTP version')
    headers = {}
    for line in lines[1:]:
        key, value = line.split(b':', 1)
        key = key.strip().lower()
        value = value.strip()
        if key in headers:
            # Connection is a list field; older proxies may emit both
            # close and keep-alive. Every reply still closes this socket.
            if key != b'connection':
                raise ValueError('Duplicate header')
            headers[key] += b', ' + value
        else:
            headers[key] = value
    if b'transfer-encoding' in headers:
        raise ValueError('Chunked requests unsupported')
    size = int(headers.get(b'content-length', b'0'))
    if size > SCHEMA['x-limits']['maxRequestBytes']:
        raise HttpError('413 Payload Too Large')
    if size < 0:
        raise ValueError('Invalid body length')
    return method.upper(), path, headers, size


class RequestParser:
    """Incremental parser: headers are located and parsed once, then the body
    accumulates in one buffer until Content-Length bytes have arrived."""

    def __init__(self):
        self.data = bytearray()
        self.scanned = 0
        self.head = None
        self.offset = self.end = 0

    def feed(self, chunk):
        self.data += chunk
        if self.head is None:
            # Resume the terminator search where the previous chunk ended.
            index = self.data.find(b'\r\n\r\n', max(0, self.scanned - 3))
            if index < 0:
                if len(self.data) > MAX_HEADER_BYTES:
                    raise ValueError('Headers too large')
                self.scanned = len(self.data)
                return None
            self.head = parse_head(self.data[:index])
            self.offset = index + 4
        method, path, headers, size = self.head
        if len(self.data) < self.offset + size:
            return None
        self.end = self.offset + size
        with memoryview(self.data) as view:
            body = bytes(view[self.offset:self.end])
        return WebRequest(method, headers, body, path)


class WebClient:
    """One connection: read one request, dispatch it, write the reply, close.

    With a notifier factory, Qt socket notifiers drive reads and writes as soon
    as the socket is ready, and replies are written when their completion
    resolves. advance() performs the same I/O manually and enforces the idle
    timeout; the add-on calls it from a low-frequency housekeeping timer."""

    def __init__(self, sock, handler, notifier=None, on_close=None):
        self.sock, self.handler, self.onClose = sock, handler, on_close
        self.parser = RequestParser()
        self.writeBuff = b''
        self.writeOffset = 0
        self.pending = False
        self.started = time.monotonic()
        self.reader = self.writer = None
        if notifier is not None and sock is not None:
            self.reader = notifier(sock.fileno(), 'read', self.read)
            self.writer = notifier(sock.fileno(), 'write', self.flush)
            self.writer.setEnabled(False)

    def advance(self, recvSize=65536):
        if self.sock is None:
            return False
        if not self.pending and time.monotonic() - self.started > 10:
            self.close()
            return False
        if self.pending and not self.writeBuff:
            return True  # Accepted work has no implicit deadline.
        if self.writeBuff:
            self.flush()
        else:
            self.read(recvSize=recvSize)
        return self.sock is not None

    def read(self, *_, recvSize=65536):
        try:
            # Drain everything available; header and body limits bound the loop.
            while self.sock is not None and not self.pending and not self.writeBuff:
                msg = self.sock.recv(recvSize)
                if not msg:
                    self.close()
                    return
                self.started = time.monotonic()
                req = self.parser.feed(msg)
                if req is not None:
                    self.dispatch(req)
        except BlockingIOError:
            pass
        except HttpError as error:
            self.respond(_http_error_response(error.status))
        except (ValueError, UnicodeError):
            self.respond(_http_error_response('400 Bad Request'))
        except OSError:
            self.close()

    def dispatch(self, req):
        self.parser = RequestParser()
        self.pending = True
        self.setNotifier(self.reader, False)
        reply = self.handler(req)
        def finish(source):
            self.pending = False
            self.started = time.monotonic()
            if self.sock is not None:
                self.respond(source.value if not source.error else _http_error_response('500 Internal Server Error'))
        (reply if isinstance(reply, Deferred) else completed(reply)).then(finish)

    def respond(self, data):
        self.setNotifier(self.reader, False)
        self.writeBuff, self.writeOffset = data, 0
        self.flush()

    def flush(self, *_):
        if self.sock is None or not self.writeBuff:
            return
        try:
            with memoryview(self.writeBuff) as view:
                while self.writeOffset < len(view):
                    length = self.sock.send(view[self.writeOffset:])
                    if not length:
                        break
                    self.started = time.monotonic()
                    self.writeOffset += length
        except BlockingIOError:
            pass
        except OSError:
            self.close()
            return
        if self.writeOffset >= len(self.writeBuff):
            self.close()
        else:
            self.setNotifier(self.writer, True)

    @staticmethod
    def setNotifier(notifier, enabled):
        if notifier is not None:
            notifier.setEnabled(enabled)

    def close(self):
        for notifier in (self.reader, self.writer):
            if notifier is not None:
                notifier.setEnabled(False)
                # Deferred deletion: close() may run inside the notifier's own slot.
                notifier.deleteLater()
        self.reader = self.writer = None
        self.parser = RequestParser()
        self.writeBuff, self.writeOffset = b'', 0
        if self.sock is not None:
            self.sock.close()
            self.sock = None
            if self.onClose is not None:
                self.onClose(self)

    def parseRequest(self, data):
        parser = RequestParser()
        req = parser.feed(data)
        return (req, parser.end) if req is not None else (None, 0)


class WebServer:
    def __init__(self, handler, rpc_handler=None):
        self.handler, self.rpc_handler = handler, rpc_handler
        self.clients, self.sock = [], None
        self.listener = None
        self.notifier = None
        # Called on Qt's thread when the last open connection closes.
        self.onIdle = None

    def advance(self):
        """Housekeeping and fallback I/O: accept, progress and time out clients."""
        if self.sock is not None:
            self.acceptClients()
            self.advanceClients()

    def acceptClients(self, *_):
        while self.sock is not None:
            try:
                sock = self.sock.accept()[0]
            except OSError:  # Includes BlockingIOError when the backlog is empty.
                return
            sock.setblocking(False)
            client = WebClient(sock, self.handlerWrapper, self.notifier, self.removeClient)
            self.clients.append(client)
            if client.reader is not None:
                client.read()  # The request may already be queued before the notifier arms.

    def advanceClients(self):
        for client in list(self.clients):
            if not client.advance():
                self.removeClient(client)

    def removeClient(self, client):
        if client in self.clients:
            self.clients.remove(client)
            if not self.clients and self.onIdle is not None:
                self.onIdle()

    def listen(self, notifier_class=None):
        """Bind the listener. With a QSocketNotifier class, socket readiness drives
        all I/O; otherwise callers must drive advance() themselves."""
        self.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        self.sock.bind((util.setting('webBindAddress'), util.setting('webBindPort')))
        self.sock.listen(util.setting('webBacklog'))
        if notifier_class is not None:
            kinds = {'read': notifier_class.Type.Read, 'write': notifier_class.Type.Write}
            listener = self.listener = notifier_class(self.sock.fileno(), notifier_class.Type.Read)
            listener.activated.connect(self.acceptClients)
            def notifier(fileno, kind, slot):
                # Parented to the listener so Qt owns it: deleteLater() stays safe
                # even when the last Python reference disappears inside its slot.
                watcher = notifier_class(fileno, kinds[kind], listener)
                watcher.activated.connect(slot)
                return watcher
            self.notifier = notifier

    def handlerWrapper(self, req):
        allowed, origin = self.allowOrigin(req)
        def response(value, status='200 OK', empty=False):
            body = b'' if empty else json.dumps(value, allow_nan=False).encode('utf-8')
            headers = self.buildHeaders(origin, body)
            headers[0][0] = 'HTTP/1.1 ' + status
            if req.method == b'OPTIONS' and req.headers.get(b'access-control-request-private-network') == b'true':
                headers.append(['Access-Control-Allow-Private-Network', 'true'])
            return self.buildResponse(headers, body)
        if req.method == b'OPTIONS':
            # Preflight has no JSON body, so it cannot identify permission discovery.
            # Let browsers send the POST; origin admission below still protects actions.
            origin = req.headers.get(b'origin', b'').decode() or origin
            return response(None, empty=True)
        rpc = req.path == b'/api/v1'
        if req.path not in (b'/', b'/api/v1'):
            return response(None, '404 Not Found', empty=True)
        if rpc and req.method != b'POST':
            return response(None, '405 Method Not Allowed', empty=True)
        try:
            def reject_constant(value):
                raise ValueError('Non-finite JSON number')
            params = json.loads(req.body.decode('utf-8'), parse_constant=reject_constant)
            if not rpc:
                from .legacy import legacy_request
                params = legacy_request(params)
                error = jsonschema.exceptions.best_match(request_validator.iter_errors(params))
                if error is not None:
                    raise error
        except (ValueError, UnicodeError, jsonschema.ValidationError, ApiError) as error:
            if not allowed:
                return response(None, '403 Forbidden', empty=True)
            if rpc:
                return response(Dispatcher.reply(None, error=ApiError('parseError', 'Invalid JSON.')))
            if not req.body:
                return response({'apiVersion': 'AnkiConnect v.{}'.format(util.setting('apiVersion'))})
            return response(format_exception_reply(util.setting('apiVersion'), error))
        permission = isinstance(params, dict) and params.get('method' if rpc else 'action') == ('access.requestPermission' if rpc else 'requestPermission')
        if not allowed and not permission:
            return response(None, '403 Forbidden', empty=True)
        if permission:
            origin = req.headers.get(b'origin', b'').decode()
        if rpc:
            key = util.setting('apiKey')
            if key is not None and not permission and req.headers.get(b'authorization') != ('Bearer ' + key).encode():
                return response(None, '401 Unauthorized', empty=True)
            if self.rpc_handler is None:
                return response(None, '404 Not Found', empty=True)
            pending = self.rpc_handler(params, {'origin': req.headers.get(b'origin', b'').decode(), 'allowed': allowed})
            return pending.map(lambda value: response(value, '204 No Content' if value is None else '200 OK',
                                                      empty=value is None))
        if permission:
            params.setdefault('params', {}).update(allowed=allowed, origin=origin)
        value = self.handler(params)
        return value.map(response) if isinstance(value, Deferred) else response(value)

    def allowOrigin(self, req):
        origins = list(util.setting('webCorsOriginList'))
        deprecated = util.setting('webCorsOrigin')
        if deprecated:
            origins.append(deprecated)
        origin = req.headers.get(b'origin', b'').decode()
        if '*' in origins:
            return True, '*'
        allowed = not origin or origin in origins
        if 'http://localhost' in origins and origin.startswith(('http://127.0.0.1:', 'https://127.0.0.1:', 'chrome-extension://', 'moz-extension://', 'safari-web-extension://')):
            allowed = True
        if 'http://localhost' in origins and origin in ('http://127.0.0.1', 'https://127.0.0.1'):
            allowed = True
        return allowed, origin if allowed and origin else 'http://localhost'

    def buildHeaders(self, corsOrigin, body):
        return [['HTTP/1.1 200 OK', None], ['Content-Type', 'application/json'],
                ['Access-Control-Allow-Origin', corsOrigin], ['Vary', 'Origin'],
                ['Access-Control-Allow-Headers', 'Content-Type, Authorization, *'],
                ['Access-Control-Allow-Methods', 'POST, OPTIONS'], ['Connection', 'close'],
                ['Content-Length', str(len(body))]]

    def buildResponse(self, headers, body):
        return ''.join(key + (': ' + value if value is not None else '') + '\r\n' for key, value in headers).encode() + b'\r\n' + body

    def close(self):
        clients, self.clients = self.clients, []
        for client in clients:
            client.onClose = None
            client.close()
        if self.listener is not None:
            self.listener.setEnabled(False)
            self.listener.deleteLater()
            self.listener = None
        self.notifier = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None


def format_success_reply(api_version, result):
    if api_version <= 4:
        return result
    else:
        return {"result": result, "error": None}


def format_exception_reply(_api_version, exception):
    if isinstance(exception, ApiError) and exception.kind == 'collectionUnavailable':
        return {"result": None, "error": 'collection is not available'}
    return {"result": None, "error": str(exception)}


request_schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "minLength": 1},
        "version": {"type": "integer"},
        "params": {"type": "object"},
    },
    "required": ["action"],
}

# jsonschema.validate() rechecks the schema on every call. Reuse one validator
# with the same best-match error selection, so error messages are unchanged.
request_validator = jsonschema.validators.validator_for(request_schema)(request_schema)
