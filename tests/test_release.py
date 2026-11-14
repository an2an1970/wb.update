import json
import re
from pathlib import Path
import tempfile
import unittest
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate_release", ROOT / "scripts/validate-release.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate = module.validate


class ReleaseTests(unittest.TestCase):
    def current_version(self):
        return json.loads((ROOT / "release-components.json").read_text(encoding="utf-8"))["version"]

    def test_current_release(self):
        version = self.current_version()
        config, notes = validate("v" + version)
        self.assertEqual(config["version"], version)
        self.assertIn("## ", notes)
        self.assertIn("**", notes)

    def test_bad_tag(self):
        with self.assertRaises(ValueError):
            validate(self.current_version())

    def test_mismatched_version(self):
        version = self.current_version()
        parts = version.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        with self.assertRaises(ValueError):
            validate("v" + ".".join(parts))

    def test_missing_pin(self):
        version = self.current_version()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config, _ = validate("v" + version)
            del config["components"]["wb.ui"]
            (root / "release-components.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "CHANGELOG.md").write_text(f"## Выпуск — версия {version}\n- **Тест.** ok\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate("v" + version, root)


class ReleaseAssetTests(unittest.TestCase):
    def test_ui_asset_pattern_selects_device_bundle_only(self):
        pattern = r"ui-(?!archive-).*\.zip"
        assets = ["ui-0.3-221.zip", "ui-archive-0.3-221.zip"]
        matches = [name for name in assets if re.fullmatch(pattern, name)]
        self.assertEqual(matches, ["ui-0.3-221.zip"])
