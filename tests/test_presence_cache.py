from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.main_window import RadioStudio


class DiscordPresenceCacheTests(unittest.TestCase):
    def test_track_metadata_is_read_once_during_presence_refreshes(self):
        player = Mock()
        player.playbackState.return_value = QMediaPlayer.PlayingState
        player.position.return_value = 1000
        player.duration.return_value = 180000
        mix_player = Mock()
        mix_player.playbackState.return_value = QMediaPlayer.StoppedState
        presence = Mock()
        app = SimpleNamespace(
            discord_client_id="123456789012345678",
            discord_enabled=True,
            player=player,
            mix_player=mix_player,
            mix_target_index=-1,
            mix_gain=0.0,
            main_gain=1.0,
            current_index=0,
            tracks=[r"C:\music\song.mp3"],
            broadcast=SimpleNamespace(running=False),
            discord_presence=presence,
            station_metadata={"name": "Radio"},
            discord_share_track=True,
            discord_use_track_cover=True,
            discord_cover_url="https://radio.example/cover",
            _discord_cover_version="a1b2c3d4e5f60718",
            discord_public_url="",
            discord_large_image="radiostudio",
            discord_activity_name="RadioStudio Python Sirius",
            discord_activity_type=2,
            discord_status_display_type=0,
            discord_details_template="%title",
            discord_state_template="%artist",
            discord_large_text="RadioStudio",
            discord_button1_label="Ouvir",
            discord_button2_label="",
            discord_button2_url="",
            _discord_track_signature=None,
            _discord_track_started_at=0,
            _discord_was_paused=False,
            _discord_metadata_cache={},
        )
        metadata = SimpleNamespace(
            path=app.tracks[0], title="Song", artist="Artist", album="Album", duration=180.0,
        )
        with (patch("radiostudio.ui.main_window.read_track_metadata", return_value=metadata)
              as read_metadata,
              patch("radiostudio.ui.main_window.make_activity", side_effect=lambda **data: data)):
            RadioStudio.sync_discord_presence(app)
            RadioStudio.sync_discord_presence(app)

        read_metadata.assert_called_once_with(app.tracks[0])
        self.assertEqual(presence.set_activity.call_count, 2)
        self.assertEqual(presence.set_activity.call_args.args[0]["title"], "Song")
        self.assertEqual(presence.set_activity.call_args.args[0]["cover_url"],
                         "https://radio.example/cover")
        self.assertEqual(presence.set_activity.call_args.args[0]["cover_version"],
                         "a1b2c3d4e5f60718")

    def test_disabled_presence_skips_playback_and_metadata_work(self):
        app = SimpleNamespace(
            discord_client_id="123456789012345678",
            discord_enabled=False,
            discord_presence=Mock(),
        )
        with patch("radiostudio.ui.main_window.read_track_metadata") as read_metadata:
            RadioStudio.sync_discord_presence(app)

        read_metadata.assert_not_called()
        app.discord_presence.set_activity.assert_not_called()

    def test_private_presence_skips_track_metadata_reads(self):
        player = Mock()
        player.playbackState.return_value = QMediaPlayer.PlayingState
        player.position.return_value = 1000
        player.duration.return_value = 180000
        mix_player = Mock()
        mix_player.playbackState.return_value = QMediaPlayer.StoppedState
        app = SimpleNamespace(
            discord_client_id="123456789012345678", discord_enabled=True,
            player=player, mix_player=mix_player, mix_target_index=-1,
            mix_gain=0.0, main_gain=1.0, current_index=0,
            tracks=[r"C:\music\private-song.mp3"],
            broadcast=SimpleNamespace(running=False), discord_presence=Mock(),
            station_metadata={"name": "Radio"}, discord_share_track=False,
            discord_use_track_cover=True, discord_cover_url="https://radio.example/cover",
            _discord_cover_version="a1b2c3d4e5f60718", discord_public_url="",
            discord_large_image="radiostudio", discord_activity_name="RadioStudio",
            discord_activity_type=2, discord_status_display_type=0,
            discord_details_template="%title", discord_state_template="%artist",
            discord_large_text="RadioStudio", discord_button1_label="Listen",
            discord_button2_label="", discord_button2_url="",
            _discord_track_signature=None, _discord_track_started_at=0,
            _discord_was_paused=False, _discord_metadata_cache={},
        )
        with patch("radiostudio.ui.main_window.read_track_metadata") as read_metadata:
            RadioStudio.sync_discord_presence(app)

        read_metadata.assert_not_called()
        activity = app.discord_presence.set_activity.call_args.args[0]
        self.assertEqual(activity["details"], "📻 RadioStudio Python")
        self.assertNotIn("start", activity)
        self.assertNotIn("end", activity)

    def test_resuming_a_paused_track_reanchors_the_discord_timer(self):
        player = Mock()
        player.playbackState.side_effect = [
            QMediaPlayer.PausedState, QMediaPlayer.PlayingState]
        player.position.return_value = 30_000
        player.duration.return_value = 180_000
        mix_player = Mock()
        mix_player.playbackState.return_value = QMediaPlayer.StoppedState
        app = SimpleNamespace(
            discord_client_id="123456789012345678", discord_enabled=True,
            player=player, mix_player=mix_player, mix_target_index=-1,
            mix_gain=0.0, main_gain=1.0, current_index=0,
            tracks=[r"C:\music\song.mp3"], broadcast=SimpleNamespace(running=False),
            discord_presence=Mock(), station_metadata={"name": "Radio"},
            discord_share_track=True, discord_use_track_cover=False,
            discord_cover_url="", _discord_cover_version="", discord_public_url="",
            discord_large_image="radiostudio", discord_activity_name="RadioStudio",
            discord_activity_type=2, discord_status_display_type=0,
            discord_details_template="%title", discord_state_template="%artist",
            discord_large_text="RadioStudio", discord_button1_label="Listen",
            discord_button2_label="", discord_button2_url="",
            _discord_track_signature=None, _discord_track_started_at=0,
            _discord_was_paused=False, _discord_metadata_cache={},
        )
        metadata = SimpleNamespace(
            path=app.tracks[0], title="Song", artist="Artist", album="Album",
            duration=180.0)
        with (patch("radiostudio.ui.main_window.read_track_metadata", return_value=metadata),
              patch("radiostudio.ui.main_window.make_activity", side_effect=lambda **data: data),
              patch("radiostudio.ui.main_window.time.time", side_effect=[1000.0, 1100.0])):
            RadioStudio.sync_discord_presence(app)
            RadioStudio.sync_discord_presence(app)

        resumed_activity = app.discord_presence.set_activity.call_args_list[1].args[0]
        self.assertEqual(resumed_activity["started_at"], 1070)
        self.assertEqual(resumed_activity["ends_at"], 1250)


if __name__ == "__main__":
    unittest.main()
