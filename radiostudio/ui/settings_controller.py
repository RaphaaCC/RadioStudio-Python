"""Settings Controller operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


class SettingsControllerMixin:
    @guarded
    def show_settings(self):
        current_track = (read_track_metadata(self.tracks[self.current_index])
                         if 0 <= self.current_index < len(self.tracks) else None)
        dialog = SettingsDialog(self.main_device_key, self.preview_device_key,
                                self.local_master_enabled, self.station_metadata,
                                current_track, self,
                                minimize_to_tray=self.minimize_to_tray,
                                resume_on_startup=self.resume_on_startup,
                                tray_available=self.tray_icon is not None,
                                player_controls_position=self.player_controls_position,
                                waveform_style=self.waveform_style,
                                auto_start_broadcast=self.auto_start_broadcast,
                                auto_transitions_enabled=self.auto_transitions_enabled,
                                fade_in_ms=self.fade_in_ms, fade_out_ms=self.fade_out_ms,
                                mix_ms=self.mix_ms, trim_silence=self.trim_silence,
                                silence_threshold_db=self.silence_threshold_db,
                                silence_hold_ms=self.silence_hold_ms,
                                input_device_keys=self.input_device_keys,
                                mic_music_level_percent=self.mic_music_level_percent,
                                dynamic_mic_duck=self.dynamic_mic_duck,
                                speech_music_level_percent=self.speech_music_level_percent,
                                speech_threshold_db=self.speech_threshold_db)
        if dialog.exec() != QDialog.Accepted:
            return
        (self.main_device_key, self.preview_device_key,
         self.local_master_enabled, self.station_metadata,
         self.minimize_to_tray, self.resume_on_startup,
         controls_position, waveform_style, self.auto_start_broadcast,
         self.auto_transitions_enabled,
         self.fade_in_ms, self.fade_out_ms, self.mix_ms, self.trim_silence,
         self.silence_threshold_db, self.silence_hold_ms,
         self.input_device_keys, self.mic_music_level_percent, self.dynamic_mic_duck,
         self.speech_music_level_percent, self.speech_threshold_db) = dialog.values()
        self.set_player_controls_position(controls_position)
        self.waveform_style = waveform_style
        self.on_air.set_waveform_style(waveform_style)
        if self.auto_transitions_enabled and 0 <= self.current_index < len(self.tracks):
            self.request_transition_analysis(self.tracks[self.current_index])
            if self.current_index + 1 < len(self.tracks):
                self.request_transition_analysis(self.tracks[self.current_index + 1])
        self.apply_audio_devices()
        self.audio_input_controller.speech_threshold_db = self.speech_threshold_db
        self.update_music_duck_target()
        # The modal settings window can stay open long enough for playback to
        # advance. Re-read the current row here instead of publishing metadata
        # captured when the dialog was opened.
        current_on_air = (read_track_metadata(self.tracks[self.current_index])
                          if 0 <= self.current_index < len(self.tracks) else None)
        if current_on_air and self.player.playbackState() != QMediaPlayer.StoppedState:
            self.broadcast.set_title(format_broadcast_title(
                self.station_metadata["title_format"], current_on_air, self.station_metadata))
        if self.broadcast.local_server:
            server = self.broadcast.local_server
            server.station = self.broadcast_panel.local_config()["station"] or self.station_metadata["name"]
            server.description = self.station_metadata["description"]
            server.genre = self.station_metadata["genre"]
            server.url = self.station_metadata["url"]
        self.save_state()
        self.statusBar().showMessage(
            "Saída local do master ativa" if self.local_master_enabled
            else "Master local silenciado; transmissão continua ativa")

    @guarded
    def apply_audio_devices(self):
        self.audio.setDevice(resolve_output_device(self.main_device_key))
        self.audio.setMuted(not self.local_master_enabled)
        self.mix_audio.setDevice(resolve_output_device(self.main_device_key))
        self.mix_audio.setMuted(not self.local_master_enabled)
        self.preview_player.stop()
        self.preview_audio.setDevice(resolve_output_device(self.preview_device_key))
        for _player, output, _buffer, _source in self.cart_players:
            output.setDevice(resolve_output_device(self.main_device_key))
            output.setMuted(not self.local_master_enabled)
        self.audio_input_controller.configure(
            self.input_device_keys, self.main_device_key, self.local_master_enabled,
            self.master_volume, self.speech_threshold_db)

    @guarded
    def toggle_prelisten(self):
        row = self.playlist.currentRow()
        if row < 0:
            self.statusBar().showMessage("Selecione uma faixa para escutar")
            return
        path = self.tracks[row]
        if not Path(path).is_file():
            QMessageBox.warning(self, "Arquivo ausente", path)
            return
        self.preview_dialog.load_track(path)
        self.statusBar().showMessage(f"Pré-escuta: {Path(path).stem}")

    @guarded
    def stop_prelisten(self):
        self.preview_dialog.stop()
        self.statusBar().showMessage("Pré-escuta parada")
