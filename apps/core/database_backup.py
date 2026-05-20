"""تصدير واستيراد نسخة احتياطية من ملف SQLite — للإعدادات فقط."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

SQLITE_MAGIC = b"SQLite format 3\x00"
ALLOWED_UPLOAD_SUFFIXES = frozenset({".sqlite3", ".db", ".sqlite"})
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB


class DatabaseBackupError(Exception):
    """خطأ يُعرض للمستخدم في الواجهة."""


def sqlite_backend_enabled() -> bool:
    return settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


def database_vendor_label() -> str:
    return connection.vendor


def resolve_sqlite_db_path() -> Optional[Path]:
    """المسار المطلق لملف SQLite المُعدّ في الإعدادات، أو None إن لم يكن SQLite."""
    if not sqlite_backend_enabled():
        return None
    raw = settings.DATABASES["default"]["NAME"]
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path.resolve()


def backup_export_filename() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"cafe_backup_{ts}.sqlite3"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _validate_upload_name(name: str) -> None:
    suffix = Path(name or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise DatabaseBackupError(
            "امتداد الملف غير مقبول. ارفع ملف SQLite بامتداد .sqlite3 أو .db أو .sqlite."
        )


def _read_header(source: BinaryIO, nbytes: int = 16) -> bytes:
    if hasattr(source, "seek") and hasattr(source, "tell"):
        pos = source.tell()
        try:
            source.seek(0)
            return source.read(nbytes)
        finally:
            source.seek(pos)
    data = source.read(nbytes)
    if hasattr(source, "seek"):
        try:
            source.seek(0)
        except (OSError, ValueError):
            pass
    return data


def _is_sqlite_bytes(header: bytes) -> bool:
    return header[:16] == SQLITE_MAGIC


def validate_sqlite_upload(uploaded_file) -> None:
    """يتحقق من الامتداد والحجم ورأس ملف SQLite."""
    _validate_upload_name(getattr(uploaded_file, "name", "") or "")
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise DatabaseBackupError("حجم الملف كبير جداً (الحد الأقصى 512 ميجابايت).")
    header = _read_header(uploaded_file)
    if not _is_sqlite_bytes(header):
        raise DatabaseBackupError("الملف المرفوع ليس قاعدة بيانات SQLite صالحة.")
    uploaded_file.seek(0)


def _validate_sqlite_path(path: Path) -> None:
    with path.open("rb") as f:
        if not _is_sqlite_bytes(f.read(16)):
            raise DatabaseBackupError("الملف ليس قاعدة بيانات SQLite صالحة.")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as e:
        raise DatabaseBackupError("تعذّر فتح ملف SQLite للتحقق.") from e


def _write_upload_to_temp(uploaded_file) -> Path:
    total = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite3")
    tmp_path = Path(tmp.name)
    try:
        for chunk in uploaded_file.chunks():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise DatabaseBackupError("حجم الملف كبير جداً (الحد الأقصى 512 ميجابايت).")
            tmp.write(chunk)
        tmp.flush()
    finally:
        tmp.close()
    return tmp_path


def import_sqlite_database(uploaded_file) -> dict:
    """
    يستبدل ملف SQLite الحالي بملف مرفوع بعد نسخ احتياطي للملف الحالي.
    يُغلق اتصال Django قبل النسخ.
    """
    db_path = resolve_sqlite_db_path()
    if db_path is None:
        raise NotImplementedError(
            "استيراد قاعدة البيانات من الواجهة متاح فقط مع SQLite. "
            f"المحرك الحالي: {database_vendor_label()}. "
            "لـ PostgreSQL استخدم pg_dump و pg_restore من الطرفية."
        )

    validate_sqlite_upload(uploaded_file)
    tmp_path = _write_upload_to_temp(uploaded_file)
    backup_path: Optional[Path] = None
    try:
        _validate_sqlite_path(tmp_path)
        connection.close()
        if db_path.exists():
            backup_path = Path(f"{db_path}.bak.{_timestamp()}")
            if backup_path.resolve().parent != db_path.parent:
                raise DatabaseBackupError("مسار النسخة الاحتياطية غير آمن.")
            shutil.copy2(db_path, backup_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_path, db_path)
        logger.warning(
            "sqlite database import completed target=%s backup=%s",
            db_path.name,
            backup_path.name if backup_path else None,
        )
        return {
            "replaced": True,
            "backup_created": backup_path is not None,
            "backup_filename": backup_path.name if backup_path else "",
        }
    finally:
        tmp_path.unlink(missing_ok=True)
        connection.close()


def open_export_file():
    """يفتح ملف SQLite للتصدير؛ يُغلق الاتصال أولاً."""
    db_path = resolve_sqlite_db_path()
    if db_path is None:
        raise NotImplementedError(
            "تصدير قاعدة البيانات من الواجهة متاح فقط مع SQLite. "
            f"المحرك الحالي: {database_vendor_label()}."
        )
    if not db_path.is_file():
        raise DatabaseBackupError("ملف قاعدة البيانات غير موجود على الخادم.")
    connection.close()
    return db_path.open("rb"), backup_export_filename()
