import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.database_backup import (
    DatabaseBackupError,
    SQLITE_MAGIC,
    import_sqlite_database,
    validate_sqlite_upload,
)


class DatabaseBackupViewTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "testdb.sqlite3"
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        self.staff = User.objects.create_user(
            username="staff_bk", password="pass-12345", is_staff=True
        )
        self.regular = User.objects.create_user(
            username="reg_bk", password="pass-12345", is_staff=False
        )

        self.db_settings = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(self.db_path),
            }
        }

    def _login_staff(self):
        self.client.login(username="staff_bk", password="pass-12345")

    def test_export_requires_staff(self):
        export_url = reverse("shell:settings_database_export")
        with self.settings(DATABASES=self.db_settings):
            self.client.login(username="reg_bk", password="pass-12345")
            resp = self.client.get(export_url)
            self.assertIn(resp.status_code, (403, 302))

            self.client.logout()
            resp = self.client.get(export_url)
            self.assertEqual(resp.status_code, 302)

    def test_export_returns_attachment(self):
        export_url = reverse("shell:settings_database_export")
        with self.settings(DATABASES=self.db_settings):
            self._login_staff()
            resp = self.client.get(export_url)
            self.assertEqual(resp.status_code, 200)
            cd = resp.get("Content-Disposition", "")
            self.assertIn("attachment", cd)
            self.assertIn("cafe_backup_", cd)
            body = b"".join(resp.streaming_content)
            self.assertTrue(body.startswith(SQLITE_MAGIC))

    def test_import_rejects_non_staff(self):
        import_url = reverse("shell:settings_database_import")
        with self.settings(DATABASES=self.db_settings):
            self.client.login(username="reg_bk", password="pass-12345")
            resp = self.client.post(
                import_url,
                {"accept_replace": "1"},
                follow=False,
            )
            self.assertIn(resp.status_code, (403, 302))

    def test_import_rejects_invalid_file(self):
        import_url = reverse("shell:settings_database_import")
        with self.settings(DATABASES=self.db_settings):
            self._login_staff()
            resp = self.client.post(
                import_url,
                {
                    "accept_replace": "1",
                    "database_file": SimpleUploadedFile("bad.txt", b"not sqlite", "text/plain"),
                },
                follow=True,
            )
            self.assertEqual(resp.status_code, 200)
            messages = [m.message for m in resp.context["messages"]]
            self.assertTrue(any("SQLite" in m or "امتداد" in m for m in messages))

    def test_validate_sqlite_upload_rejects_wrong_header(self):
        class FakeUpload:
            name = "x.sqlite3"
            size = 8

            def tell(self):
                return 0

            def seek(self, pos):
                pass

            def read(self, n):
                return b"NOTSQLIT"

        with self.assertRaises(DatabaseBackupError):
            validate_sqlite_upload(FakeUpload())


class DatabaseBackupImportUnitTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "live.sqlite3"
        sqlite3.connect(self.db_path).close()
        self.other_db = Path(self.tmp_dir) / "other.sqlite3"
        c = sqlite3.connect(self.other_db)
        c.execute("CREATE TABLE z (n INTEGER)")
        c.commit()
        c.close()

    def test_import_creates_backup_and_replaces(self):
        db_settings = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(self.db_path),
            }
        }

        data = self.other_db.read_bytes()
        upload = SimpleUploadedFile("other.sqlite3", data, "application/octet-stream")
        with self.settings(DATABASES=db_settings):
            result = import_sqlite_database(upload)
        self.assertTrue(result["backup_created"])
        backups = list(self.db_path.parent.glob("live.sqlite3.bak.*"))
        self.assertEqual(len(backups), 1)
        with self.db_path.open("rb") as f:
            self.assertTrue(f.read(16).startswith(SQLITE_MAGIC))
