import math
import unittest

from radiostudio.ui.audio_inputs import AudioInputsControllerMixin
from radiostudio.ui.cart_wall import CART_GAIN, CartWallMixin
from radiostudio.ui.playback import PlaybackMixin


class _AudioOutput:
    def __init__(self):
        self.volume = None

    def setVolume(self, volume):
        self.volume = volume


class _InputController:
    def set_monitor_gain(self, _gain):
        pass


class _CartHost(CartWallMixin, PlaybackMixin, AudioInputsControllerMixin):
    def __init__(self):
        self.master_volume = 0.8
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.music_duck_gain = 0.5
        self.music_duck_target = 0.5
        self.audio = _AudioOutput()
        self.mix_audio = _AudioOutput()
        self.audio_input_controller = _InputController()
        self.cart_output = _AudioOutput()
        self.cart_players = [(None, self.cart_output, None, "cart-test")]


class CartWallVolumeRegressionTests(unittest.TestCase):
    def test_master_volume_slider_controls_local_cart_audio(self):
        host = _CartHost()

        host.set_master_volume(25)

        self.assertAlmostEqual(host.cart_output.volume, 0.25 * CART_GAIN * 0.5)

    def test_microphone_duck_updates_active_cart_volume(self):
        host = _CartHost()
        host.master_volume = 0.6
        host.music_duck_target = 0.2

        host.update_music_ducking()

        expected_duck = 0.5 + (0.2 - 0.5) * (1 - math.exp(-0.02 / 0.09))
        self.assertAlmostEqual(
            host.cart_output.volume, host.master_volume * CART_GAIN * expected_duck)


if __name__ == "__main__":
    unittest.main()
