"""OpenAlex 可选认证只进入请求头；诊断与失败消息不暴露凭据。"""
from __future__ import annotations

import json
import os
import traceback
import unittest
from unittest import mock

import httpx

from src import doctor
from src.connectors import openalex


FAKE_KEY = "synthetic-openalex-key-for-tests"


class OpenAlexAuthenticationTest(unittest.TestCase):
    def test_optional_key_only_changes_request_header(self):
        cache_keys = []
        for key in ("", FAKE_KEY):
            response = httpx.Response(200, json={"results": []},
                                      request=httpx.Request("GET", openalex.BASE + "/works"))
            with self.subTest(authenticated=bool(key)), \
                 mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": key}), \
                 mock.patch.object(openalex, "_cache_get", return_value=None) as cache_get, \
                 mock.patch.object(openalex, "_cache_put") as cache_put, \
                 mock.patch.object(openalex, "log_tool") as log, \
                 mock.patch.object(openalex._client, "get", return_value=response) as get:
                self.assertEqual(openalex.search("retrieval"), [])
            kwargs = get.call_args.kwargs
            expected = {"Authorization": f"Bearer {key}"} if key else {}
            self.assertEqual(kwargs.get("headers", {}), expected)
            self.assertNotIn("api_key", kwargs["params"])
            self.assertNotIn(FAKE_KEY, json.dumps(kwargs["params"]))
            self.assertNotIn(FAKE_KEY, str(cache_put.call_args_list))
            self.assertNotIn(FAKE_KEY, str(log.call_args_list))
            self.assertNotIn("Authorization", openalex._client.headers)
            cache_keys.append(cache_get.call_args.args[0])
        self.assertEqual(cache_keys[0], cache_keys[1])

    def test_http_failure_reports_status_without_reflected_key(self):
        for status in (401, 403, 429):
            request = httpx.Request("GET", openalex.BASE + "/works",
                                    headers={"Authorization": "Bearer " + FAKE_KEY})
            response = httpx.Response(status, request=request)
            error = httpx.HTTPStatusError(f"reflected credential: {FAKE_KEY}",
                                          request=request, response=response)
            with self.subTest(status=status), \
                 mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": FAKE_KEY}), \
                 mock.patch.object(openalex, "_cache_get", return_value=None), \
                 mock.patch.object(openalex, "log_tool") as log, \
                 mock.patch.object(openalex.time, "sleep"), \
                 mock.patch.object(openalex._client, "get", side_effect=error) as get:
                try:
                    openalex.search("retrieval")
                except RuntimeError as exc:
                    self.assertIn(f"HTTP {status}", str(exc))
                    self.assertNotIn(FAKE_KEY, "".join(traceback.format_exception(exc)))
                else:
                    self.fail("HTTP failure should raise RuntimeError")
                self.assertEqual(get.call_count, openalex._MAX_RETRY)
                self.assertNotIn(FAKE_KEY, str(log.call_args_list))

    def test_network_failure_reports_exception_type_without_raw_message(self):
        error = httpx.ConnectError("reflected credential: " + FAKE_KEY)
        with mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": FAKE_KEY}), \
             mock.patch.object(openalex, "_cache_get", return_value=None), \
             mock.patch.object(openalex, "log_tool") as log, \
             mock.patch.object(openalex.time, "sleep"), \
             mock.patch.object(openalex._client, "get", side_effect=error):
            try:
                openalex.search("retrieval")
            except RuntimeError as exc:
                self.assertIn("ConnectError", str(exc))
                self.assertNotIn(FAKE_KEY, "".join(traceback.format_exception(exc)))
            else:
                self.fail("Network failure should raise RuntimeError")
        self.assertNotIn(FAKE_KEY, str(log.call_args_list))

    def test_doctor_and_connector_share_authentication_helper(self):
        response = httpx.Response(200, json={"results": []},
                                  request=httpx.Request("GET", openalex.BASE + "/works"))
        for key in ("", FAKE_KEY):
            with self.subTest(authenticated=bool(key)), \
                 mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": key}), \
                 mock.patch.object(openalex, "request_headers", wraps=openalex.request_headers) as headers, \
                 mock.patch.object(httpx, "get", return_value=response) as get:
                ok, detail = doctor._openalex_check()
            self.assertTrue(ok)
            headers.assert_called_once()
            expected = {"Authorization": "Bearer " + key} if key else {}
            self.assertEqual(get.call_args.kwargs["headers"], expected)
            self.assertNotIn(FAKE_KEY, detail)
            self.assertNotIn(FAKE_KEY, str(get.call_args.kwargs["params"]))

    def test_doctor_failure_does_not_expose_key(self):
        response = httpx.Response(401, request=httpx.Request("GET", openalex.BASE + "/works"))
        with mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": FAKE_KEY}), \
             mock.patch.object(httpx, "get", return_value=response):
            ok, detail = doctor._openalex_check()
        self.assertFalse(ok)
        self.assertIn("HTTP 401", detail)
        self.assertNotIn(FAKE_KEY, detail)
        with mock.patch.dict(os.environ, {"GEZHI_OPENALEX_API_KEY": FAKE_KEY}), \
             mock.patch.object(httpx, "get", side_effect=httpx.ConnectError(FAKE_KEY)):
            ok, detail = doctor._openalex_check()
        self.assertFalse(ok)
        self.assertIn("ConnectError", detail)
        self.assertNotIn(FAKE_KEY, detail)


if __name__ == "__main__":
    unittest.main()
