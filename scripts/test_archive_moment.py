import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("archive_moment.py")


class ArchiveMomentTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.repo = self.root / "repo"
        self.vault = self.root / "vault"

    def run_archive(self, text="已经确认的正文。\n\n保留这个段落 😄", day="2026-11-12", title="测试主题", confirmed=True, vault=None):
        text_file = self.root / ("input-" + str(len(list(self.root.glob("input-*")))) + ".txt")
        text_file.write_text(text, encoding="utf-8")
        command = [sys.executable, str(SCRIPT), "--text-file", str(text_file), "--title", title,
                   "--date", day, "--repo-root", str(self.repo), "--obsidian-dir", str(vault or self.vault)]
        if confirmed:
            command.append("--confirmed")
        return subprocess.run(command, capture_output=True, text=True)

    def test_creates_both_copies_with_matching_body(self):
        text = "这是最终稿。\n\n链接保留：https://example.com/a?q=1 😄"
        process = self.run_archive(text=text)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(Path(result["repo_file"]).read_text(), text + "\n")
        monthly = Path(result["obsidian_file"])
        self.assertEqual(monthly.name, "微信朋友圈2026年11月.md")
        self.assertIn(text + "\n", monthly.read_text())
        self.assertIn("account: 若洪大叔", monthly.read_text())
        self.assertNotIn("已发布", monthly.read_text())

    def test_existing_note_bytes_are_preserved(self):
        self.vault.mkdir()
        note = self.vault / "微信朋友圈2026年11月.md"
        original = b"---\r\ntags: [existing]\r\n---\r\n\r\n# Existing\r\nBody"
        note.write_bytes(original)
        process = self.run_archive()
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertTrue(note.read_bytes().startswith(original))
        self.assertEqual(note.read_text().count("wechat-moment:"), 2)

    def test_retry_does_not_duplicate_either_copy(self):
        first = json.loads(self.run_archive().stdout)
        local_before = Path(first["repo_file"]).read_bytes()
        note_before = Path(first["obsidian_file"]).read_bytes()
        second = json.loads(self.run_archive().stdout)
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(second["repo_action"], "unchanged")
        self.assertEqual(second["obsidian_action"], "unchanged")
        self.assertEqual(Path(first["repo_file"]).read_bytes(), local_before)
        self.assertEqual(Path(first["obsidian_file"]).read_bytes(), note_before)

    def test_changed_final_is_a_new_entry(self):
        first = json.loads(self.run_archive(text="第一版定稿").stdout)
        second = json.loads(self.run_archive(text="重新确认的第二版").stdout)
        self.assertNotEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(Path(first["repo_file"]).read_text(), "第一版定稿\n")
        monthly = Path(second["obsidian_file"]).read_text()
        self.assertIn("第一版定稿", monthly)
        self.assertIn("重新确认的第二版", monthly)

    def test_retry_with_changed_label_keeps_original_entry(self):
        first = json.loads(self.run_archive(title="最初标题").stdout)
        before = Path(first["obsidian_file"]).read_bytes()
        retried = self.run_archive(title="重试时换了标题")
        self.assertEqual(retried.returncode, 0, retried.stdout)
        self.assertEqual(Path(first["obsidian_file"]).read_bytes(), before)

    def test_month_rollover_including_new_year(self):
        paths = []
        for day in ["2026-11-30", "2026-12-01", "2027-01-01"]:
            result = json.loads(self.run_archive(day=day).stdout)
            paths.append(Path(result["obsidian_file"]).name)
        self.assertEqual(paths, ["微信朋友圈2026年11月.md", "微信朋友圈2026年12月.md", "微信朋友圈2027年1月.md"])

    def test_missing_confirmation_or_invalid_input_writes_nothing(self):
        for kwargs in [dict(confirmed=False), dict(text=" \n"), dict(day="2026-02-30")]:
            process = self.run_archive(**kwargs)
            self.assertNotEqual(process.returncode, 0)
        self.assertFalse(self.repo.exists())
        self.assertFalse(self.vault.exists())

    def test_partial_failure_reports_saved_copy_and_can_retry(self):
        blocked = self.root / "blocked-vault"
        blocked.write_text("a file rather than a directory")
        failed = self.run_archive(vault=blocked)
        self.assertEqual(failed.returncode, 1)
        result = json.loads(failed.stdout)
        self.assertTrue(result["repo_saved"])
        self.assertFalse(result["obsidian_saved"])
        self.assertTrue(Path(result["repo_file"]).is_file())
        recovered = json.loads(self.run_archive().stdout)
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["repo_action"], "unchanged")

    def test_refuses_to_overwrite_manually_changed_copy(self):
        result = json.loads(self.run_archive().stdout)
        local = Path(result["repo_file"])
        local.write_text("手动修改")
        note_before = Path(result["obsidian_file"]).read_bytes()
        retry = self.run_archive()
        self.assertEqual(retry.returncode, 1)
        self.assertEqual(local.read_text(), "手动修改")
        self.assertEqual(Path(result["obsidian_file"]).read_bytes(), note_before)

    def test_concurrent_appends_keep_all_entries(self):
        inputs = []
        for i in range(6):
            path = self.root / f"parallel-{i}.txt"
            path.write_text(f"并发正文 {i}")
            inputs.append([sys.executable, str(SCRIPT), "--text-file", str(path), "--title", f"主题{i}",
                           "--confirmed", "--date", "2026-11-12", "--repo-root", str(self.repo),
                           "--obsidian-dir", str(self.vault)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            processes = list(pool.map(lambda command: subprocess.run(command, capture_output=True, text=True), inputs))
        self.assertTrue(all(p.returncode == 0 for p in processes), [p.stdout for p in processes])
        note = (self.vault / "微信朋友圈2026年11月.md").read_text()
        for i in range(6):
            self.assertEqual(note.count(f"并发正文 {i}"), 1)
        self.assertEqual(note.count("# 微信朋友圈2026年11月"), 1)


if __name__ == "__main__":
    unittest.main()
