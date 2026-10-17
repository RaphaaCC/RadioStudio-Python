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
        buffer_output.audioBufferReceived.connect(
            lambda buffer, key=source_id: self.forward_buffer(key, buffer, 0.8))
        player.setAudioBufferOutput(buffer_output)
        self.cart_players.append((player, output, buffer_output, source_id))
        player.mediaStatusChanged.connect(
            lambda status, p=player, original=path:
            self.cart_media_status_changed(p, original, status))
        player.setSource(QUrl.fromLocalFile(path))
        player.play()

    @guarded
    def cart_media_status_changed(self, player, source, status):
        if status == QMediaPlayer.InvalidMedia:
            if self.try_decode_fallback(
                    player, source,
                    lambda original, decoded, error: self.cart_decode_fallback_ready(
                        player, original, decoded, error)):
                self.log_event("info", f"Preparando cart para compatibilidade: {Path(source).name}")
                return
            self.log_event("error", f"Não foi possível reproduzir o cart: {source}")
            self.release_cart(player)
        elif status == QMediaPlayer.EndOfMedia:
            self.release_cart(player)

    @guarded
    def cart_decode_fallback_ready(self, player, source, decoded_path, error):
        if (not any(entry[0] is player for entry in self.cart_players)
                or player.source().toLocalFile() != source):
            if decoded_path:
                self.audio_decode_fallback.release(decoded_path, delay_ms=0)
            return
        if not decoded_path:
            self.log_event("error", f"Não foi possível decodificar o cart: {error}")
            self.release_cart(player)
            return
        self.decoded_audio_sources[player] = (source, decoded_path)
        player.setSource(QUrl.fromLocalFile(decoded_path))
        player.play()

    @guarded
    def release_cart(self, player):
        for p, output, buffer_output, source_id in self.cart_players:
            if p == player:
                self.broadcast.remove_source(source_id)
                p.stop()
                p.setSource(QUrl())
                buffer_output.deleteLater()
                output.deleteLater()
        self.audio_decode_fallback.cancel(player)
        self.release_decoded_media(player)
        self.audio_fallback_attempts.pop(player, None)
        self.cart_players = [entry for entry in self.cart_players if entry[0] != player]
        player.deleteLater()

    @guarded
    def stop_all_carts(self):
        for player, _output, _buffer_output, _source_id in tuple(self.cart_players):
            self.release_cart(player)
