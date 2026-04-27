from django.contrib import admin

from apps.accounting.models import Account, JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account", "debit", "credit", "description")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "name_en", "account_type", "system_code", "is_active")
    list_filter = ("account_type", "is_active")
    search_fields = ("code", "name_ar", "name_en", "system_code")
    ordering = ("code",)


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("entry_number", "date", "description", "reference_type", "is_reversed")
    list_filter = ("date", "reference_type", "is_reversed")
    search_fields = ("entry_number", "description", "reference_pk")
    date_hierarchy = "date"
    inlines = [JournalLineInline]
    readonly_fields = ("entry_number",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
