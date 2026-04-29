from django import forms

from apps.core.payment_methods import get_payment_method_choices
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
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    payment_method = forms.ChoiceField(
        choices=[],
        label="طريقة الدفع",
        widget=forms.Select(attrs={"class": "form-input"}),
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
        # الرواتب والسلف تُسجَّل فقط من شاشة الموظفين (مصروف رواتب تلقائياً)
        self.fields["category"].queryset = ExpenseCategory.objects.exclude(
            code=ExpenseCategory.Code.SALARIES
        ).order_by("code")
        self.fields["payment_method"].choices = get_payment_method_choices()


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ["code", "name_ar", "name_en"]
        widgets = {
            "code": forms.Select(attrs={"class": "form-input"}),
            "name_ar": forms.TextInput(attrs={"class": "form-input"}),
            "name_en": forms.TextInput(attrs={"class": "form-input"}),
        }
