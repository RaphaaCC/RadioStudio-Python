"""Scheduler operations for the main window."""

from ._shared import *
from ..runtime_safety import guarded


class SchedulerMixin:
    @guarded
    def refresh_schedule(self):
        day_names = ["Todos", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        self.schedule.setRowCount(len(self.events))
        for i, event in enumerate(self.events):
            values = ["Sim" if event["enabled"] else "Não", event["time"],
                      day_names[event["day"]], Path(event["path"]).name]
            for col, value in enumerate(values):
                self.schedule.setItem(i, col, QTableWidgetItem(value))

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
        if row < 0:
            return
        dialog = EventDialog(self, self.events[row])
        if dialog.exec() == QDialog.Accepted:
            self.events[row] = dialog.value()
            self.refresh_schedule()
            self.save_state()

    @guarded
    def delete_event(self):
        row = self.schedule.currentRow()
        if row >= 0:
            del self.events[row]
            self.refresh_schedule()
            self.save_state()

    @guarded
    def run_event(self):
        row = self.schedule.currentRow()
        if row >= 0:
            event = self.events[row]
            now = datetime.now()
            matches_day = event["day"] == 0 or event["day"] == now.weekday() + 1
            if matches_day and event["time"] == now.strftime("%H:%M"):
                self.last_fired.add((event.get("id") or row,
                                     now.strftime("%Y-%m-%d %H:%M")))
            self.execute_event(event)

    @guarded
    def execute_event(self, event):
        path = event["path"]
        if not Path(path).is_file():
            self.statusBar().showMessage(f"Evento ignorado: arquivo ausente: {path}")
            return
        self.cancel_pending_transition()
        insert_at = self.current_index + 1 if self.current_index >= 0 else 0
        self.tracks.insert(insert_at, path)
        self.track_sources.insert(insert_at, "schedule")
        self.refresh_playlist()
        self.play_index(insert_at)
        self.save_state()

    @guarded
    def check_schedule(self):
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        due = []
        for index, event in enumerate(self.events):
            key = (event.get("id") or index, stamp)
            matches_day = event["day"] == 0 or event["day"] == now.weekday() + 1
            if event["enabled"] and matches_day and event["time"] == now.strftime("%H:%M") and key not in self.last_fired:
                self.last_fired.add(key)
                due.append(event)
        if due:
            valid = [event for event in due if Path(event["path"]).is_file()]
            for event in due:
                if event not in valid:
                    self.statusBar().showMessage(
                        f"Evento ignorado: arquivo ausente: {event['path']}")
            if valid:
                first_insert = self.current_index + 1 if self.current_index >= 0 else 0
                self.execute_event(valid[0])
                if len(valid) > 1:
                    paths = [event["path"] for event in valid[1:]]
                    insert_at = first_insert + 1
                    self.tracks[insert_at:insert_at] = paths
                    self.track_sources[insert_at:insert_at] = ["schedule"] * len(paths)
                    self.refresh_playlist()
                    self.save_state()
        if len(self.last_fired) > 100:
            self.last_fired = {key for key in self.last_fired if key[1] == stamp}
        self.fill_autodj_queue()
