import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from text_engine import (
    API_KEY_ENV, MAX_SOURCE, TextError, TextRequest, TextSettings, build_messages,
    generate, load_settings, save_settings, validate_settings,
)

HAS_HTTPX = importlib.util.find_spec("httpx") is not None
if HAS_HTTPX:
    import httpx


class TextSettingsTests(unittest.TestCase):
    def test_import_without_qt_or_httpx_and_settings_contain_no_document_or_key(self):
        result = subprocess.run([sys.executable, "-c", "import sys, text_engine; "
                                 "assert 'PyQt6' not in sys.modules; assert 'httpx' not in sys.modules"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "папка данных" / "text-settings.json"
            with patch.dict(os.environ, {API_KEY_ENV: "synthetic-key-not-for-disk"}):
                save_settings(path, replace(TextSettings(), provider="cloud", cloud_model="test-model"))
            self.assertNotIn("synthetic-key", path.read_text())
            loaded = load_settings(path)
            self.assertEqual(loaded.provider, "ollama")
            self.assertEqual(loaded.cloud_model, "test-model")
            path.write_text("not json")
            with self.assertRaises(TextError):
                load_settings(path)

    def test_reject_remote_local_provider_insecure_cloud_secrets_and_bad_ports(self):
        for changes in ({"ollama_url": "http://example.com"}, {"ollama_url": "http://localhost.evil.test"},
                        {"cloud_url": "http://example.com/v1"}, {"cloud_url": "https://user:key@example.com"},
                        {"cloud_url": "https://example.com?api_key=secret"},
                        {"ollama_url": "http://localhost:bad"}, {"cloud_model": ""}):
            with self.subTest(changes=changes), self.assertRaises(TextError):
                validate_settings(replace(TextSettings(), **changes))
        validate_settings(replace(TextSettings(), ollama_url="http://[::1]:11434"))

    def test_both_modes_validate_without_silent_truncation(self):
        source = "Документ с числами: 125,50.\n\nВторой абзац."
        for mode, instruction in (("translate", ""), ("write", "Отредактируй стиль")):
            messages = build_messages(TextRequest(mode, source, instruction, "Английский"))
            self.assertEqual(json.loads(messages[1]["content"])["source"], source)
        for request in (TextRequest("write"), TextRequest("translate"),
                        TextRequest("translate", "x" * (MAX_SOURCE + 1))):
            with self.assertRaises(TextError):
                build_messages(request)


@unittest.skipUnless(HAS_HTTPX, "Установите requirements-text.txt")
class TextProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_request_is_local_and_uses_exact_material(self):
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"message": {"content": "Hello, world!"}, "done": True, "done_reason": "stop"})

        with patch.dict(os.environ, {API_KEY_ENV: "synthetic-secret"}):
            result = await generate(TextRequest("translate", "Привет, мир!", language="Английский"),
                                    TextSettings(), transport=httpx.MockTransport(handler))
        self.assertEqual(result.text, "Hello, world!")
        self.assertEqual(str(calls[0].url), "http://127.0.0.1:11434/api/chat")
        self.assertNotIn("authorization", calls[0].headers)
        payload = json.loads(calls[0].content)
        self.assertFalse(payload["stream"])
        self.assertEqual(json.loads(payload["messages"][1]["content"])["source"], "Привет, мир!")

    async def test_cloud_requires_consent_and_key_before_any_connection(self):
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Деловое письмо"}, "finish_reason": "stop"}]})

        settings = replace(TextSettings(), provider="cloud", cloud_url="https://provider.example/v1", cloud_model="synthetic-model")
        transport = httpx.MockTransport(handler)
        request = TextRequest("write", instruction="Напиши письмо")
        with self.assertRaisesRegex(TextError, "подтверждения"):
            await generate(request, settings, transport=transport)
        with patch.dict(os.environ, {API_KEY_ENV: ""}), self.assertRaisesRegex(TextError, "API-ключ"):
            await generate(request, settings, cloud_consent=True, transport=transport)
        self.assertEqual(calls, [])
        with patch.dict(os.environ, {API_KEY_ENV: "synthetic-secret"}):
            result = await generate(request, settings, cloud_consent=True, transport=transport)
        self.assertEqual(result.text, "Деловое письмо")
        self.assertEqual(str(calls[0].url), "https://provider.example/v1/chat/completions")
        self.assertEqual(calls[0].headers["authorization"], "Bearer synthetic-secret")

    async def test_deadline_and_cancel_close_request_without_retry(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        count = 0

        async def hanging(request):
            nonlocal count
            count += 1
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        request = TextRequest("translate", "Синтетический текст")
        with self.assertRaisesRegex(TextError, "Истекло время"):
            await generate(request, replace(TextSettings(), timeout=1), transport=httpx.MockTransport(hanging))
        self.assertTrue(cancelled.is_set())
        started.clear()
        cancelled.clear()
        task = asyncio.create_task(generate(request, TextSettings(), transport=httpx.MockTransport(hanging)))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(count, 2)

    async def test_http_errors_redirects_and_network_errors_do_not_echo_secrets(self):
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(code, text="synthetic-private-document synthetic-secret", headers={"location": "https://elsewhere.example"})

        for code in (301, 401, 403, 404, 429, 500):
            with self.subTest(code=code), self.assertRaises(TextError) as error:
                await generate(TextRequest("translate", "synthetic-private-document"), TextSettings(), transport=httpx.MockTransport(handler))
            self.assertNotIn("synthetic-", str(error.exception))
        self.assertEqual(len(calls), 6)

        async def offline(request):
            raise httpx.ConnectError("synthetic-secret", request=request)

        with self.assertRaisesRegex(TextError, "Не удалось связаться") as error:
            await generate(TextRequest("translate", "test"), TextSettings(), transport=httpx.MockTransport(offline))
        self.assertNotIn("synthetic-secret", str(error.exception))

    async def test_invalid_empty_large_and_truncated_responses(self):
        for body in ({}, {"message": {"content": ""}, "done": True}, {"message": {"content": "partial"}, "done": False}):
            with self.subTest(body=body), self.assertRaisesRegex(TextError, "неподдерживаемый"):
                await generate(TextRequest("translate", "test"), TextSettings(),
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
        with self.assertRaisesRegex(TextError, "превышает"):
            await generate(TextRequest("translate", "test"), TextSettings(),
                           transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2_000_001)))
        result = await generate(TextRequest("translate", "test"), TextSettings(),
                                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
                                    "message": {"content": "Incomplete translation"}, "done": True, "done_reason": "length"})))
        self.assertIn("неполным", result.warning)
        self.assertTrue(result.text)

    async def test_real_loopback_http_server_and_proxy_bypass(self):
        recorded = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                recorded.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps({"message": {"content": "Local synthetic response"}, "done": True}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                result = await generate(TextRequest("write", instruction="Test"),
                                        replace(TextSettings(), ollama_url=f"http://127.0.0.1:{server.server_port}"))
            self.assertEqual(result.text, "Local synthetic response")
            self.assertEqual(recorded[0][0], "/api/chat")
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
