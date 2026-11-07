import unittest
from unittest.mock import patch
from installer.screen_setup import kiosk_target, enable_screen

class ScreenSetupTests(unittest.TestCase):
    def test_hash_route_is_valid_kiosk(self):
        from unittest.mock import MagicMock
        import io
        payload = b'[{"type":"page","url":"http://127.0.0.1:7777/#/start","webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/page/abc"}]'
        response = io.BytesIO(payload)
        with patch("installer.screen_setup.urllib.request.urlopen", return_value=response):
            self.assertEqual(kiosk_target()["type"], "page")

    def test_cdp_active_never_restarts_kiosk(self):
        with patch("installer.screen_setup.kiosk_target", return_value={"type":"page"}), \
             patch("installer.screen_setup.check_frame"), \
             patch("installer.screen_setup.subprocess.run") as run:
            self.assertTrue(enable_screen("/nonexistent", idle_confirmed=True))
            run.assert_not_called()

    def test_no_idle_confirmation_never_restarts_kiosk(self):
        with patch("installer.screen_setup.kiosk_target", side_effect=RuntimeError("CDP unavailable")), \
             patch("installer.screen_setup.subprocess.run") as run:
            self.assertFalse(enable_screen("/nonexistent", idle_confirmed=False))
            run.assert_not_called()
