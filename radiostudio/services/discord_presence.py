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
from ..i18n import translate


_APP_ID_PATTERN = re.compile(r"^[0-9]{15,25}$")
_PRESENCE_PLACEHOLDER_PATTERN = re.compile(r"%([a-z_]+)", re.IGNORECASE)
_DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
ACTIVITY_TYPES = (0, 2, 3, 5)
STATUS_DISPLAY_TYPES = (0, 1, 2)
PRESENCE_PLACEHOLDERS = {"title", "artist", "album", "station", "status"}
DISCORD_IMAGE_URL_LIMIT = 256
DISCORD_BUTTON_URL_LIMIT = 512
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
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Informe um endereço HTTP ou HTTPS válido.")
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
    if parsed.netloc.endswith(":"):
        raise ValueError("Informe um endereço HTTP ou HTTPS válido.")
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
        # Browsers treat numeric dotted hosts as IPv4-like addresses, so don't
        # let malformed forms such as 999.1.1.1 pass as ordinary DNS names.
        if re.fullmatch(r"[0-9.]+", hostname):
            raise ValueError("Informe um endereço HTTP ou HTTPS válido.")
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Informe um endereço HTTP ou HTTPS válido.") from exc
        labels = ascii_hostname.split(".")
        if ("." not in ascii_hostname or len(ascii_hostname) > 253
                or any(not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels)):
            raise ValueError("Informe o domínio público da rádio.")
        # Browsers accept legacy hexadecimal IPv4 forms such as 0x7f.0.0.1.
        # Reject numeric-only host labels so private addresses cannot bypass
        # the normal ipaddress checks through URL canonicalization.
        if all(re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)", label)
               for label in labels):
            raise ValueError("Informe um endereço HTTP ou HTTPS válido.")
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


def validate_public_button_url(value: str) -> str:
    """Validate a public URL that fits Discord's Rich Presence button limit."""
    value = validate_public_radio_url(value)
    if value and len(value) > DISCORD_BUTTON_URL_LIMIT:
        raise ValueError("O link de cada botão deve ter no máximo 512 caracteres.")
    return value


def unknown_presence_placeholders(value: str) -> list[str]:
    """Return unsupported Rich Presence template tokens, preserving order."""
    found = []
    for match in _PRESENCE_PLACEHOLDER_PATTERN.finditer(str(value or "")):
        token = match.group(1).lower()
        if token not in PRESENCE_PLACEHOLDERS and token not in found:
            found.append(token)
    return found


def playback_start_timestamp(position_ms: int, *, now: float | None = None,
                              previous_start: int = 0, force_reset: bool = False) -> int:
    """Keep Discord's elapsed timer aligned with the player's current position."""
    wall_time = time.time() if now is None else float(now)
    position_seconds = max(0, int(position_ms)) / 1000
    candidate = int(wall_time - position_seconds)
    if force_reset or previous_start <= 0 or abs(candidate - previous_start) >= 2:
        return max(0, candidate)
    return previous_start


def _clean_presence_text(value: object) -> str:
    """Collapse whitespace and remove control characters from RPC text."""
    text = str(value or "")
    text = "".join(
        " " if char.isspace() else char
        for char in text
        if char.isspace() or ord(char) >= 32 and not 127 <= ord(char) <= 159)
    return re.sub(r"\s+", " ", text).strip()


def _format_presence_template(template: str, values: dict[str, str], limit: int = 128) -> str:
    text = str(template or "")

    def replace(match):
        token = match.group(1).lower()
        return values.get(token, match.group(0))

    text = _PRESENCE_PLACEHOLDER_PATTERN.sub(replace, text)
    # Metadata may contain newlines, tabs, or repeated whitespace from tags.
    # Keep Discord's compact activity lines readable and predictable.
    text = _clean_presence_text(text)
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
    title = _clean_presence_text(title)
    artist = _clean_presence_text(artist)
    album = _clean_presence_text(album)
    station = _clean_presence_text(station) or "RadioStudio Python"
    if not broadcasting and not playing and not paused:
        return {}

    if activity_type not in ACTIVITY_TYPES:
        activity_type = 2
    if status_display_type not in STATUS_DISPLAY_TYPES:
        status_display_type = 0
    visible_track = bool(share_track and (playing or paused))
    if not visible_track:
        title = artist = album = ""
    status_key = "No ar" if broadcasting else "Pausado" if paused else "Tocando"
    status = translate(status_key)
    values = {"title": title, "artist": artist, "album": album,
              "station": station, "status": status}
    fallback_details = "🎵 " + title if visible_track and title else "📻 RadioStudio Python"
    template_uses_track_data = bool(re.search(r"%(title|artist|album)\b", details_template,
                                              re.IGNORECASE))
    details = (_format_presence_template(details_template, values)
               if visible_track or not template_uses_track_data else "")
    details = details or fallback_details[:128]
    # Discord rejects activity details/state/tooltips shorter than two
    # characters. A malformed optional field can reject the whole activity,
    # so keep a valid details fallback and omit invalid optional text.
    if len(details) < 2:
        details = fallback_details[:128]
    state = _format_presence_template(state_template, values)
    display_name = _clean_presence_text(activity_name)[:128]
    display_name = display_name or "RadioStudio Python Sirius"
    payload = {"name": display_name, "activity_type": activity_type,
               "status_display_type": status_display_type, "details": details}
    if len(state) >= 2:
        payload["state"] = state
    image_key = str(large_image or "").strip()
    cover_version = str(cover_version or "")
    if (visible_track and cover_url
            and re.fullmatch(r"[a-fA-F0-9]{1,32}", cover_version)):
        try:
            public_cover_url = validate_public_cover_url(cover_url)
        except ValueError:
            # A bad optional cover should not invalidate otherwise valid
            # playback details or buttons (for example, when loading old state).
            public_cover_url = ""
        if public_cover_url:
            image_url = f"{public_cover_url}?rs_cover={cover_version.lower()}"
            if len(image_url) <= DISCORD_IMAGE_URL_LIMIT:
                image_key = image_url
    if image_key:
        payload["large_image"] = image_key[:DISCORD_IMAGE_URL_LIMIT]
        tooltip = _clean_presence_text(large_text)
        if len(tooltip) >= 2:
            payload["large_text"] = tooltip[:128]
    # When track sharing is off, playback timestamps can still reveal a track's
    # progress and duration. Keep them private along with its title and cover.
    if visible_track and playing and not paused and started_at is not None:
        payload["start"] = max(0, int(started_at * 1000))
        if ends_at is not None and int(ends_at) > int(started_at):
            payload["end"] = int(ends_at * 1000)
    # public_url remains as a compatibility alias for settings saved by earlier builds.
    first_url = button1_url or public_url
    buttons = []
    for label, url, fallback_label in (
            (button1_label, first_url, translate("Ouvir a rádio")),
            (button2_label, button2_url, translate("Acessar site"))):
        try:
            radio_url = validate_public_button_url(url)
        except ValueError:
            # A legacy button URL outside Discord's contract must not reject
            # the activity or suppress a separate valid button.
            continue
        if radio_url:
            button_label = (_clean_presence_text(label)
                            or _clean_presence_text(fallback_label))[:32]
            if button_label:
                buttons.append({"label": button_label, "url": radio_url})
    if buttons:
        payload["buttons"] = buttons[:2]
    return payload


class DiscordPresenceService(QObject):
    """Owns Discord IPC in a worker thread and exposes only a small Qt API."""

    availability_changed = Signal(bool, str)
    MIN_UPDATE_INTERVAL = 15.0
    # Keep state changes and clear calls below Discord's five-update/20s cap.
    MIN_RPC_CALL_INTERVAL = 4.1
    HEARTBEAT_INTERVAL = 60.0
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
        self._activity_revision = 0
        self._urgent_activity_update = False
        self._generation = 0
        self._available = False
        self._connected = False
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
    def connected(self):
        """Whether the worker currently owns an open Discord IPC connection."""
        with self._lock:
            return self._connected

    @property
    def enabled(self):
        """Whether Rich Presence is enabled in the active application state."""
        with self._lock:
            return self._enabled

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
        verify = bool(verify and client_id)
        with self._lock:
            changed_client = client_id != self._client_id
            changed_state = (changed_client or enabled != self._enabled
                             or verify != self._verification_requested)
            self._client_id = client_id
            self._enabled = enabled
            self._verification_requested = verify
            if not enabled:
                if self._activity:
                    self._activity_revision += 1
                self._activity = {}
            if changed_client:
                if self._activity:
                    self._activity_revision += 1
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

    def set_activity(self, activity: dict, *, urgent: bool = False) -> None:
        updated = dict(activity) if isinstance(activity, dict) else {}
        with self._lock:
            if updated == self._activity:
                return
            self._activity = updated
            self._activity_revision += 1
            self._urgent_activity_update = self._urgent_activity_update or bool(urgent)
        self._wake.set()

    def _set_status_locked(self, available: bool, status: str) -> None:
        changed = self._available != available or self._status != status
        self._available = available
        self._status = status
        if changed:
            self.availability_changed.emit(available, status)

    def _set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = bool(connected)

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
        last_rpc_call_at = None
        next_update_at = 0.0
        next_heartbeat_at = 0.0
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
                    activity_revision = self._activity_revision
                    urgent_activity_update = False
                    generation = self._generation
                else:
                    client_id = self._client_id
                    enabled = self._enabled
                    verify_requested = self._verification_requested
                    activity = dict(self._activity)
                    activity_revision = self._activity_revision
                    urgent_activity_update = self._urgent_activity_update
                    self._urgent_activity_update = False
                    generation = self._generation

            if self._stopping:
                self._close_rpc(rpc, last_activity is not None)
                self._set_connected(False)
                return

            if rpc is not None and (client_id != rpc_client_id or generation != rpc_generation):
                self._close_rpc(rpc, last_activity is not None)
                self._set_connected(False)
                rpc = None
                rpc_client_id = ""
                last_activity = None
                last_rpc_call_at = None
                next_update_at = 0.0
                next_heartbeat_at = 0.0

            if not client_id:
                if rpc is not None:
                    self._close_rpc(rpc, last_activity is not None)
                    self._set_connected(False)
                    rpc = None
                    last_activity = None
                self._wake.wait()
                continue

            if not enabled and not verify_requested:
                if rpc is not None:
                    self._close_rpc(rpc, last_activity is not None)
                    self._set_connected(False)
                    rpc = None
                    rpc_client_id = ""
                    last_activity = None
                # Presence is optional; avoid repeatedly opening Discord IPC
                # when the user has disabled it. The Settings button requests
                # a single connection probe when needed.
                self._wake.wait()
                continue

            now = time.monotonic()
            if verify_requested:
                # An explicit check should not sit behind the automatic retry
                # backoff after the user has asked to test Discord now.
                retry_at = 0.0
                if enabled:
                    # Consume the one-shot request before connecting. If that
                    # attempt fails, the normal retry delay must still apply.
                    with self._lock:
                        if generation == self._generation:
                            self._verification_requested = False
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
                        # The handshake may take several seconds. Publish the
                        # latest track/privacy state, not the earlier snapshot.
                        activity = dict(self._activity)
                        activity_revision = self._activity_revision
                    if not still_current:
                        self._close_rpc(candidate, False)
                        continue
                    # Never publish a synthetic activity just to probe IPC or
                    # while playback is idle. It can briefly appear to friends.
                    if enabled and activity:
                        candidate_activity_set = True
                        self._update_rpc(candidate, activity)
                        last_activity = activity
                        last_rpc_call_at = time.monotonic()
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
                        self._close_rpc(candidate, False)
                        candidate = None
                        self._set_connected(False)
                        with self._lock:
                            if generation == self._generation and not self._stopping:
                                self._verification_requested = False
                        retry_at = 0.0
                        self._status_update(
                            True, "Conexão com o Discord verificada; Rich Presence desativada.",
                            generation)
                        continue
                    rpc = candidate
                    self._set_connected(True)
                    rpc_client_id = client_id
                    rpc_generation = generation
                    if verify_requested:
                        with self._lock:
                            if generation == self._generation and not self._stopping:
                                self._verification_requested = False
                    connected_at = time.monotonic()
                    next_update_at = (connected_at + self.MIN_UPDATE_INTERVAL
                                      if last_activity is not None else connected_at)
                    # Keep an idle IPC connection observable too. Discord can
                    # close its pipe while the user is not playing; a periodic
                    # clear is invisible but confirms the connection is alive.
                    next_heartbeat_at = connected_at + self.HEARTBEAT_INTERVAL
                    retry_at = 0.0
                    status = ("Discord conectado; Rich Presence registrada."
                              if last_activity is not None else
                              "Discord conectado; Rich Presence aguardando reprodução.")
                    self._status_update(True, status, generation)
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
                            last_rpc_call_at = time.monotonic()
                            next_update_at = 0.0
                            next_heartbeat_at = 0.0
                    elif not activity:
                        if last_activity is not None:
                            call_due_at = (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                                           if last_rpc_call_at is not None else now)
                            if now >= call_due_at:
                                rpc.clear()
                                last_activity = None
                                last_rpc_call_at = time.monotonic()
                                next_update_at = 0.0
                                next_heartbeat_at = (last_rpc_call_at
                                                     + self.HEARTBEAT_INTERVAL)
                                with self._lock:
                                    still_idle = (activity_revision == self._activity_revision
                                                  and not self._activity)
                                if still_idle:
                                    self._status_update(
                                        True,
                                        "Discord conectado; Rich Presence aguardando reprodução.",
                                        generation)
                        elif now >= max(
                                next_heartbeat_at,
                                (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                                 if last_rpc_call_at is not None else now)):
                            # A clear request is also a lightweight liveness
                            # check when there is no activity to refresh.
                            rpc.clear()
                            last_rpc_call_at = time.monotonic()
                            next_heartbeat_at = last_rpc_call_at + self.HEARTBEAT_INTERVAL
                    else:
                        call_due_at = (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                                       if last_rpc_call_at is not None else now)
                        rate_ready = now >= call_due_at
                        activity_changed = activity != last_activity
                        update_due = (now >= next_update_at or urgent_activity_update)
                        heartbeat_due = now >= next_heartbeat_at
                        if activity_changed and not rate_ready and urgent_activity_update:
                            # Keep urgency pending while coalescing rapid state changes.
                            with self._lock:
                                if activity_revision == self._activity_revision:
                                    self._urgent_activity_update = True
                        if ((activity_changed and rate_ready and update_due)
                                or heartbeat_due):
                            with self._lock:
                                if activity_revision != self._activity_revision:
                                    continue
                            had_registered_activity = last_activity is not None
                            self._update_rpc(rpc, activity)
                            last_activity = activity
                            sent_at = time.monotonic()
                            last_rpc_call_at = sent_at
                            next_update_at = sent_at + self.MIN_UPDATE_INTERVAL
                            next_heartbeat_at = sent_at + self.HEARTBEAT_INTERVAL
                            if not had_registered_activity:
                                self._status_update(
                                    True, "Discord conectado; Rich Presence registrada.",
                                    generation)
                except Exception as exc:
                    self._close_rpc(rpc, last_activity is not None)
                    self._set_connected(False)
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
            if rpc is not None and enabled and not activity and last_activity is not None:
                deadline = (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                            if last_rpc_call_at is not None else time.monotonic())
                wait_for = max(0.05, deadline - time.monotonic())
            elif rpc is not None and enabled and activity and activity != last_activity:
                call_due_at = (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                               if last_rpc_call_at is not None else 0.0)
                deadline = call_due_at if urgent_activity_update else max(
                    call_due_at, next_update_at)
                wait_for = max(0.05, deadline - time.monotonic())
            elif rpc is not None and enabled:
                call_due_at = (last_rpc_call_at + self.MIN_RPC_CALL_INTERVAL
                               if last_rpc_call_at is not None else 0.0)
                wait_for = max(0.05, max(next_heartbeat_at, call_due_at)
                               - time.monotonic())
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
