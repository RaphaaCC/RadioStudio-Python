"""Live PCM mix, MP3 encoding, Icecast/SHOUTcast publishing and local listeners."""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import socket
import ssl
import subprocess
import threading
import time
import uuid
from array import array
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import imageio_ffmpeg
import keyring
from ..config import APP_DIR


SAMPLE_RATE = 44100
CHANNELS = 2
FRAME_MS = 20
PCM_BYTES_PER_FRAME = SAMPLE_RATE * CHANNELS * 2 * FRAME_MS // 1000
SILENCE_PCM_FRAME = bytes(PCM_BYTES_PER_FRAME)
ENCODED_CHUNK_BYTES = 1024
ENCODED_QUEUE_MAX_MS = 2000
OUTPUT_RECONNECT_DELAY = 2.0
OUTPUT_RECONNECT_MAX_DELAY = 30.0
CREDENTIAL_SERVICE = "RadioStudioPython.Broadcast"
SETTINGS_DIR = APP_DIR


def reconnect_delay(failed_attempts: int) -> float:
    """Bound retries so an unavailable server is not hammered continuously."""
    try:
        attempts = max(1, int(failed_attempts))
    except (TypeError, ValueError, OverflowError):
        attempts = 1
    exponent = min(attempts - 1, 8)
    return min(OUTPUT_RECONNECT_MAX_DELAY, OUTPUT_RECONNECT_DELAY * (2 ** exponent))


ENCODER_QUALITY_PRESETS = (
    ("Máxima (mais lenta)", 0), ("Alta", 2), ("Equilibrada", 5), ("Rápida", 7),
)


def encoded_queue_capacity(bitrate: int) -> int:
    """Keep the encoded backlog near two seconds at every configured bitrate."""
    safe_bitrate = max(1, int(bitrate))
    return max(1, int(ENCODED_QUEUE_MAX_MS * safe_bitrate /
                      (ENCODED_CHUNK_BYTES * 8)))


class PortInUseError(OSError):
    """The embedded listener cannot own the requested port."""


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def new_profile(kind: str = "icecast") -> dict:
    return {"id": uuid.uuid4().hex, "kind": kind, "name": kind.title(),
            "host": "", "port": 8000, "mount": "/live.mp3", "user": "source",
            "bitrate": 192, "encoder_quality": 2, "enabled": True, "station": "",
            "genre": "", "url": "", "description": "", "tls": False,
            "metadata_user": "admin", "http_port": 0, "stream_id": 1}


def set_password(profile_id: str, password: str) -> None:
    keyring.set_password(CREDENTIAL_SERVICE, profile_id, password)


def get_password(profile_id: str) -> str | None:
    return keyring.get_password(CREDENTIAL_SERVICE, profile_id)


def delete_password(profile_id: str) -> None:
    try:
        keyring.delete_password(CREDENTIAL_SERVICE, profile_id)
    except keyring.errors.PasswordDeleteError:
        pass


class PCMBuffer:
    """A bounded FIFO for one audio source."""

    def __init__(self):
        self.chunks = deque()
        self.length = 0
        self.lock = threading.Lock()
        self.capacity_bytes = SAMPLE_RATE * CHANNELS * 2 * 2
        self.min_prebuffer_bytes = PCM_BYTES_PER_FRAME * 2
        self.primed = False
        self.warmup_silence_frames = 0
        self.zero_filled_bytes = 0
        self.underflow_frames = 0
        self.dropped_bytes = 0
        self.high_watermark_bytes = 0

    def append(self, data: bytes, gain: float | None = None):
        if not data:
            return
        data = bytes(data)
        # Keep only complete stereo int16 frames, including when one unusually
        # large callback exceeds the entire per-source buffer capacity.
        frame_bytes = CHANNELS * 2
        data = data[:len(data) - (len(data) % frame_bytes)]
        if not data:
            return
        with self.lock:
            if len(data) > self.capacity_bytes:
                self.dropped_bytes += len(data) - self.capacity_bytes
                data = data[-self.capacity_bytes:]
            self.chunks.append((data, gain))
            self.length += len(data)
            while self.length > self.capacity_bytes and self.chunks:
                excess = self.length - self.capacity_bytes
                oldest, oldest_gain = self.chunks[0]
                if len(oldest) <= excess:
                    self.chunks.popleft()
                    self.length -= len(oldest)
                    self.dropped_bytes += len(oldest)
                else:
                    self.chunks[0] = (oldest[excess:], oldest_gain)
                    self.length -= excess
                    self.dropped_bytes += excess
            self.high_watermark_bytes = max(self.high_watermark_bytes, self.length)

    def clear(self, *, preserve_primed: bool = False):
        """Discard queued samples, optionally preserving steady-state startup."""
        with self.lock:
            self.chunks.clear()
            self.length = 0
            if not preserve_primed:
                self.primed = False

    def take_if_empty(self, length: int):
        """Return shared silence for an empty frame without allocating a buffer."""
        length = max(0, int(length))
        if not length:
            return b""
        with self.lock:
            if self.length:
                return None
            if not self.primed and self.length < self.min_prebuffer_bytes:
                self.warmup_silence_frames += 1
            else:
                self.primed = True
                self.zero_filled_bytes += length
                self.underflow_frames += 1
        return SILENCE_PCM_FRAME if length == PCM_BYTES_PER_FRAME else bytes(length)

    def take_gained(self, length: int) -> list[tuple[bytes, float | None]]:
        """Read PCM while preserving the gain captured for each queued block."""
        length = max(0, int(length))
        if not length:
            return []
        result = []
        collected = 0
        with self.lock:
            if not self.primed and self.length < self.min_prebuffer_bytes:
                self.warmup_silence_frames += 1
                return [(b"\0" * length, None)]
            self.primed = True
            while self.chunks and collected < length:
                chunk, gain = self.chunks.popleft()
                needed = length - collected
                count = min(len(chunk), needed)
                result.append((chunk[:count], gain))
                collected += count
                self.length -= count
                if len(chunk) > needed:
                    remainder = chunk[needed:]
                    self.chunks.appendleft((remainder, gain))
                    break
            if collected < length:
                missing = length - collected
                self.zero_filled_bytes += missing
                self.underflow_frames += 1
                result.append((b"\0" * missing, None))
        return result

    def take(self, length: int) -> bytes:
        """Compatibility read for callers that do not need per-block gains."""
        return b"".join(data for data, _gain in self.take_gained(length))

    def diagnostics_snapshot(self):
        with self.lock:
            return {"buffer_ms": round(self.length * 1000 / (SAMPLE_RATE * CHANNELS * 2)),
                    "prebuffer_ms": round(self.min_prebuffer_bytes * 1000 /
                                           (SAMPLE_RATE * CHANNELS * 2)),
                    "primed": self.primed,
                    "warmup_silence_frames": self.warmup_silence_frames,
                    "high_watermark_ms": round(self.high_watermark_bytes * 1000 /
                                                (SAMPLE_RATE * CHANNELS * 2)),
                    "underflow_frames": self.underflow_frames,
                    "zero_filled_bytes": self.zero_filled_bytes,
                    "dropped_bytes": self.dropped_bytes}


class LocalStreamServer:
    def __init__(self, host: str, port: int, max_listeners: int, station: str,
                 genre: str = "", url: str = "", description: str = "",
                 send_cover_art: bool = False):
        self.host, self.port = host, port
        self.max_listeners = max_listeners
        self.station = station
        self.genre = genre
        self.url = url
        self.description = description
        self.send_cover_art = bool(send_cover_art)
        self.cover_art_data: bytes | None = None
        self.cover_art_mime_type = ""
        self.cover_lock = threading.Lock()
        self.diagnostics_provider = None
        self.title = ""
        self.tls = False
        self.clients: set[queue.Queue] = set()
        # Keep each streaming socket reachable so a listener that stops
        # reading cannot leave a request-handler thread blocked forever.
        self.client_sockets: dict[queue.Queue, socket.socket] = {}
        self.lock = threading.Lock()
        self.server = None
        self.thread = None

    @property
    def listeners(self) -> int:
        with self.lock:
            return len(self.clients)

    def start(self):
        owner = self

        def icy_header(value):
            return str(value).replace("\r", " ").replace("\n", " ").encode(
                "latin-1", "replace").decode("latin-1")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args):
                pass

            def do_GET(self):
                route = self.path.partition("?")[0]
                if route == "/status.json":
                    diagnostics = {}
                    if callable(owner.diagnostics_provider):
                        try:
                            diagnostics = owner.diagnostics_provider()
                        except Exception:
                            logging.getLogger("radiostudio").exception(
                                "Falha ao coletar diagnóstico da transmissão")
                    with owner.cover_lock:
                        cover_available = bool(
                            owner.send_cover_art and owner.cover_art_data)
                        cover_mime_type = owner.cover_art_mime_type if cover_available else ""
                    body = json.dumps({"station": owner.station, "genre": owner.genre,
                                       "url": owner.url, "description": owner.description,
                                       "title": owner.title,
                                       "listeners": owner.listeners,
                                       "cover_url": "/cover" if cover_available else None,
                                       "cover_mime_type": cover_mime_type,
                                       "audio_diagnostics": diagnostics},
                                      ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if route == "/cover":
                    with owner.cover_lock:
                        cover_data = owner.cover_art_data if owner.send_cover_art else None
                        cover_mime_type = owner.cover_art_mime_type
                    if not cover_data:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", cover_mime_type or "application/octet-stream")
                    self.send_header("Content-Length", str(len(cover_data)))
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.end_headers()
                    self.wfile.write(cover_data)
                    return
                if route not in ("/", "/live.mp3"):
                    self.send_error(404)
                    return
                client = queue.Queue(maxsize=80)
                with owner.lock:
                    listener_limit_reached = len(owner.clients) >= owner.max_listeners
                    if not listener_limit_reached:
                        owner.clients.add(client)
                        owner.client_sockets[client] = self.connection
                if listener_limit_reached:
                    # Writing an HTTP error can block on a slow client. Do not
                    # hold the mixer-facing client lock during socket I/O.
                    self.send_error(503, "Limite de ouvintes atingido")
                    return
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("icy-name", icy_header(owner.station))
                    self.send_header("icy-genre", icy_header(owner.genre))
                    self.send_header("icy-url", icy_header(owner.url))
                    self.send_header("icy-description", icy_header(owner.description))
                    metadata = self.headers.get("Icy-MetaData") == "1"
                    if metadata:
                        self.send_header("icy-metaint", "8192")
                    self.end_headers()
                    until_metadata = 8192
                    while owner.server is not None:
                        try:
                            data = client.get(timeout=1)
                        except queue.Empty:
                            with owner.lock:
                                if client not in owner.clients:
                                    break
                            continue
                        if metadata:
                            while data:
                                part, data = data[:until_metadata], data[until_metadata:]
                                self.wfile.write(part)
                                until_metadata -= len(part)
                                if until_metadata == 0:
                                    title = owner.title.replace("'", " ")
                                    payload = f"StreamTitle='{title}';".encode("utf-8")[:4080]
                                    length = (len(payload) + 15) // 16
                                    self.wfile.write(bytes([length]) + payload.ljust(length * 16, b"\0"))
                                    until_metadata = 8192
                        else:
                            self.wfile.write(data)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    owner._remove_listener(client)

        try:
            self.server = ExclusiveHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or exc.errno == 98:
                raise PortInUseError(f"Porta {self.port} já está em uso") from exc
            raise
        self.server.daemon_threads = True
        certificate = SETTINGS_DIR / "server_cert.pem"
        private_key = SETTINGS_DIR / "server_key.pem"
        try:
            if certificate.is_file() and private_key.is_file():
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(certificate), str(private_key))
                self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
                self.tls = True
        except Exception:
            self.server.server_close()
            self.server = None
            raise
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        try:
            thread.start()
        except Exception:
            server, self.server = self.server, None
            self.thread = None
            if server:
                server.server_close()
            raise
        self.thread = thread

    def set_cover_art(self, data: bytes | None, mime_type: str = ""):
        """Atomically replace or clear the optional cover exposed to listeners."""
        with self.cover_lock:
            safe_data = bytes(data) if self.send_cover_art and data else None
            safe_mime_type = str(mime_type or "") if safe_data else ""
            self.cover_art_data = safe_data
            self.cover_art_mime_type = safe_mime_type

    def set_cover_art_enabled(self, enabled: bool):
        with self.cover_lock:
            self.send_cover_art = bool(enabled)
            if not self.send_cover_art:
                self.cover_art_data = None
                self.cover_art_mime_type = ""

    def publish(self, data: bytes):
        stalled_sockets = []
        with self.lock:
            for client in tuple(self.clients):
                try:
                    client.put_nowait(data)
                except queue.Full:
                    self.clients.discard(client)
                    connection = self.client_sockets.pop(client, None)
                    if connection is not None:
                        stalled_sockets.append(connection)
        for connection in stalled_sockets:
            self._close_listener_socket(connection)
            logging.getLogger("radiostudio").warning(
                "Ouvinte local desconectado por não acompanhar o fluxo de áudio")

    def _remove_listener(self, client: queue.Queue):
        with self.lock:
            self.clients.discard(client)
            return self.client_sockets.pop(client, None)

    @staticmethod
    def _close_listener_socket(connection: socket.socket):
        # shutdown() interrupts a blocked write; close() also releases the
        # descriptor promptly if the handler is waiting on the socket.
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            connection.close()
        except OSError:
            pass

    def stop(self):
        server, self.server = self.server, None
        thread, self.thread = self.thread, None
        with self.lock:
            active_sockets = tuple(self.client_sockets.values())
            self.client_sockets.clear()
            self.clients.clear()
        for connection in active_sockets:
            self._close_listener_socket(connection)
        if server:
            if thread and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2)


class RemoteSink:
    def __init__(self, profile: dict, password: str):
        self.profile, self.password = profile, password
        self.sock = None
        self._socket_lock = threading.Lock()
        self._connection_generation = 0
        self._connecting = False

    @staticmethod
    def _abort_socket(sock) -> None:
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _track_connect_socket(self, generation: int, sock) -> bool:
        with self._socket_lock:
            if generation != self._connection_generation:
                return False
            self.sock = sock
            return True

    def _finish_failed_connect(self, generation: int, sock) -> None:
        with self._socket_lock:
            if generation == self._connection_generation:
                self._connecting = False
                if self.sock is sock:
                    self.sock = None
        self._abort_socket(sock)

    def connect(self):
        profile = self.profile
        host = str(profile.get("host") or "").strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        port = int(profile["port"])
        with self._socket_lock:
            self._connection_generation += 1
            generation = self._connection_generation
            self._connecting = True
            previous_sock, self.sock = self.sock, None
        self._abort_socket(previous_sock)
        sock = None
        stage = "conectar ao servidor"
        try:
            sock = socket.create_connection((host, port), timeout=8)
            if not self._track_connect_socket(generation, sock):
                raise ConnectionError("Tentativa de conexão cancelada")
            if profile.get("tls"):
                stage = "negociar TLS/SSL"
                secure_sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=host)
                sock = secure_sock
                if not self._track_connect_socket(generation, sock):
                    raise ConnectionError("Tentativa de conexão cancelada")
            sock.settimeout(8)
            if profile["kind"] == "icecast":
                stage = "enviar a solicitação ao Icecast"
                mount = "/" + profile["mount"].lstrip("/")
                auth = base64.b64encode(f"{profile.get('user') or 'source'}:{self.password}".encode()).decode()
                host_header = f"[{host}]" if ":" in host else host
                clean_header = lambda value: str(value or "").replace("\r", " ").replace("\n", " ").strip()
                headers = [f"PUT {quote(mount, safe='/')} HTTP/1.1",
                           f"Host: {host_header}:{port}",
                           f"Authorization: Basic {auth}", "Content-Type: audio/mpeg",
                           "Expect: 100-continue", "Connection: keep-alive",
                           f"Ice-Name: {clean_header(profile.get('station'))}",
                           f"Ice-Genre: {clean_header(profile.get('genre'))}",
                           f"Ice-URL: {clean_header(profile.get('url'))}",
                           f"Ice-Description: {clean_header(profile.get('description'))}", "", ""]
                sock.sendall("\r\n".join(headers).encode("utf-8"))
                response = b""
                accepted = False
                stage = "aguardar autenticação do Icecast"
                while len(response) < 8192:
                    while b"\r\n\r\n" not in response and len(response) < 8192:
                        chunk = sock.recv(1024)
                        if not chunk:
                            raise ConnectionError("Servidor encerrou a conexão")
                        response += chunk
                    if b"\r\n\r\n" not in response:
                        break
                    header, _, response = response.partition(b"\r\n\r\n")
                    status = header.split(b"\r\n", 1)[0]
                    parts = status.split()
                    try:
                        status_code = int(parts[1])
                    except (IndexError, ValueError):
                        raise ConnectionError(status.decode("latin-1", "replace"))
                    # Icecast 2.4.x does not accept chunked PUT bodies. The
                    # source protocol used by FFmpeg sends Expect: 100-continue
                    # and then raw MP3 bytes, without HTTP chunk framing.
                    if status_code == 100 or 200 <= status_code < 300:
                        accepted = True
                        break
                    if 100 <= status_code < 200:
                        continue
                    raise ConnectionError(status.decode("latin-1", "replace"))
                if not accepted:
                    raise ConnectionError("Resposta HTTP incompleta do Icecast")
            elif profile["kind"] == "shoutcast":
                # DNAS >=2.4.7 maps legacy sources to a stream with password:#SID.
                stream_id = int(profile.get("stream_id", 1))
                source_password = self.password if stream_id == 1 else f"{self.password}:#{stream_id}"
                stage = "enviar a senha de origem SHOUTcast"
                sock.sendall((source_password + "\n").encode("utf-8"))
                response = bytearray()
                stage = "aguardar autenticação do SHOUTcast"
                while len(response) < 1024 and not response.endswith(b"\n"):
                    chunk = sock.recv(1)
                    if not chunk:
                        raise ConnectionError("Servidor encerrou a conexão durante a autenticação")
                    response.extend(chunk)
                response = bytes(response)
                if not response.startswith((b"OK", b"ICY")):
                    raise ConnectionError(response.decode("latin-1", "replace").strip())
                headers = [f"icy-name:{profile.get('station', '')}",
                           f"icy-genre:{profile.get('genre', '')}",
                           f"icy-url:{profile.get('url', '')}",
                           f"icy-br:{profile.get('bitrate', 128)}", "icy-pub:0", ""]
                stage = "enviar os metadados SHOUTcast"
                sock.sendall(("\n".join(headers) + "\n").encode("utf-8"))
            else:
                raise ValueError("Protocolo de transmissão desconhecido")
            sock.settimeout(10)
            with self._socket_lock:
                if (generation != self._connection_generation or self.sock is not sock
                        or not self._connecting):
                    raise ConnectionError("Tentativa de conexão cancelada")
                self._connecting = False
        except TimeoutError as exc:
            self._finish_failed_connect(generation, sock)
            logging.getLogger("radiostudio").warning(
                "Tempo esgotado ao %s para o perfil de transmissão %s",
                stage, profile.get("name", "sem nome"))
            raise TimeoutError(
                "Tempo esgotado ao conectar ao servidor de transmissão.") from exc
        except Exception:
            self._finish_failed_connect(generation, sock)
            raise

    def cancel_connect(self) -> bool:
        """Interrupt a pending handshake without affecting an active stream."""
        with self._socket_lock:
            if not self._connecting:
                return False
            self._connection_generation += 1
            self._connecting = False
            sock, self.sock = self.sock, None
        self._abort_socket(sock)
        return True

    def publish(self, data: bytes):
        with self._socket_lock:
            sock = self.sock
        if sock is None:
            raise ConnectionError("Conexão de transmissão encerrada")
        if self.profile["kind"] == "icecast":
            sock.sendall(data)
        else:
            sock.sendall(data)

    def close(self, graceful: bool = True):
        with self._socket_lock:
            was_connecting = self._connecting
            self._connection_generation += 1
            self._connecting = False
            sock, self.sock = self.sock, None
        if sock:
            if was_connecting or not graceful:
                self._abort_socket(sock)
                return
            if self.profile["kind"] == "icecast":
                try:
                    # The request body is a live, unframed MP3 stream.
                    # Half-close it cleanly; there is no chunk terminator.
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    graceful = False
            if not graceful:
                self._abort_socket(sock)
                return
            try:
                sock.close()
            except OSError:
                pass

    def listener_count(self) -> int | None:
        profile = self.profile
        port = int(profile.get("http_port") or profile["port"])
        scheme = "https" if profile.get("tls") else "http"
        base = f"{scheme}://{profile['host']}:{port}"
        if profile["kind"] == "icecast":
            url = f"{base}/status-json.xsl"
        else:
            url = f"{base}/stats?{urlencode({'sid': profile.get('stream_id', 1), 'json': 1})}"
        try:
            with urlopen(url, timeout=4) as response:
                data = json.load(response)
            if profile["kind"] == "icecast":
                sources = data.get("icestats", {}).get("source", [])
                if isinstance(sources, dict):
                    sources = [sources]
                mount = "/" + profile["mount"].lstrip("/")
                source = next((item for item in sources if item.get("listenurl", "").endswith(mount)), None)
                return int(source["listeners"]) if source else None
            return int(data.get("currentlisteners", 0))
        except (OSError, ValueError, TypeError, KeyError):
            return None


class EncoderOutput:
    def __init__(self, name: str, bitrate: int, destination, status_callback=None,
                 encoder_quality: int = 2):
        self.name, self.bitrate = name, bitrate
        try:
            self.encoder_quality = min(9, max(0, int(encoder_quality)))
        except (TypeError, ValueError, OverflowError):
            self.encoder_quality = 2
        self.destination = destination
        self.profile_id = destination.profile["id"] if isinstance(destination, RemoteSink) else None
        self.status = "Parado"
        self.error = ""
        self.queue = queue.Queue(maxsize=120)
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.status_callback = status_callback
        self.listeners = None
        self.dropped_pcm_frames = 0
        self.max_pcm_queue_depth = 0
        self.network_queue = queue.Queue(maxsize=encoded_queue_capacity(bitrate))
        self.max_network_queue_depth = 0
        self.dropped_encoded_chunks = 0
        self.max_publish_ms = 0.0
        self.slow_publish_count = 0
        self.connection_failed = threading.Event()
        self.stats_thread = None
        self.reader_thread = None
        self.writer_thread = None

    def _status(self, status: str, error: str = ""):
        self.status, self.error = status, error
        if self.status_callback:
            self.status_callback()

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        self.thread = thread
        if isinstance(self.destination, RemoteSink):
            stats_thread = threading.Thread(target=self._poll_listeners, daemon=True)
            try:
                stats_thread.start()
                self.stats_thread = stats_thread
            except RuntimeError as exc:
                self.error = f"Consulta de ouvintes indisponível: {exc}"

    def _poll_listeners(self):
        while not self.stop_event.is_set():
            if self.status == "Ativo":
                try:
                    self.listeners = self.destination.listener_count()
                except Exception as exc:
                    self.error = str(exc)
            self.stop_event.wait(15)

    def feed(self, data: bytes):
        if self.stop_event.is_set() or not data:
            return
        # Do not build a stale PCM backlog while a destination is connecting
        # or reconnecting. Start from live audio once the encoder is ready.
        if self.status != "Ativo" or self.connection_failed.is_set():
            self.dropped_pcm_frames += 1
            return
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            removed_oldest = False
            try:
                self.queue.get_nowait()
                removed_oldest = True
            except queue.Empty:
                # The encoder may have consumed the item after put_nowait()
                # reported Full. Retry instead of silently losing this frame.
                pass
            try:
                self.queue.put_nowait(data)
                if removed_oldest:
                    self.dropped_pcm_frames += 1
            except queue.Full:
                # Another producer filled the gap; keep the mixer alive and
                # account for each frame that could not be retained.
                self.dropped_pcm_frames += 1 + int(removed_oldest)
        self.max_pcm_queue_depth = max(self.max_pcm_queue_depth, self.queue.qsize())

    def _discard_queued_pcm(self):
        discarded = 0
        while True:
            try:
                self.queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        self.dropped_pcm_frames += discarded

    def _discard_queued_encoded(self):
        discarded = 0
        while True:
            try:
                self.network_queue.get_nowait()
                discarded += 1
            except queue.Empty:
                break
        self.dropped_encoded_chunks += discarded

    def _signal_connection_failure(self, message: str, process=None):
        self.error = str(message)
        self.connection_failed.set()
        # The mixer thread writes PCM to FFmpeg's stdin. If FFmpeg is blocked
        # because its stdout consumer stopped draining, setting the event alone
        # cannot wake that blocking write. Terminating this exact encoder does.
        process = process if process is not None else self.process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except (OSError, subprocess.SubprocessError):
                logging.getLogger("radiostudio").debug(
                    "Falha ao interromper encoder após erro de saída", exc_info=True)

    def _enqueue_encoded(self, data: bytes, network_queue=None, process=None) -> bool:
        target_queue = self.network_queue if network_queue is None else network_queue
        try:
            target_queue.put_nowait(data)
        except queue.Full:
            self.dropped_encoded_chunks += 1
            self._signal_connection_failure(
                "A rede não acompanhou o encoder; reconectando para evitar atraso acumulado.",
                process)
            return False
        self.max_network_queue_depth = max(
            self.max_network_queue_depth, target_queue.qsize())
        return True

    def _run(self):
        first_attempt = True
        failed_attempts = 0
        while not self.stop_event.is_set():
            attempt_started = time.monotonic()
            if not first_attempt:
                self._discard_queued_pcm()
            self.connection_failed.clear()
            # Keep the last failure visible while the next attempt is in
            # progress; otherwise the cause disappears during a long timeout.
            retry_error = "" if first_attempt else self.error
            self._status("Conectando" if first_attempt else "Reconectando",
                         retry_error)
            try:
                self.destination.connect() if isinstance(self.destination, RemoteSink) else None
                if self.stop_event.is_set():
                    break
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                       # Input format, rate and channels are fixed PCM; probing
                       # for several seconds only delays the first live MP3.
                       "-probesize", "32", "-analyzeduration", "0",
                       "-f", "s16le",
                       "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
                       "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k",
                       "-compression_level", str(self.encoder_quality), "-flush_packets", "1",
                       "-id3v2_version", "0", "-write_xing", "0", "-f", "mp3", "pipe:1"]
                self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if self.stop_event.is_set():
                    break
                self.network_queue = queue.Queue(maxsize=encoded_queue_capacity(self.bitrate))
                self.reader_thread = threading.Thread(
                    target=self._read_encoded, args=(self.process, self.network_queue), daemon=True)
                self.writer_thread = threading.Thread(
                    target=self._publish_encoded, args=(self.process, self.network_queue), daemon=True)
                self.reader_thread.start()
                self.writer_thread.start()
                self._status("Ativo")
                while not self.stop_event.is_set() and not self.connection_failed.is_set():
                    try:
                        data = self.queue.get(timeout=0.2)
                    except queue.Empty:
                        # A socket or encoder-reader failure terminates this
                        # FFmpeg process from another thread. Prefer that
                        # original failure over reporting its expected exit.
                        if self.connection_failed.is_set():
                            raise ConnectionError(self.error or "Conexão encerrada")
                        if self.process.poll() is not None:
                            raise RuntimeError("Codificador MP3 encerrou")
                        continue
                    self.process.stdin.write(data)
                    self.process.stdin.flush()
                if self.connection_failed.is_set():
                    raise ConnectionError(self.error or "Conexão encerrada")
            except Exception as exc:
                if not self.stop_event.is_set():
                    error = (self.error if self.connection_failed.is_set() and self.error
                             else str(exc))
                    self._status("Reconectando", error)
            finally:
                self._close_connection()
            first_attempt = False
            if self.stop_event.is_set():
                break
            if time.monotonic() - attempt_started >= 30.0:
                failed_attempts = 0
            else:
                failed_attempts += 1
            self.stop_event.wait(reconnect_delay(failed_attempts))
        self._status("Parado")

    def _close_connection(self):
        process, self.process = self.process, None
        if process:
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                if process.stdin:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
        writer = self.writer_thread
        if writer and writer.is_alive() and writer is not threading.current_thread():
            writer.join(timeout=0.25)
        if isinstance(self.destination, RemoteSink):
            self.destination.close(graceful=not (writer and writer.is_alive()))
        for thread in (self.reader_thread, self.writer_thread):
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=0.5)
        if process:
            for stream in (process.stdout, process.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
        self._discard_queued_encoded()
        self.reader_thread = None
        self.writer_thread = None

    def _read_encoded(self, process, network_queue):
        try:
            while not self.stop_event.is_set():
                data = process.stdout.read(ENCODED_CHUNK_BYTES)
                if not data:
                    break
                if self.connection_failed.is_set():
                    break
                if not self._enqueue_encoded(data, network_queue, process):
                    break
        except Exception as exc:
            if not self.stop_event.is_set():
                self._signal_connection_failure(exc, process)

    def _publish_encoded(self, process, network_queue):
        while not self.stop_event.is_set() and not self.connection_failed.is_set():
            try:
                data = network_queue.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    return
                continue
            try:
                started = time.monotonic()
                self.destination.publish(data)
                elapsed_ms = (time.monotonic() - started) * 1000
                self.max_publish_ms = max(self.max_publish_ms, elapsed_ms)
                if elapsed_ms >= 100:
                    self.slow_publish_count += 1
            except Exception as exc:
                self.dropped_encoded_chunks += 1
                if not self.stop_event.is_set():
                    self._signal_connection_failure(exc, process)
                return

    def stop(self):
        self.stop_event.set()
        if isinstance(self.destination, RemoteSink):
            self.destination.cancel_connect()
        process = self.process
        if process:
            try:
                if process.poll() is None:
                    process.terminate()
            except OSError:
                pass


class BroadcastManager:
    def __init__(self):
        self.sources: dict[str, tuple[PCMBuffer, float]] = {}
        self.sources_lock = threading.Lock()
        self.outputs: list[EncoderOutput] = []
        self.local_server = None
        self.mixer_thread = None
        self.stop_event = threading.Event()
        self.title = ""
        self.master_gain = 1.0
        self.cover_art_data: bytes | None = None
        self.cover_art_mime_type = ""
        self.profiles = []
        self.startup_errors: dict[str, str] = {}
        self._metadata_lock = threading.Lock()
        self._metadata_pending: dict[str, tuple[dict, str]] = {}
        self._metadata_active: set[str] = set()
        self.metadata_update_mode = "current"
        self.metadata_update_interval_seconds = 30
        self._metadata_schedule_changed = threading.Event()
        self._metadata_stop_event = threading.Event()
        self._metadata_scheduler_thread = None

    @property
    def running(self) -> bool:
        return bool(self.outputs)

    @property
    def recording(self):
        return getattr(self, "recording_session", None)

    @property
    def recording_active(self) -> bool:
        session = self.recording
        return bool(session and session.active)

    @property
    def audio_bus_active(self) -> bool:
        thread = self.mixer_thread
        return bool((self.running or self.recording_active) and thread
                    and thread.is_alive() and not self.stop_event.is_set())

    def _ensure_mixer(self):
        self.stop_event.clear()
        if not self.mixer_thread or not self.mixer_thread.is_alive():
            self.mixer_thread = threading.Thread(target=self._mix_loop, daemon=True,
                                                 name="RadioStudioAudioMixer")
            self.mixer_thread.start()

    def start_recording(self, directory, format_name="mp3", bitrate=192):
        """Record the final limited program bus without enabling transmission."""
        session = self.recording
        if session and session.busy:
            return None
        from .recording import RecordingSession

        session = RecordingSession(directory, format_name, bitrate)
        self.recording_session = session
        session.start()
        try:
            self._ensure_mixer()
        except Exception:
            session.stop()
            self.recording_session = None
            raise
        return session.session_id

    def pause_recording(self) -> bool:
        session = self.recording
        return bool(session and session.pause())

    def resume_recording(self) -> bool:
        session = self.recording
        return bool(session and session.resume())

    def stop_recording(self, session_id: str | None = None, *, wait: bool = False) -> bool:
        session = self.recording
        if not session or (session_id is not None and session.session_id != session_id):
            return False
        if wait:
            stopped = session.stop()
        else:
            stopped = session.request_stop()
        self._stop_mixer_if_idle()
        return stopped

    def _stop_mixer_if_idle(self):
        if self.running or self.recording_active:
            return
        self.stop_event.set()
        thread = self.mixer_thread
        with self.sources_lock:
            self.sources.clear()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        # The mixer can request its own shutdown after an asynchronous
        # recording failure. Keep its reference until it actually exits: a
        # new recording may clear stop_event in the meantime, and _ensure_mixer
        # must reuse this live thread instead of launching a second mixer.
        if (thread is None or not thread.is_alive()) and self.mixer_thread is thread:
            self.mixer_thread = None

    def _publish_mix_frame(self, pcm: bytes):
        self._feed_outputs(pcm)
        session = self.recording
        if session and session.active:
            session.submit(pcm)
        elif session and not session.busy and not self.running:
            # An asynchronous encoder/disk failure must not leave an idle
            # record-only mixer spinning after its writer has exited.
            self._stop_mixer_if_idle()

    def push_audio(self, source: str, data: bytes, gain: float = 1.0):
        if not (self.running or self.recording_active) or not data:
            return
        with self.sources_lock:
            if source not in self.sources:
                self.sources[source] = (PCMBuffer(), gain)
            buffer, _ = self.sources[source]
            self.sources[source] = (buffer, gain)
        buffer.append(data, gain)

    def set_master_gain(self, gain: float):
        """Set the program-bus gain applied to every source before transmission."""
        try:
            self.master_gain = max(0.0, min(1.0, float(gain)))
        except (TypeError, ValueError, OverflowError):
            self.master_gain = 1.0

    def remove_source(self, source: str):
        with self.sources_lock:
            self.sources.pop(source, None)

    def clear_source(self, source: str, *, preserve_primed: bool = False):
        with self.sources_lock:
            current = self.sources.get(source)
            if current:
                current[0].clear(preserve_primed=preserve_primed)

    def promote_source(self, incoming: str, active: str):
        """Promote a crossfaded source while discarding the outgoing tail."""
        if incoming == active:
            return
        with self.sources_lock:
            promoted = self.sources.pop(incoming, None)
            self.sources.pop(active, None)
            if promoted is not None:
                self.sources[active] = promoted

    def start(self, profiles: list[dict], local: dict):
        if self.running:
            return
        targets = []
        self.stop_event.clear()
        self.startup_errors = {}
        credentials = {}
        startup_errors = {}
        for profile in profiles:
            if profile.get("enabled"):
                try:
                    password = get_password(profile["id"])
                except Exception as exc:
                    startup_errors[profile["id"]] = f"Credencial indisponível: {exc}"
                    continue
                if not password:
                    startup_errors[profile["id"]] = (
                        "Senha ausente no Gerenciador de Credenciais")
                    continue
                credentials[profile["id"]] = password
        local_server = None
        if local.get("enabled"):
            host = "0.0.0.0" if local.get("lan") else "127.0.0.1"
            local_server = LocalStreamServer(host, int(local.get("port", 8090)),
                                              int(local.get("max_listeners", 10)),
                                              local.get("station", "RadioStudio Python"),
                                              local.get("genre", ""), local.get("url", ""),
                                              local.get("description", ""),
                                              send_cover_art=bool(local.get("send_cover_art", False)))
            local_server.set_cover_art(self.cover_art_data, self.cover_art_mime_type)
            local_server.diagnostics_provider = getattr(self, "diagnostics_snapshot", None)
            targets.append(EncoderOutput(
                "Servidor embutido", int(local.get("bitrate", 192)), local_server,
                encoder_quality=local.get("encoder_quality", 2)))
        for profile in profiles:
            if not profile.get("enabled") or profile["id"] not in credentials:
                continue
            targets.append(EncoderOutput(
                profile["name"], int(profile["bitrate"]),
                RemoteSink(profile, credentials[profile["id"]]),
                encoder_quality=profile.get("encoder_quality", 2)))
        if not targets:
            missing = [f"{profile.get('name', profile_id)}: {message}"
                       for profile_id, message in startup_errors.items()
                       for profile in profiles if profile.get("id") == profile_id]
            if missing:
                raise ValueError("Nenhuma saída externa pôde iniciar. " + "; ".join(missing))
            raise ValueError("Ative o servidor embutido ou um perfil de transmissão")
        self.outputs = targets
        self.profiles = [profile for profile in profiles
                         if profile.get("enabled") and profile["id"] in credentials]
        self.startup_errors = startup_errors
        self.local_server = local_server
        try:
            if self.local_server:
                try:
                    self.local_server.start()
                    self.local_server.title = self.title
                except PortInUseError:
                    raise
                except Exception as exc:
                    self.startup_errors["local"] = str(exc)
                    try:
                        self.local_server.stop()
                    except Exception:
                        logging.getLogger("radiostudio").exception(
                            "Falha ao encerrar o servidor embutido após erro na inicialização")
                    self.outputs = [output for output in targets
                                    if output.destination is not self.local_server]
                    self.local_server = None
                    if not self.outputs:
                        raise RuntimeError(f"Servidor embutido: {exc}") from exc
            started = 0
            for output in targets:
                if output not in self.outputs:
                    continue
                try:
                    output.start()
                    started += 1
                except Exception as exc:
                    output.status = "Falha"
                    output.error = str(exc)
            if not started:
                details = "; ".join(output.error for output in targets if output.error)
                raise RuntimeError(details or "Nenhuma saída conseguiu iniciar")
            self._ensure_mixer()
            if self.title:
                for profile in tuple(self.profiles):
                    self._queue_metadata_update(profile, self.title)
            if self.metadata_update_mode == "interval":
                self._start_metadata_scheduler()
        except Exception:
            self.stop_transmission()
            raise

    def _mix_loop(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            mixed = self._mix_source_samples()
            pcm = (SILENCE_PCM_FRAME if mixed is None else
                   array("h", (max(-32768, min(32767, value)) for value in mixed)).tobytes())
            self._publish_mix_frame(pcm)
            deadline += FRAME_MS / 1000
            self.stop_event.wait(max(0, deadline - time.monotonic()))
            if time.monotonic() - deadline > 0.5:
                deadline = time.monotonic()

    def _mix_source_samples(self):
        """Mix active sources; return None when the full frame is silence."""
        master_gain = self.master_gain
        with self.sources_lock:
            sources = list(self.sources.items())
        mixed = None
        for source_name, (buffer, gain) in sources:
            try:
                take_if_empty = getattr(buffer, "take_if_empty", None)
                if (callable(take_if_empty)
                        and take_if_empty(PCM_BYTES_PER_FRAME) is not None):
                    continue
                # Keep third-party/test buffers compatible with the original
                # take() interface; their behavior remains isolated below.
                take_gained = getattr(buffer, "take_gained", None)
                if callable(take_gained):
                    segments = take_gained(PCM_BYTES_PER_FRAME)
                    sample_offset = 0
                    if mixed is None:
                        mixed = [0] * (PCM_BYTES_PER_FRAME // 2)
                    for data, captured_gain in segments:
                        samples = array("h")
                        samples.frombytes(data)
                        segment_gain = gain if captured_gain is None else captured_gain
                        segment_gain *= master_gain
                        for sample in samples:
                            if sample_offset >= len(mixed):
                                raise ValueError("A fonte retornou um quadro PCM grande demais")
                            mixed[sample_offset] += int(sample * segment_gain)
                            sample_offset += 1
                    if sample_offset != len(mixed):
                        raise ValueError("A fonte retornou um quadro PCM com tamanho inválido")
                else:
                    samples = array("h")
                    samples.frombytes(buffer.take(PCM_BYTES_PER_FRAME))
                    if len(samples) != PCM_BYTES_PER_FRAME // 2:
                        raise ValueError("A fonte retornou um quadro PCM com tamanho inválido")
                    if mixed is None:
                        mixed = [0] * (PCM_BYTES_PER_FRAME // 2)
                    for index, sample in enumerate(samples):
                        mixed[index] += int(sample * gain * master_gain)
            except Exception:
                with self.sources_lock:
                    current = self.sources.get(source_name)
                    if current and current[0] is buffer:
                        self.sources.pop(source_name, None)
                logging.getLogger("radiostudio").exception(
                    "Falha ao ler a fonte de áudio %s; fonte removida do mixer",
                    source_name)
        return mixed

    def _feed_outputs(self, pcm: bytes):
        """A failing output reconnects independently without stopping other streams."""
        for output in tuple(self.outputs):
            if output.status == "Falha":
                continue
            try:
                output.feed(pcm)
            except Exception as exc:
                logging.getLogger("radiostudio").exception(
                    "Falha isolada no envio de áudio para %s",
                    getattr(output, "name", "saída"))
                reason = f"Falha ao entregar áudio ao encoder: {exc}"
                try:
                    output._signal_connection_failure(reason)
                except Exception:
                    logging.getLogger("radiostudio").exception(
                        "Falha ao interromper a saída %s após erro",
                        getattr(output, "name", "saída"))
                    try:
                        output.connection_failed.set()
                        output.status = "Falha"
                        output.error = reason
                    except Exception:
                        logging.getLogger("radiostudio").exception(
                            "Falha ao isolar a saída de áudio")

    def stop_transmission(self):
        self._metadata_stop_event.set()
        self._metadata_schedule_changed.set()
        with self._metadata_lock:
            self._metadata_pending.clear()
        outputs, self.outputs = tuple(self.outputs), []
        local_server, self.local_server = self.local_server, None
        self.profiles = []
        self.startup_errors = {}
        for output in outputs:
            output.stop()
        if (self._metadata_scheduler_thread
                and self._metadata_scheduler_thread.is_alive()):
            self._metadata_scheduler_thread.join(timeout=2)
        self._metadata_scheduler_thread = None
        for output in outputs:
            if output.thread and output.thread.is_alive():
                output.thread.join(timeout=2)
            if output.stats_thread and output.stats_thread.is_alive():
                output.stats_thread.join(timeout=2)
        if local_server:
            local_server.stop()
        self._stop_mixer_if_idle()

    def stop(self):
        """Stop transmission and recording (used for full application shutdown)."""
        session = self.recording
        if session:
            try:
                self.stop_recording(wait=True)
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha ao finalizar a gravação durante o encerramento")
        self.stop_transmission()

    def set_title(self, title: str):
        self._set_local_title(title)
        if self.metadata_update_mode == "current":
            self._publish_metadata_title(title)

    def set_track_title(self, title: str):
        """Update the local title and publish for track-based modes."""
        self._set_local_title(title)
        if self.metadata_update_mode in {"current", "track_change"}:
            self._publish_metadata_title(title)

    def _set_local_title(self, title: str) -> None:
        self.title = str(title)
        if self.local_server:
            self.local_server.title = self.title

    def _publish_metadata_title(self, title: str) -> None:
        for profile in tuple(self.profiles):
            self._queue_metadata_update(profile, title)

    def set_metadata_update_preferences(self, mode: str, interval_seconds: int) -> None:
        valid_modes = {"current", "track_change", "interval"}
        self.metadata_update_mode = (
            mode if isinstance(mode, str) and mode in valid_modes else "current")
        try:
            interval = int(interval_seconds)
        except (TypeError, ValueError, OverflowError):
            interval = 30
        self.metadata_update_interval_seconds = min(3600, max(5, interval))
        if self.running and self.metadata_update_mode == "interval":
            self._start_metadata_scheduler()
        if self._metadata_scheduler_thread and self._metadata_scheduler_thread.is_alive():
            self._metadata_schedule_changed.set()

    def _start_metadata_scheduler(self) -> None:
        if (self._metadata_scheduler_thread
                and self._metadata_scheduler_thread.is_alive()):
            self._metadata_schedule_changed.set()
            return
        self._metadata_stop_event.clear()
        self._metadata_schedule_changed.clear()
        self._metadata_scheduler_thread = threading.Thread(
            target=self._metadata_schedule_loop, daemon=True,
            name="broadcast-metadata-scheduler")
        try:
            self._metadata_scheduler_thread.start()
        except RuntimeError:
            self._metadata_scheduler_thread = None
            logging.getLogger("radiostudio").exception(
                "Falha ao iniciar a atualização periódica de metadados")

    def _metadata_schedule_loop(self) -> None:
        while not self.stop_event.is_set() and not self._metadata_stop_event.is_set():
            self._metadata_schedule_changed.clear()
            if self.metadata_update_mode != "interval":
                self._metadata_schedule_changed.wait()
                continue
            interval = self.metadata_update_interval_seconds
            if self._metadata_schedule_changed.wait(interval):
                continue
            if self.stop_event.is_set() or not self.title:
                continue
            self._publish_metadata_title(self.title)

    def _queue_metadata_update(self, profile: dict, title: str) -> None:
        profile_id = str(profile.get("id") or "")
        if not profile_id:
            return
        with self._metadata_lock:
            self._metadata_pending[profile_id] = (dict(profile), str(title))
            if profile_id in self._metadata_active:
                return
            self._metadata_active.add(profile_id)
        try:
            threading.Thread(target=self._drain_metadata_updates,
                             args=(profile_id,), daemon=True).start()
        except RuntimeError:
            with self._metadata_lock:
                self._metadata_active.discard(profile_id)
                self._metadata_pending.pop(profile_id, None)
            logging.getLogger("radiostudio").exception(
                "Falha ao iniciar atualização de metadados para %s",
                profile.get("name", "perfil"))

    def _drain_metadata_updates(self, profile_id: str) -> None:
        while True:
            with self._metadata_lock:
                update = self._metadata_pending.pop(profile_id, None)
                if update is None:
                    self._metadata_active.discard(profile_id)
                    return
            profile, title = update
            try:
                if get_password(profile_id + ":metadata"):
                    self._send_metadata(profile, title)
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha ao enviar atualização de metadados para %s",
                    profile.get("name", "perfil"))

    def set_cover_art(self, data: bytes | None, mime_type: str = ""):
        self.cover_art_data = bytes(data) if data else None
        self.cover_art_mime_type = str(mime_type or "") if data else ""
        if self.local_server:
            self.local_server.set_cover_art(self.cover_art_data, self.cover_art_mime_type)

    def set_cover_art_enabled(self, enabled: bool):
        if self.local_server:
            self.local_server.set_cover_art_enabled(enabled)
            if enabled:
                self.local_server.set_cover_art(
                    self.cover_art_data, self.cover_art_mime_type)

    @staticmethod
    def _send_metadata(profile: dict, title: str):
        password = get_password(profile["id"] + ":metadata")
        if not password:
            return
        port = int(profile.get("http_port") or profile["port"])
        scheme = "https" if profile.get("tls") else "http"
        base = f"{scheme}://{profile['host']}:{port}"
        if profile["kind"] == "icecast":
            query = urlencode({"mount": "/" + profile["mount"].lstrip("/"),
                               "mode": "updinfo", "song": title})
            url = f"{base}/admin/metadata?{query}"
            user = profile.get("metadata_user") or "admin"
            auth = base64.b64encode(f"{user}:{password}".encode()).decode()
            request = Request(url, headers={"Authorization": f"Basic {auth}"})
        else:
            query = urlencode({"pass": password, "mode": "updinfo", "song": title,
                               "sid": int(profile.get("stream_id", 1))})
            request = Request(f"{base}/admin.cgi?{query}")
        try:
            with urlopen(request, timeout=5) as response:
                response.read(1024)
        except (OSError, ValueError):
            pass
