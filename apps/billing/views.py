from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.billing.models import SaleInvoice, SaleReturn, SaleReturnLine
from apps.billing.purge_service import purge_sale_invoice
from apps.billing.sale_invoice_edit import apply_sale_invoice_line_edits, can_edit_sale_invoice
from apps.core.models import get_pos_settings
from apps.core.pagination import paginate_queryset


def _sale_invoice_edit_error_message(code: str) -> str:
    if code.startswith("PAYMENT_MISMATCH:"):
        parts = code.split(":")
        pay_s = parts[1] if len(parts) > 1 else "?"
        tot_s = parts[2] if len(parts) > 2 else "?"
        return (
            "مجموع الدفعات لا يطابق الإجمالي الجديد. استخدم دفعة واحدة (غير الآجل) أو عدّل الدفعات يدوياً. "
            f"(دفعات: {pay_s} — إجمالي: {tot_s})"
        )
    if code == "CREDIT_PAYMENTS_NO_EDIT":
        return "لا يمكن تعديل فاتورة فيها دفع آجل من هنا."
    if code == "MISSING_FIELDS":
        return "أكمل الكمية والسعر لكل سطر."
    if code == "BAD_NUMBER":
        return "تأكد من إدخال أرقام صحيحة للكمية والسعر."
    if code == "NO_PAYMENTS_ON_INVOICE":
        return "لا توجد دفعات مسجّلة على هذه الفاتورة — لا يمكن المتابعة."
    if code.startswith("INSUFFICIENT_STOCK"):
        return "المخزون غير كافٍ لهذا التعديل."
    if code == "INVALID_TOTALS":
        return "مجاميع غير صالحة بعد التعديل."
    return code.replace("_", " ")


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

    ctx = {
        "q": q,
        "status": status or "",
    }
    ctx.update(paginate_queryset(request, qs))
    return render(request, "shell/invoice_list.html", ctx)


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
        "has_sale_returns": invoice.returns.exists(),
    })


@login_required
@require_GET
def sale_invoice_edit_panel(request, pk):
    """HTML جزئي لتعديل الفاتورة داخل طبقة الكاشير (GET فقط)."""
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related(
            "customer", "supplier_buyer",
            "order__table_session__dining_table", "work_session",
        ),
        pk=pk,
    )
    lines = list(invoice.lines.select_related("product").order_by("pk"))
    payments = list(invoice.payments.all())
    can_edit, reason = can_edit_sale_invoice(invoice)
    has_returns = invoice.returns.exists()
    return render(
        request,
        "shell/_sale_invoice_edit_fragment.html",
        {
            "invoice": invoice,
            "lines": lines,
            "payments": payments,
            "can_edit_sale_invoice": can_edit,
            "cannot_edit_reason": reason,
            "has_sale_returns": has_returns,
            "pos_allows_edit": get_pos_settings().allow_sale_invoice_edit,
        },
    )


@login_required
def sale_invoice_edit(request, pk):
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related(
            "customer", "supplier_buyer",
            "order__table_session__dining_table", "work_session",
        ),
        pk=pk,
    )
    lines = list(invoice.lines.select_related("product").order_by("pk"))
    payments = list(invoice.payments.all())
    can_edit, reason = can_edit_sale_invoice(invoice)
    has_returns = invoice.returns.exists()
    is_pos_embed = request.method == "POST" and request.POST.get("pos_embed") == "1"

    if request.method == "POST":
        if not can_edit or has_returns:
            msg = reason or "لا يمكن تعديل هذه الفاتورة."
            if is_pos_embed:
                return JsonResponse({"ok": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("shell:invoice_detail", pk=invoice.pk)
        rows = []
        try:
            for ln in lines:
                qkey = f"qty_{ln.pk}"
                pkey = f"price_{ln.pk}"
                rq = (request.POST.get(qkey) or "").strip()
                rp = (request.POST.get(pkey) or "").strip()
                if not rq or not rp:
                    raise ValueError("MISSING_FIELDS")
                try:
                    rows.append((ln.pk, Decimal(rq.replace(",", ".")), Decimal(rp.replace(",", "."))))
                except Exception:
                    raise ValueError("BAD_NUMBER") from None
            apply_sale_invoice_line_edits(invoice=invoice, user=request.user, rows=rows)
        except ValueError as e:
            code = str(e)
            err_msg = _sale_invoice_edit_error_message(code)
            if is_pos_embed:
                return JsonResponse({"ok": False, "error": err_msg}, status=400)
            messages.error(request, err_msg)
            return redirect("shell:sale_invoice_edit", pk=invoice.pk)
        if is_pos_embed:
            return JsonResponse({"ok": True})
        messages.success(request, "تم حفظ تعديلات الفاتورة.")
        return redirect("shell:invoice_detail", pk=invoice.pk)

    return render(
        request,
        "shell/sale_invoice_edit.html",
        {
            "invoice": invoice,
            "lines": lines,
            "payments": payments,
            "can_edit_sale_invoice": can_edit,
            "cannot_edit_reason": reason,
            "has_sale_returns": has_returns,
            "pos_allows_edit": get_pos_settings().allow_sale_invoice_edit,
        },
    )


@login_required
def customer_invoices(request, customer_id):
    from apps.contacts.models import Customer
    customer = get_object_or_404(Customer, pk=customer_id)
    inv_qs = SaleInvoice.objects.filter(
        customer=customer,
    ).select_related(
        "order__table_session__dining_table", "work_session",
    ).order_by("-created_at")
    ctx = {"customer": customer}
    ctx.update(paginate_queryset(request, inv_qs))
    return render(request, "shell/customer_invoices.html", ctx)


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
