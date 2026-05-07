from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseModelFormSet, modelformset_factory

from apps.accounting.models import Account, JournalEntry, JournalLine


class JournalEntryEditForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ("date", "description")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-input"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 3}),
        }


class JournalLineEditForm(forms.ModelForm):
    class Meta:
        model = JournalLine
        fields = ("account", "debit", "credit", "description")
        widgets = {
            "account": forms.Select(attrs={"class": "form-input"}),
            "debit": forms.NumberInput(attrs={"class": "form-input tabular-nums", "step": "0.01", "min": "0"}),
            "credit": forms.NumberInput(attrs={"class": "form-input tabular-nums", "step": "0.01", "min": "0"}),
            "description": forms.TextInput(attrs={"class": "form-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True).order_by("code")
        if not self.instance.pk:
            self.empty_permitted = True

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd
        if cd.get("DELETE"):
            return cd
        account = cd.get("account")
        debit = cd.get("debit") if cd.get("debit") is not None else Decimal("0")
        credit = cd.get("credit") if cd.get("credit") is not None else Decimal("0")
        debit = Decimal(debit).quantize(Decimal("0.01"))
        credit = Decimal(credit).quantize(Decimal("0.01"))
        if account is None and debit == 0 and credit == 0:
            return cd
        if account is None:
            raise ValidationError("اختر الحساب لهذا السطر.")
        if debit > 0 and credit > 0:
            raise ValidationError("السطر لا يجمع بين مدين ودائن.")
        if debit <= 0 and credit <= 0:
            raise ValidationError("أدخل مبلغاً في المدين أو الدائن.")
        return cd


class BaseJournalLineFormSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        total_d = Decimal("0")
        total_c = Decimal("0")
        active = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            account = form.cleaned_data.get("account")
            debit = form.cleaned_data.get("debit")
            credit = form.cleaned_data.get("credit")
            d = Decimal(debit or 0).quantize(Decimal("0.01"))
            c = Decimal(credit or 0).quantize(Decimal("0.01"))
            if account is None and d == 0 and c == 0:
                continue
            active += 1
            total_d += d
            total_c += c
        if active < 2:
            raise ValidationError("القيد يحتاج سطرين على الأقل بمبالغ صحيحة.")
        if abs(total_d - total_c) > Decimal("0.02"):
            raise ValidationError(
                f"القيد غير متوازن: مجموع المدين {total_d} لا يساوي مجموع الدائن {total_c}."
            )


def make_journal_line_formset():
    return modelformset_factory(
        JournalLine,
        form=JournalLineEditForm,
        formset=BaseJournalLineFormSet,
        extra=1,
        can_delete=True,
        min_num=0,
    )
