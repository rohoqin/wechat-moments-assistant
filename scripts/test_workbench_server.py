import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

try:
    from serve_workbench import make_server
except ImportError:
    make_server = None


class WorkbenchServerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(make_server, "缺少可实际访问的本地编辑页面与接口")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = make_server(self.root / "repo", self.root / "vault", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            return response.status, json.loads(payload) if response.headers.get_content_type() == "application/json" else payload

    def test_page_and_persistent_editing_flow(self):
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("朋友圈工作台".encode(), page)
        class Options(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = []

            def handle_starttag(self, tag, attrs):
                if tag == "option":
                    self.values.append(dict(attrs).get("value"))
        options = Options()
        options.feed(page.decode())
        self.assertIn("水彩", options.values)
        status, result = self.request("/api/posts", {"title": "测试稿", "body": "正文原文", "style": "水彩"})
        self.assertEqual(status, 201)
        post = result["data"]
        status, confirmed = self.request(f"/api/posts/{post['id']}/confirm", {"revision": post["revision"]})
        self.assertEqual(status, 200)
        self.assertTrue(confirmed["data"]["archive"]["ok"])
        status, reloaded = self.request(f"/api/posts/{post['id']}")
        self.assertEqual(reloaded["data"]["body"], "正文原文")
        self.assertEqual(reloaded["data"]["confirmed_version"], 1)

    def test_stale_revision_returns_conflict(self):
        _, created = self.request("/api/posts", {"title": "测试稿", "body": "原文"})
        post = created["data"]
        self.request(f"/api/posts/{post['id']}", {"revision": post["revision"], "body": "新稿"})
        status, result = self.request(f"/api/posts/{post['id']}", {"revision": post["revision"], "body": "过期稿"})
        self.assertEqual(status, 409)
        self.assertFalse(result["ok"])

    def test_image_and_text_requests_are_bound_to_visible_revision(self):
        _, created = self.request("/api/posts", {"title": "测试稿", "body": "旧版"})
        post = created["data"]
        base = f"/api/posts/{post['id']}"
        _, confirmed = self.request(base + "/confirm", {"revision": post["revision"]})
        visible = confirmed["data"]["revision"]
        status, _ = self.request(base + f"/image-request?revision={visible}")
        self.assertEqual(status, 200)
        _, updated = self.request(base, {"revision": visible, "body": "新版"})
        _, confirmed = self.request(base + "/confirm", {"revision": updated["data"]["revision"]})
        for action in ("image-request", "text"):
            status, result = self.request(base + f"/{action}?revision={visible}")
            self.assertEqual(status, 409)
            self.assertFalse(result["ok"])
        status, versioned = self.request(base + f"/text?revision={confirmed['data']['revision']}")
        self.assertEqual(status, 200)
        self.assertEqual(versioned.decode(), "新版")

    def test_foreign_origin_cannot_write(self):
        status, _ = self.request("/api/posts", {"title": "外部请求", "body": "不应保存"}, {"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        self.assertFalse((self.root / "repo" / "output").exists())

    def test_invalid_title_returns_json_error(self):
        for title in [None, [], 12]:
            status, result = self.request("/api/posts", {"title": title, "body": "正文"})
            self.assertEqual(status, 400)
            self.assertFalse(result["ok"])
        self.assertFalse((self.root / "repo" / "output").exists())

    def test_invalid_json_and_unknown_files_do_not_expose_local_files(self):
        status, _ = self.request("/api/posts/../../etc/passwd")
        self.assertIn(status, [400, 404])
        status, _ = self.request("/scripts/archive_moment.py")
        self.assertEqual(status, 404)
        req = urllib.request.Request(self.url + "/api/posts", data=b"{", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(req)
        self.assertEqual(result.exception.code, 400)
        result.exception.close()


if __name__ == "__main__":
    unittest.main()
