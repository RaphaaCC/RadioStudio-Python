"""Main-window actions for live audio inputs and microphone music ducking."""

import math

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_inputs import INPUT_LABELS
from ..i18n import translate


class AudioInputsControllerMixin:
    def _sync_audio_input_buttons(self, channel: str, enabled: bool):
        panel = getattr(self, "audio_inputs_panel", None)
        if panel is not None:
            panel.set_enabled(channel, enabled)
        if channel == "microphone":
            button = getattr(self, "quick_microphone_button", None)
            if button is not None and button.isChecked() != enabled:
                button.blockSignals(True)
                button.setChecked(enabled)
                button.blockSignals(False)

    @guarded
    def set_audio_input_active(self, channel: str, enabled: bool):
        if channel not in INPUT_LABELS:
            return
        input_label = translate(INPUT_LABELS[channel])
        if enabled:
            started = self.audio_input_controller.start(channel)
            self._sync_audio_input_buttons(channel, started)
            if not started:
                self.statusBar().showMessage(translate(
                    "Não foi possível ativar {v1}").format(v1=input_label))
            else:
                if channel == "microphone":
                    self.mic_active = True
                    self.update_music_duck_target()
                self.log_event("connected", f"{input_label} ativado")
                self.statusBar().showMessage(translate(
                    "{v0} enviado ao master").format(v0=input_label))
        else:
            was_active = channel in self.audio_input_controller.active
            self.audio_input_controller.stop(channel)
            self._sync_audio_input_buttons(channel, False)
            if channel == "microphone":
                self.mic_active = False
                self.mic_speaking = False
                self.update_music_duck_target()
            if was_active:
                self.log_event("disconnected", f"{input_label} desativado")
                self.statusBar().showMessage(translate(
                    "{v0} desativado").format(v0=input_label))

    @guarded
    def audio_input_status_changed(self, channel: str, status: str):
        if channel == "monitor":
            if status.startswith(("Escuta local indisponível", "Escuta local interrompida", "Sem dispositivo")):
                self.log_event("error", status)
                self.statusBar().showMessage(translate(status))
            return
        self.audio_inputs_panel.set_status(channel, status)
        if not status.startswith(("Ativo", "Desativado")):
            self._sync_audio_input_buttons(channel, False)
            if channel == "microphone":
                self.mic_active = False
                self.mic_speaking = False
                self.update_music_duck_target()
            input_label = translate(INPUT_LABELS.get(channel, channel))
            display_status = translate(status)
            message = f"{input_label}: {display_status}"
            self.log_event("error", message)
            self.statusBar().showMessage(message)

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
            self.mix_audio.setVolume(
                self.monitor_output_gain() * self.mix_gain * self.music_duck_gain)
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
        self.mix_audio.setVolume(
            self.monitor_output_gain() * self.mix_gain * self.music_duck_gain)
