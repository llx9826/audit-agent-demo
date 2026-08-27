import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = PROJECT_ROOT / "backend/app"


class ScopeBoundaryTests(unittest.TestCase):
    def test_fixed_interview_scenario_values_exist_only_under_demo(self):
        fixed_tokens = [
            "CASE-ZD-042",
            "张伟",
            "林芳",
            "明达商贸",
            "same_value_low_confidence",
            "Demo VLM observation",
            "南京公积金贷款，离婚需要什么婚姻证明？有依据吗？",
        ]
        protected_roots = [CORE_ROOT, PROJECT_ROOT / "app"]
        core = "\n".join(
            path.read_text(encoding="utf-8")
            for root in protected_roots
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".json", ".jsonl", ".txt", ".md"}
        )
        demo = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROJECT_ROOT / "demo").rglob("*")
            if path.is_file() and path.suffix in {".py", ".json", ".jsonl", ".txt", ".md"}
        )
        for token in fixed_tokens:
            self.assertNotIn(token, core)
            self.assertIn(token, demo)

    def test_only_composition_root_may_import_demo_package(self):
        composition_roots = {
            CORE_ROOT / "bootstrap/container.py",
            CORE_ROOT / "main.py",
        }
        offenders = []
        for path in CORE_ROOT.rglob("*.py"):
            if path in composition_roots:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports_demo = any(
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (node.module == "demo" or node.module.startswith("demo."))
                for node in ast.walk(tree)
            )
            if imports_demo:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
