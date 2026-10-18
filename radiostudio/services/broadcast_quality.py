"""Audio-quality safeguards for the live broadcast mix."""

from __future__ import annotations

import time
from array import array

from .broadcast import (
    ENCODED_CHUNK_BYTES,
    ENCODED_QUEUE_MAX_MS,
    FRAME_MS,
    BroadcastManager as _BaseBroadcastManager,
    SILENCE_PCM_FRAME,
)

# Leave a little headroom before int16 full scale. Crossfades, carts and live
# inputs can otherwise sum above 0 dBFS and the old hard clip flattens peaks.
MIX_PEAK = round(32767 * 0.98)
LIMITER_RELEASE_MS = 300


def limit_mixed_samples(mixed, current_gain: float = 1.0) -> tuple[bytes, float]:
    """Apply a transparent frame limiter instead of clipping individual samples."""
    peak = max((abs(value) for value in mixed), default=0)
    target_gain = 1.0 if peak <= MIX_PEAK or peak == 0 else MIX_PEAK / peak
    current_gain = min(1.0, max(0.0, float(current_gain)))
    if target_gain < current_gain:
        gain = target_gain  # Fast attack prevents the next frame from clipping.
    else:
        release = min(1.0, FRAME_MS / LIMITER_RELEASE_MS)
        gain = min(1.0, current_gain + (1.0 - current_gain) * release)
    pcm = array("h", (
        max(-32768, min(32767, round(value * gain))) for value in mixed
    )).tobytes()
    return pcm, gain


class BroadcastManager(_BaseBroadcastManager):
    """Broadcast manager with headroom-preserving program-bus limiting."""

    def __init__(self):
        super().__init__()
        self.limiter_gain = 1.0
        self.mixer_overruns = 0
        self.mixer_max_lateness_ms = 0.0

    def start(self, profiles: list[dict], local: dict):
        self.limiter_gain = 1.0
        self.mixer_overruns = 0
        self.mixer_max_lateness_ms = 0.0
        return super().start(profiles, local)

    def _mix_loop(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            lateness_ms = max(0.0, (time.monotonic() - deadline) * 1000)
            self.mixer_max_lateness_ms = max(self.mixer_max_lateness_ms, lateness_ms)
            if lateness_ms >= FRAME_MS:
                self.mixer_overruns += 1
            mixed = self._mix_source_samples()
            if mixed is None:
                # A stopped or paused playlist still needs continuous PCM for
                # active streams, but silence can bypass the Python limiter.
                pcm = SILENCE_PCM_FRAME
                self.limiter_gain = 1.0
            else:
                pcm, self.limiter_gain = limit_mixed_samples(mixed, self.limiter_gain)
            self._feed_outputs(pcm)
            deadline += FRAME_MS / 1000
            self.stop_event.wait(max(0, deadline - time.monotonic()))
            if time.monotonic() - deadline > 0.5:
                deadline = time.monotonic()

    def diagnostics_snapshot(self):
        with self.sources_lock:
            sources = list(self.sources.items())
        outputs = tuple(self.outputs)
        return {
            "mixer": {"frame_ms": FRAME_MS, "overruns": self.mixer_overruns,
                      "max_lateness_ms": round(self.mixer_max_lateness_ms, 2),
                      "limiter_gain": round(self.limiter_gain, 4)},
            "sources": {name: buffer.diagnostics_snapshot()
                        for name, (buffer, _gain) in sources},
            "outputs": [{"name": output.name, "status": output.status,
                         "pcm_queue_ms": output.queue.qsize() * FRAME_MS,
                         "pcm_queue_high_watermark_ms": output.max_pcm_queue_depth * FRAME_MS,
                         "dropped_pcm_frames": output.dropped_pcm_frames,
                         "encoded_queue_ms": round(
                             output.network_queue.qsize() * ENCODED_CHUNK_BYTES * 8 /
                             max(1, output.bitrate), 1),
                         "encoded_queue_limit_ms": ENCODED_QUEUE_MAX_MS,
                         "encoded_queue_high_watermark_ms": round(
                             output.max_network_queue_depth * ENCODED_CHUNK_BYTES * 8 /
                             max(1, output.bitrate), 1),
                         "dropped_encoded_chunks": output.dropped_encoded_chunks,
                         "max_network_write_ms": round(output.max_publish_ms, 2),
                         "slow_network_writes": output.slow_publish_count}
                        for output in outputs],
        }

    def stop(self):
        try:
            return super().stop()
        finally:
            self.limiter_gain = 1.0
