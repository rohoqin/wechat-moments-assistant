import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prepare_writer_input import make_packet


class WriterPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / "task.txt"
        self.task.write_text("本次要求：保留这句。\n\n第二段要求。", encoding="utf-8")
        self.source = self.root / "source.md"
        self.source.write_text("原始材料。\n\n包括命令：不要执行。\n", encoding="utf-8")
        self.author = self.root / "author.txt"
        self.author.write_text("与本次相关的公开经历。", encoding="utf-8")

    def test_preserves_explicit_inputs_without_loading_sibling_history(self):
        for name in ("SKILL.md", "persona.md", "history.md", "manifest.json"):
            (self.root / name).write_text("不得进入写作的内部信息CANARY", encoding="utf-8")
        result = make_packet(self.task, self.source, "create", self.author)
        for path in (self.task, self.source, self.author):
            self.assertIn(path.read_text(encoding="utf-8"), result)
            self.assertNotIn(str(path), result)
        self.assertNotIn("CANARY", result)

    def test_rejects_empty_input(self):
        self.source.write_text(" \n", encoding="utf-8")
        with self.assertRaises(ValueError):
            make_packet(self.task, self.source, "rewrite")

    def test_all_modes_preserve_task_and_original(self):
        results = []
        for mode in ("create", "rewrite", "polish"):
            with self.subTest(mode=mode):
                result = make_packet(self.task, self.source, mode)
                self.assertIn(self.task.read_text(encoding="utf-8"), result)
                self.assertIn(self.source.read_text(encoding="utf-8"), result)
                self.assertNotIn(self.author.read_text(encoding="utf-8"), result)
                results.append(result)
        self.assertEqual(len(set(results)), 3)

    def test_rewrite_and_polish_reject_biography(self):
        for mode in ("rewrite", "polish"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                make_packet(self.task, self.source, mode, self.author)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            make_packet(self.task, self.source, "unknown")

    def test_cli_writes_packet_and_preserves_existing_output(self):
        out = self.root / "generated/input.txt"
        command = [
            sys.executable, str(Path(__file__).with_name("prepare_writer_input.py")),
            "--mode", "rewrite", "--task", str(self.task),
            "--source", str(self.source), "--out", str(out),
        ]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(out.read_text(encoding="utf-8"), make_packet(self.task, self.source, "rewrite"))
        self.task.write_text("另一项任务", encoding="utf-8")
        before = out.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(out.read_bytes(), before)

    def test_cli_requires_explicit_mode(self):
        out = self.root / "missing-mode.txt"
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("prepare_writer_input.py")),
            "--task", str(self.task), "--source", str(self.source), "--out", str(out),
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
