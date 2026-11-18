#!/usr/bin/env python3
"""将已确认的朋友圈正文保存到仓库，并追加到 Obsidian 月文件。"""

import argparse
from datetime import date, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


DEFAULT_REPO = Path(__file__).resolve().parents[1]


def configured_obsidian_dir(repo_root):
    config = Path(repo_root) / ".moments.local" / "obsidian-dir"
    try:
        value = config.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return Path(value).expanduser() if value else None


def month_header(day):
    title = f"微信朋友圈{day.year}年{day.month}月"
    return (
        "---\n"
        f"date: {day.year:04d}-{day.month:02d}-01\n"
        "type: 朋友圈月度归档\n"
        "tags:\n  - 自媒体\n  - 朋友圈\n"
        "account: 若洪大叔\n"
        "---\n\n"
        f"# {title}\n"
    )


def save_copy(path, text):
    """首次原子创建；重试只接受相同正文，不覆盖已有文案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
            return "created"
        except FileExistsError:
            if path.read_bytes().decode("utf-8") != text:
                raise ValueError(f"本地归档内容有变化，未覆盖：{path}")
            return "unchanged"
    finally:
        temporary.unlink()


def month_entry_matches(existing, entry_id, text):
    start = f"\n%% wechat-moment:{entry_id} %%\n"
    end = f"\n%% /wechat-moment:{entry_id} %%\n"
    _, start_found, rest = ("\n" + existing).partition(start)
    block, end_found, _ = rest.partition(end)
    heading, separator, body = block.partition("\n\n")
    return bool(start_found and end_found and separator and heading.startswith("## ")
                and "\n" not in heading and body == text)


def append_month(path, day, title, text, entry_id):
    start = f"%% wechat-moment:{entry_id} %%"
    block = (
        f"{start}\n"
        f"## {day.isoformat()} · {title}\n\n"
        f"{text}\n"
        f"%% /wechat-moment:{entry_id} %%\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同一台机器上并发归档时，锁内读取与追加，保留已有文件的全部字节。
    with path.open("a+", encoding="utf-8", newline="") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = handle.read()
        if start in existing:
            if not month_entry_matches(existing, entry_id, text):
                raise ValueError(f"月文件中的同条记录已变更或不完整，未重复追加：{path}")
            return "unchanged"
        prefix = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
        handle.write((month_header(day) + "\n" if not existing else prefix) + block)
        handle.flush()
        os.fsync(handle.fileno())
    return "appended" if existing else "created"


def archive_text(raw, title, day, repo_root=DEFAULT_REPO, obsidian_dir=None):
    result = {"ok": False, "repo_saved": False, "obsidian_saved": False}
    try:
        day = date.fromisoformat(day) if isinstance(day, str) else day
        title = " ".join(title.split())
        if not title or not raw.strip() or "\x00" in raw:
            raise ValueError("标题和定稿正文不能为空，正文不能含 NUL 字符")
        text = raw.rstrip("\r\n") + "\n"
        digest = hashlib.sha256((day.isoformat() + "\0" + text).encode("utf-8")).hexdigest()
        entry_id = f"{day:%Y%m%d}-{digest[:16]}"
        local = Path(repo_root) / "output" / "moments" / day.isoformat() / entry_id / "content.md"
        result.update(entry_id=entry_id, repo_file=str(local.resolve()))
        result["repo_action"] = save_copy(local, text)
        result["repo_saved"] = True
        if obsidian_dir is None:
            raise ValueError("请在 .moments.local/obsidian-dir 配置 Obsidian 目录，或传入 --obsidian-dir 后补写")
        monthly = Path(obsidian_dir) / f"微信朋友圈{day.year}年{day.month}月.md"
        result["obsidian_file"] = str(monthly.resolve())
        result["obsidian_action"] = append_month(monthly, day, title, text, entry_id)
        result["obsidian_saved"] = True
        result["ok"] = True
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--confirmed", required=True, action="store_true")
    parser.add_argument("--date", default=datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat())
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--obsidian-dir", type=Path)
    args = parser.parse_args()
    try:
        raw = args.text_file.read_text(encoding="utf-8")
        destination = args.obsidian_dir if args.obsidian_dir is not None else configured_obsidian_dir(args.repo_root)
        result = archive_text(raw, args.title, args.date, args.repo_root, destination)
    except OSError as error:
        result = {"ok": False, "repo_saved": False, "obsidian_saved": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
