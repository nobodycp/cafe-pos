"""مسح بيانات التشغيل للاختبار — يحافظ على المستخدمين والهجرات وContentType والصلاحيات وإعدادات الـ POS.

يمكن تمرير مفاتيح «احتفاظ» اختيارية (منتجات، موظفين، عملاء، موردين، طرق دفع) لتفريغ الباقي فقط.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Set

from django.db import connection, transaction

logger = logging.getLogger(__name__)

# لا تُحذف: هيكل Django، المستخدمون، إعدادات المقهى/النظام
# (جداول auth_user_* الربطية تُفرغ مع بقاء المستخدمين)
EXCLUDED_TABLES = frozenset(
    {
        "django_migrations",
        "django_content_type",
        "auth_permission",
        "auth_user",
        "core_possettings",
    }
)

# مفاتيح POST المسموحة → جداول لا تُفرغ عند تفعيل الخيار
PRESERVE_OPTION_TABLES: dict[str, frozenset[str]] = {
    "catalog": frozenset(
        {
            "catalog_category",
            "catalog_unit",
            "catalog_product",
            "catalog_recipeline",
            "catalog_productmodifiergroup",
            "catalog_productmodifieroption",
        }
    ),
    "employees": frozenset({"payroll_employee"}),
    "customers": frozenset({"contacts_customer"}),
    "suppliers": frozenset({"purchasing_supplier"}),
    "payment_methods": frozenset({"core_paymentmethod"}),
}

ALLOWED_PRESERVE_KEYS = frozenset(PRESERVE_OPTION_TABLES.keys())

PRESERVE_LABELS_AR: dict[str, str] = {
    "catalog": "المنتجات (تصنيفات، وحدات، أصناف، معادلات تصنيع، معدّلات)",
    "employees": "بيانات الموظفين الأساسية (الأسماء والأجور — تُصفّر أرصدة الجاري)",
    "customers": "بطاقات العملاء (يُصفّر حقل الرصيد الظاهر)",
    "suppliers": "بطاقات الموردين (يُصفّر حقل الرصيد الظاهر)",
    "payment_methods": "طرق الدفع في الإعدادات",
}


def resolve_preserve_tables(preserve_keys: Iterable[str]) -> frozenset[str]:
    tables: Set[str] = set(EXCLUDED_TABLES)
    for k in preserve_keys:
        if k in PRESERVE_OPTION_TABLES:
            tables |= PRESERVE_OPTION_TABLES[k]
    return frozenset(tables)


def wipe_runtime_tables(preserve_keys: Iterable[str] | None = None) -> dict:
    """
    يفرّغ جداول التطبيق ما عدا EXCLUDED_TABLES وأي جداول ضمن خيارات الاحتفاظ.

    preserve_keys: قائمة من المفاتيح المسموحة (مثل catalog, employees, …).
    إذا فارغة أو None: نفس السلوك السابق (تفريغ كامل لكل الجداول عدا المستثنى دائماً).
    """
    keys = frozenset(k for k in (preserve_keys or ()) if k in ALLOWED_PRESERVE_KEYS)
    preserve_tables = resolve_preserve_tables(keys)
    vendor = connection.vendor
    with transaction.atomic():
        if vendor == "sqlite":
            n = _wipe_sqlite(preserve_tables)
        elif vendor == "postgresql":
            n = _wipe_postgresql(preserve_tables)
        else:
            raise NotImplementedError(
                "تفريغ البيانات من الإعدادات يدعم حالياً SQLite و PostgreSQL فقط. "
                f"المحرك الحالي: {vendor}"
            )
        _post_wipe_normalize(keys)
    return {
        "tables_cleared": n,
        "vendor": vendor,
        "preserve_keys": sorted(keys),
    }


def _pre_wipe_nullify_fks(preserve_tables: Set[str], wipe_tables: Set[str]) -> None:
    """تفادي أخطاء FK عند الإبقاء على جداول مرتبطة بجداول ستُفرغ."""
    with connection.cursor() as c:
        if "catalog_product" in preserve_tables and "purchasing_supplier" in wipe_tables:
            c.execute(
                "UPDATE catalog_product SET commission_vendor_id = NULL WHERE commission_vendor_id IS NOT NULL"
            )
        if "purchasing_supplier" in preserve_tables and "contacts_customer" in wipe_tables:
            c.execute(
                "UPDATE purchasing_supplier SET linked_customer_id = NULL WHERE linked_customer_id IS NOT NULL"
            )


def _post_wipe_normalize(preserve_keys: frozenset[str]) -> None:
    """بعد التفريغ: تسوية أرصدة ظاهرة على سجلات أُبقيت دون دفاترها التفصيلية."""
    if "customers" in preserve_keys:
        try:
            from apps.contacts.models import Customer

            Customer.objects.all().update(balance=0)
        except Exception:
            logger.exception("post_wipe customer balance reset")
    if "suppliers" in preserve_keys:
        try:
            from apps.purchasing.models import Supplier

            Supplier.objects.all().update(balance=0)
        except Exception:
            logger.exception("post_wipe supplier balance reset")
    if "employees" in preserve_keys:
        try:
            from apps.payroll.models import Employee

            Employee.objects.all().update(
                work_days_balance=0,
                work_hours_balance=0,
                advance_balance=0,
                store_purchases_balance=0,
                net_balance=0,
            )
        except Exception:
            logger.exception("post_wipe employee balances reset")


def _list_user_tables_sqlite() -> List[str]:
    with connection.cursor() as c:
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        )
        return [row[0] for row in c.fetchall() if row[0] not in EXCLUDED_TABLES]


def _wipe_sqlite(preserve_tables: frozenset[str]) -> int:
    all_names = _list_user_tables_sqlite()
    wipe = [n for n in all_names if n not in preserve_tables]
    if not wipe:
        return 0
    wipe_set = set(wipe)
    preserve_set = set(preserve_tables)
    _pre_wipe_nullify_fks(preserve_set, wipe_set)
    qn = connection.ops.quote_name
    with connection.cursor() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        for name in wipe:
            c.execute(f"DELETE FROM {qn(name)}")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            ph = ",".join(["?"] * len(wipe))
            c.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({ph})", wipe)
        except Exception:
            logger.debug("sqlite_sequence cleanup skipped", exc_info=True)
    return len(wipe)


def _list_user_tables_postgresql() -> List[str]:
    with connection.cursor() as c:
        c.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename NOT LIKE 'pg\\_%%' ESCAPE '\\'
            """
        )
        return [row[0] for row in c.fetchall() if row[0] not in EXCLUDED_TABLES]


def _wipe_postgresql(preserve_tables: frozenset[str]) -> int:
    all_names = _list_user_tables_postgresql()
    wipe = sorted(n for n in all_names if n not in preserve_tables)
    if not wipe:
        return 0
    wipe_set = set(wipe)
    preserve_set = set(preserve_tables)
    _pre_wipe_nullify_fks(preserve_set, wipe_set)
    qn = connection.ops.quote_name
    stmt = "TRUNCATE " + ", ".join(qn(n) for n in wipe) + " RESTART IDENTITY CASCADE"
    with connection.cursor() as c:
        c.execute(stmt)
    return len(wipe)
