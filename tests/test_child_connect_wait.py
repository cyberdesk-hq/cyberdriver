import ast
import copy
import os
import pathlib
import platform
import tempfile
import time
import unittest
from pathlib import Path
from typing import List, Optional, Tuple


SOURCE_PATH = Path(__file__).resolve().parents[1] / "cyberdriver.py"

TARGET_ASSIGNMENTS = {
    "_CHILD_CONNECTED_MARKER",
    "_CHILD_AUTH_FAILURE_MARKERS",
    "_CHILD_TLS_FAILURE_MARKERS",
    "_CHILD_FATAL_FAILURE_MARKERS",
}
TARGET_FUNCTIONS = {"_wait_for_child_connected"}


def _load_wait_namespace():
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
            selected_nodes.append(copy.deepcopy(node))

    extracted_module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace = {
        "List": List,
        "Optional": Optional,
        "Tuple": Tuple,
        "os": os,
        "pathlib": pathlib,
        "platform": platform,
        "time": time,
        "_pid_is_running": lambda _pid: True,
    }
    exec(compile(extracted_module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class WaitForChildConnectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = _load_wait_namespace()
        cls.wait_for_child_connected = staticmethod(namespace["_wait_for_child_connected"])
        cls.fatal_marker = namespace["_CHILD_FATAL_FAILURE_MARKERS"][0]
        cls.tls_marker = namespace["_CHILD_TLS_FAILURE_MARKERS"][0]

    def test_fatal_failure_returns_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "child.log"
            log_path.write_text(f"{self.fatal_marker}\n", encoding="utf-8")

            started = time.time()
            state, excerpt = self.wait_for_child_connected(log_path, 0, 123, timeout_seconds=1.0)
            elapsed = time.time() - started

        self.assertEqual(state, "unknown_error")
        self.assertIn(self.fatal_marker, excerpt)
        self.assertLess(elapsed, 0.5)

    def test_fatal_failure_is_not_masked_by_later_tls_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "child.log"
            log_path.write_text(
                f"{self.fatal_marker}\n{self.tls_marker}\n",
                encoding="utf-8",
            )

            state, excerpt = self.wait_for_child_connected(log_path, 0, 123, timeout_seconds=1.0)

        self.assertEqual(state, "unknown_error")
        self.assertIn(self.fatal_marker, excerpt)


if __name__ == "__main__":
    unittest.main()
