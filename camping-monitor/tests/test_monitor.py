"""모의 페이지 + 가짜 ntfy 서버로 판별 로직과 알림 흐름을 검증한다.

  python -m unittest discover -s tests -v
"""
import json
import sys
import threading
import unittest
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

import monitor  # noqa: E402
from common import launch_browser  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
SENT = []


class FakeNtfy(BaseHTTPRequestHandler):
    def do_POST(self):
        SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class MonitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = serve(partial(Quiet, directory=str(FIX)))
        cls.ntfy = serve(FakeNtfy)
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.web.shutdown()
        cls.ntfy.shutdown()

    def cfg(self, page):
        c = json.loads((Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8"))
        c["page_url"] = f"http://127.0.0.1:{self.web.server_port}/{page}.html?checkin={{checkin}}"
        c["ntfy_server"] = f"http://127.0.0.1:{self.ntfy.server_port}"
        c["_dir"] = str(Path(__file__).parent)
        return c

    def check(self, page):
        return monitor.check_dom(self.cfg(page), self.browser)

    # --- 판별 정확도 ---
    def test_all_closed(self):
        r = self.check("closed")
        self.assertTrue(r["ok"])
        self.assertEqual([i["status"] for i in r["items"]], ["closed", "closed"])
        self.assertEqual(r["available"], [])

    def test_partially_open(self):
        r = self.check("open")
        self.assertEqual(r["available"], ["B구역 데크"])
        self.assertEqual(len(r["items"]), 2)  # 내부 태그 li(전기/개수대)나 상단 메뉴는 카드로 잡히지 않음

    def test_page_level_closed_message(self):
        r = self.check("page_closed")
        self.assertTrue(r["ok"])
        self.assertTrue(r["page_closed"])
        self.assertEqual(r["available"], [])

    def test_unrecognized_page_is_error_not_closed(self):
        r = self.check("broken")
        self.assertFalse(r["ok"])  # 차단/오류 페이지를 '마감'으로 오판해 상태를 지우면 안 됨

    # --- 알림 흐름: 마감 → 열림(알림) → 유지(무알림) → 추가 열림(알림) → 마감 → 다시 열림(재알림) ---
    def test_notification_sequence(self):
        SENT.clear()
        state = {"available": []}
        cfg = self.cfg("closed")
        expected_sends = [("closed", 0), ("open", 1), ("open", 0), ("open_both", 1), ("closed", 0), ("open", 1)]
        for page, n in expected_sends:
            before = len(SENT)
            monitor.process(cfg, self.check(page), state)
            self.assertEqual(len(SENT) - before, n, f"{page}: 알림 수")
        self.assertEqual(len(SENT), 3)
        first = SENT[0]
        self.assertIn("B구역 데크", first["message"])
        self.assertEqual(first["click"], monitor.page_url(cfg))
        self.assertEqual(first["topic"], cfg["ntfy_topic"])
        self.assertIn("A구역", SENT[1]["message"])
        self.assertNotIn("새로 열림: A구역 오토캠핑, B구역", SENT[1]["message"])  # B는 이미 알렸으므로 새로 열림에 없음

    # --- API 모드 경로 파서 ---
    def test_get_path(self):
        data = {"data": {"rooms": [{"name": "A", "stock": 0}, {"name": "B", "stock": 2}]}}
        self.assertEqual([r["name"] for r in monitor.get_path(data, "data.rooms[]")], ["A", "B"])
        self.assertEqual(monitor.get_path(data["data"]["rooms"][1], "stock"), [2])


if __name__ == "__main__":
    unittest.main()
