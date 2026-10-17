"""Audio-quality safeguards for the live broadcast mix."""

from __future__ import annotations

import time
from array import array

from .broadcast import (
    FRAME_MS,
    PCM_BYTES_PER_FRAME,
    BroadcastManager as _BaseBroadcastManager,
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

    def start(self, profiles: list[dict], local: dict):
        self.limiter_gain = 1.0
        return super().start(profiles, local)

    def _mix_loop(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            with self.sources_lock:
                sources = list(self.sources.values())
            mixed = [0] * (PCM_BYTES_PER_FRAME // 2)
            for buffer, gain in sources:
                samples = array("h")
                samples.frombytes(buffer.take(PCM_BYTES_PER_FRAME))
                for index, sample in enumerate(samples):
                    mixed[index] += int(sample * gain)
            pcm, self.limiter_gain = limit_mixed_samples(mixed, self.limiter_gain)
            for output in tuple(self.outputs):
                if output.status != "Falha":
                    output.feed(pcm)
            deadline += FRAME_MS / 1000
            self.stop_event.wait(max(0, deadline - time.monotonic()))
            if time.monotonic() - deadline > 0.5:
                deadline = time.monotonic()

    def stop(self):
        try:
            return super().stop()
        finally:
            self.limiter_gain = 1.0
