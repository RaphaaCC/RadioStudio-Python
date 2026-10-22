"""Scheduler operations for the main window."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ._shared import *
from ..runtime_safety import guarded
from ..i18n import translate
from ..services.schedule_engine import (
    latest_occurrence_at_or_before, next_occurrence, normalize_event, occurrence_key,
)
from .event_dialog import EventDialog
from .playlist import m3u_entries


LATE_GRACE = timedelta(minutes=5)
LATE_DISPLAY_THRESHOLD = timedelta(seconds=5)


class SchedulerMixin:
    def _event_description(self, event):
        action = event["action"]
        target = event["target"]
        if action in {"play_track", "play_playlist"}:
            return Path(target).name
        if action == "play_cart":
            return translate(f"Cart {target + 1}")
        descriptions = {
            ("playback_command", "play"): "Reproduzir",
            ("playback_command", "pause"): "Pausar",
            ("playback_command", "resume"): "Retomar",
            ("playback_command", "stop"): "Parar",
            ("playback_command", "next"): "Próxima faixa",
            ("broadcast_command", "start"): "Iniciar transmissão",
            ("broadcast_command", "stop"): "Parar transmissão",
        }
        return translate(descriptions.get((action, target), "Ação agendada"))

    def _event_recurrence_label(self, event):
        if event["recurrence"] == "once":
            try:
                event_date = datetime.strptime(event["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                event_date = event["date"]
            return f"{translate('Uma vez')} · {event_date}"
        weekday_names = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
        days = ", ".join(translate(weekday_names[day]) for day in event["weekdays"])
        return days

    @guarded
    def refresh_schedule(self):
        now = datetime.now()
        self.schedule.setRowCount(len(self.events))
        for row, raw_event in enumerate(self.events):
            event = normalize_event(raw_event)
            if event is None:
                values = [translate("Não"), "—", "—", translate("Evento inválido"), "—", "—"]
            else:
                occurrence = next_occurrence(event, now) if event["enabled"] else None
                if occurrence:
                    next_run = occurrence.strftime("%d/%m/%Y %H:%M")
                elif event["recurrence"] == "once" and event["last_result"] in {"success", "late", "manual"}:
                    next_run = translate("Concluído")
                elif event["recurrence"] == "once" and event["last_result"] == "missed":
                    next_run = translate("Perdido")
                elif event["recurrence"] == "once" and event["last_result"] == "failed":
                    next_run = translate("Falhou")
                else:
                    next_run = "—"
                result_labels = {
                    "success": "Executado", "late": "Executado com atraso",
                    "missed": "Perdido", "failed": "Falhou", "manual": "Executado manualmente",
                }
                values = [
                    translate("Sim" if event["enabled"] else "Não"),
                    next_run,
                    self._event_recurrence_label(event),
                    translate({
                        "play_track": "Tocar faixa", "play_playlist": "Carregar playlist",
                        "play_cart": "Executar Cart Wall",
                        "playback_command": "Comando de reprodução",
                        "broadcast_command": "Comando de transmissão",
                    }[event["action"]]),
                    self._event_description(event),
                    translate(result_labels.get(event["last_result"], "Nunca executado")),
                ]
            for column, value in enumerate(values):
                self.schedule.setItem(row, column, QTableWidgetItem(value))

    @guarded
    def add_event(self):
        dialog = EventDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.events.append(dialog.value())
            self.refresh_schedule()
            self.save_state()

    @guarded
    def edit_event(self):
        row = self.schedule.currentRow()
        if not 0 <= row < len(self.events):
            return
        dialog = EventDialog(self, self.events[row])
        if dialog.exec() == QDialog.Accepted:
            self.events[row] = dialog.value()
            self.refresh_schedule()
            self.save_state()

    @guarded
    def delete_event(self):
        row = self.schedule.currentRow()
        if 0 <= row < len(self.events):
            del self.events[row]
            self.refresh_schedule()
            self.save_state()

    def _event_log(self, result, event, detail=""):
        summary = self._event_description(event)
        messages = {
            "success": "Evento agendado executado",
            "late": "Evento agendado atrasado executado",
            "missed": "Evento agendado perdido",
            "failed": "Falha em evento agendado",
            "manual": "Evento executado manualmente",
        }
        message = f"{translate(messages[result])}: {summary}"
        if detail:
            message += f" — {detail}"
        logger = getattr(self, "log_event", None)
        if callable(logger):
            logger("error" if result in {"missed", "failed"} else "info", message)
        status_bar = getattr(self, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(message, 8000)

    def _mark_event_result(self, event, result, occurrence=None):
        if occurrence is not None:
            event["last_occurrence"] = occurrence_key(occurrence)
        event["last_result"] = result
        if event["recurrence"] == "once" and result in {"success", "late", "missed", "failed"}:
            event["enabled"] = False

    @guarded
    def run_event(self):
        row = self.schedule.currentRow()
        if not 0 <= row < len(self.events):
            return
        event = normalize_event(self.events[row])
        if event is None:
            self._event_log("failed", {"action": "play_track", "target": "?"},
                            translate("Evento inválido"))
            return
        self.events[row] = event
        now = datetime.now()
        occurrence = latest_occurrence_at_or_before(event, now)
        if occurrence is None or occurrence_key(occurrence) != now.strftime("%Y-%m-%d %H:%M"):
            occurrence = None
        self._execute_event_batch([(row, event, occurrence, False, True)])

    @guarded
    def execute_event(self, event):
        """Compatibility entry point used by older callers and tests."""
        raw_event = dict(event) if isinstance(event, dict) else event
        if isinstance(raw_event, dict):
            raw_event.setdefault("time", datetime.now().strftime("%H:%M"))
            raw_event.setdefault("day", 0)
        normalized = normalize_event(raw_event)
        if normalized is None:
            raise ValueError(translate("Evento inválido"))
        index = len(self.events)
        self.events.append(normalized)
        self._execute_event_batch([(index, normalized, None, False, True)])

    def _load_media_for_event(self, event):
        target = Path(event["target"]).expanduser()
        if event["action"] == "play_track":
            if not target.is_file():
                raise FileNotFoundError(str(target))
            return [str(target)]
        if not target.is_file():
            raise FileNotFoundError(str(target))
        lines = m3u_entries(target.read_text(encoding="utf-8-sig", errors="replace").splitlines())
        paths = []
        missing = 0
        for line in lines:
            candidate = Path(line).expanduser()
            if not candidate.is_absolute():
                candidate = target.parent / candidate
            candidate_text = str(candidate.resolve())
            if Path(candidate_text).is_file():
                paths.append(candidate_text)
            else:
                missing += 1
        if not paths:
            raise ValueError(translate("A playlist não contém faixas disponíveis."))
        if missing:
            logger = getattr(self, "log_event", None)
            if callable(logger):
                logger("error", f"{translate('Faixas ausentes ignoradas na playlist')}: {missing}")
        return paths

    def _player_is_active(self):
        player = getattr(self, "player", None)
        if player is None:
            return self.current_index >= 0
        return player.playbackState() in (QMediaPlayer.PlayingState, QMediaPlayer.PausedState)

    def _execute_media_group(self, entries, results):
        media_events = [entry for entry in entries
                        if entry[1]["action"] in {"play_track", "play_playlist"}]
        if not media_events:
            return
        paths = []
        replace_queue = False
        successful = []
        for entry in media_events:
            index, event, _occurrence, late, manual = entry
            try:
                event_paths = self._load_media_for_event(event)
            except Exception as exc:
                results[index] = ("failed", str(exc))
                self._event_log("failed", event, str(exc))
                continue
            paths.extend(event_paths)
            successful.append((index, event, late, manual))
            if event["action"] == "play_playlist" and event["playlist_mode"] in {"replace", "load_only"}:
                replace_queue = True

        if not paths:
            return
        first_successful_event = successful[0][1]
        first_is_load_only = (first_successful_event["action"] == "play_playlist"
                              and first_successful_event["playlist_mode"] == "load_only")
        start_policy = first_successful_event["playback_mode"]
        if first_is_load_only:
            replace_queue = True
        active = self._player_is_active()
        if replace_queue:
            if getattr(self, "mix_target_index", -1) >= 0:
                self.cancel_mix()
            if active and 0 <= self.current_index < len(self.tracks):
                # Keep played rows and the current row so any in-progress
                # fade-to-next callback still points at the correct next slot.
                prefix_end = self.current_index + 1
                self.tracks = self.tracks[:prefix_end] + paths
                self.track_sources = self.track_sources[:prefix_end] + ["schedule"] * len(paths)
                start_index = prefix_end
            else:
                self.tracks = paths
                self.track_sources = ["schedule"] * len(paths)
                self.current_index = -1
                start_index = 0
        else:
            start_index = self.current_index + 1 if self.current_index >= 0 else 0
            self.tracks[start_index:start_index] = paths
            self.track_sources[start_index:start_index] = ["schedule"] * len(paths)

        if successful:
            if first_is_load_only:
                should_start = False
            elif active:
                should_start = start_policy == "at_time"
            else:
                should_start = True
            if should_start and start_index < len(self.tracks):
                self.play_index(start_index)
            self.refresh_playlist()
            self.save_state()
            for index, event, late, manual in successful:
                result = "manual" if manual else "late" if late else "success"
                results[index] = (result, "")
                self._event_log(result, event)

    def _execute_non_media_event(self, event):
        action = event["action"]
        target = event["target"]
        if action == "play_cart":
            if target >= len(getattr(self, "carts", [])) or not self.carts[target]:
                raise ValueError(translate("O Cart selecionado não tem áudio atribuído."))
            path = self.carts[target]
            if not Path(path).is_file():
                raise FileNotFoundError(path)
            self.play_cart(target)
            return
        if action == "playback_command":
            state = self.player.playbackState()
            if target == "play":
                if state == QMediaPlayer.PausedState:
                    self.pause()
                elif state == QMediaPlayer.StoppedState:
                    self.play_selected()
            elif target == "pause":
                if state == QMediaPlayer.PlayingState:
                    self.pause()
            elif target == "resume":
                if state == QMediaPlayer.PausedState:
                    self.pause()
                elif state == QMediaPlayer.StoppedState:
                    self.play_selected()
            elif target == "stop":
                self.stop()
            elif target == "next":
                self.next_track()
            return
        if action == "broadcast_command":
            panel = self.broadcast_panel
            if target == "start":
                if not getattr(self, "broadcast_was_running", False):
                    panel.start()
                    if not getattr(self, "broadcast_was_running", False):
                        raise RuntimeError(translate("Não foi possível iniciar a transmissão."))
            elif target == "stop" and getattr(self, "broadcast_was_running", False):
                panel.stop()
                if getattr(self, "broadcast_was_running", False):
                    raise RuntimeError(translate("Não foi possível parar a transmissão."))
            return
        raise ValueError(translate("Ação agendada inválida."))

    def _execute_event_batch(self, entries):
        """Run one occurrence group, preserving table order for scheduled media."""
        results = {}
        media_done = False
        for index, event, occurrence, late, manual in entries:
            self.events[index] = event
            if not manual:
                self._mark_event_result(event, "late" if late else "success", occurrence)
            elif occurrence is not None:
                self._mark_event_result(event, "manual", occurrence)
                if event["recurrence"] == "once":
                    event["enabled"] = False
            if event["action"] in {"play_track", "play_playlist"}:
                if media_done:
                    continue
                self._execute_media_group(entries, results)
                media_done = True
                continue
            try:
                self._execute_non_media_event(event)
                result = "manual" if manual else "late" if late else "success"
                results[index] = (result, "")
                self._event_log(result, event)
            except Exception as exc:
                results[index] = ("failed", str(exc))
                self._event_log("failed", event, str(exc))

        for index, (result, _detail) in results.items():
            event = self.events[index]
            event["last_result"] = result
            if result == "failed" and event["recurrence"] == "once":
                event["enabled"] = False
        self.refresh_schedule()
        self.save_state()

    @guarded
    def check_schedule(self):
        now = datetime.now()
        started_at = getattr(self, "scheduler_started_at", None)
        if not isinstance(started_at, type(now)):
            started_at = now.replace(year=1, month=1, day=1, hour=0, minute=0,
                                     second=0, microsecond=0)
        due_by_occurrence = defaultdict(list)
        changed = False
        for index, raw_event in enumerate(self.events):
            event = normalize_event(raw_event)
            if event is None:
                continue
            if event != raw_event:
                self.events[index] = event
                changed = True
            if not event["enabled"]:
                continue
            occurrence = latest_occurrence_at_or_before(event, now)
            if occurrence is None:
                continue
            key = occurrence_key(occurrence)
            if event["last_occurrence"] == key:
                continue
            lateness = now - occurrence
            if occurrence < started_at or lateness > LATE_GRACE:
                self._mark_event_result(event, "missed", occurrence)
                self._event_log("missed", event)
                changed = True
                continue
            self._mark_event_result(event, "late" if lateness >= LATE_DISPLAY_THRESHOLD else "success",
                                    occurrence)
            due_by_occurrence[key].append((index, event, occurrence,
                                           lateness >= LATE_DISPLAY_THRESHOLD, False))
            changed = True

        for key in sorted(due_by_occurrence):
            entries = sorted(due_by_occurrence[key], key=lambda item: item[0])
            self._execute_event_batch(entries)
        if changed and not due_by_occurrence:
            self.refresh_schedule()
            self.save_state()
        self.fill_autodj_queue()
