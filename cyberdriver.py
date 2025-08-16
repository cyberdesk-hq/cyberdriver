"""
Cyberdriver: A comprehensive remote computer control tool
=========================================================
This module provides a feature-complete implementation for remote computer control.
It includes all features from the original Zig implementation:

- HTTP API server with all endpoints
- WebSocket tunnel client for remote control
- XDO keyboard input support (e.g., 'ctrl+c ctrl+v')
- Screenshot with scaling modes (Exact, AspectFit, AspectFill)
- Instant mouse movement with pyautogui
- Mouse button press/release control
- Configuration persistence (fingerprint, version)
- Exponential backoff reconnection
- File transfer support (read/write/list any file type through tunnel)

File Transfer Features:
  - Write any file type to the remote machine (binary safe)
  - Read files from the remote machine (with size limits)
  - List directory contents
  - Base64 encoding for binary data transport
  - Support for Drake tax files, medical images, or any binary format

Dependencies:
  - fastapi and uvicorn: HTTP API server
  - websockets: WebSocket client for the reverse tunnel
  - httpx: HTTP client for forwarding requests
  - mss: screen capture library
  - pyautogui: keyboard and mouse automation
  - pillow: image processing for scaling
  - numpy: efficient image operations

Install dependencies:
    pip install fastapi uvicorn[standard] websockets httpx mss pyautogui pillow numpy

Usage:
    cyberdriver start [--port 3000]
    cyberdriver join --secret YOUR_API_KEY [--host example.com] [--port 443]
"""

import argparse
import asyncio
import base64
import json
import os
import platform
import pathlib
import socket
import subprocess
import sys
import time
import uuid
import signal
import threading
from typing import Dict, List, Optional, Tuple, Union, Any
from enum import Enum
from dataclasses import dataclass
from io import BytesIO
from contextlib import asynccontextmanager

import httpx
import mss
import numpy as np
import pyautogui
from PIL import Image
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
import uvicorn
import websockets

# -----------------------------------------------------------------------------
# Windows Console Fix
# -----------------------------------------------------------------------------

def disable_windows_console_quickedit():
    """Disable QuickEdit mode in Windows console to prevent output blocking.
    
    QuickEdit mode causes console output to block when the console window has focus
    and is in selection mode. This is a common issue that makes applications appear
    to hang until Escape or Ctrl+C is pressed.
    """
    if platform.system() != "Windows":
        return
    
    try:
        import ctypes
        from ctypes import wintypes
        
        kernel32 = ctypes.windll.kernel32
        
        # Get handle to current console
        STD_INPUT_HANDLE = -10
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        
        # Get current console mode
        mode = wintypes.DWORD()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        
        # Disable QuickEdit (0x0040) and Insert mode (0x0020)
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_INSERT_MODE = 0x0020
        ENABLE_EXTENDED_FLAGS = 0x0080
        
        # First, enable extended flags to make the change
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_EXTENDED_FLAGS)
        
        # Then disable QuickEdit and Insert modes
        new_mode = mode.value & ~ENABLE_QUICK_EDIT_MODE & ~ENABLE_INSERT_MODE
        kernel32.SetConsoleMode(handle, new_mode | ENABLE_EXTENDED_FLAGS)
        
        # Try to print with checkmark
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            print("✓ Disabled Windows console QuickEdit mode")
        except:
            print("√ Disabled Windows console QuickEdit mode")
    except Exception as e:
        print(f"Note: Could not disable QuickEdit mode: {e}")
        print("If output appears stuck, click elsewhere or press Escape in the console")


# Define websocket compatibility helper inline
async def connect_with_headers(uri, headers_dict):
    """Compatibility wrapper for websocket connections with headers and keepalive settings."""
    # Common kwargs for robustness across proxies
    ws_kwargs = {
        # Send pings to keep NATs and proxies alive
        "ping_interval": 20,
        "ping_timeout": 20,
        # Allow larger messages (screenshots, file reads)
        "max_size": None,
        # Avoid unbounded back-pressure
        "max_queue": 32,
        # Faster close handshakes
        "close_timeout": 3,
    }
    # Try websockets v15+ API (uses additional_headers)
    try:
        return await websockets.connect(uri, additional_headers=headers_dict, **ws_kwargs)
    except TypeError:
        pass
    
    # Try websockets v10-14 API (uses extra_headers)
    try:
        return await websockets.connect(uri, extra_headers=headers_dict, **ws_kwargs)
    except TypeError:
        pass
    
    # Try list of tuples format (websockets 8.x - 9.x)
    try:
        return await websockets.connect(uri, extra_headers=list(headers_dict.items()), **ws_kwargs)
    except TypeError:
        pass
    
    # Last resort - connect without headers
    print("WARNING: Could not send custom headers with WebSocket connection")
    print("This may cause authentication to fail ")
    return await websockets.connect(uri, **ws_kwargs)

# -----------------------------------------------------------------------------
# Configuration Management
# -----------------------------------------------------------------------------

CONFIG_DIR = ".cyberdriver"
CONFIG_FILE = "config.json"
VERSION = "0.0.15"

@dataclass
class Config:
    version: str
    fingerprint: str
    
    def to_dict(self):
        return {"version": self.version, "fingerprint": self.fingerprint}
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(version=data["version"], fingerprint=data["fingerprint"])


def get_config_dir() -> pathlib.Path:
    """Get the configuration directory path."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return pathlib.Path(base) / CONFIG_DIR


def get_config() -> Config:
    """Load or create configuration."""
    config_dir = get_config_dir()
    config_path = config_dir / CONFIG_FILE
    should_create = False
    existing_fingerprint = None
    
    # Create new config if it doesn't exist or version is outdated
    if not config_path.exists():
        should_create = True
    else:
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)
            # Check if version is outdated
            if data.get("version") != VERSION:
                print("Configuration is outdated, creating a new one.")
                should_create = True
                # Preserve fingerprint if it exists
                if "fingerprint" in data:
                    existing_fingerprint = data["fingerprint"]
            else:
                return Config.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            print("Configuration is corrupt, creating a new one.")
            should_create = True

    if should_create:
        config_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = existing_fingerprint or str(uuid.uuid4())
        config = Config(version=VERSION, fingerprint=fingerprint)
        
        with open(config_path, 'w') as f:
            json.dump(config.to_dict(), f, indent=2)
        
        return config
    
    # Fallback in case logic fails
    config_dir.mkdir(parents=True, exist_ok=True)
    config = Config(version=VERSION, fingerprint=str(uuid.uuid4()))
    with open(config_path, 'w') as f:
        json.dump(config.to_dict(), f, indent=2)
    return config


# -----------------------------------------------------------------------------
# Network Utilities
# -----------------------------------------------------------------------------

def find_available_port(host: str, start_port: int, max_attempts: int = 50) -> Optional[int]:
    """Find an available TCP port by trying to bind to it."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                if port == start_port:
                    print(f"Port {port} is in use, searching for an available one...")
                continue
    return None

# -----------------------------------------------------------------------------
# Screenshot Scaling
# -----------------------------------------------------------------------------

class ScaleMode(Enum):
    EXACT = "exact"
    ASPECT_FIT = "aspect_fit"
    ASPECT_FILL = "aspect_fill"


def scale_image(image: Image.Image, width: Optional[int], height: Optional[int], mode: ScaleMode) -> Image.Image:
    """Scale an image according to the specified mode."""
    orig_width, orig_height = image.size
    
    if width is None and height is None:
        return image
    
    target_width = width or orig_width
    target_height = height or orig_height
    
    if mode == ScaleMode.EXACT:
        # Scale to exact dimensions, ignoring aspect ratio
        return image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    
    # Calculate aspect ratios
    orig_aspect = orig_width / orig_height
    target_aspect = target_width / target_height
    
    if mode == ScaleMode.ASPECT_FIT:
        # Scale to fit within target dimensions, maintaining aspect ratio
        if orig_aspect > target_aspect:
            # Original is wider, fit to width
            new_width = target_width
            new_height = int(target_width / orig_aspect)
        else:
            # Original is taller, fit to height
            new_height = target_height
            new_width = int(target_height * orig_aspect)
    else:  # ASPECT_FILL
        # Scale to fill target dimensions, maintaining aspect ratio
        if orig_aspect > target_aspect:
            # Original is wider, fit to height
            new_height = target_height
            new_width = int(target_height * orig_aspect)
        else:
            # Original is taller, fit to width
            new_width = target_width
            new_height = int(target_width / orig_aspect)
    
    return image.resize((new_width, new_height), Image.Resampling.LANCZOS)


# -----------------------------------------------------------------------------
# XDO Keyboard Support
# -----------------------------------------------------------------------------

class KeyEvent:
    def __init__(self, key: str, down: bool):
        self.key = key
        self.down = down


class XDOParser:
    """Parse XDO-style keyboard sequences like 'ctrl+c ctrl+v'."""
    
    MODIFIERS = {'ctrl', 'alt', 'shift', 'win', 'cmd', 'super', 'meta'}
    
    @staticmethod
    def parse(sequence: str) -> List[List[KeyEvent]]:
        """Parse XDO sequence into a list of key event groups."""
        commands = sequence.strip().split()
        result = []
        
        for command in commands:
            events = []
            parts = [p.lower() for p in command.split('+')]
            
            # Separate modifiers from regular keys
            modifiers = [p for p in parts if p in XDOParser.MODIFIERS]
            keys = [p for p in parts if p not in XDOParser.MODIFIERS]
            
            # Press modifiers
            for mod in modifiers:
                events.append(KeyEvent(mod, True))
            
            # Press and release regular keys
            for key in keys:
                events.append(KeyEvent(key, True))
                events.append(KeyEvent(key, False))
            
            # Release modifiers in reverse order
            for mod in reversed(modifiers):
                events.append(KeyEvent(mod, False))
            
            result.append(events)
        
        return result


def execute_xdo_sequence(sequence: str):
    """Execute an XDO-style keyboard sequence."""
    command_groups = XDOParser.parse(sequence)
    
    for group in command_groups:
        for event in group:
            # Map 'cmd' to 'win' for Windows compatibility
            key = event.key
            if key == 'cmd':
                key = 'win'
            
            if event.down:
                pyautogui.keyDown(key)
            else:
                pyautogui.keyUp(key)
        # Small delay between command groups
        time.sleep(0.01)


# -----------------------------------------------------------------------------
# PyAutoGUI Configuration
# -----------------------------------------------------------------------------

# Disable PyAutoGUI's default pause between commands for better performance
pyautogui.PAUSE = 0
# Keep fail-safe enabled for safety (move mouse to top-left corner to abort)
pyautogui.FAILSAFE = True

# -----------------------------------------------------------------------------
# Local API implementation
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    yield
    # Shutdown
    print("Shutting down...")
    
    # Shutdown the thread pool executor
    executor.shutdown(wait=False)
    print("Cleanup complete")

app = FastAPI(title="Cyberdriver", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def disable_buffering(request, call_next):
    """Middleware to ensure responses are not buffered."""
    response = await call_next(request)
    # Add headers to disable any proxy buffering
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/computer/display/screenshot", response_class=Response)
async def get_screenshot(
    width: Optional[int] = Query(None),
    height: Optional[int] = Query(None),
    mode: str = Query("exact")
) -> Response:
    """Capture the screen with optional scaling."""
    try:
        scale_mode = ScaleMode(mode.lower())
    except ValueError:
        scale_mode = ScaleMode.EXACT
    
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        # Convert to PIL Image
        pil_image = Image.frombytes('RGB', img.size, img.bgra, 'raw', 'BGRX')
        
        # Default to 1024x768 if no dimensions specified
        if width is None and height is None:
            width = 1024
            height = 768
        
        # Apply scaling
        if width is not None or height is not None:
            pil_image = scale_image(pil_image, width, height, scale_mode)
        
        # Convert to PNG
        output = BytesIO()
        pil_image.save(output, format='PNG')
        png_bytes = output.getvalue()
    
    return Response(content=png_bytes, media_type="image/png")


@app.get("/computer/display/dimensions")
async def get_dimensions() -> Dict[str, int]:
    """Return the width and height of the primary monitor."""
    screen_width, screen_height = pyautogui.size()
    return {"width": screen_width, "height": screen_height}


@app.post("/computer/input/keyboard/type")
async def post_keyboard_type(payload: Dict[str, str]):
    """Type a string of text."""
    text = payload.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")
    pyautogui.typewrite(text)
    return {}


@app.post("/computer/input/mouse/scroll")
async def post_mouse_scroll(payload: Dict[str, Any]):
    """Scroll the mouse wheel vertically or horizontally.
    
    Payload:
    - direction: 'up' | 'down' | 'left' | 'right'
    - amount: int number of scroll steps (clicks); positive integer
    - x, y: optional cursor position to move to before scrolling
    """
    direction = str(payload.get("direction", "")).lower()
    amount = payload.get("amount")
    if direction not in ("up", "down", "left", "right"):
        raise HTTPException(status_code=400, detail="Invalid direction: expected 'up', 'down', 'left', or 'right'")
    try:
        amount_int = int(amount)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid 'amount' (must be integer)")
    if amount_int < 0:
        raise HTTPException(status_code=400, detail="'amount' must be non-negative")
    if amount_int == 0:
        return {}
    x = payload.get("x")
    y = payload.get("y")
    if x is not None and y is not None:
        try:
            pyautogui.moveTo(int(x), int(y), duration=0)
        except Exception:
            pass
    # Map to pyautogui scroll functions
    if direction in ("up", "down"):
        clicks = amount_int if direction == "up" else -amount_int
        pyautogui.scroll(clicks)
    else:
        clicks = amount_int if direction == "right" else -amount_int
        # Use hscroll if available; fallback to shift+scroll
        try:
            pyautogui.hscroll(clicks)
        except AttributeError:
            # Fallback: hold shift and use vertical scroll as horizontal surrogate
            pyautogui.keyDown("shift")
            try:
                pyautogui.scroll(clicks)
            finally:
                pyautogui.keyUp("shift")
    return {}

@app.post("/computer/input/keyboard/key")
async def post_keyboard_key(payload: Dict[str, str]):
    """Execute XDO-style key sequence (e.g., 'ctrl+c', 'alt+tab')."""
    sequence = payload.get("text")
    if not sequence:
        raise HTTPException(status_code=400, detail="Missing 'text' field")
    
    execute_xdo_sequence(sequence)
    return {}


@app.get("/computer/input/mouse/position")
async def get_mouse_position() -> Dict[str, int]:
    """Return the current mouse position."""
    x, y = pyautogui.position()
    return {"x": x, "y": y}


@app.post("/computer/input/mouse/move")
async def post_mouse_move(payload: Dict[str, int]):
    """Move the mouse cursor instantly."""
    try:
        x = int(payload.get("x"))
        y = int(payload.get("y"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid 'x'/'y'")
    
    pyautogui.moveTo(x, y, duration=0)
    return {}


@app.post("/computer/input/mouse/click")
async def post_mouse_click(payload: Dict[str, Any]):
    """Click the mouse with full press/release control."""
    button = payload.get("button", "left").lower()
    if button not in ("left", "right"):
        raise HTTPException(status_code=400, detail="Invalid button: expected 'left' or 'right'")
    
    down = payload.get("down")
    x = payload.get("x")
    y = payload.get("y")
    
    # Move to position if specified
    if x is not None and y is not None:
        pyautogui.moveTo(int(x), int(y), duration=0)
    
    if down is None:
        # Full click
        pyautogui.click(button=button)
    elif down:
        pyautogui.mouseDown(button=button)
    else:
        pyautogui.mouseUp(button=button)
    
    return {}


# File system endpoints - Full access (localhost only)
@app.get("/computer/fs/list")
async def get_fs_list(path: str = Query(".")):
    """List directory contents with full file system access."""
    try:
        # Resolve path - no restrictions
        safe_path = pathlib.Path(path).expanduser().resolve()
        
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="Directory not found")
        
        if not safe_path.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        
        # List directory contents
        items = []
        try:
            for item in safe_path.iterdir():
                try:
                    stat = item.stat()
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": stat.st_size if item.is_file() else None,
                        "modified": stat.st_mtime
                    })
                except (PermissionError, OSError):
                    # Skip items we can't access
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": None,
                        "size": None,
                        "modified": None,
                        "error": "Permission denied"
                    })
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied to list directory")
        
        return {
            "path": str(safe_path),
            "entries": sorted(items, key=lambda x: (x.get("is_dir") is not True, x["name"]))
        }
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/computer/fs/read")
async def get_fs_read(path: str = Query(...)):
    """Read file contents with full file system access."""
    try:
        # Resolve path - no restrictions
        safe_path = pathlib.Path(path).expanduser().resolve()
        
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        if not safe_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")
        
        # Check file size (limit to 100MB for safety)
        if safe_path.stat().st_size > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (>100MB)")
        
        # Read file and encode as base64
        try:
            with open(safe_path, "rb") as f:
                content = f.read()
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied to read file")
        
        return {
            "path": str(safe_path),
            "content": base64.b64encode(content).decode("utf-8"),
            "size": len(content)
        }
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/computer/fs/write")
async def post_fs_write(payload: Dict[str, Any]):
    """Write file contents with full file system access.
    
    Expects:
    - path: Target file path
    - content: Base64 encoded file content
    - mode: (optional) Write mode - "write" (default) or "append"
    """
    file_path = payload.get("path")
    content = payload.get("content")
    mode = payload.get("mode", "write")
    
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing 'path' field")
    if not content:
        raise HTTPException(status_code=400, detail="Missing 'content' field")
    
    try:
        # Decode base64 content
        file_data = base64.b64decode(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 content: {e}")
    
    try:
        # Resolve path - no restrictions
        safe_path = pathlib.Path(file_path).expanduser().resolve()
        
        # If path doesn't specify a directory, default to CyberdeskTransfers
        if not safe_path.parent.exists() and str(safe_path.parent) == ".":
            safe_path = pathlib.Path.home() / "CyberdeskTransfers" / safe_path.name
        
        # Create parent directories if they don't exist
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        write_mode = "ab" if mode == "append" else "wb"
        try:
            with open(safe_path, write_mode) as f:
                f.write(file_data)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Permission denied to write file")
        
        # Get file info for response
        stat = safe_path.stat()
        
        return {
            "path": str(safe_path),
            "size": stat.st_size,
            "modified": stat.st_mtime
        }
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")


# PowerShell endpoints
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=5)



def execute_powershell_command(command: str, session_id: str, working_directory: Optional[str] = None, same_session: bool = True, timeout: float = 30.0):
    """Execute PowerShell command in a session with timeout."""
    import subprocess
    import threading
    
    # For clean output, we'll use a different approach - execute each command as a separate process
    # This avoids the echo/prompt issues with interactive PowerShell
    
    powershell_cmd = "pwsh" if platform.system() != "Windows" else "powershell"
    try:
        # Test if pwsh is available on Windows
        if platform.system() == "Windows":
            subprocess.run(["pwsh", "-Version"], capture_output=True, check=True, timeout=5)
            powershell_cmd = "pwsh"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Create command script that handles working directory
    script_lines = []
    if working_directory:
        script_lines.append(f"Set-Location -Path '{working_directory}'")
    script_lines.append(command)
    
    # Join with semicolons for single-line execution
    full_script = "; ".join(script_lines)
    
    # Build PowerShell arguments for clean output
    ps_args = [
        powershell_cmd,
        "-NoLogo",           # No startup banner
        "-NoProfile",        # Don't load profile
        "-NonInteractive",   # No prompts
        "-ExecutionPolicy", "Bypass",
        "-OutputFormat", "Text",  # Plain text output
        "-Command", full_script   # Execute directly
    ]
    
    # Setup process
    startupinfo = None
    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    
    try:
        # Run the command
        process = subprocess.Popen(
            ps_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo,
            encoding='utf-8',
            errors='replace'
        )
        
        # Wait for completion with timeout
        stdout, stderr = process.communicate(timeout=timeout)
        
        # Clean output - remove empty lines at start/end
        stdout_lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
        stderr_lines = [line.rstrip() for line in stderr.splitlines() if line.strip()]
        
        return {
            "stdout": "\n".join(stdout_lines),
            "stderr": "\n".join(stderr_lines),
            "exit_code": process.returncode,
            "session_id": session_id
        }
        
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return {
            "stdout": "",
            "stderr": "Command timed out",
            "exit_code": -1,
            "session_id": session_id,
            "error": f"Command timed out after {timeout} seconds"
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "session_id": session_id,
            "error": str(e)
        }

@app.post("/computer/shell/powershell/simple")
async def simple_powershell_test():
    """Ultra-simple PowerShell test."""
    import subprocess
    
    print("\n=== SIMPLE POWERSHELL TEST ===")
    
    try:
        # Run a simple command directly
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Write-Output 'Hello World'"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        print(f"Return code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/computer/shell/powershell/test")
async def test_powershell():
    """Test PowerShell functionality with a simple command."""
    import subprocess
    import threading
    
    print("\n=== POWERSHELL TEST ===")
    
    # Start a simple PowerShell process
    ps_args = ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    print(f"Starting PowerShell with: {ps_args}")
    
    try:
        process = subprocess.Popen(
            ps_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        print(f"Process started, PID: {process.pid}")
        
        # Test 1: Simple echo
        test_cmd = 'Write-Output "Hello from PowerShell"\n'
        print(f"Sending: {test_cmd.strip()}")
        process.stdin.write(test_cmd)
        process.stdin.flush()
        
        # Try to read output with timeout
        output = []
        def read_output():
            try:
                line = process.stdout.readline()
                if line:
                    output.append(line.strip())
                    print(f"Got output: {line.strip()}")
            except Exception as e:
                print(f"Read error: {e}")
        
        for i in range(10):  # Try for 1 second
            t = threading.Thread(target=read_output)
            t.daemon = True
            t.start()
            t.join(0.1)
            if output:
                break
        
        if not output:
            print("No output received!")
            
            # Check if process is still alive
            if process.poll() is not None:
                print(f"Process died with code: {process.poll()}")
                stderr = process.stderr.read()
                if stderr:
                    print(f"Stderr: {stderr}")
        
        # Kill the process
        process.terminate()
        
        return {"test": "complete", "output": output}
        
    except Exception as e:
        print(f"Test error: {e}")
        return {"error": str(e)}

@app.post("/computer/shell/powershell/exec")
async def post_powershell_exec(payload: Dict[str, Any]):
    """Execute PowerShell command with optional session management."""
    command = payload.get("command")
    same_session = payload.get("same_session", True)
    working_directory = payload.get("working_directory")
    session_id = payload.get("session_id", str(uuid.uuid4()))
    timeout = payload.get("timeout", 30.0)  # Default 30 second timeout
    
    if not command:
        raise HTTPException(status_code=400, detail="Missing 'command' field")
    
    try:
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            execute_powershell_command,
            command,
            session_id,
            working_directory,
            same_session,
            timeout
        )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to execute command: {e}")

@app.post("/computer/shell/powershell/session")
async def post_powershell_session(payload: Dict[str, Any]):
    """Manage PowerShell sessions (compatibility endpoint)."""
    action = payload.get("action")
    session_id = payload.get("session_id")
    
    if action not in ["create", "destroy"]:
        raise HTTPException(status_code=400, detail="Invalid action. Must be 'create' or 'destroy'")
    
    if action == "create":
        # Sessions are no longer maintained - each command runs independently
        new_session_id = str(uuid.uuid4())
        return {"session_id": new_session_id, "message": "Session ID generated (sessions are now stateless)"}
    
    elif action == "destroy":
        # No-op since we don't maintain sessions anymore
        return {"message": "Session destroyed (no-op in stateless mode)"}


# -----------------------------------------------------------------------------
# WebSocket Tunnel with Proper Protocol
# -----------------------------------------------------------------------------

class TunnelClient:
    """WebSocket tunnel client with proper message framing."""
    
    def __init__(self, host: str, port: int, secret: str, target_port: int, config: Config):
        self.host = host
        self.port = port
        self.secret = secret
        self.target_port = target_port
        self.config = config
        self.min_sleep = 1
        self.max_sleep = 16
        
    async def run(self):
        """Run the tunnel with exponential backoff reconnection."""
        sleep_time = self.min_sleep
        
        while True:
            try:
                await self._connect_and_run()
                # Reset sleep time on successful connection
                sleep_time = self.min_sleep
            except Exception as e:
                print(f"Tunnel error: {e}")
                print(f"Retrying in {sleep_time} seconds...")
                await asyncio.sleep(sleep_time)
                sleep_time = min(sleep_time * 2, self.max_sleep)
    
    async def _connect_and_run(self):
        """Connect to control server and handle messages."""
        # Clean up host
        host = self.host
        for prefix in ['http://', 'https://']:
            if host.startswith(prefix):
                host = host[len(prefix):]
        host = host.rstrip('/')
        
        uri = f"wss://{host}:{self.port}/tunnel/ws"
        
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {self.secret}",
            "X-PIGLET-FINGERPRINT": self.config.fingerprint,
            "X-PIGLET-VERSION": self.config.version,
        }
        
        # Use compatibility wrapper for connection
        websocket = await connect_with_headers(uri, headers)
        async with websocket:
            # Print success message with green checkmark
            if platform.system() == "Windows":
                # Check if colors are supported
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                    green = '\033[92m'
                    white = '\033[97m'
                    reset = '\033[0m'
                    print(f"{green}✓{reset} {white}Connected!{reset} Forwarding to http://127.0.0.1:{self.target_port}")
                except:
                    print(f"√ Connected! Forwarding to http://127.0.0.1:{self.target_port}")
            else:
                green = '\033[92m'
                white = '\033[97m'
                reset = '\033[0m'
                print(f"{green}✓{reset} {white}Connected!{reset} Forwarding to http://127.0.0.1:{self.target_port}")
            
            # Message handling state
            request_meta = None
            body_buffer = bytearray()
            
            # Configure httpx client with no buffering and reasonable timeouts
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            ) as http_client:
                async for message in websocket:
                    if isinstance(message, str):
                        if message == "end":
                            if request_meta:
                                # Process complete request
                                response = await self._forward_request(
                                    request_meta, bytes(body_buffer), http_client
                                )
                                
                                # Send response
                                await self._send_response(websocket, request_meta, response)
                                
                                # Reset state
                                request_meta = None
                                body_buffer.clear()
                        else:
                            # New request metadata
                            request_meta = json.loads(message)
                            body_buffer.clear()
                    else:
                        # Binary body chunk
                        body_buffer.extend(message)

                # If we exit the async for without an exception, the server closed gracefully
                # Ensure we signal this to the reconnection loop by raising to trigger backoff
                raise RuntimeError("WebSocket closed by server")
    
    async def _forward_request(self, meta: dict, body: bytes, client: httpx.AsyncClient) -> dict:
        """Forward request to local API."""
        method = meta["method"].upper()
        path = meta["path"]
        query = meta.get("query", "")
        headers = meta.get("headers", {})
        
        url = f"http://127.0.0.1:{self.target_port}{path}"
        if query:
            url += f"?{query}"
        
        try:
            # IMPORTANT: Use stream=True to avoid buffering the entire response
            async with client.stream(method, url, headers=headers, content=body) as response:
                print(f"{method} {path} -> {response.status_code}")
                
                # Read the response body immediately to avoid buffering
                body_chunks = []
                async for chunk in response.aiter_bytes():
                    body_chunks.append(chunk)
                
                return {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": b''.join(body_chunks),
                }
        except Exception as e:
            return {
                "status": 500,
                "headers": {"content-type": "text/plain"},
                "body": str(e).encode(),
            }
    
    async def _send_response(self, websocket, request_meta: dict, response: dict):
        """Send response back through tunnel."""
        # Send response metadata
        resp_meta = {
            "requestId": request_meta["requestId"],
            "status": response["status"],
            "headers": response["headers"],
        }
        await websocket.send(json.dumps(resp_meta))
        
        # Send body in chunks (16KB max per chunk)
        body = response["body"]
        if body:
            chunk_size = 16 * 1024
            for i in range(0, len(body), chunk_size):
                chunk = body[i:i + chunk_size]
                await websocket.send(chunk)
        
        # Send end marker
        await websocket.send("end")


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def run_server(port: int):
    """Run the FastAPI server."""
    uvicorn.run(app, host="0.0.0.0", port=port)


async def run_server_async(port: int):
    """Run the FastAPI server asynchronously."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def run_join(host: str, port: int, secret: str, target_port: int):
    """Run both API server and tunnel client."""
    config = get_config()
    
    # Find an available port for the local server, starting with the one provided
    actual_target_port = find_available_port("127.0.0.1", target_port)
    if actual_target_port is None:
        print(f"Error: Could not find an available port starting from {target_port}.")
        sys.exit(1)
    
    if actual_target_port != target_port:
        print(f"Using available port {actual_target_port} for local server.")

    # Start API server asynchronously in the same event loop
    server_task = asyncio.create_task(run_server_async(actual_target_port))
    
    # Give server time to start
    await asyncio.sleep(1)
    
    # Start tunnel client
    tunnel = TunnelClient(host, port, secret, actual_target_port, config)
    
    # Run both concurrently
    try:
        await asyncio.gather(server_task, tunnel.run())
    except KeyboardInterrupt:
        print("\nShutting down...")


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    print("\n\nReceived interrupt signal. Shutting down gracefully...")
    # The finally block in main() will handle cleanup
    sys.exit(0)


def print_banner_no_color(mode="default"):
    """Print banner without colors for terminals that don't support ANSI."""
    banner = [
        " ██████╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗ ██████╗ ██╗██╗   ██╗███████╗██████╗ ",
        "██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔══██╗██║██║   ██║██╔════╝██╔══██╗",
        "██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║  ██║██████╔╝██║██║   ██║█████╗  ██████╔╝",
        "██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗██║  ██║██╔══██╗██║╚██╗ ██╔╝██╔══╝  ██╔══██╗",
        "╚██████╗   ██║   ██████╔╝███████╗██║  ██║██████╔╝██║  ██║██║ ╚████╔╝ ███████╗██║  ██║",
        " ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝"
    ]
    
    for line in banner:
        print(line)
    
    print()
    
    if mode == "connecting":
        print("Connecting to Cyberdesk Cloud...")
    else:
        print("Get started:")
        print("→ cyberdriver join --secret YOUR_API_KEY")
    
    print("→ Run -h for help")
    print("→ Visit https://docs.cyberdesk.io for documentation")
    print()


def print_banner(mode="default"):
    """Print a cool gradient banner for Cyberdriver.
    
    Args:
        mode: "default" for normal banner, "connecting" for join command
    """
    # Enable Windows terminal colors if needed
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable ANSI escape sequences
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except:
            # If we can't enable ANSI, disable colors
            print_banner_no_color(mode)
            return
    
    # Colors
    white = '\033[97m'
    blue = '\033[38;2;0;123;255m'
    purple = '\033[38;2;147;51;234m'
    green = '\033[92m'
    reset = '\033[0m'
    
    banner = [
        " ██████╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗ ██████╗ ██╗██╗   ██╗███████╗██████╗ ",
        "██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔══██╗██║██║   ██║██╔════╝██╔══██╗",
        "██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║  ██║██████╔╝██║██║   ██║█████╗  ██████╔╝",
        "██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗██║  ██║██╔══██╗██║╚██╗ ██╔╝██╔══╝  ██╔══██╗",
        "╚██████╗   ██║   ██████╔╝███████╗██║  ██║██████╔╝██║  ██║██║ ╚████╔╝ ███████╗██║  ██║",
        " ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝"
    ]
    
    # Print banner with left-to-right gradient
    for line in banner:
        output = ""
        line_length = len(line)
        for i, char in enumerate(line):
            # Calculate gradient position (0 to 1)
            position = i / max(line_length - 1, 1)
            
            # Interpolate between blue and purple
            r = int(0 + (147 - 0) * position)
            g = int(123 + (51 - 123) * position)
            b = int(255 + (234 - 255) * position)
            
            color = f'\033[38;2;{r};{g};{b}m'
            output += f"{color}{char}"
        print(f"{output}{reset}")
    
    print()
    
    # Different messages based on mode
    if mode == "connecting":
        print(f"{white}Connecting to Cyberdesk Cloud...{reset}")
    else:
        print(f"{white}Get started:{reset}")
        print(f"{white}→ {blue}cyberdriver join --secret{reset} {white}YOUR_API_KEY{reset}")
    
    # Always show help and docs
    print(f"{white}→ Run {blue}-h{reset} {white}for help{reset}")
    print(f"{white}→ Visit {blue}https://docs.cyberdesk.io{reset} for documentation")
    print()


def main():
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Print banner if no arguments or help requested
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ['-h', '--help']):
        print_banner()
    
    parser = argparse.ArgumentParser(
        description="Remote computer control via Cyberdesk",
        epilog="",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False  # We'll add custom help
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {VERSION}"
    )
    parser.add_argument(
        "-h", "--help",
        action="store_true",
        help="Show help"
    )
    
    subparsers = parser.add_subparsers(dest="command", metavar="")
    
    # start command
    start_parser = subparsers.add_parser(
        "start", 
        help="Start local server",
        description="Start Cyberdriver API server locally for testing"
    )
    start_parser.add_argument("--port", type=int, default=3000, help="Port (default: 3000)")
    
    # join command
    join_parser = subparsers.add_parser(
        "join", 
        help="Connect to Cyberdesk Cloud",
        description="Connect your machine to Cyberdesk Cloud for remote control"
    )
    join_parser.add_argument("--secret", required=True, help="Your API key from Cyberdesk")
    join_parser.add_argument("--host", default="api.cyberdesk.io", help="Control server (default: api.cyberdesk.io)")
    join_parser.add_argument("--port", type=int, default=443, help="Control server port (default: 443)")
    join_parser.add_argument("--target-port", type=int, default=3000, help="Local port (default: 3000)")
    
    args = parser.parse_args()

    # Handle help or no command
    if not args.command or args.help:
        if not (len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ['-h', '--help'])):
            print_banner()
        print("Commands:")
        print("  join --secret KEY  Connect to Cyberdesk Cloud")
        print("  start              Start local server")
        print()
        print("For more info: cyberdriver <command> -h")
        sys.exit(0)
    
    # Show banner for join command
    if args.command == "join":
        print_banner(mode="connecting")
    
    # Disable Windows console QuickEdit mode to prevent output blocking
    disable_windows_console_quickedit()
    
    try:
        if args.command == "start":
            actual_port = find_available_port("0.0.0.0", args.port)
            if actual_port is None:
                print(f"Error: Could not find an available port starting from {args.port}.")
                sys.exit(1)
            
            # Try to print with checkmark
            if platform.system() == "Windows":
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                    print(f"✓ Cyberdriver server starting on http://0.0.0.0:{actual_port}")
                except:
                    print(f"√ Cyberdriver server starting on http://0.0.0.0:{actual_port}")
            else:
                print(f"✓ Cyberdriver server starting on http://0.0.0.0:{actual_port}")
            run_server(actual_port)

        elif args.command == "join":
            asyncio.run(run_join(
                args.host, args.port, args.secret, args.target_port
            ))
    except KeyboardInterrupt:
        print("\n\nKeyboard interrupt received. Shutting down...")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        sys.exit(1)
    finally:
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
