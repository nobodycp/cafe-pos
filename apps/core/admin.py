from django.contrib import admin

from apps.core.models import AuditLog, IdSequence, PosSettings, WorkSession


@admin.register(PosSettings)
class PosSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "default_tax_percent",
        "default_service_charge_percent",
        "kitchen_auto_print",
    )


@admin.register(WorkSession)
class WorkSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "opened_by", "created_at", "closed_at", "opening_cash", "closing_cash")
    list_filter = ("status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "user", "model_label", "object_pk", "created_at")
    search_fields = ("action", "model_label", "object_pk")


@admin.register(IdSequence)
class IdSequenceAdmin(admin.ModelAdmin):
    list_display = ("key", "value")
