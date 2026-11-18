#!/usr/bin/env python3
"""朋友圈草稿、版本、确认、归档与配图的可恢复工作流。"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import uuid
from zoneinfo import ZoneInfo

from archive_moment import DEFAULT_REPO, archive_text, configured_obsidian_dir, month_entry_matches


def now():
    return datetime.now(ZoneInfo("Asia/Singapore")).isoformat(timespec="seconds")


def body_hash(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ConflictError(ValueError):
    pass


class WorkflowStore:
    def __init__(self, repo_root=DEFAULT_REPO, obsidian_dir=None):
        self.repo_root = Path(repo_root).resolve()
        self.obsidian_dir = Path(obsidian_dir).resolve() if obsidian_dir is not None else None
        self.root = self.repo_root / "output" / "workbench"

    def _directory(self, post_id):
        if not isinstance(post_id, str) or not re.fullmatch(r"[a-f0-9]{32}", post_id):
            raise ValueError("文案 ID 无效")
        directory = self.root / post_id
        if not (directory / "state.json").is_file():
            raise FileNotFoundError("找不到这条文案")
        return directory

    @contextmanager
    def _edit(self, post_id, revision=None):
        directory = self._directory(post_id)
        with (directory / ".lock").open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            state = json.loads((directory / "state.json").read_text())
            if revision is not None and (type(revision) is not int or state["revision"] != revision):
                raise ConflictError("文案已在另一窗口更新，请刷新后再操作")
            state["revision"] += 1
            state["updated_at"] = now()
            yield state
            write_json(directory / "state.json", state)

    def _view(self, state):
        version = next(v for v in state["versions"] if v["number"] == state["active_version"])
        state["body"] = version["body"]
        state["status"] = "draft"
        if state["confirmed_version"] == state["active_version"]:
            state["status"] = "archived" if state["archive"] and state["archive"].get("ok") else "confirmed"
        state["current_image"] = next((i for i in reversed(state["images"])
                                       if state["status"] == "archived" and i["version"] == state["active_version"] and i["requested_style"] == state["style"]
                                       and Path(i["file"]).is_file()), None)
        if state["current_image"] and state["status"] == "archived":
            state["status"] = "illustrated"
        return state

    def create(self, title, body, style="auto"):
        self._validate_text(body, style)
        self._validate_title(title)
        post_id = uuid.uuid4().hex
        directory = self.root / post_id
        directory.mkdir(parents=True)
        stamp = now()
        state = {"schema_version": 1, "id": post_id, "title": title.strip() or "未命名朋友圈",
                 "created_at": stamp, "updated_at": stamp, "revision": 1, "active_version": 1,
                 "versions": [{"number": 1, "body": body, "sha256": body_hash(body), "created_at": stamp}],
                 "confirmed_version": None, "confirmed_date": None, "confirmed_obsidian_dir": None, "style": style,
                 "archive": None, "archive_history": [], "images": [], "image_error": None}
        write_json(directory / "state.json", state)
        return self._view(state)

    @staticmethod
    def _validate_title(title):
        if not isinstance(title, str) or len(title) > 500 or "\x00" in title:
            raise ValueError("标题须为不超过 500 字符的文本")

    @staticmethod
    def _validate_text(body, style):
        if not isinstance(body, str) or len(body) > 50000 or "\x00" in body:
            raise ValueError("正文须为不超过 50000 字符的文本")
        if not isinstance(style, str) or not style.strip() or len(style) > 200:
            raise ValueError("请填写自动或具体配图风格")

    def get(self, post_id):
        path = self._directory(post_id) / "state.json"
        return self._view(json.loads(path.read_text(encoding="utf-8")))

    def list(self):
        if not self.root.exists():
            return []
        return sorted([self.get(p.parent.name) for p in self.root.glob("*/state.json")],
                      key=lambda p: p["updated_at"], reverse=True)

    def update(self, post_id, revision, *, body=None, title=None, style=None, active_version=None):
        if type(revision) is not int:
            raise ValueError("缺少有效的修订号，请刷新后再保存")
        if title is not None:
            self._validate_title(title)
        with self._edit(post_id, revision) as state:
            current = next(v for v in state["versions"] if v["number"] == state["active_version"])
            self._validate_text(body if body is not None else current["body"], style if style is not None else state["style"])
            if body is not None and body != current["body"]:
                number = len(state["versions"]) + 1
                state["versions"].append({"number": number, "body": body, "sha256": body_hash(body), "created_at": now()})
                active_version = number
            if active_version is not None and active_version != state["active_version"]:
                if type(active_version) is not int or active_version not in [v["number"] for v in state["versions"]]:
                    raise ValueError("找不到选定版本")
                if state["archive"]:
                    state["archive_history"].append(state["archive"])
                state.update(active_version=active_version, confirmed_version=None, confirmed_date=None,
                             confirmed_obsidian_dir=None, archive=None, image_error=None)
            if title is not None:
                state["title"] = title.strip() or "未命名朋友圈"
            if style is not None:
                state["style"] = style
                state["image_error"] = None
        return self._view(state)

    def confirm(self, post_id, revision, day=None):
        if type(revision) is not int:
            raise ValueError("缺少有效的修订号，请刷新后再确认")
        if day is not None:
            day = date.fromisoformat(day).isoformat()
        with self._edit(post_id, revision) as state:
            body = next(v["body"] for v in state["versions"] if v["number"] == state["active_version"])
            if not body.strip():
                raise ValueError("正文为空，不能确认定稿")
            day = day or state["confirmed_date"] or now()[:10]
            new_confirmation = state["confirmed_version"] != state["active_version"] or state["confirmed_date"] != day
            destination = None if new_confirmation else state.get("confirmed_obsidian_dir")
            if not new_confirmation and not destination and state["archive"] and state["archive"].get("obsidian_file"):
                destination = str(Path(state["archive"]["obsidian_file"]).parent)
            if not destination:
                configured = self.obsidian_dir if self.obsidian_dir is not None else configured_obsidian_dir(self.repo_root)
                destination = str(configured.resolve()) if configured is not None else None
            if new_confirmation:
                if state["archive"]:
                    state["archive_history"].append(state["archive"])
                state.update(confirmed_version=state["active_version"], confirmed_date=day,
                             archive=None, image_error=None)
            if new_confirmation or state.get("confirmed_obsidian_dir") != destination:
                state["confirmed_obsidian_dir"] = destination
                write_json(self.root / post_id / "state.json", state)
                state["revision"] += 1
            result = archive_text(body, state["title"], day, self.repo_root, destination)
            state["archive"] = {**result, "version": state["active_version"]}
        return self._view(state)

    def _image_request(self, state):
        if state["confirmed_version"] != state["active_version"]:
            raise ValueError("请先确认当前版本定稿")
        result = state["archive"]
        if not result or not result.get("ok") or not result.get("repo_saved") or not result.get("obsidian_saved"):
            raise ValueError("两处归档尚未完成，请先补写成功再配图")
        body = next(v["body"] for v in state["versions"] if v["number"] == state["active_version"])
        text = body.rstrip("\r\n") + "\n"
        local = Path(result["repo_file"])
        monthly = Path(result["obsidian_file"])
        if not local.is_file() or local.read_bytes().decode("utf-8") != text or not monthly.is_file():
            raise ValueError("归档文件缺失或正文不一致，请重新核对归档")
        month_text = monthly.read_bytes().decode("utf-8")
        if not month_entry_matches(month_text, result["entry_id"], text):
            raise ValueError("Obsidian 中的定稿记录不完整或正文已变化")
        request_id = body_hash(f"{state['id']}\0{state['active_version']}\0{body}\0{state['style']}")
        style = "按正文主题和情绪自动选择合适风格" if state["style"] == "auto" else state["style"]
        prompt = (f"为以下已确认的朋友圈生成 1 张配图。风格：{style}。方形构图，主体清楚，手机易读。"
                  "画面表达一个主题，不把整段文案印在图上，不伪造产品截图、项目成果、客户现场或本人面孔。"
                  f"\n\n正文：\n{body}")
        return {"post_id": state["id"], "version": state["active_version"], "request_id": request_id,
                "style": state["style"], "body": body, "prompt": prompt,
                "cli_prefix": shlex.join(["python3", str(Path(__file__).resolve()), "--repo-root", str(self.repo_root),
                                          "--obsidian-dir", str(monthly.parent)]),
                "archive_directory": str(local.parent)}

    def image_request(self, post_id, revision=None):
        state = self.get(post_id)
        if revision is not None and state["revision"] != revision:
            raise ConflictError("文案已在另一窗口更新，请打开最新稿后再配图")
        return self._image_request(state)

    def attach_image(self, post_id, request_id, source, prompt, actual_style, engine="imported"):
        limit = 20 * 1024 * 1024
        with Path(source).open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit or len(data) < 32:
            raise ValueError("图片须在 20 MB 以内，且为完整图片文件")
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and data.endswith(b"IEND\xaeB`\x82"):
            extension = ".png"
        elif data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            extension = ".jpg"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP" and int.from_bytes(data[4:8], "little") + 8 == len(data):
            extension = ".webp"
        else:
            raise ValueError("请选择有效的 PNG、JPEG 或 WebP 图片")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            checked_source = directory / ("source" + extension)
            checked_source.write_bytes(data)
            output = directory / "decoded.png"
            try:
                decoded = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(checked_source)],
                                         capture_output=True, text=True, timeout=15)
                dimensions = re.findall(r"pixel(?:Width|Height): (\d+)", decoded.stdout)
                if decoded.returncode or len(dimensions) != 2 or any(int(value) <= 0 for value in dimensions):
                    raise ValueError("图片无法解码，请选择完整的 PNG、JPEG 或 WebP 图片")
                checked = subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(checked_source), "--out", str(output)],
                                         capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired as error:
                raise ValueError("图片解码超时，请选择其他图片") from error
            if checked.returncode or not output.is_file() or output.stat().st_size < 32:
                raise ValueError("图片像素无法解码，请选择完整图片")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(actual_style, str) or not actual_style.strip():
            raise ValueError("需要记录图片对应的提示词和实际风格")
        with self._edit(post_id) as state:
            request = self._image_request(state)
            if request_id != request["request_id"]:
                raise ValueError("文案或风格已变化，不能把旧请求的图片关联到当前稿")
            image_id = uuid.uuid4().hex
            target = Path(request["archive_directory"]) / ("image-" + image_id + extension)
            with target.open("xb") as handle:
                handle.write(data)
            prompt_file = target.with_suffix(".prompt.txt")
            with prompt_file.open("x", encoding="utf-8") as handle:
                handle.write(prompt)
            state["images"].append({"id": image_id, "file": str(target), "prompt_file": str(prompt_file),
                                    "version": request["version"], "requested_style": state["style"],
                                    "actual_style": actual_style, "engine": engine, "sha256": hashlib.sha256(data).hexdigest(),
                                    "created_at": now()})
            state["image_error"] = None
        return self._view(state)

    def image_failed(self, post_id, request_id, error):
        with self._edit(post_id) as state:
            if self._image_request(state)["request_id"] != request_id:
                raise ValueError("失败记录对应的是旧版本，请核对当前文案")
            state["image_error"] = {"message": str(error), "request_id": request_id, "created_at": now()}
        return self._view(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--obsidian-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    create = sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--text-file", type=Path, required=True)
    create.add_argument("--style", default="auto")
    for name in ["get", "update", "confirm", "image-request", "attach-image", "image-failed"]:
        command = sub.add_parser(name)
        command.add_argument("--id", required=True)
        if name in {"update", "confirm"}:
            command.add_argument("--revision", type=int, required=True)
        if name == "update":
            command.add_argument("--text-file", type=Path)
            command.add_argument("--title")
            command.add_argument("--style")
            command.add_argument("--active-version", type=int)
        if name == "confirm":
            command.add_argument("--confirmed", action="store_true", required=True)
            command.add_argument("--date")
        if name == "image-request":
            command.add_argument("--revision", type=int)
        if name in {"attach-image", "image-failed"}:
            command.add_argument("--request-id", required=True)
        if name == "attach-image":
            command.add_argument("--image", type=Path, required=True)
            command.add_argument("--prompt-file", type=Path, required=True)
            command.add_argument("--actual-style", required=True)
            command.add_argument("--engine", default="imported")
        if name == "image-failed":
            command.add_argument("--error", required=True)
    args = parser.parse_args()
    store = WorkflowStore(args.repo_root, args.obsidian_dir)
    try:
        if args.command == "list":
            result = store.list()
        elif args.command == "create":
            result = store.create(args.title, args.text_file.read_text(), args.style)
        elif args.command == "get":
            result = store.get(args.id)
        elif args.command == "update":
            result = store.update(args.id, args.revision, body=args.text_file.read_text() if args.text_file else None,
                                  title=args.title, style=args.style, active_version=args.active_version)
        elif args.command == "confirm":
            result = store.confirm(args.id, args.revision, args.date)
        elif args.command == "image-request":
            result = store.image_request(args.id, args.revision)
        elif args.command == "attach-image":
            result = store.attach_image(args.id, args.request_id, args.image, args.prompt_file.read_text(), args.actual_style, args.engine)
        else:
            result = store.image_failed(args.id, args.request_id, args.error)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
