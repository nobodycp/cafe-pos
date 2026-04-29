from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.services import SessionService
from apps.expenses.forms import ExpenseCategoryForm, ExpenseForm
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_expense, delete_expense_permanent


@login_required
def expense_list(request):
    qs = Expense.objects.select_related("category").order_by("-expense_date", "-created_at")[:300]
    return render(request, "expenses/list.html", {"expenses": qs})


@login_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense.objects.select_related("category"), pk=pk)
    if expense.category.code == ExpenseCategory.Code.SALARIES:
        messages.error(
            request,
            "مصروف «رواتب» لا يُعدَّل يدوياً — يُدار من الموظفين (سلفة أو صرف راتب). يمكنك حذف السطر من قائمة المصروفات إن لزم.",
        )
        return redirect("expenses:list")
    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    ws = expense.work_session or SessionService.get_open_session()
                    delete_expense_permanent(expense=expense, user=request.user)
                    create_expense(
                        category=form.cleaned_data["category"],
                        amount=form.cleaned_data["amount"],
                        payment_method=form.cleaned_data["payment_method"],
                        expense_date=form.cleaned_data["expense_date"],
                        notes=form.cleaned_data["notes"],
                        work_session=ws,
                        user=request.user,
                    )
                messages.success(request, "تم تحديث المصروف.")
                return redirect("expenses:list")
            except ValueError as e:
                msg = str(e)
                if msg == "SALARIES_VIA_PAYROLL_ONLY":
                    msg = "تصنيف الرواتب غير متاح هنا — سجّل السلفة أو صرف الراتب من «الموظفون»."
                messages.error(request, msg)
    else:
        form = ExpenseForm(initial={
            "category": expense.category_id,
            "amount": expense.amount,
            "payment_method": expense.payment_method,
            "expense_date": expense.expense_date,
            "notes": expense.notes,
        })
    return render(request, "expenses/expense_form.html", {"form": form, "edit_expense": expense})


@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    try:
        delete_expense_permanent(expense=expense, user=request.user)
        messages.success(request, "تم حذف المصروف.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("expenses:list")


@login_required
def expense_create(request):
    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            session = SessionService.get_open_session()
            try:
                create_expense(
                    category=form.cleaned_data["category"],
                    amount=form.cleaned_data["amount"],
                    payment_method=form.cleaned_data["payment_method"],
                    expense_date=form.cleaned_data["expense_date"],
                    notes=form.cleaned_data["notes"],
                    work_session=session,
                    user=request.user,
                )
                messages.success(request, "تم إضافة المصروف بنجاح.")
                return redirect("expenses:list")
            except ValueError as e:
                msg = str(e)
                if msg == "SALARIES_VIA_PAYROLL_ONLY":
                    msg = "تصنيف الرواتب غير متاح هنا — سجّل السلفة أو صرف الراتب من «الموظفون»."
                messages.error(request, msg)
    else:
        form = ExpenseForm()
    return render(request, "expenses/expense_form.html", {"form": form, "edit_expense": None})


@login_required
def expense_category_list(request):
    categories = ExpenseCategory.objects.order_by("code")
    return render(request, "expenses/category_list.html", {"categories": categories})


@login_required
def expense_category_create(request):
    if request.method == "POST":
        form = ExpenseCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة التصنيف بنجاح.")
            return redirect("expenses:categories")
    else:
        form = ExpenseCategoryForm()
    return render(request, "expenses/category_form.html", {"form": form, "edit": False})


@login_required
def expense_category_edit(request, pk):
    category = get_object_or_404(ExpenseCategory, pk=pk)
    if request.method == "POST":
        form = ExpenseCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل التصنيف بنجاح.")
            return redirect("expenses:categories")
    else:
        form = ExpenseCategoryForm(instance=category)
    return render(request, "expenses/category_form.html", {"form": form, "edit": True})
