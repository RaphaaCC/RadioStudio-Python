"""Playback operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded
from ..services.transition_analysis import (
    bounded_transition_duration_ms, equal_power_gains, plan_automatic_transition,
)


class PlaybackMixin:
    @guarded
    def seek_main_player(self, position_ms):
        if self.mix_target_index >= 0 or self.stop_pending:
            self.cancel_pending_transition()
        self.player.setPosition(max(0, int(position_ms)))

    @guarded
    def play_index(self, index, resume_position_ms=0, *, immediate=False):
        if not 0 <= index < len(self.tracks):
            return
        path = self.tracks[index]
        if not Path(path).is_file():
            QMessageBox.warning(self, "Arquivo ausente", path)
            return

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
            self.ramp_gain("main", 0.0, fade_out_duration)
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
        self.player.setSource(QUrl.fromLocalFile(path))
        self.pending_resume_position = max(0, int(resume_position_ms)) or None
        metadata = read_track_metadata(path)
        self.pending_track_log = metadata.display_title
        self.player.play()
        self.apply_pending_resume_position()
        self.on_air.show_track(metadata.display_title, path)
        if self.tray_icon:
            self.tray_icon.setToolTip(f"{APP_DISPLAY_NAME} — {metadata.display_title[:80]}")
        self.broadcast.set_title(format_broadcast_title(
            self.station_metadata["title_format"], metadata, self.station_metadata))
        self.playlist.selectRow(index)
        self.statusBar().showMessage(f"Reproduzindo: {path}")

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
            self.ramp_gain("main", 0.0, fade_out_duration)
            self.schedule_transition_action(fade_out_duration, self.finish_stop, "stop")
            return
        self.finish_stop()

    @guarded
    def finish_stop(self):
        self.cancel_transition_action()
        self.stop_pending = False
        self.pending_track_log = None
        self.pending_resume_position = None
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0
        self.cancel_mix()
        self.gain_ramps.clear()
        self.gain_paused_at = None
        self.gain_timer.stop()
        self.player.stop()
        self.main_gain = 1.0
        self.apply_main_volume()
        self.on_air.clear_track()
        if self.tray_icon:
            self.tray_icon.setToolTip(APP_DISPLAY_NAME)

    @guarded
    def next_track(self):
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
                self.ramp_gain("main", 0.0, fade_out_duration)
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
            if self.stop_pending:
                return
            if self.mix_target_index >= 0 and self.mix_player.playbackState() == QMediaPlayer.PlayingState:
                self.promote_mix_player()
            else:
                self.next_track()
        elif status == QMediaPlayer.InvalidMedia:
            path = self.player.source().toLocalFile()
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
            self.statusBar().showMessage(message)
            self.log_event("error", message)
            self.last_playback["was_playing"] = False
            if not self.is_quitting:
                self.save_state()
                QTimer.singleShot(0, self.next_track)
        elif status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.apply_pending_resume_position()
            self.apply_pending_main_cue()

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
        if state == QMediaPlayer.PlayingState and self.pending_track_log:
            self.log_event("music", self.pending_track_log)
            self.record_track_started(self.player.source().toLocalFile())
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
            path = player.source().toLocalFile()
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
        self.ramp_gain("main", 1.0, duration_ms)

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
            if not (self.mix_ms or self.fade_out_ms):
                return
            transition_ms = max(self.mix_ms, self.fade_out_ms, self.fade_in_ms)
            cue_ms = 0
        next_duration = read_track_metadata(next_path).duration
        transition_ms = bounded_transition_duration_ms(
            transition_ms, duration, remaining, round(next_duration * 1000))
        if remaining <= transition_ms:
            self.start_mix(transition_ms, cue_ms=cue_ms)

    @guarded
    def start_mix(self, duration_ms=None, cue_ms=None):
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
        if self.auto_transitions_enabled and duration_ms is None:
            plan = plan_automatic_transition(
                self.transition_analyses.get(self.tracks[self.current_index]),
                self.transition_analyses.get(path), self.mix_ms)
            duration_ms = plan.duration_ms
            if cue_ms is None:
                cue_ms = plan.cue_ms
        requested_duration = max(1, int(duration_ms or self.mix_ms or self.fade_out_ms
                                         or self.fade_in_ms or 1000))
        outgoing_remaining = self.player.duration() - self.player.position()
        if outgoing_remaining <= 0:
            return
        incoming_duration = read_track_metadata(path).duration
        duration_ms = bounded_transition_duration_ms(
            requested_duration, self.player.duration(), outgoing_remaining,
            round(incoming_duration * 1000))
        self.mix_target_index = self.current_index + 1
        self.mix_fade_duration_ms = duration_ms
        self.mix_fade_started = False
        self.mix_cue_ms = max(0, int(cue_ms or 0))
        self.mix_gain = 0.0
        self.mix_audio.setVolume(0.0)
        self.mix_player.setSource(QUrl.fromLocalFile(path))
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
        self.mix_fade_started = True
        self.ramp_gain("main", 0.0, duration_ms, curve="equal_power_out")
        self.ramp_gain("mix", 1.0, duration_ms, curve="equal_power_in")

    @guarded
    def promote_mix_player(self):
        old_player, old_audio, old_buffer = self.player, self.audio, self.main_buffer_output
        next_index = self.mix_target_index
        self.player, self.audio, self.main_buffer_output = (
            self.mix_player, self.mix_audio, self.mix_buffer_output)
        self.mix_player, self.mix_audio, self.mix_buffer_output = old_player, old_audio, old_buffer
        self.mix_target_index = -1
        self.mix_fade_duration_ms = 0
        self.mix_fade_started = False
        self.mix_cue_ms = 0
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0
        self.current_index = next_index
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.gain_ramps.pop("main", None)
        self.gain_ramps.pop("mix", None)
        self.apply_main_volume()
        self.mix_audio.setVolume(0.0)
        self.mix_player.stop()
        self.broadcast.remove_source("mix")
        metadata = read_track_metadata(self.tracks[self.current_index])
        self.pending_track_log = metadata.display_title
        self.on_air.show_track(metadata.display_title, self.tracks[self.current_index])
        self.broadcast.set_title(format_broadcast_title(
            self.station_metadata["title_format"], metadata, self.station_metadata))
        self.log_event("music", metadata.display_title)
        self.record_track_started(metadata.path)
        self.pending_track_log = None
        self.playlist.selectRow(self.current_index)
        self.update_duration(self.player.duration())
        self.update_position(self.player.position())
        self.silence_ms = 0
        self.apply_pending_resume_position()
        self.save_state()

    @guarded
    def cancel_mix(self):
        had_active_mix = self.mix_target_index >= 0
        self.mix_target_index = -1
        self.mix_fade_duration_ms = 0
        self.mix_fade_started = False
        self.mix_cue_ms = 0
        self.gain_ramps.pop("mix", None)
        if hasattr(self, "mix_player"):
            self.mix_player.stop()
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

    @guarded
    def apply_pending_resume_position(self):
        if (self.pending_resume_position is not None and self.player.isSeekable()
                and self.player.duration() > 0
                and self.player.mediaStatus() in (QMediaPlayer.LoadedMedia,
                                                  QMediaPlayer.BufferedMedia)):
            self.player.setPosition(min(self.pending_resume_position,
                                        max(0, self.player.duration() - 1000)))
            self.pending_resume_position = None
