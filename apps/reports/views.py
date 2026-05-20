import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.core.pagination import paginate_queryset
from apps.core.payment_methods import load_payment_method_rows, payment_method_label_map
from apps.core.models import AuditLog, WorkSession
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.reports.payment_channel_ledger import (
    LEDGER_KIND_CHOICES,
    LEDGER_SORT_CHOICES,
    apply_amount_bounds,
    apply_flow_filter,
    apply_kind_filter,
    apply_search,
    attach_running_balance,
    collect_all_ledger_rows,
    collect_ledger_rows,
    sort_ledger_rows,
    summarize,
    summarize_inflows_by_method,
)
from apps.core.services import SessionService
from apps.expenses.models import Expense
from apps.inventory.models import StockBalance


@login_required
def reports_dashboard(request):
    from apps.reports.services import build_dashboard_context

    return render(request, "reports/dashboard.html", build_dashboard_context())


@login_required
def daily_sales_report(request):
    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    try:
        d_from = date.fromisoformat(date_from) if date_from else today
    except ValueError:
        d_from = today
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    invoices = (
        SaleInvoice.objects.filter(
            is_cancelled=False,
            created_at__date__gte=d_from,
            created_at__date__lte=d_to,
        )
        .select_related("order__table", "customer")
        .prefetch_related("payments")
        .order_by("-created_at")
    )

    from apps.core.list_filters import get_search_q

    q = get_search_q(request)
    if q:
        invoices = invoices.filter(
            Q(invoice_number__icontains=q)
            | Q(customer__name_ar__icontains=q)
            | Q(customer__name_en__icontains=q)
        )

    agg = invoices.aggregate(
        total_sales=Sum("total"),
        total_profit=Sum("total_profit"),
        count=Count("id"),
    )

    from apps.reports.services import build_daily_sales_invoice_list

    pag = paginate_queryset(request, invoices)
    invoice_list = build_daily_sales_invoice_list(pag["page_obj"])

    ctx = {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "q": q,
        "invoices": invoice_list,
        "total_sales": agg["total_sales"] or Decimal("0"),
        "total_profit": agg["total_profit"] or Decimal("0"),
        "invoice_count": agg["count"] or 0,
        "filters_open": bool(q),
    }
    ctx.update(pag)
    return render(request, "reports/daily_sales.html", ctx)


@login_required
def payroll_report(request):
    from apps.payroll.models import Employee, EmployeeAdvance, EmployeeCafePurchase, EmployeeSalaryPayout

    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    from apps.reports.services import build_payroll_report_rows

    rows, grand_adv, grand_pay, grand_cafe = build_payroll_report_rows(d_from, d_to)

    return render(request, "reports/payroll_report.html", {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "rows": rows,
        "grand_advances": grand_adv,
        "grand_payouts": grand_pay,
        "grand_cafe": grand_cafe,
        "grand_total": grand_adv + grand_pay + grand_cafe,
    })


@login_required
def expense_report(request):
    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    from apps.reports.services import build_expense_report_breakdown

    breakdown = build_expense_report_breakdown(d_from, d_to)

    return render(request, "reports/expense_report.html", {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        **breakdown,
    })


@login_required
def weekly_report(request):
    today = date.today()
    weeks = []
    for i in range(4):
        week_end = today - timedelta(days=today.weekday()) - timedelta(weeks=i)
        week_start = week_end - timedelta(days=6)
        if i == 0:
            week_end = today

        iso_year, iso_week, _ = week_start.isocalendar()

        invs = SaleInvoice.objects.filter(
            is_cancelled=False,
            created_at__date__gte=week_start,
            created_at__date__lte=week_end,
        )
        inv_agg = invs.aggregate(
            sales=Sum("total"),
            profit=Sum("total_profit"),
            count=Count("id"),
        )

        exp_total = (
            Expense.objects.filter(
                expense_date__gte=week_start,
                expense_date__lte=week_end,
            ).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )

        sales = inv_agg["sales"] or Decimal("0")
        profit = inv_agg["profit"] or Decimal("0")
        net = profit - exp_total

        weeks.append({
            "iso_year": iso_year,
            "iso_week": iso_week,
            "start": week_start,
            "end": week_end,
            "sales": sales,
            "expenses": exp_total,
            "profit": profit,
            "net": net,
            "invoice_count": inv_agg["count"] or 0,
        })

    return render(request, "reports/weekly_report.html", {"weeks": weeks})


@login_required
def product_movement_report(request):
    from apps.billing.models import SaleInvoiceLine
    from apps.catalog.models import Category, Product
    from apps.reports.product_movement_filters import (
        SECTION_CHOICES,
        SLOW_SORT_CHOICES,
        TOP_SORT_CHOICES,
        apply_product_name_filters,
        movement_filters_open,
        order_slow_movers,
        order_top_sellers,
        parse_movement_filters,
        quick_period_dates,
    )

    today = date.today()
    period_quick = (request.GET.get("period") or "").strip()
    f = parse_movement_filters(request.GET, today=today)
    if period_quick in ("week", "month", "year"):
        d_from, d_to = quick_period_dates(period_quick, today=today)
        f["date_from"] = d_from
        f["date_to"] = d_to
        f["date_from_iso"] = d_from.isoformat()
        f["date_to_iso"] = d_to.isoformat()
    else:
        period_quick = ""

    valid_types = {c[0] for c in Product.ProductType.choices}
    product_type = f["product_type"] if f["product_type"] in valid_types else ""

    line_base = SaleInvoiceLine.objects.filter(
        invoice__is_cancelled=False,
        invoice__created_at__date__gte=f["date_from"],
        invoice__created_at__date__lte=f["date_to"],
    )
    line_base = apply_product_name_filters(line_base, {**f, "product_type": product_type}, prefix="product__")

    top_qs = (
        line_base.values("product__pk", "product__name_ar")
        .annotate(
            total_qty=Sum("quantity"),
            total_revenue=Sum("line_subtotal"),
            total_profit=Sum("line_profit"),
        )
    )
    top_qs = order_top_sellers(top_qs, f["sort_top"])

    sold_pks = line_base.values_list("product_id", flat=True).distinct()

    slow_qs = Product.objects.filter(
        is_active=True,
        is_stock_tracked=True,
    ).exclude(product_type__in=[Product.ProductType.RAW, Product.ProductType.SERVICE]).exclude(
        pk__in=sold_pks
    )
    slow_qs = apply_product_name_filters(slow_qs, {**f, "product_type": product_type})
    slow_qs = order_slow_movers(slow_qs, f["sort_slow"])

    ctx = {
        "movement_filters": f,
        "filters_open": movement_filters_open(f, today=today),
        "section_choices": SECTION_CHOICES,
        "top_sort_choices": TOP_SORT_CHOICES,
        "slow_sort_choices": SLOW_SORT_CHOICES,
        "product_type_choices": Product.ProductType.choices,
        "filter_category_options": list(Category.objects.filter(is_active=True).order_by("sort_order", "name_ar")),
        "date_from": f["date_from_iso"],
        "date_to": f["date_to_iso"],
        "period_quick": period_quick,
        "show_top": f["section"] in ("all", "top"),
        "show_slow": f["section"] in ("all", "slow"),
    }

    if ctx["show_top"]:
        top_ctx = paginate_queryset(request, top_qs, page_param="page_top", default_per_page=25)
        ctx.update({f"top_{k}": v for k, v in top_ctx.items()})
        ctx["top_sellers"] = top_ctx["page_obj"]
    else:
        ctx["top_sellers"] = []

    if ctx["show_slow"]:
        slow_ctx = paginate_queryset(request, slow_qs, page_param="page_slow", default_per_page=25)
        ctx.update({f"slow_{k}": v for k, v in slow_ctx.items()})
        ctx["slow_movers"] = slow_ctx["page_obj"]
    else:
        ctx["slow_movers"] = []

    qd = request.GET.copy()
    for pk in ("page_top", "page_slow"):
        qd.pop(pk, None)
    ctx["movement_list_query"] = qd.urlencode()

    return render(request, "reports/product_movement.html", ctx)


@login_required
def cash_flow_report(request):
    from apps.purchasing.models import SupplierPayment
    from apps.contacts.models import CustomerLedgerEntry
    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    cash_sales = InvoicePayment.objects.filter(
        method="cash",
        invoice__is_cancelled=False,
        invoice__created_at__date__gte=d_from,
        invoice__created_at__date__lte=d_to,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    bank_sales = (
        InvoicePayment.objects.filter(
            Q(method="bank") | Q(method="bank_ps") | Q(method="palpay") | Q(method="jawwalpay"),
            invoice__is_cancelled=False,
            invoice__created_at__date__gte=d_from,
            invoice__created_at__date__lte=d_to,
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )

    customer_payments = CustomerLedgerEntry.objects.filter(
        entry_type="payment",
        created_at__date__gte=d_from,
        created_at__date__lte=d_to,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    customer_payments = abs(customer_payments)

    total_inflows = cash_sales + bank_sales + customer_payments

    expenses_total = Expense.objects.filter(
        expense_date__gte=d_from,
        expense_date__lte=d_to,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    supplier_payments = SupplierPayment.objects.filter(
        created_at__date__gte=d_from,
        created_at__date__lte=d_to,
    ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

    # السلف وصرف الرواتب تُسجَّل ضمن المصروفات (تصنيف رواتب) — لا نجمعها مرة ثانية
    total_outflows = expenses_total + supplier_payments

    net_flow = total_inflows - total_outflows

    return render(request, "reports/cash_flow.html", {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "cash_sales": cash_sales,
        "bank_sales": bank_sales,
        "customer_payments": customer_payments,
        "total_inflows": total_inflows,
        "expenses_total": expenses_total,
        "supplier_payments": supplier_payments,
        "total_outflows": total_outflows,
        "net_flow": net_flow,
    })


@login_required
def payment_channels_report(request):
    """تقرير طرق الدفع والتتبع: كشف موحّد لكل الطرق مع فلاتر ومجاميع."""
    from apps.billing.models import InvoicePayment

    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    q = (request.GET.get("q") or "").strip()
    min_amount_s = (request.GET.get("min_amount") or "").strip()
    max_amount_s = (request.GET.get("max_amount") or "").strip()
    min_amt = max_amt = None
    try:
        if min_amount_s:
            min_amt = Decimal(min_amount_s).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        min_amt = None
    try:
        if max_amount_s:
            max_amt = Decimal(max_amount_s).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        max_amt = None

    valid_kinds = {c[0] for c in LEDGER_KIND_CHOICES}
    kind = (request.GET.get("kind") or "").strip()
    if kind not in valid_kinds:
        kind = ""

    flow = (request.GET.get("flow") or "all").strip().lower()
    if flow not in ("all", "in", "out"):
        flow = "all"

    valid_sorts = {c[0] for c in LEDGER_SORT_CHOICES}
    sort_key = (request.GET.get("sort") or "chrono").strip().lower()
    if sort_key not in valid_sorts:
        sort_key = "chrono"

    pay_method = (request.GET.get("pay_method") or "").strip()
    valid_pm = {r["code"] for r in load_payment_method_rows()}
    if pay_method and pay_method not in valid_pm:
        pay_method = ""

    raw_rows = collect_all_ledger_rows(date_from=d_from, date_to=d_to)
    if pay_method:
        raw_rows = [r for r in raw_rows if r.method_code == pay_method]
    filtered = apply_search(raw_rows, q)
    filtered = apply_kind_filter(filtered, kind)
    filtered = apply_flow_filter(filtered, flow)
    filtered = apply_amount_bounds(filtered, min_amt, max_amt)
    filtered = sort_ledger_rows(filtered, sort_key)
    row_dicts = attach_running_balance(filtered)
    summary = summarize(row_dicts)
    inflows_by_method = summarize_inflows_by_method(filtered)

    def _channels_q(**overrides):
        qd = request.GET.copy()
        for k, v in overrides.items():
            qd[k] = str(v)
        return qd.urlencode()

    next_date_sort = "chrono_desc" if sort_key == "chrono" else "chrono"
    next_amount_sort = "amount_asc" if sort_key == "amount_desc" else "amount_desc"
    next_kind_sort = "chrono" if sort_key == "kind" else "kind"
    next_party_sort = "chrono" if sort_key == "party" else "party"
    header_sort_qs = {
        "date": _channels_q(sort=next_date_sort, page=1),
        "amount": _channels_q(sort=next_amount_sort, page=1),
        "kind": _channels_q(sort=next_kind_sort, page=1),
        "party": _channels_q(sort=next_party_sort, page=1),
    }

    pay_method_options = [
        {"code": r["code"], "label": r.get("label_ar") or r.get("label") or r["code"]}
        for r in load_payment_method_rows()
    ]

    inv_pay_base = InvoicePayment.objects.filter(
        invoice__is_cancelled=False,
        invoice__created_at__date__gte=d_from,
        invoice__created_at__date__lte=d_to,
    )
    cash_in = Decimal("0")
    for r in load_payment_method_rows():
        if (r.get("ledger") or "").strip().lower() != "cash":
            continue
        cash_in += inv_pay_base.filter(method=r["code"]).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    expense_cash_out = (
        Expense.objects.filter(
            expense_date__gte=d_from,
            expense_date__lte=d_to,
            payment_method="cash",
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )
    cash_net_approx = (cash_in - expense_cash_out).quantize(Decimal("0.01"))

    ctx = {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "q": q,
        "kind": kind,
        "flow": flow,
        "sort_key": sort_key,
        "min_amount": min_amount_s,
        "max_amount": max_amount_s,
        "pay_method": pay_method,
        "ledger_kind_choices": LEDGER_KIND_CHOICES,
        "ledger_sort_choices": LEDGER_SORT_CHOICES,
        "pay_method_options": pay_method_options,
        "summary": summary,
        "inflows_by_method": inflows_by_method,
        "cash_net_approx": cash_net_approx,
        "ledger_note": (
            "الإجماليات والجدول بعد تطبيق الفترة والفلاتر. «وارد حسب طريقة الدفع» من السطور المصفّاة حالياً."
        ),
        "header_sort_qs": header_sort_qs,
    }
    ctx.update(paginate_queryset(request, row_dicts))
    page_obj = ctx["page_obj"]
    if page_obj.object_list:
        page_in = sum((r["amount"] for r in page_obj if r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
        page_out = sum((r["amount"] for r in page_obj if not r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
    else:
        page_in = page_out = Decimal("0")
    ctx["page_totals"] = {
        "total_in": page_in,
        "total_out": page_out,
        "net": (page_in - page_out).quantize(Decimal("0.01")),
    }
    ctx["show_page_totals"] = page_obj.paginator.num_pages > 1 and page_obj.paginator.count > 0
    ctx["last_running"] = row_dicts[-1]["running"] if row_dicts else Decimal("0")

    return render(request, "reports/payment_channels.html", ctx)


@login_required
def payment_channel_ledger(request):
    """كشف حركة طريقة دفع واحدة: مبيعات، مصروفات، سدادات موردين، سندات خزينة (قبض/صرف/خصومات)."""
    import re as _re

    def _ledger_query(overrides: dict) -> str:
        q = request.GET.copy()
        for k, v in overrides.items():
            if v is None:
                q.pop(k, None)
            else:
                q[k] = str(v)
        return q.urlencode()

    method = (request.GET.get("method") or "").strip().lower()
    if not method or not _re.match(r"^[a-z][a-z0-9_]{0,31}$", method):
        return redirect(reverse("shell:payment_channels"))

    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    q = (request.GET.get("q") or "").strip()
    min_amount_s = (request.GET.get("min_amount") or "").strip()
    max_amount_s = (request.GET.get("max_amount") or "").strip()
    min_amt = max_amt = None
    try:
        if min_amount_s:
            min_amt = Decimal(min_amount_s).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        min_amt = None
    try:
        if max_amount_s:
            max_amt = Decimal(max_amount_s).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        max_amt = None

    valid_kinds = {c[0] for c in LEDGER_KIND_CHOICES}
    kind = (request.GET.get("kind") or "").strip()
    if kind not in valid_kinds:
        kind = ""

    flow = (request.GET.get("flow") or "all").strip().lower()
    if flow not in ("all", "in", "out"):
        flow = "all"

    valid_sorts = {c[0] for c in LEDGER_SORT_CHOICES}
    sort_key = (request.GET.get("sort") or "chrono").strip().lower()
    if sort_key not in valid_sorts:
        sort_key = "chrono"

    raw_rows = collect_ledger_rows(method=method, date_from=d_from, date_to=d_to)
    filtered = apply_search(raw_rows, q)
    filtered = apply_kind_filter(filtered, kind)
    filtered = apply_flow_filter(filtered, flow)
    filtered = apply_amount_bounds(filtered, min_amt, max_amt)
    filtered = sort_ledger_rows(filtered, sort_key)
    row_dicts = attach_running_balance(filtered)
    summary = summarize(row_dicts)
    inflows_by_method = summarize_inflows_by_method(filtered)
    labels = payment_method_label_map()
    method_label = labels.get(method, method)

    next_date_sort = "chrono_desc" if sort_key == "chrono" else "chrono"
    next_amount_sort = "amount_asc" if sort_key == "amount_desc" else "amount_desc"
    next_kind_sort = "chrono" if sort_key == "kind" else "kind"
    next_party_sort = "chrono" if sort_key == "party" else "party"

    header_sort_qs = {
        "date": _ledger_query({"sort": next_date_sort, "page": 1}),
        "amount": _ledger_query({"sort": next_amount_sort, "page": 1}),
        "kind": _ledger_query({"sort": next_kind_sort, "page": 1}),
        "party": _ledger_query({"sort": next_party_sort, "page": 1}),
    }

    ctx = {
        "method": method,
        "method_label": method_label,
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "q": q,
        "kind": kind,
        "flow": flow,
        "sort_key": sort_key,
        "min_amount": min_amount_s,
        "max_amount": max_amount_s,
        "ledger_kind_choices": LEDGER_KIND_CHOICES,
        "ledger_sort_choices": LEDGER_SORT_CHOICES,
        "summary": summary,
        "inflows_by_method": inflows_by_method,
        "ledger_note": (
            "الإجماليات في الأعلى وفي تذييل الجدول لجميع السطور بعد تطبيق الفترة والفلاتر والبحث. "
            "الرصيد التراكمي يُحسب بالترتيب المعروض حالياً في الجدول."
        ),
        "header_sort_qs": header_sort_qs,
    }
    ctx.update(paginate_queryset(request, row_dicts))
    page_obj = ctx["page_obj"]
    if page_obj.object_list:
        page_in = sum((r["amount"] for r in page_obj if r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
        page_out = sum((r["amount"] for r in page_obj if not r["flow_in"]), Decimal("0")).quantize(Decimal("0.01"))
    else:
        page_in = page_out = Decimal("0")
    ctx["page_totals"] = {
        "total_in": page_in,
        "total_out": page_out,
        "net": (page_in - page_out).quantize(Decimal("0.01")),
    }
    ctx["show_page_totals"] = page_obj.paginator.num_pages > 1 and page_obj.paginator.count > 0

    ctx["last_running"] = row_dicts[-1]["running"] if row_dicts else Decimal("0")

    return render(request, "reports/payment_channel_ledger.html", ctx)


@login_required
def payment_boxes_report(request):
    """تقرير الصناديق: وارد/صادر لكل طريقة دفع عبر فترة."""
    from apps.reports.payment_boxes import build_payment_boxes_report

    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    q = (request.GET.get("q") or "").strip()
    pay_method = (request.GET.get("pay_method") or "").strip()
    valid_pm = {r["code"] for r in load_payment_method_rows()}
    if pay_method and pay_method not in valid_pm:
        pay_method = ""

    report = build_payment_boxes_report(
        date_from=d_from,
        date_to=d_to,
        payment_method=pay_method or None,
        q=q,
    )

    pay_method_options = [
        {"code": r["code"], "label": r.get("label_ar") or r.get("label") or r["code"]}
        for r in load_payment_method_rows()
        if r["ledger"] in ("cash", "bank")
    ]

    def _ledger_url(code: str, flow: str) -> str:
        base = reverse("shell:payment_channel_ledger")
        params = (
            f"method={code}&date_from={d_from.isoformat()}&date_to={d_to.isoformat()}"
            f"&flow={flow}&from=payment_boxes"
        )
        return f"{base}?{params}"

    rows_with_urls = []
    for row in report["rows"]:
        rows_with_urls.append(
            {
                **row,
                "inflow_url": _ledger_url(row["code"], "in"),
                "outflow_url": _ledger_url(row["code"], "out"),
            }
        )

    return render(
        request,
        "reports/payment_boxes.html",
        {
            "date_from": d_from.isoformat(),
            "date_to": d_to.isoformat(),
            "q": q,
            "pay_method": pay_method,
            "pay_method_options": pay_method_options,
            "rows": rows_with_urls,
            "totals": report["totals"],
            "opening_note": report["opening_note"],
            "has_opening_session": report["has_opening_session"],
            "filters_open": bool(q or pay_method),
        },
    )


@login_required
def treasury_vouchers_report(request):
    """سندات الخزينة الموحّدة من سجل التدقيق — مع فلترة حسب النوع والفترة."""
    today = date.today()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    try:
        d_from = date.fromisoformat(date_from) if date_from else today.replace(day=1)
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        d_to = today

    from apps.core.list_filters import get_search_q

    kind = (request.GET.get("v") or "all").strip().lower()
    q = get_search_q(request)

    from apps.reports.services import collect_treasury_voucher_rows, treasury_voucher_totals

    rows = collect_treasury_voucher_rows(d_from=d_from, d_to=d_to, kind=kind, q=q)

    ctx = {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "kind_filter": kind,
        "q": q,
        "filters_open": bool(q or kind not in ("", "all")),
        "voucher_totals": treasury_voucher_totals(rows),
    }
    ctx.update(paginate_queryset(request, rows))
    return render(request, "reports/treasury_vouchers.html", ctx)
