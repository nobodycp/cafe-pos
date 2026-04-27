from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.services import SessionService
from apps.expenses.forms import ExpenseCategoryForm, ExpenseForm
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_expense


@login_required
def expense_list(request):
    qs = Expense.objects.select_related("category").order_by("-expense_date", "-created_at")[:300]
    return render(request, "expenses/list.html", {"expenses": qs})


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
                messages.error(request, str(e))
    else:
        form = ExpenseForm()
    return render(request, "expenses/expense_form.html", {"form": form})


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
