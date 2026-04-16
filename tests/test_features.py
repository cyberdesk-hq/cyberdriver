import ast
import asyncio
import copy
import secrets
import tempfile
import time
import types
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse


SOURCE_PATH = Path(__file__).resolve().parents[1] / "cyberdriver.py"

TARGET_ASSIGNMENTS = {
    "TUNNEL_INTERNAL_REQUEST_HEADER",
    "TUNNEL_PROTECTED_ROUTE_PREFIXES",
}
TARGET_FUNCTIONS = {
    "_is_tunnel_protected_path",
    "_build_local_forward_headers",
    "_is_request_from_active_tunnel",
    "disable_buffering",
}


def _load_route_protection_namespace():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(SOURCE_PATH))
    selected_nodes = []

    for node in module.body:
        if isinstance(node, ast.Assign):
            target_names = {
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if target_names & TARGET_ASSIGNMENTS:
                selected_nodes.append(copy.deepcopy(node))
        elif isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            node_copy = copy.deepcopy(node)
            node_copy.decorator_list = []
            selected_nodes.append(node_copy)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in TARGET_FUNCTIONS:
            node_copy = copy.deepcopy(node)
            node_copy.decorator_list = []
            selected_nodes.append(node_copy)

    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(tunnel_internal_token="test-internal-token")
    )
    extracted_module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "JSONResponse": JSONResponse,
        "Request": object,
        "app": fake_app,
        "print": lambda *args, **kwargs: None,
        "secrets": secrets,
        "time": time,
    }
    exec(compile(extracted_module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


def _load_run_join_namespace(temp_home: Path, record: Dict[str, Any]):
    source = SOURCE_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(SOURCE_PATH))
    selected_nodes = []

    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_join":
            selected_nodes.append(copy.deepcopy(node))

    extracted_module = ast.Module(body=selected_nodes, type_ignores=[])

    class _PathProxy:
        @staticmethod
        def home():
            return temp_home

    class _KeepAliveManager:
        def __init__(self, **kwargs):
            self.enabled = kwargs.get("enabled", False)
            self.kwargs = kwargs
            record["keepalive_manager_kwargs"] = kwargs

        async def run(self):
            record["keepalive_run_calls"] = record.get("keepalive_run_calls", 0) + 1

        def is_busy(self):
            return False

        async def wait_until_idle(self):
            return None

        def record_activity(self):
            return None

    class _BlackScreenRecoveryManager:
        def __init__(self, **kwargs):
            self.enabled = kwargs.get("enabled", False)
            self.kwargs = kwargs
            record["black_screen_manager_kwargs"] = kwargs

        async def run(self):
            record["black_screen_run_calls"] = record.get("black_screen_run_calls", 0) + 1

        def stop(self):
            record["black_screen_stop_calls"] = record.get("black_screen_stop_calls", 0) + 1

    class _TunnelClient:
        def __init__(
            self,
            host,
            port,
            secret,
            target_port,
            config,
            keepalive_manager=None,
            remote_keepalive_for_main_id=None,
            internal_request_token=None,
        ):
            record.setdefault("tunnels", []).append(
                {
                    "host": host,
                    "port": port,
                    "secret": secret,
                    "target_port": target_port,
                    "config": config,
                    "keepalive_manager": keepalive_manager,
                    "remote_keepalive_for_main_id": remote_keepalive_for_main_id,
                    "internal_request_token": internal_request_token,
                }
            )

        async def run(self):
            record["tunnel_run_calls"] = record.get("tunnel_run_calls", 0) + 1

    async def _fake_run_server_async(port):
        record.setdefault("server_ports", []).append(port)

    def _fake_find_available_port(host, start_port):
        record.setdefault("find_available_port_calls", []).append((host, start_port))
        return start_port + 7

    def _fake_write_pid_info(info):
        record["pid_info"] = info

    def _fake_set_connection_info(host, port):
        record["connection_info"] = (host, port)

    async def _fake_sleep(_seconds):
        return None

    fake_asyncio = types.SimpleNamespace(
        create_task=asyncio.create_task,
        sleep=_fake_sleep,
        gather=asyncio.gather,
        wait_for=asyncio.wait_for,
        CancelledError=asyncio.CancelledError,
        TimeoutError=asyncio.TimeoutError,
        Task=asyncio.Task,
    )
    fake_app = types.SimpleNamespace(state=types.SimpleNamespace())
    namespace = {
        "Optional": Optional,
        "asyncio": fake_asyncio,
        "app": fake_app,
        "get_config": lambda: types.SimpleNamespace(fingerprint="fp", version="0.0.42"),
        "find_available_port": _fake_find_available_port,
        "write_pid_info": _fake_write_pid_info,
        "run_server_async": _fake_run_server_async,
        "KeepAliveManager": _KeepAliveManager,
        "BlackScreenRecoveryManager": _BlackScreenRecoveryManager,
        "TunnelClient": _TunnelClient,
        "_set_connection_info": _fake_set_connection_info,
        "pathlib": types.SimpleNamespace(Path=_PathProxy),
        "print": lambda *args, **kwargs: None,
        "secrets": types.SimpleNamespace(token_hex=lambda _n: "generated-tunnel-token"),
        "sys": types.SimpleNamespace(exit=lambda code: (_ for _ in ()).throw(SystemExit(code))),
    }
    exec(compile(extracted_module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class _FakeRequest:
    def __init__(self, path: str, headers: Optional[Dict[str, str]] = None, method: str = "GET"):
        self.method = method
        self.headers = headers or {}
        self.url = types.SimpleNamespace(path=path)


class TunnelProtectionTests(unittest.TestCase):
    def test_evidence_privileged_routes_are_classified_as_tunnel_only(self):
        namespace = _load_route_protection_namespace()

        self.assertTrue(namespace["_is_tunnel_protected_path"]("/computer/display/screenshot"))
        self.assertTrue(namespace["_is_tunnel_protected_path"]("/internal/update"))
        self.assertFalse(namespace["_is_tunnel_protected_path"]("/docs"))
        self.assertFalse(namespace["_is_tunnel_protected_path"]("/tunnel/ws"))

    def test_evidence_privileged_tunnel_forwarded_requests_get_internal_token(self):
        namespace = _load_route_protection_namespace()

        headers = namespace["_build_local_forward_headers"](
            {"x-idempotency-key": "abc123"},
            "/computer/fs/read",
            "secret-token",
        )

        self.assertEqual(headers["x-idempotency-key"], "abc123")
        self.assertEqual(
            headers[namespace["TUNNEL_INTERNAL_REQUEST_HEADER"]],
            "secret-token",
        )

    def test_unprotected_routes_do_not_gain_internal_token(self):
        namespace = _load_route_protection_namespace()

        headers = namespace["_build_local_forward_headers"](
            {"x-idempotency-key": "abc123"},
            "/docs",
            "secret-token",
        )

        self.assertEqual(headers, {"x-idempotency-key": "abc123"})

    def test_evidence_privileged_routes_reject_direct_requests_without_trusted_token(self):
        namespace = _load_route_protection_namespace()
        call_next_called = False

        async def call_next(_request):
            nonlocal call_next_called
            call_next_called = True
            return JSONResponse(status_code=200, content={"ok": True})

        response = asyncio.run(
            namespace["disable_buffering"](
                _FakeRequest("/computer/input/mouse/click"),
                call_next,
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(call_next_called)

    def test_evidence_privileged_tunnel_forwarded_requests_succeed_as_expected(self):
        namespace = _load_route_protection_namespace()
        call_next_called = False
        header_name = namespace["TUNNEL_INTERNAL_REQUEST_HEADER"]

        async def call_next(_request):
            nonlocal call_next_called
            call_next_called = True
            return JSONResponse(status_code=200, content={"ok": True})

        response = asyncio.run(
            namespace["disable_buffering"](
                _FakeRequest(
                    "/internal/update",
                    headers={header_name: "test-internal-token"},
                    method="POST",
                ),
                call_next,
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(call_next_called)

    def test_evidence_legacy_cyberdriver_start_path_has_been_removed(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertNotIn('start_parser = subparsers.add_parser(', source)
        self.assertNotIn('if args.command == "start":', source)
        self.assertNotIn("cyberdriver start [--port 3000]", source)
        self.assertNotIn("0.0.0.0", source)


def _load_url_builder_namespace():
    """Extract build_tunnel_url / build_api_base_url and their helpers."""
    from urllib.parse import urlparse

    source = SOURCE_PATH.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(SOURCE_PATH))

    wanted_fns = {"_parse_host", "_is_default_port", "build_tunnel_url", "build_api_base_url"}
    wanted_assigns = {"_WS_SCHEME_MAP", "_HTTP_SCHEME_MAP"}

    selected_nodes = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assigns:
                selected_nodes.append(copy.deepcopy(node))
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_fns:
            n = copy.deepcopy(node)
            n.decorator_list = []
            selected_nodes.append(n)

    extracted = ast.Module(body=selected_nodes, type_ignores=[])
    ns = {"urlparse": urlparse, "Optional": Optional}
    exec(compile(extracted, str(SOURCE_PATH), "exec"), ns)
    return ns


class UrlBuilderTests(unittest.TestCase):
    """Regression coverage for Bug 1 - --host parsing must preserve scheme + explicit port."""

    def setUp(self):
        self.ns = _load_url_builder_namespace()
        self.build_tunnel_url = self.ns["build_tunnel_url"]
        self.build_api_base_url = self.ns["build_api_base_url"]

    def test_bare_host_defaults_to_wss_with_default_port_omitted(self):
        self.assertEqual(
            self.build_tunnel_url("api.cyberdesk.io", 443),
            "wss://api.cyberdesk.io/tunnel/ws",
        )

    def test_https_host_with_explicit_port_is_preserved(self):
        # This is the exact case from the bug report (local dev over TLS).
        self.assertEqual(
            self.build_tunnel_url("https://localhost:8443", 443),
            "wss://localhost:8443/tunnel/ws",
        )

    def test_http_host_downgrades_to_plain_ws(self):
        # Plain-http host must become ws:// (not wss://) - no TLS needed for local dev.
        self.assertEqual(
            self.build_tunnel_url("http://localhost:8080", 443),
            "ws://localhost:8080/tunnel/ws",
        )

    def test_explicit_ws_scheme_is_preserved(self):
        self.assertEqual(
            self.build_tunnel_url("ws://localhost:8080", 443),
            "ws://localhost:8080/tunnel/ws",
        )

    def test_wss_scheme_is_preserved(self):
        self.assertEqual(
            self.build_tunnel_url("wss://staging.cyberdesk.io", 443),
            "wss://staging.cyberdesk.io/tunnel/ws",
        )

    def test_bare_host_with_non_default_port_includes_port(self):
        # Keeps --port backward compatible when no scheme is given.
        self.assertEqual(
            self.build_tunnel_url("api.cyberdesk.io", 8443),
            "wss://api.cyberdesk.io:8443/tunnel/ws",
        )

    def test_url_does_not_double_append_443(self):
        # The regression this test guards against: --host https://localhost:8443
        # used to produce wss://localhost:8443:443/tunnel/ws.
        for host in (
            "https://localhost:8443",
            "http://localhost:8080",
            "wss://api.cyberdesk.io",
            "ws://localhost:9000",
            "https://api.cyberdesk.io",
        ):
            url = self.build_tunnel_url(host, 443)
            with self.subTest(host=host):
                self.assertFalse(
                    url.rstrip("/").endswith(":443/tunnel/ws") and "localhost" in url,
                    f"build_tunnel_url({host!r}, 443) appended :443 incorrectly: {url}",
                )
                self.assertNotIn(":8443:443", url)
                self.assertNotIn(":8080:443", url)
                self.assertNotIn(":9000:443", url)

    def test_api_base_url_preserves_scheme_and_port(self):
        self.assertEqual(
            self.build_api_base_url("http://localhost:8080", 443),
            "http://localhost:8080",
        )
        self.assertEqual(
            self.build_api_base_url("https://localhost:8443", 443),
            "https://localhost:8443",
        )
        self.assertEqual(
            self.build_api_base_url("api.cyberdesk.io", 443),
            "https://api.cyberdesk.io",
        )

    def test_tunnel_url_function_exists_in_source(self):
        # Make sure we didn't accidentally replace the new helpers with inline string concat.
        src = SOURCE_PATH.read_text(encoding="utf-8")
        self.assertIn("def build_tunnel_url(", src)
        # The old buggy pattern must be gone.
        self.assertNotIn('f"wss://{host}:{self.port}/tunnel/ws"', src)


class JoinFlowTests(unittest.TestCase):
    def test_evidence_cyberdriver_join_still_initializes_loopback_server_and_tunnel(self):
        record: Dict[str, Any] = {}

        with tempfile.TemporaryDirectory() as temp_dir:
            namespace = _load_run_join_namespace(Path(temp_dir), record)
            asyncio.run(
                namespace["run_join"](
                    host="api.cyberdesk.io",
                    port=443,
                    secret="SK-test",
                    target_port=3000,
                )
            )

        self.assertEqual(record["connection_info"], ("api.cyberdesk.io", 443))
        self.assertEqual(record["find_available_port_calls"], [("127.0.0.1", 3000)])
        self.assertEqual(record["server_ports"], [3007])
        self.assertEqual(record["pid_info"]["command"], "join")
        self.assertEqual(record["pid_info"]["local_port"], 3007)
        self.assertEqual(record["tunnel_run_calls"], 1)
        self.assertEqual(len(record["tunnels"]), 1)
        self.assertEqual(record["tunnels"][0]["target_port"], 3007)
        self.assertEqual(
            record["tunnels"][0]["internal_request_token"],
            "generated-tunnel-token",
        )
        self.assertEqual(
            namespace["app"].state.tunnel_internal_token,
            "generated-tunnel-token",
        )
        self.assertFalse(record["keepalive_manager_kwargs"]["enabled"])
        self.assertFalse(record["black_screen_manager_kwargs"]["enabled"])


if __name__ == "__main__":
    unittest.main()