import os
import subprocess
import sys
import unittest


class WaveformPerformanceTests(unittest.TestCase):
    def test_disabled_visualizers_hide_and_skip_analysis_and_live_pcm(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
panel._start_waveform_analysis = Mock()
panel.set_waveform_visualizers_enabled(False)
assert panel.waveform.isHidden()
panel.show_track("Faixa", "faixa.mp3")
panel.set_waveform_style("reactive_wave")
panel.feed_audio_buffer(object())
panel.show()
app.processEvents()
assert not panel._start_waveform_analysis.called
assert panel.waveform.isHidden()
panel.set_waveform_visualizers_enabled(True)
assert not panel.waveform.isHidden()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_disabling_visualizers_cancels_running_static_analysis(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
process = Mock()
process.state.return_value = QProcess.Running
panel._process = process
panel._wave_processes[id(process)] = process
panel.set_waveform_visualizers_enabled(False)
assert panel._process is None
process.kill.assert_called_once_with()
process.deleteLater.assert_not_called()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_hidden_panel_defers_static_analysis_until_visible_and_cancels_on_hide(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
panel._start_waveform_analysis = Mock()
panel.show_track("Faixa", "faixa.mp3")
assert not panel._start_waveform_analysis.called
panel.show()
app.processEvents()
panel._start_waveform_analysis.assert_called_once_with("faixa.mp3")

process = Mock()
process.state.return_value = QProcess.Running
panel._process = process
panel.hide()
assert panel._process is None
process.kill.assert_called_once_with()
process.deleteLater.assert_not_called()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_switching_static_visualizer_keeps_the_existing_analysis_process(self):
        harness = r'''
from unittest.mock import Mock, patch
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
panel.show()
app.processEvents()
panel._track_path = "faixa.mp3"
process = Mock()
process.state.return_value = QProcess.Running
panel._process = process
panel._wave_processes[id(process)] = process
with patch("radiostudio.ui.studio_widgets.QProcess") as process_factory:
    panel.set_waveform_style("line")
    process_factory.assert_not_called()
assert panel._process is process
assert list(panel._wave_processes.values()) == [process]
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_progress_paint_is_skipped_when_change_is_subpixel(self):
        harness = r'''
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import Waveform

app = QApplication([])
waveform = Waveform()
waveform.resize(1000, 32)
updates = []
waveform.update = lambda: updates.append(waveform.progress)
waveform.set_progress(0.25)
waveform.set_progress(0.2501)
waveform.set_progress(0.251)
assert updates == [0.25, 0.251]
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_progress_labels_are_not_rewritten_for_same_displayed_second(self):
        harness = r'''
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

class Label:
    def __init__(self):
        self.value = ""
        self.writes = 0
    def text(self):
        return self.value
    def setText(self, value):
        self.value = value
        self.writes += 1

app = QApplication([])
panel = OnAirPanel()
panel.elapsed = Label()
panel.remaining = Label()
panel.waveform.set_progress = lambda _progress: None
panel.set_progress(1100, 60000)
panel.set_progress(1101, 60000)
assert panel.elapsed.value == "00:01" and panel.elapsed.writes == 1
assert panel.remaining.value == "−00:58" and panel.remaining.writes == 1
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_live_visualizer_downmixes_stereo_and_normalizes_mono_samples(self):
        harness = r'''
from array import array
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import Waveform

app = QApplication([])
waveform = Waveform()
waveform.set_style("reactive_wave")
waveform.feed_audio_buffer(array("h", [32767, -32768, 16384, 16384]).tobytes(), 2, 44100)
stereo = list(waveform._samples)
assert len(stereo) == 2
assert abs(stereo[0] - (-1 / 65536)) < 1e-9
assert abs(stereo[1] - 0.5) < 1e-9
waveform.reset_live()
waveform.feed_audio_buffer(array("h", [0, 16384, -32768]).tobytes(), 1, 44100)
assert list(waveform._samples) == [0.0, 0.5, -1.0]
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

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

    def test_canceled_waveform_process_is_kept_until_it_finishes(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
process = Mock()
process.state.return_value = QProcess.Running
panel._process = process
panel._cancel_analysis()
assert panel._process is None
process.kill.assert_called_once_with()
process.deleteLater.assert_not_called()

process.state.return_value = QProcess.NotRunning
panel._finish_wave(process, -1)
process.deleteLater.assert_called_once_with()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_waveform_process_failed_to_start_is_released(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
process = Mock()
panel._process = process
panel._wave_data.extend(b"stale")
panel._wave_process_error(process, QProcess.FailedToStart)
assert panel._process is None
assert not panel._wave_data
process.deleteLater.assert_called_once_with()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_visualizer_shutdown_waits_for_its_killed_process(self):
        harness = r'''
from unittest.mock import Mock
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
process = Mock()
process.state.side_effect = [QProcess.Running, QProcess.Running, QProcess.NotRunning]
panel._process = process
panel._wave_processes[id(process)] = process
panel.shutdown_analysis()
assert panel._process is None
assert not panel._wave_processes
process.kill.assert_called()
process.waitForFinished.assert_called_once_with(2000)
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_visualizer_closes_without_destroying_a_running_child_process(self):
        harness = r'''
import sys
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
process = QProcess(panel)
process.finished.connect(lambda code, _status: panel._finish_wave(process, code))
panel._process = process
panel._wave_processes[id(process)] = process
process.start(sys.executable, ["-c", "import time; time.sleep(30)"])
assert process.waitForStarted(3000)
panel.shutdown_analysis()
assert process.state() == QProcess.NotRunning
app.processEvents()
panel.close()
app.processEvents()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertNotIn("QProcess: Destroyed while process", result.stderr)

    def test_visualizer_shutdown_reaps_older_canceled_waveform_jobs(self):
        harness = r'''
import sys
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import OnAirPanel

app = QApplication([])
panel = OnAirPanel()
old = QProcess(panel)
current = QProcess(panel)
for process in (old, current):
    process.finished.connect(lambda code, _status, item=process: panel._finish_wave(item, code))
    panel._wave_processes[id(process)] = process
    process.start(sys.executable, ["-c", "import time; time.sleep(30)"])
    assert process.waitForStarted(3000)
panel._process = current
assert old.state() == QProcess.Running
assert current.state() == QProcess.Running
panel.shutdown_analysis()
assert old.state() == QProcess.NotRunning
assert current.state() == QProcess.NotRunning
assert not panel._wave_processes
app.processEvents()
panel.close()
app.processEvents()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertNotIn("QProcess: Destroyed while process", result.stderr)


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
waveform.reset_live()
samples_before_modal_audio = len(waveform._samples)
with patch("radiostudio.ui.studio_widgets.time.monotonic",
           side_effect=AssertionError("não deve calcular espectro sob diálogo modal")):
    waveform.feed_audio_buffer(pcm, 2, 44100)
assert len(waveform._samples) == samples_before_modal_audio
modal.close()
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_spectrum_keeps_a_synthetic_tone_in_its_frequency_band(self):
        harness = r'''
import math
from PySide6.QtWidgets import QApplication
from radiostudio.ui.studio_widgets import Waveform

app = QApplication([])
waveform = Waveform()
size = 2048
waveform._update_spectrum(
    [0.8 * math.sin(2 * math.pi * 16 * index / size) for index in range(size)], 44100)
peak = max(range(len(waveform.live_spectrum)), key=waveform.live_spectrum.__getitem__)
assert 17 <= peak <= 19, peak
assert all(math.isfinite(level) and 0 <= level <= 1 for level in waveform.live_spectrum)
'''
        result = subprocess.run(
            [sys.executable, "-c", harness], capture_output=True, text=True,
            timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
