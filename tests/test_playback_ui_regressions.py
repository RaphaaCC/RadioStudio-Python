import unittest
from types import SimpleNamespace

from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.playback import PlaybackMixin


class _Slider:
    def __init__(self, down):
        self.down = down
        self.values = []

    def isSliderDown(self):
        return self.down

    def setValue(self, value):
        self.values.append(value)


class _PlaybackHarness(PlaybackMixin):
    def __init__(self, slider_down):
        self.position = _Slider(slider_down)
        self.position_label = SimpleNamespace(setText=lambda text: None)
        self.player = SimpleNamespace(
            duration=lambda: 180_000,
            playbackState=lambda: QMediaPlayer.StoppedState,
        )
        self.on_air = SimpleNamespace(set_progress=lambda *_args: None)
        self.is_quitting = False
        self.maybe_start_mix = lambda: None


class PlaybackUiRegressionTests(unittest.TestCase):
    def test_position_updates_do_not_fight_a_user_dragging_the_seek_slider(self):
        playback = _PlaybackHarness(slider_down=True)

        PlaybackMixin.update_position(playback, 45_000)

        self.assertEqual(playback.position.values, [])

    def test_position_updates_refresh_the_seek_slider_when_not_dragging(self):
        playback = _PlaybackHarness(slider_down=False)

        PlaybackMixin.update_position(playback, 45_000)

        self.assertEqual(playback.position.values, [45_000])


if __name__ == "__main__":
    unittest.main()
