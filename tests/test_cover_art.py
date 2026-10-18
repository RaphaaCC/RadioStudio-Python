import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from radiostudio.services.cover_art import MAX_COVER_BYTES, _read_cover


def _png_bytes(width=24, height=12):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#2478c8"))
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Could not create the PNG fixture")
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


class CoverArtTests(unittest.TestCase):
    def test_reads_embedded_front_cover_and_preserves_image_data(self):
        png = _png_bytes()
        cover = SimpleNamespace(data=png, mime_type="image/png")
        tag = SimpleNamespace(images=SimpleNamespace(front_cover=cover, any=None))
        with patch("radiostudio.services.cover_art.TinyTag.get", return_value=tag) as get:
            result = _read_cover("track.mp3")

        get.assert_called_once_with("track.mp3", tags=True, duration=False, image=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.data, png)
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual((result.image.width(), result.image.height()), (24, 12))

    def test_uses_any_cover_when_front_cover_is_missing(self):
        png = _png_bytes(8, 8)
        cover = SimpleNamespace(data=png, mime_type="image/png")
        tag = SimpleNamespace(images=SimpleNamespace(front_cover=None, any=cover))
        with patch("radiostudio.services.cover_art.TinyTag.get", return_value=tag):
            self.assertIsNotNone(_read_cover("track.flac"))

    def test_rejects_cover_payloads_over_the_size_limit(self):
        cover = SimpleNamespace(data=b"x" * (MAX_COVER_BYTES + 1), mime_type="image/png")
        tag = SimpleNamespace(images=SimpleNamespace(front_cover=cover, any=None))
        with patch("radiostudio.services.cover_art.TinyTag.get", return_value=tag):
            self.assertIsNone(_read_cover("large-cover.mp3"))

    def test_reads_a_real_id3_embedded_cover(self):
        png = _png_bytes(16, 16)
        apic_data = b"\x00image/png\x00\x03\x00" + png
        frame = b"APIC" + len(apic_data).to_bytes(4, "big") + b"\x00\x00" + apic_data
        tag_size = len(frame)
        synchsafe_size = bytes((
            (tag_size >> 21) & 0x7F,
            (tag_size >> 14) & 0x7F,
            (tag_size >> 7) & 0x7F,
            tag_size & 0x7F,
        ))
        mp3_bytes = b"ID3\x03\x00\x00" + synchsafe_size + frame

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "embedded-cover.mp3"
            path.write_bytes(mp3_bytes)
            result = _read_cover(str(path))

        self.assertIsNotNone(result)
        self.assertEqual(result.data, png)
        self.assertEqual(result.mime_type, "image/png")
        self.assertEqual((result.image.width(), result.image.height()), (16, 16))


if __name__ == "__main__":
    unittest.main()
