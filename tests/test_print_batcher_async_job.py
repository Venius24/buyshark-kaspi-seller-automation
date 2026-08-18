"""
Склейка выполняется фоновой задачей, а не в открытом HTTP-запросе.

Партия в 1200 накладных считается ~55с на проде, а перед сервисом стоит
Cloudflare с жёстким лимитом 100с: он обрывал соединение и отдавал в браузер
HTML-страницу ошибки, на которой фронт спотыкался с "Unexpected token '<'".
Теперь POST /process сразу возвращает job_id, а клиент опрашивает статус.
"""
import io
import os
import sys
import time
import unittest
import zipfile
from unittest.mock import patch

sys.path.append(os.getcwd())

import fitz
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import print_batcher as pb_router
from backend.security.store_context import (
    StoreContext,
    get_store_context,
    get_store_context_billed,
)

STORE_A = "store-A"
STORE_B = "store-B"


def _ctx(store_id):
    async def _override():
        return StoreContext(token="tok", merchant_id="m1", store_id=store_id)
    return _override


def _waybill_zip(count=2):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(count):
            doc = fitz.open()
            page = doc.new_page(width=290, height=417)
            page.insert_text((20, 40), f"Заказ 1 034 00{i}000-1", fontsize=11)
            z.writestr(f"KASPI_SHOP-10300000{i}.pdf", doc.tobytes())
            doc.close()
    return buf.getvalue()


def _wait_for_job(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/print-batcher/process-status/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "processing":
            return body
        time.sleep(0.1)
    raise AssertionError("задача не завершилась за отведённое время")


class PrintBatcherAsyncJobTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[get_store_context_billed] = _ctx(STORE_A)
        app.dependency_overrides[get_store_context] = _ctx(STORE_A)
        pb_router._JOBS.clear()

    def tearDown(self):
        app.dependency_overrides.pop(get_store_context_billed, None)
        app.dependency_overrides.pop(get_store_context, None)
        pb_router._JOBS.clear()

    def _submit(self, data=None, **form):
        payload = {
            "sort_preset": "filename",
            "output_layout": "thermal_75_120",
            "deduplicate_history": "false",
            "force_reprint": "true",
        }
        payload.update(form)
        return self.client.post(
            "/api/print-batcher/process",
            files={"file": ("waybill.zip", data if data is not None else _waybill_zip(), "application/zip")},
            data=payload,
        )

    def test_process_returns_job_id_immediately(self):
        resp = self._submit()
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "processing")
        self.assertTrue(body["job_id"])

    def test_job_completes_and_returns_manifest(self):
        job_id = self._submit().json()["job_id"]
        body = _wait_for_job(self.client, job_id)
        self.assertEqual(body["status"], "done", body)
        self.assertEqual(body["manifest"]["total_pages"], 2)
        self.assertIsNone(body["detail"])

    def test_broken_archive_reports_error_through_job(self):
        """Ошибка обработки не должна теряться: она приезжает в статусе задачи."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("KASPI_SHOP-1030000001.pdf", "это не pdf".encode("utf-8"))
        job_id = self._submit(data=buf.getvalue()).json()["job_id"]
        body = _wait_for_job(self.client, job_id)
        self.assertEqual(body["status"], "error")
        self.assertIn("не удалось прочитать", body["detail"])

    def test_status_of_foreign_store_job_is_not_exposed(self):
        job_id = self._submit().json()["job_id"]
        _wait_for_job(self.client, job_id)

        app.dependency_overrides[get_store_context] = _ctx(STORE_B)
        resp = self.client.get(f"/api/print-batcher/process-status/{job_id}")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_job_returns_404(self):
        resp = self.client.get("/api/print-batcher/process-status/deadbeef")
        self.assertEqual(resp.status_code, 404)

    def test_rejects_unsupported_file_type(self):
        resp = self.client.post(
            "/api/print-batcher/process",
            files={"file": ("waybill.txt", b"nope", "text/plain")},
            data={"output_layout": "thermal_75_120"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_progress_is_reported_while_processing(self):
        seen = []

        real_cb_factory = pb_router._make_progress_cb

        def _spy(job_id):
            cb = real_cb_factory(job_id)

            def _wrapped(stage, done, total):
                seen.append((stage, done, total))
                cb(stage, done, total)
            return _wrapped

        with patch.object(pb_router, "_make_progress_cb", _spy):
            job_id = self._submit(data=_waybill_zip(3)).json()["job_id"]
            _wait_for_job(self.client, job_id)

        self.assertTrue(seen, "прогресс ни разу не отчитался")
        stages = {s for s, _, _ in seen}
        self.assertIn("Чтение накладных", stages)
        self.assertIn("Компоновка страниц", stages)
        for _, done, total in seen:
            self.assertLessEqual(done, total)

    def test_expired_jobs_are_pruned(self):
        job_id = self._submit().json()["job_id"]
        _wait_for_job(self.client, job_id)

        pb_router._JOBS[job_id]["updated_at"] = time.time() - pb_router._JOB_TTL_SECONDS - 1
        self._submit()  # любой новый запуск подчищает протухшие
        self.assertNotIn(job_id, pb_router._JOBS)


if __name__ == "__main__":
    unittest.main()
