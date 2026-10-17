import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.audio_pipeline import AudioPipelineMixin
from radiostudio.ui.playback import PlaybackMixin


class _Buffer:
    def isValid(self):
        return True


class _OnAir:
    def feed_audio_buffer(self, _buffer):
        pass


class _Player:
    def __init__(self, *, state=QMediaPlayer.PlayingState, duration=10000, position=9500):
        self._state = state
        self._duration = duration
        self._position = position

    def playbackState(self):
        return self._state

    def duration(self):
        return self._duration

    def position(self):
        return self._position


class _PipelineHarness(AudioPipelineMixin):
    def __init__(self):
        self.player = _Player()
        self.mix_player = _Player(state=QMediaPlayer.StoppedState)
        self.main_gain = 0.75
        self.mix_gain = 0.0
        self.master_volume = 0.2
        self.on_air = _OnAir()
        self.forwarded = []

    def detect_trailing_silence(self, _buffer):
        pass

    def forward_buffer(self, *args):
        self.forwarded.append(args)


class _MixHarness(PlaybackMixin):
    def __init__(self):
        self.mix_target_index = -1
        self.current_index = 0
        self.tracks = ["current.mp3", "next.mp3"]
        self.stop_pending = False
        self.player = _Player()
        self.auto_transitions_enabled = False
        self.mix_ms = 0
        self.fade_out_ms = 2500
        self.fade_in_ms = 2500
        self.started = []

    def start_mix(self, *args, **kwargs):
        self.started.append((args, kwargs))


class FadeMixRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_local_monitor_volume_does_not_attenuate_broadcast_program(self):
        harness = _PipelineHarness()

        AudioPipelineMixin.handle_player_audio_buffer(harness, harness.player, _Buffer())

        self.assertEqual(len(harness.forwarded), 1)
        source, _buffer, gain = harness.forwarded[0]
        self.assertEqual(source, "main")
        self.assertAlmostEqual(gain, 0.75)

    def test_fade_settings_alone_do_not_create_an_automatic_mix(self):
        harness = _MixHarness()

        PlaybackMixin.maybe_start_mix(harness)

        self.assertEqual(harness.started, [])


if __name__ == "__main__":
    unittest.main()
