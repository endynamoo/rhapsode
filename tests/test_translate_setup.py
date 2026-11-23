"""Tests for detect-and-connect: the status, the probe, saving an endpoint, and the gate."""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from murmur import server, store, translate


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.settings = mock.patch.object(translate.store, "get_settings", return_value={})
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_status_names_the_ports_it_will_look_at(self):
        status = translate.translator.status()
        self.assertIn("ports", status)
        self.assertEqual(status["ports"], list(translate.LOCAL_PORTS))
        self.assertIn("available", status)
        self.assertIn("reason", status)

    def test_no_server_means_a_reason_not_a_crash(self):
        with mock.patch.object(translate, "_discover", return_value=None), \
                mock.patch.object(translate, "_saved_url", return_value=""):
            status = translate.translator.status()
        self.assertFalse(status["available"])
        self.assertIn("llama.cpp", status["reason"])

    def test_a_saved_endpoint_is_reported(self):
        with mock.patch.object(translate, "_saved_url", return_value="http://127.0.0.1:8091/v1"), \
                mock.patch.object(translate.HttpTranslator, "available", return_value=True), \
                mock.patch.object(translate.HttpTranslator, "url", "http://127.0.0.1:8091/v1",
                                  create=True):
            status = translate.translator.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "http")


class CheckUrlTest(unittest.TestCase):
    def test_only_this_machine_is_accepted(self):
        for url in ("http://example.com/v1", "https://10.0.0.5:8091/v1", "ftp://localhost/v1"):
            result = translate.check_url(url)
            self.assertFalse(result["ok"], url)
            self.assertIn("this machine", result["reason"])

    def test_an_empty_address_says_what_to_do(self):
        self.assertIn("address", translate.check_url("")["reason"])

    def test_a_bare_port_gains_the_v1_suffix(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": [{"id": "qwen2.5-3b"}]}

        with mock.patch("httpx.Client.get", return_value=Response()) as getter:
            result = translate.check_url("http://127.0.0.1:8091")
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "http://127.0.0.1:8091/v1")
        self.assertEqual(result["model"], "qwen2.5-3b")
        self.assertIn("/v1/models", getter.call_args[0][0])

    def test_a_refusal_is_reported_with_the_status(self):
        class Response:
            status_code = 404

            @staticmethod
            def json():
                return {}

        with mock.patch("httpx.Client.get", return_value=Response()):
            result = translate.check_url("http://127.0.0.1:8091")
        self.assertFalse(result["ok"])
        self.assertIn("404", result["reason"])


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.saved: dict = {}
        getter = mock.patch.object(store, "get_settings",
                                   side_effect=lambda: {**store.DEFAULT_SETTINGS, **self.saved})
        setter = mock.patch.object(store, "set_settings",
                                   side_effect=lambda patch: self.saved.update(patch))
        getter.start()
        setter.start()
        self.addCleanup(getter.stop)
        self.addCleanup(setter.stop)

    def test_status_route_answers(self):
        with TestClient(server.app) as client:
            status = client.get("/api/translate").json()
        self.assertIn("available", status)

    def test_connecting_a_broken_address_is_refused_and_not_saved(self):
        with TestClient(server.app) as client:
            before = self.saved.get("translateUrl")
            response = client.post("/api/translate", json={"url": "http://127.0.0.1:9999/v1"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.saved.get("translateUrl"), before)

    def test_a_good_address_is_saved_and_clears_the_cache(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": [{"id": "qwen2.5-3b-instruct"}]}

        with ClientIfy() as client, \
                mock.patch("httpx.Client.get", return_value=Response()), \
                mock.patch.object(translate, "reset_discovery") as reset:
            response = client.post("/api/translate", json={"url": "http://127.0.0.1:8091"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["connected"]["url"], "http://127.0.0.1:8091/v1")
        self.assertEqual(self.saved.get("translateUrl"), "http://127.0.0.1:8091/v1")
        reset.assert_called()


def ClientIfy():
    return TestClient(server.app)


if __name__ == "__main__":
    unittest.main()
