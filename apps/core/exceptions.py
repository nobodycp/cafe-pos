"""أخطاء أعمال قابلة للعرض للمستخدم — للاستخدام التدريجي في الخدمات."""

from __future__ import annotations

from typing import Optional


class BusinessError(Exception):
    """خطأ منطق أعمال مع رسالة للمستخدم (وكود اختياري للواجهة/API)."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        self.message = message
        self.code = code
        super().__init__(message)
