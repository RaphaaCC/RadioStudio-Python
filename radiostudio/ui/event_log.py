"""Event Log operations for the main window."""

import logging
import os

from ._shared import *
from ..runtime_safety import guarded


class EventLogMixin:
    @guarded
    def append_event_row(self, timestamp: str, kind: str, message: str):
        icons = {"music": "🎵", "connected": "📡", "disconnected": "🔌", "error": "⚠️"}
        if self.event_log.rowCount() >= MAX_LOG_ROWS:
            self.event_log.removeRow(0)
        row = self.event_log.rowCount()
        self.event_log.insertRow(row)
        for column, value in enumerate((timestamp[11:19], f"{icons.get(kind, '•')} {message}")):
            item = QTableWidgetItem(value)
            item.setToolTip(f"{timestamp}\n{message}")
            item.setData(Qt.UserRole, kind)
            self.color_event_item(item, kind)
            self.event_log.setItem(row, column, item)
        self.event_log.scrollToBottom()

    @guarded
    def color_event_item(self, item: QTableWidgetItem, kind: str,
                         scheme=None):
        if scheme is None:
            scheme = QApplication.instance().styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Unknown:
            window = QApplication.instance().palette().color(QPalette.Window)
            dark = window.lightness() < 128
        else:
            dark = scheme == Qt.ColorScheme.Dark
        colors = ({"connected": "#75d8a6", "error": "#ff8a80"} if dark else
                  {"connected": "#067647", "error": "#b42318"})
        if kind in colors:
            item.setForeground(QColor(colors[kind]))
        else:
            item.setData(Qt.ForegroundRole, None)

    @guarded
    def refresh_event_colors(self, scheme):
        for row in range(self.event_log.rowCount()):
            for column in range(self.event_log.columnCount()):
                item = self.event_log.item(row, column)
                if item:
                    self.color_event_item(item, item.data(Qt.UserRole), scheme)

    @guarded
    def load_event_log(self):
        path = APP_DIR / "events.jsonl"
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as source:
                for line in deque(source, maxlen=MAX_LOG_ROWS):
                    try:
                        entry = json.loads(line)
                        self.append_event_row(entry["time"], entry["kind"], entry["message"])
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            logging.getLogger("radiostudio").exception(
                "Não foi possível carregar o histórico de eventos")

    @guarded
    def log_event(self, kind: str, message: str):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        self.append_event_row(timestamp, kind, message)
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            path = APP_DIR / "events.jsonl"
            try:
                if path.stat().st_size > 2_000_000:
                    oldest = path.with_name(path.name + ".4")
                    oldest.unlink(missing_ok=True)
                    for index in range(3, 0, -1):
                        source = path.with_name(path.name + f".{index}")
                        if source.exists():
                            os.replace(source, path.with_name(path.name + f".{index + 1}"))
                    os.replace(path, path.with_name(path.name + ".1"))
            except OSError:
                # Rotation is best-effort; the current event is still useful.
                pass
            with path.open("a", encoding="utf-8") as destination:
                destination.write(json.dumps({"time": timestamp, "kind": kind,
                                              "message": message}, ensure_ascii=False) + "\n")
        except OSError:
            logging.getLogger("radiostudio").exception(
                "Não foi possível gravar o histórico de eventos")
            self.statusBar().showMessage("Não foi possível gravar o histórico no disco")
