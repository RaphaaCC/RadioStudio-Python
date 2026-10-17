import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.playback import PlaybackMixin
from radiostudio.ui.audio_pipeline import AudioPipelineMixin, trailing_silence_scan_window


class _FakeSource:
    def toLocalFile(self):
        return "bad-track.mp3"


class _FakePlayer:
    def source(self):
        return _FakeSource()


class _FakeAudio:
    def setVolume(self, _value):
        pass


class _FakeTimer:
    def __init__(self):
        self.active = False

    def isActive(self):
        return self.active

    def start(self):
        self.active = True

    def stop(self):
        self.active = False


class _GainAudio:
    def __init__(self):
        self.volume = 0.0

    def setVolume(self, value):
        self.volume = value


class _SeekPlayer:
    def __init__(self):
        self.requested_position = None

    def setPosition(self, value):
        self.requested_position = value


class _StatePlayer:
    def __init__(self, state):
        self.state = state

    def playbackState(self):
        return self.state

    def duration(self):
        return 10000

    def position(self):
        return 5000

    def pause(self):
        self.state = QMediaPlayer.PausedState


class _ValidBuffer:
    def isValid(self):
        return True


class _AudioPipelineHarness(AudioPipelineMixin):
    def __init__(self):
        self.player = _StatePlayer(QMediaPlayer.PausedState)
        self.mix_player = _StatePlayer(QMediaPlayer.PlayingState)
        self.master_volume = 1.0
        self.mix_gain = 1.0
        self.forwarded = []

    def forward_buffer(self, *args):
        self.forwarded.append(args)


class _GainHarness(PlaybackMixin):
    def __init__(self):
        self.gain_ramps = {}
        self.gain_timer = _FakeTimer()
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.master_volume = 1.0
        self.audio = _GainAudio()
        self.mix_audio = _GainAudio()
        self.auto_transitions_enabled = False

    def apply_main_volume(self):
        self.audio.setVolume(self.master_volume * self.main_gain)


class _FakeStatusBar:
    def showMessage(self, _message):
        pass


class _FakeOnAir:
    def clear_track(self):
        pass


class _PlaybackHarness(PlaybackMixin):
    def __init__(self):
        self.player = _FakePlayer()
        self.gain_ramps = {}
        self.gain_timer = _FakeTimer()
        self.main_gain = 1.0
        self.master_volume = 0.8
        self.audio = _FakeAudio()
        self.on_air = _FakeOnAir()
        self.last_playback = {"was_playing": True}
        self.is_quitting = False
        self.error_messages = []
        self.next_calls = 0

    def apply_main_volume(self):
        pass

    def statusBar(self):
        return _FakeStatusBar()

    def log_event(self, kind, message):
        self.error_messages.append((kind, message))

    def save_state(self):
        pass

    def next_track(self):
        self.next_calls += 1


class PlaybackErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_invalid_track_is_logged_and_skipped_without_crashing(self):
        player = _PlaybackHarness()
        PlaybackMixin.media_status_changed(player, QMediaPlayer.InvalidMedia)
        self.app.processEvents()

        self.assertEqual(player.next_calls, 1)
        self.assertTrue(player.error_messages)
        self.assertEqual(player.error_messages[0][0], "error")
        self.assertFalse(player.last_playback["was_playing"])

    def test_mix_gain_ramps_use_equal_power_curve(self):
        player = _GainHarness()
        with patch("radiostudio.ui.playback.time.monotonic", return_value=0):
            PlaybackMixin.ramp_gain(
                player, "main", 0, 1000, curve="equal_power_out")
            PlaybackMixin.ramp_gain(
                player, "mix", 1, 1000, curve="equal_power_in")
        with patch("radiostudio.ui.playback.time.monotonic", return_value=0.5):
            PlaybackMixin.update_gain_ramps(player)

        self.assertAlmostEqual(player.main_gain, 2 ** -0.5)
        self.assertAlmostEqual(player.mix_gain, 2 ** -0.5)
        self.assertAlmostEqual(player.audio.volume ** 2 + player.mix_audio.volume ** 2, 1.0)

    def test_mix_fade_waits_until_both_decks_are_playing(self):
        player = _GainHarness()
        player.player = _StatePlayer(QMediaPlayer.PausedState)
        player.mix_player = _StatePlayer(QMediaPlayer.PlayingState)
        player.mix_target_index = 1
        player.mix_fade_started = False
        player.mix_fade_duration_ms = 1000

        PlaybackMixin.begin_mix_fade(player)

        self.assertFalse(player.mix_fade_started)
        self.assertEqual(player.gain_ramps, {})

    def test_paused_main_deck_suppresses_late_mix_audio(self):
        player = _AudioPipelineHarness()

        AudioPipelineMixin.handle_player_audio_buffer(
            player, player.mix_player, _ValidBuffer())

        self.assertEqual(player.forwarded, [])

    def test_late_mix_start_is_paused_when_main_deck_is_paused(self):
        player = _GainHarness()
        player.player = _StatePlayer(QMediaPlayer.PausedState)
        player.mix_player = _StatePlayer(QMediaPlayer.PlayingState)
        player.mix_target_index = 1

        PlaybackMixin.slot_playback_state_changed(
            player, player.mix_player, QMediaPlayer.PlayingState)

        self.assertEqual(player.mix_player.playbackState(), QMediaPlayer.PausedState)

    def test_static_tail_analysis_cannot_promote_a_mix_before_media_end(self):
        player = _GainHarness()
        player.auto_transitions_enabled = True
        player.player = _StatePlayer(QMediaPlayer.PlayingState)
        player.mix_target_index = 1
        player.mix_fade_started = True
        player.mix_early_promotion = True  # Legacy state must not trigger promotion.
        player.main_gain = 0.0
        player.mix_gain = 1.0
        player.promote_calls = 0
        player.promote_mix_player = lambda: setattr(
            player, "promote_calls", player.promote_calls + 1)

        PlaybackMixin.update_gain_ramps(player)

        self.assertEqual(player.promote_calls, 0)

    def test_silence_detection_is_limited_to_track_tail(self):
        self.assertEqual(trailing_silence_scan_window(240000, 30000, 30000), 15000)
        self.assertEqual(trailing_silence_scan_window(20000, 30000, 30000), 5000)
        self.assertEqual(trailing_silence_scan_window(240000, 0, 0), 6000)

    def test_seeking_during_crossfade_cancels_the_pending_deck(self):
        player = _GainHarness()
        player.player = _SeekPlayer()
        player.mix_target_index = 1
        player.stop_pending = False
        player.cancelled = False

        def cancel_pending_transition():
            player.cancelled = True
            player.mix_target_index = -1

        player.cancel_pending_transition = cancel_pending_transition
        PlaybackMixin.seek_main_player(player, 42500)

        self.assertTrue(player.cancelled)
        self.assertEqual(player.player.requested_position, 42500)

    def test_seek_cancels_a_pending_stop_fade(self):
        player = _GainHarness()
        player.mix_target_index = -1
        player.stop_pending = True
        player.pending_transition_kind = "stop"
        player.pending_transition_action = lambda: None
        player.cancelled_action = False
        player.cancel_transition_action = lambda: setattr(player, "cancelled_action", True)
        player.player = _SeekPlayer()

        PlaybackMixin.cancel_pending_transition(player)

        self.assertTrue(player.cancelled_action)
        self.assertFalse(player.stop_pending)
        self.assertNotIn("main", player.gain_ramps)
        self.assertEqual(player.main_gain, 1.0)


if __name__ == "__main__":
    unittest.main()
