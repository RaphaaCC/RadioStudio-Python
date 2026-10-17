import tempfile
import unittest
from pathlib import Path

from radiostudio.services.autodj import choose_track, reserve_track


class AutoDJQueueRegressionTests(unittest.TestCase):
    def test_reserved_queue_artist_obeys_artist_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            a1 = Path(folder) / "a1.mp3"
            a2 = Path(folder) / "a2.mp3"
            b1 = Path(folder) / "b1.mp3"
            for path in (a1, a2, b1):
                path.write_bytes(b"audio")
            first = {"path": str(a1), "artist": "Artist A", "title": "One"}
            same_artist = {"path": str(a2), "artist": "Artist A", "title": "Two"}
            other_artist = {"path": str(b1), "artist": "Artist B", "title": "Three"}
            history = ({}, {})

            reserve_track(history, first, now=1000)
            chosen, note = choose_track(
                [same_artist, other_artist], history,
                artist_cooldown_minutes=60, now=1000,
            )

            self.assertEqual(chosen, other_artist)
            self.assertEqual(note, "")

    def test_never_played_track_is_not_recent_when_clock_is_small(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new.mp3"
            path.write_bytes(b"audio")
            track = {"path": str(path), "artist": "New Artist"}

            chosen, note = choose_track(
                [track], ({}, {}), artist_cooldown_minutes=60,
                track_cooldown_minutes=60, now=1000,
            )

            self.assertEqual(chosen, track)
            self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
