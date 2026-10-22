"""Optional Discord Rich Presence, isolated from the audio/UI threads."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import threading
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal

from ..config import DISCORD_APPLICATION_ID


_APP_ID_PATTERN = re.compile(r"^[0-9]{15,25}$")
_PRESENCE_PLACEHOLDER_PATTERN = re.compile(r"%([a-z_]+)", re.IGNORECASE)
ACTIVITY_TYPES = (0, 2, 3, 5)
STATUS_DISPLAY_TYPES = (0, 1, 2)
PRESENCE_PLACEHOLDERS = {"title", "artist", "album", "station", "status"}
_LOG = logging.getLogger("radiostudio.discord")


def valid_discord_app_id(value: str) -> bool:
    return bool(_APP_ID_PATTERN.fullmatch(str(value or "").strip()))


def validate_public_radio_url(value: str) -> str:
    """Return a safe public HTTP(S) URL, or raise ValueError for unsafe values."""
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 2048:
        raise ValueError("O link público é muito longo.")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Informe um endereço HTTP ou HTTPS válido.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("O link precisa começar com http:// ou https://.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("O link não pode conter usuário ou senha.")
    if parsed.query or parsed.fragment:
        raise ValueError("Remova parâmetros e fragmentos do link antes de compartilhá-lo.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("A porta do link é inválida.")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
            (".local", ".internal", ".lan", ".home", ".localhost")):
        raise ValueError("Use um endereço público, não um endereço local da rede.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or " " in hostname:
            raise ValueError("Informe o domínio público da rádio.")
    else:
        if not address.is_global:
            raise ValueError("Endereços IP privados ou locais não podem ser compartilhados.")
    return value


def validate_public_cover_url(value: str) -> str:
    """Validate a public cover endpoint and reserve room for cache busting."""
    value = validate_public_radio_url(value)
    if value and len(value) > 230:
        raise ValueError("A URL da capa precisa ter no máximo 230 caracteres.")
    return value


def unknown_presence_placeholders(value: str) -> list[str]:
    """Return unsupported Rich Presence template tokens, preserving order."""
    found = []
    for match in _PRESENCE_PLACEHOLDER_PATTERN.finditer(str(value or "")):
        token = match.group(1).lower()
        if token not in PRESENCE_PLACEHOLDERS and token not in found:
            found.append(token)
    return found


def _format_presence_template(template: str, values: dict[str, str], limit: int = 128) -> str:
    text = str(template or "")

    def replace(match):
        token = match.group(1).lower()
        return values.get(token, match.group(0))

    text = _PRESENCE_PLACEHOLDER_PATTERN.sub(replace, text)
    # A blank metadata field should not leave a dangling separator in the state.
    text = re.sub(r"\s*[·|,;]\s*(?=[·|,;]|$)", "", text)
    text = re.sub(r"(?:\s*[·|,;]\s*){2,}", " · ", text)
    text = re.sub(r"^\s*[·|,;]\s*|\s*[·|,;]\s*$", "", text)
    return text.strip()[:limit]


def make_activity(*, title: str = "", artist: str = "", station: str = "RadioStudio Python",
                  broadcasting: bool = False, playing: bool = False, paused: bool = False,
                  share_track: bool = True, public_url: str = "", large_image: str = "radiostudio",
                  cover_url: str = "", cover_version: str = "",
                  started_at: int | None = None, ends_at: int | None = None,
                  album: str = "", activity_name: str = "RadioStudio Python Sirius",
                  activity_type: int = 2, status_display_type: int = 0,
                  details_template: str = "🎵 %title",
                  state_template: str = "%artist · %station · %status",
                  large_text: str = "RadioStudio Python Sirius",
                  button1_label: str = "Ouvir a rádio", button1_url: str = "",
                  button2_label: str = "", button2_url: str = "") -> dict:
    """Create a small, privacy-aware activity payload for pypresence."""
    title = str(title or "").strip()
    artist = str(artist or "").strip()
    station = str(station or "RadioStudio Python").strip() or "RadioStudio Python"
    if not broadcasting and not playing and not paused:
        return {}

    if activity_type not in ACTIVITY_TYPES:
        activity_type = 2
    if status_display_type not in STATUS_DISPLAY_TYPES:
        status_display_type = 0
    visible_track = bool(share_track and (playing or paused))
    if not visible_track:
        title = artist = album = ""
    status = "No ar" if broadcasting else "Pausado" if paused else "Tocando"
    values = {"title": title, "artist": artist, "album": album,
              "station": station, "status": status}
    fallback_details = "🎵 " + title if visible_track and title else "📻 RadioStudio Python"
    template_uses_track_data = bool(re.search(r"%(title|artist|album)\b", details_template,
                                              re.IGNORECASE))
    details = (_format_presence_template(details_template, values)
               if visible_track or not template_uses_track_data else "")
    details = details or fallback_details[:128]
    state = _format_presence_template(state_template, values)
    if len(details) < 2:
        details = fallback_details[:128]
    display_name = str(activity_name or "").strip()[:128] or "RadioStudio Python Sirius"
    payload = {"name": display_name, "activity_type": activity_type,
               "status_display_type": status_display_type, "details": details}
    if len(state) >= 2:
        payload["state"] = state
    image_key = str(large_image or "").strip()
    cover_version = str(cover_version or "")
    if (visible_track and cover_url
            and re.fullmatch(r"[a-fA-F0-9]{1,32}", cover_version)):
        public_cover_url = validate_public_cover_url(cover_url)
        if public_cover_url:
            image_key = f"{public_cover_url}?rs_cover={cover_version.lower()}"
    if image_key:
        payload["large_image"] = image_key[:256]
        tooltip = str(large_text or "").strip()
        if tooltip:
            payload["large_text"] = tooltip[:128]
    if playing and not paused and started_at is not None:
        payload["start"] = max(0, int(started_at * 1000))
        if ends_at is not None and int(ends_at) > int(started_at):
            payload["end"] = int(ends_at * 1000)
    # public_url remains as a compatibility alias for settings saved by earlier builds.
    first_url = button1_url or public_url
    buttons = []
    for label, url, fallback_label in (
            (button1_label, first_url, "Ouvir a rádio"),
            (button2_label, button2_url, "Acessar site")):
        radio_url = validate_public_radio_url(url)
        if radio_url:
            button_label = str(label or fallback_label).strip()[:32]
            if button_label:
                buttons.append({"label": button_label, "url": radio_url})
    if buttons:
        payload["buttons"] = buttons[:2]
    return payload


class DiscordPresenceService(QObject):
    """Owns Discord IPC in a worker thread and exposes only a small Qt API."""

    availability_changed = Signal(bool, str)
    MIN_UPDATE_INTERVAL = 15.0
    RETRY_INTERVAL = 5.0

    def __init__(self, parent=None, rpc_factory=None):
        super().__init__(parent)
        self._rpc_factory = rpc_factory or self._default_rpc_factory
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = False
        self._client_id = DISCORD_APPLICATION_ID
        self._enabled = False
        self._verification_requested = False
        self._activity: dict = {}
        self._generation = 0
        self._available = False
        self._status = "Abra o aplicativo Discord e verifique a conexão."
        self._thread: threading.Thread | None = None

    @staticmethod
    def _default_rpc_factory(client_id):
        # Keep the dependency import lazy so an unavailable optional package
        # cannot prevent the rest of the radio from starting.
        from pypresence import Presence

        class BoundedPresence(Presence):
            """Bound pypresence's unbounded Windows IPC handshake read."""

            def connect(self):
                try:
                    self.loop.run_until_complete(asyncio.wait_for(
                        self.handshake(), timeout=self.connection_timeout))
                except BaseException:
                    writer = self.sock_writer
                    transport = getattr(writer, "transport", None) if writer else None
                    if transport is not None:
                        transport.abort()
                    self.sock_reader = None
                    self.sock_writer = None
                    if not self.loop.is_closed():
                        self.loop.close()
                    raise

        loop = asyncio.new_event_loop()
        return BoundedPresence(client_id, loop=loop,
                               connection_timeout=6, response_timeout=5)

    @staticmethod
    def _update_rpc(rpc, activity: dict):
        payload = dict(activity)
        if "activity_type" in payload or "status_display_type" in payload:
            from pypresence import ActivityType, StatusDisplayType
        if "activity_type" in payload:
            payload["activity_type"] = ActivityType(payload["activity_type"])
        if "status_display_type" in payload:
            payload["status_display_type"] = StatusDisplayType(payload["status_display_type"])
        return rpc.update(**payload)

    @staticmethod
    def _connection_error_status(exc: Exception) -> str:
        error_type = type(exc).__name__
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or error_type in {
                "ConnectionTimeout", "ResponseTimeout"}:
            return ("O Discord abriu o canal local, mas não respondeu a tempo. "
                    "Confira se o aplicativo Discord está aberto e atualizado.")
        if error_type == "InvalidID":
            return "O Discord recusou o ID do aplicativo RadioStudio."
        if error_type in {"DiscordNotFound", "InvalidPipe"}:
            return ("Não foi possível abrir o canal local do Discord. "
                    "Abra o aplicativo Discord e tente novamente.")
        if error_type == "ServerError":
            return "O Discord recusou o registro da Rich Presence. Consulte o log do aplicativo."
        return f"Falha na comunicação com o Discord ({error_type})."

    @property
    def client_id(self):
        with self._lock:
            return self._client_id

    @property
    def available(self):
        with self._lock:
            return self._available

    @property
    def status(self):
        with self._lock:
            return self._status

    def configure(self, client_id: str, enabled: bool = False, *,
                  verify: bool = False) -> None:
        client_id = str(client_id or "").strip()
        if not valid_discord_app_id(client_id):
            client_id = ""
        enabled = bool(enabled)
        verify = bool(verify and client_id and not enabled)
        with self._lock:
            changed_client = client_id != self._client_id
            changed_state = (changed_client or enabled != self._enabled
                             or verify != self._verification_requested)
            self._client_id = client_id
            self._enabled = enabled
            self._verification_requested = verify
            if changed_client:
                self._activity = {}
            if changed_state:
                self._generation += 1
            if not client_id:
                self._set_status_locked(
                    False, "Abra o aplicativo Discord e verifique a conexão.")
            elif enabled or verify:
                if changed_state or not self._available:
                    self._set_status_locked(
                        False, "Verificando conexão e registro da Rich Presence…")
            else:
                self._set_status_locked(False, "Rich Presence desativada.")
            if client_id and (enabled or verify) and not self._stopping:
                if not self._thread or not self._thread.is_alive():
                    self._thread = threading.Thread(
                        target=self._run, name="RadioStudio Discord RPC", daemon=True)
                    self._thread.start()
        self._wake.set()

    def set_activity(self, activity: dict) -> None:
        updated = dict(activity) if isinstance(activity, dict) else {}
        with self._lock:
            if updated == self._activity:
                return
            self._activity = updated
        self._wake.set()

    def _set_status_locked(self, available: bool, status: str) -> None:
        changed = self._available != available or self._status != status
        self._available = available
        self._status = status
        if changed:
            self.availability_changed.emit(available, status)

    def _status_update(self, available: bool, status: str, generation: int) -> None:
        with self._lock:
            # A handshake can finish after shutdown (or after the app ID was
            # changed). Never publish a late result into a closing/reconfigured
            # window; Qt may already be tearing down its receiver at shutdown.
            if self._stopping or generation != self._generation:
                return
            self._set_status_locked(available, status)

    @staticmethod
    def _close_rpc(rpc, activity_was_set: bool) -> None:
        if rpc is None:
            return
        if activity_was_set:
            try:
                rpc.clear()
            except Exception:
                _LOG.debug("Discord activity could not be cleared", exc_info=True)
        try:
            rpc.close()
        except Exception:
            _LOG.debug("Discord RPC connection could not be closed", exc_info=True)

    def _run(self) -> None:
        rpc = None
        rpc_client_id = ""
        rpc_generation = -1
        last_activity = None
        next_update_at = 0.0
        retry_at = 0.0
        while True:
            # Clear before taking the state snapshot. Any change arriving
            # afterward leaves the event set and cannot be lost before wait().
            self._wake.clear()
            with self._lock:
                if self._stopping:
                    client_id = ""
                    enabled = False
                    verify_requested = False
                    activity = {}
                    generation = self._generation
                else:
                    client_id = self._client_id
                    enabled = self._enabled
                    verify_requested = self._verification_requested
                    activity = dict(self._activity)
                    generation = self._generation

            if self._stopping:
                self._close_rpc(rpc, last_activity is not None)
                return

            if rpc is not None and (client_id != rpc_client_id or generation != rpc_generation):
                self._close_rpc(rpc, last_activity is not None)
                rpc = None
                rpc_client_id = ""
                last_activity = None
                next_update_at = 0.0

            if not client_id:
                if rpc is not None:
                    self._close_rpc(rpc, last_activity is not None)
                    rpc = None
                    last_activity = None
                self._wake.wait()
                continue

            if not enabled and not verify_requested:
                if rpc is not None:
                    self._close_rpc(rpc, last_activity is not None)
                    rpc = None
                    rpc_client_id = ""
                    last_activity = None
                # Presence is optional; avoid repeatedly opening Discord IPC
                # when the user has disabled it. The Settings button requests
                # a single connection probe when needed.
                self._wake.wait()
                continue

            now = time.monotonic()
            if rpc is None and now >= retry_at:
                candidate = None
                candidate_activity_set = False
                try:
                    candidate = self._rpc_factory(client_id)
                    candidate.connect()
                    with self._lock:
                        still_current = (not self._stopping
                                         and generation == self._generation
                                         and client_id == self._client_id)
                    if not still_current:
                        self._close_rpc(candidate, False)
                        continue
                    # An actual update verifies that Discord accepts and
                    # registers activity before the setting becomes available.
                    if enabled and activity:
                        self._update_rpc(candidate, activity)
                        last_activity = activity
                        candidate_activity_set = True
                    else:
                        probe = {"name": "RadioStudio Python Sirius", "activity_type": 2,
                                 "details": "RadioStudio Python", "state": "Conexão verificada"}
                        for key in ("large_image", "large_text"):
                            if activity.get(key):
                                probe[key] = activity[key]
                        self._update_rpc(candidate, probe)
                        candidate_activity_set = True
                        candidate.clear()
                        candidate_activity_set = False
                        last_activity = None
                    # connect/update can block briefly. Recheck before keeping
                    # this connection if shutdown or reconfiguration happened
                    # while Discord was responding.
                    with self._lock:
                        still_current = (not self._stopping
                                         and generation == self._generation
                                         and client_id == self._client_id)
                    if not still_current:
                        self._close_rpc(candidate, candidate_activity_set)
                        continue
                    if verify_requested and not enabled:
                        self._close_rpc(candidate, candidate_activity_set)
                        candidate = None
                        with self._lock:
                            if generation == self._generation and not self._stopping:
                                self._verification_requested = False
                        retry_at = 0.0
                        self._status_update(
                            True, "Discord conectado; Rich Presence registrada.", generation)
                        continue
                    rpc = candidate
                    rpc_client_id = client_id
                    rpc_generation = generation
                    next_update_at = now + self.MIN_UPDATE_INTERVAL
                    retry_at = 0.0
                    self._status_update(
                        True, "Discord conectado; Rich Presence registrada.", generation)
                except Exception as exc:
                    self._close_rpc(candidate, candidate_activity_set)
                    rpc = None
                    verification_only = verify_requested and not enabled
                    if verification_only:
                        with self._lock:
                            if generation == self._generation:
                                self._verification_requested = False
                        retry_at = 0.0
                    else:
                        retry_at = now + self.RETRY_INTERVAL
                    _LOG.info("Discord Rich Presence indisponível (%s): %s",
                              type(exc).__name__, exc)
                    self._status_update(
                        False, self._connection_error_status(exc), generation)

            if rpc is not None:
                try:
                    if not enabled:
                        if last_activity is not None:
                            rpc.clear()
                            last_activity = None
                    elif not activity:
                        if last_activity is not None:
                            rpc.clear()
                            last_activity = None
                    elif activity != last_activity and now >= next_update_at:
                        self._update_rpc(rpc, activity)
                        last_activity = activity
                        next_update_at = now + self.MIN_UPDATE_INTERVAL
                except Exception as exc:
                    self._close_rpc(rpc, last_activity is not None)
                    rpc = None
                    rpc_client_id = ""
                    last_activity = None
                    retry_at = time.monotonic() + self.RETRY_INTERVAL
                    _LOG.info("Falha ao atualizar Discord Rich Presence (%s): %s",
                              type(exc).__name__, exc)
                    self._status_update(
                        False, ("A conexão com o Discord foi perdida; tentando novamente. "
                                f"({type(exc).__name__})"), generation)

            # State changes wake this worker directly. While idle, sleep until
            # the next allowed update or retry instead of polling twice a second.
            wait_for = None
            if rpc is not None and enabled and activity != last_activity:
                wait_for = max(0.05, next_update_at - time.monotonic())
            elif rpc is None and retry_at > time.monotonic():
                wait_for = max(0.05, retry_at - time.monotonic())
            self._wake.wait(wait_for)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
