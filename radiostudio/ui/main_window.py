"""Main RadioStudio window shell and UI assembly."""

import hashlib
import logging
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QVBoxLayout)

from ._shared import *
from .event_dialog import EventDialog
from .playlist_table import PlaylistTable
from ..runtime_safety import guarded
from .event_log import EventLogMixin
from .explorer import ExplorerMixin
from .playlist import PlaylistMixin
from .settings_controller import SettingsControllerMixin
from .playback import PlaybackMixin
from .scheduler import SchedulerMixin
from .cart_wall import CartWallMixin
from .audio_pipeline import AudioPipelineMixin
from .audio_inputs import AudioInputsControllerMixin
from .persistence import PersistenceMixin
from .autodj_panel import AutoDJPanel
from .autodj_controller import AutoDJControllerMixin
from .preview_player_dialog import PreviewPlayerDialog
from ..services.music_library import MusicLibrary
from ..services.audio_inputs import AudioInputController, INPUT_CHANNELS
from ..services.transition_analysis import TransitionAnalyzer
from ..services.audio_decode_fallback import AudioDecodeFallback
from ..services.discord_presence import DiscordPresenceService, make_activity
from ..services.audio_paths import audio_path_key, same_audio_path
from ..services.cover_art import CoverArtLoader
from ..config import (ABOUT_LOGO_FILE, APP_DIR, APP_NAME, APP_VERSION,
                      DISCORD_APPLICATION_ID, MUSIC_LIBRARY_FILE, USER_MANUAL_FILE)
from .icons import app_icon, apply_app_icon
from .studio_widgets import CoverArtLabel


class RadioStudio(EventLogMixin, ExplorerMixin, PlaylistMixin, SettingsControllerMixin,
                  PlaybackMixin, SchedulerMixin, AutoDJControllerMixin, CartWallMixin, AudioPipelineMixin,
                  AudioInputsControllerMixin, PersistenceMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1250, 760)
        self.tracks = []
        self.track_sources = []
        self.events = []
        self.backup_directory = ""
        self.backup_periodic_enabled = False
        self.backup_interval_seconds = 86400
        self.backup_retention = 10
        self.backup_last_run = 0.0
        self._backup_retry_after = 0.0
        self._backup_worker = None
        self._active_settings_dialog = None
        self._skip_state_save = False
        self.music_library_fallback = False
        try:
            self.music_library = MusicLibrary()
        except Exception:
            # Keep playback and transmission available when the library database
            # is damaged, locked, or its profile folder is temporarily unavailable.
            logging.getLogger("radiostudio").exception(
                "Falha ao abrir a biblioteca principal; usando um banco temporário")
            fallback = Path(tempfile.gettempdir()) / f"RadioStudioPython-{uuid.uuid4().hex}.sqlite3"
            self.music_library = MusicLibrary(fallback)
            self.music_library_fallback = True
        self.autodj_config = {"enabled": False, "artist_cooldown_minutes": 60,
                              "track_cooldown_minutes": 360, "queue_ahead": 4}
        self.autodj_programs = []
        self.autodj_last_signature = None
        self.autodj_last_fill = 0.0
        # The one-second scheduler tick can fill AutoDJ before the delayed
        # startup hook. Keep the restored queue intact through that first fill.
        self.autodj_startup_pending = True
        self.carts = ["" for _ in range(25)]
        self.current_index = -1
        self.last_fired = set()
        self.main_device_key = ""
        self.preview_device_key = ""
        self.input_device_keys = {channel: "" for channel in INPUT_CHANNELS}
        self.mic_music_level_percent = 35
        self.dynamic_mic_duck = False
        self.speech_music_level_percent = 20
        self.speech_threshold_db = -38.0
        self.mic_active = False
        self.mic_speaking = False
        self.music_duck_gain = 1.0
        self.music_duck_target = 1.0
        self.local_master_enabled = True
        self.station_metadata = DEFAULT_STATION_METADATA.copy()
        self.explorer_folder = ""
        self.pending_track_log = None
        self.minimize_to_tray = True
        self.resume_on_startup = True
        self.auto_start_broadcast = False
        self.process_priority = "normal"
        self.broadcast_was_running = False
        self.discord_client_id = DISCORD_APPLICATION_ID
        self.discord_enabled = False
        self.discord_share_track = True
        self.discord_use_track_cover = False
        self.discord_cover_url = ""
        self._discord_cover_version = ""
        self.discord_public_url = ""
        self.discord_large_image = "radiostudio"
        self.discord_activity_name = "RadioStudio Python Sirius"
        self.discord_activity_type = 2
        self.discord_status_display_type = 0
        self.discord_details_template = "🎵 %title"
        self.discord_state_template = "%artist · %station · %status"
        self.discord_large_text = "RadioStudio Python Sirius"
        self.discord_button1_label = "Ouvir a rádio"
        self.discord_button2_label = ""
        self.discord_button2_url = ""
        self.discord_presence = DiscordPresenceService(self)
        self._discord_track_signature = None
        self._discord_track_started_at = 0
        self._discord_metadata_cache = {}
        self.player_controls_position = "bottom"
        self.waveform_style = "bars"
        self.waveform_visualizers_enabled = True
        self.fade_in_ms = 0
        self.fade_out_ms = 0
        self.mix_ms = 0
        self.auto_transitions_enabled = False
        self.transition_analyses = {}
        self.transition_analyzer = TransitionAnalyzer(self)
        self.transition_analyzer.result_ready.connect(self.transition_analysis_ready)
        self.fade_in_pending_ms = 0
        self.pending_main_cue_ms = 0
        self.trim_silence = False
        self.silence_threshold_db = -42
        self.silence_hold_ms = 1200
        self.main_gain = 1.0
        self.mix_gain = 0.0
        self.master_volume = 0.8
        self.mix_timer = None
        self.mix_target_index = -1
        self.mix_fade_duration_ms = 0
        self.mix_fade_started = False
        self.mix_cue_ms = 0
        self.mix_progress_timer = None
        self.pending_transition_action = None
        self.pending_transition_kind = None
        self._media_end_generation = 0
        self._pending_media_end = None
        self._main_source_generation = 0
        self.mix_failed_key = None
        self.silence_ms = 0
        self._silence_next_duration_path = None
        self._silence_next_duration = 0.0
        self.stop_pending = False
        self._suppress_end_recovery = False
        self.last_playback = {"path": "", "position_ms": 0, "was_playing": False}
        self.pending_resume_position = None
        self.last_position_save_at = 0.0
        self.is_quitting = False
        self.tray_icon = None
        self.audio_decode_fallback = AudioDecodeFallback(self)
        self.decoded_audio_sources = {}
        self.audio_fallback_attempts = {}
        self.player = QMediaPlayer(self)
        self.player_slot_a = self.player
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.8)
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_audio.setVolume(0.8)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_dialog = PreviewPlayerDialog(self.preview_player, self.preview_audio, self)
        self.broadcast = BroadcastManager()
        self.audio_input_controller = AudioInputController(self.broadcast, self)
        self.audio_input_controller.status_changed.connect(self.audio_input_status_changed)
        self.audio_input_controller.speech_activity_changed.connect(self.microphone_speech_changed)
        self.broadcast_format = QAudioFormat()
        self.broadcast_format.setSampleRate(SAMPLE_RATE)
        self.broadcast_format.setChannelCount(CHANNELS)
        self.broadcast_format.setSampleFormat(QAudioFormat.Int16)
        self.main_buffer_output = QAudioBufferOutput(self.broadcast_format, self)
        self.main_buffer_output.audioBufferReceived.connect(self.handle_slot_a_audio_buffer)
        self.player.setAudioBufferOutput(self.main_buffer_output)
        self.player.mediaStatusChanged.connect(
            lambda status, player=self.player: self.slot_media_status_changed(player, status))
        self.player.playbackStateChanged.connect(
            lambda state, player=self.player: self.slot_playback_state_changed(player, state))
        self.player.positionChanged.connect(
            lambda value, player=self.player: self.slot_position_changed(player, value))
        self.player.durationChanged.connect(
            lambda value, player=self.player: self.slot_duration_changed(player, value))
        self.player.seekableChanged.connect(
            lambda _seekable, player=self.player: self.apply_pending_resume_position()
            if player is self.player else None)
        self.mix_player = QMediaPlayer(self)
        self.player_slot_b = self.mix_player
        self.mix_audio = QAudioOutput(self)
        self.mix_audio.setVolume(0.0)
        self.mix_player.setAudioOutput(self.mix_audio)
        self.mix_buffer_output = QAudioBufferOutput(self.broadcast_format, self)
        self.mix_buffer_output.audioBufferReceived.connect(self.handle_slot_b_audio_buffer)
        self.mix_player.setAudioBufferOutput(self.mix_buffer_output)
        self.mix_player.mediaStatusChanged.connect(
            lambda status, player=self.mix_player: self.slot_media_status_changed(player, status))
        self.mix_player.playbackStateChanged.connect(
            lambda state, player=self.mix_player: self.slot_playback_state_changed(player, state))
        self.mix_player.positionChanged.connect(
            lambda value, player=self.mix_player: self.slot_position_changed(player, value))
        self.mix_player.durationChanged.connect(
            lambda value, player=self.mix_player: self.slot_duration_changed(player, value))
        self.mix_player.seekableChanged.connect(
            lambda _seekable, player=self.mix_player: self.apply_pending_resume_position()
            if player is self.player else None)
        self.gain_ramps = {}
        self.gain_paused_at = None
        self.gain_timer = QTimer(self)
        self.gain_timer.setInterval(20)
        self.gain_timer.timeout.connect(self.update_gain_ramps)
        self.duck_timer = QTimer(self)
        self.duck_timer.setInterval(20)
        self.duck_timer.timeout.connect(self.update_music_ducking)
        self.transition_timer = QTimer(self)
        self.transition_timer.setSingleShot(True)
        self.transition_timer.timeout.connect(self.run_pending_transition_action)
        self.cart_players = []
        self.build_ui()
        self.on_air.cover_art.cover_clicked.connect(
            lambda image: self.show_cover_preview(image, "Capa da faixa no ar"))
        self.selected_cover_art.cover_clicked.connect(
            lambda image: self.show_cover_preview(image, "Capa da faixa selecionada"))
        self.cover_art_loader = CoverArtLoader(self)
        self.cover_art_loader.artwork_ready.connect(self.apply_cover_art)
        self.on_air.track_changed.connect(self.request_on_air_cover)
        self.playlist.itemSelectionChanged.connect(self.request_selected_cover)
        QApplication.instance().styleHints().colorSchemeChanged.connect(self.refresh_event_colors)
        self.load_event_log()
        self.load_state()
        self.request_selected_cover()
        self.discord_presence.configure(self.discord_client_id, self.discord_enabled)
        if self.music_library_fallback:
            message = "Banco de bibliotecas indisponível; AutoDJ está usando um índice temporário."
            self.log_event("error", message)
            self.statusBar().showMessage(message)
        self.setup_tray()
        QTimer.singleShot(0, self.restore_playback)
        QTimer.singleShot(500, self.restore_broadcast)
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.timeout.connect(self.check_schedule)
        self.scheduler_timer.start(1000)
        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(60_000)
        self.backup_timer.timeout.connect(self.check_scheduled_backup)
        self.backup_timer.start()
        QTimer.singleShot(1200, self.start_autodj_if_needed)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        self.main_layout = main
        main.setContentsMargins(8, 8, 8, 8)
        heading = QHBoxLayout()
        self.clock = StudioClock(self)
        heading.addWidget(self.clock)
        self.on_air = OnAirPanel(self)
        heading.addWidget(self.on_air, 1)
        settings_button = QPushButton("⚙ Configurações")
        settings_button.clicked.connect(self.show_settings)
        heading.addWidget(settings_button, 0, Qt.AlignTop)
        main.addLayout(heading)
        self.player_controls = self.build_player_controls()

        splitter = QSplitter(Qt.Horizontal)
        main.addWidget(splitter, 1)
        explorer_panel = QWidget()
        explorer_layout = QVBoxLayout(explorer_panel)
        explorer_layout.setContentsMargins(0, 0, 0, 0)
        explorer_splitter = QSplitter(Qt.Vertical)
        explorer_layout.addWidget(explorer_splitter)
        browser_panel = QWidget()
        browser_layout = QVBoxLayout(browser_panel)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(3)
        navigation = QHBoxLayout()
        navigation.setSpacing(1)
        for emoji, tooltip, handler in (
                ("📁", "Escolher pasta de músicas", self.choose_explorer_folder),
                ("💻", "Mostrar unidades deste computador", self.show_drives),
                ("⬆️", "Pasta acima", self.explorer_up),
                ("🏠", "Pasta de músicas", self.open_music_folder),
                ("🔎", "Filtrar arquivos de áudio pelo nome", self.toggle_explorer_search)):
            button = QToolButton()
            button.setText(emoji)
            button.setFont(QFont("Segoe UI Emoji", 10))
            button.setToolTip(tooltip)
            button.setFixedSize(28, 25)
            button.setAutoRaise(True)
            button.clicked.connect(handler)
            navigation.addWidget(button)
        path_button = QToolButton()
        path_button.setText("📍")
        path_button.setFont(QFont("Segoe UI Emoji", 10))
        path_button.setToolTip("Mostrar ou ocultar o caminho da pasta")
        path_button.setFixedSize(28, 25)
        path_button.setAutoRaise(True)
        path_button.clicked.connect(self.toggle_explorer_path)
        navigation.addWidget(path_button)
        navigation.addStretch()
        browser_layout.addLayout(navigation)
        self.explorer_search = QLineEdit()
        self.explorer_search.setPlaceholderText("Filtrar arquivos por nome...")
        self.explorer_search.setClearButtonEnabled(True)
        self.explorer_search.textChanged.connect(self.filter_explorer)
        self.explorer_search.hide()
        browser_layout.addWidget(self.explorer_search)
        self.explorer_filter_timer = QTimer(self)
        self.explorer_filter_timer.setSingleShot(True)
        self.explorer_filter_timer.setInterval(180)
        self.explorer_filter_timer.timeout.connect(self.apply_explorer_filter)
        self.explorer_path = QLineEdit()
        self.explorer_path.setPlaceholderText("Caminho da pasta")
        self.explorer_path.hide()
        self.explorer_path.returnPressed.connect(
            lambda: self.open_explorer_folder(self.explorer_path.text(), persist=True))
        self.file_model = QFileSystemModel(self)
        # QFileSystemModel reacts to every filesystem notification. When a
        # downloader creates many tracks in the open folder, rescanning once
        # per file can keep a CPU core busy. ExplorerMixin replaces that with
        # a debounced directory watcher and refreshes after the burst settles.
        self.file_model.setOption(QFileSystemModel.Option.DontWatchForChanges, True)
        self.file_model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.file_model.setNameFilters([f"*{extension}" for extension in sorted(AUDIO_EXTENSIONS)])
        self.file_model.setNameFilterDisables(False)
        self.file_model.setRootPath("")
        self._explorer_watcher = QFileSystemWatcher(self)
        self._explorer_watcher.directoryChanged.connect(self.queue_explorer_refresh)
        self._explorer_refresh_timer = QTimer(self)
        self._explorer_refresh_timer.setSingleShot(True)
        self._explorer_refresh_timer.setInterval(750)
        self._explorer_refresh_timer.timeout.connect(self.refresh_explorer_folder)
        self._explorer_last_refresh_at = 0.0
        self._explorer_expanded_paths = set()
        self._explorer_mode_switching = False
        self._explorer_restore_state = None
        self._explorer_restore_timer = QTimer(self)
        self._explorer_restore_timer.setSingleShot(True)
        self._explorer_restore_timer.setInterval(10000)
        self._explorer_restore_timer.timeout.connect(self.discard_explorer_restore_state)
        self.file_model.directoryLoaded.connect(self.restore_explorer_view_state)
        self.explorer = QTreeView()
        self.explorer.setModel(self.file_model)
        self.explorer.setRootIndex(QModelIndex())
        self.explorer.setHeaderHidden(True)
        self.explorer.setRootIsDecorated(True)
        self.explorer.setItemsExpandable(True)
        self.explorer.setExpandsOnDoubleClick(False)
        self.explorer.setUniformRowHeights(True)
        self.explorer.setIndentation(17)
        self.explorer.setTextElideMode(Qt.ElideNone)
        for column in (1, 2, 3):
            self.explorer.hideColumn(column)
        self.explorer.setDragEnabled(True)
        self.explorer.setDragDropMode(QTreeView.DragOnly)
        self.explorer.setSelectionMode(QTreeView.ExtendedSelection)
        self.explorer.expanded.connect(self.remember_explorer_expansion)
        self.explorer.collapsed.connect(self.forget_explorer_expansion)
        self.explorer.doubleClicked.connect(self.explorer_double_clicked)
        browser_layout.addWidget(self.explorer, 1)
        self.explorer_path.setMaximumHeight(24)
        self.explorer_path.setToolTip("Pasta principal exibida no explorador; digite um caminho e pressione Enter")
        browser_layout.addWidget(self.explorer_path)
        insert_button = QPushButton("➕ Adicionar seleção")
        insert_button.setToolTip("Adiciona os arquivos de áudio selecionados à Now Playing")
        insert_button.clicked.connect(self.add_explorer_selection)
        browser_layout.addWidget(insert_button)
        explorer_splitter.addWidget(browser_panel)

        log_panel = QWidget()
        log_panel.setMinimumHeight(160)
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 3, 0, 0)
        log_layout.setSpacing(3)
        log_heading = QLabel("📜 Histórico")
        heading_font = log_heading.font()
        heading_font.setBold(True)
        log_heading.setFont(heading_font)
        log_layout.addWidget(log_heading)
        self.event_log = QTableWidget(0, 2)
        self.event_log.setHorizontalHeaderLabels(["Hora", "Evento / título"])
        self.event_log.setColumnWidth(0, 68)
        self.event_log.horizontalHeader().setStretchLastSection(True)
        self.event_log.verticalHeader().hide()
        self.event_log.setSelectionBehavior(QTableWidget.SelectRows)
        self.event_log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.event_log.setMinimumHeight(100)
        log_layout.addWidget(self.event_log)
        explorer_splitter.addWidget(log_panel)
        explorer_splitter.setSizes([330, 300])
        splitter.addWidget(explorer_panel)

        playlist_panel = QWidget()
        playlist_layout = QVBoxLayout(playlist_panel)
        self.playlist_layout = playlist_layout
        playlist_layout.setContentsMargins(0, 0, 0, 0)
        playlist_layout.addWidget(QLabel("Now Playing"))
        toolbar = QGridLayout()
        for index, (label, handler) in enumerate([
                ("+ Áudio", self.add_files), ("+ Pasta", self.add_folder),
                ("Abrir", self.open_m3u), ("Salvar", self.save_m3u),
                ("↑", lambda: self.move_track(-1)), ("↓", lambda: self.move_track(1)),
                ("Remover", self.remove_track)]):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button, index // 4, index % 4)
        playlist_layout.addLayout(toolbar)
        self.playlist = PlaylistTable(self.add_audio_paths, self.commit_playlist_order,
                                      self.move_selected_tracks, self.remove_selected_tracks,
                                      restore_order=lambda: self.refresh_playlist(force=True))
        self.playlist.setHorizontalHeaderLabels(["Título", "Artista", "Álbum"])
        self.playlist.verticalHeader().setDefaultSectionSize(32)
        self.playlist.verticalHeader().setMinimumWidth(38)
        self.playlist.setColumnWidth(1, 115)
        self.playlist.setColumnWidth(2, 115)
        self.playlist.horizontalHeader().setStretchLastSection(True)
        self.playlist.setSelectionBehavior(QTableWidget.SelectRows)
        self.playlist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.playlist.setToolTip(
            "Arraste para reordenar • Ctrl+A seleciona tudo • Delete remove • Ctrl+↑/↓ move a seleção")
        self.playlist.setEditTriggers(QTableWidget.NoEditTriggers)
        self.playlist.doubleClicked.connect(self.play_selected)
        self.playlist.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist.customContextMenuRequested.connect(self.playlist_menu)
        playlist_layout.addWidget(self.playlist)
        playlist_layout.addWidget(self.player_controls)
        self.prelisten_button = QPushButton("🎧 Pré-escutar seleção")
        self.prelisten_button.clicked.connect(self.toggle_prelisten)
        playlist_layout.addWidget(self.prelisten_button)
        splitter.addWidget(playlist_panel)

        tabs = QTabWidget()
        schedule_page = QWidget()
        schedule_layout = QVBoxLayout(schedule_page)
        event_buttons = QHBoxLayout()
        for label, handler in [("Adicionar", self.add_event), ("Editar", self.edit_event),
                               ("Excluir", self.delete_event), ("Executar agora", self.run_event)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            event_buttons.addWidget(button)
        schedule_layout.addLayout(event_buttons)
        self.schedule = QTableWidget(0, 4)
        self.schedule.setHorizontalHeaderLabels(["Ativo", "Hora", "Dias", "Arquivo"])
        self.schedule.horizontalHeader().setStretchLastSection(True)
        self.schedule.setSelectionBehavior(QTableWidget.SelectRows)
        self.schedule.setEditTriggers(QTableWidget.NoEditTriggers)
        schedule_layout.addWidget(self.schedule)
        tabs.addTab(schedule_page, "📅 Agendador")

        cart_page = QWidget()
        cart_layout = QGridLayout(cart_page)
        self.cart_buttons = []
        for index in range(25):
            button = QPushButton(f"Cart {index + 1}")
            button.setMinimumHeight(68)
            button.clicked.connect(lambda checked=False, i=index: self.play_cart(i))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda point, i=index: self.cart_menu(i))
            cart_layout.addWidget(button, index // 5, index % 5)
            self.cart_buttons.append(button)
        tabs.addTab(cart_page, "🎛️ Cart wall")
        self.audio_inputs_panel = AudioInputsPanel(self.show_settings)
        self.audio_inputs_panel.input_toggled.connect(self.set_audio_input_active)
        inputs_scroll = QScrollArea()
        inputs_scroll.setWidgetResizable(True)
        inputs_scroll.setWidget(self.audio_inputs_panel)
        tabs.addTab(inputs_scroll, "🎙 Entradas")
        self.broadcast_panel = BroadcastPanel(self)
        broadcast_scroll = QScrollArea()
        broadcast_scroll.setWidgetResizable(True)
        broadcast_scroll.setWidget(self.broadcast_panel)
        tabs.addTab(broadcast_scroll, "📡 Transmissão")
        self.autodj_panel = AutoDJPanel(self)
        tabs.addTab(self.autodj_panel, "🤖 AutoDJ")
        splitter.addWidget(tabs)
        splitter.setSizes([260, 620, 370])
        self.build_menu_bar()
        self.statusBar().showMessage("Pronto")
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.clock.update_time)
        self.clock_timer.timeout.connect(self.refresh_playback_display)
        self.clock_timer.timeout.connect(self.sync_discord_presence)
        self.clock_timer.start(1000)
        music_folder = QStandardPaths.writableLocation(QStandardPaths.MusicLocation)
        self.open_explorer_folder(
            music_folder if music_folder and Path(music_folder).is_dir() else str(Path.home()))

    def build_player_controls(self):
        panel = QWidget()
        panel.setMinimumHeight(76)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        selected_cover_row = QHBoxLayout()
        selected_cover_row.addStretch(1)
        self.selected_cover_art = CoverArtLabel(
            112, panel, "da faixa selecionada na Now Playing")
        selected_cover_row.addWidget(self.selected_cover_art)
        layout.addLayout(selected_cover_row)
        transport = QHBoxLayout()
        transport.setSpacing(4)
        for label, handler in [("▶ Reproduzir", self.play_selected), ("⏸ Pausar", self.pause),
                               ("⏹ Parar", self.stop), ("⏭ Próxima", self.next_track)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            transport.addWidget(button)
        transport.addStretch(1)
        transport.addWidget(QLabel("Volume"))
        volume = QSlider(Qt.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(80)
        self.volume_slider = volume
        volume.setFixedWidth(100)
        volume.valueChanged.connect(self.set_master_volume)
        transport.addWidget(volume)
        layout.addLayout(transport)
        timeline = QHBoxLayout()
        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderMoved.connect(self.seek_main_player)
        timeline.addWidget(self.position, 1)
        self.position_label = QLabel("00:00 / 00:00")
        timeline.addWidget(self.position_label)
        layout.addLayout(timeline)
        return panel

    @guarded
    def request_on_air_cover(self, path: str):
        self._discord_cover_version = ""
        self.on_air.cover_art.set_cover_image(None)
        self.broadcast.set_cover_art(None)
        if path:
            self.cover_art_loader.request(path)

    @guarded
    def request_selected_cover(self):
        row = self.playlist.currentRow()
        item = self.playlist.item(row, 0) if row >= 0 else None
        path = str(item.data(Qt.UserRole) or "") if item else ""
        self.selected_cover_art.set_cover_image(None)
        if path:
            self.cover_art_loader.request(path)

    @guarded
    def apply_cover_art(self, path: str, artwork):
        if not path:
            return
        current_path = getattr(self.on_air, "_track_path", "")
        if same_audio_path(current_path, path):
            image = artwork.image if artwork is not None else None
            self.on_air.cover_art.set_cover_image(image)
            self.broadcast.set_cover_art(
                artwork.data if artwork is not None else None,
                artwork.mime_type if artwork is not None else "")
            self._discord_cover_version = (
                hashlib.blake2s(artwork.data, digest_size=8).hexdigest()
                if artwork is not None else "")
            self.sync_discord_presence()

        row = self.playlist.currentRow()
        item = self.playlist.item(row, 0) if row >= 0 else None
        selected_path = str(item.data(Qt.UserRole) or "") if item else ""
        if same_audio_path(selected_path, path):
            self.selected_cover_art.set_cover_image(
                artwork.image if artwork is not None else None)

    @guarded
    def show_cover_preview(self, image, title: str = "Visualizar capa"):
        if image is None or image.isNull():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        apply_app_icon(dialog)
        available = self.screen().availableGeometry()
        max_width = max(200, min(900, available.width() - 80))
        max_height = max(200, min(800, available.height() - 140))
        pixmap = QPixmap.fromImage(image).scaled(
            max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        layout = QVBoxLayout(dialog)
        preview = QLabel(dialog)
        preview.setAlignment(Qt.AlignCenter)
        preview.setPixmap(pixmap)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.accept)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.resize(pixmap.width() + 24, pixmap.height() + 64)
        dialog.exec()

    @guarded
    def set_player_controls_position(self, position):
        position = "top" if position == "top" else "bottom"
        self.main_layout.removeWidget(self.player_controls)
        self.playlist_layout.removeWidget(self.player_controls)
        if position == "top":
            self.main_layout.insertWidget(1, self.player_controls)
        else:
            self.playlist_layout.insertWidget(
                self.playlist_layout.indexOf(self.prelisten_button), self.player_controls)
        self.player_controls.show()
        self.player_controls_position = position

    def build_menu_bar(self):
        bar = self.menuBar()
        bar.setNativeMenuBar(False)

        def add(menu, label, handler, shortcut=None):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(handler)
            menu.addAction(action)
            return action

        files = bar.addMenu("&Arquivo")
        add(files, "Adicionar áudio...", self.add_files, "Ctrl+O")
        add(files, "Adicionar pasta...", self.add_folder)
        add(files, "Abrir playlist...", self.open_m3u)
        add(files, "Salvar playlist...", self.save_m3u, "Ctrl+S")
        files.addSeparator()
        tray_action = add(files, "Ocultar na bandeja", self.hide_to_tray)
        tray_action.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        add(files, "Sair", self.request_exit, "Ctrl+Q")

        playback = bar.addMenu("&Reprodução")
        add(playback, "Reproduzir / Pausar", self.toggle_playback, "Ctrl+Space")
        add(playback, "Próxima faixa", self.next_track, "Ctrl+Right")
        add(playback, "Parar", self.stop)

        transmission = bar.addMenu("&Transmissão")
        add(transmission, "Iniciar transmissão", self.broadcast_panel.start)
        add(transmission, "Parar transmissão", self.broadcast_panel.stop)

        preferences = bar.addMenu("&Configurações")
        add(preferences, "Preferências...", self.show_settings, "Ctrl+P")

        help_menu = bar.addMenu("A&juda")
        add(help_menu, "Manual do usuário", self.open_user_manual)
        help_menu.addSeparator()
        add(help_menu, f"Sobre o {APP_NAME}", self.show_about)

    @guarded
    def open_user_manual(self):
        if not USER_MANUAL_FILE.is_file():
            QMessageBox.warning(
                self, "Manual não encontrado",
                "O PDF do manual não está disponível. Reinstale ou atualize o RadioStudio.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(USER_MANUAL_FILE))):
            QMessageBox.warning(
                self, "Não foi possível abrir o manual",
                "O Windows não encontrou um aplicativo padrão para arquivos PDF.",
            )

    @guarded
    def show_about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sobre")
        apply_app_icon(dialog)
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(ABOUT_LOGO_FILE))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(460, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            logo.setStyleSheet("background-color: #f3f6fa; border-radius: 6px; padding: 6px;")
            layout.addWidget(logo)

        details = QLabel(
            f"<b>{APP_NAME}</b><br>"
            f"Versão: {APP_VERSION}<br><br>"
            "Automação de áudio, playlist e transmissão.<br><br>"
            "Desenvolvido por Raemi Labs<br>"
            "Autor: Raphael Cherfem Carlini<br>"
            "Apoiado e testado por:<br>"
            "- Gabriel Sousa<br>"
            "Contato: <a href=\"mailto:contato@gabastt.shop\">contato@gabastt.shop</a><br>"
            "© 2026 Raemi Labs<br><br>"
            "<b>Idioma:</b> disponível somente em Português nesta versão.<br>"
            "<b>Relatar bugs:</b> "
            "<a href=\"mailto:raphael@raemi.xyz\">raphael@raemi.xyz</a>"
        )
        details.setTextFormat(Qt.TextFormat.RichText)
        details.setOpenExternalLinks(True)
        details.setWordWrap(True)
        layout.addWidget(details)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.accept)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    @guarded
    def sync_discord_presence(self):
        if not self.discord_client_id:
            self.discord_presence.set_activity({})
            return

        main_state = self.player.playbackState()
        mix_state = self.mix_player.playbackState()
        use_mix = (self.mix_target_index >= 0 and self.mix_gain > self.main_gain
                   and mix_state in (QMediaPlayer.PlayingState, QMediaPlayer.PausedState))
        active_player = self.mix_player if use_mix else self.player
        active_state = mix_state if use_mix else main_state
        active_index = self.mix_target_index if use_mix else self.current_index
        playing = active_state == QMediaPlayer.PlayingState
        paused = active_state == QMediaPlayer.PausedState
        broadcasting = bool(self.broadcast.running)

        metadata = None
        if (playing or paused) and 0 <= active_index < len(self.tracks):
            track_path = self.tracks[active_index]
            path_key = audio_path_key(track_path)
            metadata = self._discord_metadata_cache.get(path_key)
            if metadata is None:
                metadata = read_track_metadata(track_path)
                # Rich Presence is refreshed every second. Keep a small cache so
                # tag parsing and filesystem access happen once per track, not
                # once per UI tick. Queue edits and removals can visit many
                # tracks, so cap retained metadata to a few recent entries.
                if len(self._discord_metadata_cache) >= 8:
                    self._discord_metadata_cache.pop(next(iter(self._discord_metadata_cache)))
                self._discord_metadata_cache[path_key] = metadata
        if metadata:
            signature = metadata.path
            if signature != self._discord_track_signature:
                self._discord_track_signature = signature
                self._discord_track_started_at = int(time.time() - max(0, active_player.position()) / 1000)
            start_time = self._discord_track_started_at
            duration_ms = max(0, int(active_player.duration()))
            duration_seconds = duration_ms / 1000 if duration_ms else metadata.duration
            end_time = start_time + int(duration_seconds) if duration_seconds > 0 else None
            title, artist = metadata.title, metadata.artist
        else:
            self._discord_track_signature = None
            self._discord_track_started_at = 0
            start_time = end_time = None
            title = artist = ""

        try:
            activity = make_activity(
                title=title, artist=artist, album=metadata.album if metadata else "",
                station=self.station_metadata.get("name", "RadioStudio Python"),
                broadcasting=broadcasting, playing=playing, paused=paused,
                share_track=self.discord_share_track,
                public_url=self.discord_public_url,
                large_image=self.discord_large_image,
                cover_url=(self.discord_cover_url if self.discord_use_track_cover else ""),
                cover_version=self._discord_cover_version,
                activity_name=self.discord_activity_name,
                activity_type=self.discord_activity_type,
                status_display_type=self.discord_status_display_type,
                details_template=self.discord_details_template,
                state_template=self.discord_state_template,
                large_text=self.discord_large_text,
                button1_label=self.discord_button1_label,
                button2_label=self.discord_button2_label,
                button2_url=self.discord_button2_url,
                started_at=start_time, ends_at=end_time)
        except ValueError as exc:
            logging.getLogger("radiostudio.discord").warning(
                "Link público de Rich Presence ignorado: %s", exc)
            self.discord_public_url = ""
            self.discord_button2_url = ""
            activity = make_activity(
                title=title, artist=artist, album=metadata.album if metadata else "",
                station=self.station_metadata.get("name", "RadioStudio Python"),
                broadcasting=broadcasting, playing=playing, paused=paused,
                share_track=self.discord_share_track,
                large_image=self.discord_large_image,
                activity_name=self.discord_activity_name,
                activity_type=self.discord_activity_type,
                status_display_type=self.discord_status_display_type,
                details_template=self.discord_details_template,
                state_template=self.discord_state_template,
                large_text=self.discord_large_text,
                button1_label=self.discord_button1_label,
                button2_label=self.discord_button2_label,
                button2_url=self.discord_button2_url,
                started_at=start_time, ends_at=end_time)
        self.discord_presence.set_activity(activity)

    @guarded
    def setup_tray(self):
        icon = app_icon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.SP_MediaPlay)
        self.setWindowIcon(icon)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(APP_DISPLAY_NAME)
        tray_menu = QMenu(self)
        tray_menu.addAction(f"Abrir {APP_NAME}", self.restore_window)
        tray_menu.addSeparator()
        tray_menu.addAction("Reproduzir / Pausar", self.toggle_playback)
        tray_menu.addAction("Próxima faixa", self.next_track)
        tray_menu.addAction("Parar", self.stop)
        tray_menu.addSeparator()
        tray_menu.addAction("Sair", self.request_exit)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    @guarded
    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.restore_window()

    @guarded
    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @guarded
    def hide_to_tray(self):
        if self.tray_icon and self.tray_icon.isVisible():
            self.save_state()
            self.hide()

    @guarded
    def request_exit(self):
        self.is_quitting = True
        self.close()
