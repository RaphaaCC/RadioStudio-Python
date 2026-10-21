"""Settings Controller operations for the main window."""

import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QProcess, QThreadPool, Signal

from ._shared import *
from ..runtime_safety import guarded
from ..config import APP_DIR, APP_VERSION, MUSIC_LIBRARY_FILE, STATE_FILE
from ..services.backups import (BackupError, create_backup, import_backup,
                                repair_database, restore_preserved_profile)
from ..services.process_priority import apply_process_priority


class _BackupSignals(QObject):
    finished = Signal(object, object, bool)


class _BackupWorker(QRunnable):
    def __init__(self, directory, retention, scheduled):
        super().__init__()
        self.directory = Path(directory)
        self.retention = retention
        self.scheduled = scheduled
        self.signals = _BackupSignals()

    def run(self):
        try:
            result = create_backup(self.directory, self.retention, STATE_FILE,
                                   MUSIC_LIBRARY_FILE, APP_VERSION)
        except Exception as exc:
            result = exc
        self.signals.finished.emit(self, result, self.scheduled)


class SettingsControllerMixin:
    @guarded
    def show_settings(self):
        current_track = (read_track_metadata(self.tracks[self.current_index])
                         if 0 <= self.current_index < len(self.tracks) else None)
        dialog = SettingsDialog(self.main_device_key, self.preview_device_key,
                                self.local_master_enabled, self.station_metadata,
                                current_track, self,
                                minimize_to_tray=self.minimize_to_tray,
                                resume_on_startup=self.resume_on_startup,
                                tray_available=self.tray_icon is not None,
                                player_controls_position=self.player_controls_position,
                                waveform_style=self.waveform_style,
                                waveform_visualizers_enabled=self.waveform_visualizers_enabled,
                                auto_start_broadcast=self.auto_start_broadcast,
                                auto_transitions_enabled=self.auto_transitions_enabled,
                                fade_in_ms=self.fade_in_ms, fade_out_ms=self.fade_out_ms,
                                mix_ms=self.mix_ms, trim_silence=self.trim_silence,
                                silence_threshold_db=self.silence_threshold_db,
                                silence_hold_ms=self.silence_hold_ms,
                                input_device_keys=self.input_device_keys,
                                mic_music_level_percent=self.mic_music_level_percent,
                                dynamic_mic_duck=self.dynamic_mic_duck,
                                speech_music_level_percent=self.speech_music_level_percent,
                                speech_threshold_db=self.speech_threshold_db,
                                discord_client_id=self.discord_client_id,
                                discord_enabled=self.discord_enabled,
                                discord_share_track=self.discord_share_track,
                                discord_use_track_cover=self.discord_use_track_cover,
                                discord_cover_url=self.discord_cover_url,
                                discord_public_url=self.discord_public_url,
                                discord_large_image=self.discord_large_image,
                                discord_activity_name=self.discord_activity_name,
                                discord_activity_type=self.discord_activity_type,
                                discord_status_display_type=self.discord_status_display_type,
                                discord_details_template=self.discord_details_template,
                                discord_state_template=self.discord_state_template,
                                discord_large_text=self.discord_large_text,
                                discord_button1_label=self.discord_button1_label,
                                discord_button2_label=self.discord_button2_label,
                                discord_button2_url=self.discord_button2_url,
                                process_priority=self.process_priority,
                                discord_service=self.discord_presence,
                                backup_directory=self.backup_directory,
                                backup_periodic_enabled=self.backup_periodic_enabled,
                                backup_interval_seconds=self.backup_interval_seconds,
                                backup_retention=self.backup_retention,
                                backup_create_callback=self.start_backup,
                                backup_import_callback=self.import_settings_backup,
                                database_repair_callback=self.repair_music_database)
        self._active_settings_dialog = dialog
        if dialog.exec() != QDialog.Accepted:
            self._active_settings_dialog = None
            self.discord_presence.configure(self.discord_client_id, self.discord_enabled)
            self.sync_discord_presence()
            return
        old_backup_settings = (self.backup_directory, self.backup_periodic_enabled,
                               self.backup_interval_seconds)
        (self.main_device_key, self.preview_device_key,
         self.local_master_enabled, self.station_metadata,
         self.minimize_to_tray, self.resume_on_startup,
         controls_position, waveform_style, self.waveform_visualizers_enabled,
         self.auto_start_broadcast,
         self.auto_transitions_enabled,
         self.fade_in_ms, self.fade_out_ms, self.mix_ms, self.trim_silence,
         self.silence_threshold_db, self.silence_hold_ms,
         self.input_device_keys, self.mic_music_level_percent, self.dynamic_mic_duck,
         self.speech_music_level_percent, self.speech_threshold_db,
         self.discord_client_id, self.discord_enabled, self.discord_share_track,
         self.discord_use_track_cover, self.discord_cover_url,
         self.discord_public_url, self.discord_large_image,
         self.discord_activity_name, self.discord_activity_type,
         self.discord_status_display_type, self.discord_details_template,
         self.discord_state_template, self.discord_large_text,
         self.discord_button1_label, self.discord_button2_label,
         self.discord_button2_url, self.process_priority,
         self.backup_directory, self.backup_periodic_enabled,
         self.backup_interval_seconds, self.backup_retention) = dialog.values()
        if (self.backup_periodic_enabled
                and old_backup_settings != (self.backup_directory,
                                            self.backup_periodic_enabled,
                                            self.backup_interval_seconds)):
            self.backup_last_run = time.time()
        self._active_settings_dialog = None
        priority_applied, priority_error = apply_process_priority(self.process_priority)
        if not priority_applied:
            logging.getLogger("radiostudio").warning(
                "Não foi possível aplicar a prioridade do processo: %s", priority_error)
        self.discord_presence.configure(self.discord_client_id, self.discord_enabled)
        self.sync_discord_presence()
        self.set_player_controls_position(controls_position)
        self.waveform_style = waveform_style
        if not self.waveform_visualizers_enabled:
            self.on_air.set_waveform_visualizers_enabled(False)
        self.on_air.set_waveform_style(waveform_style)
        if self.waveform_visualizers_enabled:
            self.on_air.set_waveform_visualizers_enabled(True)
        if self.auto_transitions_enabled and 0 <= self.current_index < len(self.tracks):
            self.request_transition_analysis(self.tracks[self.current_index])
            if self.current_index + 1 < len(self.tracks):
                self.request_transition_analysis(self.tracks[self.current_index + 1])
        self.apply_audio_devices()
        self.audio_input_controller.speech_threshold_db = self.speech_threshold_db
        self.update_music_duck_target()
        # The modal settings window can stay open long enough for playback to
        # advance. Re-read the current row here instead of publishing metadata
        # captured when the dialog was opened.
        current_on_air = (read_track_metadata(self.tracks[self.current_index])
                          if 0 <= self.current_index < len(self.tracks) else None)
        if current_on_air and self.player.playbackState() != QMediaPlayer.StoppedState:
            self.broadcast.set_title(format_broadcast_title(
                self.station_metadata["title_format"], current_on_air, self.station_metadata))
        if self.broadcast.local_server:
            server = self.broadcast.local_server
            server.station = self.broadcast_panel.local_config()["station"] or self.station_metadata["name"]
            server.description = self.station_metadata["description"]
            server.genre = self.station_metadata["genre"]
            server.url = self.station_metadata["url"]
        self.save_state()
        status_message = (
            "Saída local do master ativa" if self.local_master_enabled
            else "Master local silenciado; transmissão continua ativa")
        if not priority_applied:
            status_message += f" • Prioridade não alterada: {priority_error}"
        self.statusBar().showMessage(status_message)

    @guarded
    def start_backup(self, directory: str, retention: int, scheduled: bool = False) -> bool:
        if getattr(self, "_backup_worker", None) is not None:
            return False
        if not str(directory or "").strip():
            self._set_backup_status("Selecione uma pasta para salvar os backups.")
            return False
        self.save_state()
        worker = _BackupWorker(directory, retention, scheduled)
        self._backup_worker = worker
        worker.signals.finished.connect(self._backup_task_finished)
        QThreadPool.globalInstance().start(worker, priority=-1)
        return True

    @guarded
    def _backup_task_finished(self, worker, result, scheduled: bool) -> None:
        if getattr(self, "_backup_worker", None) is worker:
            self._backup_worker = None
        if isinstance(result, Exception):
            logging.getLogger("radiostudio").exception(
                "Falha ao criar backup", exc_info=(type(result), result, result.__traceback__))
            message = f"Falha ao criar backup: {result}"
            if scheduled:
                self._backup_retry_after = time.time() + 300
        else:
            self.backup_last_run = time.time()
            self.save_state()
            message = f"Backup criado: {result.path}"
            if result.retention_warning:
                message += f"\n{result.retention_warning}"
            elif result.removed:
                message += f"\n{result.removed} backup(s) antigo(s) removido(s)."
            self.log_event("info", f"Backup de configurações criado: {result.path}")
            self._backup_retry_after = 0.0
        self._set_backup_status(message)
        self.statusBar().showMessage(message.replace("\n", " "), 12000)

    @guarded
    def _set_backup_status(self, message: str) -> None:
        dialog = getattr(self, "_active_settings_dialog", None)
        if dialog is not None:
            try:
                if dialog.isVisible():
                    dialog.backup_status.setText(str(message))
            except RuntimeError:
                self._active_settings_dialog = None

    @guarded
    def check_scheduled_backup(self) -> None:
        if not self.backup_periodic_enabled or not self.backup_directory:
            return
        now = time.time()
        if now < getattr(self, "_backup_retry_after", 0):
            return
        try:
            interval = int(self.backup_interval_seconds)
            last_run = float(self.backup_last_run)
        except (TypeError, ValueError, OverflowError):
            interval, last_run = 86400, 0
        if interval <= 0 or now - last_run < interval:
            return
        self.start_backup(self.backup_directory, self.backup_retention, scheduled=True)

    @guarded
    def import_settings_backup(self, backup_file: str) -> None:
        answer = QMessageBox.question(
            self, "Importar configurações",
            "As configurações e o banco de bibliotecas deste backup substituirão os dados atuais. "
            f"O RadioStudio será reiniciado e uma cópia do perfil atual será preservada em "
            f"{APP_DIR / 'recovery'}. "
            "As senhas do Gerenciador de Credenciais permanecem neste computador.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        if getattr(self, "_backup_worker", None) is not None:
            QMessageBox.warning(
                self, "Backup em andamento",
                "Aguarde a criação do backup atual terminar antes de importar outro.")
            return
        if getattr(self.autodj_panel, "scanning_libraries", set()):
            QMessageBox.warning(
                self, "Biblioteca em uso",
                "Aguarde o exame das bibliotecas terminar antes de importar configurações.")
            return
        self.save_state()
        try:
            result = import_backup(backup_file, STATE_FILE, MUSIC_LIBRARY_FILE,
                                   APP_DIR / "recovery")
        except (BackupError, OSError) as exc:
            QMessageBox.warning(self, "Importar configurações", str(exc))
            return
        if not self._restart_application():
            rollback_succeeded = True
            try:
                restore_preserved_profile(result.recovery_directory, STATE_FILE,
                                          MUSIC_LIBRARY_FILE)
            except Exception:
                rollback_succeeded = False
                logging.getLogger("radiostudio").exception(
                    "Falha ao reverter a importação após erro ao reiniciar")
            if rollback_succeeded:
                restart_message = (
                    "O RadioStudio não conseguiu reiniciar. A importação foi desfeita e os "
                    "dados anteriores foram restaurados; tente importar novamente mais tarde.")
            else:
                restart_message = (
                    "O RadioStudio não conseguiu reiniciar nem restaurar automaticamente o perfil. "
                    f"A cópia de segurança está em {result.recovery_directory}.")
            QMessageBox.warning(
                self, "Reinicialização", restart_message)
            return
        self._skip_state_save = True
        self.is_quitting = True
        self.close()

    @guarded
    def repair_music_database(self) -> None:
        if getattr(self, "_backup_worker", None) is not None:
            QMessageBox.warning(
                self, "Backup em andamento",
                "Aguarde a criação do backup terminar antes de reparar o banco de dados.")
            return
        if getattr(self.autodj_panel, "scanning_libraries", set()):
            QMessageBox.warning(
                self, "Biblioteca em uso",
                "Aguarde o exame das bibliotecas terminar antes de verificar o banco de dados.")
            return
        answer = QMessageBox.question(
            self, "Verificar e reparar banco de dados",
            "O índice das bibliotecas e o histórico serão verificados. Se estiverem danificados, "
            "o RadioStudio tentará usar um backup ou recuperar os registros legíveis. "
            + ("Como esta sessão está usando um índice temporário, o RadioStudio será reiniciado "
               "ao fim da verificação para reconectar ao banco principal."
               if getattr(self, "music_library_fallback", False) else
               "O arquivo anterior será preservado e o programa será reiniciado se houver reparação."),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        backup_directory = Path(self.backup_directory) if self.backup_directory else APP_DIR / "backups"
        self.save_state()
        try:
            result = repair_database(MUSIC_LIBRARY_FILE, backup_directory,
                                     APP_DIR / "recovery", STATE_FILE)
        except (BackupError, OSError) as exc:
            QMessageBox.critical(self, "Banco de dados", str(exc))
            return
        reconnecting_from_fallback = bool(getattr(self, "music_library_fallback", False))
        if not result.repaired and not reconnecting_from_fallback:
            QMessageBox.information(self, "Banco de dados", result.message)
            return
        repair_summary = result.message
        if reconnecting_from_fallback and not result.repaired:
            repair_summary += (
                "\n\nO RadioStudio iniciou com um índice temporário. Ele será reiniciado "
                "para reconectar ao banco principal íntegro.")
        if result.recovery_directory is not None:
            repair_summary += f"\n\nCópia do perfil anterior: {result.recovery_directory}"
        dialog_title = "Reparo concluído" if result.repaired else "Reconexão com banco de dados"
        QMessageBox.information(self, dialog_title, repair_summary +
                                "\n\nO RadioStudio será reiniciado.")
        if not self._restart_application():
            restart_failure = (
                "O banco principal está íntegro, mas o reinício automático falhou. Feche e abra "
                "o RadioStudio para reconectar ao banco principal."
                if reconnecting_from_fallback else
                result.message + "\n\nO reinício automático falhou. Feche e abra "
                "o RadioStudio para usar o banco reparado.")
            QMessageBox.warning(self, "Banco de dados", restart_failure)
            self._skip_state_save = True
            self.is_quitting = True
            self.close()
            return
        self._skip_state_save = True
        self.is_quitting = True
        self.close()

    @staticmethod
    def _restart_application() -> bool:
        try:
            executable = Path(sys.executable).resolve()
            first_argument = Path(sys.argv[0]).resolve() if sys.argv else None
            compiled = (getattr(sys, "frozen", False)
                        or first_argument == executable)
            if compiled:
                arguments = list(sys.argv[1:])
            else:
                # orig_argv retains the actual launcher form (for example,
                # ``python -m radiostudio``) instead of a package __main__.py
                # path that cannot be executed directly.
                original_arguments = getattr(sys, "orig_argv", None)
                arguments = (list(original_arguments[1:]) if original_arguments
                             else list(sys.argv))
            arguments.append("--radiostudio-restart-wait")
            result = QProcess.startDetached(sys.executable, arguments, str(Path.cwd()))
            return bool(result[0] if isinstance(result, tuple) else result)
        except Exception:
            logging.getLogger("radiostudio").exception(
                "Não foi possível iniciar o RadioStudio novamente")
            return False

    @guarded
    def apply_audio_devices(self):
        self.audio.setDevice(resolve_output_device(self.main_device_key))
        self.audio.setMuted(not self.local_master_enabled)
        self.mix_audio.setDevice(resolve_output_device(self.main_device_key))
        self.mix_audio.setMuted(not self.local_master_enabled)
        self.preview_player.stop()
        self.preview_audio.setDevice(resolve_output_device(self.preview_device_key))
        for _player, output, _buffer, _source in self.cart_players:
            output.setDevice(resolve_output_device(self.main_device_key))
            output.setMuted(not self.local_master_enabled)
        self.audio_input_controller.configure(
            self.input_device_keys, self.main_device_key, self.local_master_enabled,
            self.master_volume, self.speech_threshold_db)

    @guarded
    def toggle_prelisten(self):
        row = self.playlist.currentRow()
        if row < 0:
            self.statusBar().showMessage("Selecione uma faixa para escutar")
            return
        path = self.tracks[row]
        if not Path(path).is_file():
            QMessageBox.warning(self, "Arquivo ausente", path)
            return
        self.preview_dialog.load_track(path)
        self.statusBar().showMessage(f"Pré-escuta: {Path(path).stem}")

    @guarded
    def stop_prelisten(self):
        self.preview_dialog.stop()
        self.statusBar().showMessage("Pré-escuta parada")
