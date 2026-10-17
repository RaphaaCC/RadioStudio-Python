"""Main-window actions for live audio inputs and microphone music ducking."""

import math

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_inputs import INPUT_LABELS


class AudioInputsControllerMixin:
    @guarded
    def set_audio_input_active(self, channel: str, enabled: bool):
        if channel not in INPUT_LABELS:
            return
        if enabled:
            started = self.audio_input_controller.start(channel)
            self.audio_inputs_panel.set_enabled(channel, started)
            if not started:
                self.statusBar().showMessage(f"Não foi possível ativar {INPUT_LABELS[channel]}")
            else:
                if channel == "microphone":
                    self.mic_active = True
                    self.update_music_duck_target()
                self.log_event("connected", f"{INPUT_LABELS[channel]} ativado")
                self.statusBar().showMessage(f"{INPUT_LABELS[channel]} enviado ao master")
        else:
            was_active = channel in self.audio_input_controller.active
            self.audio_input_controller.stop(channel)
            self.audio_inputs_panel.set_enabled(channel, False)
            if channel == "microphone":
                self.mic_active = False
                self.mic_speaking = False
                self.update_music_duck_target()
            if was_active:
                self.log_event("disconnected", f"{INPUT_LABELS[channel]} desativado")
                self.statusBar().showMessage(f"{INPUT_LABELS[channel]} desativado")

    @guarded
    def audio_input_status_changed(self, channel: str, status: str):
        if channel == "monitor":
            if status.startswith(("Escuta local indisponível", "Escuta local interrompida", "Sem dispositivo")):
                self.log_event("error", status)
                self.statusBar().showMessage(status)
            return
        self.audio_inputs_panel.set_status(channel, status)
        if not status.startswith(("Ativo", "Desativado")):
            self.audio_inputs_panel.set_enabled(channel, False)
            if channel == "microphone":
                self.mic_active = False
                self.mic_speaking = False
                self.update_music_duck_target()
            self.log_event("error", f"{INPUT_LABELS.get(channel, channel)}: {status}")
            self.statusBar().showMessage(f"{INPUT_LABELS.get(channel, channel)}: {status}")

    @guarded
    def microphone_speech_changed(self, speaking: bool):
        self.mic_speaking = bool(speaking)
        if self.is_quitting:
            return
        self.update_music_duck_target()

    @guarded
    def update_music_duck_target(self):
        if not self.mic_active:
            target = 1.0
        elif self.dynamic_mic_duck and self.mic_speaking:
            target = min(self.mic_music_level_percent,
                         self.speech_music_level_percent) / 100.0
        else:
            target = self.mic_music_level_percent / 100.0
        self.music_duck_target = max(0.0, min(1.0, target))
        if abs(self.music_duck_gain - self.music_duck_target) <= 0.001:
            self.music_duck_gain = self.music_duck_target
            self.duck_timer.stop()
            self.apply_main_volume()
            self.mix_audio.setVolume(self.master_volume * self.mix_gain * self.music_duck_gain)
            return
        if not self.duck_timer.isActive():
            self.duck_timer.start()

    @guarded
    def update_music_ducking(self):
        difference = self.music_duck_target - self.music_duck_gain
        if abs(difference) <= 0.002:
            self.music_duck_gain = self.music_duck_target
            self.duck_timer.stop()
        else:
            # Short attack and a gentler release avoid abrupt level jumps.
            time_constant = 0.09 if difference < 0 else 0.42
            self.music_duck_gain += difference * (1 - math.exp(-0.02 / time_constant))
        self.apply_main_volume()
        self.mix_audio.setVolume(self.master_volume * self.mix_gain * self.music_duck_gain)
        for _player, output, _buffer, _source in self.cart_players:
            output.setVolume(0.8 * self.music_duck_gain)
