from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.core.forms import (
    CafeInfoForm,
    CurrencyForm,
    OrderSettingsForm,
    PrinterForm,
    ReceiptForm,
    TaxServiceForm,
)
from apps.core.models import PosSettings
from apps.core.services import SessionService
from apps.expenses.models import Expense
from apps.pos.forms import DiningTableForm
from apps.pos.models import DiningTable, Order, TableSession
from apps.pos.table_service import prepare_work_session_for_shift_close


@login_required
def home(request):
    return redirect("pos:main")


@login_required
@require_POST
def open_session_view(request):
    raw = request.POST.get("opening_cash", "0")
    try:
        opening = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        opening = Decimal("0")
    try:
        SessionService.open_session(request.user, opening, request.POST.get("notes", ""))
    except ValueError as e:
        if str(e) == "SESSION_ALREADY_OPEN":
            request.session["flash_error"] = "يوجد وردية مفتوحة بالفعل."
        else:
            request.session["flash_error"] = str(e)
    return redirect("pos:main")


@login_required
@require_POST
def close_session_view(request):
    raw = request.POST.get("closing_cash", "")
    closing = None
    if raw != "":
        try:
            closing = Decimal(str(raw).replace(",", "."))
        except (InvalidOperation, ValueError):
            closing = None
    ws = SessionService.get_open_session()
    if ws:
        prepare_work_session_for_shift_close(ws)
        if Order.objects.filter(work_session=ws, status=Order.Status.OPEN).exists():
            request.session["flash_error"] = (
                "لا يمكن إغلاق الوردية: يوجد طلبات مفتوحة أو طاولات لم تُسوَّ بعد. "
                "أكمل الدفع أو علّق الطلبات من خريطة الطاولات."
            )
            return redirect("pos:main")
        if TableSession.objects.filter(work_session=ws, status=TableSession.Status.OPEN).exists():
            request.session["flash_error"] = (
                "لا يمكن إغلاق الوردية: جلسات طاولات مفتوحة. راجع خريطة الطاولات."
            )
            return redirect("pos:main")
    try:
        SessionService.close_session(request.user, closing, request.POST.get("notes", ""))
        request.session["flash_ok"] = "تم إغلاق الوردية."
    except ValueError as e:
        request.session["flash_error"] = str(e)
    request.session.pop("active_pos_order_id", None)
    return redirect("pos:main")


@login_required
def settings_page(request):
    obj, _ = PosSettings.objects.get_or_create(pk=1)
    section = request.POST.get("section", request.GET.get("tab", ""))

    form_map = {
        "cafe": CafeInfoForm,
        "currency": CurrencyForm,
        "tax": TaxServiceForm,
        "order": OrderSettingsForm,
        "printer": PrinterForm,
        "receipt": ReceiptForm,
    }

    if request.method == "POST" and section in form_map:
        form = form_map[section](request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ الإعدادات بنجاح.")
            return redirect(f"{request.path}?tab={section}")
    ctx = {k: cls(instance=obj) for k, cls in form_map.items()}
    ctx["active_tab"] = section or "cafe"
    return render(request, "core/settings.html", ctx)


@login_required
def tables_list(request):
    tables = DiningTable.objects.order_by("sort_order", "name_ar")
    return render(request, "core/tables_list.html", {"tables": tables})


@login_required
def table_create(request):
    if request.method == "POST":
        form = DiningTableForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة الطاولة بنجاح.")
            return redirect("core:tables_list")
    else:
        form = DiningTableForm()
    return render(request, "core/table_form.html", {"form": form, "edit": False})


@login_required
def table_edit(request, pk):
    table = get_object_or_404(DiningTable, pk=pk)
    if request.method == "POST":
        form = DiningTableForm(request.POST, instance=table)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الطاولة بنجاح.")
            return redirect("core:tables_list")
    else:
        form = DiningTableForm(instance=table)
    return render(request, "core/table_form.html", {"form": form, "edit": True})


@login_required
@require_POST
def table_toggle(request, pk):
    table = get_object_or_404(DiningTable, pk=pk)
    table.is_active = not table.is_active
    table.save(update_fields=["is_active", "updated_at"])
    status = "تفعيل" if table.is_active else "إلغاء تفعيل"
    messages.success(request, f"تم {status} الطاولة «{table.name_ar}».")
    return redirect("core:tables_list")


@login_required
def session_summary(request):
    ws = SessionService.get_open_session()
    if not ws:
        return redirect("pos:main")

    if request.method == "POST":
        raw = request.POST.get("closing_cash", "")
        closing = None
        if raw != "":
            try:
                closing = Decimal(str(raw).replace(",", "."))
            except (InvalidOperation, ValueError):
                closing = None
        prepare_work_session_for_shift_close(ws)
        if Order.objects.filter(work_session=ws, status=Order.Status.OPEN).exists():
            messages.error(
                request,
                "لا يمكن إغلاق الوردية: يوجد طلبات مفتوحة. أكمل الدفع أو علّق الطلبات.",
            )
            return redirect("core:session_summary")
        if TableSession.objects.filter(work_session=ws, status=TableSession.Status.OPEN).exists():
            messages.error(request, "لا يمكن إغلاق الوردية: جلسات طاولات مفتوحة.")
            return redirect("core:session_summary")
        try:
            SessionService.close_session(request.user, closing, request.POST.get("notes", ""))
            messages.success(request, "تم إغلاق الوردية بنجاح.")
        except ValueError as e:
            messages.error(request, str(e))
        request.session.pop("active_pos_order_id", None)
        return redirect("pos:main")

    invoices = SaleInvoice.objects.filter(work_session=ws, is_cancelled=False)
    totals = invoices.aggregate(
        revenue=Sum("total"),
        profit=Sum("total_profit"),
        cost=Sum("total_cost"),
    )
    revenue = totals["revenue"] or Decimal("0")
    profit = totals["profit"] or Decimal("0")
    invoice_count = invoices.count()

    pay_qs = (
        InvoicePayment.objects.filter(invoice__work_session=ws, invoice__is_cancelled=False)
        .values("method")
        .annotate(s=Sum("amount"))
    )
    pay_map = {"cash": Decimal("0"), "bank": Decimal("0"), "credit": Decimal("0")}
    for p in pay_qs:
        m = p["method"]
        if m in pay_map:
            pay_map[m] = p["s"] or Decimal("0")

    expenses_qs = Expense.objects.filter(work_session=ws)
    total_expenses = expenses_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    cash_expenses = (
        expenses_qs.filter(payment_method="cash").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )

    opening_cash = ws.opening_cash or Decimal("0")
    expected_cash = opening_cash + pay_map["cash"] - cash_expenses

    open_orders = Order.objects.filter(
        work_session=ws, status=Order.Status.OPEN
    ).select_related("table")

    net_profit = profit - total_expenses

    return render(request, "core/session_summary.html", {
        "session": ws,
        "revenue": revenue,
        "profit": profit,
        "invoice_count": invoice_count,
        "payments": pay_map,
        "total_expenses": total_expenses,
        "cash_expenses": cash_expenses,
        "opening_cash": opening_cash,
        "expected_cash": expected_cash,
        "net_profit": net_profit,
        "open_orders": open_orders,
    })
