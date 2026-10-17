import struct
import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QAudioFormat

from radiostudio.services.audio_inputs import (convert_input_to_master,
                                               convert_master_to_device,
                                               mix_pcm_chunks, pcm_rms_db,
                                               AudioInputController)
from radiostudio.services.broadcast import SAMPLE_RATE


class AudioInputConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_mono_int16_input_becomes_stereo_master_pcm(self):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.Int16)

        result = convert_input_to_master(struct.pack("<hh", 12000, -12000), audio_format)

        self.assertEqual(struct.unpack("<hhhh", result), (12000, 12000, -12000, -12000))

    def test_float_48khz_input_is_resampled_to_44_1khz(self):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48000)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.Float)
        source = struct.pack("<480f", *([0.5] * 480))

        result = convert_input_to_master(source, audio_format)

        self.assertEqual(len(result), 441 * 2 * 2)
        self.assertTrue(all(sample in (16383, 16384)
                            for sample in struct.unpack("<" + "h" * (len(result) // 2), result)))

    def test_master_is_converted_to_a_float_device_format(self):
        output_format = QAudioFormat()
        output_format.setSampleRate(48000)
        output_format.setChannelCount(2)
        output_format.setSampleFormat(QAudioFormat.Float)
        source = struct.pack("<" + "h" * (441 * 2), *([8192] * (441 * 2)))

        result = convert_master_to_device(source, output_format)

        self.assertEqual(len(result), 480 * 2 * 4)
        self.assertAlmostEqual(struct.unpack_from("<f", result)[0], 0.25, places=3)

    def test_non_finite_float_samples_are_silenced(self):
        audio_format = QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.Float)

        result = convert_input_to_master(struct.pack("<ff", float("nan"), float("inf")),
                                         audio_format)

        self.assertEqual(result, b"\0" * 8)

    def test_mixer_clips_and_rms_is_measured(self):
        loud = struct.pack("<" + "h" * 1764, *([22000] * 1764))

        result = mix_pcm_chunks([loud, loud])

        self.assertEqual(struct.unpack_from("<h", result)[0], 32767)
        self.assertGreater(pcm_rms_db(loud), -5)

    def test_missing_monitor_device_is_reported_once_per_error_state(self):
        from PySide6.QtMultimedia import QAudioDevice

        controller = AudioInputController(broadcast=None)
        controller.active["microphone"] = {}
        resolved = []
        controller._resolve_output = lambda: (resolved.append(True), QAudioDevice())[1]
        statuses = []
        controller.status_changed.connect(lambda *status: statuses.append(status))

        controller._ensure_monitor()
        controller._ensure_monitor()

        self.assertEqual(statuses, [("monitor", "Sem dispositivo para monitorar as entradas")])
        self.assertEqual(len(resolved), 1)
        controller.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
