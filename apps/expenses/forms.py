import json
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.core.payment_methods import get_payment_method_codes, load_payment_method_rows
from apps.expenses.models import ExpenseCategory


class ExpenseForm(forms.Form):
    category = forms.ModelChoiceField(
        queryset=ExpenseCategory.objects.none(),
        label="التصنيف",
        widget=forms.Select(attrs={"class": "form-input"}),
    )
    amount = forms.DecimalField(
        min_value=0.01,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01", "id": "id_amount"}),
    )
    payment_method = forms.CharField(
        widget=forms.HiddenInput(attrs={"id": "expense-pay-method"}),
        required=False,
        initial="",
    )
    payment_splits_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "expense-payment-splits-json", "autocomplete": "off"}),
        initial="",
    )
    use_payment_splits = forms.BooleanField(
        required=False,
        label="تقسيم الدفع على أكثر من طريقة (مثال: جزء كاش وجزء بنك)",
        widget=forms.CheckboxInput(attrs={"id": "expense-split-pay-toggle", "class": "rounded border-gray-400"}),
    )
    expense_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-input"}),
        label="التاريخ",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-input"}),
        label="ملاحظات",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.exclude(
            code=ExpenseCategory.Code.SALARIES
        ).order_by("code")
        if not self.data:
            rows = load_payment_method_rows()
            pm_init = (self.initial.get("payment_method") or "").strip()
            if pm_init == "split":
                self.fields["use_payment_splits"].initial = True
            elif rows and not pm_init:
                self.fields["payment_method"].initial = rows[0]["code"]

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd

        codes = get_payment_method_codes()
        if not codes:
            raise ValidationError("فعّل طريقة دفع واحدة على الأقل من الإعدادات ← طرق الدفع.")

        amount = cd.get("amount")
        if amount is None:
            return cd
        amt_q = amount.quantize(Decimal("0.01"))
        use_splits = bool(cd.get("use_payment_splits"))

        if use_splits:
            raw = (cd.get("payment_splits_json") or "").strip()
            if not raw:
                raise ValidationError({"payment_splits_json": "أدخل أسطر تقسيم الدفع أو ألغِ «دفع مختلط»."})
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise ValidationError({"payment_splits_json": "بيانات تقسيم الدفع غير صالحة."})
            if not isinstance(data, list) or len(data) > 16:
                raise ValidationError({"payment_splits_json": "عدد أسطر الدفع غير صالح."})

            lines: list[tuple[str, Decimal]] = []
            for item in data:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    code = str(item[0] or "").strip().lower()
                    amt_raw = item[1]
                elif isinstance(item, dict):
                    code = str(item.get("method") or "").strip().lower()
                    amt_raw = item.get("amount")
                else:
                    continue
                if not code or code not in codes:
                    raise ValidationError({"payment_splits_json": "طريقة دفع غير صالحة في التقسيم."})
                try:
                    a = Decimal(str(amt_raw).replace(",", ".")).quantize(Decimal("0.01"))
                except Exception:
                    raise ValidationError({"payment_splits_json": "مبلغ غير صالح في التقسيم."})
                if a <= 0:
                    continue
                lines.append((code, a))

            if not lines:
                raise ValidationError({"payment_splits_json": "أضف سطر دفع واحداً على الأقل بمبلغ أكبر من صفر."})

            total = sum(a for _, a in lines).quantize(Decimal("0.01"))
            if total != amt_q:
                raise ValidationError(
                    {"payment_splits_json": "مجموع أسطر الدفع يجب أن يساوي مبلغ المصروف."}
                )

            cd["payment_method"] = "split"
            cd["payment_splits_json"] = json.dumps([[c, str(a)] for c, a in lines], ensure_ascii=False)
        else:
            m = (cd.get("payment_method") or "").strip().lower()
            if not m or m not in codes:
                raise ValidationError({"payment_method": "اختر طريقة الدفع."})
            cd["payment_method"] = m
            cd["payment_splits_json"] = ""

        return cd


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["code", "name_ar", "name_en"]
        widgets = {
            "code": forms.Select(attrs={"class": "form-input"}),
            "name_ar": forms.TextInput(attrs={"class": "form-input"}),
            "name_en": forms.TextInput(attrs={"class": "form-input"}),
        }
