"""Cart Wall operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


class CartWallMixin:
    @guarded
    def cart_menu(self, index):
        menu = QMenu(self)
        assign = menu.addAction("Atribuir áudio...")
        clear = menu.addAction("Limpar")
        chosen = menu.exec(self.cart_buttons[index].mapToGlobal(self.cart_buttons[index].rect().bottomLeft()))
        if chosen == assign:
            path, _ = QFileDialog.getOpenFileName(self, "Áudio do cart", "", AUDIO_FILTER)
            if path:
                self.carts[index] = path
        elif chosen == clear:
            self.carts[index] = ""
        self.refresh_carts()
        self.save_state()

    @guarded
    def refresh_carts(self):
        for index, path in enumerate(self.carts):
            if path:
                metadata = read_track_metadata(path)
                self.cart_buttons[index].setText(metadata.title[:20])
                self.cart_buttons[index].setToolTip("\n".join(filter(None, (
                    path, metadata.artist, metadata.album))))
            else:
                self.cart_buttons[index].setText(f"Cart {index + 1}")
                self.cart_buttons[index].setToolTip("Clique com o botão direito para atribuir áudio")

    @guarded
    def play_cart(self, index):
        path = self.carts[index]
        if not path:
            self.cart_menu(index)
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "Arquivo ausente", path)
            return
        player = QMediaPlayer(self)
        output = QAudioOutput(self)
        output.setVolume(0.8 * self.music_duck_gain)
        output.setDevice(resolve_output_device(self.main_device_key))
        output.setMuted(not self.local_master_enabled)
        player.setAudioOutput(output)
        buffer_output = QAudioBufferOutput(self.broadcast_format, self)
        source_id = f"cart-{id(player)}"
        # Keep broadcast carts in the same music-duck envelope as their local
        # monitor. Previously only the local cart level fell under microphone
        # ducking, while listeners still heard the cart at full level.
        buffer_output.audioBufferReceived.connect(
            lambda buffer, key=source_id: self.forward_buffer(
                key, buffer, 0.8 * self.music_duck_gain))
        player.setAudioBufferOutput(buffer_output)
        player.setSource(QUrl.fromLocalFile(path))
        self.cart_players.append((player, output, buffer_output, source_id))
        player.mediaStatusChanged.connect(lambda status, p=player: self.release_cart(p) if status in (QMediaPlayer.EndOfMedia, QMediaPlayer.InvalidMedia) else None)
        player.play()

    @guarded
    def release_cart(self, player):
        for p, output, buffer_output, source_id in self.cart_players:
            if p == player:
                self.broadcast.remove_source(source_id)
                p.stop()
                buffer_output.deleteLater()
                output.deleteLater()
        self.cart_players = [entry for entry in self.cart_players if entry[0] != player]
        player.deleteLater()

    @guarded
    def stop_all_carts(self):
        for player, _output, _buffer_output, _source_id in tuple(self.cart_players):
            self.release_cart(player)
