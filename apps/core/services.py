from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.core.models import WorkSession, log_audit


class SessionService:
    @staticmethod
    def get_open_session():
        return WorkSession.objects.filter(status=WorkSession.Status.OPEN).order_by("-created_at").first()

    @staticmethod
    def require_open_session():
        s = SessionService.get_open_session()
        if not s:
            raise ValueError("WORK_SESSION_REQUIRED")
        return s

    @staticmethod
    @transaction.atomic
    def open_session(user, opening_cash, notes=""):
        if SessionService.get_open_session():
            raise ValueError("SESSION_ALREADY_OPEN")
        ws = WorkSession.objects.create(
            opened_by=user,
            opening_cash=opening_cash or 0,
            notes=notes or "",
        )
        log_audit(user, "work_session.open", "core.WorkSession", ws.pk, {"opening_cash": str(opening_cash)})
        return ws

    @staticmethod
    @transaction.atomic
    def close_session(user, closing_cash=None, notes=""):
        ws = SessionService.require_open_session()
        ws.status = WorkSession.Status.CLOSED
        ws.closed_by = user
        ws.closed_at = timezone.now()
        if closing_cash is not None:
            ws.closing_cash = closing_cash
        if notes:
            ws.notes = (ws.notes + "\n" + notes).strip() if ws.notes else notes
        ws.save()
        log_audit(user, "work_session.close", "core.WorkSession", ws.pk, {"closing_cash": str(closing_cash)})
        return ws
