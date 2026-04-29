from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.contacts.models import Customer
from apps.core.models import PaymentMethod, PosSettings
from apps.core.payment_methods import get_payment_method_codes
from apps.payroll.models import Employee
from apps.purchasing.models import Supplier


class CafeInfoForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "cafe_name_ar",
            "cafe_name_en",
            "cafe_phone",
            "cafe_address",
            "cafe_tax_number",
        ]
        widgets = {
            "cafe_name_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "مقهى ..."}),
            "cafe_name_en": forms.TextInput(attrs={"class": "form-input", "placeholder": "Café ..."}),
            "cafe_phone": forms.TextInput(attrs={"class": "form-input", "placeholder": "05xxxxxxxx", "dir": "ltr"}),
            "cafe_address": forms.Textarea(attrs={"class": "form-input", "rows": 2}),
            "cafe_tax_number": forms.TextInput(attrs={"class": "form-input", "dir": "ltr"}),
        }


class CurrencyForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "currency_symbol",
            "currency_code",
            "decimal_places",
        ]
        widgets = {
            "currency_symbol": forms.TextInput(attrs={"class": "form-input", "style": "max-width:120px"}),
            "currency_code": forms.TextInput(attrs={"class": "form-input", "style": "max-width:120px", "dir": "ltr"}),
            "decimal_places": forms.NumberInput(attrs={"class": "form-input", "style": "max-width:120px", "min": 0, "max": 4}),
        }


class TaxServiceForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "default_tax_percent",
            "default_service_charge_percent",
            "tax_included_in_price",
        ]
        widgets = {
            "default_tax_percent": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "default_service_charge_percent": forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
            "tax_included_in_price": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class OrderSettingsForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "default_order_type",
            "allow_negative_stock",
            "require_customer_for_credit",
        ]
        widgets = {
            "default_order_type": forms.Select(attrs={"class": "form-input"}),
            "allow_negative_stock": forms.CheckboxInput(attrs={"class": "form-check"}),
            "require_customer_for_credit": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class PrinterForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "kitchen_auto_print",
            "printer_kitchen_label",
            "printer_receipt_label",
        ]
        widgets = {
            "kitchen_auto_print": forms.CheckboxInput(attrs={"class": "form-check"}),
            "printer_kitchen_label": forms.TextInput(attrs={"class": "form-input"}),
            "printer_receipt_label": forms.TextInput(attrs={"class": "form-input"}),
        }


class ReceiptForm(forms.ModelForm):
    class Meta:
        model = PosSettings
        fields = [
            "receipt_header",
            "receipt_footer",
            "receipt_show_tax_number",
            "allow_sale_invoice_edit",
        ]
        widgets = {
            "receipt_header": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "نص يظهر أعلى الإيصال"}),
            "receipt_footer": forms.Textarea(attrs={"class": "form-input", "rows": 3, "placeholder": "شكراً لزيارتكم ..."}),
            "receipt_show_tax_number": forms.CheckboxInput(attrs={"class": "form-check"}),
            "allow_sale_invoice_edit": forms.CheckboxInput(attrs={"class": "form-check"}),
        }


class PaymentMethodForm(forms.ModelForm):
    """إضافة/تعديل طريقة دفع من شاشة الإعدادات."""

    class Meta:
        model = PaymentMethod
        fields = ["code", "label_ar", "label_en", "ledger", "is_active", "sort_order"]
        widgets = {
            "code": forms.TextInput(
                attrs={
                    "class": "form-input font-mono text-sm",
                    "dir": "ltr",
                    "placeholder": "cash",
                    "autocomplete": "off",
                }
            ),
            "label_ar": forms.TextInput(attrs={"class": "form-input", "placeholder": "كاش"}),
            "label_en": forms.TextInput(attrs={"class": "form-input", "dir": "ltr", "placeholder": "Cash"}),
            "ledger": forms.Select(attrs={"class": "form-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check"}),
            "sort_order": forms.NumberInput(attrs={"class": "form-input", "style": "max-width:120px", "min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["code"].disabled = True

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd
        is_active = cd.get("is_active", True)
        code = (cd.get("code") or (self.instance.code if self.instance.pk else "") or "").strip().lower()
        if self.instance.pk and code == "cash" and not is_active:
            others = PaymentMethod.objects.filter(is_active=True, code="cash").exclude(pk=self.instance.pk)
            if not others.exists():
                raise ValidationError("يجب بقاء طريقة «كاش» (cash) نشطة واحدة على الأقل.")
        if not self.instance.pk:
            if code != "cash" or not is_active:
                if not PaymentMethod.objects.filter(is_active=True, code="cash").exists():
                    raise ValidationError(
                        "يجب وجود طريقة كاش نشطة (رمز cash). أضفها أولاً أو فعّل الصف الموجود.",
                    )
        return cd

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.instance.pk:
            obj.code = self.instance.code
        if commit:
            obj.save()
        return obj


class TreasuryVoucherForm(forms.Form):
    """سند موحّد: نوع الحركة قبض/صرف، وتصنيف الجهة منفصل."""

    VT_RECEIPT = "receipt"
    VT_DISBURSEMENT = "disbursement"
    PARTY_CUSTOMER = "customer"
    PARTY_SUPPLIER = "supplier"
    PARTY_EMPLOYEE = "employee"
    PARTY_EXPENSE = "expense"

    voucher_type = forms.ChoiceField(
        choices=[
            (VT_RECEIPT, "سند قبض"),
            (VT_DISBURSEMENT, "سند صرف"),
        ],
        label="نوع السند",
        widget=forms.HiddenInput(),
    )
    party_type = forms.ChoiceField(
        choices=[
            (PARTY_CUSTOMER, "عميل"),
            (PARTY_SUPPLIER, "مورد"),
            (PARTY_EMPLOYEE, "موظف"),
            (PARTY_EXPENSE, "مصاريف"),
        ],
        label="الحساب / الجهة",
        widget=forms.HiddenInput(),
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        required=False,
        label="العميل",
        widget=forms.HiddenInput(),
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.none(),
        required=False,
        label="المورد",
        widget=forms.HiddenInput(),
    )
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        label="الموظف",
        widget=forms.HiddenInput(),
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        label="المبلغ",
        widget=forms.NumberInput(attrs={"class": "form-input", "step": "0.01"}),
    )
    method = forms.CharField(
        max_length=32,
        label="طريقة الدفع / التحصيل",
        widget=forms.HiddenInput(),
    )
    note = forms.CharField(
        required=False,
        label="ملاحظة",
        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("name_ar")
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True).order_by("name_ar")
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True).order_by("name_ar")
        if not self.data:
            self.fields["voucher_type"].initial = self.VT_RECEIPT
            self.fields["party_type"].initial = self.PARTY_CUSTOMER
            self.fields["method"].initial = "cash"

    def clean_method(self):
        m = self.cleaned_data.get("method")
        if m not in get_payment_method_codes():
            raise ValidationError("طريقة دفع غير صالحة.")
        return m

    def clean(self):
        cd = super().clean()
        if not cd:
            return cd
        t = cd.get("voucher_type")
        party_type = cd.get("party_type")
        if t not in (self.VT_RECEIPT, self.VT_DISBURSEMENT):
            raise ValidationError({"voucher_type": "اختر نوع السند."})
        if party_type == self.PARTY_CUSTOMER:
            if not cd.get("customer"):
                raise ValidationError({"customer": "اختر العميل من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_SUPPLIER:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "المورد مرتبط بسند صرف."})
            if not cd.get("supplier"):
                raise ValidationError({"supplier": "اختر المورد من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_EMPLOYEE:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "الموظف مرتبط بسند صرف."})
            if not cd.get("employee"):
                raise ValidationError({"employee": "اختر الموظف من الاقتراحات بعد البحث."})
        elif party_type == self.PARTY_EXPENSE:
            if t != self.VT_DISBURSEMENT:
                raise ValidationError({"party_type": "المصاريف مرتبطة بسند صرف."})
        else:
            raise ValidationError({"party_type": "اختر الحساب أو الجهة."})
        return cd
