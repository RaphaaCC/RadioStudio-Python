import unittest

from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.autodj_controller import AutoDJControllerMixin


class _StoppedPlayer:
    def playbackState(self):
        return QMediaPlayer.StoppedState


class _AutoDJHarness:
    def __init__(self):
        self.autodj_config = {"enabled": True}
        self.player = _StoppedPlayer()
        self.tracks = ["first.mp3", "second.mp3"]
        self.pending_track_log = None
        self.next_calls = 0
        self.fill_calls = 0

    def fill_autodj_queue(self, **_kwargs):
        self.fill_calls += 1
        # The queue fill starts a track, but Qt has not emitted PlayingState yet.
        self.pending_track_log = "First track"

    def next_track(self):
        self.next_calls += 1


class AutoDJStartupTests(unittest.TestCase):
    def test_startup_does_not_skip_track_while_media_is_loading(self):
        harness = _AutoDJHarness()
        AutoDJControllerMixin.start_autodj_if_needed(harness)
        self.assertEqual(harness.fill_calls, 1)
        self.assertEqual(harness.next_calls, 0)


if __name__ == "__main__":
    unittest.main()
