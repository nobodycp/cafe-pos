from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.models import WorkSession, log_audit
from apps.core.operation_mode import requires_work_session_for_pos
from apps.core.payment_methods import load_payment_method_rows


class SessionService:
    @staticmethod
    def get_open_session():
        return WorkSession.objects.filter(status=WorkSession.Status.OPEN).order_by("-created_at").first()

    @staticmethod
    def pos_is_ready() -> bool:
        """هل يمكن استخدام الكاشير الآن (وردية مفتوحة أو وضع مستمر)."""
        if not requires_work_session_for_pos():
            return True
        return SessionService.get_open_session() is not None

    @staticmethod
    def pos_session_filter_kwargs() -> dict:
        """فلتر الطلبات/الجلسات للسياق الحالي."""
        if not requires_work_session_for_pos():
            return {"work_session__isnull": True}
        ws = SessionService.get_open_session()
        if not ws:
            return {"work_session_id": -1}
        return {"work_session": ws}

    @staticmethod
    def order_belongs_to_pos_context(order) -> bool:
        if not requires_work_session_for_pos():
            return order.work_session_id is None
        ws = SessionService.get_open_session()
        return ws is not None and order.work_session_id == ws.id

    @staticmethod
    def require_open_session():
        if not requires_work_session_for_pos():
            return None
        s = SessionService.get_open_session()
        if not s:
            raise ValueError("WORK_SESSION_REQUIRED")
        return s

    @staticmethod
    @transaction.atomic
    def open_session(user, opening_cash, notes="", opening_balances=None):
        if SessionService.get_open_session():
            raise ValueError("SESSION_ALREADY_OPEN")
        rows = load_payment_method_rows()
        balances: dict[str, str] = {}
        q = Decimal("0.01")
        if opening_balances:
            for r in rows:
                code = r["code"]
                raw = str(opening_balances.get(code, "0") or "0").strip().replace(",", ".")
                try:
                    v = Decimal(raw) if raw else Decimal("0")
                except (InvalidOperation, ValueError):
                    v = Decimal("0")
                if v < 0:
                    v = Decimal("0")
                balances[code] = str(v.quantize(q))
        else:
            try:
                oc = Decimal(str(opening_cash or 0).replace(",", "."))
            except (InvalidOperation, ValueError):
                oc = Decimal("0")
            if oc < 0:
                oc = Decimal("0")
            for r in rows:
                c = r["code"]
                balances[c] = str(oc.quantize(q)) if c == "cash" else "0.00"
        cash_dec = Decimal(balances.get("cash", "0"))
        ws = WorkSession.objects.create(
            opened_by=user,
            opening_cash=cash_dec,
            opening_balances_json=balances,
            notes=notes or "",
        )
        log_audit(
            user,
            "work_session.open",
            "core.WorkSession",
            ws.pk,
            {"opening_cash": str(cash_dec), "opening_balances_json": balances},
        )
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
