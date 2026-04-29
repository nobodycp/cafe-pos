import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import render

from apps.billing.models import InvoicePayment, SaleInvoice
from apps.core.models import WorkSession
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

    from django.db.models import F
    low_stock = StockBalance.objects.filter(
        product__is_active=True,
        product__is_stock_tracked=True,
        product__min_stock_level__gt=0,
        quantity_on_hand__lte=F("product__min_stock_level"),
    ).count()

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
    pay_map = {
        "cash": Decimal("0"),
        "bank": Decimal("0"),
        "bank_ps": Decimal("0"),
        "palpay": Decimal("0"),
        "jawwalpay": Decimal("0"),
        "credit": Decimal("0"),
    }
    for p in pay:
        m = p["method"]
        if m in pay_map:
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
        label_map = {
            "cash": "كاش",
            "bank": "شبكة (عام)",
            "bank_ps": "بنك فلسطين",
            "palpay": "بال باي",
            "jawwalpay": "جوال باي",
            "credit": "آجل",
        }
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
    from apps.catalog.models import Product

    period = request.GET.get("period", "month")
    today = date.today()
    if period == "week":
        d_from = today - timedelta(days=7)
    elif period == "year":
        d_from = today.replace(month=1, day=1)
    else:
        d_from = today.replace(day=1)

    top_sellers = (
        SaleInvoiceLine.objects.filter(
            invoice__is_cancelled=False,
            invoice__created_at__date__gte=d_from,
        )
        .values("product__pk", "product__name_ar")
        .annotate(
            total_qty=Sum("quantity"),
            total_revenue=Sum("line_subtotal"),
            total_profit=Sum("line_profit"),
        )
        .order_by("-total_qty")[:20]
    )

    sold_pks = SaleInvoiceLine.objects.filter(
        invoice__is_cancelled=False,
        invoice__created_at__date__gte=d_from,
    ).values_list("product_id", flat=True).distinct()

    slow_movers = Product.objects.filter(
        is_active=True,
        is_stock_tracked=True,
    ).exclude(
        product_type__in=[Product.ProductType.RAW, Product.ProductType.SERVICE]
    ).exclude(pk__in=sold_pks).order_by("name_ar")[:20]

    return render(request, "reports/product_movement.html", {
        "top_sellers": top_sellers,
        "slow_movers": slow_movers,
        "period": period,
        "date_from": d_from,
    })


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
    """تقرير طرق الدفع + تتبع التحويلات الإلكترونية + ملخص كاش وارد/صادر تقريبي."""
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

    inv_pay_base = InvoicePayment.objects.filter(
        invoice__is_cancelled=False,
        invoice__created_at__date__gte=d_from,
        invoice__created_at__date__lte=d_to,
    )

    payment_rows = list(
        inv_pay_base.select_related("invoice", "invoice__order").order_by("-invoice__created_at", "pk")
    )

    by_method = (
        inv_pay_base.values("method")
        .annotate(total=Sum("amount"), n=Count("id"))
        .order_by("-total")
    )

    cash_in = (
        inv_pay_base.filter(method="cash").aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    expense_cash_out = (
        Expense.objects.filter(
            expense_date__gte=d_from,
            expense_date__lte=d_to,
            payment_method="cash",
        ).aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )

    method_label = {
        "cash": "كاش",
        "bank": "شبكة عام",
        "bank_ps": "بنك فلسطين",
        "palpay": "بال باي",
        "jawwalpay": "جوال باي",
        "credit": "آجل",
    }
    for row in by_method:
        row["label"] = method_label.get(row["method"], row["method"])

    return render(
        request,
        "reports/payment_channels.html",
        {
            "date_from": d_from.isoformat(),
            "date_to": d_to.isoformat(),
            "payment_rows": payment_rows,
            "by_method": by_method,
            "cash_in": cash_in,
            "expense_cash_out": expense_cash_out,
            "cash_net_approx": cash_in - expense_cash_out,
        },
    )
