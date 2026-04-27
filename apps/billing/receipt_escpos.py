"""
ESC/POS thermal receipt bytes. Content is UTF-8; set your printer/driver code page to UTF-8 or Arabic as supported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from apps.billing.models import SaleInvoice

ESC = "\x1b"
GS = "\x1d"


def _b(s: str) -> bytes:
    return s.encode("ascii", errors="ignore")


def build_invoice_receipt(invoice: "SaleInvoice", cafe_name: str) -> bytes:
    out = bytearray()
    out.extend(_b(ESC + "@"))  # init
    out.extend(_b(ESC + "a" + chr(1)))  # center
    out.extend(_b(ESC + "E" + chr(1)))  # bold on
    out.extend((cafe_name + "\n").encode("utf-8"))
    out.extend(_b(ESC + "E" + chr(0)))
    out.extend(_b(ESC + "a" + chr(0)))  # left
    lines: List[str] = [
        "=" * 32,
        f"فاتورة: {invoice.invoice_number}",
        f"النوع: {invoice.order.get_order_type_display()}",
    ]
    if invoice.order.table:
        lines.append(f"الطاولة: {invoice.order.table.name_ar}")
    lines.append("-" * 32)
    for li in invoice.lines.select_related("product"):
        lines.append(li.product.name_ar)
        lines.append(f"  {li.quantity} × {li.unit_price} = {li.line_subtotal}")
    lines.append("-" * 32)
    lines.append(f"الإجمالي: {invoice.total}")
    pays = list(invoice.payments.all())
    if pays:
        lines.append("الدفع:")
        for p in pays:
            lines.append(f"  {p.get_method_display()}: {p.amount}")
    lines.extend(["", "شكراً لزيارتكم", ""])
    out.extend("\n".join(lines).encode("utf-8"))
    out.extend(b"\n\n")
    out.extend(_b(GS + "V" + chr(65) + chr(0)))  # partial cut
    return bytes(out)
