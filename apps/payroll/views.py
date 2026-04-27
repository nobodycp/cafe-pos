import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from apps.payroll.forms import (
    EmployeeAdvanceForm,
    EmployeeCafePurchaseForm,
    EmployeeCreateForm,
    EmployeeForm,
    EmployeePayoutForm,
    EmployeeWorkDaysForm,
)
from apps.payroll.models import Employee, EmployeeAdvance, EmployeeCafePurchase, EmployeeSalaryPayout

logger = logging.getLogger(__name__)


def _post_advance_journal(advance, user=None):
    """Debit EMPLOYEE_ADVANCES, Credit CASH when an advance is given."""
    try:
        from apps.accounting.services import _build_entry, _add_line, _get_account, _d
        amt = _d(advance.amount)
        if amt <= 0:
            return
        entry = _build_entry(
            description=f"سلفة موظف: {advance.employee.name_ar} — {amt}",
            reference_type="payroll.EmployeeAdvance",
            reference_pk=str(advance.pk),
            user=user,
        )
        entry.save()
        _add_line(entry, _get_account("EMPLOYEE_ADVANCES"), debit=amt, desc=f"سلفة {advance.employee.name_ar}")
        _add_line(entry, _get_account("CASH"), credit=amt, desc="صرف سلفة نقداً")
    except Exception:
        logger.exception("Failed to post advance journal entry")


def _post_advance_deduction_journal(employee, deduction_amount, user=None):
    """Debit EXP_SALARIES, Credit EMPLOYEE_ADVANCES when advance is deducted from salary."""
    try:
        from apps.accounting.services import _build_entry, _add_line, _get_account, _d
        amt = _d(deduction_amount)
        if amt <= 0:
            return
        entry = _build_entry(
            description=f"خصم سلفة من راتب: {employee.name_ar} — {amt}",
            reference_type="payroll.EmployeeSalaryPayout",
            reference_pk=str(employee.pk),
            user=user,
        )
        entry.save()
        _add_line(entry, _get_account("EXP_SALARIES"), debit=amt, desc=f"خصم سلفة {employee.name_ar}")
        _add_line(entry, _get_account("EMPLOYEE_ADVANCES"), credit=amt, desc=f"تسوية سلفة {employee.name_ar}")
    except Exception:
        logger.exception("Failed to post advance deduction journal entry")


def _recalc_balance(emp):
    emp.net_balance = (emp.work_days_balance * emp.daily_wage) - emp.advance_balance - emp.store_purchases_balance
    emp.save(update_fields=["net_balance", "updated_at"])


@login_required
def employee_list(request):
    qs = Employee.objects.filter(is_active=True).order_by("name_ar")
    return render(request, "payroll/employees.html", {"employees": qs})


@login_required
def employee_detail(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    return render(
        request,
        "payroll/employee_detail.html",
        {
            "employee": emp,
            "advances": emp.advances.order_by("-created_at")[:50],
            "payouts": emp.salary_payouts.order_by("-created_at")[:50],
            "purchases": emp.cafe_purchases.order_by("-created_at")[:50],
        },
    )


@login_required
def employee_create(request):
    if request.method == "POST":
        form = EmployeeCreateForm(request.POST)
        if form.is_valid():
            emp = form.save()
            messages.success(request, f"تم إضافة الموظف «{emp.name_ar}» بنجاح")
            return redirect("payroll:employee_detail", pk=emp.pk)
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
            return redirect("payroll:employee_detail", pk=emp.pk)
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
            amount = form.cleaned_data["amount"]
            adv = EmployeeAdvance.objects.create(
                employee=emp,
                amount=amount,
                note=form.cleaned_data["note"],
            )
            emp.advance_balance = (emp.advance_balance + Decimal(str(amount))).quantize(Decimal("0.01"))
            emp.save(update_fields=["advance_balance", "updated_at"])
            _recalc_balance(emp)
            _post_advance_journal(adv, request.user)
            messages.success(request, "تم تسجيل السلفة بنجاح")
            return redirect("payroll:employee_detail", pk=emp.pk)
    else:
        form = EmployeeAdvanceForm()
    return render(request, "payroll/employee_advance_form.html", {"form": form, "employee": emp})


@login_required
@transaction.atomic
def employee_payout_create(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeePayoutForm(request.POST)
        if form.is_valid():
            days = form.cleaned_data["days_count"]
            amount = (Decimal(str(days)) * emp.daily_wage).quantize(Decimal("0.01"))
            EmployeeSalaryPayout.objects.create(
                employee=emp,
                days_count=days,
                amount=amount,
                note=form.cleaned_data["note"],
            )
            advance_deduction = Decimal("0")
            if emp.advance_balance > 0:
                advance_deduction = min(emp.advance_balance, amount)
                emp.advance_balance = (emp.advance_balance - advance_deduction).quantize(Decimal("0.01"))
            emp.work_days_balance = (emp.work_days_balance - Decimal(str(days))).quantize(Decimal("0.01"))
            emp.save(update_fields=["work_days_balance", "advance_balance", "updated_at"])
            _recalc_balance(emp)
            if advance_deduction > 0:
                _post_advance_deduction_journal(emp, advance_deduction, user=request.user)
            messages.success(request, f"تم صرف راتب {amount} بنجاح")
            return redirect("payroll:employee_detail", pk=emp.pk)
    else:
        form = EmployeePayoutForm()
    return render(
        request,
        "payroll/employee_payout_form.html",
        {"form": form, "employee": emp, "daily_wage": emp.daily_wage},
    )


@login_required
@transaction.atomic
def employee_add_days(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeWorkDaysForm(request.POST)
        if form.is_valid():
            days = form.cleaned_data["days_count"]
            emp.work_days_balance = (emp.work_days_balance + Decimal(str(days))).quantize(Decimal("0.01"))
            emp.save(update_fields=["work_days_balance", "updated_at"])
            _recalc_balance(emp)
            messages.success(request, f"تم إضافة {days} يوم عمل بنجاح")
            return redirect("payroll:employee_detail", pk=emp.pk)
    else:
        form = EmployeeWorkDaysForm()
    return render(request, "payroll/employee_add_days_form.html", {"form": form, "employee": emp})


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
            emp.store_purchases_balance = (emp.store_purchases_balance + Decimal(str(amount))).quantize(Decimal("0.01"))
            emp.save(update_fields=["store_purchases_balance", "updated_at"])
            _recalc_balance(emp)
            messages.success(request, "تم تسجيل الشراء بنجاح")
            return redirect("payroll:employee_detail", pk=emp.pk)
    else:
        form = EmployeeCafePurchaseForm()
    return render(request, "payroll/employee_cafe_purchase_form.html", {"form": form, "employee": emp})
