"""AutoDJ queue generation and durable played-track history hooks."""

import logging
import time

from ._shared import *
from ..runtime_safety import guarded
from ..services.autodj import active_program, choose_track, filter_tracks, program_signature


class AutoDJControllerMixin:
    @guarded
    def start_autodj_if_needed(self):
        if not self.autodj_config.get("enabled"):
            return
        self.fill_autodj_queue(force=True)
        if self.player.playbackState() == QMediaPlayer.StoppedState and self.tracks:
            # fill_autodj_queue may already have started the first item. Qt can
            # report StoppedState until the asynchronous media load completes.
            if self.pending_track_log:
                return
            self.next_track()

    @guarded
    def set_autodj_enabled(self, enabled):
        enabled = bool(enabled)
        self.autodj_config["enabled"] = enabled
        self.autodj_last_signature = None
        self.autodj_last_fill = 0.0
        self.autodj_panel.set_enabled(enabled)
        self.log_event("info", "AutoDJ ativado" if enabled else "AutoDJ desativado")
        self.save_state()
        if enabled:
            self.start_autodj_if_needed()
        elif hasattr(self, "autodj_panel"):
            self.autodj_panel.set_status("AutoDJ desligado; a fila atual continua disponível.")

    @guarded
    def fill_autodj_queue(self, force=False, allow_current_repeat=False):
        if not hasattr(self, "music_library") or not hasattr(self, "autodj_panel"):
            return
        now_mono = time.monotonic()
        if not force and now_mono - self.autodj_last_fill < 3:
            return
        self.autodj_last_fill = now_mono

        config = self.autodj_config
        if not config.get("enabled") and not force:
            return

        libraries = self.music_library.libraries()
        enabled_ids = [lib["id"] for lib in libraries if lib["enabled"]]
        program = active_program(self.autodj_programs)
        requested_ids = program.get("library_ids", []) if program else []
        if not isinstance(requested_ids, (list, tuple, set)):
            requested_ids = []
        requested_ids = list(requested_ids)
        selected_ids = [value for value in requested_ids if value in enabled_ids]
        if not requested_ids:
            selected_ids = enabled_ids
        signature = (program_signature(program), tuple(sorted(selected_ids)))

        if signature != self.autodj_last_signature:
            self._clear_generated_autodj_tracks()
            self.autodj_last_signature = signature
        program_name = str(program.get("name") or "Bloco musical") if program else "Aleatório — todas as bibliotecas"
        if not selected_ids:
            self.autodj_panel.set_status(
                "O bloco não tem bibliotecas habilitadas; ajuste sua seleção."
                if requested_ids else "Adicione e examine pelo menos uma biblioteca habilitada.")
            return

        candidates = self.music_library.tracks_for_libraries(selected_ids)
        candidates = filter_tracks(candidates, program.get("filters", {}) if program else {})
        if not candidates:
            message = (f"O bloco ‘{program_name}’ não encontrou faixas. Confira filtros e bibliotecas."
                       if program else "As bibliotecas ainda não têm faixas indexadas.")
            self.autodj_panel.set_status(message)
            return

        if self.current_index >= 0:
            pending_count = max(0, len(self.tracks) - self.current_index - 1)
            excluded_from = self.current_index + 1 if allow_current_repeat else self.current_index
            excluded = list(self.tracks[excluded_from:])
        else:
            pending_count = len(self.tracks)
            excluded = list(self.tracks)
        target = int(config.get("queue_ahead", 4))
        if not config.get("enabled"):
            target = max(target, 1) if force else 0
        needed = max(0, target - pending_count)
        history = self.music_library.latest_track_plays()
        added = 0
        notes = []
        while needed > 0:
            track, note = choose_track(
                candidates, history,
                artist_cooldown_minutes=config.get("artist_cooldown_minutes", 60),
                track_cooldown_minutes=config.get("track_cooldown_minutes", 360),
                excluded_paths=excluded,
            )
            if not track:
                if note:
                    notes.append(note)
                break
            self.tracks.append(track["path"])
            self.track_sources.append("autodj")
            excluded.append(track["path"])
            added += 1
            needed -= 1
            if note and note not in notes:
                notes.append(note)
        if added:
            self.refresh_playlist()
            self.save_state()
            if self.current_index < 0:
                self.playlist.selectRow(0)
                if config.get("enabled") and self.player.playbackState() == QMediaPlayer.StoppedState:
                    self.play_index(0)
        label = f"{program_name}: {added} faixa(s) adicionada(s)" if added else f"{program_name}: fila atualizada"
        if notes:
            label += " • " + notes[0]
        elif needed and len(candidates) <= target:
            label += " • biblioteca pequena; evitei repetir faixas na fila"
        self.autodj_panel.set_status(label)

    @guarded
    def _clear_generated_autodj_tracks(self):
        if not getattr(self, "track_sources", None):
            return
        keep_current = self.current_index if self.current_index >= 0 else -1
        removed = [index for index, source in enumerate(self.track_sources)
                   if source == "autodj" and index > keep_current]
        if not removed:
            return
        for index in reversed(removed):
            del self.tracks[index]
            del self.track_sources[index]
        self.refresh_playlist()
        self.save_state()

    @guarded
    def record_track_started(self, path):
        if not path:
            return
        try:
            metadata = read_track_metadata(path)
            self.music_library.record_play(metadata)
            if hasattr(self, "autodj_panel"):
                self.autodj_panel.refresh_history()
        except Exception:
            logging.getLogger("radiostudio").exception("Falha ao registrar histórico da faixa: %s", path)
        # Playlist mutations are deferred until the media-state callback unwinds;
        # touching QTableWidget inside QMediaPlayer's signal can re-enter Qt's
        # playback transition and crash some multimedia backends.
        if self.autodj_config.get("remove_played_from_queue"):
            QTimer.singleShot(0, self.trim_played_queue)

    @guarded
    def trim_played_queue(self, persist=True):
        """Drop the queue prefix while keeping the current row and all upcoming rows."""
        if not self.autodj_config.get("remove_played_from_queue") or self.current_index <= 0:
            return
        removed = self.current_index
        del self.tracks[:removed]
        del self.track_sources[:removed]
        self.current_index = 0
        if self.mix_target_index >= 0:
            self.mix_target_index = max(0, self.mix_target_index - removed)
        self.refresh_playlist()
        self.playlist.selectRow(0)
        if persist:
            self.save_state()
