"""كشف حركة لكل طريقة دفع: مبيعات، مصروفات، سدادات موردين، سند قبض عميل/موظف."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, List, Optional

from django.utils import timezone

from apps.billing.models import InvoicePayment
from apps.core.models import AuditLog
from apps.core.payment_methods import load_payment_method_rows, payment_method_label_map
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.expenses.models import Expense
from apps.purchasing.models import SupplierPayment


def _parse_voucher_date(payload: dict, fallback: datetime) -> date:
    raw = payload.get("voucher_date")
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    if timezone.is_aware(fallback):
        return fallback.astimezone(timezone.get_current_timezone()).date()
    return fallback.date()


def _as_decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


@dataclass
class LedgerRow:
    """kind_code ثابت للفلترة: sale_payment, expense, supplier_payment, treasury_customer, treasury_employee."""

    method_code: str
    sort_at: datetime
    row_date: date
    flow_in: bool
    kind_code: str
    kind_ar: str
    party: str
    detail: str
    amount: Decimal
    invoice_pk: Optional[int] = None
    expense_pk: Optional[int] = None
    supplier_payment_pk: Optional[int] = None
    supplier_pk: Optional[int] = None
    customer_pk: Optional[int] = None

    def search_blob(self) -> str:
        parts = [self.kind_ar, self.kind_code, self.method_code, self.party, self.detail]
        return " ".join(p for p in parts if p).lower()


def collect_ledger_rows(*, method: str, date_from: date, date_to: date) -> List[LedgerRow]:
    method = (method or "").strip()
    rows: List[LedgerRow] = []

    inv_qs = (
        InvoicePayment.objects.filter(
            method=method,
            invoice__is_cancelled=False,
            invoice__created_at__date__gte=date_from,
            invoice__created_at__date__lte=date_to,
        )
        .select_related("invoice", "invoice__customer", "invoice__order")
        .order_by("invoice__created_at", "pk")
    )
    for p in inv_qs:
        inv = p.invoice
        cust = inv.customer
        party = cust.name_ar if cust else "—"
        src = (p.payment_source or "").strip()
        src_ar = {"table": "طاولة", "takeaway": "سفري", "delivery": "توصيل"}.get(src, src or "—")
        extra = []
        if (p.payer_name or "").strip():
            extra.append(f"محوّل: {p.payer_name.strip()}")
        if (p.payer_phone or "").strip():
            extra.append(f"جوال: {p.payer_phone.strip()}")
        detail = f"{inv.invoice_number} · {src_ar}"
        if extra:
            detail += " · " + " · ".join(extra)
        rows.append(
            LedgerRow(
                method_code=method,
                sort_at=inv.created_at,
                row_date=inv.created_at.date(),
                flow_in=True,
                kind_code="sale_payment",
                kind_ar="دفعة فاتورة بيع",
                party=party,
                detail=detail,
                amount=p.amount.quantize(Decimal("0.01")),
                invoice_pk=inv.pk,
                customer_pk=cust.pk if cust else None,
            )
        )

    exp_qs = (
        Expense.objects.filter(
            payment_method=method,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .select_related("category")
        .order_by("expense_date", "created_at", "pk")
    )
    for e in exp_qs:
        sort_e = e.created_at or timezone.make_aware(datetime.combine(e.expense_date, datetime.min.time()))
        note = (e.notes or "").strip()
        detail = note[:200] if note else "—"
        rows.append(
            LedgerRow(
                method_code=method,
                sort_at=sort_e,
                row_date=e.expense_date,
                flow_in=False,
                kind_code="expense",
                kind_ar="مصروف",
                party=e.category.name_ar if e.category else "—",
                detail=detail,
                amount=e.amount.quantize(Decimal("0.01")),
                expense_pk=e.pk,
            )
        )

    sup_qs = (
        SupplierPayment.objects.filter(
            method=method,
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .select_related("supplier")
        .order_by("created_at", "pk")
    )
    for sp in sup_qs:
        note = (sp.note or "").strip()
        detail = note[:200] if note else "سداد مورد"
        rows.append(
            LedgerRow(
                method_code=method,
                sort_at=sp.created_at,
                row_date=sp.created_at.date(),
                flow_in=False,
                kind_code="supplier_payment",
                kind_ar="سداد مورد",
                party=sp.supplier.name_ar if sp.supplier else "—",
                detail=detail,
                amount=sp.amount.quantize(Decimal("0.01")),
                supplier_payment_pk=sp.pk,
                supplier_pk=sp.supplier_id,
            )
        )

    # سند قبض عميل (الصندوق الموحّد) — لا يُنشئ InvoicePayment
    log_from = date_from - timedelta(days=14)
    log_to = date_to + timedelta(days=14)
    logs = (
        AuditLog.objects.filter(
            action=TREASURY_VOUCHER_AUDIT_ACTION,
            created_at__date__gte=log_from,
            created_at__date__lte=log_to,
        )
        .order_by("created_at", "pk")
    )
    for log in logs:
        payload = log.payload or {}
        if payload.get("cancelled"):
            continue
        if payload.get("voucher_type") != "receipt" or payload.get("party_type") != "customer":
            continue
        vd = _parse_voucher_date(payload, log.created_at)
        if not (date_from <= vd <= date_to):
            continue
        party = (payload.get("party_label") or "").strip() or "عميل"
        note = (payload.get("note") or "").strip()
        detail = (note[:180] + "…") if len(note) > 180 else (note or "سند قبض")
        splits = payload.get("payment_splits")
        customer_pk = payload.get("customer_pk")
        try:
            customer_pk = int(customer_pk) if customer_pk is not None else None
        except (TypeError, ValueError):
            customer_pk = None

        amounts_for_method: List[Decimal] = []
        if splits and isinstance(splits, list):
            for item in splits:
                if not isinstance(item, dict):
                    continue
                m = (item.get("method") or "").strip()
                if m != method:
                    continue
                amounts_for_method.append(_as_decimal(item.get("amount")).quantize(Decimal("0.01")))
        else:
            m = (payload.get("method") or "").strip()
            if m == method:
                amounts_for_method.append(_as_decimal(payload.get("amount")).quantize(Decimal("0.01")))

        for i, amt in enumerate(amounts_for_method):
            if amt <= 0:
                continue
            sort_at = log.created_at
            if i == 0 and payload.get("voucher_date"):
                try:
                    d = date.fromisoformat(str(payload["voucher_date"])[:10])
                    sort_at = timezone.make_aware(datetime.combine(d, datetime.min.time()))
                except ValueError:
                    pass
            rows.append(
                LedgerRow(
                    method_code=method,
                    sort_at=sort_at,
                    row_date=vd,
                    flow_in=True,
                    kind_code="treasury_customer",
                    kind_ar="سند قبض (صندوق)",
                    party=party,
                    detail=detail if i == 0 else f"{detail} (تقسيم {i + 1})",
                    amount=amt,
                    customer_pk=customer_pk,
                )
            )

    # سند قبض موظف (سداد ذمة) — الصندوق الموحّد (مع تقسيم طرق دفع)
    for log in logs:
        payload = log.payload or {}
        if payload.get("cancelled"):
            continue
        if payload.get("voucher_type") != "receipt" or payload.get("party_type") != "employee":
            continue
        vd = _parse_voucher_date(payload, log.created_at)
        if not (date_from <= vd <= date_to):
            continue
        party = (payload.get("party_label") or "").strip() or "موظف"
        note = (payload.get("note") or "").strip()
        detail = (note[:180] + "…") if len(note) > 180 else (note or "سند قبض موظف")
        splits = payload.get("payment_splits")

        amounts_for_method: List[Decimal] = []
        if splits and isinstance(splits, list):
            for item in splits:
                if not isinstance(item, dict):
                    continue
                m = (item.get("method") or "").strip()
                if m != method:
                    continue
                amounts_for_method.append(_as_decimal(item.get("amount")).quantize(Decimal("0.01")))
        else:
            m = (payload.get("method") or "").strip()
            if m == method:
                amounts_for_method.append(_as_decimal(payload.get("amount")).quantize(Decimal("0.01")))

        for i, amt in enumerate(amounts_for_method):
            if amt <= 0:
                continue
            sort_at = log.created_at
            if i == 0 and payload.get("voucher_date"):
                try:
                    d = date.fromisoformat(str(payload["voucher_date"])[:10])
                    sort_at = timezone.make_aware(datetime.combine(d, datetime.min.time()))
                except ValueError:
                    pass
            rows.append(
                LedgerRow(
                    method_code=method,
                    sort_at=sort_at,
                    row_date=vd,
                    flow_in=True,
                    kind_code="treasury_employee",
                    kind_ar="سند قبض موظف (ذمة)",
                    party=party,
                    detail=detail if i == 0 else f"{detail} (تقسيم {i + 1})",
                    amount=amt,
                )
            )

    return rows


def collect_all_ledger_rows(*, date_from: date, date_to: date) -> List[LedgerRow]:
    rows: List[LedgerRow] = []
    for cfg in load_payment_method_rows():
        code = (cfg.get("code") or "").strip()
        if not code:
            continue
        rows.extend(collect_ledger_rows(method=code, date_from=date_from, date_to=date_to))
    return rows


def summarize_inflows_by_method(rows: List[LedgerRow]) -> List[dict[str, Any]]:
    """إجمالي الوارد فقط لكل طريقة دفع (بعد الفلاتر على قائمة السطور)."""
    from collections import defaultdict

    sums: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for r in rows:
        if not r.flow_in:
            continue
        sums[r.method_code] = sums[r.method_code] + r.amount
    labels = payment_method_label_map()
    out: List[dict[str, Any]] = []
    for code, amt in sums.items():
        amt = amt.quantize(Decimal("0.01"))
        if amt <= 0:
            continue
        out.append({"method": code, "label": labels.get(code, code), "total_in": amt})
    out.sort(key=lambda x: (-x["total_in"], x["label"]))
    return out


LEDGER_KIND_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "كل الأنواع"),
    ("sale_payment", "دفعة فاتورة بيع"),
    ("expense", "مصروف"),
    ("supplier_payment", "سداد مورد"),
    ("treasury_customer", "سند قبض (صندوق)"),
    ("treasury_employee", "سند قبض موظف (ذمة)"),
)

LEDGER_SORT_CHOICES: tuple[tuple[str, str], ...] = (
    ("chrono", "زمني (قديم → جديد)"),
    ("chrono_desc", "زمني (جديد → قديم)"),
    ("amount_desc", "المبلغ الأكبر أولاً"),
    ("amount_asc", "المبلغ الأصغر أولاً"),
    ("kind", "النوع (أ-ي)"),
    ("party", "الجهة (أ-ي)"),
    ("in_first", "الوارد ثم الصادر"),
)


def apply_search(rows: List[LedgerRow], q: str) -> List[LedgerRow]:
    q = (q or "").strip().lower()
    if not q:
        return rows
    return [r for r in rows if q in r.search_blob()]


def apply_kind_filter(rows: List[LedgerRow], kind_code: str) -> List[LedgerRow]:
    k = (kind_code or "").strip()
    if not k:
        return rows
    return [r for r in rows if r.kind_code == k]


def apply_flow_filter(rows: List[LedgerRow], flow: str) -> List[LedgerRow]:
    f = (flow or "all").strip().lower()
    if f == "in":
        return [r for r in rows if r.flow_in]
    if f == "out":
        return [r for r in rows if not r.flow_in]
    return rows


def apply_amount_bounds(rows: List[LedgerRow], min_amt: Optional[Decimal], max_amt: Optional[Decimal]) -> List[LedgerRow]:
    out = rows
    if min_amt is not None:
        out = [r for r in out if r.amount >= min_amt]
    if max_amt is not None:
        out = [r for r in out if r.amount <= max_amt]
    return out


def _row_stable_key(r: LedgerRow) -> tuple:
    return (r.sort_at, r.method_code, r.invoice_pk or 0, r.expense_pk or 0, r.supplier_payment_pk or 0)


def sort_ledger_rows(rows: List[LedgerRow], sort_key: str) -> List[LedgerRow]:
    sk = (sort_key or "chrono").strip().lower()
    if sk not in {c[0] for c in LEDGER_SORT_CHOICES}:
        sk = "chrono"
    if sk == "chrono":
        return sorted(rows, key=_row_stable_key)
    if sk == "chrono_desc":
        return sorted(rows, key=_row_stable_key, reverse=True)
    if sk == "amount_desc":
        return sorted(rows, key=lambda r: (-r.amount, r.sort_at, r.invoice_pk or 0))
    if sk == "amount_asc":
        return sorted(rows, key=lambda r: (r.amount, r.sort_at, r.invoice_pk or 0))
    if sk == "kind":
        return sorted(rows, key=lambda r: (r.kind_ar, r.sort_at))
    if sk == "party":
        return sorted(rows, key=lambda r: (r.party, r.sort_at))
    if sk == "in_first":
        return sorted(rows, key=lambda r: (0 if r.flow_in else 1, r.sort_at, r.invoice_pk or 0))
    return sorted(rows, key=_row_stable_key)


def attach_running_balance(rows: List[LedgerRow]) -> List[dict[str, Any]]:
    bal = Decimal("0")
    out: List[dict[str, Any]] = []
    for r in rows:
        signed = r.amount if r.flow_in else -r.amount
        bal = (bal + signed).quantize(Decimal("0.01"))
        out.append(
            {
                "sort_at": r.sort_at,
                "row_date": r.row_date,
                "method_code": r.method_code,
                "flow_in": r.flow_in,
                "kind_code": r.kind_code,
                "kind_ar": r.kind_ar,
                "party": r.party,
                "detail": r.detail,
                "amount": r.amount,
                "in_display": r.amount if r.flow_in else None,
                "out_display": None if r.flow_in else r.amount,
                "running": bal,
                "invoice_pk": r.invoice_pk,
                "expense_pk": r.expense_pk,
                "supplier_payment_pk": r.supplier_payment_pk,
                "supplier_pk": r.supplier_pk,
                "customer_pk": r.customer_pk,
            }
        )
    return out


def summarize(rows: List[dict[str, Any]]) -> dict[str, Decimal]:
    total_in = sum((r["amount"] for r in rows if r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
    total_out = sum((r["amount"] for r in rows if not r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
    return {
        "total_in": total_in,
        "total_out": total_out,
        "net": (total_in - total_out).quantize(Decimal("0.01")),
    }
