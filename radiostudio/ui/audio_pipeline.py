"""Audio Pipeline operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


def trailing_silence_scan_window(duration_ms, mix_ms, fade_out_ms):
    """Limit live silence detection to the final part of a track."""
    try:
        duration = max(0, int(duration_ms))
        configured = max(0, int(mix_ms)) + max(0, int(fade_out_ms)) + 2000
    except (TypeError, ValueError, OverflowError):
        return 0
    if duration <= 0:
        return 0
    requested = max(6000, configured)
    return max(1, min(requested, 15000, duration // 4))


class AudioPipelineMixin:
    @guarded
    def forward_buffer(self, source_id, buffer, gain):
        if not buffer.isValid() or not self.broadcast.running:
            return
        fmt = buffer.format()
        if (fmt.sampleRate() != SAMPLE_RATE or fmt.channelCount() != CHANNELS
                or fmt.sampleFormat() != QAudioFormat.Int16):
            self.statusBar().showMessage("Formato de áudio incompatível com a transmissão")
            return
        if source_id in ("main", "mix") or source_id.startswith("cart-"):
            gain *= getattr(self, "music_duck_gain", 1.0)
        self.broadcast.push_audio(source_id, bytes(buffer.data()), gain)

    @Slot(QAudioBuffer)
    @guarded
    def handle_main_audio_buffer(self, buffer):
        self.handle_player_audio_buffer(self.player, buffer)

    @Slot(QAudioBuffer)
    @guarded
    def handle_slot_a_audio_buffer(self, buffer):
        self.handle_player_audio_buffer(self.player_slot_a, buffer)

    @Slot(QAudioBuffer)
    @guarded
    def handle_slot_b_audio_buffer(self, buffer):
        self.handle_player_audio_buffer(self.player_slot_b, buffer)

    @guarded
    def handle_mix_audio_buffer(self, buffer):
        self.handle_player_audio_buffer(self.mix_player, buffer)

    @guarded
    def handle_player_audio_buffer(self, player, buffer):
        if not buffer.isValid():
            return
        active = player is self.player
        if active and player.playbackState() == QMediaPlayer.PlayingState:
            self.on_air.feed_audio_buffer(buffer)
            self.detect_trailing_silence(buffer)
            # master_volume belongs to local monitoring. Keep the program feed at
            # unity and apply only transition/ducking gains before transmission.
            self.forward_buffer("main", buffer, self.main_gain)
        elif (player is self.mix_player
              and player.playbackState() == QMediaPlayer.PlayingState
              and self.player.playbackState() != QMediaPlayer.PausedState):
            self.forward_buffer("mix", buffer, self.mix_gain)

    @guarded
    def detect_trailing_silence(self, buffer):
        if not self.trim_silence or self.mix_target_index >= 0:
            return
        if self.current_index + 1 >= len(self.tracks):
            self.silence_ms = 0
            return
        duration = self.player.duration()
        remaining = duration - self.player.position()
        scan_window = trailing_silence_scan_window(
            duration, self.mix_ms, self.fade_out_ms)
        next_duration = read_track_metadata(self.tracks[self.current_index + 1]).duration
        if next_duration > 0:
            scan_window = min(scan_window, max(1, round(next_duration * 500)))
        if scan_window <= 0 or remaining <= 0 or remaining > scan_window:
            self.silence_ms = 0
            return
        try:
            import array
            samples = array.array("h")
            samples.frombytes(bytes(buffer.data()))
            peak = max((abs(sample) for sample in samples), default=0)
        except (ValueError, TypeError):
            return
        threshold = int(32767 * (10 ** (self.silence_threshold_db / 20)))
        if peak <= threshold:
            sample_rate = buffer.format().sampleRate()
            channels = max(1, buffer.format().channelCount())
            frames = len(samples) // channels
            self.silence_ms += round(frames * 1000 / sample_rate) if sample_rate else 0
            if self.silence_ms >= self.silence_hold_ms:
                # Use the remaining silent tail as overlap, but do not fade the
                # incoming song over that entire tail. A long silence should be
                # trimmed quickly instead of making the next track crawl in.
                fade_ms = min(1200, max(250, remaining))
                self.start_mix(remaining, fade_in_ms=fade_ms, fade_out_ms=fade_ms)
        else:
            self.silence_ms = 0
