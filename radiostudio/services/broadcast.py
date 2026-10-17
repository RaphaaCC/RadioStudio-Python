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
CREDENTIAL_SERVICE = "RadioStudioPython.Broadcast"
SETTINGS_DIR = APP_DIR


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
            "bitrate": 128, "enabled": True, "station": "",
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

    def append(self, data: bytes):
        if not data:
            return
        with self.lock:
            self.chunks.append(data)
            self.length += len(data)
            while self.length > SAMPLE_RATE * CHANNELS * 2 * 2 and self.chunks:
                self.length -= len(self.chunks.popleft())

    def take(self, length: int) -> bytes:
        result = bytearray()
        with self.lock:
            while self.chunks and len(result) < length:
                chunk = self.chunks.popleft()
                needed = length - len(result)
                result.extend(chunk[:needed])
                self.length -= min(len(chunk), needed)
                if len(chunk) > needed:
                    remainder = chunk[needed:]
                    self.chunks.appendleft(remainder)
                    break
        if len(result) < length:
            result.extend(b"\0" * (length - len(result)))
        return bytes(result)


class LocalStreamServer:
    def __init__(self, host: str, port: int, max_listeners: int, station: str,
                 genre: str = "", url: str = "", description: str = ""):
        self.host, self.port = host, port
        self.max_listeners = max_listeners
        self.station = station
        self.genre = genre
        self.url = url
        self.description = description
        self.title = ""
        self.tls = False
        self.clients: set[queue.Queue] = set()
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
                if self.path == "/status.json":
                    body = json.dumps({"station": owner.station, "genre": owner.genre,
                                       "url": owner.url, "description": owner.description,
                                       "title": owner.title,
                                       "listeners": owner.listeners}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path not in ("/", "/live.mp3"):
                    self.send_error(404)
                    return
                client = queue.Queue(maxsize=80)
                with owner.lock:
                    if len(owner.clients) >= owner.max_listeners:
                        self.send_error(503, "Limite de ouvintes atingido")
                        return
                    owner.clients.add(client)
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
                    with owner.lock:
                        owner.clients.discard(client)

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

    def publish(self, data: bytes):
        with self.lock:
            for client in tuple(self.clients):
                try:
                    client.put_nowait(data)
                except queue.Full:
                    self.clients.discard(client)

    def stop(self):
        server, self.server = self.server, None
        thread, self.thread = self.thread, None
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

    def connect(self):
        profile = self.profile
        sock = socket.create_connection((profile["host"], int(profile["port"])), timeout=8)
        try:
            if profile.get("tls"):
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=profile["host"])
            sock.settimeout(8)
            if profile["kind"] == "icecast":
                mount = "/" + profile["mount"].lstrip("/")
                auth = base64.b64encode(f"{profile.get('user') or 'source'}:{self.password}".encode()).decode()
                headers = [f"PUT {quote(mount, safe='/')} HTTP/1.1",
                           f"Host: {profile['host']}:{profile['port']}",
                           f"Authorization: Basic {auth}", "Content-Type: audio/mpeg",
                           "Transfer-Encoding: chunked", "Connection: keep-alive",
                           f"Ice-Name: {profile.get('station', '')}",
                           f"Ice-Genre: {profile.get('genre', '')}",
                           f"Ice-URL: {profile.get('url', '')}",
                           f"Ice-Description: {profile.get('description', '')}", "", ""]
                sock.sendall("\r\n".join(headers).encode("utf-8"))
                response = b""
                for _ in range(2):
                    while b"\r\n\r\n" not in response and len(response) < 8192:
                        chunk = sock.recv(1024)
                        if not chunk:
                            raise ConnectionError("Servidor encerrou a conexão")
                        response += chunk
                    header, _, response = response.partition(b"\r\n\r\n")
                    status = header.split(b"\r\n", 1)[0]
                    if b" 100 " not in status:
                        break
                if b" 200 " not in status and b" 201 " not in status:
                    raise ConnectionError(status.decode("latin-1", "replace"))
            elif profile["kind"] == "shoutcast":
                # DNAS >=2.4.7 maps legacy sources to a stream with password:#SID.
                stream_id = int(profile.get("stream_id", 1))
                source_password = self.password if stream_id == 1 else f"{self.password}:#{stream_id}"
                sock.sendall((source_password + "\n").encode("utf-8"))
                response = bytearray()
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
                sock.sendall(("\n".join(headers) + "\n").encode("utf-8"))
            else:
                raise ValueError("Protocolo de transmissão desconhecido")
            sock.settimeout(10)
            self.sock = sock
        except Exception:
            sock.close()
            raise

    def publish(self, data: bytes):
        if self.profile["kind"] == "icecast":
            self.sock.sendall(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        else:
            self.sock.sendall(data)

    def close(self):
        sock, self.sock = self.sock, None
        if sock:
            try:
                if self.profile["kind"] == "icecast":
                    sock.sendall(b"0\r\n\r\n")
            except OSError:
                pass
            sock.close()

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
    def __init__(self, name: str, bitrate: int, destination, status_callback=None):
        self.name, self.bitrate = name, bitrate
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
        self.connection_failed = threading.Event()
        self.stats_thread = None

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
        if self.stop_event.is_set():
            return
        try:
            self.queue.put_nowait(data)
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(data)
            except queue.Empty:
                pass

    def _run(self):
        first_attempt = True
        while not self.stop_event.is_set():
            self.connection_failed.clear()
            self._status("Conectando" if first_attempt else "Reconectando")
            try:
                self.destination.connect() if isinstance(self.destination, RemoteSink) else None
                if self.stop_event.is_set():
                    break
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le",
                       "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
                       "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k",
                       "-id3v2_version", "0", "-write_xing", "0", "-f", "mp3", "pipe:1"]
                self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if self.stop_event.is_set():
                    break
                self._status("Ativo")
                threading.Thread(target=self._read_encoded, daemon=True).start()
                while not self.stop_event.is_set() and not self.connection_failed.is_set():
                    try:
                        data = self.queue.get(timeout=0.2)
                    except queue.Empty:
                        if self.process.poll() is not None:
                            raise RuntimeError("Codificador MP3 encerrou")
                        continue
                    self.process.stdin.write(data)
                    self.process.stdin.flush()
                if self.connection_failed.is_set():
                    raise ConnectionError(self.error or "Conexão encerrada")
            except Exception as exc:
                if not self.stop_event.is_set():
                    self._status("Reconectando", str(exc))
            finally:
                self._close_connection()
            first_attempt = False
            self.stop_event.wait(5)
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
        if isinstance(self.destination, RemoteSink):
            self.destination.close()

    def _read_encoded(self):
        process = self.process
        try:
            while not self.stop_event.is_set():
                data = process.stdout.read(4096)
                if not data:
                    break
                self.destination.publish(data)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = str(exc)
                self.connection_failed.set()

    def stop(self):
        self.stop_event.set()
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
        self.profiles = []
        self.startup_errors: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return bool(self.outputs)

    def push_audio(self, source: str, data: bytes, gain: float = 1.0):
        if not self.running or not data:
            return
        with self.sources_lock:
            if source not in self.sources:
                self.sources[source] = (PCMBuffer(), gain)
            buffer, _ = self.sources[source]
            self.sources[source] = (buffer, gain)
        buffer.append(data)

    def remove_source(self, source: str):
        with self.sources_lock:
            self.sources.pop(source, None)

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
                                              local.get("description", ""))
            targets.append(EncoderOutput("Servidor embutido", int(local.get("bitrate", 128)),
                                         local_server))
        for profile in profiles:
            if not profile.get("enabled") or profile["id"] not in credentials:
                continue
            targets.append(EncoderOutput(profile["name"], int(profile["bitrate"]),
                                         RemoteSink(profile, credentials[profile["id"]])))
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
                        pass
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
            self.mixer_thread = threading.Thread(target=self._mix_loop, daemon=True)
            self.mixer_thread.start()
        except Exception:
            self.stop()
            raise

    def _mix_loop(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            with self.sources_lock:
                sources = list(self.sources.values())
            mixed = [0] * (PCM_BYTES_PER_FRAME // 2)
            for buffer, gain in sources:
                samples = array("h")
                samples.frombytes(buffer.take(PCM_BYTES_PER_FRAME))
                for i, sample in enumerate(samples):
                    mixed[i] += int(sample * gain)
            pcm = array("h", (max(-32768, min(32767, value)) for value in mixed)).tobytes()
            for output in tuple(self.outputs):
                if output.status != "Falha":
                    output.feed(pcm)
            deadline += FRAME_MS / 1000
            self.stop_event.wait(max(0, deadline - time.monotonic()))
            if time.monotonic() - deadline > 0.5:
                deadline = time.monotonic()

    def stop(self):
        self.stop_event.set()
        for output in self.outputs:
            output.stop()
        if self.mixer_thread:
            self.mixer_thread.join(timeout=2)
        for output in self.outputs:
            if output.thread and output.thread.is_alive():
                output.thread.join(timeout=2)
            if output.stats_thread and output.stats_thread.is_alive():
                output.stats_thread.join(timeout=2)
        if self.local_server:
            self.local_server.stop()
            self.local_server = None
        self.outputs = []
        self.profiles = []
        self.startup_errors = {}
        with self.sources_lock:
            self.sources.clear()

    def set_title(self, title: str):
        self.title = title
        if self.local_server:
            self.local_server.title = title
        for profile in self.profiles:
            try:
                if get_password(profile["id"] + ":metadata"):
                    threading.Thread(target=self._send_metadata,
                                     args=(profile, title), daemon=True).start()
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha ao preparar atualização de metadados para %s", profile.get("name", "perfil"))

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
