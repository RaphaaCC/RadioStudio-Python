"""Playback operations for the main window."""

import logging

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_paths import same_audio_path
from ..services.transition_analysis import (
    bounded_transition_duration_ms, equal_power_gains, plan_automatic_transition,
)

_logger = logging.getLogger("radiostudio")
MAX_TRANSITION_ANALYSES = 256


class PlaybackMixin:
    def cancel_pending_media_end(self):
        self._media_end_generation = getattr(self, "_media_end_generation", 0) + 1
        self._pending_media_end = None

    @guarded
    def schedule_media_end_transition(self):
        if (getattr(self, "_suppress_end_recovery", False)
                or self.stop_pending or self.is_quitting
                or getattr(self, "_pending_media_end", None) is not None
                or not 0 <= self.current_index < len(self.tracks)):
            return
        path = self.tracks[self.current_index]
        generation = getattr(self, "_media_end_generation", 0) + 1
        self._media_end_generation = generation
        pending = (generation, self.current_index, path)
        self._pending_media_end = pending
        QTimer.singleShot(0, lambda expected=pending: self.finish_media_end_transition(expected))

    @guarded
    def finish_media_end_transition(self, expected):
        if getattr(self, "_pending_media_end", None) != expected:
            return
        self._pending_media_end = None
        _generation, index, path = expected
        if (self.is_quitting or self.stop_pending or index != self.current_index
                or not 0 <= index < len(self.tracks)
                or not same_audio_path(self.tracks[index], path)
                or not same_audio_path(self.original_media_path(self.player), path)):
            return
        # EndOfMedia can be queued while Qt is switching to another source.
        # Re-check the active deck after the deferred callback so a stale signal
        # cannot skip a newly loaded track (especially a duplicate queue path).
        if not self._main_track_reached_end():
            return
        target = self.mix_target_index
        mix_matches_queue = (
            target == index + 1
            and 0 <= target < len(self.tracks)
            and same_audio_path(self.original_media_path(self.mix_player), self.tracks[target])
        )
        if (mix_matches_queue
                and self.mix_player.playbackState() == QMediaPlayer.PlayingState):
            self.promote_mix_player()
        else:
            self.next_track()

    @guarded
    def shutdown_audio_fallback(self):
        for player in (self.player, self.mix_player):
            self.audio_decode_fallback.cancel(player)
            player.stop()
            player.setSource(QUrl())
            self.release_decoded_media(player)
            self.audio_fallback_attempts.pop(player, None)
        self.audio_decode_fallback.shutdown()

    def original_media_path(self, player):
        decoded = self.decoded_audio_sources.get(player)
        current = player.source().toLocalFile()
        if decoded and same_audio_path(current, decoded[1]):
            return decoded[0]
        return current

    def release_decoded_media(self, player):
        decoded = self.decoded_audio_sources.pop(player, None)
        if decoded:
            self.audio_decode_fallback.release(decoded[1])

    def set_media_source(self, player, path):
        self.audio_decode_fallback.cancel(player)
        self.release_decoded_media(player)
        self.audio_fallback_attempts.pop(player, None)
        source_id = ("main" if player is self.player else
                     "mix" if player is self.mix_player else None)
        if source_id == "main":
            self._main_source_generation = getattr(self, "_main_source_generation", 0) + 1
            self.broadcast.clear_source(source_id, preserve_primed=True)
        elif source_id:
            self.broadcast.remove_source(source_id)
        player.setSource(QUrl.fromLocalFile(path))

    def try_decode_fallback(self, player, path, callback):
        decoded = self.decoded_audio_sources.get(player)
        if decoded and same_audio_path(player.source().toLocalFile(), decoded[1]):
            return False
        if same_audio_path(self.audio_fallback_attempts.get(player, ""), path):
            return self.audio_decode_fallback.is_pending(player)
        self.audio_fallback_attempts[player] = path
        if not Path(path).is_file():
            return False
        callback_called = False

        def completed(source, decoded_path, error):
            nonlocal callback_called
            callback_called = True
            callback(source, decoded_path, error)

        started = self.audio_decode_fallback.convert(player, path, completed)
        # A setup failure can complete synchronously. Treat the callback as
        # handled so InvalidMedia does not schedule the next track twice.
        return bool(started or callback_called)

    @guarded
    def main_decode_fallback_ready(self, player, source, decoded_path, error):
        if (self.is_quitting or player is not self.player
                or not 0 <= self.current_index < len(self.tracks)
                or not same_audio_path(self.tracks[self.current_index], source)
                or not same_audio_path(player.source().toLocalFile(), source)):
            if decoded_path:
                self.audio_decode_fallback.release(decoded_path, delay_ms=0)
            return
        if not decoded_path:
            self.handle_invalid_main_media(source, error)
            return
        self.decoded_audio_sources[player] = (source, decoded_path)
        self.log_event("info", f"Decodificando formato compatível: {Path(source).name}")
        self.statusBar().showMessage(f"Preparando áudio: {Path(source).name}")
        self.broadcast.remove_source("main")
        player.setSource(QUrl.fromLocalFile(decoded_path))
        player.play()

    @guarded
    def mix_decode_fallback_ready(self, player, source, decoded_path, error):
        index = self.mix_target_index
        if (self.is_quitting or player is not self.mix_player or index < 0
                or not 0 <= index < len(self.tracks)
                or not same_audio_path(self.tracks[index], source)
                or not same_audio_path(player.source().toLocalFile(), source)):
            if decoded_path:
                self.audio_decode_fallback.release(decoded_path, delay_ms=0)
            return
        if not decoded_path:
            self.mix_failed_key = (self.current_index, str(source))
            self.log_event("error", f"Não foi possível decodificar o Mix: {error}")
            self.cancel_mix()
            return
        self.decoded_audio_sources[player] = (source, decoded_path)
        self.log_event("info", f"Preparando Mix com FFmpeg: {Path(source).name}")
        self.broadcast.remove_source("mix")
        player.setSource(QUrl.fromLocalFile(decoded_path))
        player.play()

    @guarded
    def seek_main_player(self, position_ms):
        if self.mix_target_index >= 0 or self.stop_pending:
            self.cancel_pending_transition()
        self.broadcast.clear_source("main", preserve_primed=True)
        self.player.setPosition(max(0, int(position_ms)))

    @guarded
    def play_index(self, index, resume_position_ms=0, *, immediate=False):
        if not 0 <= index < len(self.tracks):
            return
        path = self.tracks[index]
        if not Path(path).is_file():
            QMessageBox.warning(self, "Arquivo ausente", path)
            return

        self.cancel_pending_media_end()
        fade_out_duration = self.automatic_fade_out_ms()
        if (not immediate and index != self.current_index and fade_out_duration
                and self.player.playbackState() == QMediaPlayer.PlayingState):
            interrupting_fade = self.pending_transition_kind in ("advance", "stop")
            self.cancel_transition_action()
            self.stop_pending = False
            if interrupting_fade:
                self.gain_ramps.pop("main", None)
                self.main_gain = 1.0
                self.apply_main_volume()
                if not self.gain_ramps:
                    self.gain_timer.stop()
            self.cancel_mix()
            self.stop_pending = True
            self.ramp_gain("main", 0.0, fade_out_duration, curve="equal_power_out")
            self.schedule_transition_action(
                fade_out_duration,
                lambda: self.play_index(index, resume_position_ms, immediate=True),
                "advance")
            return

        self.cancel_transition_action()
        self.cancel_mix()
        self.mix_failed_key = None
        self.gain_ramps.pop("main", None)
        self.gain_paused_at = None
        if not self.gain_ramps:
            self.gain_timer.stop()
        self.stop_pending = False
        self.current_index = index
        self._suppress_end_recovery = False
        self.silence_ms = 0
        if self.auto_transitions_enabled:
            self.request_transition_analysis(path)
            if index + 1 < len(self.tracks):
                self.request_transition_analysis(self.tracks[index + 1])
        self.fade_in_pending_ms = self.automatic_fade_in_ms(path)
        self.pending_main_cue_ms = (
            self.automatic_cue_ms(path)
            if self.auto_transitions_enabled and not resume_position_ms else 0)
        self.main_gain = 0.0 if self.fade_in_pending_ms else 1.0
        self.apply_main_volume()
        self.set_media_source(self.player, path)
        self.pending_resume_position = max(0, int(resume_position_ms)) or None
        metadata = read_track_metadata(path)
        self._display_sync_path = path
        self._display_sync_metadata = metadata
        self.pending_track_log = metadata.display_title
        self.on_air.show_track(metadata.display_title, path)
        if self.tray_icon:
            self.tray_icon.setToolTip(f"{APP_DISPLAY_NAME} — {metadata.display_title[:80]}")
        self.broadcast.set_title(format_broadcast_title(
            self.station_metadata["title_format"], metadata, self.station_metadata))
        self.playlist.selectRow(index)
        self.statusBar().showMessage(f"Reproduzindo: {path}")
        self.player.play()
        self.apply_pending_resume_position()

    @guarded
    def pause(self):
        if self.stop_pending:
            self.cancel_transition_action()
            self.stop_pending = False
            self.gain_ramps.pop("main", None)
            self.main_gain = 1.0
            self.apply_main_volume()
            if not self.gain_ramps:
                self.gain_timer.stop()
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            if (self.mix_target_index >= 0
                    and self.mix_player.playbackState() == QMediaPlayer.PlayingState):
                self.mix_player.pause()
            if self.gain_ramps:
                self.gain_paused_at = time.monotonic()
                self.gain_timer.stop()
        else:
            if self.gain_paused_at is not None:
                paused_for = max(0.0, time.monotonic() - self.gain_paused_at)
                self.gain_ramps = {
                    channel: (ramp[0], ramp[1], ramp[2] + paused_for, *ramp[3:])
                    for channel, ramp in self.gain_ramps.items()
                }
                self.gain_paused_at = None
                if self.gain_ramps:
                    self.gain_timer.start()
            self.player.play()
            if self.mix_target_index >= 0:
                self.mix_player.play()

    @guarded
    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.StoppedState:
            self.play_selected()
        else:
            self.pause()

    @guarded
    def stop(self):
        self.cancel_transition_action()
        self.stop_pending = False
        self.cancel_mix()
        self.gain_ramps.pop("main", None)
        self.gain_paused_at = None
        if not self.gain_ramps:
            self.gain_timer.stop()
        fade_out_duration = self.automatic_fade_out_ms()
        if (fade_out_duration
                and self.player.playbackState() == QMediaPlayer.PlayingState):
            self.stop_pending = True
            self.ramp_gain("main", 0.0, fade_out_duration, curve="equal_power_out")
            self.schedule_transition_action(fade_out_duration, self.finish_stop, "stop")
            return
        self.finish_stop()

    @guarded
    def finish_stop(self):
        self.cancel_pending_media_end()
        self.cancel_transition_action()
        self.stop_pending = False
        self._suppress_end_recovery = True
        self.pending_track_log = None
        self.pending_resume_position = None
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0
        self.cancel_mix()
        self.gain_ramps.clear()
        self.gain_paused_at = None
        self.gain_timer.stop()
        self.audio_decode_fallback.cancel(self.player)
        self.player.stop()
        self.broadcast.remove_source("main")
        self.release_decoded_media(self.player)
        self.audio_fallback_attempts.pop(self.player, None)
        self.main_gain = 1.0
        self.apply_main_volume()
        self.on_air.clear_track()
        if self.tray_icon:
            self.tray_icon.setToolTip(APP_DISPLAY_NAME)

    @guarded
    def next_track(self):
        self.cancel_pending_media_end()
        interrupting_fade = self.pending_transition_kind in ("advance", "stop")
        self.cancel_transition_action()
        self.stop_pending = False
        if interrupting_fade:
            self.gain_ramps.pop("main", None)
            self.main_gain = 1.0
            self.apply_main_volume()
            if not self.gain_ramps:
                self.gain_timer.stop()
        self.cancel_mix()
        self.gain_paused_at = None
        if self.autodj_config.get("enabled") and self.current_index + 1 >= len(self.tracks):
            self.fill_autodj_queue(force=True, allow_current_repeat=True)
        if self.current_index < 0 and self.pending_track_log:
            return

        prefix_end = max(0, min(len(self.tracks), self.current_index + 1))
        upcoming = list(zip(self.tracks[prefix_end:], self.track_sources[prefix_end:]))
        available = [(path, source) for path, source in upcoming if Path(path).is_file()]
        missing = len(upcoming) - len(available)
        if missing:
            self.tracks[prefix_end:] = [path for path, _source in available]
            self.track_sources[prefix_end:] = [source for _path, source in available]
            self.log_event("error", f"{missing} faixa(s) ausente(s) foram ignoradas na fila.")
            self.refresh_playlist()
            self.save_state()
        if (self.autodj_config.get("enabled")
                and self.current_index + 1 >= len(self.tracks)):
            self.fill_autodj_queue(force=True, allow_current_repeat=True)
            if self.current_index < 0 and self.pending_track_log:
                return
        if self.current_index + 1 < len(self.tracks):
            next_index = self.current_index + 1
            fade_out_duration = self.automatic_fade_out_ms()
            if fade_out_duration and self.player.playbackState() == QMediaPlayer.PlayingState:
                self.stop_pending = True
                self.ramp_gain("main", 0.0, fade_out_duration, curve="equal_power_out")
                self.schedule_transition_action(
                    fade_out_duration,
                    lambda: self.advance_after_fade(next_index), "advance")
            else:
                self.play_index(next_index)
        else:
            self.stop()

    @guarded
    def media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.schedule_media_end_transition()
        elif status == QMediaPlayer.InvalidMedia:
            path = self.original_media_path(self.player)
            if self.try_decode_fallback(
                    self.player, path,
                    lambda source, decoded, error: self.main_decode_fallback_ready(
                        self.player, source, decoded, error)):
                if self.audio_decode_fallback.is_pending(self.player):
                    self.log_event("info", f"Preparando áudio para compatibilidade: {Path(path).name}")
                    self.statusBar().showMessage(f"Preparando áudio: {Path(path).name}")
                return
            self.handle_invalid_main_media(path)
        elif status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.apply_pending_resume_position()
            self.apply_pending_main_cue()

    @guarded
    def handle_invalid_main_media(self, path, fallback_error=""):
        if (self.is_quitting or self.stop_pending
                or getattr(self, "_suppress_end_recovery", False)):
            return
        self.fade_in_pending_ms = 0
        self.gain_ramps.pop("main", None)
        if not self.gain_ramps:
            self.gain_timer.stop()
        self.main_gain = 1.0
        self.apply_main_volume()
        self.pending_track_log = None
        self.pending_resume_position = None
        self.on_air.clear_track()
        message = f"Não foi possível reproduzir: {path or 'arquivo desconhecido'}"
        if fallback_error:
            message += f" ({fallback_error})"
        self.statusBar().showMessage(message)
        self.log_event("error", message)
        self.last_playback["was_playing"] = False
        if not self.is_quitting:
            self.save_state()
            source_generation = getattr(self, "_main_source_generation", 0)
            QTimer.singleShot(
                0, lambda source=path, generation=source_generation:
                self.skip_invalid_track_if_current(source, generation))

    @guarded
    def skip_invalid_track_if_current(self, path, source_generation):
        """Ignore a deferred error skip if playback changed before it ran."""
        if (self.is_quitting or self.stop_pending
                or getattr(self, "_suppress_end_recovery", False)
                or source_generation != getattr(self, "_main_source_generation", 0)
                or not same_audio_path(self.original_media_path(self.player), path)
                or self.player.mediaStatus() != QMediaPlayer.InvalidMedia):
            return
        self.next_track()

    @guarded
    def advance_after_fade(self, next_index):
        self.stop_pending = False
        if not 0 <= next_index < len(self.tracks) or not Path(self.tracks[next_index]).is_file():
            self.gain_ramps.pop("main", None)
            self.main_gain = 1.0
            self.apply_main_volume()
            if not self.gain_ramps:
                self.gain_timer.stop()
            self.log_event("error", "A próxima faixa não está mais disponível; procurando a seguinte.")
            self.next_track()
            return
        self.play_index(next_index, immediate=True)

    @guarded
    def playback_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self.refresh_playback_display()
            if self.pending_track_log:
                self.log_event("music", self.pending_track_log)
                self.record_track_started(self.original_media_path(self.player))
                self.pending_track_log = None
        if not self.is_quitting:
            self.save_state()

    @guarded
    def slot_media_status_changed(self, player, status):
        if player is self.player:
            self.media_status_changed(status)
            if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
                self.apply_pending_main_cue()
                self.begin_main_fade_in()
        elif player is self.mix_player and status == QMediaPlayer.InvalidMedia:
            path = self.original_media_path(player)
            if self.try_decode_fallback(
                    player, path,
                    lambda source, decoded, error: self.mix_decode_fallback_ready(
                        player, source, decoded, error)):
                if self.audio_decode_fallback.is_pending(player):
                    self.log_event("info", f"Preparando Mix para compatibilidade: {Path(path).name}")
                return
            self.mix_failed_key = (self.current_index, str(path))
            self.log_event("error", f"Não foi possível preparar o Mix: {path}")
            self.cancel_mix()
        elif (player is self.mix_player
              and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia)):
            self.apply_mix_cue()
            self.begin_mix_fade()

    @guarded
    def slot_playback_state_changed(self, player, state):
        if player is self.player:
            if state == QMediaPlayer.PlayingState:
                self.begin_main_fade_in()
                if (self.mix_target_index >= 0
                        and self.mix_player.playbackState() == QMediaPlayer.PlayingState):
                    self.begin_mix_fade()
            self.playback_state_changed(state)
        elif player is self.mix_player and state == QMediaPlayer.PlayingState:
            # A mix can finish loading after the user pauses the main player.
            # Keep that secondary deck from leaking audio to the broadcast.
            if self.player.playbackState() == QMediaPlayer.PausedState:
                player.pause()
            elif self.player.playbackState() == QMediaPlayer.PlayingState:
                self.begin_mix_fade()

    @guarded
    def slot_position_changed(self, player, value):
        if player is self.player:
            self.update_position(value)

    @guarded
    def slot_duration_changed(self, player, value):
        if player is self.player:
            self.update_duration(value)

    @guarded
    def ramp_gain(self, channel, target, duration_ms, curve=None):
        current = self.main_gain if channel == "main" else self.mix_gain
        self.gain_ramps[channel] = (
            current, float(target), time.monotonic(), max(1, duration_ms), curve)
        # 100 Hz gain updates make short fades substantially smoother than the
        # previous 50 Hz staircase while keeping the work trivial for the UI.
        if hasattr(self.gain_timer, "setInterval"):
            self.gain_timer.setInterval(10)
        if not self.gain_timer.isActive():
            self.gain_timer.start()

    @guarded
    def begin_main_fade_in(self):
        if self.fade_in_pending_ms <= 0:
            return
        if self.pending_main_cue_ms and self.player.mediaStatus() not in (
                QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            return
        self.apply_pending_main_cue()
        duration_ms = self.fade_in_pending_ms
        self.fade_in_pending_ms = 0
        self.ramp_gain("main", 1.0, duration_ms, curve="equal_power_in")

    def automatic_fade_in_ms(self, path):
        if not self.auto_transitions_enabled:
            return self.fade_in_ms
        analysis = self.transition_analyses.get(path)
        return plan_automatic_transition(None, analysis, self.mix_ms).fade_in_ms

    def automatic_cue_ms(self, path):
        analysis = self.transition_analyses.get(path)
        return plan_automatic_transition(None, analysis, self.mix_ms).cue_ms

    def automatic_fade_out_ms(self):
        if self.auto_transitions_enabled:
            return self.fade_out_ms or 650
        return self.fade_out_ms

    @guarded
    def request_transition_analysis(self, path):
        if self.auto_transitions_enabled and path not in self.transition_analyses:
            self.transition_analyzer.request(path)

    @guarded
    def transition_analysis_ready(self, path, analysis):
        self.transition_analyses[path] = analysis
        if len(self.transition_analyses) > MAX_TRANSITION_ANALYSES:
            self.transition_analyses.pop(next(iter(self.transition_analyses)))
        if (self.auto_transitions_enabled and not self.is_quitting
                and self.player.playbackState() == QMediaPlayer.PlayingState):
            self.maybe_start_mix()

    @guarded
    def apply_pending_main_cue(self):
        cue_ms = self.pending_main_cue_ms
        if cue_ms <= 0:
            return
        if self.pending_resume_position is not None:
            self.pending_main_cue_ms = 0
            return
        if (self.player.isSeekable() and self.player.duration() > 0
                and self.player.mediaStatus() in (QMediaPlayer.LoadedMedia,
                                                  QMediaPlayer.BufferedMedia)):
            self.player.setPosition(min(cue_ms, max(0, self.player.duration() - 500)))
        self.pending_main_cue_ms = 0

    @guarded
    def apply_mix_cue(self):
        cue_ms = self.mix_cue_ms
        if cue_ms <= 0:
            return
        if self.mix_player.isSeekable() and self.mix_player.duration() > 0:
            self.mix_player.setPosition(min(cue_ms, max(0, self.mix_player.duration() - 500)))
        self.mix_cue_ms = 0

    @guarded
    def update_gain_ramps(self):
        now = time.monotonic()
        for channel, ramp in list(self.gain_ramps.items()):
            start, target, began, duration = ramp[:4]
            curve = ramp[4] if len(ramp) > 4 else None
            progress = min(1.0, (now - began) * 1000 / duration)
            if curve == "equal_power_out":
                outgoing, _incoming = equal_power_gains(progress)
                gain = target + (start - target) * outgoing
            elif curve == "equal_power_in":
                _outgoing, incoming = equal_power_gains(progress)
                gain = start + (target - start) * incoming
            else:
                gain = start + (target - start) * progress
            if channel == "main":
                self.main_gain = gain
                self.apply_main_volume()
            else:
                self.mix_gain = gain
                self.mix_audio.setVolume(
                    self.master_volume * gain * getattr(self, "music_duck_gain", 1.0))
            if progress >= 1.0:
                del self.gain_ramps[channel]
        if not self.gain_ramps:
            self.gain_timer.stop()

    @guarded
    def set_master_volume(self, value):
        self.master_volume = value / 100
        self.audio_input_controller.set_monitor_gain(self.master_volume)
        self.apply_main_volume()
        self.mix_audio.setVolume(
            self.master_volume * self.mix_gain * getattr(self, "music_duck_gain", 1.0))
        self.apply_cart_local_volume()

    @guarded
    def apply_main_volume(self):
        self.audio.setVolume(
            self.master_volume * self.main_gain * getattr(self, "music_duck_gain", 1.0))

    @guarded
    def maybe_start_mix(self):
        if (self.mix_target_index >= 0 or self.current_index + 1 >= len(self.tracks)
                or self.stop_pending
                or self.player.playbackState() != QMediaPlayer.PlayingState):
            return
        duration = self.player.duration()
        if duration <= 0:
            return
        remaining = duration - self.player.position()
        if remaining <= 0:
            return
        next_path = self.tracks[self.current_index + 1]
        if self.auto_transitions_enabled:
            current_path = self.tracks[self.current_index]
            self.request_transition_analysis(current_path)
            self.request_transition_analysis(next_path)
            plan = plan_automatic_transition(
                self.transition_analyses.get(current_path),
                self.transition_analyses.get(next_path), self.mix_ms)
            transition_ms = plan.duration_ms
            cue_ms = plan.cue_ms
        else:
            # Fade in/out are start/stop actions. Only Mix defines automatic
            # overlap between consecutive tracks.
            if self.mix_ms <= 0:
                return
            transition_ms = self.mix_ms
            cue_ms = 0
        # Position updates arrive repeatedly during playback. The requested
        # overlap is an upper bound, so there is no reason to stat/read the
        # next file's tags until the current track is inside that window.
        if remaining > transition_ms:
            return
        next_duration = read_track_metadata(next_path).duration
        transition_ms = bounded_transition_duration_ms(
            transition_ms, duration, remaining, round(next_duration * 1000))
        if remaining <= transition_ms:
            self.start_mix(transition_ms, cue_ms=cue_ms)

    @guarded
    def start_mix(self, duration_ms=None, cue_ms=None, fade_in_ms=None, fade_out_ms=None):
        if (self.mix_target_index >= 0 or self.current_index + 1 >= len(self.tracks)
                or self.stop_pending
                or self.player.playbackState() != QMediaPlayer.PlayingState):
            return
        path = self.tracks[self.current_index + 1]
        failure_key = (self.current_index, str(path))
        if self.mix_failed_key == failure_key:
            return
        if not Path(path).is_file():
            self.mix_failed_key = failure_key
            return
        outgoing_analysis = None
        if self.auto_transitions_enabled:
            outgoing_analysis = self.transition_analyses.get(self.tracks[self.current_index])
            plan = plan_automatic_transition(
                outgoing_analysis,
                self.transition_analyses.get(path), self.mix_ms)
            if duration_ms is None:
                duration_ms = plan.duration_ms
            if cue_ms is None:
                cue_ms = plan.cue_ms
            # A long detected silent tail should not make the next song crawl in
            # for several seconds. Shorten both ramps only when the outgoing tail
            # is confidently silent; audible fades keep the equal-power overlap.
            if (fade_in_ms is None and fade_out_ms is None and outgoing_analysis
                    and outgoing_analysis.trailing_silence_ms >= 800
                    and outgoing_analysis.tail_fade_ms < 1600):
                fade_in_ms = plan.fade_in_ms
                fade_out_ms = plan.fade_in_ms
        requested_duration = max(1, int(duration_ms or self.mix_ms or 1000))
        outgoing_remaining = self.player.duration() - self.player.position()
        if outgoing_remaining <= 0:
            return
        incoming_duration = read_track_metadata(path).duration
        duration_ms = bounded_transition_duration_ms(
            requested_duration, self.player.duration(), outgoing_remaining,
            round(incoming_duration * 1000))

        def bounded_fade(value):
            if value is None:
                return duration_ms
            try:
                value = int(value)
            except (TypeError, ValueError, OverflowError):
                return duration_ms
            return min(duration_ms, max(1, value))

        self.mix_target_index = self.current_index + 1
        self.mix_fade_duration_ms = duration_ms
        self.mix_fade_in_duration_ms = bounded_fade(fade_in_ms)
        self.mix_fade_out_duration_ms = bounded_fade(fade_out_ms)
        self.mix_fade_started = False
        self.mix_cue_ms = max(0, int(cue_ms or 0))
        self.mix_gain = 0.0
        self.mix_audio.setVolume(0.0)
        self.set_media_source(self.mix_player, path)
        if self.mix_target_index != self.current_index + 1:
            return
        self.mix_player.play()
        if self.mix_player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.apply_mix_cue()
            self.begin_mix_fade()

    @guarded
    def begin_mix_fade(self):
        if self.mix_target_index < 0 or self.mix_fade_started:
            return
        if (self.player.playbackState() == QMediaPlayer.PausedState
                or self.mix_player.playbackState() != QMediaPlayer.PlayingState):
            return
        remaining = self.player.duration() - self.player.position()
        if remaining <= 0:
            return
        duration_ms = min(max(1, self.mix_fade_duration_ms), remaining)
        self.mix_fade_duration_ms = duration_ms
        fade_out_ms = min(
            duration_ms, max(1, getattr(self, "mix_fade_out_duration_ms", duration_ms)))
        fade_in_ms = min(
            duration_ms, max(1, getattr(self, "mix_fade_in_duration_ms", duration_ms)))
        self.mix_fade_started = True
        self.ramp_gain("main", 0.0, fade_out_ms, curve="equal_power_out")
        self.ramp_gain("mix", 1.0, fade_in_ms, curve="equal_power_in")

    @guarded
    def promote_mix_player(self):
        old_player, old_audio, old_buffer = self.player, self.audio, self.main_buffer_output
        next_index = self.mix_target_index
        self.player, self.audio, self.main_buffer_output = (
            self.mix_player, self.mix_audio, self.mix_buffer_output)
        self.mix_player, self.mix_audio, self.mix_buffer_output = old_player, old_audio, old_buffer
        self.mix_target_index = -1
        self.mix_fade_duration_ms = 0
        self.mix_fade_in_duration_ms = 0
        self.mix_fade_out_duration_ms = 0
        self.mix_fade_started = False
        self.mix_cue_ms = 0
        self.current_index = next_index
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.gain_ramps.pop("main", None)
        self.gain_ramps.pop("mix", None)
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0

        # Commit the visible track state immediately after the deck swap. Audio
        # device and broadcast cleanup are independent and must not leave the
        # interface showing the outgoing track if one of them fails.
        metadata = read_track_metadata(self.tracks[self.current_index])
        self._display_sync_path = self.tracks[self.current_index]
        self._display_sync_metadata = metadata
        self.pending_track_log = metadata.display_title
        self.on_air.show_track(metadata.display_title, self.tracks[self.current_index])
        self.playlist.selectRow(self.current_index)
        self.silence_ms = 0
        self.apply_pending_resume_position()
        self.refresh_playback_display()

        try:
            self.apply_main_volume()
        except Exception:
            _logger.exception("Falha ao atualizar o volume após promover a faixa")
        try:
            self.mix_audio.setVolume(0.0)
        except Exception:
            _logger.exception("Falha ao silenciar o deck antigo após o Mix")
        try:
            self.mix_player.stop()
        except Exception:
            _logger.exception("Falha ao parar o deck antigo após o Mix")
        try:
            self.broadcast.promote_source("mix", "main")
        except Exception:
            _logger.exception("Falha ao promover a fonte de áudio do Mix")
            self.log_event("error", "A faixa mudou, mas houve uma falha ao atualizar a fonte da transmissão.")
        self.broadcast.set_title(format_broadcast_title(
            self.station_metadata["title_format"], metadata, self.station_metadata))
        self.log_event("music", metadata.display_title)
        self.record_track_started(metadata.path)
        self.pending_track_log = None
        self.save_state()

    @guarded
    def cancel_mix(self):
        had_active_mix = self.mix_target_index >= 0
        self.mix_target_index = -1
        self.mix_fade_duration_ms = 0
        self.mix_fade_in_duration_ms = 0
        self.mix_fade_out_duration_ms = 0
        self.mix_fade_started = False
        self.mix_cue_ms = 0
        self.gain_ramps.pop("mix", None)
        if hasattr(self, "mix_player"):
            self.audio_decode_fallback.cancel(self.mix_player)
            self.mix_player.stop()
            self.release_decoded_media(self.mix_player)
            self.audio_fallback_attempts.pop(self.mix_player, None)
            self.broadcast.remove_source("mix")
            self.mix_gain = 0.0
            self.mix_audio.setVolume(0.0)
        if had_active_mix:
            self.gain_ramps.pop("main", None)
            self.main_gain = 1.0
            self.apply_main_volume()
        if not self.gain_ramps and hasattr(self, "gain_timer"):
            self.gain_timer.stop()

    @guarded
    def cancel_pending_transition(self):
        had_mix = self.mix_target_index >= 0
        had_fade_action = self.pending_transition_kind in ("advance", "stop")
        if not had_mix and not had_fade_action:
            return
        if had_fade_action:
            self.cancel_transition_action()
        self.stop_pending = False
        if had_mix:
            self.cancel_mix()
        if had_fade_action:
            self.gain_ramps.pop("main", None)
            self.main_gain = 1.0
            self.apply_main_volume()
        if not self.gain_ramps:
            self.gain_timer.stop()

    @guarded
    def schedule_transition_action(self, delay_ms, callback, kind=None):
        self.pending_transition_action = callback
        self.pending_transition_kind = kind
        self.transition_timer.start(max(0, int(delay_ms)))

    @guarded
    def cancel_transition_action(self):
        if hasattr(self, "transition_timer"):
            self.transition_timer.stop()
        self.pending_transition_action = None
        self.pending_transition_kind = None

    @guarded
    def run_pending_transition_action(self):
        callback = self.pending_transition_action
        self.pending_transition_action = None
        self.pending_transition_kind = None
        if callback:
            callback()

    @guarded
    def update_position(self, value):
        if not self.position.isSliderDown():
            self.position.setValue(value)
        self.position_label.setText(f"{clock_text(value)} / {clock_text(self.player.duration())}")
        self.on_air.set_progress(value, self.player.duration())
        self.maybe_start_mix()
        if (not self.is_quitting and self.player.playbackState() == QMediaPlayer.PlayingState
                and time.monotonic() - self.last_position_save_at >= 5):
            self.last_position_save_at = time.monotonic()
            self.save_state()

    @guarded
    def update_duration(self, value):
        self.position.setRange(0, value)
        self.update_position(self.player.position())
        self.apply_pending_resume_position()

    def _main_track_reached_end(self):
        if getattr(self, "_suppress_end_recovery", False):
            return False
        if not 0 <= self.current_index < len(self.tracks):
            return False
        path = self.tracks[self.current_index]
        if not same_audio_path(self.original_media_path(self.player), path):
            return False

        status = self.player.mediaStatus()
        if status == QMediaPlayer.EndOfMedia:
            return True
        if status in (QMediaPlayer.NoMedia, QMediaPlayer.LoadingMedia,
                      QMediaPlayer.InvalidMedia):
            return False
        state = self.player.playbackState()
        if state == QMediaPlayer.PausedState:
            return False

        duration = max(0, int(self.player.duration()))
        position = max(0, int(self.player.position()))
        end_tolerance = min(250, duration * 0.1)
        at_end = duration > 0 and position >= duration - end_tolerance
        if not at_end:
            return False
        if state == QMediaPlayer.StoppedState:
            # A backend can stop at the end without delivering EndOfMedia.
            return True

        # If the backend still reports Playing, only an already-playing Mix
        # deck matching the next queue row proves that playback crossed tracks.
        target = self.mix_target_index
        return (
            target == self.current_index + 1
            and 0 <= target < len(self.tracks)
            and same_audio_path(self.original_media_path(self.mix_player), self.tracks[target])
            and self.mix_player.playbackState() == QMediaPlayer.PlayingState
        )

    @guarded
    def refresh_playback_display(self):
        """Resync the on-air display from the active deck as a signal-race fallback."""
        if self._main_track_reached_end():
            self.schedule_media_end_transition()
        if self.player.playbackState() == QMediaPlayer.StoppedState:
            return
        path = self.original_media_path(self.player)
        if not path:
            return

        cached_path = getattr(self, "_display_sync_path", "")
        metadata = getattr(self, "_display_sync_metadata", None)
        if not same_audio_path(cached_path, path) or metadata is None:
            metadata = read_track_metadata(path)
            self._display_sync_path = path
            self._display_sync_metadata = metadata
        if not same_audio_path(getattr(self.on_air, "_track_path", ""), path):
            self.on_air.show_track(metadata.display_title, path)
        else:
            # Keep the visible title independent from the waveform worker's
            # internal path marker. A partial/failed UI update must not freeze
            # the title while the player and queue have already moved on.
            title_widget = getattr(self.on_air, "title", None)
            title_text = getattr(title_widget, "text", None)
            if callable(title_text) and title_text() != metadata.display_title:
                title_widget.setText(metadata.display_title)
                title_widget.setToolTip(metadata.display_title)

        duration = max(0, int(self.player.duration()))
        if duration <= 0 and metadata.duration > 0:
            duration = round(metadata.duration * 1000)
        position = max(0, int(self.player.position()))
        if duration > 0:
            position = min(position, duration)
        self.position.setRange(0, duration)
        if not self.position.isSliderDown():
            self.position.setValue(position)
        display = f"{clock_text(position)} / {clock_text(duration)}"
        current_text = getattr(self.position_label, "text", None)
        if not callable(current_text) or current_text() != display:
            self.position_label.setText(display)
        self.on_air.set_progress(position, duration)

    @guarded
    def apply_pending_resume_position(self):
        if (self.pending_resume_position is not None and self.player.isSeekable()
                and self.player.duration() > 0
                and self.player.mediaStatus() in (QMediaPlayer.LoadedMedia,
                                                  QMediaPlayer.BufferedMedia)):
            self.player.setPosition(min(self.pending_resume_position,
                                        max(0, self.player.duration() - 1000)))
            self.pending_resume_position = None
