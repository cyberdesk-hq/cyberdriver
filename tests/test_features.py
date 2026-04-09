import ast
import asyncio
import copy
import secrets
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


class _FakeRequest:
    def __init__(self, path: str, headers: Optional[Dict[str, str]] = None, method: str = "GET"):
        self.method = method
        self.headers = headers or {}
        self.url = types.SimpleNamespace(path=path)


class TunnelProtectionTests(unittest.TestCase):
    def test_protected_path_helper_matches_expected_prefixes(self):
        namespace = _load_route_protection_namespace()

        self.assertTrue(namespace["_is_tunnel_protected_path"]("/computer/display/screenshot"))
        self.assertTrue(namespace["_is_tunnel_protected_path"]("/internal/update"))
        self.assertFalse(namespace["_is_tunnel_protected_path"]("/docs"))
        self.assertFalse(namespace["_is_tunnel_protected_path"]("/tunnel/ws"))

    def test_forward_headers_add_internal_token_for_protected_routes(self):
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

    def test_forward_headers_leave_unprotected_routes_unchanged(self):
        namespace = _load_route_protection_namespace()

        headers = namespace["_build_local_forward_headers"](
            {"x-idempotency-key": "abc123"},
            "/docs",
            "secret-token",
        )

        self.assertEqual(headers, {"x-idempotency-key": "abc123"})

    def test_middleware_blocks_direct_requests_without_tunnel_token(self):
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

    def test_middleware_allows_requests_with_valid_tunnel_token(self):
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

    def test_source_no_longer_contains_legacy_start_command(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")

        self.assertNotIn('start_parser = subparsers.add_parser(', source)
        self.assertNotIn('if args.command == "start":', source)
        self.assertNotIn("cyberdriver start [--port 3000]", source)
        self.assertNotIn("0.0.0.0", source)


if __name__ == "__main__":
    unittest.main()