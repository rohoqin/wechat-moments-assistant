import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prepare_editor_input import make_packet


class EditorPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        references = self.repo / "references"
        references.mkdir()
        (references / "persona.md").write_text("完整画像CANARY")
        (references / "example.md").write_text("范例主题CANARY")
        self.task = self.repo / "task.txt"
        self.task.write_text("用户要求：保留原意")
        self.source = self.repo / "source.md"
        self.source.write_text("来源归属\n原文主张与限制。\n")
        self.draft = self.repo / "draft.txt"
        self.draft.write_text("第一段。\n\n第二段。")

    def test_preserves_inputs_without_loading_biography_or_examples(self):
        for mode in ("create", "rewrite", "polish"):
            with self.subTest(mode=mode):
                result = make_packet(self.task, self.source, self.draft, mode)
                for path in (self.task, self.source, self.draft):
                    self.assertIn(path.read_text(), result)
                    self.assertNotIn(str(path), result)
                self.assertNotIn("CANARY", result)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            make_packet(self.task, self.source, self.draft, "unknown")

    def test_rejects_empty_original(self):
        self.source.write_text(" \n")
        with self.assertRaises(ValueError):
            make_packet(self.task, self.source, self.draft, "rewrite")

    def test_cli_preserves_existing_output(self):
        out = self.repo / "packet.txt"
        command = [
            sys.executable, str(Path(__file__).with_name("prepare_editor_input.py")),
            "--mode", "rewrite", "--task", str(self.task), "--source", str(self.source),
            "--draft", str(self.draft), "--out", str(out),
        ]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        before = out.read_bytes()
        self.draft.write_text("后续版本")
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(out.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
