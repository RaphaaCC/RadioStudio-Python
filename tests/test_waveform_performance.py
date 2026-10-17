import os
import subprocess
import sys
import unittest


class WaveformPerformanceTests(unittest.TestCase):
    def test_reactive_visualizer_is_rate_limited_without_dropping_samples(self):
        harness = r'''
from array import array
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import LIVE_WAVEFORM_MIN_INTERVAL, Waveform

app = QApplication([])
waveform = Waveform()
waveform.set_style("reactive_wave")
updates = []
waveform._update_reactive_wave = lambda: updates.append(len(waveform._samples))
pcm = array("h", [1000, -1000] * 882).tobytes()
with patch("radiostudio.ui.studio_widgets.time.monotonic",
           side_effect=[1.0, 1.01, 1.04]):
    for _ in range(3):
        waveform.feed_audio_buffer(pcm, 2, 44100)
assert len(waveform._samples) == 882 * 3
assert updates == [882, 2646]
assert LIVE_WAVEFORM_MIN_INTERVAL <= 1 / 30
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_live_visualizer_does_not_decode_the_whole_track_again(self):
        harness = r'''
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
panel.set_waveform_style("spectrum")
panel.show_track("Faixa sintética", "arquivo-de-teste.wav")
assert panel._process is None
panel.clear_track()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


    def test_spectrum_is_rate_limited_and_skips_work_while_a_modal_dialog_is_open(self):
        harness = r'''
from array import array
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QDialog
from radiostudio.ui.studio_widgets import LIVE_SPECTRUM_MIN_INTERVAL, Waveform

app = QApplication([])
waveform = Waveform()
waveform.set_style("spectrum")
updates = []
waveform._update_spectrum = lambda samples, _rate: updates.append(len(samples))
pcm = array("h", [1000, -1000] * 2048).tobytes()
with patch("radiostudio.ui.studio_widgets.time.monotonic",
           side_effect=[1.0, 1.02, 1.08]):
    for _ in range(3):
        waveform.feed_audio_buffer(pcm, 2, 44100)
assert updates == [2048, 2048]
assert LIVE_SPECTRUM_MIN_INTERVAL >= 1 / 15

modal = QDialog()
modal.setModal(True)
modal.show()
app.processEvents()
assert app.activeModalWidget() is modal
with patch("radiostudio.ui.studio_widgets.time.monotonic",
           side_effect=AssertionError("não deve calcular espectro sob diálogo modal")):
    waveform.feed_audio_buffer(pcm, 2, 44100)
modal.close()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
