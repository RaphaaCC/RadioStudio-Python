import unittest
from array import array

from radiostudio.services.broadcast_quality import MIX_PEAK, limit_mixed_samples


class BroadcastQualityTests(unittest.TestCase):
    def test_limiter_scales_the_whole_frame_instead_of_hard_clipping_peaks(self):
        pcm, gain = limit_mixed_samples([40000, -20000], 1.0)
        samples = array("h")
        samples.frombytes(pcm)

        self.assertLess(gain, 1.0)
        self.assertLessEqual(max(map(abs, samples)), MIX_PEAK)
        self.assertAlmostEqual(samples[1] / samples[0], -0.5, places=2)

    def test_limiter_releases_gradually_after_an_overload(self):
        _pcm, reduced = limit_mixed_samples([50000, -50000], 1.0)
        _pcm, recovered = limit_mixed_samples([1000, -1000], reduced)

        self.assertGreater(recovered, reduced)
        self.assertLess(recovered, 1.0)


if __name__ == "__main__":
    unittest.main()
