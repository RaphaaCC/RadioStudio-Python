import array
import math
import tempfile
import unittest
from pathlib import Path

from radiostudio.services.transition_analysis import (
    ANALYSIS_SAMPLE_RATE,
    ANALYSIS_WINDOW_MS,
    TrackTransitionAnalysis,
    analyze_pcm_segments,
    analyze_track_transition,
    bounded_transition_duration_ms,
    equal_power_gains,
    plan_automatic_transition,
)


def pcm_from_levels(levels_db):
    samples_per_window = round(ANALYSIS_SAMPLE_RATE * ANALYSIS_WINDOW_MS / 1000)
    samples = array.array("h")
    for level in levels_db:
        amplitude = round(32767 * 10 ** (level / 20))
        samples.extend([amplitude] * samples_per_window)
    return samples.tobytes()


class TransitionAnalysisTests(unittest.TestCase):
    def test_pcm_analysis_finds_leading_and_trailing_silence(self):
        intro = pcm_from_levels([-60] * 5 + [-20] * 20)
        tail = pcm_from_levels([-20] * 20 + [-60] * 10)

        result = analyze_pcm_segments(intro, tail, 12000)

        self.assertEqual(result.intro_silence_ms, 1000)
        self.assertEqual(result.trailing_silence_ms, 2000)
        self.assertEqual(result.duration_ms, 12000)

    def test_smart_mix_adapts_to_incoming_energy_and_user_ceiling(self):
        outgoing = TrackTransitionAnalysis(180000, tail_loudness_db=-18)
        loud_incoming = TrackTransitionAnalysis(180000, intro_loudness_db=-5)
        quiet_incoming = TrackTransitionAnalysis(180000, intro_loudness_db=-30)

        loud = plan_automatic_transition(outgoing, loud_incoming)
        quiet = plan_automatic_transition(outgoing, quiet_incoming)
        limited = plan_automatic_transition(outgoing, quiet_incoming, 4000)

        self.assertLess(loud.duration_ms, quiet.duration_ms)
        self.assertEqual(limited.duration_ms, 4000)
        self.assertEqual(loud.fade_in_ms, 700)
        self.assertEqual(quiet.fade_in_ms, 1300)

    def test_silence_and_outro_fade_guide_mix_duration_and_intro_cue(self):
        outgoing = TrackTransitionAnalysis(
            200000, trailing_silence_ms=5000, tail_fade_ms=6000)
        incoming = TrackTransitionAnalysis(
            180000, intro_silence_ms=3000, intro_loudness_db=-18)

        plan = plan_automatic_transition(outgoing, incoming)

        self.assertEqual(plan.duration_ms, 6000)
        self.assertEqual(plan.cue_ms, 1200)

    def test_equal_power_curve_keeps_constant_combined_power(self):
        outgoing, incoming = equal_power_gains(0.5)

        self.assertAlmostEqual(outgoing, math.sqrt(0.5))
        self.assertAlmostEqual(incoming, math.sqrt(0.5))
        self.assertAlmostEqual(outgoing ** 2 + incoming ** 2, 1.0)
        self.assertEqual(equal_power_gains(0), (1.0, 0.0))
        end = equal_power_gains(1)
        self.assertAlmostEqual(end[0], 0.0)
        self.assertAlmostEqual(end[1], 1.0)

    def test_mix_trigger_duration_respects_shorter_incoming_track(self):
        self.assertEqual(
            bounded_transition_duration_ms(15000, 240000, 100000, 10000), 5000)
        self.assertEqual(
            bounded_transition_duration_ms(15000, 240000, 3000, 180000), 3000)

    def test_missing_or_unsupported_file_falls_back_without_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.mp3"
            self.assertIsNone(analyze_track_transition(str(missing)))


if __name__ == "__main__":
    unittest.main()
