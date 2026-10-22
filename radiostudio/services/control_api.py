"""Authenticated HTTP API for controlling the local RadioStudio instance."""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import keyring
from PySide6.QtCore import QObject, Qt, Signal, QThread


API_PREFIX = "/api/v1"
API_CREDENTIAL_SERVICE = "RadioStudioPython.API"
API_TOKEN_ACCOUNT = "bearer-token"
API_BASIC_ACCOUNT = "basic-auth"
API_DEFAULT_PORT = 8091
API_MAX_BODY_BYTES = 1_000_000
API_DISPATCH_TIMEOUT_SECONDS = 5.0


class _ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    MAX_CONCURRENT_CLIENTS = 32

    def __init__(self, server_address, request_handler_class):
        self._request_slots = threading.BoundedSemaphore(self.MAX_CONCURRENT_CLIENTS)
        super().__init__(server_address, request_handler_class)

    def process_request(self, request, client_address):
        # Authentication does not prevent unauthenticated peers from opening
        # sockets. Bound handler threads so LAN connection floods cannot grow
        # this optional service without limit.
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        # On Windows, SO_REUSEADDR can permit another process to bind the
        # same listening port. Keep the API listener exclusive instead.
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


class APIError(Exception):
    """An expected API failure with an HTTP status and stable error code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


class APIDispatchTimeout(APIError):
    def __init__(self):
        super().__init__(HTTPStatus.GATEWAY_TIMEOUT, "command_timeout",
                         "The application did not process the command in time.")


def parse_allowed_origins(value) -> tuple[str, ...]:
    """Validate and normalize the explicit browser origins allowed by CORS."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw_origins = value.replace("\r", "\n").replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw_origins = list(value)
    else:
        raise ValueError("CORS origins must be a list or text.")

    result = []
    for raw in raw_origins:
        origin = str(raw).strip()
        if not origin:
            continue
        if len(origin) > 512 or "*" in origin:
            raise ValueError(f"Invalid CORS origin: {origin}")
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid CORS origin: {origin}") from exc
        if (parsed.scheme.lower() not in {"http", "https"} or not hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError(f"Invalid CORS origin: {origin}")
        if any(character.isspace() for character in hostname):
            raise ValueError(f"Invalid CORS origin: {origin}")
        if ":" in hostname:
            try:
                host = f"[{ipaddress.IPv6Address(hostname).compressed}]"
            except ipaddress.AddressValueError as exc:
                raise ValueError(f"Invalid CORS origin: {origin}") from exc
        else:
            try:
                host = str(ipaddress.IPv4Address(hostname))
            except ipaddress.AddressValueError:
                try:
                    ascii_host = hostname.encode("idna").decode("ascii").lower().rstrip(".")
                except UnicodeError as exc:
                    raise ValueError(f"Invalid CORS origin: {origin}") from exc
                labels = ascii_host.split(".")
                if (not ascii_host or len(ascii_host) > 253
                        or any(not label or len(label) > 63
                               or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) is None
                               for label in labels)):
                    raise ValueError(f"Invalid CORS origin: {origin}")
                host = ascii_host
        if port is not None and not (
                parsed.scheme.lower() == "http" and port == 80
                or parsed.scheme.lower() == "https" and port == 443):
            host = f"{host}:{port}"
        normalized = f"{parsed.scheme.lower()}://{host}"
        if normalized not in result:
            result.append(normalized)
    if len(result) > 32:
        raise ValueError("At most 32 CORS origins may be configured.")
    return tuple(result)


def validate_port(value, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError("Port must be an integer.")
    try:
        port = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Port must be an integer.") from exc
    minimum = 0 if allow_zero else 1
    if not minimum <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port


def get_api_token() -> str | None:
    return keyring.get_password(API_CREDENTIAL_SERVICE, API_TOKEN_ACCOUNT)


def set_api_token(token: str) -> None:
    keyring.set_password(API_CREDENTIAL_SERVICE, API_TOKEN_ACCOUNT, token)


def get_api_basic_credentials() -> tuple[str, str] | None:
    raw = keyring.get_password(API_CREDENTIAL_SERVICE, API_BASIC_ACCOUNT)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    username, password = value.get("username"), value.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        return None
    return username, password


def set_api_basic_credentials(username: str, password: str) -> None:
    if not username or not password or ":" in username:
        raise ValueError("A username and password are required.")
    keyring.set_password(
        API_CREDENTIAL_SERVICE, API_BASIC_ACCOUNT,
        json.dumps({"username": username, "password": password},
                   ensure_ascii=False, separators=(",", ":")))


def api_connection_addresses(port: int) -> tuple[str, ...]:
    """Return likely IPv4 addresses that clients on the LAN can use."""
    addresses = set()
    try:
        for result in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            address = result[4][0]
            if address != "0.0.0.0" and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    if not addresses:
        addresses.add("127.0.0.1")
    return tuple(f"http://{address}:{int(port)}{API_PREFIX}" for address in sorted(addresses))


@dataclass
class _DispatchRequest:
    operation: str
    payload: dict
    generation: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    result: object = None
    error: BaseException | None = None
    cancelled: bool = False
    started: bool = False
    completed: bool = False


class APICommandBridge(QObject):
    """Deliver API reads and mutations to the Qt thread and return their result."""

    requested = Signal(object)

    def __init__(self, handler, parent=None,
                 timeout: float = API_DISPATCH_TIMEOUT_SECONDS):
        super().__init__(parent)
        self._handler = handler
        self.timeout = max(0.1, float(timeout))
        self._state_lock = threading.Lock()
        self._active = True
        self._generation = 0
        self.requested.connect(self._run_request, Qt.ConnectionType.QueuedConnection)

    def set_active(self, active: bool) -> None:
        """Invalidate queued requests when the listener is disabled or replaced."""
        active = bool(active)
        with self._state_lock:
            if self._active != active:
                self._active = active
                self._generation += 1

    def dispatch(self, operation: str, payload: dict):
        with self._state_lock:
            if not self._active:
                raise APIError(HTTPStatus.SERVICE_UNAVAILABLE, "api_unavailable",
                               "The control API is not accepting requests.")
            generation = self._generation
        if QThread.currentThread() == self.thread():
            return self._handler(operation, payload)
        request = _DispatchRequest(str(operation), dict(payload), generation=generation)
        self.requested.emit(request)
        if not request.done.wait(self.timeout):
            wait_for_started_request = False
            with request.lock:
                if request.completed:
                    pass
                elif request.started:
                    # Once the UI has begun a mutation, wait for its result. A
                    # 504 after execution started could make a client retry a
                    # command such as "next" and skip a second track.
                    wait_for_started_request = True
                else:
                    request.cancelled = True
                    raise APIDispatchTimeout()
            if wait_for_started_request:
                request.done.wait()
        if request.error is not None:
            raise request.error
        return request.result

    def _run_request(self, request: _DispatchRequest) -> None:
        with self._state_lock:
            with request.lock:
                if request.cancelled:
                    request.completed = True
                    request.done.set()
                    return
                if not self._active or request.generation != self._generation:
                    request.error = APIError(
                        HTTPStatus.SERVICE_UNAVAILABLE, "api_unavailable",
                        "The control API was disabled or reconfigured.")
                    request.completed = True
                    request.done.set()
                    return
                request.started = True
        try:
            request.result = self._handler(request.operation, request.payload)
        except BaseException as exc:
            request.error = exc
        finally:
            with request.lock:
                request.completed = True
            request.done.set()


def openapi_document(auth_mode: str | None = None) -> dict:
    """Return the OpenAPI description served at /api/v1/openapi.json."""
    if auth_mode == "basic":
        security = [{"BasicAuth": []}]
    elif auth_mode == "token":
        security = [{"BearerAuth": []}]
    else:
        # The checked-in document describes both supported configurations;
        # the live endpoint narrows this to the mode selected in Settings.
        security = [{"BasicAuth": []}, {"BearerAuth": []}]
    def json_response(description: str, schema: dict | None = None) -> dict:
        response = {"description": description}
        if schema is not None:
            response["content"] = {"application/json": {"schema": schema}}
        return response

    error_response = {
        "description": "JSON error response",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
    options_operation = {
        "summary": "CORS preflight",
        "security": [],
        "responses": {"204": {"description": "Preflight accepted"},
                      "403": error_response, "404": error_response},
    }
    queue_item = {"$ref": "#/components/schemas/QueueItem"}
    status = {"$ref": "#/components/schemas/Status"}
    queue = {"$ref": "#/components/schemas/Queue"}
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "RadioStudio Control API",
            "version": "1.0.0",
            "description": "Local playback and queue control. Configure one authentication mode in RadioStudio Settings; the live OpenAPI document reflects that choice. HTTP does not encrypt credentials or traffic; use only on a trusted network.",
        },
        "servers": [{"url": "http://{host}:{port}/api/v1",
                      "variables": {"host": {"default": "127.0.0.1"},
                                    "port": {"default": str(API_DEFAULT_PORT)}}}],
        "paths": {
            "/status": {"get": {
                "operationId": "getStatus",
                "summary": "Read playback status",
                "security": security,
                "responses": {"200": json_response("Playback and current-track status", status),
                              "401": error_response, "403": error_response,
                              "405": error_response, "500": error_response,
                              "503": error_response, "504": error_response},
            }, "options": options_operation},
            "/queue": {"get": {
                "operationId": "getQueue",
                "summary": "Read the current queue",
                "security": security,
                "responses": {"200": json_response("Queue entries and revision", queue),
                              "401": error_response, "403": error_response,
                              "405": error_response, "500": error_response,
                              "503": error_response, "504": error_response},
            }, "options": options_operation},
            "/playback": {"post": {
                "operationId": "controlPlayback",
                "summary": "Control playback",
                "security": security,
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/PlaybackRequest"}}}},
                "responses": {"200": json_response("Updated playback status", status),
                              "400": error_response, "401": error_response,
                              "403": error_response, "405": error_response,
                              "411": error_response, "413": error_response,
                              "415": error_response, "500": error_response,
                              "503": error_response, "504": error_response},
            }, "options": options_operation},
            "/queue/order": {"put": {
                "operationId": "reorderQueue",
                "summary": "Reorder all entries in the current queue",
                "security": security,
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/QueueOrderRequest"}}}},
                "responses": {"200": json_response("Reordered queue", queue),
                              "400": error_response, "401": error_response,
                              "403": error_response, "405": error_response,
                              "409": error_response, "411": error_response,
                              "413": error_response, "415": error_response,
                              "500": error_response, "503": error_response,
                              "504": error_response},
            }, "options": options_operation},
            "/openapi.json": {"get": {
                "operationId": "getOpenApiDocument",
                "summary": "Read this API's OpenAPI document",
                "security": security,
                "responses": {"200": json_response("OpenAPI 3.0.3 document", {"type": "object"}),
                              "401": error_response, "403": error_response,
                              "405": error_response},
            }, "options": options_operation},
        },
        "components": {"securitySchemes": {
            "BasicAuth": {"type": "http", "scheme": "basic"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }, "schemas": {
            "QueueItem": {"type": "object", "required": [
                "position", "title", "artist", "album", "is_current"],
                "properties": {
                    "position": {"type": "integer", "minimum": 0},
                    "title": {"type": "string"}, "artist": {"type": "string"},
                    "album": {"type": "string"}, "is_current": {"type": "boolean"},
                }},
            "Status": {"type": "object", "required": [
                "playback_state", "position_ms", "duration_ms", "current_track",
                "queue_length", "queue_revision"],
                "properties": {
                    "playback_state": {"type": "string",
                                        "enum": ["playing", "paused", "stopped"]},
                    "position_ms": {"type": "integer", "minimum": 0},
                    "duration_ms": {"type": "integer", "minimum": 0},
                    "current_track": {"type": "object", "nullable": True,
                                       "allOf": [queue_item]},
                    "queue_length": {"type": "integer", "minimum": 0},
                    "queue_revision": {"type": "integer", "minimum": 0},
                }},
            "Queue": {"type": "object", "required": ["revision", "current_track", "items"],
                      "properties": {"revision": {"type": "integer", "minimum": 0},
                                     "current_track": {"type": "object", "nullable": True,
                                                       "allOf": [queue_item]},
                                     "items": {"type": "array", "items": queue_item}}},
            "PlaybackRequest": {"type": "object", "required": ["action"],
                "properties": {"action": {"type": "string",
                                            "enum": ["play", "pause", "stop", "next"]}}},
            "QueueOrderRequest": {"type": "object", "required": ["revision", "order"],
                "properties": {"revision": {"type": "integer", "minimum": 0},
                               "order": {"type": "array", "items": {
                                   "type": "integer", "minimum": 0},
                                   "description": "Complete permutation of the zero-based positions from GET /queue."}}},
            "Error": {"type": "object", "required": ["error"],
                "properties": {"error": {"type": "object", "required": ["code", "message"],
                    "properties": {"code": {"type": "string"},
                                   "message": {"type": "string"}}}}},
        }},
    }


class ControlAPIServer:
    """Small stdlib HTTP server; application work is delegated by callback."""

    def __init__(self, dispatch):
        self._dispatch = dispatch
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None \
            and self._thread.is_alive()

    @property
    def port(self) -> int | None:
        if self._server is None:
            return None
        return int(self._server.server_address[1])

    def start(self, *, host: str = "0.0.0.0", port: int = API_DEFAULT_PORT,
              auth_mode: str, username: str = "", password: str = "",
              token: str = "", allowed_origins=()) -> None:
        if self._server is not None:
            raise RuntimeError("The control API is already running.")
        safe_port = validate_port(port, allow_zero=True)
        mode = str(auth_mode or "").strip().lower()
        if mode not in {"basic", "token"}:
            raise ValueError("Authentication mode must be basic or token.")
        if mode == "basic" and (not username or not password or ":" in username):
            raise ValueError("Basic authentication needs a username and password.")
        if mode == "token" and not token:
            raise ValueError("Bearer authentication needs a token.")
        origins = parse_allowed_origins(allowed_origins)
        if not host or host in {"localhost", "*"}:
            raise ValueError("The listener host must be an explicit local address.")

        owner = self
        safe_username = str(username)
        safe_password = str(password)
        safe_token = str(token)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "RadioStudioAPI"
            sys_version = ""

            def setup(self):
                super().setup()
                self.connection.settimeout(10.0)

            def log_message(self, *_args):
                # Avoid logging headers, request payloads, or credential material.
                return

            def _origin(self):
                raw = self.headers.get("Origin", "").strip()
                if not raw:
                    return "", True
                try:
                    normalized = parse_allowed_origins([raw])
                except ValueError:
                    return raw, False
                return (normalized[0] if normalized else raw), bool(
                    normalized and normalized[0] in origins)

            def _cors_headers(self, origin: str) -> list[tuple[str, str]]:
                headers = [("Vary", "Origin")]
                if origin and origin in origins:
                    headers.extend((
                        ("Access-Control-Allow-Origin", origin),
                        ("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS"),
                        ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
                        ("Access-Control-Max-Age", "600"),
                    ))
                return headers

            def _send_json(self, status: int, payload: dict, *, extra_headers=(),
                           origin: str = ""):
                body = json.dumps(payload, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for name, value in self._cors_headers(origin):
                    self.send_header(name, value)
                for name, value in extra_headers:
                    self.send_header(name, value)
                self.end_headers()
                if self.command == "HEAD":
                    return
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _send_error(self, status: int, code: str, message: str,
                            *, origin: str = "", extra_headers=()):
                # Errors can be returned before a request body is consumed
                # (for example, bad auth or an unsupported content type).
                # Closing the connection prevents those unread bytes from
                # being parsed as the next HTTP/1.1 request.
                self.close_connection = True
                self._send_json(status, {"error": {"code": code, "message": message}},
                                origin=origin,
                                extra_headers=[*extra_headers, ("Connection", "close")])

            def _is_authorized(self) -> bool:
                authorization = self.headers.get("Authorization", "")
                scheme, separator, credential = authorization.partition(" ")
                if not separator:
                    return False
                if mode == "token":
                    return (scheme.lower() == "bearer" and bool(credential)
                            and hmac.compare_digest(
                                credential.strip().encode("utf-8"),
                                safe_token.encode("utf-8")))
                if scheme.lower() != "basic":
                    return False
                try:
                    decoded = base64.b64decode(credential.strip(), validate=True).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    return False
                if ":" not in decoded:
                    return False
                supplied_user, supplied_password = decoded.split(":", 1)
                return (hmac.compare_digest(supplied_user.encode("utf-8"),
                                            safe_username.encode("utf-8"))
                        and hmac.compare_digest(supplied_password.encode("utf-8"),
                                                safe_password.encode("utf-8")))

            def _authorize(self, origin: str) -> bool:
                if not self._is_authorized():
                    challenge = ("Bearer" if mode == "token"
                                 else 'Basic realm="RadioStudio API", charset="UTF-8"')
                    self._send_error(
                        HTTPStatus.UNAUTHORIZED, "unauthorized", "Authentication is required.",
                        origin=origin,
                        extra_headers=[("WWW-Authenticate", challenge)])
                    return False
                return True

            def _check_origin(self):
                origin, allowed = self._origin()
                if origin and not allowed:
                    self._send_error(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                                     "This browser origin is not allowed.")
                    return None
                return origin

            def _read_json(self, origin: str):
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required",
                                     "Content-Type must be application/json.", origin=origin)
                    return None
                length_text = self.headers.get("Content-Length")
                try:
                    length = int(length_text)
                except (TypeError, ValueError):
                    self._send_error(HTTPStatus.LENGTH_REQUIRED, "content_length_required",
                                     "Content-Length is required.", origin=origin)
                    return None
                if length < 0:
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid_content_length",
                                     "Content-Length is invalid.", origin=origin)
                    return None
                if length > API_MAX_BODY_BYTES:
                    self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large",
                                     "The request body is too large.", origin=origin)
                    return None
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid_json",
                                     "The request body must be valid UTF-8 JSON.", origin=origin)
                    return None
                if not isinstance(data, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid_json_shape",
                                     "The request body must be a JSON object.", origin=origin)
                    return None
                return data

            def _dispatch(self, operation: str, payload: dict, origin: str):
                try:
                    result = owner._dispatch(operation, payload)
                except APIError as exc:
                    self._send_error(exc.status, exc.code, exc.message, origin=origin)
                    return
                except Exception:
                    logging.getLogger("radiostudio").exception(
                        "Falha ao processar solicitação autenticada da API REST (%s)", operation)
                    self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error",
                                     "The request could not be completed.", origin=origin)
                    return
                self._send_json(HTTPStatus.OK, result if isinstance(result, dict) else {},
                                origin=origin)

            def do_OPTIONS(self):
                origin, allowed = self._origin()
                if origin and not allowed:
                    self._send_error(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                                     "This browser origin is not allowed.")
                    return
                if self.path.partition("?")[0] not in {
                        f"{API_PREFIX}/status", f"{API_PREFIX}/queue",
                        f"{API_PREFIX}/playback", f"{API_PREFIX}/queue/order",
                        f"{API_PREFIX}/openapi.json"}:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Endpoint not found.",
                                     origin=origin)
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                for name, value in self._cors_headers(origin):
                    self.send_header(name, value)
                self.end_headers()

            def do_GET(self):
                origin = self._check_origin()
                if origin is None:
                    return
                if not self._authorize(origin):
                    return
                route = self.path.partition("?")[0]
                if route == f"{API_PREFIX}/openapi.json":
                    self._send_json(HTTPStatus.OK, openapi_document(mode), origin=origin)
                elif route == f"{API_PREFIX}/status":
                    self._dispatch("status", {}, origin)
                elif route == f"{API_PREFIX}/queue":
                    self._dispatch("queue", {}, origin)
                elif route in {
                        f"{API_PREFIX}/playback", f"{API_PREFIX}/queue/order",
                        f"{API_PREFIX}/openapi.json"}:
                    self._method_not_allowed(origin)
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found",
                                     "Endpoint not found.", origin=origin)

            def do_POST(self):
                origin = self._check_origin()
                if origin is None:
                    return
                if not self._authorize(origin):
                    return
                if self.path.partition("?")[0] != f"{API_PREFIX}/playback":
                    route = self.path.partition("?")[0]
                    if route in {
                            f"{API_PREFIX}/status", f"{API_PREFIX}/queue",
                            f"{API_PREFIX}/queue/order", f"{API_PREFIX}/openapi.json"}:
                        self._method_not_allowed(origin)
                    else:
                        self._send_error(HTTPStatus.NOT_FOUND, "not_found",
                                         "Endpoint not found.", origin=origin)
                    return
                payload = self._read_json(origin)
                if payload is None:
                    return
                action = payload.get("action")
                if not isinstance(action, str) or action not in {
                        "play", "pause", "stop", "next"}:
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid_action",
                                     "Action must be play, pause, stop, or next.", origin=origin)
                    return
                self._dispatch("playback", {"action": action}, origin)

            def do_PUT(self):
                origin = self._check_origin()
                if origin is None:
                    return
                if not self._authorize(origin):
                    return
                if self.path.partition("?")[0] != f"{API_PREFIX}/queue/order":
                    route = self.path.partition("?")[0]
                    if route in {
                            f"{API_PREFIX}/status", f"{API_PREFIX}/queue",
                            f"{API_PREFIX}/playback", f"{API_PREFIX}/openapi.json"}:
                        self._method_not_allowed(origin)
                    else:
                        self._send_error(HTTPStatus.NOT_FOUND, "not_found",
                                         "Endpoint not found.", origin=origin)
                    return
                payload = self._read_json(origin)
                if payload is None:
                    return
                revision, order = payload.get("revision"), payload.get("order")
                if type(revision) is not int:
                    self._send_error(HTTPStatus.BAD_REQUEST, "invalid_order",
                                     "A queue revision is required.",
                                     origin=origin)
                    return
                if (not isinstance(order, list)
                        or any(type(index) is not int or index < 0 for index in order)):
                    self._send_error(HTTPStatus.CONFLICT, "invalid_order",
                                     "Order must be a sequence of non-negative queue positions.",
                                     origin=origin)
                    return
                self._dispatch("reorder_queue", {"revision": revision, "order": order}, origin)

            def _method_not_allowed(self, origin: str = ""):
                route = self.path.partition("?")[0]
                routes = {
                    f"{API_PREFIX}/status": "GET, OPTIONS",
                    f"{API_PREFIX}/queue": "GET, OPTIONS",
                    f"{API_PREFIX}/playback": "POST, OPTIONS",
                    f"{API_PREFIX}/queue/order": "PUT, OPTIONS",
                    f"{API_PREFIX}/openapi.json": "GET, OPTIONS",
                }
                allowed_methods = routes.get(route)
                if allowed_methods is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "not_found",
                                     "Endpoint not found.", origin=origin)
                    return
                self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed",
                                 "Method is not allowed for this endpoint.",
                                 origin=origin,
                                 extra_headers=[("Allow", allowed_methods)])

            def _reject_unsupported_method(self):
                origin = self._check_origin()
                if origin is None or not self._authorize(origin):
                    return
                self._method_not_allowed(origin)

            do_DELETE = _reject_unsupported_method
            do_PATCH = _reject_unsupported_method
            do_HEAD = _reject_unsupported_method

        try:
            server = _ControlHTTPServer((host, safe_port), Handler)
        except OSError as exc:
            raise OSError(f"Could not bind the RadioStudio API to port {safe_port}.") from exc
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.1},
                                  name="RadioStudio-ControlAPI", daemon=True)
        try:
            thread.start()
        except Exception:
            server.server_close()
            raise
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
