import ast
import re
import types
import unittest
from pathlib import Path
from typing import Dict, List


SOURCE_PATH = Path(__file__).resolve().parents[1] / "cyberdriver.py"

TARGET_ASSIGNMENTS = {
    "XDO_MODIFIER_KEYS",
    "XDO_MULTIWORD_KEY_ALIASES",
    "XDO_KEY_ALIASES",
    "EXPERIMENTAL_SPACE_ENABLED",
    "LETTER_SCANCODES",
    "NUMBER_SCANCODES",
    "SYMBOL_SCANCODES",
    "SPECIAL_KEY_SCANCODES",
    "MODIFIER_SCANCODES",
}
TARGET_FUNCTIONS = {
    "_normalize_xdo_sequence",
    "_canonicalize_keyboard_key",
    "execute_xdo_sequence",
    "_press_key_with_scancode",
}
TARGET_CLASSES = {"KeyEvent", "XDOParser"}


def _load_keyboard_namespace():
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
                selected_nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in TARGET_CLASSES:
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in TARGET_FUNCTIONS:
            selected_nodes.append(node)

    extracted_module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace = {
        "Dict": Dict,
        "List": List,
        "platform": types.SimpleNamespace(system=lambda: "Linux"),
        "pyautogui": types.SimpleNamespace(keyDown=lambda key: None, keyUp=lambda key: None),
        "re": re,
    }
    exec(compile(extracted_module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


def _event_tuples(group):
    return [(event.key, event.down) for event in group]


class KeyboardAliasTests(unittest.TestCase):
    def test_parser_normalizes_common_model_output_variants(self):
        namespace = _load_keyboard_namespace()

        groups = namespace["XDOParser"].parse("CTRL + ARROWRIGHT page down")

        self.assertEqual(
            _event_tuples(groups[0]),
            [("ctrl", True), ("right", True), ("right", False), ("ctrl", False)],
        )
        self.assertEqual(
            _event_tuples(groups[1]),
            [("pagedown", True), ("pagedown", False)],
        )

    def test_canonicalizer_covers_common_modifier_and_navigation_aliases(self):
        namespace = _load_keyboard_namespace()
        canonicalize = namespace["_canonicalize_keyboard_key"]

        self.assertEqual(canonicalize("Left-Control"), "ctrl")
        self.assertEqual(canonicalize("Option"), "alt")
        self.assertEqual(canonicalize("WinLeft"), "win")
        self.assertEqual(canonicalize("PgDn"), "pagedown")
        self.assertEqual(canonicalize("ARROWLEFT"), "left")
        self.assertEqual(canonicalize("spacebar"), "space")

    def test_execute_xdo_sequence_uses_canonical_keys_in_fallback_backend(self):
        namespace = _load_keyboard_namespace()
        recorded_calls = []

        namespace["platform"] = types.SimpleNamespace(system=lambda: "Linux")
        namespace["pyautogui"] = types.SimpleNamespace(
            keyDown=lambda key: recorded_calls.append(("down", key)),
            keyUp=lambda key: recorded_calls.append(("up", key)),
        )

        namespace["execute_xdo_sequence"]("Meta + arrow left PgDn")

        self.assertEqual(
            recorded_calls,
            [
                ("down", "win"),
                ("down", "left"),
                ("up", "left"),
                ("up", "win"),
                ("down", "pagedown"),
                ("up", "pagedown"),
            ],
        )

    def test_scan_code_path_accepts_aliases_after_canonicalization(self):
        namespace = _load_keyboard_namespace()
        recorded_calls = []

        namespace["_win32_send_key"] = lambda scan_code, key_up=False: recorded_calls.append(
            (scan_code, key_up)
        )

        namespace["_press_key_with_scancode"]("ARROWRIGHT")
        namespace["_press_key_with_scancode"]("Page_Down", key_up=True)
        namespace["_press_key_with_scancode"]("Left-Control")

        self.assertEqual(
            recorded_calls,
            [
                (0xE04D, False),
                (0xE051, True),
                (0x1D, False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
