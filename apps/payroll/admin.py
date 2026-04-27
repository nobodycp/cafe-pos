from django.contrib import admin

from apps.payroll.models import Employee, EmployeeAdvance, EmployeeCafePurchase, EmployeeSalaryPayout


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "daily_wage", "net_balance", "is_active")


@admin.register(EmployeeAdvance)
class EmployeeAdvanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "amount", "created_at")


@admin.register(EmployeeSalaryPayout)
class EmployeeSalaryPayoutAdmin(admin.ModelAdmin):
    list_display = ("employee", "amount", "days_count", "created_at")


@admin.register(EmployeeCafePurchase)
class EmployeeCafePurchaseAdmin(admin.ModelAdmin):
    list_display = ("employee", "amount", "created_at")
