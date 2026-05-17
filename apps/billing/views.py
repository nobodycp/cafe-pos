import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.billing.models import SaleInvoice, SaleReturn, SaleReturnLine
from apps.purchasing.models import PurchaseInvoice
from apps.billing.purge_service import purge_sale_invoice, purge_sale_return
from apps.billing.sale_invoice_edit import (
    apply_sale_invoice_full_edit,
    can_edit_sale_invoice,
    parse_sale_invoice_full_edit_post,
)
from apps.core.models import get_pos_settings
from apps.core.payment_methods import load_payment_method_rows
from apps.core.pagination import paginate_queryset


def _safe_return_path(raw: str) -> str:
    """مسار داخلي آمن للرجوع (يحفظ فلاتر القائمة عند تمرير return=)."""
    path = (raw or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return ""
    if "\n" in path or "\r" in path:
        return ""
    return path


def _invoice_detail_back_context(request, invoice: SaleInvoice) -> dict:
    """زر الرجوع في تفاصيل الفاتورة حسب مصدر الدخول."""
    return_path = _safe_return_path(request.GET.get("return", ""))
    if return_path:
        return {
            "toolbar_back_url": return_path,
            "toolbar_back_label": "← رجوع",
            "toolbar_back_title": "الصفحة السابقة",
        }

    from_key = (request.GET.get("from") or "").strip().lower()
    if from_key == "pos":
        return {
            "toolbar_back_url": reverse("pos:main"),
            "toolbar_back_label": "← لوحة الطلبات",
            "toolbar_back_title": "لوحة الطلبات",
        }
    if from_key == "customer" and invoice.customer_id:
        return {
            "toolbar_back_url": reverse("shell:customer_detail", args=[invoice.customer_id]),
            "toolbar_back_label": "← العميل",
            "toolbar_back_title": "بطاقة العميل",
        }
    if from_key == "customer_invoices" and invoice.customer_id:
        return {
            "toolbar_back_url": reverse("shell:customer_invoices", args=[invoice.customer_id]),
            "toolbar_back_label": "← فواتير العميل",
            "toolbar_back_title": "فواتير العميل",
        }
    if from_key == "reports":
        return {
            "toolbar_back_url": reverse("shell:reports"),
            "toolbar_back_label": "← التقارير",
            "toolbar_back_title": "التقارير",
        }
    if from_key == "supplier" and getattr(invoice, "supplier_buyer_id", None):
        return {
            "toolbar_back_url": reverse("shell:supplier_detail", args=[invoice.supplier_buyer_id]),
            "toolbar_back_label": "← المورد",
            "toolbar_back_title": "بطاقة المورد",
        }

    return {
        "toolbar_back_url": reverse("shell:invoice_list"),
        "toolbar_back_label": "← الفواتير",
        "toolbar_back_title": "أرشيف الفواتير",
    }


def _redirect_sale_invoice_detail(request, pk: int, *, edit: bool = False):
    """إعادة توجيه لتفاصيل الفاتورة مع الحفاظ على from/return."""
    q = request.GET.copy()
    if edit:
        q["edit"] = "1"
    if "from" not in q and not _safe_return_path(q.get("return", "")):
        q["from"] = "invoices"
    url = reverse("shell:invoice_detail", kwargs={"pk": pk})
    if q:
        url = f"{url}?{q.urlencode()}"
    return redirect(url)


def _invoice_list_parse_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


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
    if code == "NO_LINES":
        return "أضف صنفاً واحداً على الأقل بكمية وسعر."
    if code == "INVALID_LINE_PRODUCT":
        return "أحد الأصناف غير صالح أو غير مفعّل."
    if code == "ZERO_QTY_NOT_ALLOWED":
        return "الكمية يجب أن تكون أكبر من صفر لكل سطر."
    if code == "CREDIT_REQUIRES_CUSTOMER":
        return "الدفع الآجل يتطلب عميلاً موجوداً — ابحث عنه أو اكتب اسمه."
    if code == "PAYER_DETAILS_REQUIRED":
        return "أدخل اسم المحوّل ورقم الجوال (للتتبع) مع بنك فلسطين / بال باي / جوال باي."
    if code == "INVALID_PAYMENT_SPLITS":
        return "بيانات الدفع المختلط غير صالحة — راجع الأسطر والمبالغ."
    if code == "INVALID_PAYMENT_METHOD":
        return "طريقة دفع غير معروفة — اختر من القائمة."
    if code.startswith("PAYMENT_SUM_MISMATCH:"):
        parts = code.split(":")
        return f"مجموع الدفعات ({parts[1] if len(parts) > 1 else '?'}) لا يساوي الإجمالي ({parts[2] if len(parts) > 2 else '?'})."
    return code.replace("_", " ")


def _sale_edit_pay_boot(invoice, payments) -> dict:
    """بيانات قسم الدفع — تُقرأ من JSON في الصفحة (تعمل بعد حقن AJAX للنافذة)."""
    rows = load_payment_method_rows()
    return {
        "customersSearchUrl": reverse("pos:customers_search"),
        "customerCreateUrl": reverse("pos:customer_quick_create"),
        "payerHintsUrl": reverse("pos:payer_hints"),
        "pmRows": [
            {
                "code": str(r.get("code") or "").strip().lower(),
                "label_ar": str(r.get("label_ar") or ""),
                "ledger": str(r.get("ledger") or ""),
                "needsPayer": str(r.get("ledger") or "") == "bank",
            }
            for r in rows
        ],
        "payInit": {
            "currency": get_pos_settings().currency_symbol or "",
            "total": f"{invoice.total:.2f}",
            "customerId": str(invoice.customer_id or ""),
            "customerName": (invoice.customer.name_ar if invoice.customer else "") or "",
            "payments": [
                {
                    "method": p.method,
                    "amount": f"{p.amount:.2f}",
                    "payer_name": p.payer_name or "",
                    "payer_phone": p.payer_phone or "",
                }
                for p in payments
            ],
        },
    }


def _sale_invoice_edit_form_context(invoice, lines, payments, can_edit, reason, has_returns):
    slots = len(lines) + 2
    line_edit_rows = [{"idx": i, "line": lines[i] if i < len(lines) else None} for i in range(slots)]
    return {
        "invoice": invoice,
        "lines": lines,
        "payments": payments,
        "can_edit_sale_invoice": can_edit,
        "cannot_edit_reason": reason,
        "has_sale_returns": has_returns,
        "pos_allows_edit": get_pos_settings().allow_sale_invoice_edit,
        "payment_method_rows": load_payment_method_rows(),
        "line_edit_rows": line_edit_rows,
        "pos_products_search_url": reverse("pos:products_search"),
        "sale_edit_pay_boot": _sale_edit_pay_boot(invoice, payments),
        "sale_edit_pay_boot_json": json.dumps(
            _sale_edit_pay_boot(invoice, payments), ensure_ascii=False
        ),
    }


def _invoice_list_kind(request) -> str:
    raw = (request.GET.get("invoice_kind") or "all").strip().lower()
    if raw in ("sale", "purchase"):
        return raw
    return "all"


@login_required
def sale_invoice_list(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status")
    date_from = _invoice_list_parse_date(request.GET.get("date_from", ""))
    date_to = _invoice_list_parse_date(request.GET.get("date_to", ""))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    sale_qs = SaleInvoice.objects.select_related(
        "customer", "order__table_session__dining_table", "work_session",
    ).order_by("-created_at")
    if q:
        sale_qs = sale_qs.filter(
            Q(invoice_number__icontains=q)
            | Q(customer__name_ar__icontains=q)
            | Q(customer__name_en__icontains=q)
        )
    if status in ("active", "cancelled"):
        sale_qs = sale_qs.filter(is_cancelled=(status == "cancelled"))
    if date_from:
        sale_qs = sale_qs.filter(created_at__date__gte=date_from)
    if date_to:
        sale_qs = sale_qs.filter(created_at__date__lte=date_to)

    purchase_qs = PurchaseInvoice.objects.select_related("supplier", "work_session").order_by("-created_at")
    if q:
        purchase_qs = purchase_qs.filter(
            Q(invoice_number__icontains=q)
            | Q(supplier__name_ar__icontains=q)
            | Q(supplier__name_en__icontains=q)
        )
    if status in ("active", "cancelled"):
        purchase_qs = purchase_qs.filter(is_cancelled=(status == "cancelled"))
    if date_from:
        purchase_qs = purchase_qs.filter(created_at__date__gte=date_from)
    if date_to:
        purchase_qs = purchase_qs.filter(created_at__date__lte=date_to)

    kind = _invoice_list_kind(request)

    sale_sum = Decimal("0")
    purchase_sum = Decimal("0")
    if kind in ("all", "sale"):
        agg = sale_qs.aggregate(s=Sum("total"))
        sale_sum = (agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
    if kind in ("all", "purchase"):
        agg = purchase_qs.aggregate(s=Sum("total"))
        purchase_sum = (agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
    if kind == "all":
        sum_total = (sale_sum + purchase_sum).quantize(Decimal("0.01"))
    elif kind == "sale":
        sum_total = sale_sum
    else:
        sum_total = purchase_sum

    merged_rows = []
    if kind in ("all", "sale"):
        for inv in sale_qs:
            merged_rows.append({"kind": "sale", "obj": inv})
    if kind in ("all", "purchase"):
        for pinv in purchase_qs:
            merged_rows.append({"kind": "purchase", "obj": pinv})
    merged_rows.sort(key=lambda r: r["obj"].created_at, reverse=True)

    invoice_totals = {"sum_total": sum_total}

    ctx = {
        "q": q,
        "status": status or "",
        "invoice_kind": kind,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "invoice_totals": invoice_totals,
    }
    ctx.update(paginate_queryset(request, merged_rows))
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
    ctx = {
        "invoice": invoice,
        "lines": lines,
        "payments": payments,
        "has_sale_returns": invoice.returns.exists(),
        "sale_returns": list(invoice.returns.order_by("-created_at")),
    }
    ctx.update(_invoice_detail_back_context(request, invoice))
    return render(request, "shell/invoice_detail.html", ctx)


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
        "shell/_sale_invoice_edit_modal_fragment.html",
        _sale_invoice_edit_form_context(invoice, lines, payments, can_edit, reason, has_returns),
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
            return _redirect_sale_invoice_detail(request, invoice.pk)
        try:
            lr, pr = parse_sale_invoice_full_edit_post(request.POST)
            apply_sale_invoice_full_edit(
                invoice=invoice,
                user=request.user,
                line_rows=lr,
                payment_rows=pr,
                post=request.POST,
            )
        except ValueError as e:
            code = str(e)
            err_msg = _sale_invoice_edit_error_message(code)
            if is_pos_embed:
                return JsonResponse({"ok": False, "error": err_msg}, status=400)
            messages.error(request, err_msg)
            return _redirect_sale_invoice_detail(request, invoice.pk, edit=True)
        if is_pos_embed:
            return JsonResponse({"ok": True})
        messages.success(request, "تم حفظ تعديل الفاتورة (الأصناف والدفعات والقيود).")
        return _redirect_sale_invoice_detail(request, invoice.pk)

    return _redirect_sale_invoice_detail(request, invoice.pk, edit=True)


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


@login_required
@user_passes_test(lambda u: u.is_staff)
@require_POST
def sale_return_delete(request, invoice_pk, return_pk):
    invoice = get_object_or_404(SaleInvoice, pk=invoice_pk)
    ret = get_object_or_404(SaleReturn, pk=return_pk, invoice=invoice)
    reason = (request.POST.get("reason") or "").strip()
    fallback = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse(
        "shell:invoice_detail", kwargs={"pk": invoice.pk}
    )
    if len(reason) < 3:
        messages.error(request, "اكتب سبب حذف المرتجع (3 أحرف على الأقل).")
        return redirect(fallback)
    ret_label = ret.return_number
    try:
        purge_sale_return(sale_return=ret, reason=reason, user=request.user)
    except ValueError as e:
        code = str(e)
        if code == "PRODUCT_NOT_STOCK_TRACKED" or code.startswith("INSUFFICIENT_STOCK"):
            messages.error(
                request,
                "تعذر عكس المخزون أثناء حذف المرتجع. راجع الكميات أو فعّل السماح بالمخزون السالب مؤقتاً من الإعدادات.",
            )
        else:
            messages.error(request, code.replace("_", " "))
        return redirect(fallback)
    messages.success(
        request,
        f"تم حذف المرتجع {ret_label} وعكس أثره (مخزون / رصيد عميل إن وُجد). يمكنك الآن حذف الفاتورة إن رغبت.",
    )
    return redirect(reverse("shell:invoice_detail", kwargs={"pk": invoice.pk}))
