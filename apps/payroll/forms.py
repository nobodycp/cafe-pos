from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.payroll.models import Employee


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["name_ar", "name_en", "daily_wage", "hourly_wage", "is_active"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "daily_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "hourly_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class EmployeeCreateForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["name_ar", "name_en", "daily_wage", "hourly_wage"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "daily_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "hourly_wage": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
        }


class EmployeeAdvanceForm(forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        label="مبلغ السلفة",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeePayoutForm(forms.Form):
    days_count = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0"),
        label="عدد الأيام",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.5"}),
    )
    hours_count = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=10,
        decimal_places=2,
        initial=Decimal("0"),
        label="عدد الساعات",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.25"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )

    def clean(self):
        data = super().clean()
        d = data.get("days_count")
        h = data.get("hours_count")
        d = Decimal("0") if d is None else Decimal(str(d))
        h = Decimal("0") if h is None else Decimal(str(h))
        if d < 0 or h < 0:
            raise ValidationError("القيم يجب أن تكون موجبة أو صفراً.")
        if d <= 0 and h <= 0:
            raise ValidationError("أدخل عدد أيام و/أو ساعات أكبر من صفر.")
        data["days_count"] = d
        data["hours_count"] = h
        return data


class EmployeeWorkDaysForm(forms.Form):
    days_count = forms.DecimalField(
        min_value=Decimal("0.5"),
        label="عدد الأيام",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.5"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeeWorkHoursForm(forms.Form):
    hours_count = forms.DecimalField(
        min_value=Decimal("0.25"),
        label="عدد الساعات",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.25"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )


class EmployeeCafePurchaseForm(forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )
