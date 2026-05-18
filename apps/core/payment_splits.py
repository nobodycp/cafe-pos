"""Parse ``payment_splits_json`` payloads shared across POS, billing, purchasing, expenses, treasury."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.core.exceptions import BusinessError


class PaymentSplitsParseError(BusinessError, ValueError):
    """Raised when JSON structure or a split row fails validation."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code, code=code)


def _amount_from_raw(amt_raw: Any, *, quantize: bool) -> Decimal:
    amount = Decimal(str(amt_raw).replace(",", "."))
    if quantize:
        return amount.quantize(Decimal("0.01"))
    return amount


def _split_code_and_amount(item: Any) -> tuple[str, Any] | None:
    if isinstance(item, dict):
        code = str(item.get("method") or "").strip().lower()
        amt_raw = item.get("amount")
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        code = str(item[0] or "").strip().lower()
        amt_raw = item[1]
    else:
        return None
    return code, amt_raw


def parse_payment_splits_json(
    raw: str,
    *,
    allowed_codes: frozenset[str],
    max_rows: int = 24,
    skip_invalid_methods: bool = False,
    skip_invalid_amounts: bool = False,
    quantize: bool = False,
) -> list[tuple[str, Decimal]]:
    """
    Parse ``[[code, amount], ...]`` or ``{method, amount}`` list payloads.

    Sum / credit-remainder checks stay with callers.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PaymentSplitsParseError("INVALID_JSON") from exc

    if not isinstance(data, list):
        raise PaymentSplitsParseError("INVALID_SHAPE")
    if len(data) > max_rows:
        raise PaymentSplitsParseError("TOO_MANY_ROWS")

    lines: list[tuple[str, Decimal]] = []
    for item in data:
        parsed = _split_code_and_amount(item)
        if parsed is None:
            continue
        code, amt_raw = parsed
        if not code:
            if skip_invalid_methods:
                continue
            raise PaymentSplitsParseError("INVALID_METHOD")
        if code not in allowed_codes:
            if skip_invalid_methods:
                continue
            raise PaymentSplitsParseError("INVALID_METHOD")
        try:
            amount = _amount_from_raw(amt_raw, quantize=quantize)
        except (InvalidOperation, ValueError, TypeError) as exc:
            if skip_invalid_amounts:
                continue
            raise PaymentSplitsParseError("INVALID_AMOUNT") from exc
        if amount <= 0:
            continue
        lines.append((code, amount))
    return lines
