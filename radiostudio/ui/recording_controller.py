"""Recording ownership and schedule dispatch for the main window."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..i18n import translate


class RecordingControllerMixin:
    def start_recording_session(self, owner: str) -> str | None:
        settings = self.recording_config
        try:
            session_id = self.broadcast.start_recording(
                settings.get("folder", ""), settings.get("format", "mp3"),
                settings.get("bitrate", 192))
        except Exception as exc:
            logging.getLogger("radiostudio").exception("Falha ao iniciar a gravação")
            error = translate(str(exc))
            message = f"Falha ao iniciar a gravação: {error}"
            self.log_event("error", message)
            self.statusBar().showMessage(translate("Falha ao iniciar a gravação: {error}").format(
                error=error))
            return None
        if session_id is None:
            message = translate("Gravação ignorada: outra gravação já está ativa.")
            self.log_event("error", message)
            self.statusBar().showMessage(message)
            return None
        session = self.broadcast.recording
        self._recording_owner = owner
        self.log_event("connected", f"Gravação iniciada: {session.path}")
        self.save_state()
        return session_id

    def stop_recording_session_if_id(self, session_id: str | None, *, reason="manual") -> bool:
        session = self.broadcast.recording
        if not session or (session_id is not None and session.session_id != session_id):
            return False
        if not self.broadcast.stop_recording(session_id, wait=False):
            return False
        if getattr(self, "_recording_owner", "") == reason or reason == "manual":
            self._recording_owner = ""
        self.log_event("disconnected", f"Gravação finalizando: {session.path}")
        return True

    def on_broadcast_started(self):
        if self.recording_config.get("auto_on_broadcast"):
            session_id = self.start_recording_session("broadcast")
            self._broadcast_recording_session_id = session_id

    def on_broadcast_stopped(self):
        session_id = getattr(self, "_broadcast_recording_session_id", None)
        self._broadcast_recording_session_id = None
        if session_id:
            self.stop_recording_session_if_id(session_id, reason="broadcast")

    def check_recording_schedules(self):
        now = datetime.now().replace(second=0, microsecond=0)
        stamp = now.strftime("%Y-%m-%d %H:%M")
        fired = self._recording_schedule_fired
        active_sessions = self.recording_schedule_session

        # End only the exact session started by this schedule. Manual or other
        # automatic recordings are never stopped by a schedule's end time.
        for schedule_id, active in tuple(active_sessions.items()):
            try:
                end_at = datetime.fromisoformat(active["end_at"])
            except (TypeError, ValueError):
                active_sessions.pop(schedule_id, None)
                continue
            if now >= end_at:
                self.stop_recording_session_if_id(active.get("session_id"), reason=schedule_id)
                active_sessions.pop(schedule_id, None)

        for schedule in tuple(self.recording_schedules):
            if not schedule.get("enabled"):
                continue
            schedule_id = schedule.get("id", "")
            if schedule["type"] == "once":
                match_start = schedule.get("date") == now.strftime("%Y-%m-%d")
            else:
                match_start = now.weekday() in schedule.get("weekdays", [])
            start_key = (schedule_id, stamp)
            if match_start and schedule.get("start") == now.strftime("%H:%M") \
                    and start_key not in fired:
                fired.add(start_key)
                end_at = now.replace(hour=int(schedule["end"][:2]),
                                     minute=int(schedule["end"][3:]))
                if end_at <= now:
                    end_at += timedelta(days=1)
                session_id = self.start_recording_session(schedule_id)
                if session_id:
                    active_sessions[schedule_id] = {
                        "session_id": session_id, "end_at": end_at.isoformat(),
                    }
                if schedule["type"] == "once":
                    schedule["enabled"] = False
                    self.recording_panel.refresh_schedules()
                    self.save_state()
        if len(fired) > 256:
            self._recording_schedule_fired = {item for item in fired if item[1][:10] == stamp[:10]}
