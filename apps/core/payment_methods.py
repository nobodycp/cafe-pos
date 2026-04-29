"""
طرق الدفع الموحّدة: تُعرّف في جدول PaymentMethod وتُستخدم في الكاشير والسندات والمصروفات…
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from django.core.exceptions import ValidationError

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
LEDGER_ALLOWED = frozenset({"cash", "bank", "ar"})

LEGACY_LEDGER_MAP: Dict[str, str] = {
    "cash": "CASH",
    "bank": "BANK",
    "bank_ps": "BANK",
    "palpay": "BANK",
    "jawwalpay": "BANK",
    "credit": "AR",
}

DEFAULT_PAYMENT_METHOD_ROWS: List[Dict[str, str]] = [
    {"code": "cash", "label_ar": "كاش", "ledger": "cash"},
    {"code": "bank_ps", "label_ar": "بنك فلسطين", "ledger": "bank"},
    {"code": "palpay", "label_ar": "بال باي", "ledger": "bank"},
    {"code": "jawwalpay", "label_ar": "جوال باي", "ledger": "bank"},
    {"code": "credit", "label_ar": "آجل", "ledger": "ar"},
]


def _ledger_to_sys(ledger: str) -> str:
    lg = (ledger or "bank").strip().lower()
    if lg == "cash":
        return "CASH"
    if lg == "ar":
        return "AR"
    return "BANK"


def _rows_from_queryset(qs) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for pm in qs:
        out.append(
            {
                "code": pm.code,
                "label_ar": (pm.label_ar or "")[:120],
                "ledger": pm.ledger,
            }
        )
    return out


def load_payment_method_rows() -> List[Dict[str, str]]:
    from apps.core.models import PaymentMethod

    qs = PaymentMethod.objects.filter(is_active=True).order_by("sort_order", "pk")
    rows = _rows_from_queryset(qs)
    if rows:
        return rows
    return [dict(x) for x in DEFAULT_PAYMENT_METHOD_ROWS]


def resolve_ledger_account_code(method_code: str) -> str:
    """رمز حساب الصندوق/البنك/الذمم لـ _get_account: CASH | BANK | AR."""
    mc = (method_code or "").strip().lower()
    for row in load_payment_method_rows():
        if row["code"] == mc:
            return _ledger_to_sys(row["ledger"])
    return LEGACY_LEDGER_MAP.get(mc, "BANK")


def resolve_cash_bank_line_code(method_code: str) -> str:
    """للدفع/التحصيل النقدي (سند قبض/صرف): لا نستخدم AR كسطر مدين/دائن نقدي."""
    sys_code = resolve_ledger_account_code(method_code)
    if sys_code == "AR":
        return "BANK"
    return sys_code


def get_payment_method_choices() -> List[Tuple[str, str]]:
    return [(r["code"], r["label_ar"]) for r in load_payment_method_rows()]


def get_payment_method_codes() -> frozenset:
    return frozenset(r["code"] for r in load_payment_method_rows())


def credit_method_codes() -> frozenset:
    return frozenset(r["code"] for r in load_payment_method_rows() if r.get("ledger") == "ar")


def method_codes_requiring_payer_details() -> frozenset:
    """أكواد طرق الدفع ذات الحساب «شبكة/بنك» — يُطلب اسم المحوّل والجوال عند الدفع في الكاشير."""
    return frozenset(
        r["code"] for r in load_payment_method_rows() if (r.get("ledger") or "").strip().lower() == "bank"
    )


def payment_bucket_keys() -> List[str]:
    keys = [r["code"] for r in load_payment_method_rows()]
    extra = ["bank"]
    seen = set(keys)
    for k in extra:
        if k not in seen:
            keys.append(k)
            seen.add(k)
    return keys


def payments_list_to_dict(payments: List[Tuple]) -> Dict[str, Decimal]:
    """يجمع المبالغ حسب الرمز؛ يقبل (method, amount) أو (method, amount, payer_name, payer_phone, …)."""
    d = {k: Decimal("0") for k in payment_bucket_keys()}
    for item in payments:
        if not item:
            continue
        m = str(item[0])
        amt = item[1]
        if m in d:
            a = amt if isinstance(amt, Decimal) else Decimal(str(amt))
            d[m] += a
    return d


def assert_active_cash_payment_method() -> None:
    """يُستدعى بعد الحفظ/الحذف للتأكد من بقاء كاش نشط."""
    from apps.core.models import PaymentMethod

    if not PaymentMethod.objects.filter(is_active=True, code="cash").exists():
        raise ValidationError(
            "يجب وجود طريقة دفع نشطة برمز «cash» (كاش). أضف أو فعّل طريقة كاش قبل المتابعة.",
        )


# ── توافق مع هجرات/كود قديم يقرأ JSON (لم يعد مستخدماً في التشغيل) ──


def _normalize_rows(raw: Any) -> List[Dict[str, str]]:
    if raw is None or raw == "" or raw == []:
        return [dict(x) for x in DEFAULT_PAYMENT_METHOD_ROWS]
    if not isinstance(raw, list):
        return [dict(x) for x in DEFAULT_PAYMENT_METHOD_ROWS]
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip().lower()
        label = str(item.get("label_ar", "")).strip()
        ledger = str(item.get("ledger", "bank")).strip().lower()
        if not CODE_RE.match(code) or not label or ledger not in LEDGER_ALLOWED:
            continue
        out.append({"code": code, "label_ar": label[:120], "ledger": ledger})
    if not out:
        return [dict(x) for x in DEFAULT_PAYMENT_METHOD_ROWS]
    return out


def payment_method_rows_for_instance(payment_methods_json: Any) -> List[Dict[str, str]]:
    """مهمل — استخدم load_payment_method_rows()."""
    return _normalize_rows(payment_methods_json)


def validate_payment_methods_json(raw: Any) -> List[Dict[str, str]]:
    rows = _normalize_rows(raw)
    codes = [r["code"] for r in rows]
    if len(codes) != len(set(codes)):
        raise ValidationError("رموز طرق الدفع يجب أن تكون فريدة.")
    if "cash" not in set(codes):
        raise ValidationError("يجب تضمين طريقة كاش (الرمز: cash).")
    return rows


def default_payment_methods_json() -> list:
    return [dict(x) for x in DEFAULT_PAYMENT_METHOD_ROWS]
