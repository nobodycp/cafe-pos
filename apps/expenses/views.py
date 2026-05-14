from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.core.pagination import paginate_queryset
from apps.core.payment_methods import get_payment_method_codes, load_payment_method_rows
from apps.core.services import SessionService
from apps.expenses.forms import ExpenseCategoryForm, ExpenseForm
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_expense, delete_expense_permanent


def _expense_value_error_message(msg: str) -> str:
    if msg == "SALARIES_VIA_PAYROLL_ONLY":
        return "تصنيف الرواتب غير متاح هنا — سجّل السلفة أو صرف الراتب من «الموظفون»."
    if msg == "SPLITS_REQUIRED":
        return "بيانات الدفع المختلط غير مكتملة."
    if msg in ("INVALID_EXPENSE_SPLITS", "INVALID_EXPENSE_SPLITS_JSON", "INVALID_EXPENSE_SPLIT_METHOD"):
        return "بيانات تقسيم الدفع غير صالحة — أعد المحاولة أو عدّل الطرق."
    if msg in ("EXPENSE_PAYMENT_MISMATCH", "EXPENSE_PAYMENT_EMPTY"):
        return "خطأ في مطابقة مبالغ الدفع مع المبلغ — راجع أسطر الدفع."
    return msg


def _expense_form_template_ctx(form, request, edit_expense=None):
    if request.method == "POST":
        state = {
            "payment_method": (request.POST.get("payment_method") or "").strip(),
            "use_payment_splits": bool(request.POST.get("use_payment_splits")),
            "payment_splits_json": (request.POST.get("payment_splits_json") or "").strip(),
        }
    elif edit_expense:
        state = {
            "payment_method": edit_expense.payment_method,
            "use_payment_splits": edit_expense.payment_method == "split",
            "payment_splits_json": (edit_expense.payment_splits_json or "").strip()
            if edit_expense.payment_method == "split"
            else "",
        }
    else:
        state = {}
    show_split_ui = False
    if request.method == "POST" and request.POST.get("use_payment_splits"):
        show_split_ui = True
    elif edit_expense and edit_expense.payment_method == "split":
        show_split_ui = True
    return {
        "form": form,
        "edit_expense": edit_expense,
        "payment_method_rows": load_payment_method_rows(),
        "expense_form_state": state,
        "expense_split_ui_open": show_split_ui,
    }


@login_required
def expense_list(request):
    qs = Expense.objects.select_related("category").order_by("-expense_date", "-created_at")

    date_from_s = (request.GET.get("date_from") or "").strip()
    date_to_s = (request.GET.get("date_to") or "").strip()
    df = parse_date(date_from_s) if date_from_s else None
    dt = parse_date(date_to_s) if date_to_s else None
    if df and dt and df > dt:
        df, dt = dt, df
        date_from_s = df.isoformat()
        date_to_s = dt.isoformat()
    if df:
        qs = qs.filter(expense_date__gte=df)
    if dt:
        qs = qs.filter(expense_date__lte=dt)

    category_s = (request.GET.get("category") or "").strip()
    filter_category = category_s
    if category_s.isdigit():
        cid = int(category_s)
        if ExpenseCategory.objects.filter(pk=cid).exists():
            qs = qs.filter(category_id=cid)
        else:
            filter_category = ""

    pay_raw = (request.GET.get("payment") or "").strip().lower()
    codes = get_payment_method_codes()
    filter_payment = pay_raw
    if pay_raw == "split":
        qs = qs.filter(payment_method="split")
    elif pay_raw and pay_raw in codes:
        qs = qs.filter(payment_method=pay_raw)
    elif pay_raw:
        filter_payment = ""

    q_raw = (request.GET.get("q") or "").strip()[:240]
    filter_q = q_raw
    if q_raw:
        qs = qs.filter(notes__icontains=q_raw)

    totals_agg = qs.aggregate(sum_amount=Sum("amount"))
    expense_totals = {
        "sum_amount": (totals_agg["sum_amount"] or Decimal("0")).quantize(Decimal("0.01")),
    }
    ctx = {
        "expense_totals": expense_totals,
        "filter_date_from": date_from_s,
        "filter_date_to": date_to_s,
        "filter_category": filter_category,
        "filter_payment": filter_payment,
        "filter_q": filter_q,
        "filter_categories": ExpenseCategory.objects.order_by("code"),
        "payment_filter_rows": load_payment_method_rows(),
    }
    ctx.update(paginate_queryset(request, qs))
    return render(request, "expenses/list.html", ctx)


@login_required
def expense_edit(request, pk):
    expense = get_object_or_404(Expense.objects.select_related("category"), pk=pk)
    if expense.category.code == ExpenseCategory.Code.SALARIES:
        messages.error(
            request,
            "مصروف «رواتب» لا يُعدَّل يدوياً — يُدار من الموظفين (سلفة أو صرف راتب). يمكنك حذف السطر من قائمة المصروفات إن لزم.",
        )
        return redirect("shell:expenses")
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
                        payment_splits_json=form.cleaned_data.get("payment_splits_json") or "",
                    )
                messages.success(request, "تم تحديث المصروف.")
                return redirect("shell:expenses")
            except ValueError as e:
                messages.error(request, _expense_value_error_message(str(e)))
    else:
        form = ExpenseForm(initial={
            "category": expense.category_id,
            "amount": expense.amount,
            "payment_method": expense.payment_method,
            "payment_splits_json": expense.payment_splits_json or "",
            "expense_date": expense.expense_date,
            "notes": expense.notes,
        })
    return render(
        request,
        "expenses/expense_form.html",
        _expense_form_template_ctx(form, request, edit_expense=expense),
    )


@login_required
@require_POST
def expense_delete(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    try:
        delete_expense_permanent(expense=expense, user=request.user)
        messages.success(request, "تم حذف المصروف.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("shell:expenses")


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
                    payment_splits_json=form.cleaned_data.get("payment_splits_json") or "",
                )
                messages.success(request, "تم إضافة المصروف بنجاح.")
                return redirect("shell:expenses")
            except ValueError as e:
                messages.error(request, _expense_value_error_message(str(e)))
    else:
        form = ExpenseForm()
    return render(
        request,
        "expenses/expense_form.html",
        _expense_form_template_ctx(form, request, edit_expense=None),
    )


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
            return redirect("shell:expense_categories")
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
            return redirect("shell:expense_categories")
    else:
        form = ExpenseCategoryForm(instance=category)
    return render(request, "expenses/category_form.html", {"form": form, "edit": True})
