"""Persistence operations for the main window."""

import logging
import math
import os
import shutil
import uuid

from ._shared import *
from ..config import DISCORD_APPLICATION_ID
from ..runtime_safety import guarded
from ..services.audio_paths import same_audio_path
from ..services.audio_inputs import INPUT_CHANNELS
from ..services.discord_presence import validate_public_cover_url, validate_public_radio_url
from ..services.control_api import API_DEFAULT_PORT, parse_allowed_origins
from ..services.process_priority import apply_process_priority, normalize_process_priority
from ..services.recording import sanitize_recording_schedules, SUPPORTED_BITRATES
from ..services.schedule_engine import normalize_event
from .recording_panel import default_recording_folder


def _tuple_tree(value):
    if isinstance(value, list):
        return tuple(_tuple_tree(item) for item in value)
    return value


def bounded_int(value, default, low, high):
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return min(high, max(low, parsed))


def bounded_float(value, default, low, high):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return min(high, max(low, parsed))


def _state_bool(state, key, default=False):
    """Read a saved boolean, defaulting safely when a present value is malformed."""
    if key not in state:
        return default
    value = state[key]
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return False


class PersistenceMixin:
    @guarded
    def capture_playback(self):
        mix_state = self.mix_player.playbackState()
        use_mix = (self.mix_target_index >= 0 and self.mix_gain > self.main_gain
                   and mix_state in (QMediaPlayer.PlayingState, QMediaPlayer.PausedState))
        active_player = self.mix_player if use_mix else self.player
        path = (self.tracks[self.mix_target_index] if use_mix
                and 0 <= self.mix_target_index < len(self.tracks)
                else self.original_media_path(active_player))
        if path:
            active_index = (self.mix_target_index if use_mix
                            and 0 <= self.mix_target_index < len(self.tracks)
                            else getattr(self, "current_index", -1))
            self.last_playback = {
                "path": path,
                "index": active_index,
                "position_ms": max(0, active_player.position()),
                "was_playing": active_player.playbackState() == QMediaPlayer.PlayingState,
            }

    @guarded
    def restore_playback(self):
        if not self.resume_on_startup:
            self.last_playback["was_playing"] = False
            self.save_state()
            return
        if not self.last_playback.get("was_playing"):
            return
        path = self.last_playback.get("path", "")
        if not path or not Path(path).is_file():
            self.last_playback["was_playing"] = False
            self.log_event("error", f"Não foi possível retomar o áudio: {path or 'arquivo ausente'}")
            self.save_state()
            return
        index = -1
        try:
            saved_index = int(self.last_playback.get("index", -1))
        except (TypeError, ValueError, OverflowError):
            saved_index = -1
        if (0 <= saved_index < len(self.tracks)
                and same_audio_path(self.tracks[saved_index], path)):
            index = saved_index
        else:
            index = next((i for i, candidate in enumerate(self.tracks)
                          if same_audio_path(candidate, path)), -1)
        if index < 0:
            self.tracks.append(path)
            self.track_sources.append("manual")
            self.refresh_playlist()
            index = len(self.tracks) - 1
        self.play_index(index, self.last_playback.get("position_ms", 0))

    @guarded
    def restore_broadcast(self):
        if self.auto_start_broadcast and self.broadcast_was_running:
            self.broadcast_panel.start()

    @guarded
    def load_state(self):
        if not STATE_FILE.exists():
            logging.getLogger("radiostudio").info(
                "Arquivo de configurações não encontrado; usando valores padrão: %s", STATE_FILE)
            return
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("O arquivo de estado precisa conter um objeto JSON.")
            saved_language = state.get("language_preference")
            if isinstance(saved_language, str):
                from ..i18n import normalize_language
                self.language_preference = normalize_language(saved_language) or "system"
            saved_tracks = state.get("tracks", [])
            if not isinstance(saved_tracks, list):
                saved_tracks = []
            self.tracks = [path for path in saved_tracks if isinstance(path, str) and path.strip()]
            saved_recording = state.get("recording", {})
            if isinstance(saved_recording, dict):
                saved_folder = saved_recording.get("folder")
                saved_format = saved_recording.get("format", "mp3")
                saved_bitrate = saved_recording.get("bitrate", 192)
                if type(saved_bitrate) is not int or saved_bitrate not in SUPPORTED_BITRATES:
                    saved_bitrate = 192
                self.recording_config = {
                    "folder": (saved_folder.strip() if isinstance(saved_folder, str)
                               and saved_folder.strip() else str(default_recording_folder())),
                    "format": (saved_format if isinstance(saved_format, str)
                               and saved_format in {"mp3", "wav"} else "mp3"),
                    "bitrate": saved_bitrate,
                    "auto_on_broadcast": saved_recording.get("auto_on_broadcast") is True,
                }
            self.recording_schedules = sanitize_recording_schedules(
                state.get("recording_schedules", []))
            saved_api = state.get("control_api", {})
            if isinstance(saved_api, dict):
                saved_port = saved_api.get("port", API_DEFAULT_PORT)
                if type(saved_port) is not int or not 1 <= saved_port <= 65535:
                    saved_port = API_DEFAULT_PORT
                saved_auth_mode = saved_api.get("auth_mode", "token")
                if not isinstance(saved_auth_mode, str) or saved_auth_mode not in {"basic", "token"}:
                    saved_auth_mode = "token"
                try:
                    saved_origins = list(parse_allowed_origins(
                        saved_api.get("allowed_origins", [])))
                except ValueError:
                    saved_origins = []
                    logging.getLogger("radiostudio").warning(
                        "Origens CORS inválidas foram ignoradas nas configurações salvas")
                self.control_api_config = {
                    "enabled": saved_api.get("enabled") is True,
                    "port": saved_port,
                    "auth_mode": saved_auth_mode,
                    "allowed_origins": saved_origins,
                }
            saved_sources = state.get("track_sources", [])
            if not isinstance(saved_sources, list):
                saved_sources = []
            valid_sources = {"manual", "autodj", "schedule"}
            self.track_sources = [source if isinstance(source, str) and source in valid_sources else "manual"
                                  for source in (saved_sources + ["manual"] * len(self.tracks))
                                  [:len(self.tracks)]]
            saved_queue_signature = state.get("autodj_queue_signature")
            self.autodj_last_signature = (
                _tuple_tree(saved_queue_signature)
                if isinstance(saved_queue_signature, list) else None)
            saved_events = state.get("events", [])
            self.events = []
            if isinstance(saved_events, list):
                for event in saved_events:
                    normalized_event = normalize_event(event)
                    if normalized_event is not None:
                        self.events.append(normalized_event)
            saved_carts = state.get("carts", [])
            if not isinstance(saved_carts, list):
                saved_carts = []
            self.carts = (saved_carts + [""] * 25)[:25]
            self.carts = [path if isinstance(path, str) else "" for path in self.carts]
            self.main_device_key = str(state.get("main_device_key", "") or "")
            self.preview_device_key = str(state.get("preview_device_key", "") or "")
            saved_input_devices = state.get("input_device_keys", {})
            if isinstance(saved_input_devices, dict):
                self.input_device_keys = {
                    channel: (str(saved_input_devices.get(channel, "") or "")
                              if isinstance(saved_input_devices.get(channel, ""), str) else "")
                    for channel in INPUT_CHANNELS}
            self.mic_music_level_percent = bounded_int(
                state.get("mic_music_level_percent", 35), 35, 0, 100)
            self.dynamic_mic_duck = _state_bool(state, "dynamic_mic_duck", False)
            self.speech_music_level_percent = bounded_int(
                state.get("speech_music_level_percent", 20), 20, 0, 100)
            self.speech_threshold_db = bounded_float(
                state.get("speech_threshold_db", -38), -38.0, -80.0, -10.0)
            self.local_master_enabled = _state_bool(state, "local_master_enabled", True)
            self.monitor_volume = bounded_float(
                state.get("monitor_volume", state.get("master_volume", 0.8)),
                0.8, 0.0, 1.0)
            self.master_live_volume = bounded_float(
                state.get("master_live_volume", 1.0), 1.0, 0.0, 1.0)
            self.monitor_volume_slider.setValue(round(self.monitor_volume * 100))
            self.master_live_volume_slider.setValue(round(self.master_live_volume * 100))
            self.broadcast.set_master_gain(self.master_live_volume)
            self.apply_monitor_output_gains()
            self.minimize_to_tray = _state_bool(state, "minimize_to_tray", True)
            self.resume_on_startup = _state_bool(state, "resume_on_startup", True)
            self.auto_start_broadcast = _state_bool(state, "auto_start_broadcast", False)
            self.process_priority = normalize_process_priority(
                state.get("process_priority", "normal"))
            priority_applied, priority_error = apply_process_priority(self.process_priority)
            if not priority_applied:
                logging.getLogger("radiostudio").warning(
                    "Não foi possível restaurar a prioridade do processo: %s", priority_error)
            backup_directory = state.get("backup_directory", "")
            self.backup_directory = (backup_directory[:2048]
                                     if isinstance(backup_directory, str) else "")
            saved_periodic_backup = state.get("backup_periodic_enabled", False)
            self.backup_periodic_enabled = (saved_periodic_backup
                                            if isinstance(saved_periodic_backup, bool) else False)
            valid_backup_intervals = {3600, 21600, 43200, 86400, 604800}
            self.backup_interval_seconds = bounded_int(
                state.get("backup_interval_seconds", 86400), 86400, 1, 604800)
            if self.backup_interval_seconds not in valid_backup_intervals:
                self.backup_interval_seconds = 86400
            self.backup_retention = bounded_int(
                state.get("backup_retention", 10), 10, 1, 500)
            self.backup_last_run = bounded_float(
                state.get("backup_last_run", 0), 0.0, 0.0, time.time() + 31536000)
            if self.backup_last_run > time.time():
                self.backup_last_run = 0.0
            self.broadcast_was_running = _state_bool(state, "broadcast_was_running", False)
            # The application ID belongs to RadioStudio; users do not need to
            # create or configure their own Discord Developer application.
            self.discord_client_id = DISCORD_APPLICATION_ID
            self.discord_enabled = _state_bool(state, "discord_enabled", False)
            self.discord_share_track = _state_bool(state, "discord_share_track", True)
            self.discord_use_track_cover = _state_bool(state, "discord_use_track_cover", False)
            try:
                self.discord_cover_url = validate_public_cover_url(
                    state.get("discord_cover_url", ""))
            except (TypeError, ValueError):
                self.discord_cover_url = ""
                self.discord_use_track_cover = False
            try:
                self.discord_public_url = validate_public_radio_url(
                    state.get("discord_public_url", ""))
            except (TypeError, ValueError):
                self.discord_public_url = ""
            self.discord_activity_name = str(
                state.get("discord_activity_name", "RadioStudio Python Sirius")
                or "RadioStudio Python Sirius")[:128]
            try:
                activity_type = int(state.get("discord_activity_type", 2))
            except (TypeError, ValueError, OverflowError):
                activity_type = 2
            self.discord_activity_type = activity_type if activity_type in {0, 2, 3, 5} else 2
            try:
                display_type = int(state.get("discord_status_display_type", 0))
            except (TypeError, ValueError, OverflowError):
                display_type = 0
            self.discord_status_display_type = display_type if display_type in {0, 1, 2} else 0
            self.discord_details_template = str(
                state.get("discord_details_template", "🎵 %title") or "🎵 %title")[:128]
            self.discord_state_template = str(
                state.get("discord_state_template", "%artist · %station · %status")
                or "%artist · %station · %status")[:128]
            self.discord_large_text = str(
                state.get("discord_large_text", "RadioStudio Python Sirius")
                or "RadioStudio Python Sirius")[:128]
            self.discord_button1_label = str(
                state.get("discord_button1_label", "Ouvir a rádio") or "Ouvir a rádio")[:32]
            self.discord_button2_label = str(state.get("discord_button2_label", "") or "")[:32]
            try:
                self.discord_button2_url = validate_public_radio_url(
                    state.get("discord_button2_url", ""))
            except (TypeError, ValueError):
                self.discord_button2_url = ""
            self.discord_large_image = "radiostudio"
            saved_autodj = state.get("autodj", {})
            if isinstance(saved_autodj, dict):
                self.autodj_config.update(saved_autodj)
            self.autodj_config["enabled"] = _state_bool(self.autodj_config, "enabled", False)
            self.autodj_config["remove_played_from_queue"] = _state_bool(
                self.autodj_config, "remove_played_from_queue", False)
            for key, default, maximum in (("artist_cooldown_minutes", 60, 10080),
                                          ("track_cooldown_minutes", 360, 43200),
                                          ("queue_ahead", 4, 30)):
                try:
                    value = int(self.autodj_config.get(key, default))
                except (TypeError, ValueError, OverflowError):
                    value = default
                self.autodj_config[key] = min(maximum, max(1 if key == "queue_ahead" else 0, value))
            saved_programs = state.get("autodj_programs", [])
            self.autodj_programs = []
            if isinstance(saved_programs, list):
                for program in saved_programs:
                    if isinstance(program, dict):
                        normalized_program = dict(program)
                        normalized_program["enabled"] = _state_bool(
                            program, "enabled", True)
                        self.autodj_programs.append(normalized_program)
            self.fade_in_ms = bounded_int(state.get("fade_in_ms", 0), 0, 0, 30000)
            self.fade_out_ms = bounded_int(state.get("fade_out_ms", 0), 0, 0, 30000)
            self.mix_ms = bounded_int(state.get("mix_ms", 0), 0, 0, 30000)
            self.auto_transitions_enabled = _state_bool(state, "auto_transitions_enabled", False)
            self.trim_silence = _state_bool(state, "trim_silence", False)
            self.silence_threshold_db = bounded_float(
                state.get("silence_threshold_db", -42), -42.0, -80.0, -10.0)
            self.silence_hold_ms = bounded_int(
                state.get("silence_hold_ms", 1200), 1200, 200, 10000)
            self.set_player_controls_position(state.get("player_controls_position", "bottom"))
            self.waveform_style = (state.get("waveform_style")
                                   if isinstance(state.get("waveform_style"), str) else "bars")
            self.on_air.set_waveform_style(self.waveform_style)
            self.waveform_visualizers_enabled = (
                state.get("waveform_visualizers_enabled")
                if isinstance(state.get("waveform_visualizers_enabled"), bool) else True)
            self.on_air.set_waveform_visualizers_enabled(self.waveform_visualizers_enabled)
            playback = state.get("playback", {})
            if isinstance(playback, dict):
                try:
                    position_ms = max(0, int(playback.get("position_ms", 0)))
                except (TypeError, ValueError, OverflowError):
                    position_ms = 0
                self.last_playback = {
                    "path": (playback.get("path", "")
                             if isinstance(playback.get("path", ""), str) else ""),
                    "index": bounded_int(playback.get("index", -1), -1, -1, 1_000_000),
                    "position_ms": position_ms,
                    "was_playing": _state_bool(playback, "was_playing", False),
                }
            saved_metadata = state.get("station_metadata", {})
            self.station_metadata = {**DEFAULT_STATION_METADATA,
                                     **(saved_metadata if isinstance(saved_metadata, dict) else {})}
            self.station_metadata = {key: str(value or "")
                                     for key, value in self.station_metadata.items()}
            metadata_update_mode = state.get("metadata_update_mode", "current")
            self.metadata_update_mode = (
                metadata_update_mode if isinstance(metadata_update_mode, str) and metadata_update_mode in
                {"current", "track_change", "interval"} else "current")
            self.metadata_update_interval_seconds = bounded_int(
                state.get("metadata_update_interval_seconds", 30), 30, 5, 3600)
            saved_edit_target = state.get("metadata_edit_default_target", "internal")
            self.metadata_edit_default_target = (
                saved_edit_target if saved_edit_target in {"internal", "file"} else "internal")
            self.broadcast.set_metadata_update_preferences(
                self.metadata_update_mode, self.metadata_update_interval_seconds)
            explorer_folder = state.get("explorer_folder", "")
            if not isinstance(explorer_folder, str):
                explorer_folder = ""
            if explorer_folder and Path(explorer_folder).is_dir():
                self.open_explorer_folder(explorer_folder)
            self.apply_audio_devices()
            saved_profiles = state.get("broadcast_profiles", [])
            saved_local = state.get("local_broadcast", {})
            self.broadcast_panel.load_config(
                [profile for profile in saved_profiles if isinstance(profile, dict)]
                if isinstance(saved_profiles, list) else [],
                saved_local if isinstance(saved_local, dict) else {})
            self.refresh_playlist()
            self.refresh_schedule()
            self.refresh_carts()
            self.autodj_panel.load_settings(self.autodj_config)
            self.autodj_panel.refresh_programs()
            self.autodj_panel.refresh_libraries()
            self.library_panel.refresh_libraries()
            self.library_panel.refresh_organization()
            self.library_panel.refresh_catalog()
            # Older state files did not store the selector signature. Seed it
            # from the current block so the first startup fill extends the
            # saved queue instead of discarding its already-selected tracks.
            if (self.autodj_last_signature is None
                    and "autodj" in self.track_sources):
                try:
                    self.autodj_last_signature = self.current_autodj_signature()
                except Exception:
                    logging.getLogger("radiostudio").exception(
                        "Não foi possível restaurar a assinatura da fila do AutoDJ")
            self.autodj_panel.refresh_history()
            self.library_panel.refresh_history()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logging.getLogger("radiostudio").error(
                "Não foi possível carregar dados salvos de %s: %s", STATE_FILE, exc)
            recovery_path = None
            try:
                if STATE_FILE.is_file():
                    recovery_path = STATE_FILE.with_name(
                        f"{STATE_FILE.stem}.recovery-{uuid.uuid4().hex[:8]}{STATE_FILE.suffix}")
                    shutil.copy2(STATE_FILE, recovery_path)
            except OSError:
                logging.getLogger("radiostudio").exception(
                    "Não foi possível preservar uma cópia dos dados salvos inválidos")
                recovery_path = None
            message = translate("Não foi possível carregar os dados salvos:") + f"\n{exc}"
            if recovery_path is not None:
                logging.getLogger("radiostudio").warning(
                    "Cópia recuperável das configurações preservada em %s", recovery_path)
                message += ("\n\n" + translate("Uma cópia foi preservada em:")
                            + f"\n{recovery_path}")
            show_warning(self, translate("Configuração"), message)

    @guarded
    def save_state(self):
        if getattr(self, "_skip_state_save", False):
            return
        if not self.is_quitting:
            self.capture_playback()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        recording_config = getattr(self, "recording_config", {})
        recording_format = recording_config.get("format", "mp3")
        if not isinstance(recording_format, str) or recording_format not in {"mp3", "wav"}:
            recording_format = "mp3"
        recording_bitrate = recording_config.get("bitrate", 192)
        if type(recording_bitrate) is not int or recording_bitrate not in SUPPORTED_BITRATES:
            recording_bitrate = 192
        recording_folder = recording_config.get("folder", "")
        if not isinstance(recording_folder, str):
            recording_folder = ""
        data = {"tracks": self.tracks, "track_sources": self.track_sources,
                "recording": {
                    "folder": recording_folder,
                    "format": recording_format,
                    "bitrate": recording_bitrate,
                    "auto_on_broadcast": bool(recording_config.get(
                        "auto_on_broadcast", False)),
                },
                "recording_schedules": getattr(self, "recording_schedules", []),
                "events": self.events, "carts": self.carts,
                "autodj": self.autodj_config, "autodj_programs": self.autodj_programs,
                "autodj_queue_signature": self.autodj_last_signature,
                "main_device_key": self.main_device_key,
                "preview_device_key": self.preview_device_key,
                "input_device_keys": self.input_device_keys,
                "mic_music_level_percent": self.mic_music_level_percent,
                "dynamic_mic_duck": self.dynamic_mic_duck,
                "speech_music_level_percent": self.speech_music_level_percent,
                "speech_threshold_db": self.speech_threshold_db,
                "local_master_enabled": self.local_master_enabled,
                "monitor_volume": self.monitor_volume,
                "master_volume": self.monitor_volume,
                "master_live_volume": self.master_live_volume,
                "minimize_to_tray": self.minimize_to_tray,
                "resume_on_startup": self.resume_on_startup,
                "auto_start_broadcast": self.auto_start_broadcast,
                "process_priority": normalize_process_priority(self.process_priority),
                "backup_directory": self.backup_directory,
                "backup_periodic_enabled": self.backup_periodic_enabled,
                "backup_interval_seconds": self.backup_interval_seconds,
                "backup_retention": self.backup_retention,
                "backup_last_run": self.backup_last_run,
                "control_api": {
                    "enabled": bool(self.control_api_config.get("enabled", False)),
                    "port": int(self.control_api_config.get("port", API_DEFAULT_PORT)),
                    "auth_mode": ("basic" if self.control_api_config.get("auth_mode") == "basic"
                                  else "token"),
                    "allowed_origins": list(self.control_api_config.get(
                        "allowed_origins", [])),
                },
                "language_preference": self.language_preference,
                "broadcast_was_running": self.broadcast_was_running,
                "discord_client_id": DISCORD_APPLICATION_ID,
                "discord_enabled": self.discord_enabled,
                "discord_share_track": self.discord_share_track,
                "discord_use_track_cover": self.discord_use_track_cover,
                "discord_cover_url": self.discord_cover_url,
                "discord_public_url": self.discord_public_url,
                "discord_large_image": "radiostudio",
                "discord_activity_name": self.discord_activity_name,
                "discord_activity_type": self.discord_activity_type,
                "discord_status_display_type": self.discord_status_display_type,
                "discord_details_template": self.discord_details_template,
                "discord_state_template": self.discord_state_template,
                "discord_large_text": self.discord_large_text,
                "discord_button1_label": self.discord_button1_label,
                "discord_button2_label": self.discord_button2_label,
                "discord_button2_url": self.discord_button2_url,
                "fade_in_ms": self.fade_in_ms, "fade_out_ms": self.fade_out_ms,
                "auto_transitions_enabled": self.auto_transitions_enabled,
                "mix_ms": self.mix_ms, "trim_silence": self.trim_silence,
                "silence_threshold_db": self.silence_threshold_db,
                "silence_hold_ms": self.silence_hold_ms,
                "player_controls_position": self.player_controls_position,
                "waveform_style": self.waveform_style,
                "waveform_visualizers_enabled": self.waveform_visualizers_enabled,
                "playback": self.last_playback,
                "station_metadata": self.station_metadata,
                "metadata_update_mode": self.metadata_update_mode,
                "metadata_update_interval_seconds": self.metadata_update_interval_seconds,
                "metadata_edit_default_target": getattr(
                    self, "metadata_edit_default_target", "internal"),
                "explorer_folder": self.explorer_folder,
                "broadcast_profiles": self.broadcast_panel.profiles,
                "local_broadcast": self.broadcast_panel.local_config()}
        temporary = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        try:
            # This file is machine state, not a user-edited document. Compact
            # JSON lowers serialization time and disk writes, especially when
            # the playback checkpoint updates it during a long session.
            temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                  encoding="utf-8")
            os.replace(temporary, STATE_FILE)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("radiostudio").warning(
                    "Não foi possível remover o arquivo temporário de configurações")

    @guarded
    def closeEvent(self, event):
        if (not self.is_quitting and self.minimize_to_tray and self.tray_icon
                and self.tray_icon.isVisible()):
            self.broadcast_was_running = bool(self.broadcast.running)
            self.save_state()
            self.hide()
            event.ignore()
            return
        self.broadcast_was_running = bool(self.broadcast.running)
        self.capture_playback()
        self.is_quitting = True
        bridge = getattr(self, "control_api_bridge", None)
        if bridge is not None:
            bridge.set_active(False)
        self.save_state()
        cleanup_steps = [
            ("API REST", self.control_api.stop),
            ("pré-escuta", self.preview_player.stop),
            ("decodificador da pré-escuta", self.preview_dialog.shutdown_fallback),
            ("cart wall", self.stop_all_carts),
            ("entradas de áudio", self.audio_input_controller.stop_all),
            ("transmissão", self.broadcast.stop),
            ("Discord Rich Presence", self.discord_presence.shutdown),
            ("player principal", self.player.stop),
            ("player de mix", self.mix_player.stop),
            ("decodificador alternativo", self.shutdown_audio_fallback),
            ("visualizador", self.on_air.shutdown_analysis),
        ]
        if self.tray_icon:
            cleanup_steps.append(("bandeja", self.tray_icon.hide))
        for component, cleanup in cleanup_steps:
            try:
                cleanup()
            except Exception:
                logging.getLogger("radiostudio").exception(
                    "Falha ao encerrar o componente %s", component)
        super().closeEvent(event)
        QTimer.singleShot(0, QApplication.instance().quit)
