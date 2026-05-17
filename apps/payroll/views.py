from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decimalutil import as_decimal
from apps.core.list_filters import get_search_q
from apps.core.pagination import paginate_queryset
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.services import SessionService
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense, delete_expense_permanent
from apps.payroll.forms import (
    EmployeeAdvanceForm,
    EmployeeCafePurchaseForm,
    EmployeeCreateForm,
    EmployeeForm,
    EmployeePayoutForm,
    EmployeeWorkDaysForm,
    EmployeeWorkHoursForm,
)
from apps.payroll.models import Employee, EmployeeAdvance, EmployeeCafePurchase, EmployeeSalaryPayout
from apps.payroll.services import recalc_employee_net_balance


def _payroll_ns(request):
    return "shell"


def _payroll_redirect(request, viewname, *args, **kwargs):
    return redirect(reverse(f"{_payroll_ns(request)}:{viewname}", args=args, kwargs=kwargs))


def _salaries_category():
    return ExpenseCategory.objects.get(code=ExpenseCategory.Code.SALARIES)


def _employee_list_hide_zero_net(request) -> bool:
    """إخفاء الموظفين ذوي صافي الرصيد = 0 (افتراضي: مفعّل)."""
    parts = request.GET.getlist("hide_zero_net")
    if not parts:
        return True
    last = (parts[-1] or "").strip().lower()
    return last not in ("0", "false", "off")


@login_required
def employee_list(request):
    qs = (
        Employee.objects.filter(is_active=True)
        .annotate(
            _employee_name_script_group=Case(
                When(name_ar__regex=r"^\s*[A-Za-z0-9]", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("_employee_name_script_group", "name_ar", "pk")
    )
    hide_zero_net = _employee_list_hide_zero_net(request)
    if hide_zero_net:
        qs = qs.exclude(net_balance=Decimal("0"))

    q = get_search_q(request) or ""
    if q:
        qs = qs.filter(
            Q(name_ar__icontains=q)
            | Q(name_en__icontains=q)
            | Q(linked_customer__phone__icontains=q)
        )

    totals_agg = qs.aggregate(sum_net_balance=Sum("net_balance"))
    totals = {
        "sum_net_balance": (totals_agg["sum_net_balance"] or Decimal("0")).quantize(Decimal("0.01")),
    }

    ctx = {
        "q": q,
        "employee_filter_hide_zero_net": hide_zero_net,
        "employee_totals": totals,
    }
    ctx.update(paginate_queryset(request, qs))
    return render(request, "payroll/employees.html", ctx)


@login_required
def employee_detail(request, pk):
    emp = get_object_or_404(Employee.objects.select_related("linked_customer"), pk=pk)
    return render(
        request,
        "payroll/employee_detail.html",
        {
            "employee": emp,
            "advances": emp.advances.select_related("linked_expense").order_by("-created_at")[:50],
            "payouts": emp.salary_payouts.select_related("linked_expense").order_by("-created_at")[:50],
            "purchases": emp.cafe_purchases.select_related("sale_invoice").order_by("-created_at")[:50],
            "debt_repayments": emp.debt_repayments.order_by("-created_at")[:50],
        },
    )


@login_required
def employee_create(request):
    if request.method == "POST":
        form = EmployeeCreateForm(request.POST)
        if form.is_valid():
            emp = form.save()
            messages.success(request, f"تم إضافة الموظف «{emp.name_ar}» بنجاح")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeCreateForm()
    return render(request, "payroll/employee_form.html", {"form": form, "title": "إضافة موظف"})


@login_required
def employee_edit(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=emp)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل بيانات الموظف بنجاح")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeForm(instance=emp)
    return render(request, "payroll/employee_form.html", {"form": form, "title": "تعديل موظف", "employee": emp})


@login_required
@transaction.atomic
def employee_advance_create(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeAdvanceForm(request.POST)
        if form.is_valid():
            amount = as_decimal(form.cleaned_data["amount"])
            note = form.cleaned_data["note"] or ""
            ws = SessionService.get_open_session()
            exp = create_expense(
                category=_salaries_category(),
                amount=amount,
                payment_method="cash",
                expense_date=timezone.localdate(),
                notes=f"سلفة موظف: {emp.name_ar}" + (f" — {note}" if note else ""),
                work_session=ws,
                user=request.user,
                allow_salary_category=True,
            )
            adv = EmployeeAdvance.objects.create(
                employee=emp,
                work_session=ws,
                amount=amount,
                note=note,
                linked_expense=exp,
            )
            emp.advance_balance = (as_decimal(emp.advance_balance) + amount).quantize(Decimal("0.01"))
            emp.save(update_fields=["advance_balance", "updated_at"])
            recalc_employee_net_balance(emp)
            messages.success(request, "تم تسجيل السلفة وإدراجها ضمن مصروفات «رواتب».")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeAdvanceForm()
    return render(request, "payroll/employee_advance_form.html", {"form": form, "employee": emp})


@login_required
@require_POST
@transaction.atomic
def employee_advance_delete(request, pk, advance_id):
    emp = get_object_or_404(Employee, pk=pk)
    adv = get_object_or_404(EmployeeAdvance, pk=advance_id, employee=emp)
    amt = as_decimal(adv.amount)
    if adv.linked_expense_id:
        delete_expense_permanent(expense=adv.linked_expense, user=request.user)
    emp.advance_balance = (as_decimal(emp.advance_balance) - amt).quantize(Decimal("0.01"))
    if emp.advance_balance < 0 and emp.advance_balance > Decimal("-0.01"):
        emp.advance_balance = Decimal("0")
    emp.save(update_fields=["advance_balance", "updated_at"])
    adv.delete()
    recalc_employee_net_balance(emp)
    messages.success(request, "تم حذف السلفة والمصروف المرتبط.")
    return _payroll_redirect(request, "employee_detail", pk=emp.pk)


@login_required
@transaction.atomic
def employee_payout_create(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeePayoutForm(request.POST, pay_type=emp.pay_type)
        if form.is_valid():
            days = as_decimal(form.cleaned_data["days_count"])
            hours = as_decimal(form.cleaned_data["hours_count"])
            if emp.pay_type == Employee.PayType.MONTHLY:
                amount = as_decimal(form.cleaned_data["amount"]).quantize(Decimal("0.01"))
            elif emp.pay_type == Employee.PayType.HOURLY:
                amount = (hours * as_decimal(emp.hourly_wage)).quantize(Decimal("0.01"))
            else:
                amount = (days * as_decimal(emp.daily_wage)).quantize(Decimal("0.01"))
            if amount <= 0:
                messages.error(request, "المبلغ المحسوب صفر.")
            elif emp.pay_type == Employee.PayType.DAILY and days > emp.work_days_balance:
                messages.error(request, "الأيام أكبر من الرصيد المستحق.")
            elif emp.pay_type == Employee.PayType.HOURLY and hours > emp.work_hours_balance:
                messages.error(request, "الساعات أكبر من الرصيد المستحق.")
            elif emp.pay_type == Employee.PayType.MONTHLY and amount > emp.net_balance:
                messages.error(request, "المبلغ أكبر من الرصيد المستحق.")
            else:
                advance_deduction = Decimal("0")
                if emp.advance_balance > 0:
                    advance_deduction = min(as_decimal(emp.advance_balance), amount)
                    emp.advance_balance = (as_decimal(emp.advance_balance) - advance_deduction).quantize(Decimal("0.01"))
                if emp.pay_type == Employee.PayType.DAILY:
                    emp.work_days_balance = (as_decimal(emp.work_days_balance) - days).quantize(Decimal("0.01"))
                elif emp.pay_type == Employee.PayType.HOURLY:
                    emp.work_hours_balance = (as_decimal(emp.work_hours_balance) - hours).quantize(Decimal("0.01"))
                emp.save(update_fields=["work_days_balance", "work_hours_balance", "advance_balance", "updated_at"])
                recalc_employee_net_balance(emp)
                net_cash = (amount - advance_deduction).quantize(Decimal("0.01"))
                linked = None
                ws = SessionService.get_open_session()
                if net_cash > 0:
                    parts = []
                    if days > 0:
                        parts.append(f"{days} يوم")
                    if hours > 0:
                        parts.append(f"{hours} ساعة")
                    linked = create_expense(
                        category=_salaries_category(),
                        amount=net_cash,
                        payment_method="cash",
                        expense_date=timezone.localdate(),
                        notes=f"صرف راتب: {emp.name_ar} ({' + '.join(parts)})" + (
                            f" — {form.cleaned_data['note']}" if form.cleaned_data.get("note") else ""
                        ),
                        work_session=ws,
                        user=request.user,
                        allow_salary_category=True,
                    )
                EmployeeSalaryPayout.objects.create(
                    employee=emp,
                    work_session=ws,
                    days_count=days,
                    hours_count=hours,
                    amount=amount,
                    advance_applied=advance_deduction,
                    note=form.cleaned_data.get("note") or "",
                    linked_expense=linked,
                )
                messages.success(request, f"تم صرف راتب {amount} بنجاح.")
                return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeePayoutForm(pay_type=emp.pay_type)
    return render(
        request,
        "payroll/employee_payout_form.html",
        {
            "form": form,
            "employee": emp,
            "daily_wage": emp.daily_wage,
            "hourly_wage": emp.hourly_wage,
        },
    )


@login_required
@require_POST
@transaction.atomic
def employee_payout_delete(request, pk, payout_id):
    emp = get_object_or_404(Employee, pk=pk)
    po = get_object_or_404(EmployeeSalaryPayout, pk=payout_id, employee=emp)
    if po.linked_expense_id:
        delete_expense_permanent(expense=po.linked_expense, user=request.user)
    emp.work_days_balance = (as_decimal(emp.work_days_balance) + as_decimal(po.days_count)).quantize(Decimal("0.01"))
    emp.work_hours_balance = (as_decimal(emp.work_hours_balance) + as_decimal(po.hours_count)).quantize(Decimal("0.01"))
    emp.advance_balance = (as_decimal(emp.advance_balance) + as_decimal(po.advance_applied)).quantize(Decimal("0.01"))
    emp.save(update_fields=["work_days_balance", "work_hours_balance", "advance_balance", "updated_at"])
    po.delete()
    recalc_employee_net_balance(emp)
    messages.success(request, "تم حذف صرف الراتب واسترجاع الأرصدة والمصروف.")
    return _payroll_redirect(request, "employee_detail", pk=emp.pk)


@login_required
@transaction.atomic
def employee_add_days(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if emp.pay_type != Employee.PayType.DAILY:
        messages.error(request, "إضافة الأيام متاحة فقط للموظف اليومي.")
        return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    if request.method == "POST":
        form = EmployeeWorkDaysForm(request.POST)
        if form.is_valid():
            days = form.cleaned_data["days_count"]
            emp.work_days_balance = (as_decimal(emp.work_days_balance) + as_decimal(days)).quantize(Decimal("0.01"))
            emp.save(update_fields=["work_days_balance", "updated_at"])
            recalc_employee_net_balance(emp)
            messages.success(request, f"تم إضافة {days} يوم عمل بنجاح")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeWorkDaysForm()
    return render(request, "payroll/employee_add_days_form.html", {"form": form, "employee": emp})


@login_required
@transaction.atomic
def employee_add_hours(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if emp.pay_type != Employee.PayType.HOURLY:
        messages.error(request, "إضافة الساعات متاحة فقط للموظف بالساعة.")
        return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    if request.method == "POST":
        form = EmployeeWorkHoursForm(request.POST)
        if form.is_valid():
            hrs = form.cleaned_data["hours_count"]
            emp.work_hours_balance = (as_decimal(emp.work_hours_balance) + as_decimal(hrs)).quantize(Decimal("0.01"))
            emp.save(update_fields=["work_hours_balance", "updated_at"])
            recalc_employee_net_balance(emp)
            messages.success(request, f"تم إضافة {hrs} ساعة عمل بنجاح")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeWorkHoursForm()
    return render(request, "payroll/employee_add_hours_form.html", {"form": form, "employee": emp})


@login_required
@transaction.atomic
def employee_cafe_purchase(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeCafePurchaseForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            EmployeeCafePurchase.objects.create(
                employee=emp,
                amount=amount,
                note=form.cleaned_data["note"],
            )
            emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) + as_decimal(amount)).quantize(Decimal("0.01"))
            emp.save(update_fields=["store_purchases_balance", "updated_at"])
            recalc_employee_net_balance(emp)
            messages.success(request, "تم تسجيل الشراء بنجاح")
            return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    else:
        form = EmployeeCafePurchaseForm()
    return render(request, "payroll/employee_cafe_purchase_form.html", {"form": form, "employee": emp})


@login_required
@require_POST
@transaction.atomic
def employee_cafe_purchase_delete(request, pk, purchase_id):
    emp = get_object_or_404(Employee, pk=pk)
    cp = get_object_or_404(EmployeeCafePurchase, pk=purchase_id, employee=emp)
    if cp.sale_invoice_id:
        messages.error(
            request,
            "لا يمكن حذف سجل مرتبط بفاتورة بيع — ألغِ الفاتورة من المحاسبة لعكس الذمة، أو سجّل سداداً من الصندوق.",
        )
        return _payroll_redirect(request, "employee_detail", pk=emp.pk)
    emp.store_purchases_balance = (as_decimal(emp.store_purchases_balance) - as_decimal(cp.amount)).quantize(Decimal("0.01"))
    if emp.store_purchases_balance < 0 and emp.store_purchases_balance > Decimal("-0.01"):
        emp.store_purchases_balance = Decimal("0")
    emp.save(update_fields=["store_purchases_balance", "updated_at"])
    cp.delete()
    recalc_employee_net_balance(emp)
    messages.success(request, "تم حذف سجل الشراء.")
    return _payroll_redirect(request, "employee_detail", pk=emp.pk)


@login_required
@require_POST
@transaction.atomic
def employee_delete(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    for adv in emp.advances.all():
        if adv.linked_expense_id:
            delete_expense_permanent(expense=adv.linked_expense, user=request.user)
    for po in emp.salary_payouts.all():
        if po.linked_expense_id:
            delete_expense_permanent(expense=po.linked_expense, user=request.user)
    name = emp.name_ar
    emp.delete()
    messages.success(request, f"تم حذف الموظف «{name}» وجميع سجلاته.")
    return _payroll_redirect(request, "employees")


@login_required
def employee_create_panel(request):
    tpl = "shell/panels/employee_create_panel.html"

    def build_context():
        form = EmployeeCreateForm(request.POST or None)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:employee_create_panel"),
            "panel_title": "إضافة موظف",
        }

    def on_valid():
        form = EmployeeCreateForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


@login_required
def employee_edit_panel(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    tpl = "shell/panels/employee_edit_panel.html"

    def build_context():
        form = EmployeeForm(request.POST or None, instance=emp)
        panelize_form(form)
        return {
            "form": form,
            "employee": emp,
            "form_action": reverse("shell:employee_edit_panel", args=[pk]),
            "panel_title": "تعديل موظف",
        }

    def on_valid():
        form = EmployeeForm(request.POST, instance=emp)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
