import json
from pathlib import Path
import tempfile
import unittest
import importlib.util
spec = importlib.util.spec_from_file_location('validate_release', Path(__file__).resolve().parents[1] / 'scripts/validate-release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate = module.validate

class ReleaseTests(unittest.TestCase):
    def test_current_release(self):
        config, notes = validate("v0.2.10")
        self.assertEqual(config["version"], "0.2.10")
        self.assertIn("Удалённый экран", notes)

    def test_bad_tag(self):
        with self.assertRaises(ValueError):
            validate("0.2.10")

    def test_mismatched_version(self):
        with self.assertRaises(ValueError):
            validate("v0.2.11")

    def test_missing_pin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config, _ = validate("v0.2.10")
            del config["components"]["wb.ui"]
            (root / "release-components.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "CHANGELOG.md").write_text("## Выпуск — версия 0.2.10\n- **Тест.** ok\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate("v0.2.10", root)
