"""مسح بيانات التشغيل للاختبار — يحافظ على المستخدمين والهجرات وContentType والصلاحيات وإعدادات الـ POS."""

from __future__ import annotations

import logging
from typing import List

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


def wipe_runtime_tables() -> dict:
    """
    يفرّغ كل جداول التطبيق تقريباً ما عدا المستثنيات أعلاه.
    يعيد dict يحوي tables_cleared و vendor.
    """
    vendor = connection.vendor
    with transaction.atomic():
        if vendor == "sqlite":
            n = _wipe_sqlite()
        elif vendor == "postgresql":
            n = _wipe_postgresql()
        else:
            raise NotImplementedError(
                "تفريغ البيانات من الإعدادات يدعم حالياً SQLite و PostgreSQL فقط. "
                f"المحرك الحالي: {vendor}"
            )
    return {"tables_cleared": n, "vendor": vendor}


def _list_user_tables_sqlite() -> List[str]:
    with connection.cursor() as c:
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        )
        return [row[0] for row in c.fetchall() if row[0] not in EXCLUDED_TABLES]


def _wipe_sqlite() -> int:
    names = _list_user_tables_sqlite()
    if not names:
        return 0
    qn = connection.ops.quote_name
    with connection.cursor() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        for name in names:
            c.execute(f"DELETE FROM {qn(name)}")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            ph = ",".join(["?"] * len(names))
            c.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({ph})", names)
        except Exception:
            logger.debug("sqlite_sequence cleanup skipped", exc_info=True)
    return len(names)


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


def _wipe_postgresql() -> int:
    names = _list_user_tables_postgresql()
    if not names:
        return 0
    qn = connection.ops.quote_name
    # جدول واحد مع CASCADE يعالج التبعيات بين جداول التطبيق
    stmt = "TRUNCATE " + ", ".join(qn(n) for n in names) + " RESTART IDENTITY CASCADE"
    with connection.cursor() as c:
        c.execute(stmt)
    return len(names)
