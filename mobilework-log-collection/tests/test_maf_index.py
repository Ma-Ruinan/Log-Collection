import importlib.util
import unittest
from pathlib import Path


script = Path(__file__).resolve().parents[1] / "scripts" / "maf_index.py"
spec = importlib.util.spec_from_file_location("maf_index", script)
maf_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maf_index)


class MAFParserTests(unittest.TestCase):
    def test_input_event(self):
        line = (
            '2026-09-25T07:42:25.936Z [INFO] facet=input role=user scene=sample '
            'preview="example user request text" → decision=2 degraded=false '
            'hit=[10400000,104] 361ms\n'
        )
        timestamp, fields, preview = maf_index.parse_line(line)
        self.assertGreater(timestamp, 0)
        self.assertEqual(fields["facet"], "input")
        self.assertEqual(fields["decision"], "2")
        self.assertEqual(fields["hit"], ["10400000", "104"])
        self.assertEqual(preview, "example user request text")

    def test_non_decision_line_is_skipped(self):
        self.assertIsNone(maf_index.parse_line("2026-09-25T07:42:25.936Z [INFO] starting\n"))


if __name__ == "__main__":
    unittest.main()
