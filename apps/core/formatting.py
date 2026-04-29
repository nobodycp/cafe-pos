"""تنسيقات أرقام ثابتة للعرض في HTML/JSON (نقطة عشرية إنجليزية، منزلتان)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Union

Numberish = Union[Decimal, int, float, str, None]


def decimal_plain_2(value: Numberish, *, quantize: str = "0.01") -> str:
    """
    سلسلة ASCII مناسبة لـ value في حقول number و data-*.
    لا تعتمد على تفعيل التعريب في القوالب.
    """
    if value is None or value == "":
        return "0.00"
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "0.00"
    q = Decimal(quantize)
    try:
        d = d.quantize(q, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return "0.00"
    return format(d, "f")
