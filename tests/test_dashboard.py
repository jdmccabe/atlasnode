from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from atlasnode_mcp import dashboard


class DashboardTests(unittest.TestCase):
    def test_dashboard_payload_includes_disk_status(self) -> None:
        fake_service = Mock()
        fake_service.to_dict.return_value = {"running": True, "service_url": "http://127.0.0.1:8000/mcp"}

        with (
            patch.object(dashboard.store, "dashboard_snapshot", return_value={"storage": {}, "state": {}, "health": {}, "recent_context": {}, "usage": {}, "categories": []}),
            patch.object(dashboard.manager, "status", return_value=fake_service),
            patch.object(dashboard.session_tracker, "active_clients", return_value=2),
            patch("atlasnode_mcp.dashboard._disk_status", return_value={"path": "F:\\", "free_bytes": 100, "total_bytes": 200, "percent_used": 50.0}),
        ):
            payload = dashboard._dashboard_payload()

        self.assertIn("system", payload)
        self.assertEqual(payload["system"]["disk"]["path"], "F:\\")
        self.assertNotIn("openai", payload["system"])
        self.assertEqual(payload["dashboard"]["active_clients"], 2)


if __name__ == "__main__":
    unittest.main()
