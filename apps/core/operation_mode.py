"""نمط تشغيل المنشأة: ورديات عمل أو محاسبة مستمرة."""

from __future__ import annotations

from apps.core.models import PosSettings

MODE_SHIFTS = "shifts"
MODE_CONTINUOUS = "continuous"


def get_operation_mode() -> str:
    s, _ = PosSettings.objects.get_or_create(pk=1)
    mode = (getattr(s, "operation_mode", None) or MODE_CONTINUOUS).strip()
    if mode not in (MODE_SHIFTS, MODE_CONTINUOUS):
        return MODE_CONTINUOUS
    return mode


def uses_shifts() -> bool:
    return get_operation_mode() == MODE_SHIFTS


def uses_continuous() -> bool:
    return not uses_shifts()


def requires_work_session_for_pos() -> bool:
    return uses_shifts()


def show_work_session_on_receipt() -> bool:
    if not uses_shifts():
        return False
    s, _ = PosSettings.objects.get_or_create(pk=1)
    return bool(getattr(s, "receipt_show_work_session", True))
