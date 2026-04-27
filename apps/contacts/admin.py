from django.contrib import admin

from apps.contacts.models import Customer, CustomerLedgerEntry


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "phone", "balance", "is_active")


@admin.register(CustomerLedgerEntry)
class CustomerLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("customer", "entry_type", "amount", "created_at")
