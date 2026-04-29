import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.catalog.models import Product, Unit
from apps.core.models import log_audit
from apps.core.payment_methods import credit_method_codes, load_payment_method_rows
from apps.purchasing.forms import SupplierForm, SupplierPaymentForm
from apps.purchasing.models import (
    PurchaseInvoice,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    SupplierLedgerEntry,
    SupplierPayment,
)
from apps.purchasing.purge_service import purge_purchase_invoice
from apps.purchasing.routing import purchasing_url_namespace
from apps.purchasing.services import post_purchase_invoice, record_supplier_payment
from apps.billing.models import SaleInvoiceLine


def _payment_rows():
    return load_payment_method_rows()


def _purchase_payments_from_request(request, total: Decimal, errors: list) -> list[tuple[str, Decimal]]:
    pay_method = (request.POST.get("pay_method") or "").strip()
    pay_amount_str = request.POST.get("pay_amount", "0")
    codes = {row["code"] for row in _payment_rows()}
    credit_codes = credit_method_codes()

    if pay_method not in codes:
        errors.append("اختر طريقة دفع صالحة.")
        return []

    try:
        pay_amount = Decimal(str(pay_amount_str or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        errors.append("المبلغ المدفوع غير صالح.")
        return []

    if pay_amount < 0:
        errors.append("المبلغ المدفوع لا يمكن أن يكون سالباً.")
        return []

    if pay_method in credit_codes:
        return [(pay_method, total)]

    if pay_amount <= 0:
        errors.append("أدخل المبلغ المدفوع أو اختر طريقة دفع آجلة.")
        return []
    if pay_amount > total:
        errors.append("المبلغ المدفوع أكبر من صافي الفاتورة.")
        return []

    payments = [(pay_method, pay_amount)]
    credit = (total - pay_amount).quantize(Decimal("0.01"))
    if credit > 0:
        credit_code = next(iter(credit_codes), "credit")
        payments.append((credit_code, credit))
    return payments


def _purchase_lines_from_request(request, errors: list) -> list:
    lines = []
    for i in range(20):
        prod_id = request.POST.get(f"product_{i}")
        qty = request.POST.get(f"qty_{i}")
        cost = request.POST.get(f"cost_{i}")
        discount = request.POST.get(f"discount_{i}", "0")
        if prod_id and qty and cost:
            try:
                product = Product.objects.get(pk=int(prod_id))
                unit_id = request.POST.get(f"unit_{i}")
                if unit_id:
                    try:
                        unit = Unit.objects.get(pk=int(unit_id))
                        if product.unit_id != unit.pk:
                            product.unit = unit
                            product.save(update_fields=["unit", "updated_at"])
                    except (Unit.DoesNotExist, ValueError):
                        errors.append(f"سطر {i + 1}: وحدة غير صالحة")
                        continue
                q = Decimal(qty)
                c = Decimal(cost)
                d = Decimal(str(discount or "0"))
                if q <= 0 or c <= 0:
                    errors.append(f"سطر {i + 1}: الكمية وسعر الوحدة يجب أن يكونا أكبر من صفر")
                    continue
                if d < 0:
                    errors.append(f"سطر {i + 1}: الخصم لا يمكن أن يكون سالباً")
                    continue
                line_total = (q * c).quantize(Decimal("0.01"))
                if d > line_total:
                    errors.append(f"سطر {i + 1}: الخصم أكبر من إجمالي السطر")
                    continue
                effective_cost = ((line_total - d) / q).quantize(Decimal("0.000001"))
                lines.append((product, q, effective_cost))
            except (Product.DoesNotExist, InvalidOperation, ValueError):
                errors.append(f"سطر {i + 1}: بيانات غير صالحة")
    if not lines:
        errors.append("يرجى إدخال صنف واحد على الأقل")
    return lines


def _apply_general_discount(request, lines: list, errors: list) -> list:
    raw = request.POST.get("general_discount", "0")
    try:
        discount = Decimal(str(raw or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        errors.append("الخصم العام غير صالح.")
        return lines
    if discount <= 0:
        return lines
    total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
    if discount > total:
        errors.append("الخصم العام أكبر من إجمالي الفاتورة.")
        return lines
    remaining = discount
    adjusted = []
    for idx, (product, qty, cost) in enumerate(lines):
        line_total = (qty * cost).quantize(Decimal("0.01"))
        if idx == len(lines) - 1:
            line_discount = remaining
        else:
            line_discount = ((line_total / total) * discount).quantize(Decimal("0.01"))
            remaining -= line_discount
        net_total = max(line_total - line_discount, Decimal("0"))
        adjusted.append((product, qty, (net_total / qty).quantize(Decimal("0.000001"))))
    return adjusted


def _purchase_form_state(request):
    if request.method != "POST":
        return {}

    supplier_id = request.POST.get("supplier_id") or ""
    supplier_label = ""
    if supplier_id:
        try:
            supplier_label = Supplier.objects.get(pk=int(supplier_id)).name_ar
        except (Supplier.DoesNotExist, ValueError):
            supplier_label = request.POST.get("supplier_label", "")

    rows = []
    for i in range(20):
        product_id = request.POST.get(f"product_{i}") or ""
        unit_id = request.POST.get(f"unit_{i}") or ""
        product_label = ""
        unit_label = ""
        if product_id:
            try:
                product_label = Product.objects.get(pk=int(product_id)).name_ar
            except (Product.DoesNotExist, ValueError):
                product_label = ""
        if unit_id:
            try:
                unit_label = Unit.objects.get(pk=int(unit_id)).name_ar
            except (Unit.DoesNotExist, ValueError):
                unit_label = ""
        rows.append({
            "product_id": product_id,
            "product_label": product_label,
            "unit_id": unit_id,
            "unit_label": unit_label,
            "qty": request.POST.get(f"qty_{i}") or "",
            "cost": request.POST.get(f"cost_{i}") or "",
            "discount": request.POST.get(f"discount_{i}") or "",
        })

    return {
        "supplier_id": supplier_id,
        "supplier_label": supplier_label,
        "rows": rows,
        "general_discount": request.POST.get("general_discount") or "0.00",
        "pay_method": request.POST.get("pay_method") or "",
        "pay_amount": request.POST.get("pay_amount") or "",
    }


def _purchasing_ctx(request, **kwargs):
    ctx = {"purchasing_ns": purchasing_url_namespace(request)}
    ctx.update(kwargs)
    return ctx


def _purchasing_reverse(request, viewname, *args, **kwargs):
    ns = purchasing_url_namespace(request)
    return reverse(f"{ns}:{viewname}", args=args, kwargs=kwargs)


def _purchasing_redirect(request, viewname, *args, **kwargs):
    return redirect(_purchasing_reverse(request, viewname, *args, **kwargs))


def _purchasing_tpl(request, shell_tpl, classic_tpl):
    return shell_tpl if purchasing_url_namespace(request) == "shell" else classic_tpl


@login_required
def supplier_list(request):
    qs = Supplier.objects.filter(is_active=True).select_related("linked_customer").order_by("name_ar")
    enriched = []
    for s in qs:
        cust_bal = s.linked_customer.balance if s.linked_customer else Decimal("0")
        net = (s.balance - cust_bal).quantize(Decimal("0.01"))
        enriched.append({"supplier": s, "customer_balance": cust_bal, "net_balance": net})
    tpl = _purchasing_tpl(request, "shell/suppliers_list.html", "purchasing/suppliers.html")
    return render(request, tpl, _purchasing_ctx(request, rows=enriched))


@login_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)
    inv = supplier.purchase_invoices.order_by("-created_at")[:50]
    pay = supplier.payments.order_by("-created_at")[:50]
    led = supplier.ledger_entries.order_by("-created_at")[:100]
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    tpl = _purchasing_tpl(request, "shell/suppliers_detail.html", "purchasing/supplier_detail.html")
    return render(
        request,
        tpl,
        _purchasing_ctx(request, supplier=supplier, invoices=inv, payments=pay, ledger=led, net_balance=net_balance),
    )


@login_required
def supplier_create(request):
    from apps.contacts.models import Customer

    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save()
            ob = form.cleaned_data.get("opening_balance") or Decimal("0")
            if ob > 0:
                supplier.balance = ob
                supplier.save(update_fields=["balance"])
                SupplierLedgerEntry.objects.create(
                    supplier=supplier,
                    entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
                    amount=ob,
                    note="رصيد افتتاحي",
                )
            if form.cleaned_data.get("also_customer"):
                cust = Customer.objects.create(
                    name_ar=supplier.name_ar,
                    name_en=supplier.name_en,
                    phone=supplier.phone,
                )
                supplier.linked_customer = cust
                supplier.save(update_fields=["linked_customer", "updated_at"])
            messages.success(request, f"تم إضافة المورد «{supplier.name_ar}» بنجاح")
            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm()
    tpl = _purchasing_tpl(request, "shell/suppliers_form.html", "purchasing/supplier_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, title="إضافة مورد"))


@login_required
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل بيانات المورد بنجاح")
            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm(instance=supplier)
    tpl = _purchasing_tpl(request, "shell/suppliers_form.html", "purchasing/supplier_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, title="تعديل مورد", supplier=supplier))


@login_required
@require_POST
@transaction.atomic
def supplier_delete(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if PurchaseInvoice.objects.filter(supplier=supplier).exists():
        messages.error(
            request,
            "لا يمكن حذف المورد: توجد فواتير شراء مرتبطة. احذف أو ألغِ فواتير الشراء أولاً.",
        )
        return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
    Product.objects.filter(commission_vendor=supplier).update(commission_vendor=None)
    SupplierPayment.objects.filter(supplier=supplier).delete()
    SupplierLedgerEntry.objects.filter(supplier=supplier).delete()
    name = supplier.name_ar
    supplier.delete()
    messages.success(request, f"تم حذف المورد «{name}» نهائياً.")
    return _purchasing_redirect(request, "suppliers")


@login_required
def supplier_payment_create(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)
    if request.method == "POST":
        form = SupplierPaymentForm(request.POST)
        if form.is_valid():
            try:
                record_supplier_payment(
                    supplier=supplier,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    note=form.cleaned_data["note"],
                    user=request.user,
                )
                messages.success(request, "تم تسجيل السداد بنجاح")
                return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
            except Exception as e:
                messages.error(request, f"حدث خطأ: {e}")
    else:
        form = SupplierPaymentForm()
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    tpl = _purchasing_tpl(request, "shell/suppliers_payment.html", "purchasing/supplier_payment_form.html")
    return render(request, tpl, _purchasing_ctx(request, form=form, supplier=supplier, net_balance=net_balance))


@login_required
@require_POST
def supplier_link_customer(request, pk):
    """Create a Customer record linked to this supplier, or link to existing."""
    from apps.contacts.models import Customer

    supplier = get_object_or_404(Supplier, pk=pk)
    if supplier.linked_customer_id:
        messages.info(request, f"المورد مرتبط بالفعل بحساب عميل: {supplier.linked_customer.name_ar}")
        return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)

    existing = Customer.objects.filter(
        name_ar=supplier.name_ar, phone=supplier.phone
    ).first()
    if existing:
        supplier.linked_customer = existing
        supplier.save(update_fields=["linked_customer", "updated_at"])
        messages.success(request, f"تم ربط المورد بحساب العميل الموجود «{existing.name_ar}»")
    else:
        cust = Customer.objects.create(
            name_ar=supplier.name_ar,
            name_en=supplier.name_en,
            phone=supplier.phone,
        )
        supplier.linked_customer = cust
        supplier.save(update_fields=["linked_customer", "updated_at"])
        messages.success(request, f"تم إنشاء حساب عميل للمورد «{supplier.name_ar}»")
    return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)


@login_required
def purchase_invoice_create(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    errors = []

    if request.method == "POST":
        lines = _purchase_lines_from_request(request, errors)
        if not errors:
            lines = _apply_general_discount(request, lines, errors)

        if not errors:
            total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
            payments = _purchase_payments_from_request(request, total, errors)

            if not errors:
                try:
                    inv = post_purchase_invoice(
                        supplier=supplier,
                        lines=lines,
                        user=request.user,
                        payments=payments,
                    )
                    messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                    return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
                except Exception as e:
                    errors.append(f"حدث خطأ: {e}")

    return render(
        request,
        _purchasing_tpl(request, "shell/purchase_form.html", "purchasing/purchase_form.html"),
        _purchasing_ctx(
            request,
            supplier=supplier,
            errors=errors,
            range10=range(10),
            range20=range(20),
            payment_method_rows=_payment_rows(),
            purchase_form_state=_purchase_form_state(request),
        ),
    )


@login_required
def purchase_invoice_new(request):
    suppliers = Supplier.objects.filter(is_active=True).order_by("name_ar")
    errors = []

    if request.method == "POST":
        sup_id = request.POST.get("supplier_id")
        if not sup_id:
            errors.append("يرجى اختيار مورد")
        else:
            try:
                supplier = Supplier.objects.get(pk=int(sup_id))
            except (Supplier.DoesNotExist, ValueError):
                errors.append("مورد غير صالح")
                supplier = None

            if supplier and not errors:
                lines = _purchase_lines_from_request(request, errors)
                if not errors:
                    lines = _apply_general_discount(request, lines, errors)

                if not errors:
                    total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
                    payments = _purchase_payments_from_request(request, total, errors)

                    if not errors:
                        try:
                            inv = post_purchase_invoice(
                                supplier=supplier,
                                lines=lines,
                                user=request.user,
                                payments=payments,
                            )
                            messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
                        except Exception as e:
                            errors.append(f"حدث خطأ: {e}")

    return render(
        request,
        _purchasing_tpl(request, "shell/purchase_new.html", "purchasing/purchase_new.html"),
        _purchasing_ctx(
            request,
            suppliers=suppliers,
            errors=errors,
            range10=range(10),
            range20=range(20),
            payment_method_rows=_payment_rows(),
            purchase_form_state=_purchase_form_state(request),
        ),
    )


@login_required
def purchase_products_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    purchasable = [Product.ProductType.RAW, Product.ProductType.READY]
    qs = (
        Product.objects.select_related("unit").filter(is_active=True, product_type__in=purchasable, name_ar__icontains=q)
        .order_by("name_ar")[:30]
    )
    return JsonResponse(
        {"results": [{"id": p.pk, "name_ar": p.name_ar, "type": p.product_type, "unit_id": p.unit_id, "unit_name": p.unit.name_ar if p.unit else ""} for p in qs]},
    )


@login_required
def purchase_units_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    qs = Unit.objects.filter(name_ar__icontains=q).order_by("name_ar")[:30]
    return JsonResponse({"results": [{"id": u.pk, "name_ar": u.name_ar, "code": u.code} for u in qs]})


@login_required
@require_POST
def purchase_unit_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:128]
    if len(name_ar) < 1:
        return JsonResponse({"error": "أدخل اسم الوحدة"}, status=400)
    existing = Unit.objects.filter(name_ar__iexact=name_ar).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "code": existing.code, "reused": True})
    n = Unit.objects.count() + 1
    code = f"unit_{n}"
    while Unit.objects.filter(code=code).exists():
        n += 1
        code = f"unit_{n}"
    unit = Unit.objects.create(code=code, name_ar=name_ar, name_en="")
    log_audit(request.user, "catalog.unit.quick_create_purchase", "catalog.Unit", unit.pk, {})
    return JsonResponse({"id": unit.pk, "name_ar": unit.name_ar, "code": unit.code, "reused": False})


@login_required
def purchase_suppliers_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    qs = (
        Supplier.objects.filter(is_active=True)
        .filter(name_ar__icontains=q)
        .order_by("name_ar")[:30]
    )
    return JsonResponse({"results": [{"id": s.pk, "name_ar": s.name_ar, "phone": s.phone} for s in qs]})


@login_required
@require_POST
def purchase_supplier_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم مورد بحرفين على الأقل"}, status=400)
    existing = Supplier.objects.filter(name_ar__iexact=name_ar, is_active=True).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "reused": True})
    supplier = Supplier.objects.create(name_ar=name_ar, name_en="", phone="", email="")
    log_audit(request.user, "purchasing.supplier.quick_create", "purchasing.Supplier", supplier.pk, {})
    return JsonResponse({"id": supplier.pk, "name_ar": supplier.name_ar, "reused": False})


@login_required
@require_POST
def purchase_product_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسماً بحرفين على الأقل"}, status=400)
    ptype = body.get("product_type") or Product.ProductType.RAW
    unit_id = body.get("unit_id")
    unit = None
    if unit_id:
        try:
            unit = Unit.objects.get(pk=int(unit_id))
        except (Unit.DoesNotExist, ValueError):
            return JsonResponse({"error": "وحدة غير صالحة"}, status=400)
    if ptype not in (Product.ProductType.RAW, Product.ProductType.READY):
        ptype = Product.ProductType.RAW
    existing = Product.objects.filter(
        name_ar__iexact=name_ar,
        is_active=True,
        product_type__in=[Product.ProductType.RAW, Product.ProductType.READY],
    ).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "reused": True})
    with transaction.atomic():
        prod = Product.objects.create(
            name_ar=name_ar,
            name_en="",
            unit=unit,
            product_type=ptype,
            selling_price=Decimal("0"),
            is_stock_tracked=True,
            is_active=True,
        )
    log_audit(request.user, "catalog.product.quick_create_purchase", "catalog.Product", prod.pk, {"type": ptype})
    return JsonResponse({"id": prod.pk, "name_ar": prod.name_ar, "unit_id": prod.unit_id, "unit_name": prod.unit.name_ar if prod.unit else "", "reused": False})


@login_required
def supplier_statement(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    try:
        date_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    except ValueError:
        date_from = None
    try:
        date_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        date_to = None

    all_entries = supplier.ledger_entries.order_by("created_at")

    if date_from:
        opening_agg = all_entries.filter(created_at__date__lt=date_from).aggregate(s=Sum("amount"))
        opening_balance = (opening_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
        entries = all_entries.filter(created_at__date__gte=date_from)
    else:
        opening_balance = Decimal("0.00")
        entries = all_entries

    if date_to:
        entries = entries.filter(created_at__date__lte=date_to)

    rows = []
    running = opening_balance
    for e in entries:
        running += e.amount
        rows.append({
            "date": e.created_at,
            "type": e.get_entry_type_display(),
            "entry_type": e.entry_type,
            "amount": e.amount,
            "running": running.quantize(Decimal("0.01")),
            "reference": e.note or e.reference_model,
            "reference_model": e.reference_model,
            "reference_pk": e.reference_pk,
        })

    closing_balance = running.quantize(Decimal("0.01"))

    net_balance = closing_balance
    if supplier.linked_customer:
        net_balance = (closing_balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))

    tpl = _purchasing_tpl(request, "shell/suppliers_statement.html", "purchasing/supplier_statement.html")
    return render(request, tpl, _purchasing_ctx(
        request,
        supplier=supplier,
        rows=rows,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        net_balance=net_balance,
        date_from=date_from,
        date_to=date_to,
    ))


@login_required
def supplier_balances(request):
    suppliers = Supplier.objects.filter(is_active=True).select_related(
        "linked_customer",
    ).annotate(
        last_txn=Max("ledger_entries__created_at"),
    ).order_by("name_ar")

    results = []
    grand_total = Decimal("0.00")
    for s in suppliers:
        bal = s.computed_balance
        cust_bal = s.linked_customer.balance if s.linked_customer else Decimal("0")
        net = (bal - cust_bal).quantize(Decimal("0.01"))
        if bal != Decimal("0") or cust_bal != Decimal("0"):
            results.append({
                "supplier": s,
                "balance": bal,
                "customer_balance": cust_bal,
                "net_balance": net,
                "last_txn": s.last_txn,
            })
            grand_total += net

    grand_total = grand_total.quantize(Decimal("0.01"))

    tpl = _purchasing_tpl(request, "shell/suppliers_balances.html", "purchasing/supplier_balances.html")
    return render(request, tpl, _purchasing_ctx(request, results=results, grand_total=grand_total))


@login_required
def commission_vendor_report(request):
    vendors = Supplier.objects.filter(
        commission_products__isnull=False,
        is_active=True,
    ).distinct().order_by("name_ar")

    rows = []
    grand_sales = Decimal("0")
    grand_commission = Decimal("0")
    grand_vendor_due = Decimal("0")
    grand_paid = Decimal("0")

    for vendor in vendors:
        products = vendor.commission_products.filter(is_active=True)
        product_names = [p.name_ar for p in products]

        sold_lines = SaleInvoiceLine.objects.filter(
            product__in=products,
            invoice__is_cancelled=False,
        )
        total_sales = Decimal("0")
        total_commission = Decimal("0")
        for sl in sold_lines:
            total_sales += sl.line_subtotal or Decimal("0")
            total_commission += sl.recognized_revenue or Decimal("0")

        vendor_due_total = (total_sales - total_commission).quantize(Decimal("0.01"))

        paid = Decimal("0")
        for entry in vendor.ledger_entries.filter(entry_type=SupplierLedgerEntry.EntryType.PAYMENT):
            paid += abs(entry.amount)

        remaining = vendor.balance

        rows.append({
            "vendor": vendor,
            "products": product_names,
            "total_sales": total_sales.quantize(Decimal("0.01")),
            "total_commission": total_commission.quantize(Decimal("0.01")),
            "vendor_due_total": vendor_due_total,
            "paid": paid.quantize(Decimal("0.01")),
            "remaining": remaining,
        })
        grand_sales += total_sales
        grand_commission += total_commission
        grand_vendor_due += vendor_due_total
        grand_paid += paid

    return render(request, "purchasing/commission_vendors.html", {
        "rows": rows,
        "grand_sales": grand_sales.quantize(Decimal("0.01")),
        "grand_commission": grand_commission.quantize(Decimal("0.01")),
        "grand_vendor_due": grand_vendor_due.quantize(Decimal("0.01")),
        "grand_paid": grand_paid.quantize(Decimal("0.01")),
    })


@login_required
def purchase_invoice_list(request):
    qs = PurchaseInvoice.objects.select_related("supplier", "work_session").order_by("-created_at")

    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(invoice_number__icontains=q) | Q(supplier__name_ar__icontains=q)
        )

    invoices = qs[:200]
    tpl = _purchasing_tpl(request, "shell/purchase_list.html", "purchasing/purchase_list.html")
    return render(request, tpl, _purchasing_ctx(request, invoices=invoices, q=q))


@login_required
def purchase_invoice_detail(request, pk):
    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related("supplier", "work_session"),
        pk=pk,
    )
    lines = invoice.lines.select_related("product").order_by("pk")
    tpl = _purchasing_tpl(request, "shell/purchase_detail.html", "purchasing/purchase_detail.html")
    return render(request, tpl, _purchasing_ctx(request, invoice=invoice, lines=lines))


@login_required
@require_POST
def purchase_invoice_delete(request, pk):
    invoice = get_object_or_404(PurchaseInvoice.objects.select_related("supplier"), pk=pk)
    supplier_pk = invoice.supplier_id
    inv_number = invoice.invoice_number
    try:
        purge_purchase_invoice(
            invoice=invoice,
            reason=(request.POST.get("reason") or "حذف نهائي من شاشة فاتورة الشراء").strip(),
            user=request.user,
        )
        messages.success(request, f"تم حذف فاتورة الشراء {inv_number} نهائياً مع كل آثارها.")
        return _purchasing_redirect(request, "supplier_detail", pk=supplier_pk)
    except Exception as e:
        messages.error(request, f"تعذر حذف فاتورة الشراء: {e}")
        return _purchasing_redirect(request, "purchase_detail", pk=pk)


@login_required
def purchase_return_create(request, pk):
    from decimal import InvalidOperation
    from django.db import transaction
    from apps.core.sequences import next_int
    from apps.core.models import log_audit
    from apps.core.services import SessionService
    from apps.inventory.services import adjust_stock
    from apps.inventory.models import StockMovement

    invoice = get_object_or_404(PurchaseInvoice.objects.select_related("supplier"), pk=pk)
    inv_lines = invoice.lines.select_related("product").order_by("pk")
    errors = []

    if request.method == "POST":
        reason = request.POST.get("reason", "")
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
                    errors.append(f"الكمية المرتجعة لـ {line.product.name_ar} أكبر من المشتراة")
                    continue
                return_lines.append((line, qty))
            except (InvalidOperation, ValueError):
                errors.append(f"كمية غير صالحة لـ {line.product.name_ar}")

        if not return_lines and not errors:
            errors.append("يرجى تحديد كمية مرتجعة واحدة على الأقل")

        if not errors:
            with transaction.atomic():
                total = Decimal("0")
                ret = PurchaseReturn.objects.create(
                    purchase_invoice=invoice,
                    return_number=f"PRET-{next_int('purchase_return'):06d}",
                    reason=reason,
                )

                session = SessionService.get_open_session()
                for inv_line, qty in return_lines:
                    line_total = (qty * inv_line.unit_cost).quantize(Decimal("0.01"))
                    PurchaseReturnLine.objects.create(
                        purchase_return=ret,
                        product=inv_line.product,
                        quantity=qty,
                        unit_cost=inv_line.unit_cost,
                        line_total=line_total,
                    )
                    total += line_total

                    if inv_line.product.is_stock_tracked:
                        adjust_stock(
                            product=inv_line.product,
                            quantity_delta=-qty,
                            movement_type=StockMovement.MovementType.ADJUSTMENT,
                            session=session,
                            reference_model="purchasing.PurchaseReturn",
                            reference_pk=str(ret.pk),
                            note=f"مرتجع مشتريات {ret.return_number}",
                        )

                ret.total = total
                ret.save(update_fields=["total", "updated_at"])

                supplier = invoice.supplier
                supplier.balance = (supplier.balance - total).quantize(Decimal("0.01"))
                supplier.save(update_fields=["balance", "updated_at"])
                SupplierLedgerEntry.objects.create(
                    supplier=supplier,
                    entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
                    amount=-total,
                    note=f"مرتجع مشتريات {ret.return_number}",
                    reference_model="purchasing.PurchaseReturn",
                    reference_pk=str(ret.pk),
                )

                log_audit(request.user, "purchase.return.created", "purchasing.PurchaseReturn", ret.pk, {"total": str(total)})
                messages.success(request, f"تم تسجيل مرتجع {ret.return_number} بمبلغ {total}")
                return redirect("purchasing:purchase_detail", pk=invoice.pk)

    return render(request, "purchasing/purchase_return_form.html", {
        "invoice": invoice,
        "inv_lines": inv_lines,
        "errors": errors,
    })
