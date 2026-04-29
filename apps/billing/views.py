from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.billing.models import SaleInvoice, SaleReturn, SaleReturnLine
from apps.billing.purge_service import purge_sale_invoice


@login_required
def sale_invoice_list(request):
    qs = SaleInvoice.objects.select_related(
        "customer", "order__table_session__dining_table", "work_session",
    ).order_by("-created_at")

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(customer__name_ar__icontains=q)
            | Q(customer__name_en__icontains=q)
        )

    status = request.GET.get("status")
    if status in ("active", "cancelled"):
        qs = qs.filter(is_cancelled=(status == "cancelled"))

    invoices = qs[:200]
    return render(request, "shell/invoice_list.html", {
        "invoices": invoices,
        "q": q,
        "status": status or "",
    })


@login_required
@require_POST
def sale_invoice_delete(request, pk):
    invoice = get_object_or_404(SaleInvoice.objects.select_related("customer", "order"), pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    fallback = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("shell:invoice_list")
    if not reason:
        messages.error(request, "اكتب سبب الحذف (مطلوب).")
        return redirect(fallback)
    inv_num = invoice.invoice_number
    success_next = (request.POST.get("next_success") or "").strip()
    try:
        purge_sale_invoice(invoice=invoice, reason=reason, user=request.user)
    except ValueError as e:
        code = str(e)
        if code == "INVOICE_HAS_RETURNS":
            messages.error(
                request,
                "لا يمكن حذف فاتورة عليها مرتجع. احذف المرتجع أولاً أو راجع المحاسب.",
            )
        elif code == "ALREADY_CANCELLED":
            messages.error(request, "تعارض حالة الفاتورة — أعد المحاولة.")
        elif code == "PRODUCT_NOT_STOCK_TRACKED":
            messages.error(request, "تعذر تعديل المخزون أثناء الحذف.")
        else:
            messages.error(request, code)
        return redirect(fallback)
    messages.success(request, f"تم حذف الفاتورة {inv_num} نهائياً من النظام (مع عكس المخزون والقيود).")
    if success_next.startswith("/") and not success_next.startswith("//") and "\n" not in success_next and "\r" not in success_next:
        return redirect(success_next)
    return redirect("shell:invoice_list")


@login_required
def sale_invoice_detail(request, pk):
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related(
            "customer", "supplier_buyer",
            "order__table_session__dining_table", "work_session",
        ),
        pk=pk,
    )
    lines = invoice.lines.select_related("product").order_by("pk")
    payments = invoice.payments.all()
    return render(request, "shell/invoice_detail.html", {
        "invoice": invoice,
        "lines": lines,
        "payments": payments,
    })


@login_required
def customer_invoices(request, customer_id):
    from apps.contacts.models import Customer
    customer = get_object_or_404(Customer, pk=customer_id)
    invoices = SaleInvoice.objects.filter(
        customer=customer,
    ).select_related(
        "order__table_session__dining_table", "work_session",
    ).order_by("-created_at")[:200]
    return render(request, "shell/customer_invoices.html", {
        "customer": customer,
        "invoices": invoices,
    })


@login_required
def sale_return_create(request, invoice_pk):
    from decimal import Decimal, InvalidOperation
    from django.db import transaction
    from apps.core.sequences import next_int
    from apps.core.models import log_audit
    from apps.core.services import SessionService
    from apps.inventory.services import adjust_stock
    from apps.inventory.models import StockMovement

    invoice = get_object_or_404(SaleInvoice.objects.prefetch_related("lines__product"), pk=invoice_pk)
    if invoice.is_cancelled:
        from django.contrib import messages
        messages.error(request, "لا يمكن إرجاع فاتورة ملغاة")
        return redirect("shell:invoice_detail", pk=invoice.pk)

    inv_lines = invoice.lines.select_related("product").order_by("pk")
    errors = []

    if request.method == "POST":
        reason = request.POST.get("reason", "")
        refund_method = request.POST.get("refund_method", "cash")
        return_lines = []

        for line in inv_lines:
            qty_str = request.POST.get(f"qty_{line.pk}", "").strip()
            if not qty_str:
                continue
            try:
                qty = Decimal(qty_str)
                if qty <= 0:
                    continue
                if qty > line.quantity:
                    errors.append(f"الكمية المرتجعة لـ {line.product.name_ar} أكبر من المباعة")
                    continue
                return_lines.append((line, qty))
            except (InvalidOperation, ValueError):
                errors.append(f"كمية غير صالحة لـ {line.product.name_ar}")

        if not return_lines and not errors:
            errors.append("يرجى تحديد كمية مرتجعة واحدة على الأقل")

        if not errors:
            with transaction.atomic():
                total_refund = Decimal("0")
                ret = SaleReturn.objects.create(
                    invoice=invoice,
                    return_number=f"RET-{next_int('sale_return'):06d}",
                    reason=reason,
                    refund_method=refund_method,
                )

                session = SessionService.get_open_session()
                for inv_line, qty in return_lines:
                    line_total = (qty * inv_line.unit_price).quantize(Decimal("0.01"))
                    SaleReturnLine.objects.create(
                        sale_return=ret,
                        product=inv_line.product,
                        quantity=qty,
                        unit_price=inv_line.unit_price,
                        line_total=line_total,
                    )
                    total_refund += line_total

                    if inv_line.product.is_stock_tracked:
                        adjust_stock(
                            product=inv_line.product,
                            quantity_delta=qty,
                            movement_type=StockMovement.MovementType.ADJUSTMENT,
                            session=session,
                            reference_model="billing.SaleReturn",
                            reference_pk=str(ret.pk),
                            note=f"مرتجع بيع {ret.return_number}",
                        )

                ret.total_refund = total_refund
                ret.save(update_fields=["total_refund", "updated_at"])

                if refund_method == "credit" and invoice.customer:
                    cust = invoice.customer
                    cust.balance = (cust.balance - total_refund).quantize(Decimal("0.01"))
                    cust.save(update_fields=["balance", "updated_at"])
                    from apps.contacts.models import CustomerLedgerEntry
                    CustomerLedgerEntry.objects.create(
                        customer=cust,
                        entry_type=CustomerLedgerEntry.EntryType.ADJUSTMENT,
                        amount=-total_refund,
                        note=f"مرتجع بيع {ret.return_number}",
                        reference_model="billing.SaleReturn",
                        reference_pk=str(ret.pk),
                    )

                log_audit(request.user, "sale.return.created", "billing.SaleReturn", ret.pk, {"total": str(total_refund)})

                from django.contrib import messages
                messages.success(request, f"تم تسجيل مرتجع {ret.return_number} بمبلغ {total_refund}")
                return redirect("shell:invoice_detail", pk=invoice.pk)

    return render(request, "billing/sale_return_form.html", {
        "invoice": invoice,
        "inv_lines": inv_lines,
        "errors": errors,
    })
