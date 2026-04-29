from decimal import Decimal, InvalidOperation
from typing import Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.views.decorators.http import require_GET, require_POST

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.contacts.customer_lookup import active_customers_search_qs
from apps.core.forms import TreasuryVoucherForm
from apps.core.treasury_services import recent_treasury_voucher_logs, submit_treasury_voucher
from apps.core.services import SessionService
from apps.expenses.models import Expense
from apps.payroll.models import Employee
from apps.pos.forms import DiningTableForm
from apps.pos.models import DiningTable, Order, TableSession
from apps.pos.table_service import prepare_work_session_for_shift_close
from apps.purchasing.models import Supplier


@login_required
def home(request):
    return redirect("pos:main")


def _safe_treasury_redirect_next(request) -> Optional[str]:
    """يسمح بإعادة التوجيه الداخلية فقط (مثلاً الكاشير) بعد تسجيل سند."""
    raw = (request.POST.get("next") or "").strip()
    if not raw or "\n" in raw or "\r" in raw or ".." in raw or raw.startswith("//"):
        return None
    if not raw.startswith("/"):
        return None
    path_only = raw.split("?", 1)[0]
    if not path_only.startswith("/pos"):
        return None
    try:
        resolve(path_only)
    except Resolver404:
        return None
    return raw


@login_required
def treasury(request):
    """سند موحّد: نوع السند قبض/صرف، وتصنيف الجهة منفصل."""
    ws = SessionService.get_open_session()
    voucher_form = TreasuryVoucherForm(prefix="tv")
    if request.method == "POST":
        next_url = _safe_treasury_redirect_next(request)
        voucher_form = TreasuryVoucherForm(request.POST, prefix="tv")
        if voucher_form.is_valid():
            vt = voucher_form.cleaned_data["voucher_type"]
            try:
                submit_treasury_voucher(
                    voucher_type=vt,
                    cleaned=voucher_form.cleaned_data,
                    user=request.user,
                    work_session=ws,
                )
                if vt == TreasuryVoucherForm.VT_RECEIPT:
                    messages.success(request, "تم تسجيل سند القبض بنجاح.")
                else:
                    messages.success(request, "تم تسجيل سند الصرف بنجاح.")
                if next_url:
                    return redirect(next_url)
                return redirect("shell:accounting_treasury")
            except ValueError as e:
                if str(e) == "UNKNOWN_VOUCHER_TYPE":
                    messages.error(request, "نوع السند غير معروف.")
                else:
                    messages.error(request, "المبلغ غير صالح.")
            except Exception as e:
                messages.error(request, f"تعذّر التسجيل: {e}")
        else:
            messages.error(request, "راجع بيانات السند.")
    return render(
        request,
        "shell/treasury.html",
        {
            "voucher_form": voucher_form,
            "work_session": ws,
            "recent_treasury_rows": list(recent_treasury_voucher_logs(limit=10)),
        },
    )


@login_required
@require_GET
def treasury_party_search(request):
    """اقتراحات عميل / مورد / موظف لحقل «اسم صاحب السند» في سند الصندوق."""
    q = (request.GET.get("q") or "").strip()
    party_type = (request.GET.get("party_type") or "").strip()
    if len(q) < 1 or party_type not in (
        TreasuryVoucherForm.PARTY_CUSTOMER,
        TreasuryVoucherForm.PARTY_SUPPLIER,
        TreasuryVoucherForm.PARTY_EMPLOYEE,
    ):
        return JsonResponse({"results": []})

    limit = 24
    results = []
    if party_type == TreasuryVoucherForm.PARTY_CUSTOMER:
        results = [{"id": c.pk, "label": c.name_ar} for c in active_customers_search_qs(q, limit=limit)]
    elif party_type == TreasuryVoucherForm.PARTY_SUPPLIER:
        qs = (
            Supplier.objects.filter(is_active=True)
            .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(phone__icontains=q))
            .order_by("name_ar")[:limit]
        )
        results = [{"id": s.pk, "label": s.name_ar} for s in qs]
    elif party_type == TreasuryVoucherForm.PARTY_EMPLOYEE:
        qs = (
            Employee.objects.filter(is_active=True)
            .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q))
            .order_by("name_ar")[:limit]
        )
        results = [{"id": e.pk, "label": e.name_ar} for e in qs]
    return JsonResponse({"results": results})


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
            return redirect("shell:tables_list")
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
            return redirect("shell:tables_list")
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
    return redirect("shell:tables_list")


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
    pay_map = {
        "cash": Decimal("0"),
        "bank": Decimal("0"),
        "bank_ps": Decimal("0"),
        "palpay": Decimal("0"),
        "jawwalpay": Decimal("0"),
        "credit": Decimal("0"),
    }
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
