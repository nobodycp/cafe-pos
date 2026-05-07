"""
ESC/POS — نص إيصال بنفس ترتيب مرجع «باندا مول» (بدون رسومات معقدة).
المحتوى UTF-8؛ اضبط الطابعة على UTF-8 أو العربية حسب الجهاز.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from apps.billing.models import SaleInvoice

ESC = "\x1b"
GS = "\x1d"


def _b(s: str) -> bytes:
    return s.encode("ascii", errors="ignore")


def _pay_breakdown(inv: "SaleInvoice") -> dict[str, Decimal]:
    b = {k: Decimal("0") for k in ("cash", "bank_ps", "palpay", "jawwalpay", "credit")}
    for p in inv.payments.order_by("pk"):
        m = (p.method or "").strip()
        a = p.amount or Decimal("0")
        if m == "cash":
            b["cash"] += a
        elif m in ("bank_ps", "bank"):
            b["bank_ps"] += a
        elif m == "palpay":
            b["palpay"] += a
        elif m == "jawwalpay":
            b["jawwalpay"] += a
        elif m == "credit":
            b["credit"] += a
        else:
            b["bank_ps"] += a
    return {k: v.quantize(Decimal("0.01")) for k, v in b.items()}


def _payer_line(inv: "SaleInvoice") -> str:
    if inv.customer_id:
        return inv.customer.name_ar
    for p in inv.payments.order_by("pk"):
        if p.method in ("bank_ps", "palpay", "jawwalpay") and (p.payer_name or "").strip():
            return (p.payer_name or "").strip()
    return "—"


def _order_src(inv: "SaleInvoice") -> str:
    o = inv.order
    bits = [o.get_order_type_display()]
    if o.table_id:
        bits.append(o.table.name_ar)
    return " - ".join(bits)


def _sale_terminal_or_order(inv: "SaleInvoice") -> str:
    from apps.core.models import get_pos_settings

    t = (get_pos_settings().printer_receipt_label or "").strip()
    return t if t else _order_src(inv)


def _time_ar_ampm(inv: "SaleInvoice") -> str:
    from django.utils import timezone

    dt = inv.created_at
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    h, m, s = dt.hour, dt.minute, dt.second
    ap = "م" if h >= 12 else "ص"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12:02d}:{m:02d}:{s:02d} {ap}"


def _cashier(inv: "SaleInvoice") -> str:
    try:
        u = inv.work_session.opened_by
        return (u.get_full_name() or getattr(u, "username", "") or "").strip() or "—"
    except Exception:
        return "—"


def build_invoice_receipt(invoice: "SaleInvoice", cafe_name: str) -> bytes:
    """نص خام للطابعة الحرارية — نفس أقسام الإيصال المرجعي."""
    out = bytearray()
    out.extend(_b(ESC + "@"))
    out.extend(_b(ESC + "a" + chr(1)))
    out.extend(_b(ESC + "E" + chr(1)))
    out.extend((cafe_name + "\n").encode("utf-8"))
    out.extend(_b(ESC + "E" + chr(0)))
    out.extend(_b(ESC + "a" + chr(0)))

    pay = _pay_breakdown(invoice)
    elec = (pay["bank_ps"] + pay["palpay"] + pay["jawwalpay"]).quantize(Decimal("0.01"))
    paid_all = sum(pay.values(), Decimal("0")).quantize(Decimal("0.01"))
    nlines = invoice.lines.count()

    lines: List[str] = [
        "=" * 32,
        f"التاريخ: {invoice.created_at:%d/%m/%Y}  الوقت: {_time_ar_ampm(invoice)}",
        f"رقم: {invoice.pk}  الأصل:   بيع: {_sale_terminal_or_order(invoice)}",
        "-" * 32,
        f"الإسم: {_payer_line(invoice)}",
        "-" * 32,
        "م | إسم الصنف | كمية | سعر | اجمالي",
    ]
    for i, li in enumerate(invoice.lines.select_related("product"), start=1):
        lines.append(
            f"{i} | {li.product.name_ar[:18]} | {li.quantity} | {li.unit_price} | {li.line_subtotal}"
        )
    lines.append("-" * 32)
    lines.append(f"كاشير: {_cashier(invoice)}  |  عدد الأصناف: {nlines}")
    lines.append(f"المجموع: {invoice.subtotal}")
    disc = invoice.discount_total
    if disc > 0:
        dq = disc.quantize(Decimal("0.01"))
        lines.append(f"مبلغ الخصم: {dq}-")
    else:
        lines.append("مبلغ الخصم: 0.00")
    lines.append(f"المبلغ الإجمالي: {invoice.total}")
    lines.append(f"المدفوع: {paid_all if paid_all > 0 else '—'}")
    card_line = elec if elec > 0 else pay["credit"] if pay["credit"] > 0 else "—"
    lines.append(f"بطاقة الائتمان: {card_line}")
    lines.append("المبلغ المتبقي: —")
    lines.append(f"نقدا ومرجع: {pay['cash'] if pay['cash'] > 0 else '—'}")
    if pay["credit"] > 0:
        lines.append(f"آجل: {pay['credit']}")
    lines.extend(["", "—", ""])
    out.extend("\n".join(lines).encode("utf-8"))
    out.extend(b"\n\n")
    out.extend(_b(GS + "V" + chr(65) + chr(0)))
    return bytes(out)
