import unittest
from unittest.mock import patch
from pathlib import Path
from installer import bootstrap

class BootstrapTests(unittest.TestCase):
    def test_unit_files_run_as_wbui(self):
        self.assertIn("User=wbui", bootstrap.SERVER_UNIT)
        self.assertIn("User=wbui", bootstrap.UI_UNIT)
        self.assertIn("ExecStart=/usr/bin/java -jar /opt/wb.srv/wb.srv.jar", bootstrap.SERVER_UNIT)

    def test_boot_reset_pins_match_pi_hardware(self):
        self.assertIn("pinctrl set 18 op pn dl", bootstrap.BOOT)
        self.assertIn("pinctrl set 17 op pn dl", bootstrap.RESET)

    def test_does_not_overwrite_conflicting_service(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "service"
            path.write_text("custom")
            with self.assertRaises(RuntimeError):
                bootstrap.write_if_absent(path, "new")

if __name__ == "__main__":
    unittest.main()
