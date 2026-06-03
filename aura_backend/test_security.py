import unittest
from fastapi.testclient import TestClient
from main import app, RateLimitMiddleware

class TestSecurityHardening(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Clear rate limiting history before each test to ensure a clean state
        RateLimitMiddleware.request_history.clear()

    def test_payload_size_limit_under_5mb(self):
        """Verify that requests under 5MB are allowed through the middleware."""
        # Using a small POST request to an existing endpoint.
        # It doesn't matter if it fails logic checks (e.g. returns 400 or 500),
        # but it should not return 413.
        response = self.client.post(
            "/api/memory/save",
            headers={"Content-Length": "100"},
            json={"fact": "Test memory payload size"}
        )
        self.assertNotEqual(response.status_code, 413)

    def test_payload_size_limit_exceeded(self):
        """Verify that requests exceeding 5MB are blocked with HTTP 413 Payload Too Large."""
        response = self.client.post(
            "/api/memory/save",
            headers={"Content-Length": str(6 * 1024 * 1024)},
            json={"fact": "x" * 100}  # Small payload body, but Content-Length header claims 6MB
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("Payload Too Large", response.text)

    def test_rate_limiting(self):
        """Verify rate limiter blocks request volume exceeding 100 req/min with HTTP 429."""
        # Make 100 requests (the limit)
        for i in range(100):
            response = self.client.get("/api/memory/list")
            self.assertNotEqual(response.status_code, 429)

        # The 101st request should be rate-limited
        response = self.client.get("/api/memory/list")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Rate limit exceeded", response.text)

if __name__ == "__main__":
    unittest.main()
