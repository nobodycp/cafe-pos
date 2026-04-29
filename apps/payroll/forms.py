from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.payroll.models import Employee


class EmployeeForm(forms.ModelForm):
    salary_amount = forms.DecimalField(
        min_value=Decimal("0"),
        label="قيمة الراتب",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )

    class Meta:
        model = Employee
        fields = ["name_ar", "name_en", "pay_type", "salary_amount", "is_active"]
        widgets = {
            "name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "الاسم بالعربي"}),
            "name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Name in English"}),
            "pay_type": forms.Select(attrs={"class": "form-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["salary_amount"].initial = self.instance.pay_amount

    def save(self, commit=True):
        obj = super().save(commit=False)
        amount = self.cleaned_data.get("salary_amount") or Decimal("0")
        obj.daily_wage = Decimal("0")
        obj.hourly_wage = Decimal("0")
        obj.monthly_salary = Decimal("0")
        if obj.pay_type == Employee.PayType.HOURLY:
            obj.hourly_wage = amount
            obj.work_days_balance = Decimal("0")
        elif obj.pay_type == Employee.PayType.MONTHLY:
            obj.monthly_salary = amount
            obj.work_days_balance = Decimal("0")
            obj.work_hours_balance = Decimal("0")
        else:
            obj.daily_wage = amount
            obj.work_hours_balance = Decimal("0")
        if commit:
            obj.save()
        return obj


class EmployeeCreateForm(EmployeeForm):
    class Meta(EmployeeForm.Meta):
        fields = ["name_ar", "name_en", "pay_type", "salary_amount"]


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
    amount = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        max_digits=14,
        decimal_places=2,
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
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

    def __init__(self, *args, pay_type=Employee.PayType.DAILY, **kwargs):
        self.pay_type = pay_type
        super().__init__(*args, **kwargs)
        if pay_type == Employee.PayType.DAILY:
            self.fields["hours_count"].widget = forms.HiddenInput()
            self.fields["amount"].widget = forms.HiddenInput()
        elif pay_type == Employee.PayType.HOURLY:
            self.fields["days_count"].widget = forms.HiddenInput()
            self.fields["amount"].widget = forms.HiddenInput()
        elif pay_type == Employee.PayType.MONTHLY:
            self.fields["days_count"].widget = forms.HiddenInput()
            self.fields["hours_count"].widget = forms.HiddenInput()

    def clean(self):
        data = super().clean()
        d = data.get("days_count")
        h = data.get("hours_count")
        d = Decimal("0") if d is None else Decimal(str(d))
        h = Decimal("0") if h is None else Decimal(str(h))
        if d < 0 or h < 0:
            raise ValidationError("القيم يجب أن تكون موجبة أو صفراً.")
        amount = data.get("amount")
        amount = Decimal("0") if amount is None else Decimal(str(amount))
        if self.pay_type == Employee.PayType.MONTHLY:
            if amount <= 0:
                raise ValidationError("أدخل مبلغ الراتب المصروف.")
        elif self.pay_type == Employee.PayType.DAILY and d <= 0:
            raise ValidationError("أدخل عدد أيام أكبر من صفر.")
        elif self.pay_type == Employee.PayType.HOURLY and h <= 0:
            raise ValidationError("أدخل عدد ساعات أكبر من صفر.")
        data["days_count"] = d
        data["hours_count"] = h
        data["amount"] = amount
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
