"""Persistence operations for the main window."""

import logging
import math
import os
import re
import uuid

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_paths import same_audio_path
from ..services.audio_inputs import INPUT_CHANNELS


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


class PersistenceMixin:
    @guarded
    def capture_playback(self):
        use_mix = (self.mix_target_index >= 0 and self.mix_gain > self.main_gain
                   and self.mix_player.playbackState() == QMediaPlayer.PlayingState)
        active_player = self.mix_player if use_mix else self.player
        path = (self.tracks[self.mix_target_index] if use_mix
                and 0 <= self.mix_target_index < len(self.tracks)
                else self.original_media_path(active_player))
        if path:
            self.last_playback = {
                "path": path,
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
            return
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("O arquivo de estado precisa conter um objeto JSON.")
            saved_tracks = state.get("tracks", [])
            if not isinstance(saved_tracks, list):
                saved_tracks = []
            self.tracks = [path for path in saved_tracks if isinstance(path, str) and path.strip()]
            saved_sources = state.get("track_sources", [])
            if not isinstance(saved_sources, list):
                saved_sources = []
            valid_sources = {"manual", "autodj", "schedule"}
            self.track_sources = [source if isinstance(source, str) and source in valid_sources else "manual"
                                  for source in (saved_sources + ["manual"] * len(self.tracks))
                                  [:len(self.tracks)]]
            saved_events = state.get("events", [])
            self.events = []
            if isinstance(saved_events, list):
                for event in saved_events:
                    if not isinstance(event, dict):
                        continue
                    event_time = str(event.get("time", ""))
                    try:
                        day = int(event.get("day", 0))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    path = event.get("path")
                    if (isinstance(path, str) and path.strip()
                            and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", event_time)
                            and 0 <= day <= 7):
                        event_id = event.get("id")
                        if not isinstance(event_id, str) or not event_id:
                            event_id = uuid.uuid4().hex
                        self.events.append({"id": event_id, "path": path, "time": event_time, "day": day,
                                            "enabled": bool(event.get("enabled", True))})
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
            self.dynamic_mic_duck = bool(state.get("dynamic_mic_duck", False))
            self.speech_music_level_percent = bounded_int(
                state.get("speech_music_level_percent", 20), 20, 0, 100)
            self.speech_threshold_db = bounded_float(
                state.get("speech_threshold_db", -38), -38.0, -80.0, -10.0)
            self.local_master_enabled = bool(state.get("local_master_enabled", True))
            self.master_volume = bounded_float(state.get("master_volume", 0.8), 0.8, 0.0, 1.0)
            self.volume_slider.setValue(round(self.master_volume * 100))
            self.minimize_to_tray = bool(state.get("minimize_to_tray", True))
            self.resume_on_startup = bool(state.get("resume_on_startup", True))
            self.auto_start_broadcast = bool(state.get("auto_start_broadcast", False))
            self.broadcast_was_running = bool(state.get("broadcast_was_running", False))
            saved_autodj = state.get("autodj", {})
            if isinstance(saved_autodj, dict):
                self.autodj_config.update(saved_autodj)
            self.autodj_config["enabled"] = bool(self.autodj_config.get("enabled", False))
            self.autodj_config["remove_played_from_queue"] = bool(
                self.autodj_config.get("remove_played_from_queue", False))
            for key, default, maximum in (("artist_cooldown_minutes", 60, 10080),
                                          ("track_cooldown_minutes", 360, 43200),
                                          ("queue_ahead", 4, 30)):
                try:
                    value = int(self.autodj_config.get(key, default))
                except (TypeError, ValueError, OverflowError):
                    value = default
                self.autodj_config[key] = min(maximum, max(1 if key == "queue_ahead" else 0, value))
            saved_programs = state.get("autodj_programs", [])
            self.autodj_programs = [program for program in saved_programs
                                    if isinstance(program, dict)] if isinstance(saved_programs, list) else []
            self.fade_in_ms = bounded_int(state.get("fade_in_ms", 0), 0, 0, 30000)
            self.fade_out_ms = bounded_int(state.get("fade_out_ms", 0), 0, 0, 30000)
            self.mix_ms = bounded_int(state.get("mix_ms", 0), 0, 0, 30000)
            self.auto_transitions_enabled = bool(state.get("auto_transitions_enabled", False))
            self.trim_silence = bool(state.get("trim_silence", False))
            self.silence_threshold_db = bounded_float(
                state.get("silence_threshold_db", -42), -42.0, -80.0, -10.0)
            self.silence_hold_ms = bounded_int(
                state.get("silence_hold_ms", 1200), 1200, 200, 10000)
            self.set_player_controls_position(state.get("player_controls_position", "bottom"))
            self.waveform_style = (state.get("waveform_style")
                                   if isinstance(state.get("waveform_style"), str) else "bars")
            self.on_air.set_waveform_style(self.waveform_style)
            playback = state.get("playback", {})
            if isinstance(playback, dict):
                try:
                    position_ms = max(0, int(playback.get("position_ms", 0)))
                except (TypeError, ValueError, OverflowError):
                    position_ms = 0
                self.last_playback = {
                    "path": (playback.get("path", "")
                             if isinstance(playback.get("path", ""), str) else ""),
                    "position_ms": position_ms,
                    "was_playing": bool(playback.get("was_playing", False)),
                }
            saved_metadata = state.get("station_metadata", {})
            self.station_metadata = {**DEFAULT_STATION_METADATA,
                                     **(saved_metadata if isinstance(saved_metadata, dict) else {})}
            self.station_metadata = {key: str(value or "")
                                     for key, value in self.station_metadata.items()}
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
            self.autodj_panel.refresh_history()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, "Configuração", f"Não foi possível carregar os dados salvos:\n{exc}")

    @guarded
    def save_state(self):
        if not self.is_quitting:
            self.capture_playback()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = {"tracks": self.tracks, "track_sources": self.track_sources,
                "events": self.events, "carts": self.carts,
                "autodj": self.autodj_config, "autodj_programs": self.autodj_programs,
                "main_device_key": self.main_device_key,
                "preview_device_key": self.preview_device_key,
                "input_device_keys": self.input_device_keys,
                "mic_music_level_percent": self.mic_music_level_percent,
                "dynamic_mic_duck": self.dynamic_mic_duck,
                "speech_music_level_percent": self.speech_music_level_percent,
                "speech_threshold_db": self.speech_threshold_db,
                "local_master_enabled": self.local_master_enabled,
                "master_volume": self.master_volume,
                "minimize_to_tray": self.minimize_to_tray,
                "resume_on_startup": self.resume_on_startup,
                "auto_start_broadcast": self.auto_start_broadcast,
                "broadcast_was_running": self.broadcast_was_running,
                "fade_in_ms": self.fade_in_ms, "fade_out_ms": self.fade_out_ms,
                "auto_transitions_enabled": self.auto_transitions_enabled,
                "mix_ms": self.mix_ms, "trim_silence": self.trim_silence,
                "silence_threshold_db": self.silence_threshold_db,
                "silence_hold_ms": self.silence_hold_ms,
                "player_controls_position": self.player_controls_position,
                "waveform_style": self.waveform_style,
                "playback": self.last_playback,
                "station_metadata": self.station_metadata,
                "explorer_folder": self.explorer_folder,
                "broadcast_profiles": self.broadcast_panel.profiles,
                "local_broadcast": self.broadcast_panel.local_config()}
        temporary = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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
        self.save_state()
        cleanup_steps = [
            ("pré-escuta", self.preview_player.stop),
            ("decodificador da pré-escuta", self.preview_dialog.shutdown_fallback),
            ("cart wall", self.stop_all_carts),
            ("entradas de áudio", self.audio_input_controller.stop_all),
            ("transmissão", self.broadcast.stop),
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
