import binascii
import json
import os
from pathlib import Path
import tempfile
import struct
import shlex
import subprocess
import unittest
from unittest.mock import patch
import zlib

try:
    from workflow import WorkflowStore
except ImportError:
    WorkflowStore = None


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(WorkflowStore, "缺少可恢复的版本与定稿状态工具")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkflowStore(self.root / "repo", self.root / "vault")

    def draft(self):
        return self.store.create("试用记录", "生成效果满意，销售尚未验证。", "水彩")

    def test_draft_and_style_survive_new_store_instance(self):
        post = self.draft()
        restored = WorkflowStore(self.root / "repo", self.root / "vault").get(post["id"])
        self.assertEqual(restored["style"], "水彩")
        self.assertEqual(restored["versions"][0]["body"], "生成效果满意，销售尚未验证。")
        self.assertIsNone(restored["confirmed_version"])
        self.assertFalse((self.root / "vault").exists())

    def test_unconfirmed_copy_cannot_prepare_an_image(self):
        post = self.draft()
        with self.assertRaises(ValueError):
            self.store.image_request(post["id"])

    def test_image_command_preserves_isolated_paths_from_another_directory(self):
        store = WorkflowStore(self.root / "another repo", self.root / "another vault")
        post = store.create("独立空间", "正文")
        store.confirm(post["id"], post["revision"])
        request = store.image_request(post["id"])
        result = subprocess.run([*shlex.split(request["cli_prefix"]), "image-request", "--id", post["id"]],
                                cwd=self.root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"]["request_id"], request["request_id"])

    def test_confirm_archives_exact_active_version(self):
        post = self.draft()
        final = self.store.confirm(post["id"], post["revision"])
        self.assertEqual(final["confirmed_version"], 1)
        self.assertTrue(final["archive"]["ok"])
        self.assertEqual(Path(final["archive"]["repo_file"]).read_text(), "生成效果满意，销售尚未验证。\n")
        self.assertIn("生成效果满意，销售尚未验证。", Path(final["archive"]["obsidian_file"]).read_text())

    def test_body_change_revokes_confirmation_and_keeps_history(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        edited = self.store.update(post["id"], final["revision"], body="重新确认前的新版正文。")
        self.assertIsNone(edited["confirmed_version"])
        self.assertEqual(edited["active_version"], 2)
        self.assertEqual(len(edited["versions"]), 2)
        with self.assertRaises(ValueError):
            self.store.image_request(post["id"])
        self.assertTrue(Path(final["archive"]["repo_file"]).is_file())

    def test_style_change_keeps_confirmation(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        changed = self.store.update(post["id"], final["revision"], style="黑白线稿")
        self.assertEqual(changed["confirmed_version"], 1)
        self.assertEqual(self.store.image_request(post["id"])["style"], "黑白线稿")

    def test_old_window_revision_cannot_overwrite_newer_copy(self):
        post = self.draft()
        self.store.update(post["id"], post["revision"], body="先保存的修改。")
        with self.assertRaises(ValueError):
            self.store.update(post["id"], post["revision"], body="旧窗口覆盖。")
        self.assertEqual(self.store.get(post["id"])["versions"][-1]["body"], "先保存的修改。")

    def test_invalid_revision_cannot_bypass_concurrency_check(self):
        post = self.draft()
        for revision in [None, True, "1", 1.0]:
            with self.assertRaises(ValueError):
                self.store.update(post["id"], revision, body="不能绕过检查")
            with self.assertRaises(ValueError):
                self.store.confirm(post["id"], revision)
        self.assertFalse((self.root / "vault").exists())

    def test_image_request_is_specific_to_each_post(self):
        first = self.store.confirm((a := self.draft())["id"], a["revision"])
        second = self.store.confirm((b := self.draft())["id"], b["revision"])
        self.assertNotEqual(self.store.image_request(first["id"])["request_id"], self.store.image_request(second["id"])["request_id"])

    def test_version_selection_requires_confirmation_again(self):
        post = self.draft()
        edited = self.store.update(post["id"], post["revision"], body="第二版。")
        selected = self.store.update(post["id"], edited["revision"], active_version=1)
        self.assertEqual(selected["active_version"], 1)
        self.assertIsNone(selected["confirmed_version"])

    def test_archive_failure_is_recorded_and_blocks_image(self):
        self.store.obsidian_dir.parent.mkdir(parents=True, exist_ok=True)
        self.store.obsidian_dir.write_text("阻塞目录的文件")
        post = self.draft()
        failed = self.store.confirm(post["id"], post["revision"])
        self.assertFalse(failed["archive"]["ok"])
        self.assertTrue(failed["archive"]["repo_saved"])
        with self.assertRaises(ValueError):
            self.store.image_request(post["id"])
        self.store.obsidian_dir.unlink()
        recovered = self.store.confirm(post["id"], failed["revision"])
        self.assertTrue(recovered["archive"]["ok"])
        self.assertEqual(recovered["archive"]["repo_action"], "unchanged")

    def image(self):
        path = self.root / "pixel.png"
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload))
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))
        return path

    def test_image_is_bound_to_copy_and_style(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        saved = self.store.attach_image(post["id"], request["request_id"], self.image(), "水彩画提示词", "水彩")
        self.assertEqual(saved["status"], "illustrated")
        self.assertEqual(saved["current_image"]["version"], 1)
        self.assertEqual(Path(saved["current_image"]["file"]).read_bytes(), self.image().read_bytes())
        changed = self.store.update(post["id"], saved["revision"], style="黑白线稿")
        self.assertEqual(changed["status"], "archived")
        self.assertIsNone(changed["current_image"])
        self.assertEqual(len(changed["images"]), 1)

    def test_late_image_cannot_attach_after_copy_or_style_changes(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        self.store.update(post["id"], final["revision"], style="线稿")
        with self.assertRaises(ValueError):
            self.store.attach_image(post["id"], request["request_id"], self.image(), "旧请求", "水彩")
        self.assertEqual(self.store.get(post["id"])["images"], [])

    def test_image_failure_preserves_archive_and_allows_retry(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        failed = self.store.image_failed(post["id"], request["request_id"], "生成服务暂不可用")
        self.assertTrue(failed["archive"]["ok"])
        self.assertEqual(failed["status"], "archived")
        saved = self.store.attach_image(post["id"], request["request_id"], self.image(), "重试提示词", "水彩")
        self.assertIsNone(saved["image_error"])
        self.assertEqual(saved["status"], "illustrated")

    def test_tampered_archive_blocks_image(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        Path(final["archive"]["obsidian_file"]).write_text("已移除原文")
        with self.assertRaises(ValueError):
            self.store.image_request(post["id"])

    def test_month_entry_with_extra_text_fails_archive_and_image_checks(self):
        post = self.draft()
        final = self.store.confirm(post["id"], post["revision"], "2026-11-18")
        monthly = Path(final["archive"]["obsidian_file"])
        edited = monthly.read_text().replace(final["body"], "手工新增的正文。\n\n" + final["body"])
        monthly.write_text(edited)
        with self.assertRaises(ValueError):
            self.store.image_request(post["id"])
        retried = self.store.confirm(post["id"], final["revision"])
        self.assertFalse(retried["archive"]["obsidian_saved"])
        self.assertEqual(monthly.read_text(), edited)

    def test_confirm_retries_same_date_after_result_write_fails(self):
        from workflow import write_json

        post = self.draft()

        def fail_final_write(path, state):
            if state["archive"]:
                raise OSError("模拟归档后状态落盘失败")
            write_json(path, state)

        with patch("workflow.write_json", side_effect=fail_final_write):
            with self.assertRaises(OSError):
                self.store.confirm(post["id"], post["revision"], "2026-11-30")
        pending = self.store.get(post["id"])
        self.assertEqual(pending["confirmed_date"], "2026-11-30")
        self.assertEqual(pending["status"], "confirmed")
        other_vault = self.root / "new configuration"
        restored = WorkflowStore(self.store.repo_root, other_vault)
        with patch("workflow.now", return_value="2026-12-01T00:01:00+08:00"):
            recovered = restored.confirm(post["id"], pending["revision"])
        self.assertEqual(recovered["confirmed_date"], "2026-11-30")
        self.assertEqual(recovered["archive"]["repo_action"], "unchanged")
        self.assertEqual(recovered["archive"]["obsidian_action"], "unchanged")
        self.assertEqual(len(list(self.store.obsidian_dir.glob("*.md"))), 1)
        self.assertFalse(other_vault.exists())

    def test_invalid_date_preserves_the_confirmed_archive(self):
        post = self.draft()
        final = self.store.confirm(post["id"], post["revision"], "2026-11-18")
        with self.assertRaises(ValueError):
            self.store.confirm(post["id"], final["revision"], "bad-date")
        self.assertEqual(self.store.get(post["id"]), final)

    def test_obsidian_config_can_be_added_after_local_save(self):
        repo = self.root / "unconfigured repo"
        store = WorkflowStore(repo)
        post = store.create("本机配置", "等待补写的正文。")
        pending = store.confirm(post["id"], post["revision"], "2026-11-18")
        self.assertTrue(pending["archive"]["repo_saved"])
        self.assertFalse(pending["archive"]["obsidian_saved"])
        config = repo / ".moments.local" / "obsidian-dir"
        config.parent.mkdir()
        vault = self.root / "configured vault"
        config.write_text(str(vault) + "\n")
        final = store.confirm(post["id"], pending["revision"])
        self.assertTrue(final["archive"]["ok"])
        self.assertEqual(Path(final["archive"]["obsidian_file"]).parent, vault.resolve())
        self.assertIn(str(vault.resolve()), shlex.split(store.image_request(post["id"])["cli_prefix"]))

    def test_missing_image_is_not_reported_as_current(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        saved = self.store.attach_image(post["id"], request["request_id"], self.image(), "提示词", "水彩")
        Path(saved["current_image"]["file"]).unlink()
        restored = self.store.get(post["id"])
        self.assertIsNone(restored["current_image"])
        self.assertEqual(restored["status"], "archived")

    def test_reselected_version_does_not_reuse_old_confirmation_or_current_image(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        saved = self.store.attach_image(post["id"], request["request_id"], self.image(), "提示词", "水彩")
        changed = self.store.update(post["id"], saved["revision"], body="另一版")
        selected = self.store.update(post["id"], changed["revision"], active_version=1)
        self.assertIsNone(selected["current_image"])
        self.assertEqual(selected["status"], "draft")

    def test_non_image_content_is_rejected(self):
        final = self.store.confirm((post := self.draft())["id"], post["revision"])
        request = self.store.image_request(post["id"])
        fake = self.root / "fake.png"
        fake.write_text("这是一段文本，不是图片。" * 10)
        with self.assertRaises(ValueError):
            self.store.attach_image(post["id"], request["request_id"], fake, "提示词", "水彩")

    def test_path_traversal_does_not_read_files(self):
        with self.assertRaises(ValueError):
            self.store.get("../../outside")

    def test_invalid_title_is_rejected_without_mutation(self):
        for title in [None, [], 12, "长" * 501]:
            with self.assertRaises(ValueError):
                self.store.create(title, "正文")
        self.assertFalse(self.store.root.exists())
        post = self.draft()
        with self.assertRaises(ValueError):
            self.store.update(post["id"], post["revision"], title=[])
        self.assertEqual(self.store.get(post["id"])["revision"], post["revision"])

    def test_crlf_body_supports_archive_retry_and_image(self):
        post = self.store.create("换行", "首行\r\n次行")
        saved = self.store.confirm(post["id"], post["revision"])
        self.assertTrue(saved["archive"]["ok"])
        self.store.image_request(post["id"])
        retried = self.store.confirm(post["id"], saved["revision"])
        self.assertTrue(retried["archive"]["ok"])

    def test_forged_image_markers_do_not_make_an_image(self):
        post = self.draft()
        self.store.confirm(post["id"], post["revision"])
        request = self.store.image_request(post["id"])
        fake = self.root / "fake.jpg"
        fake.write_bytes(b"\xff\xd8\xff" + b"x" * 30 + b"\xff\xd9")
        with self.assertRaises(ValueError):
            self.store.attach_image(post["id"], request["request_id"], fake, "提示词", "水彩")

    def test_png_with_valid_metadata_but_corrupt_pixels_is_rejected(self):
        original = self.image().read_bytes()
        rebuilt = original[:8]
        offset = 8
        while offset < len(original):
            size = int.from_bytes(original[offset:offset + 4], "big")
            kind = original[offset + 4:offset + 8]
            payload = original[offset + 8:offset + 8 + size]
            if kind == b"IDAT":
                payload = b"not-valid-zlib"
            rebuilt += struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload))
            offset += size + 12
        fake = self.root / "bad-pixels.png"
        fake.write_bytes(rebuilt)
        post = self.draft()
        self.store.confirm(post["id"], post["revision"])
        request = self.store.image_request(post["id"])
        with self.assertRaises(ValueError):
            self.store.attach_image(post["id"], request["request_id"], fake, "提示词", "水彩")


if __name__ == "__main__":
    unittest.main()
