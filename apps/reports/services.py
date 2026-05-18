"""بناء بيانات التقارير — منطق الاستعلامات بعيداً عن طبقة HTTP."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.billing.models import InvoicePayment, SaleInvoice, SaleInvoiceLine
from apps.core.models import AuditLog, WorkSession
from apps.core.payment_methods import load_payment_method_rows, payment_method_label_map
from apps.core.services import SessionService
from apps.core.treasury_services import TREASURY_VOUCHER_AUDIT_ACTION
from apps.expenses.models import Expense
from apps.inventory.models import StockBalance


def session_block(ws: WorkSession) -> dict:
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


def build_dashboard_context() -> dict:
    from apps.contacts.models import Customer
    from apps.inventory.services import low_stock_alert_queryset

    open_s = SessionService.get_open_session()
    closed = WorkSession.objects.filter(status=WorkSession.Status.CLOSED).order_by("-closed_at")[:14]

    session_ctx = []
    for ws in [open_s] if open_s else []:
        session_ctx.append(session_block(ws))
    for ws in closed:
        session_ctx.append(session_block(ws))

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

    low_stock = low_stock_alert_queryset().count()
    unpaid_customers = Customer.objects.filter(balance__gt=0, is_active=True).count()
    total_receivable = Customer.objects.filter(balance__gt=0, is_active=True).aggregate(s=Sum("balance"))["s"] or Decimal("0")

    return {
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
    }


def build_daily_sales_invoice_list(page_obj) -> list:
    label_map = payment_method_label_map()
    invoice_list = []
    for inv in page_obj:
        pays = [p.method for p in inv.payments.all()]
        methods = set(pays)
        method_labels = [label_map.get(m, m) for m in methods]
        table_name = ""
        if inv.order_id and inv.order.table_session_id and inv.order.table_session.dining_table_id:
            table_name = inv.order.table_session.dining_table.name_ar
        invoice_list.append({
            "invoice": inv,
            "table_name": table_name,
            "payment_methods": "، ".join(method_labels) if method_labels else "—",
        })
    return invoice_list


def build_payroll_report_rows(d_from: date, d_to: date) -> tuple:
    from apps.payroll.models import Employee, EmployeeAdvance, EmployeeCafePurchase, EmployeeSalaryPayout

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
    return rows, grand_adv, grand_pay, grand_cafe


def build_expense_report_breakdown(d_from: date, d_to: date) -> dict:
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
    return {
        "by_category": by_category,
        "method_breakdown": method_breakdown,
        "grand_total": grand_total,
    }


def treasury_audit_voucher_row_date(payload: dict, log: AuditLog) -> date:
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


def treasury_audit_payload_amount(payload: dict) -> Decimal:
    try:
        return Decimal(str(payload.get("amount") or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def treasury_audit_matches_kind_filter(payload: dict, kind: str) -> bool:
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


def collect_treasury_voucher_rows(*, d_from: date, d_to: date, kind: str, q: str) -> list:
    rows = []
    for log in (
        AuditLog.objects.filter(action=TREASURY_VOUCHER_AUDIT_ACTION)
        .select_related("user")
        .order_by("-created_at")
    ):
        payload = dict(log.payload or {})
        vd = treasury_audit_voucher_row_date(payload, log)
        if not (d_from <= vd <= d_to):
            continue
        if not treasury_audit_matches_kind_filter(payload, kind):
            continue
        if q:
            hay = " ".join(
                str(x)
                for x in (
                    log.pk,
                    payload.get("note"),
                    payload.get("description"),
                    payload.get("party_name"),
                    payload.get("voucher_number"),
                )
                if x
            ).lower()
            if q.lower() not in hay:
                continue
        rows.append({"log": log, "payload": payload, "voucher_date": vd})
    return rows


def treasury_voucher_totals(rows: list) -> dict:
    total_receipt = Decimal("0")
    total_disbursement = Decimal("0")
    for row in rows:
        p = row["payload"]
        if p.get("cancelled"):
            continue
        amt = treasury_audit_payload_amount(p)
        vt = (p.get("voucher_type") or "").strip().lower()
        if vt == "receipt":
            total_receipt += amt
        elif vt == "disbursement":
            total_disbursement += amt
    total_net = (total_receipt - total_disbursement).quantize(Decimal("0.01"))
    return {
        "receipt": total_receipt,
        "disbursement": total_disbursement,
        "net": total_net,
    }
