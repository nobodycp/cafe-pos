from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from apps.purchasing.forms import SupplierForm, SupplierPaymentForm
from apps.purchasing.models import PurchaseInvoice, PurchaseLine, PurchaseReturn, PurchaseReturnLine, Supplier, SupplierLedgerEntry
from apps.purchasing.services import post_purchase_invoice, record_supplier_payment
from apps.billing.models import SaleInvoiceLine


@login_required
def supplier_list(request):
    qs = Supplier.objects.filter(is_active=True).select_related("linked_customer").order_by("name_ar")
    enriched = []
    for s in qs:
        cust_bal = s.linked_customer.balance if s.linked_customer else Decimal("0")
        net = (s.balance - cust_bal).quantize(Decimal("0.01"))
        enriched.append({"supplier": s, "customer_balance": cust_bal, "net_balance": net})
    return render(request, "purchasing/suppliers.html", {"rows": enriched})


@login_required
def supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)
    inv = supplier.purchase_invoices.order_by("-created_at")[:50]
    pay = supplier.payments.order_by("-created_at")[:50]
    led = supplier.ledger_entries.order_by("-created_at")[:100]
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    return render(
        request,
        "purchasing/supplier_detail.html",
        {"supplier": supplier, "invoices": inv, "payments": pay, "ledger": led, "net_balance": net_balance},
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
            return redirect("purchasing:supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm()
    return render(request, "purchasing/supplier_form.html", {"form": form, "title": "إضافة مورد"})


@login_required
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل بيانات المورد بنجاح")
            return redirect("purchasing:supplier_detail", pk=supplier.pk)
    else:
        form = SupplierForm(instance=supplier)
    return render(request, "purchasing/supplier_form.html", {"form": form, "title": "تعديل مورد", "supplier": supplier})


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
                return redirect("purchasing:supplier_detail", pk=supplier.pk)
            except Exception as e:
                messages.error(request, f"حدث خطأ: {e}")
    else:
        form = SupplierPaymentForm()
    net_balance = supplier.balance
    if supplier.linked_customer:
        net_balance = (supplier.balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))
    return render(request, "purchasing/supplier_payment_form.html", {"form": form, "supplier": supplier, "net_balance": net_balance})


@login_required
@require_POST
def supplier_link_customer(request, pk):
    """Create a Customer record linked to this supplier, or link to existing."""
    from apps.contacts.models import Customer

    supplier = get_object_or_404(Supplier, pk=pk)
    if supplier.linked_customer_id:
        messages.info(request, f"المورد مرتبط بالفعل بحساب عميل: {supplier.linked_customer.name_ar}")
        return redirect("purchasing:supplier_detail", pk=supplier.pk)

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
    return redirect("purchasing:supplier_detail", pk=supplier.pk)


@login_required
def purchase_invoice_create(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    purchasable_types = [Product.ProductType.RAW, Product.ProductType.READY]
    products = Product.objects.filter(is_active=True, product_type__in=purchasable_types).order_by("name_ar")
    errors = []

    if request.method == "POST":
        lines = []
        for i in range(10):
            prod_id = request.POST.get(f"product_{i}")
            qty = request.POST.get(f"qty_{i}")
            cost = request.POST.get(f"cost_{i}")
            if prod_id and qty and cost:
                try:
                    product = Product.objects.get(pk=int(prod_id))
                    q = Decimal(qty)
                    c = Decimal(cost)
                    if q <= 0 or c <= 0:
                        errors.append(f"سطر {i + 1}: الكمية والتكلفة يجب أن تكون أكبر من صفر")
                        continue
                    lines.append((product, q, c))
                except (Product.DoesNotExist, InvalidOperation, ValueError):
                    errors.append(f"سطر {i + 1}: بيانات غير صالحة")

        pay_method = request.POST.get("pay_method", "cash")
        pay_amount_str = request.POST.get("pay_amount", "0")

        if not lines:
            errors.append("يرجى إدخال سطر واحد على الأقل")

        if not errors:
            total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
            try:
                pay_amount = Decimal(pay_amount_str).quantize(Decimal("0.01"))
            except InvalidOperation:
                pay_amount = Decimal("0")

            payments = []
            if pay_method in ("cash", "bank") and pay_amount > 0:
                credit = total - pay_amount
                payments.append((pay_method, pay_amount))
                if credit > 0:
                    payments.append(("credit", credit))
            else:
                payments.append(("credit", total))

            try:
                inv = post_purchase_invoice(
                    supplier=supplier,
                    lines=lines,
                    user=request.user,
                    payments=payments,
                )
                messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                return redirect("purchasing:supplier_detail", pk=supplier.pk)
            except Exception as e:
                errors.append(f"حدث خطأ: {e}")

    return render(
        request,
        "purchasing/purchase_form.html",
        {"supplier": supplier, "products": products, "errors": errors, "range10": range(10)},
    )


@login_required
def purchase_invoice_new(request):
    suppliers = Supplier.objects.filter(is_active=True).order_by("name_ar")
    purchasable_types = [Product.ProductType.RAW, Product.ProductType.READY]
    products = Product.objects.filter(is_active=True, product_type__in=purchasable_types).order_by("name_ar")
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
                lines = []
                for i in range(10):
                    prod_id = request.POST.get(f"product_{i}")
                    qty = request.POST.get(f"qty_{i}")
                    cost = request.POST.get(f"cost_{i}")
                    if prod_id and qty and cost:
                        try:
                            product = Product.objects.get(pk=int(prod_id))
                            q = Decimal(qty)
                            c = Decimal(cost)
                            if q <= 0 or c <= 0:
                                errors.append(f"سطر {i + 1}: الكمية والتكلفة يجب أن تكون أكبر من صفر")
                                continue
                            lines.append((product, q, c))
                        except (Product.DoesNotExist, InvalidOperation, ValueError):
                            errors.append(f"سطر {i + 1}: بيانات غير صالحة")

                pay_method = request.POST.get("pay_method", "cash")
                pay_amount_str = request.POST.get("pay_amount", "0")

                if not lines:
                    errors.append("يرجى إدخال سطر واحد على الأقل")

                if not errors:
                    total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
                    try:
                        pay_amount = Decimal(pay_amount_str).quantize(Decimal("0.01"))
                    except InvalidOperation:
                        pay_amount = Decimal("0")

                    payments = []
                    if pay_method in ("cash", "bank") and pay_amount > 0:
                        credit = total - pay_amount
                        payments.append((pay_method, pay_amount))
                        if credit > 0:
                            payments.append(("credit", credit))
                    else:
                        payments.append(("credit", total))

                    try:
                        inv = post_purchase_invoice(
                            supplier=supplier,
                            lines=lines,
                            user=request.user,
                            payments=payments,
                        )
                        messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                        return redirect("purchasing:supplier_detail", pk=supplier.pk)
                    except Exception as e:
                        errors.append(f"حدث خطأ: {e}")

    return render(
        request,
        "purchasing/purchase_new.html",
        {"suppliers": suppliers, "products": products, "errors": errors, "range10": range(10)},
    )


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
        })

    closing_balance = running.quantize(Decimal("0.01"))

    net_balance = closing_balance
    if supplier.linked_customer:
        net_balance = (closing_balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))

    return render(request, "purchasing/supplier_statement.html", {
        "supplier": supplier,
        "rows": rows,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "net_balance": net_balance,
        "date_from": date_from,
        "date_to": date_to,
    })


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

    return render(request, "purchasing/supplier_balances.html", {
        "results": results,
        "grand_total": grand_total,
    })


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
    return render(request, "purchasing/purchase_list.html", {"invoices": invoices, "q": q})


@login_required
def purchase_invoice_detail(request, pk):
    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related("supplier", "work_session"),
        pk=pk,
    )
    lines = invoice.lines.select_related("product").order_by("pk")
    return render(request, "purchasing/purchase_detail.html", {
        "invoice": invoice,
        "lines": lines,
    })


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
