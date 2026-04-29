"""تحويل آمن إلى Decimal للخدمات والعروض (بدون منطق أعمال)."""
from __future__ import annotations

from decimal import Decimal


def as_decimal(value) -> Decimal:
    """
    يعيد Decimal؛ None → 0؛ القيم Decimal تُعاد كما هي؛ غير ذلك عبر str().
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
