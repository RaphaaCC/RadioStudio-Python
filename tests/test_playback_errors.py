import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtMultimedia import QMediaPlayer

from radiostudio.ui.playback import PlaybackMixin
from radiostudio.ui.audio_pipeline import AudioPipelineMixin, trailing_silence_scan_window


class _FakeSource:
    def toLocalFile(self):
        return "bad-track.mp3"


class _FakePlayer:
    def __init__(self):
        from PySide6.QtCore import QUrl
        self._source = QUrl.fromLocalFile("bad-track.mp3")

    def source(self):
        return self._source

    def setSource(self, source):
        self._source = source

    def mediaStatus(self):
        return QMediaPlayer.InvalidMedia


class _FakeAudio:
    def setVolume(self, _value):
        pass


class _DisplayPlayer:
    def __init__(self, path, duration=0, position=0, state=QMediaPlayer.PlayingState,
                 status=QMediaPlayer.LoadedMedia):
        self._source = QUrl.fromLocalFile(path)
        self._duration = duration
        self._position = position
        self._state = state
        self._status = status

    def source(self):
        return self._source

    def duration(self):
        return self._duration

    def position(self):
        return self._position

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def stop(self):
        self._state = QMediaPlayer.StoppedState


class _DisplaySlider:
    def __init__(self):
        self.range = None
        self.value = None
        self.slider_down = False

    def setRange(self, minimum, maximum):
        self.range = (minimum, maximum)

    def setValue(self, value):
        self.value = value

    def isSliderDown(self):
        return self.slider_down


class _DisplayLabel:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


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
        self.monitor_volume = 1.0
        self.master_live_volume = 1.0
        self.mix_gain = 1.0
        self.forwarded = []
        self.events = []
        self.on_air = type("OnAir", (), {"feed_audio_buffer":
                                         lambda _self, _buffer: self.events.append("visualizer")})()
        self.trim_silence = False
        self.main_gain = 1.0
        self.music_duck_gain = 1.0

    def forward_buffer(self, *args):
        self.forwarded.append(args)
        self.events.append("broadcast")


class _GainHarness(PlaybackMixin):
    def __init__(self):
        self.broadcast = _FakeBroadcast()
        self.gain_ramps = {}
        self.gain_timer = _FakeTimer()
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.monitor_volume = 1.0
        self.master_live_volume = 1.0
        self.audio = _GainAudio()
        self.mix_audio = _GainAudio()
        self.auto_transitions_enabled = False

    def apply_main_volume(self):
        self.audio.setVolume(self.master_live_volume * self.monitor_volume * self.main_gain)


class _FakeStatusBar:
    def showMessage(self, _message):
        pass


class _FakeOnAir:
    def __init__(self):
        self._track_path = "old-track.mp3"
        self.title = _FakeTextLabel("Faixa anterior")
        self.progress = None

    def clear_track(self):
        pass

    def show_track(self, title, path):
        self.title.setText(title)
        self.title.setToolTip(title)
        self._track_path = str(path)

    def set_progress(self, position, duration):
        self.progress = (position, duration)


class _FakeTextLabel:
    def __init__(self, text=""):
        self._text = text
        self.tooltip = ""

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setToolTip(self, text):
        self.tooltip = text

    def __eq__(self, other):
        return self._text == other


class _FakeDecodeFallback:
    def cancel(self, _player):
        pass

    def release(self, _path):
        pass

    def is_pending(self, _player):
        return False


class _SynchronousFailureFallback(_FakeDecodeFallback):
    def convert(self, player, source, callback):
        callback(source, "", "FFmpeg ausente")
        return False


class _FakeBroadcast:
    def __init__(self):
        self.removed_sources = []
        self.cleared_sources = []

    def remove_source(self, source):
        self.removed_sources.append(source)

    def clear_source(self, source, *, preserve_primed=False):
        self.cleared_sources.append((source, preserve_primed))


class _PromotionBroadcast:
    def promote_source(self, _incoming, _active):
        raise RuntimeError("saída de transmissão indisponível")

    def remove_source(self, _source):
        pass

    def clear_source(self, _source, *, preserve_primed=False):
        pass

    def set_title(self, _title):
        pass


class _DisplayHarness(PlaybackMixin):
    def __init__(self):
        self.player = _DisplayPlayer("next-track.mp3", 180000, 125000)
        self.current_index = 0
        self.tracks = ["next-track.mp3"]
        self.mix_target_index = -1
        self.decoded_audio_sources = {}
        self.on_air = _FakeOnAir()
        self.position = _DisplaySlider()
        self.position_label = _DisplayLabel()
        self.pending_track_log = None
        self.is_quitting = False
        self.save_calls = 0

    def save_state(self):
        self.save_calls += 1


class _PromotionHarness(_DisplayHarness):
    def __init__(self):
        super().__init__()
        self.player = _DisplayPlayer("old-track.mp3", 180000, 180000,
                                     QMediaPlayer.StoppedState)
        self.mix_player = _DisplayPlayer("next-track.mp3", 180000, 125000)
        self.audio = _FakeAudio()
        self.mix_audio = _FakeAudio()
        self.main_buffer_output = object()
        self.mix_buffer_output = object()
        self.current_index = 0
        self.tracks = ["old-track.mp3", "next-track.mp3"]
        self.mix_target_index = 1
        self.stop_pending = False
        self.is_quitting = False
        self._media_end_generation = 0
        self._pending_media_end = None
        self.mix_fade_duration_ms = 1000
        self.mix_fade_in_duration_ms = 1000
        self.mix_fade_out_duration_ms = 1000
        self.mix_fade_started = True
        self.mix_cue_ms = 0
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0
        self.pending_resume_position = None
        self.main_gain = 0.0
        self.mix_gain = 1.0
        self.gain_ramps = {"main": (0.0,), "mix": (1.0,)}
        self.broadcast = _PromotionBroadcast()
        self.station_metadata = {"title_format": ""}
        self.playlist = type("Playlist", (), {"selectRow": lambda _self, _index: None})()
        self.events = []
        self.saved = False

    def apply_main_volume(self):
        pass

    def log_event(self, kind, message):
        self.events.append((kind, message))

    def record_track_started(self, _path):
        pass

    def save_state(self):
        self.saved = True


class _PlaybackHarness(PlaybackMixin):
    def __init__(self):
        self.player = _FakePlayer()
        self.decoded_audio_sources = {}
        self.audio_fallback_attempts = {}
        self.audio_decode_fallback = _FakeDecodeFallback()
        self.broadcast = _FakeBroadcast()
        self.gain_ramps = {}
        self.gain_timer = _FakeTimer()
        self.main_gain = 1.0
        self.monitor_volume = 0.8
        self.master_live_volume = 1.0
        self.audio = _FakeAudio()
        self.on_air = _FakeOnAir()
        self.last_playback = {"was_playing": True}
        self.is_quitting = False
        self.stop_pending = False
        self._suppress_end_recovery = False
        self._main_source_generation = 0
        self.current_index = 0
        self.tracks = ["bad-track.mp3", "next-track.mp3"]
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

    def main_decode_fallback_ready(self, player, source, decoded, error):
        PlaybackMixin.handle_invalid_main_media(self, source, error)


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

    def test_synchronous_fallback_failure_only_advances_once(self):
        player = _PlaybackHarness()
        player.audio_decode_fallback = _SynchronousFailureFallback()
        with tempfile.TemporaryDirectory(prefix="radiostudio-playback-test-") as folder:
            source = Path(folder) / "bad-track.mp3"
            source.write_bytes(b"invalid audio")
            player.player.setSource(QUrl.fromLocalFile(str(source)))

            PlaybackMixin.media_status_changed(player, QMediaPlayer.InvalidMedia)
            self.app.processEvents()

        self.assertEqual(player.next_calls, 1)
        errors = [message for kind, message in player.error_messages if kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("FFmpeg ausente", errors[0])

    def test_deferred_invalid_media_skip_is_canceled_after_a_new_track_is_loaded(self):
        player = _PlaybackHarness()
        callbacks = []

        with patch("radiostudio.ui.playback.QTimer.singleShot",
                   side_effect=lambda _delay, callback: callbacks.append(callback)):
            PlaybackMixin.handle_invalid_main_media(player, "bad-track.mp3")
            self.assertEqual(len(callbacks), 1)
            PlaybackMixin.set_media_source(
                player, player.player, "replacement-track.mp3")
            callbacks[0]()

        self.assertEqual(player.next_calls, 0)

    def test_invalid_media_after_manual_stop_does_not_skip_next_track(self):
        player = _PlaybackHarness()
        player._suppress_end_recovery = True

        with patch("radiostudio.ui.playback.QTimer.singleShot") as single_shot:
            PlaybackMixin.handle_invalid_main_media(player, "bad-track.mp3")

        single_shot.assert_not_called()
        self.assertEqual(player.next_calls, 0)

    def test_end_of_media_transition_is_deferred_and_duplicate_signals_are_ignored(self):
        player = _PlaybackHarness()
        player.current_index = 0
        player.tracks = ["bad-track.mp3", "next-track.mp3"]
        player.player = _DisplayPlayer("bad-track.mp3", 10000, 10000,
                                       QMediaPlayer.StoppedState,
                                       QMediaPlayer.EndOfMedia)
        player.mix_target_index = -1
        player.stop_pending = False
        player._media_end_generation = 0
        player._pending_media_end = None
        callbacks = []

        with patch("radiostudio.ui.playback.QTimer.singleShot",
                   side_effect=lambda _delay, callback: callbacks.append(callback)):
            PlaybackMixin.media_status_changed(player, QMediaPlayer.EndOfMedia)
            PlaybackMixin.media_status_changed(player, QMediaPlayer.EndOfMedia)

        self.assertEqual(player.next_calls, 0)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual(player.next_calls, 1)

    def test_end_of_media_callback_does_not_advance_a_newly_selected_track(self):
        player = _PlaybackHarness()
        player.current_index = 0
        player.tracks = ["bad-track.mp3", "next-track.mp3"]
        player.mix_target_index = -1
        player.stop_pending = False
        player._media_end_generation = 0
        player._pending_media_end = None
        callbacks = []

        with patch("radiostudio.ui.playback.QTimer.singleShot",
                   side_effect=lambda _delay, callback: callbacks.append(callback)):
            PlaybackMixin.media_status_changed(player, QMediaPlayer.EndOfMedia)
        player.current_index = 1
        callbacks[0]()

        self.assertEqual(player.next_calls, 0)

    def test_next_track_waits_for_async_autodj_queue_instead_of_stopping(self):
        player = _PlaybackHarness()
        player.player = _DisplayPlayer(
            "current.mp3", 180000, 180000, QMediaPlayer.StoppedState,
            QMediaPlayer.EndOfMedia)
        player.current_index = 0
        player.tracks = ["current.mp3"]
        player.track_sources = ["manual"]
        player.autodj_config = {"enabled": True}
        player._autodj_fill_worker = object()
        player.pending_transition_kind = None
        player.gain_paused_at = None
        player.mix_target_index = -1
        player.cancel_pending_media_end = lambda: None
        player.cancel_transition_action = lambda: None
        player.cancel_mix = lambda: None
        player.fill_autodj_queue = lambda **_kwargs: None
        player.stop_calls = 0
        player.stop = lambda: setattr(player, "stop_calls", player.stop_calls + 1)

        PlaybackMixin.next_track(player)

        self.assertTrue(player._autodj_waiting_for_queue)
        self.assertEqual(player.stop_calls, 0)

    def test_end_transition_does_not_promote_mix_from_a_stale_queue_row(self):
        player = _PromotionHarness()
        player.tracks[1] = "replacement-track.mp3"
        player._pending_media_end = (0, 0, "old-track.mp3")
        player.promote_calls = 0
        player.next_calls = 0
        player.promote_mix_player = lambda: setattr(
            player, "promote_calls", player.promote_calls + 1)
        player.next_track = lambda: setattr(player, "next_calls", player.next_calls + 1)

        PlaybackMixin.finish_media_end_transition(
            player, (0, 0, "old-track.mp3"))

        self.assertEqual(player.promote_calls, 0)
        self.assertEqual(player.next_calls, 1)

    def test_stale_end_callback_does_not_advance_a_track_that_is_still_playing(self):
        player = _PromotionHarness()
        player._pending_media_end = (0, 0, "old-track.mp3")
        player.mix_target_index = -1
        player.player = _DisplayPlayer("old-track.mp3", 180000, 5000,
                                       QMediaPlayer.PlayingState,
                                       QMediaPlayer.LoadedMedia)
        player.next_calls = 0
        player.next_track = lambda: setattr(player, "next_calls", player.next_calls + 1)

        PlaybackMixin.finish_media_end_transition(
            player, (0, 0, "old-track.mp3"))

        self.assertEqual(player.next_calls, 0)

    def test_loading_duplicate_track_does_not_inherit_previous_end_position(self):
        player = _PromotionHarness()
        player.tracks = ["old-track.mp3", "old-track.mp3", "third-track.mp3"]
        player.current_index = 1
        player._pending_media_end = (0, 1, "old-track.mp3")
        player.mix_target_index = -1
        player.player = _DisplayPlayer("old-track.mp3", 180000, 180000,
                                       QMediaPlayer.StoppedState,
                                       QMediaPlayer.LoadingMedia)
        player.next_calls = 0
        player.next_track = lambda: setattr(player, "next_calls", player.next_calls + 1)

        PlaybackMixin.finish_media_end_transition(
            player, (0, 1, "old-track.mp3"))

        self.assertEqual(player.next_calls, 0)

    def test_end_transition_promotes_mix_when_qt_normalizes_windows_path(self):
        player = _PromotionHarness()
        old_path = r"C:\Music\old-track.mp3"
        next_path = r"C:\Music\next-track.mp3"
        player.tracks = [old_path, next_path]
        player.player = _DisplayPlayer(old_path, 180000, 180000,
                                       QMediaPlayer.StoppedState,
                                       QMediaPlayer.EndOfMedia)
        player.mix_player = _DisplayPlayer(next_path, 180000, 125000)
        player._pending_media_end = (0, 0, old_path)
        metadata = SimpleNamespace(
            display_title="Faixa nova", path=next_path, duration=180.0)

        with (patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata),
              patch("radiostudio.ui.playback.format_broadcast_title", return_value="Faixa nova"),
              patch("radiostudio.ui.playback._logger.exception")):
            PlaybackMixin.finish_media_end_transition(
                player, (0, 0, old_path))

        self.assertEqual(player.current_index, 1)
        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.position_label.text, "02:05 / 03:00")

    def test_consecutive_mix_promotions_advance_queue_after_each_end(self):
        player = _PromotionHarness()
        first = r"C:\Music\first.mp3"
        second = r"C:\Music\second.mp3"
        third = r"C:\Music\third.mp3"
        player.tracks = [first, second, third]
        player.player = _DisplayPlayer(first, 180000, 180000,
                                       QMediaPlayer.StoppedState,
                                       QMediaPlayer.EndOfMedia)
        player.mix_player = _DisplayPlayer(second, 180000, 125000)
        player.mix_target_index = 1
        player._pending_media_end = (0, 0, first)
        player._suppress_end_recovery = False
        player.pending_resume_position = None
        player.stop_pending = False
        player._display_sync_path = ""
        player._display_sync_metadata = None

        def metadata_for(path):
            return SimpleNamespace(
                display_title=Path(path).stem.title(),
                path=path,
                duration=180.0,
            )

        callbacks = []
        with (patch("radiostudio.ui.playback.read_track_metadata",
                    side_effect=metadata_for) as read_metadata,
              patch("radiostudio.ui.playback.format_broadcast_title", side_effect=lambda _fmt, metadata, _station: metadata.display_title),
              patch("radiostudio.ui.playback.QTimer.singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback)),
              patch("radiostudio.ui.playback._logger.exception")):
            PlaybackMixin.finish_media_end_transition(player, (0, 0, first))
            self.assertEqual(player.current_index, 1)

            # The second deck is the old physical player after the first swap.
            # Prepare it for the third queue item, then deliver EndOfMedia again.
            player.mix_player._source = QUrl.fromLocalFile(third)
            player.mix_player._position = 125000
            player.mix_player._state = QMediaPlayer.PlayingState
            player.mix_player._status = QMediaPlayer.LoadedMedia
            player.mix_target_index = 2
            player.player._position = player.player.duration()
            player.player._state = QMediaPlayer.StoppedState
            player.player._status = QMediaPlayer.EndOfMedia

            PlaybackMixin.schedule_media_end_transition(player)
            self.assertEqual(len(callbacks), 1)
            callbacks.pop(0)()

        self.assertEqual(read_metadata.call_count, 2)
        self.assertEqual(player.current_index, 2)
        self.assertEqual(player.on_air.title.text(), "Third")
        self.assertEqual(player.position_label.text, "02:05 / 03:00")

    def test_end_without_prepared_mix_starts_the_third_queue_item(self):
        player = _PromotionHarness()
        with tempfile.TemporaryDirectory(prefix="radiostudio-playback-queue-") as folder:
            paths = [Path(folder) / f"track-{index}.mp3" for index in range(3)]
            for path in paths:
                path.write_bytes(b"test")
            player.tracks = [str(path) for path in paths]
            player.track_sources = ["manual"] * 3
            player.player = _DisplayPlayer(
                str(paths[1]), 180000, 180000,
                QMediaPlayer.StoppedState, QMediaPlayer.EndOfMedia)
            player.mix_player = _DisplayPlayer(
                "", 0, 0, QMediaPlayer.StoppedState, QMediaPlayer.NoMedia)
            player.current_index = 1
            player.mix_target_index = -1
            player._pending_media_end = (0, 1, str(paths[1]))
            player._media_end_generation = 0
            player.stop_pending = False
            player.is_quitting = False
            player.pending_transition_kind = None
            player.pending_transition_action = None
            player.transition_timer = _FakeTimer()
            player.gain_ramps = {}
            player.gain_timer = _FakeTimer()
            player.audio_decode_fallback = _FakeDecodeFallback()
            player.audio_fallback_attempts = {}
            player.autodj_config = {"enabled": False}
            player.auto_transitions_enabled = False
            player.fade_out_ms = 0
            player.main_gain = 1.0
            player.gain_paused_at = None
            player.played_indexes = []
            player.play_index = lambda index, *args, **kwargs: player.played_indexes.append(index)

            PlaybackMixin.finish_media_end_transition(
                player, (0, 1, str(paths[1])))

        self.assertEqual(player.played_indexes, [2])

    def test_changing_main_media_flushes_queued_audio_from_previous_track(self):
        player = _PlaybackHarness()

        PlaybackMixin.set_media_source(player, player.player, "next-track.mp3")

        self.assertEqual(player.broadcast.cleared_sources, [("main", True)])
        self.assertEqual(player.broadcast.removed_sources, [])
        self.assertEqual(player.player.source().toLocalFile(), "next-track.mp3")

    def test_active_deck_resynchronizes_on_air_title_and_remaining_time(self):
        player = _DisplayHarness()
        player.on_air._track_path = "old-track.mp3"
        metadata = SimpleNamespace(display_title="Faixa nova", duration=180.0)

        with patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata):
            PlaybackMixin.refresh_playback_display(player)

        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.on_air._track_path, "next-track.mp3")
        self.assertEqual(player.on_air.progress, (125000, 180000))
        self.assertEqual(player.position_label.text, "02:05 / 03:00")
        self.assertEqual(player.position.value, 125000)

    def test_windows_path_normalization_does_not_restart_waveform_analysis(self):
        player = _DisplayHarness()
        path = r"C:\Music\next-track.mp3"
        player.player = _DisplayPlayer(path, 180000, 125000)
        player.tracks = [path]
        player.on_air._track_path = path
        player.on_air.title.setText("Faixa nova")
        player._display_sync_path = path
        player._display_sync_metadata = SimpleNamespace(
            display_title="Faixa nova", duration=180.0)

        with patch("radiostudio.ui.playback.read_track_metadata") as read_metadata:
            PlaybackMixin.refresh_playback_display(player)

        read_metadata.assert_not_called()
        self.assertEqual(player.on_air._track_path, path)
        self.assertEqual(player.position_label.text, "02:05 / 03:00")

    def test_refresh_recovers_missed_end_signal_when_mix_is_already_audible(self):
        player = _PromotionHarness()
        metadata = SimpleNamespace(
            display_title="Faixa nova", path="next-track.mp3", duration=180.0)
        callbacks = []

        with (patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata),
              patch("radiostudio.ui.playback.format_broadcast_title", return_value="Faixa nova"),
              patch("radiostudio.ui.playback.QTimer.singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback)),
              patch("radiostudio.ui.playback._logger.exception")):
            PlaybackMixin.refresh_playback_display(player)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        self.assertEqual(player.current_index, 1)
        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.position_label.text, "02:05 / 03:00")

    def test_stale_visualizer_does_not_block_missed_end_recovery(self):
        player = _PromotionHarness()
        player.on_air._track_path = "another-stale-track.mp3"
        callbacks = []

        with (patch("radiostudio.ui.playback.read_track_metadata",
                    return_value=SimpleNamespace(
                        display_title="Faixa nova", path="next-track.mp3", duration=180.0)),
              patch("radiostudio.ui.playback.format_broadcast_title", return_value="Faixa nova"),
              patch("radiostudio.ui.playback.QTimer.singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback)),
              patch("radiostudio.ui.playback._logger.exception")):
            PlaybackMixin.refresh_playback_display(player)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        self.assertEqual(player.current_index, 1)
        self.assertEqual(player.on_air.title.text(), "Faixa nova")

    def test_refresh_advances_when_backend_stops_at_end_without_a_mix(self):
        player = _PromotionHarness()
        player.mix_target_index = -1
        player.next_calls = 0
        player.next_track = lambda: setattr(player, "next_calls", player.next_calls + 1)
        callbacks = []

        with patch("radiostudio.ui.playback.QTimer.singleShot",
                   side_effect=lambda _delay, callback: callbacks.append(callback)):
            PlaybackMixin.refresh_playback_display(player)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        self.assertEqual(player.next_calls, 1)

    def test_short_track_at_position_zero_is_not_mistaken_for_end(self):
        player = _PromotionHarness()
        player.player = _DisplayPlayer(
            "old-track.mp3", 100, 0, QMediaPlayer.StoppedState,
            QMediaPlayer.LoadedMedia)

        self.assertFalse(PlaybackMixin._main_track_reached_end(player))

    def test_stopped_player_after_manual_stop_is_not_restarted_by_end_recovery(self):
        player = _PromotionHarness()
        player._suppress_end_recovery = True
        player.player._status = QMediaPlayer.EndOfMedia

        self.assertFalse(PlaybackMixin._main_track_reached_end(player))

    def test_late_end_signal_after_manual_stop_is_ignored(self):
        player = _PlaybackHarness()
        player.current_index = 0
        player.tracks = ["bad-track.mp3", "next-track.mp3"]
        player.mix_target_index = -1
        player.stop_pending = False
        player.is_quitting = False
        player._suppress_end_recovery = True
        player._media_end_generation = 0
        player._pending_media_end = None

        with patch("radiostudio.ui.playback.QTimer.singleShot") as single_shot:
            PlaybackMixin.media_status_changed(player, QMediaPlayer.EndOfMedia)

        single_shot.assert_not_called()
        self.assertIsNone(player._pending_media_end)

    def test_stale_visualizer_path_does_not_block_end_recovery(self):
        player = _PromotionHarness()
        player.on_air._track_path = "stale-track.mp3"

        self.assertTrue(PlaybackMixin._main_track_reached_end(player))

    def test_title_recovers_when_visualizer_path_marker_is_already_current(self):
        player = _DisplayHarness()
        player.on_air._track_path = "next-track.mp3"
        player.on_air.title.setText("Faixa antiga")
        player.on_air.title.setToolTip("Faixa antiga")
        metadata = SimpleNamespace(display_title="Faixa nova", duration=180.0)

        with patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata):
            PlaybackMixin.refresh_playback_display(player)

        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.on_air.title.tooltip, "Faixa nova")

    def test_playing_signal_immediately_resynchronizes_title_and_time(self):
        player = _DisplayHarness()
        player.on_air._track_path = "next-track.mp3"
        player.on_air.title.setText("Faixa antiga")
        metadata = SimpleNamespace(display_title="Faixa nova", duration=180.0)

        with patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata):
            PlaybackMixin.playback_state_changed(player, QMediaPlayer.PlayingState)

        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.position_label.text, "02:05 / 03:00")
        self.assertEqual(player.position.value, 125000)

    def test_mix_scheduler_does_not_read_next_track_tags_outside_transition_window(self):
        starts = []
        player = SimpleNamespace(
            mix_target_index=-1, current_index=0,
            tracks=["current.mp3", "next.mp3"], stop_pending=False,
            auto_transitions_enabled=False, mix_ms=8000,
            player=SimpleNamespace(
                playbackState=lambda: QMediaPlayer.PlayingState,
                duration=lambda: 180000, position=lambda: 60000),
            start_mix=lambda *args, **kwargs: starts.append((args, kwargs)))

        with patch("radiostudio.ui.playback.read_track_metadata") as read_metadata:
            PlaybackMixin.maybe_start_mix(player)

        read_metadata.assert_not_called()
        self.assertEqual(starts, [])

    def test_mix_scheduler_still_reads_and_starts_inside_transition_window(self):
        starts = []
        player = SimpleNamespace(
            mix_target_index=-1, current_index=0,
            tracks=["current.mp3", "next.mp3"], stop_pending=False,
            auto_transitions_enabled=False, mix_ms=8000,
            player=SimpleNamespace(
                playbackState=lambda: QMediaPlayer.PlayingState,
                duration=lambda: 180000, position=lambda: 175000),
            start_mix=lambda *args, **kwargs: starts.append((args, kwargs)))

        with patch("radiostudio.ui.playback.read_track_metadata",
                   return_value=SimpleNamespace(duration=30.0)) as read_metadata:
            PlaybackMixin.maybe_start_mix(player)

        read_metadata.assert_called_once_with("next.mp3")
        self.assertEqual(starts, [((5000,), {"cue_ms": 0})])

    def test_mix_promotion_updates_display_before_broadcast_cleanup(self):
        player = _PromotionHarness()
        metadata = SimpleNamespace(
            display_title="Faixa nova", path="next-track.mp3", duration=180.0)

        with (patch("radiostudio.ui.playback.read_track_metadata", return_value=metadata),
              patch("radiostudio.ui.playback.format_broadcast_title", return_value="Faixa nova"),
              patch("radiostudio.ui.playback._logger.exception")):
            PlaybackMixin.promote_mix_player(player)

        self.assertEqual(player.current_index, 1)
        self.assertEqual(player.on_air.title.text(), "Faixa nova")
        self.assertEqual(player.on_air.progress, (125000, 180000))
        self.assertEqual(player.position_label.text, "02:05 / 03:00")
        self.assertTrue(any(kind == "error" for kind, _message in player.events))

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

    def test_main_audio_is_queued_before_optional_waveform_work(self):
        player = _AudioPipelineHarness()
        player.player.state = QMediaPlayer.PlayingState

        player.handle_player_audio_buffer(player.player, _ValidBuffer())

        self.assertEqual(player.events, ["broadcast", "visualizer"])

    def test_hidden_window_skips_visualizer_but_keeps_broadcast_audio(self):
        player = _AudioPipelineHarness()
        player.player.state = QMediaPlayer.PlayingState
        player.on_air.isVisible = lambda: False

        player.handle_player_audio_buffer(player.player, _ValidBuffer())

        self.assertEqual(player.events, ["broadcast"])

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

    def test_silence_detection_does_not_read_next_track_metadata_before_tail(self):
        player = SimpleNamespace(
            trim_silence=True, mix_target_index=-1, current_index=0,
            tracks=["current.mp3", "next.mp3"], mix_ms=0, fade_out_ms=0,
            player=SimpleNamespace(duration=lambda: 240000, position=lambda: 1000),
            silence_ms=0)
        with patch("radiostudio.ui.audio_pipeline.read_track_metadata") as read_metadata:
            AudioPipelineMixin.detect_trailing_silence(player, None)

        read_metadata.assert_not_called()

    def test_silence_detection_reads_next_duration_once_per_queue_path(self):
        from array import array

        pcm = array("h", [20000] * 1024).tobytes()
        audio_format = SimpleNamespace(sampleRate=lambda: 44100, channelCount=lambda: 2)
        buffer = SimpleNamespace(data=lambda: pcm, format=lambda: audio_format)
        player = SimpleNamespace(
            trim_silence=True, mix_target_index=-1, current_index=0,
            tracks=["current.mp3", "next.mp3"], mix_ms=0, fade_out_ms=0,
            player=SimpleNamespace(duration=lambda: 240000, position=lambda: 239000),
            silence_ms=0, silence_threshold_db=-42, silence_hold_ms=1200,
            start_mix=lambda *_args, **_kwargs: self.fail("não deve iniciar mix em áudio alto"))
        with patch("radiostudio.ui.audio_pipeline.read_track_metadata",
                   return_value=SimpleNamespace(duration=180)) as read_metadata:
            for _ in range(3):
                AudioPipelineMixin.detect_trailing_silence(player, buffer)
            read_metadata.assert_called_once_with("next.mp3")

            player.tracks[1] = "other.mp3"
            AudioPipelineMixin.detect_trailing_silence(player, buffer)
            self.assertEqual(read_metadata.call_count, 2)

    def test_transition_analysis_cache_is_bounded_during_long_sessions(self):
        from radiostudio.ui.playback import MAX_TRANSITION_ANALYSES

        analyses = {f"track-{index}.mp3": None
                    for index in range(MAX_TRANSITION_ANALYSES)}
        player = SimpleNamespace(
            transition_analyses=analyses, auto_transitions_enabled=False,
            is_quitting=True,
        )

        PlaybackMixin.transition_analysis_ready(player, "new-track.mp3", None)

        self.assertEqual(len(analyses), MAX_TRANSITION_ANALYSES)
        self.assertNotIn("track-0.mp3", analyses)
        self.assertIn("new-track.mp3", analyses)

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
