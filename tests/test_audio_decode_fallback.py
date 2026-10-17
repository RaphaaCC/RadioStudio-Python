import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from PySide6.QtWidgets import QApplication

from radiostudio.services.audio_decode_fallback import AudioDecodeFallback


class AudioDecodeFallbackTests(unittest.TestCase):
    def test_ffmpeg_lookup_failure_clears_pending_job_and_reports_error(self):
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory(prefix="radiostudio-fallback-test-") as folder:
            source = Path(folder) / "sample.mp3"
            source.write_bytes(b"synthetic audio")
            player = object()
            fallback = AudioDecodeFallback()
            result = []

            with self.assertLogs("radiostudio", level="ERROR"):
                with patch("radiostudio.services.audio_decode_fallback.imageio_ffmpeg.get_ffmpeg_exe",
                           side_effect=RuntimeError("FFmpeg ausente")):
                    started = fallback.convert(
                        player, str(source),
                        lambda original, decoded, error: result.append(
                            (original, decoded, error)))

            self.assertFalse(started)
            self.assertFalse(fallback.is_pending(player))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0][0], str(source))
            self.assertEqual(result[0][1], "")
            self.assertIn("FFmpeg ausente", result[0][2])
            fallback.shutdown()

    def test_vorbis_source_is_converted_to_qt_friendly_wav(self):
        with tempfile.TemporaryDirectory(prefix="radiostudio-fallback-test-") as folder:
            source = Path(folder) / "sample.ogg"
            generated = subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "0.25",
                 "-c:a", "libvorbis", str(source)],
                capture_output=True, text=True, timeout=10)
            if generated.returncode:
                self.skipTest(f"FFmpeg não oferece o encoder Vorbis: {generated.stderr.strip()}")

            harness = r'''
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from radiostudio.services.audio_decode_fallback import AudioDecodeFallback

app = QCoreApplication([])
loop = QEventLoop()
timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(loop.quit)
fallback = AudioDecodeFallback()
result = []
fallback.convert(object(), sys.argv[1], lambda original, decoded, error:
                 (result.append((original, decoded, error)), loop.quit()))
timer.start(10000)
loop.exec()
if not result:
    print("FFmpeg não concluiu a conversão", file=sys.stderr)
    fallback.shutdown()
    raise SystemExit(1)
original, decoded, error = result[0]
if original != sys.argv[1] or error or not Path(decoded).is_file():
    print(error or "resultado WAV inválido", file=sys.stderr)
    fallback.shutdown()
    raise SystemExit(1)
with Path(decoded).open("rb") as stream:
    valid = stream.read(4) == b"RIFF"
fallback.shutdown()
raise SystemExit(0 if valid else 1)
'''
            result = subprocess.run(
                [sys.executable, "-c", harness, str(source)],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
