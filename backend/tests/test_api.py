import unittest

from fastapi.testclient import TestClient

from app import main
from app.persistence.repository import InMemoryCaseRepository
from app.service import AuditService


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.previous_service = main.service
        main.service = AuditService(InMemoryCaseRepository())
        self.client = TestClient(main.app)

    def tearDown(self):
        main.service = self.previous_service

    def test_events_support_cursor_and_sse_ids(self):
        created = self.client.post("/api/cases/demo/architecture_demo").json()
        case_id = created["case_id"]
        self.client.post(f"/api/cases/{case_id}/run")

        all_events = self.client.get(f"/api/cases/{case_id}/events").json()
        cursor = all_events[5]["seq"]
        remaining = self.client.get(f"/api/cases/{case_id}/events", params={"after": cursor}).json()
        self.assertTrue(remaining)
        self.assertTrue(all(event["seq"] > cursor for event in remaining))

        with self.client.stream("GET", f"/api/cases/{case_id}/stream", params={"after": cursor}) as response:
            body = "".join(response.iter_text())
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: audit_event", body)
        self.assertIn(f"id: {remaining[0]['seq']}", body)


if __name__ == "__main__":
    unittest.main()
