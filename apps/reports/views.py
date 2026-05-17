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
    from apps.contacts.models import Customer

    open_s = SessionService.get_open_session()
    closed = WorkSession.objects.filter(status=WorkSession.Status.CLOSED).order_by("-closed_at")[:14]

    session_ctx = []
    for ws in [open_s] if open_s else []:
        session_ctx.append(_session_block(ws))
    for ws in closed:
        session_ctx.append(_session_block(ws))

    inv_val = sum(
        (b.quantity_on_hand * b.average_cost for b in StockBalance.objects.select_related("product")),
        Decimal("0"),
    )

    today = date.today()
    yesterday = today - timedelta(days=1)

    today_invs = SaleInvoice.objects.filter(is_cancelled=False, created_at__date=today)
    today_agg = today_invs.aggregate(
        total_sales=Sum("total"),
        total_profit=Sum("total_profit"),
        count=Count("id"),
    )
    today_sales = today_agg["total_sales"] or Decimal("0")
    today_profit = today_agg["total_profit"] or Decimal("0")
    today_count = today_agg["count"] or 0
    today_avg = (today_sales / today_count).quantize(Decimal("0.01")) if today_count > 0 else Decimal("0")

    yesterday_agg = SaleInvoice.objects.filter(
        is_cancelled=False, created_at__date=yesterday
    ).aggregate(total_sales=Sum("total"), total_profit=Sum("total_profit"), count=Count("id"))
    yesterday_sales = yesterday_agg["total_sales"] or Decimal("0")

    from apps.billing.models import SaleInvoiceLine
    top_products = (
        SaleInvoiceLine.objects.filter(
            invoice__is_cancelled=False,
            invoice__created_at__date=today,
        )
        .values("product__name_ar")
        .annotate(total_qty=Sum("quantity"), total_revenue=Sum("line_subtotal"))
        .order_by("-total_qty")[:5]
    )

    chart_labels = []
    chart_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_sales = SaleInvoice.objects.filter(
            is_cancelled=False, created_at__date=d
        ).aggregate(s=Sum("total"))["s"] or Decimal("0")
        chart_labels.append(d.strftime("%m/%d"))
        chart_data.append(float(day_sales))

    from apps.inventory.services import low_stock_alert_queryset

    low_stock = low_stock_alert_queryset().count()

    unpaid_customers = Customer.objects.filter(balance__gt=0, is_active=True).count()
    total_receivable = Customer.objects.filter(balance__gt=0, is_active=True).aggregate(s=Sum("balance"))["s"] or Decimal("0")

    return render(request, "reports/dashboard.html", {
        "session_blocks": session_ctx,
        "inventory_valuation": inv_val,
        "today_sales": today_sales,
        "today_profit": today_profit,
        "today_count": today_count,
        "today_avg": today_avg,
        "yesterday_sales": yesterday_sales,
        "top_products": top_products,
        "chart_labels": chart_labels,
        "chart_data": chart_data,
        "chart_labels_json": json.dumps(chart_labels, ensure_ascii=False),
        "chart_data_json": json.dumps(chart_data, ensure_ascii=False),
        "low_stock_count": low_stock,
        "unpaid_customers": unpaid_customers,
        "total_receivable": total_receivable,
    })


def _session_block(ws: WorkSession):
    invs = SaleInvoice.objects.filter(work_session=ws, is_cancelled=False)
    totals = invs.aggregate(
        revenue=Sum("total"),
        profit=Sum("total_profit"),
        cost=Sum("total_cost"),
    )
    pay = (
        InvoicePayment.objects.filter(invoice__work_session=ws)
        .values("method")
        .annotate(s=Sum("amount"))
    )
    pay_map = {r["code"]: Decimal("0") for r in load_payment_method_rows()}
    pay_map.setdefault("bank", Decimal("0"))
    for p in pay:
        m = p["method"]
        if m not in pay_map:
            pay_map[m] = Decimal("0")
        pay_map[m] = p["s"] or Decimal("0")
    exp = Expense.objects.filter(work_session=ws).aggregate(s=Sum("amount"))
    return {
        "session": ws,
        "invoice_count": invs.count(),
        "revenue": totals["revenue"] or Decimal("0"),
        "profit": totals["profit"] or Decimal("0"),
        "cost": totals["cost"] or Decimal("0"),
        "payments": pay_map,
        "expenses": exp["s"] or Decimal("0"),
    }


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

    agg = invoices.aggregate(
        total_sales=Sum("total"),
        total_profit=Sum("total_profit"),
        count=Count("id"),
    )

    invoice_list = []
    for inv in invoices:
        pays = [p.method for p in inv.payments.all()]
        methods = set(pays)
        method_labels = []
        label_map = payment_method_label_map()
        for m in methods:
            method_labels.append(label_map.get(m, m))
        table_name = ""
        if inv.order_id and inv.order.table_session_id and inv.order.table_session.dining_table_id:
            table_name = inv.order.table_session.dining_table.name_ar
        invoice_list.append({
            "invoice": inv,
            "table_name": table_name,
            "payment_methods": "، ".join(method_labels) if method_labels else "—",
        })

    return render(request, "reports/daily_sales.html", {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "invoices": invoice_list,
        "total_sales": agg["total_sales"] or Decimal("0"),
        "total_profit": agg["total_profit"] or Decimal("0"),
        "invoice_count": agg["count"] or 0,
    })


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

    employees = Employee.objects.filter(is_active=True).order_by("name_ar")
    rows = []
    grand_adv = grand_pay = grand_cafe = Decimal("0")
    for emp in employees:
        adv = (
            EmployeeAdvance.objects.filter(
                employee=emp,
                created_at__date__gte=d_from,
                created_at__date__lte=d_to,
            ).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )
        pay = (
            EmployeeSalaryPayout.objects.filter(
                employee=emp,
                created_at__date__gte=d_from,
                created_at__date__lte=d_to,
            ).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )
        caf = (
            EmployeeCafePurchase.objects.filter(
                employee=emp,
                created_at__date__gte=d_from,
                created_at__date__lte=d_to,
            ).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )
        grand_adv += adv
        grand_pay += pay
        grand_cafe += caf
        rows.append({
            "employee": emp,
            "advances": adv,
            "payouts": pay,
            "cafe": caf,
            "row_total": adv + pay + caf,
        })

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

    expenses = Expense.objects.filter(
        expense_date__gte=d_from,
        expense_date__lte=d_to,
    )

    by_category = (
        expenses.values("category__name_ar")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )

    by_method = (
        expenses.values("payment_method")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    method_label_map = {"cash": "نقدي", "bank": "بنك"}
    method_breakdown = []
    for row in by_method:
        method_breakdown.append({
            "label": method_label_map.get(row["payment_method"], row["payment_method"]),
            "total": row["total"] or Decimal("0"),
        })

    grand_total = expenses.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    return render(request, "reports/expense_report.html", {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "by_category": by_category,
        "method_breakdown": method_breakdown,
        "grand_total": grand_total,
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


def _treasury_audit_voucher_row_date(payload: dict, log: AuditLog) -> date:
    raw = payload.get("voucher_date")
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    ca = log.created_at
    if timezone.is_aware(ca):
        return ca.astimezone(timezone.get_current_timezone()).date()
    return ca.date()


def _treasury_audit_payload_amount(payload: dict) -> Decimal:
    try:
        return Decimal(str(payload.get("amount") or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _treasury_audit_matches_kind_filter(payload: dict, kind: str) -> bool:
    kind = (kind or "all").strip().lower()
    if kind in ("", "all"):
        return True
    vt = (payload.get("voucher_type") or "").strip().lower()
    pt = (payload.get("party_type") or "").strip().lower()
    if kind == "receipt":
        return vt == "receipt"
    if kind == "disbursement":
        return vt == "disbursement"
    if kind == "customer":
        return pt == "customer"
    if kind == "supplier":
        return pt == "supplier"
    if kind == "employee":
        return pt == "employee"
    if kind == "expense":
        return pt == "expense"
    return True


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

    kind = (request.GET.get("v") or "all").strip().lower()

    rows = []
    for log in (
        AuditLog.objects.filter(action=TREASURY_VOUCHER_AUDIT_ACTION)
        .select_related("user")
        .order_by("-created_at")
    ):
        payload = dict(log.payload or {})
        vd = _treasury_audit_voucher_row_date(payload, log)
        if not (d_from <= vd <= d_to):
            continue
        if not _treasury_audit_matches_kind_filter(payload, kind):
            continue
        rows.append({"log": log, "payload": payload, "voucher_date": vd})

    total_receipt = Decimal("0")
    total_disbursement = Decimal("0")
    for row in rows:
        p = row["payload"]
        if p.get("cancelled"):
            continue
        amt = _treasury_audit_payload_amount(p)
        vt = (p.get("voucher_type") or "").strip().lower()
        if vt == "receipt":
            total_receipt += amt
        elif vt == "disbursement":
            total_disbursement += amt
    total_net = (total_receipt - total_disbursement).quantize(Decimal("0.01"))

    ctx = {
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "kind_filter": kind,
        "voucher_totals": {
            "receipt": total_receipt,
            "disbursement": total_disbursement,
            "net": total_net,
        },
    }
    ctx.update(paginate_queryset(request, rows))
    return render(request, "reports/treasury_vouchers.html", ctx)
