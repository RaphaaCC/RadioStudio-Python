import unittest
import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.persistence import PersistenceMixin


class _Player:
    def __init__(self, path, position, state):
        self.path = path
        self._position = position
        self._state = state

    def playbackState(self):
        return self._state

    def position(self):
        return self._position

    def source(self):
        return QUrl.fromLocalFile(self.path)


class _PlaybackCaptureHarness(PersistenceMixin):
    def __init__(self, mix_state):
        self.player = _Player("outgoing.mp3", 179000, QMediaPlayer.PausedState)
        self.mix_player = _Player("incoming.mp3", 42000, mix_state)
        self.mix_target_index = 1
        self.mix_gain = 0.9
        self.main_gain = 0.1
        self.tracks = ["outgoing.mp3", "incoming.mp3"]
        self.decoded_audio_sources = {}
        self.last_playback = {}

    def original_media_path(self, player):
        return player.source().toLocalFile()


class PersistenceRegressionTests(unittest.TestCase):
    def test_resume_snapshot_uses_incoming_deck_when_crossfade_is_paused(self):
        player = _PlaybackCaptureHarness(QMediaPlayer.PausedState)

        PersistenceMixin.capture_playback(player)

        self.assertEqual(player.last_playback, {
            "path": "incoming.mp3", "index": 1,
            "position_ms": 42000, "was_playing": False,
        })

    def test_resume_restores_the_exact_occurrence_of_a_duplicate_track(self):
        class Harness(PersistenceMixin):
            def __init__(self, path):
                self.resume_on_startup = True
                self.last_playback = {"path": str(path), "index": 2,
                                      "position_ms": 5000, "was_playing": True}
                self.tracks = [str(path), "between.mp3", str(path), "after.mp3"]
                self.track_sources = ["manual"] * len(self.tracks)
                self.started = None

            def play_index(self, index, position):
                self.started = (index, position)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.mp3"
            path.write_bytes(b"audio")
            harness = Harness(path)

            PersistenceMixin.restore_playback(harness)

        self.assertEqual(harness.started, (2, 5000))


if __name__ == "__main__":
    unittest.main()
