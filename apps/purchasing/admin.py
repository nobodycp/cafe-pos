from django.contrib import admin

from apps.purchasing.models import PurchaseInvoice, PurchaseLine, Supplier, SupplierLedgerEntry, SupplierPayment


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "phone", "balance", "is_active")


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "supplier", "total", "payment_status", "created_at")
    inlines = [PurchaseLineInline]


@admin.register(PurchaseLine)
class PurchaseLineAdmin(admin.ModelAdmin):
    list_display = ("purchase", "product", "quantity", "unit_cost", "line_total")


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(admin.ModelAdmin):
    list_display = ("supplier", "amount", "method", "created_at")


@admin.register(SupplierLedgerEntry)
class SupplierLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("supplier", "entry_type", "amount", "created_at")
