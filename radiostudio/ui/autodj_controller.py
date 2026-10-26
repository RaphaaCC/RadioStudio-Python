"""AutoDJ queue generation and durable played-track history hooks."""

import logging
import time
from copy import deepcopy

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ._shared import *
from ..runtime_safety import guarded
from ..services.audio_paths import audio_path_key
from ..services.autodj import (
    active_program, choose_track, filter_tracks, program_signature, reserve_track,
)


def _cooldown_seconds(value):
    try:
        return max(0, int(value)) * 60
    except (TypeError, ValueError, OverflowError):
        return 0


class _AutoDJFillSignals(QObject):
    finished = Signal(object)


class _AutoDJFillWorker(QRunnable):
    """Load and rank indexed tracks without blocking the interface thread."""

    def __init__(self, library, request):
        super().__init__()
        self.library = library
        self.request = request
        self.signals = _AutoDJFillSignals()

    @Slot()
    def run(self):
        request = self.request
        try:
            program = request["program"]
            sources = request.get("sources", [])
            if sources:
                sources = [
                    dict(source) for source in sources
                    if isinstance(source, dict)
                    and source.get("type") in {"library", "collection", "group", "tag"}
                    and str(source.get("id", "")).strip()
                ]
                selected_library_sources = {
                    str(source.get("id", "")) for source in sources
                    if source.get("type") == "library"
                }
                sources.extend({"type": "library", "id": str(library_id), "weight": 1.0}
                               for library_id in request.get("library_ids", [])
                               if str(library_id) not in selected_library_sources)
            candidates = (self.library.tracks_for_sources(sources) if sources
                          else self.library.tracks_for_libraries(request["library_ids"]))
            if not sources:
                apply_overrides = getattr(self.library, "apply_metadata_overrides", None)
                if callable(apply_overrides):
                    candidates = apply_overrides(candidates)
            candidates = filter_tracks(candidates, program.get("filters", {}) if program else {})
            if not candidates:
                name = request["program_name"]
                message = (
                    translate("O bloco ‘{program_name}’ não encontrou faixas. Confira as fontes e os filtros.")
                    .format(program_name=name)
                    if program else translate(
                        "A Biblioteca não tem faixas indexadas nas pastas habilitadas para o AutoDJ."))
                self.signals.finished.emit({"status": message, "candidate_count": 0})
                return

            selection_time = time.time()
            config = request["config"]
            raw_history = self.library.latest_track_plays(
                path_window_seconds=_cooldown_seconds(config.get("track_cooldown_minutes", 360)),
                artist_window_seconds=_cooldown_seconds(config.get("artist_cooldown_minutes", 60)),
                now=selection_time)
            history = (dict(raw_history[0]), dict(raw_history[1]))
            reserved_keys = {audio_path_key(path) for path in request["reserved_paths"]}
            reserved_keys.discard("")
            if reserved_keys:
                # Mark already queued tracks in one pass rather than building
                # a second full-library path dictionary just for these few rows.
                for candidate in candidates:
                    if audio_path_key(candidate.get("path", "")) in reserved_keys:
                        reserve_track(history, candidate, now=selection_time)

            excluded = list(request["excluded_paths"])
            paths, notes = [], []
            needed = request["needed"]
            while needed > 0:
                track, note = choose_track(
                    candidates, history,
                    artist_cooldown_minutes=config.get("artist_cooldown_minutes", 60),
                    track_cooldown_minutes=config.get("track_cooldown_minutes", 360),
                    excluded_paths=excluded,
                    now=selection_time,
                )
                if not track:
                    if note:
                        notes.append(note)
                    break
                path = str(track["path"])
                paths.append(path)
                excluded.append(path)
                reserve_track(history, track, now=selection_time)
                needed -= 1
                if note and note not in notes:
                    notes.append(note)

            self.signals.finished.emit({
                "paths": paths,
                "notes": notes,
                "needed": needed,
                "candidate_count": len(candidates),
                "program_name": request["program_name"],
                "queue_target": request["queue_target"],
            })
        except Exception as exc:
            logging.getLogger("radiostudio").exception("Falha ao completar a fila do AutoDJ")
            self.signals.finished.emit({"error": str(exc)})


class AutoDJControllerMixin:
    def _autodj_selection(self):
        libraries = self.music_library.libraries()
        enabled_ids = {
            str(lib["id"]) for lib in libraries
            if lib.get("enabled") and lib.get("id") is not None
        }
        program = active_program(self.autodj_programs)
        requested_ids = program.get("library_ids", []) if program else []
        if not isinstance(requested_ids, (list, tuple, set)):
            requested_ids = []
        requested_ids = list(dict.fromkeys(
            str(value) for value in requested_ids if value is not None and str(value)
        ))
        sources = program.get("sources", []) if program else []
        if not isinstance(sources, (list, tuple)):
            sources = []
        sources = [
            source for source in sources
            if isinstance(source, dict)
            and source.get("type") in {"library", "collection", "group", "tag"}
            and str(source.get("id", "")).strip()
        ]
        if requested_ids:
            selected_ids = sorted(enabled_ids.intersection(requested_ids))
        elif sources:
            # An explicit collection, group, tag, or weighted source selection
            # can stand on its own. Only an empty block selection means all
            # enabled indexed folders.
            selected_ids = []
        else:
            selected_ids = sorted(enabled_ids)
        return program, selected_ids, bool(requested_ids or sources)

    def current_autodj_signature(self):
        program, selected_ids, _has_requested_libraries = self._autodj_selection()
        return program_signature(program), tuple(sorted(selected_ids, key=str))

    @guarded
    def start_autodj_if_needed(self):
        if not self.autodj_config.get("enabled"):
            self.autodj_startup_pending = False
            return
        try:
            self.fill_autodj_queue(force=True)
        finally:
            self.autodj_startup_pending = False
        if self.player.playbackState() == QMediaPlayer.StoppedState and self.tracks:
            # Start the restored queue while the background fill prepares any
            # additional rows. Qt may remain StoppedState until media loads.
            if self.pending_track_log:
                return
            self.next_track()

    @guarded
    def set_autodj_enabled(self, enabled):
        enabled = bool(enabled)
        self.autodj_config["enabled"] = enabled
        self.autodj_last_signature = None
        self.autodj_last_fill = 0.0
        if not enabled:
            self._autodj_waiting_for_queue = False
        self.autodj_panel.set_enabled(enabled)
        self.log_event("info", "AutoDJ ativado" if enabled else "AutoDJ desativado")
        self.save_state()
        if enabled:
            self.start_autodj_if_needed()
        elif hasattr(self, "autodj_panel"):
            self.autodj_panel.set_status("AutoDJ desligado; a fila atual continua disponível.")

    @guarded
    def fill_autodj_queue(self, force=False, allow_current_repeat=False,
                          bypass_throttle=False):
        if not hasattr(self, "music_library") or not hasattr(self, "autodj_panel"):
            return
        now_mono = time.monotonic()
        config = self.autodj_config
        if not config.get("enabled") and not force:
            return

        if getattr(self, "_autodj_fill_worker", None) is not None:
            if force or bypass_throttle:
                self._autodj_fill_pending_again = True
            return

        if not force and not bypass_throttle and now_mono - self.autodj_last_fill < 10:
            # Schedule blocks can change on a minute boundary while the
            # periodic queue check is throttled. Detect that cheap change
            # without loading library tracks or play history, so stale
            # generated rows are replaced immediately.
            current_program_signature = program_signature(
                active_program(self.autodj_programs))
            previous_signature = self.autodj_last_signature
            previous_program_signature = None
            if isinstance(previous_signature, (list, tuple)) and previous_signature:
                previous_program_signature = previous_signature[0]
                if isinstance(previous_program_signature, list):
                    previous_program_signature = tuple(previous_program_signature)
            if previous_program_signature == current_program_signature:
                return

        self.autodj_last_fill = now_mono

        program, selected_ids, has_requested_libraries = self._autodj_selection()
        signature = (program_signature(program), tuple(sorted(selected_ids, key=str)))

        if signature != self.autodj_last_signature:
            if getattr(self, "autodj_startup_pending", False):
                # Preserve the queue restored from disk even if a different
                # schedule block became active while the app was closed.
                self.autodj_last_signature = signature
                self.save_state()
            else:
                self._clear_generated_autodj_tracks()
                self.autodj_last_signature = signature
        program_name = (str(program.get("name") or translate("Bloco")) if program
                        else translate("Aleatório — Biblioteca completa"))
        selected_sources = (program.get("sources", []) if program
                            and isinstance(program.get("sources", []), (list, tuple)) else [])
        selected_sources = [
            source for source in selected_sources
            if isinstance(source, dict)
            and source.get("type") in {"library", "collection", "group", "tag"}
            and str(source.get("id", "")).strip()
        ]
        if not selected_ids and not selected_sources:
            self.autodj_panel.set_status(
                "As pastas selecionadas para este bloco estão desabilitadas ou indisponíveis; ajuste a seleção."
                if has_requested_libraries
                else "Ative ao menos uma pasta indexada da Biblioteca para usar o AutoDJ.")
            return

        if self.current_index >= 0:
            pending_count = max(0, len(self.tracks) - self.current_index - 1)
            excluded = list(self.tracks[
                self.current_index + 1 if allow_current_repeat else self.current_index:])
        else:
            pending_count = len(self.tracks)
            excluded = list(self.tracks)
        target = int(config.get("queue_ahead", 4))
        if not config.get("enabled"):
            target = max(target, 1) if force else 0
        needed = max(0, target - pending_count)
        if not needed:
            # The scheduler checks once per second and queue filling is
            # throttled to ten seconds. Avoid loading every indexed track
            # and aggregating the play-history database when the queue is full.
            self.autodj_panel.set_status(translate("{program_name}: fila completa").format(
                program_name=program_name))
            return

        request = {
            "signature": signature,
            "current_index": self.current_index,
            "tracks": tuple(self.tracks),
            "track_sources": tuple(self.track_sources),
            "library_ids": list(selected_ids),
            "sources": deepcopy(selected_sources),
            "program": deepcopy(program) if program else None,
            "program_name": program_name,
            "config": {
                "enabled": bool(config.get("enabled")),
                "queue_ahead": int(config.get("queue_ahead", 4)),
                "artist_cooldown_minutes": config.get("artist_cooldown_minutes", 60),
                "track_cooldown_minutes": config.get("track_cooldown_minutes", 360),
            },
            "needed": needed,
            "queue_target": target,
            "stop_generation": getattr(self, "_autodj_stop_generation", 0),
            "excluded_paths": tuple(excluded),
            # Cooldowns also cover the current/upcoming queue, including rows
            # preserved from a previous session.
            "reserved_paths": tuple(self.tracks[max(0, self.current_index):]),
        }
        worker = _AutoDJFillWorker(self.music_library, request)
        worker.signals.finished.connect(self._autodj_fill_finished)
        self._autodj_fill_worker = worker
        self._autodj_fill_request = request
        self._autodj_fill_pending_again = False
        QThreadPool.globalInstance().start(worker, priority=-1)

    @Slot(object)
    @guarded
    def _autodj_fill_finished(self, result):
        request = getattr(self, "_autodj_fill_request", None)
        self._autodj_fill_worker = None
        self._autodj_fill_request = None
        if request is None or getattr(self, "is_quitting", False):
            return

        current_config = self.autodj_config
        config_defaults = {"enabled": False, "queue_ahead": 4,
                           "artist_cooldown_minutes": 60,
                           "track_cooldown_minutes": 360}
        config_matches = all(
            current_config.get(key, config_defaults[key]) == value
            for key, value in request["config"].items()
        )
        queue_matches = (
            self.current_index == request["current_index"]
            and tuple(self.tracks) == request["tracks"]
            and tuple(self.track_sources) == request["track_sources"]
        )
        try:
            signature_matches = self.current_autodj_signature() == request["signature"]
        except Exception:
            signature_matches = False
        if not (config_matches and queue_matches and signature_matches):
            old_index = request["current_index"]
            old_current = (request["tracks"][old_index]
                           if 0 <= old_index < len(request["tracks"]) else "")
            current = (self.tracks[self.current_index]
                       if 0 <= self.current_index < len(self.tracks) else "")
            if (self.current_index != old_index
                    or audio_path_key(current) != audio_path_key(old_current)):
                # A different row became current while the queue was loading.
                # Do not let the stale end-of-queue request skip that track.
                self._autodj_waiting_for_queue = False
            if current_config.get("enabled") and not getattr(self, "is_quitting", False):
                self.fill_autodj_queue(force=True, bypass_throttle=True)
            else:
                self._autodj_waiting_for_queue = False
                self._autodj_fill_pending_again = False
            return

        if "error" in result:
            self.autodj_panel.set_status("Falha ao carregar o catálogo da Biblioteca para o AutoDJ.")
            log_event = getattr(self, "log_event", None)
            if callable(log_event):
                log_event("error", translate(
                    "Falha ao completar a fila do AutoDJ: {error}").format(error=result["error"]))
            added_paths = []
        elif "status" in result:
            self.autodj_panel.set_status(result["status"])
            added_paths = []
        else:
            added_paths = list(result.get("paths", ()))
            for path in added_paths:
                self.tracks.append(path)
                self.track_sources.append("autodj")
            if added_paths:
                self.refresh_playlist()
                self.save_state()
                if self.current_index < 0:
                    self.playlist.selectRow(0)
            notes = result.get("notes", ())
            label = translate("{program_name}: {count} faixa(s) adicionada(s)").format(
                program_name=request["program_name"], count=len(added_paths)) if added_paths else (
                    translate("{program_name}: fila atualizada").format(
                        program_name=request["program_name"]))
            if notes:
                label += " • " + translate(notes[0])
            elif result.get("needed") and result.get("candidate_count", 0) <= result.get("queue_target", 0):
                label += " • " + translate("biblioteca pequena; evitei repetir faixas na fila")
            self.autodj_panel.set_status(label)

        waiting = getattr(self, "_autodj_waiting_for_queue", False)
        self._autodj_waiting_for_queue = False
        if waiting:
            if added_paths:
                self.next_track()
            else:
                self.stop()
        elif (added_paths and self.current_index < 0 and current_config.get("enabled")
              and self.player.playbackState() == QMediaPlayer.StoppedState
              and getattr(self, "_autodj_stop_generation", 0)
              == request["stop_generation"]):
            self.next_track()

        rerun = getattr(self, "_autodj_fill_pending_again", False)
        self._autodj_fill_pending_again = False
        if rerun and current_config.get("enabled") and not getattr(self, "is_quitting", False):
            self.fill_autodj_queue(force=True, bypass_throttle=True)

    @guarded
    def _clear_generated_autodj_tracks(self):
        if not getattr(self, "track_sources", None):
            return
        keep_current = self.current_index if self.current_index >= 0 else -1
        removed = [index for index, source in enumerate(self.track_sources)
                   if source == "autodj" and index > keep_current]
        if not removed:
            return
        next_index = self.current_index + 1
        transition_target_invalidated = (
            (self.mix_target_index >= 0
             and any(index <= self.mix_target_index for index in removed))
            or (getattr(self, "pending_transition_kind", None) == "advance"
                and any(index <= next_index for index in removed)))
        if transition_target_invalidated:
            # A playing secondary deck or fade callback still refers to the
            # old queue index. Cancel it before replacing generated rows so it
            # cannot promote audio from a removed track under a new row's title.
            self.cancel_pending_transition()
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
            path = str(path)
            metadata = None
            if audio_path_key(path) == audio_path_key(
                    getattr(self, "_display_sync_path", "")):
                metadata = getattr(self, "_display_sync_metadata", None)
            if metadata is None:
                library = getattr(self, "music_library", None)
                getter = getattr(library, "metadata_for_track", None)
                metadata = getter(path) if callable(getter) else read_track_metadata(path)
            self.music_library.record_play(metadata)
            if hasattr(self, "autodj_panel"):
                self.autodj_panel.schedule_history_refresh()
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
