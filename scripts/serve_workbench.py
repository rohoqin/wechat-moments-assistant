#!/usr/bin/env python3
"""启动仅本机访问的朋友圈编辑工作台。"""

import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from urllib.parse import parse_qs, urlsplit

from archive_moment import DEFAULT_REPO
from workflow import ConflictError, WorkflowStore


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class WorkbenchHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_content(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send_content(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def valid_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.json({"ok": False, "error": "只接受本机访问"}, 403)
        request = urlsplit(self.path)
        parts = request.path.strip("/").split("/")
        store = self.server.store
        try:
            if parts == [""]:
                return self.send_content((WEB_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            if parts in [["app.js"], ["style.css"]]:
                mime = "text/javascript" if parts[0].endswith(".js") else "text/css"
                return self.send_content((WEB_ROOT / parts[0]).read_bytes(), mime + "; charset=utf-8")
            if parts == ["api", "posts"]:
                return self.json({"ok": True, "data": store.list()})
            if len(parts) >= 3 and parts[:2] == ["api", "posts"]:
                post = store.get(parts[2])
                if len(parts) == 3:
                    return self.json({"ok": True, "data": post})
                if len(parts) == 4 and parts[3] == "image-request":
                    revisions = parse_qs(request.query).get("revision", [])
                    if len(revisions) != 1:
                        raise ValueError("缺少当前修订号，请刷新后再配图")
                    return self.json({"ok": True, "data": store.image_request(post["id"], int(revisions[0]))})
                if len(parts) == 4 and parts[3] == "text":
                    revisions = parse_qs(request.query).get("revision", [])
                    if revisions and (len(revisions) != 1 or int(revisions[0]) != post["revision"]):
                        raise ConflictError("文案已经更新，请刷新后重新下载")
                    return self.send_content(post["body"].encode(), "text/plain; charset=utf-8")
                if len(parts) == 5 and parts[3] == "images":
                    image = next((i for i in post["images"] if i["id"] == parts[4]), None)
                    if image:
                        path = Path(image["file"]).resolve()
                        if path.is_relative_to(store.repo_root / "output") and path.is_file():
                            mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix]
                            return self.send_content(path.read_bytes(), mime)
            return self.json({"ok": False, "error": "找不到这个页面或文件"}, 404)
        except ConflictError as error:
            return self.json({"ok": False, "error": str(error)}, 409)
        except FileNotFoundError as error:
            return self.json({"ok": False, "error": str(error)}, 404)
        except (ValueError, KeyError) as error:
            return self.json({"ok": False, "error": str(error)}, 400)

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if not self.valid_host() or (origin and origin not in allowed):
            return self.json({"ok": False, "error": "不接受其他网站发起的修改请求"}, 403)
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            return self.json({"ok": False, "error": "请求必须是 JSON"}, 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 28 * 1024 * 1024:
                raise ValueError("请求为空或超过大小限制")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("请求应包含对象")
            parts = urlsplit(self.path).path.strip("/").split("/")
            store = self.server.store
            if parts == ["api", "posts"]:
                post = store.create(data.get("title", "未命名朋友圈"), data.get("body", ""), data.get("style", "auto"))
                return self.json({"ok": True, "data": post}, 201)
            if len(parts) >= 3 and parts[:2] == ["api", "posts"]:
                post_id = parts[2]
                if len(parts) == 3:
                    if "revision" not in data:
                        raise ValueError("缺少修订号，请刷新后再保存")
                    post = store.update(post_id, data["revision"], **{k: data[k] for k in ["body", "title", "style", "active_version"] if k in data})
                    return self.json({"ok": True, "data": post})
                if len(parts) == 4 and parts[3] == "confirm":
                    if "revision" not in data:
                        raise ValueError("缺少修订号，请刷新后再确认")
                    return self.json({"ok": True, "data": store.confirm(post_id, data["revision"])})
                if len(parts) == 4 and parts[3] == "images":
                    request = store.image_request(post_id)
                    image_data = base64.b64decode(data.get("data", ""), validate=True)
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "image"
                        path.write_bytes(image_data)
                        post = store.attach_image(post_id, data.get("request_id"), path,
                                                  data.get("prompt") or request["prompt"],
                                                  data.get("actual_style") or "用户导入")
                    return self.json({"ok": True, "data": post})
            return self.json({"ok": False, "error": "找不到这个操作"}, 404)
        except ConflictError as error:
            return self.json({"ok": False, "error": str(error)}, 409)
        except FileNotFoundError as error:
            return self.json({"ok": False, "error": str(error)}, 404)
        except (ValueError, KeyError, TypeError, OSError) as error:
            return self.json({"ok": False, "error": str(error)}, 400)


def make_server(repo_root=DEFAULT_REPO, obsidian_dir=None, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), WorkbenchHandler)
    server.store = WorkflowStore(repo_root, obsidian_dir)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--obsidian-dir", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.repo_root, args.obsidian_dir, args.port)
    print(f"朋友圈工作台：http://127.0.0.1:{server.server_port}", flush=True)
    print(f"文案仓库：{server.store.repo_root}", flush=True)
    print(f"Obsidian：{server.store.obsidian_dir or '由 .moments.local/obsidian-dir 配置'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
