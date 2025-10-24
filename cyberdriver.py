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
import random
import tempfile
import shutil
from typing import Dict, List, Optional, Tuple, Union, Any
from enum import Enum
from dataclasses import dataclass
from io import BytesIO
from contextlib import asynccontextmanager

import httpx
import mss
import numpy as np
import pyautogui
import pyperclip
from PIL import Image
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
import uvicorn
import websockets

# -----------------------------------------------------------------------------
# Windows Administrator Check and Elevation
# -----------------------------------------------------------------------------

def is_running_as_admin() -> bool:
    """Check if the process is running with administrator privileges on Windows."""
    if platform.system() != "Windows":
        return False
    
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin_elevation():
    """Restart the current process with administrator privileges on Windows."""
    if platform.system() != "Windows":
        print("Error: Admin elevation is only available on Windows")
        return False
    
    try:
        import ctypes
        
        # Get the current script/executable path and arguments
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            script = sys.executable
        else:
            # Running as Python script
            script = os.path.abspath(sys.argv[0])
        
        # Build command line arguments
        params = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in sys.argv[1:]])
        
        print("\n" + "="*60)
        print("Administrator Privileges Required")
        print("="*60)
        print("\nBlack screen recovery requires administrator privileges.")
        print("A UAC prompt will appear to elevate Cyberdriver.\n")
        print("Restarting with administrator privileges...")
        print("="*60 + "\n")
        
        # ShellExecute to run as admin
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,           # hwnd
            "runas",        # operation (runas = run as administrator)
            sys.executable if getattr(sys, 'frozen', False) else "python",  # file
            f'"{script}" {params}' if not getattr(sys, 'frozen', False) else params,  # parameters
            None,           # directory
            1               # show command (SW_SHOWNORMAL)
        )
        
        # If ShellExecute succeeds, ret > 32
        if ret > 32:
            # Exit this non-admin process
            sys.exit(0)
        else:
            print(f"Failed to request elevation (error code: {ret})")
            return False
            
    except Exception as e:
        print(f"Failed to request elevation: {e}")
        return False


# -----------------------------------------------------------------------------
# Amyuni Virtual Display Driver Management
# -----------------------------------------------------------------------------

def get_driver_files_path() -> Optional[pathlib.Path]:
    """Get the path to the bundled Amyuni driver files.
    
    Supports both running as script and as PyInstaller executable.
    """
    if platform.system() != "Windows":
        return None
    
    # Check if running as PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        base_path = pathlib.Path(sys._MEIPASS)
    else:
        # Running as script - drivers should be in ./amyuni_driver directory
        base_path = pathlib.Path(__file__).parent
    
    driver_path = base_path / "amyuni_driver"
    
    if driver_path.exists():
        return driver_path
    
    return None


def is_virtual_display_driver_installed() -> bool:
    """Check if the Amyuni virtual display driver is already installed.
    
    Uses Windows Device Manager query to check for the actual device.
    """
    if platform.system() != "Windows":
        return False
    
    try:
        # Use PowerShell to check Device Manager for the virtual display
        # This is more reliable than deviceinstaller's find command
        ps_check = """
        $device = Get-PnpDevice | Where-Object { 
            $_.FriendlyName -like "*USB Mobile Monitor*" -or 
            $_.FriendlyName -like "*usbmmidd*" 
        }
        if ($device) { 
            exit 0 
        } else { 
            exit 1 
        }
        """
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_check],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        # Exit code 0 means device found
        return result.returncode == 0
        
    except Exception as e:
        # If PowerShell check fails, fall back to assuming not installed
        # Better to attempt installation than skip it
        return False


def install_virtual_display_driver() -> bool:
    """Install and enable the Amyuni virtual display driver.
    
    Returns True if successful, False otherwise.
    """
    if platform.system() != "Windows":
        print("Virtual display driver is only supported on Windows")
        return False
    
    if not is_running_as_admin():
        print("Error: Administrator privileges required to install virtual display driver")
        return False
    
    try:
        driver_path = get_driver_files_path()
        if not driver_path:
            print("Error: Amyuni driver files not found")
            print("Please ensure the amyuni_driver folder exists in the cyberdriver directory")
            return False
        
        # Determine which deviceinstaller to use
        is_64bit = platform.machine().endswith('64')
        installer_name = "deviceinstaller64.exe" if is_64bit else "deviceinstaller.exe"
        installer_path = driver_path / installer_name
        inf_path = driver_path / "usbmmidd.inf"
        
        if not installer_path.exists():
            print(f"Error: {installer_name} not found in amyuni_driver folder")
            return False
        
        if not inf_path.exists():
            print("Error: usbmmidd.inf not found in amyuni_driver folder")
            return False
        
        print("\nInstalling Amyuni virtual display driver...")
        
        # Install the driver
        print(f"Running: {installer_name} install usbmmidd.inf usbmmidd")
        result = subprocess.run(
            [str(installer_path), "install", str(inf_path), "usbmmidd"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(driver_path)
        )
        
        if result.returncode != 0:
            print(f"Error installing driver: {result.stderr or result.stdout}")
            return False
        
        print("✓ Driver installed successfully")
        
        # Enable the virtual display
        print(f"Running: {installer_name} enableidd 1")
        result = subprocess.run(
            [str(installer_path), "enableidd", "1"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(driver_path)
        )
        
        if result.returncode != 0:
            print(f"Warning: Could not enable virtual display: {result.stderr or result.stdout}")
            print("You may need to enable it manually or restart the system")
            return False
        
        print("✓ Virtual display enabled successfully")
        print("\nℹ  You can now configure the virtual display in Windows Display Settings")
        print("   The virtual display will persist across reboots")
        
        return True
        
    except subprocess.TimeoutExpired:
        print("Error: Driver installation timed out")
        return False
    except Exception as e:
        print(f"Error installing virtual display driver: {e}")
        return False


def setup_persistent_display_if_needed() -> bool:
    """Check and install virtual display driver if not already installed.
    
    Returns True if driver is ready (already installed or just installed), False otherwise.
    """
    if platform.system() != "Windows":
        print("Note: Persistent virtual display is only supported on Windows")
        return False
    
    if not is_running_as_admin():
        print("Error: Administrator privileges required for persistent display setup")
        return False
    
    # Check if already installed
    if is_virtual_display_driver_installed():
        print("✓ Virtual display driver already installed")
        
        # Make sure it's enabled
        try:
            driver_path = get_driver_files_path()
            if driver_path:
                is_64bit = platform.machine().endswith('64')
                installer_name = "deviceinstaller64.exe" if is_64bit else "deviceinstaller.exe"
                installer_path = driver_path / installer_name
                
                print("Ensuring virtual display is enabled...")
                result = subprocess.run(
                    [str(installer_path), "enableidd", "1"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=str(driver_path)
                )
                
                if result.returncode == 0:
                    print("✓ Virtual display is enabled")
                else:
                    print(f"Note: {result.stderr or result.stdout}")
        except Exception as e:
            print(f"Note: Could not verify display state: {e}")
        
        return True
    
    # Not installed, install it
    print("\nVirtual display driver not detected. Installing...")
    return install_virtual_display_driver()


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
VERSION = "0.0.25"

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
    """Execute an XDO-style keyboard sequence.
    
    Args:
        sequence: XDO-style key sequence (e.g., 'ctrl+c')
    """
    command_groups = XDOParser.parse(sequence)
    
    # On Windows: use native SendInput with scan codes (Citrix-compatible)
    if platform.system() == "Windows":
        try:
            for group in command_groups:
                for event in group:
                    key = event.key
                    if key == 'cmd':
                        key = 'win'
                    
                    _press_key_with_scancode(key, key_up=not event.down)
            return
        except Exception as e:
            print(f"Warning: SendInput failed ({e}), falling back to PyAutoGUI")
    
    # Fallback: use PyAutoGUI (for macOS/Linux or if SendInput fails)
    for group in command_groups:
        for event in group:
            key = event.key
            if key == 'cmd':
                key = 'win'
            
            if event.down:
                pyautogui.keyDown(key)
            else:
                pyautogui.keyUp(key)


# -----------------------------------------------------------------------------
# PyAutoGUI Configuration
# -----------------------------------------------------------------------------

# Disable PyAutoGUI's default pause between commands for better performance
pyautogui.PAUSE = 0
# Disable fail-safe for virtual display environments (RustDesk, RDP, etc.)
# where display changes can trigger false positives
pyautogui.FAILSAFE = False

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
@app.post("/internal/keepalive/remote/activity")
async def post_remote_keepalive_activity():
    """Record remote activity to delay/suppress keepalive runs.
    
    Mirrors local activity semantics. Returns 204 immediately.
    """
    try:
        manager = getattr(app.state, "keepalive_manager", None)
        if manager is not None:
            # Clear countdown line to avoid mixed output, then reprint after
            try:
                if hasattr(manager, "_clear_countdown_line"):
                    manager._clear_countdown_line()
            except Exception:
                pass
            # Apply small jitter around the threshold to avoid rigid cadence
            now = time.time()
            jitter_seconds = random.uniform(-7.0, 7.0)
            manager.last_activity_ts = now - jitter_seconds
            manager._next_allowed_ts = manager.last_activity_ts + manager.threshold_seconds
            print("RemoteKeepalive: activity signal received; idle timer reset")
            try:
                if hasattr(manager, "_print_countdown"):
                    manager._print_countdown()
            except Exception:
                pass
        return Response(status_code=204)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/internal/keepalive/remote/enable")
async def post_remote_keepalive_enable():
    """Enable keepalive on this instance (used by Cloud for remote keepalive coordination)."""
    try:
        manager = getattr(app.state, "keepalive_manager", None)
        if manager is not None:
            manager.enabled = True
            # Wake scheduler and redraw countdown
            try:
                if manager._schedule_event is not None:
                    manager._schedule_event.set()
                if hasattr(manager, "_print_countdown"):
                    manager._print_countdown()
            except Exception:
                pass
            print("RemoteKeepalive: enabled")
        return Response(status_code=204)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/internal/keepalive/remote/disable")
async def post_remote_keepalive_disable():
    """Disable keepalive on this instance (used by Cloud for remote keepalive coordination)."""
    try:
        manager = getattr(app.state, "keepalive_manager", None)
        if manager is not None:
            manager.enabled = False
            # Clear countdown and wake scheduler to idle
            try:
                if hasattr(manager, "_clear_countdown_line"):
                    manager._clear_countdown_line()
                if manager._schedule_event is not None:
                    manager._schedule_event.set()
            except Exception:
                pass
            print("RemoteKeepalive: disabled")
        return Response(status_code=204)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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


# -----------------------------------------------------------------------------
# Windows SendInput Implementation (Native Windows keyboard input)
# -----------------------------------------------------------------------------

# Hardware scan code mappings (PS/2 Set 1 scan codes)
# These work correctly with Citrix/VDI unlike virtual key codes
LETTER_SCANCODES = {
    'A': 0x1E, 'B': 0x30, 'C': 0x2E, 'D': 0x20, 'E': 0x12, 'F': 0x21,
    'G': 0x22, 'H': 0x23, 'I': 0x17, 'J': 0x24, 'K': 0x25, 'L': 0x26,
    'M': 0x32, 'N': 0x31, 'O': 0x18, 'P': 0x19, 'Q': 0x10, 'R': 0x13,
    'S': 0x1F, 'T': 0x14, 'U': 0x16, 'V': 0x2F, 'W': 0x11, 'X': 0x2D,
    'Y': 0x15, 'Z': 0x2C,
}
NUMBER_SCANCODES = {
    '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06,
    '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B,
}
SYMBOL_SCANCODES = {
    '-': 0x0C, '=': 0x0D, '[': 0x1A, ']': 0x1B, ';': 0x27,
    "'": 0x28, '`': 0x29, '\\': 0x2B, ',': 0x33, '.': 0x34,
    '/': 0x35, ' ': 0x39, '\t': 0x0F, '\n': 0x1C,
}
# Special keys
SPECIAL_KEY_SCANCODES = {
    'escape': 0x01, 'esc': 0x01,
    'backspace': 0x0E,
    'tab': 0x0F,
    'enter': 0x1C, 'return': 0x1C,
    'capslock': 0x3A,
    'home': 0xE047, 'end': 0xE04F,
    'pageup': 0xE049, 'pagedown': 0xE051,
    'insert': 0xE052, 'delete': 0xE053,
    'up': 0xE048, 'down': 0xE050, 'left': 0xE04B, 'right': 0xE04D,
    'uparrow': 0xE048, 'downarrow': 0xE050, 'leftarrow': 0xE04B, 'rightarrow': 0xE04D,
    'f1': 0x3B, 'f2': 0x3C, 'f3': 0x3D, 'f4': 0x3E, 'f5': 0x3F, 'f6': 0x40,
    'f7': 0x41, 'f8': 0x42, 'f9': 0x43, 'f10': 0x44, 'f11': 0x57, 'f12': 0x58,
}
# Modifier keys
MODIFIER_SCANCODES = {
    'shift': 0x2A, 'lshift': 0x2A, 'rshift': 0x36,
    'ctrl': 0x1D, 'control': 0x1D, 'lcontrol': 0x1D, 'rcontrol': 0xE01D,
    'alt': 0x38, 'lalt': 0x38, 'ralt': 0xE038,
    'win': 0xE05B, 'windows': 0xE05B, 'lwin': 0xE05B, 'rwin': 0xE05C,
    'super': 0xE05B, 'cmd': 0xE05B, 'command': 0xE05B,
}
# Shifted versions (send shift + base key)
SHIFT_MAP = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
    '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=',
    '{': '[', '}': ']', ':': ';', '"': "'", '~': '`', '|': '\\',
    '<': ',', '>': '.', '?': '/',
}

def _win32_send_key(scan_code: int, key_up: bool = False):
    """Low-level helper to send a single key event using Windows SendInput with scan code."""
    import ctypes
    from ctypes import wintypes
    
    # Windows constants
    INPUT_KEYBOARD = 1
    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_EXTENDEDKEY = 0x0001
    
    # Structures (define once per call is fine for Python, gets cached by ctypes)
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), 
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]
    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]
    class _InputUnion(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]
    
    # Check for extended key (scan codes > 0xFF need KEYEVENTF_EXTENDEDKEY)
    flags = KEYEVENTF_SCANCODE
    if scan_code > 0xFF:
        flags |= KEYEVENTF_EXTENDEDKEY
        scan_code = scan_code & 0xFF  # Use only low byte
    if key_up:
        flags |= KEYEVENTF_KEYUP
    
    # Build and send input event
    input_event = INPUT()
    input_event.type = INPUT_KEYBOARD
    input_event.ki = KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=flags, time=0, dwExtraInfo=None)
    ctypes.windll.user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(INPUT))


def _type_with_win32_sendinput(text: str):
    """Type text using Windows SendInput API with hardware scan codes."""
    LSHIFT_SCANCODE = 0x2A
    
    for char in text:
        upper_char = char.upper()
        scan_code = None
        needs_shift = False
        
        # Determine scan code and whether shift is needed
        if char in SHIFT_MAP:
            base_char = SHIFT_MAP[char]
            scan_code = NUMBER_SCANCODES.get(base_char) or SYMBOL_SCANCODES.get(base_char)
            needs_shift = True
        elif char.isupper() and upper_char in LETTER_SCANCODES:
            scan_code = LETTER_SCANCODES[upper_char]
            needs_shift = True
        elif upper_char in LETTER_SCANCODES:
            scan_code = LETTER_SCANCODES[upper_char]
        elif char in NUMBER_SCANCODES:
            scan_code = NUMBER_SCANCODES[char]
        elif char in SYMBOL_SCANCODES:
            scan_code = SYMBOL_SCANCODES[char]
        
        if scan_code is None:
            print(f"Warning: Character '{char}' not supported by scan code method, skipping")
            continue
        
        # Send key events
        if needs_shift:
            _win32_send_key(LSHIFT_SCANCODE, key_up=False)
        _win32_send_key(scan_code, key_up=False)
        _win32_send_key(scan_code, key_up=True)
        if needs_shift:
            _win32_send_key(LSHIFT_SCANCODE, key_up=True)


def _press_key_with_scancode(key: str, key_up: bool = False):
    """Press or release a key using Windows SendInput with scan code.
    
    Args:
        key: Key name (e.g., 'tab', 'ctrl', 'a')
        key_up: True to release, False to press
    """
    key_lower = key.lower()
    
    # Check all scan code maps
    scan_code = (MODIFIER_SCANCODES.get(key_lower) or 
                 SPECIAL_KEY_SCANCODES.get(key_lower) or
                 LETTER_SCANCODES.get(key.upper()) or
                 NUMBER_SCANCODES.get(key) or
                 SYMBOL_SCANCODES.get(key))
    
    if scan_code is None:
        raise ValueError(f"Unknown key: {key}")
    
    _win32_send_key(scan_code, key_up=key_up)


@app.post("/computer/input/keyboard/type")
async def post_keyboard_type(payload: Dict[str, str]):
    """Type a string of text."""
    text = payload.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text' field")
    
    # On Windows: use native SendInput with scan codes (Citrix-compatible)
    # On macOS/Linux: use PyAutoGUI
    if platform.system() == "Windows":
        try:
            _type_with_win32_sendinput(text)
            return {}
        except Exception as e:
            print(f"Warning: SendInput failed ({e}), falling back to PyAutoGUI")
    
    # Fallback for non-Windows or if SendInput fails
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


@app.post("/computer/copy_to_clipboard")
async def post_copy_to_clipboard(payload: Dict[str, str]):
    """Execute Ctrl+C and return clipboard contents with the specified key name.
    
    Payload:
        - text: The key name for the copied data
    """
    key_name = payload.get("text")
    if not key_name:
        raise HTTPException(status_code=400, detail="Missing 'text' field (key name)")
    
    try:
        # Clear clipboard first to detect if copy actually worked
        pyperclip.copy('')
        
        # Execute Ctrl+C using existing XDO sequence handler (uses SendInput on Windows)
        execute_xdo_sequence('ctrl+c')
        
        # Citrix/RDP clipboard sync can be VERY slow - use aggressive retry with long waits
        # Start with longer initial delay for Citrix
        clipboard_content = ""
        max_attempts = 8  # More attempts for slow Citrix environments
        
        for attempt in range(max_attempts):
            # Progressive delays: 200ms, 300ms, 400ms, 500ms, 600ms, 700ms, 800ms, 900ms
            await asyncio.sleep(0.2 + (attempt * 0.1))
            
            try:
                clipboard_content = pyperclip.paste()
                if clipboard_content:  # Got content, break early
                    print(f"✓ Clipboard read successful on attempt {attempt+1} (after {int((0.2 + attempt * 0.1) * 1000)}ms)")
                    break
            except Exception as e:
                print(f"Clipboard read attempt {attempt+1} failed: {e}")
                continue
        
        if not clipboard_content:
            print("⚠ Warning: Clipboard is empty after all retry attempts")
            print("   This can happen in Citrix/RDP if clipboard redirection is disabled")
            print("   or if the Ctrl+C didn't select any text")
        
        # Return JSON with key-value pair (even if empty - let the agent handle it)
        return {
            key_name: clipboard_content
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy to clipboard: {e}")


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
    if button not in ("left", "right", "middle"):
        raise HTTPException(status_code=400, detail="Invalid button: expected 'left', 'right', or 'middle'")
    
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


@app.post("/computer/input/mouse/drag")
async def post_mouse_drag(payload: Dict[str, Any]):
    """Drag the mouse from a required start position to a required end position.

    Required:
      • to_x/to_y (preferred) OR x/y (legacy key names)
      • start_x/start_y OR from_x/from_y
    Optional:
      • duration: float seconds for the move (0 for instant)
      • button: 'left' | 'right' | 'middle' (default 'left')
    """
    # Validate button
    button = str(payload.get("button", "left")).lower()
    if button not in ("left", "right", "middle"):
        raise HTTPException(status_code=400, detail="Invalid button: expected 'left', 'right', or 'middle'")

    # Destination coordinates (absolute only)
    to_x = payload.get("to_x")
    to_y = payload.get("to_y")
    try:
        end_x = int(to_x)
        end_y = int(to_y)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid destination coordinates")

    # Required start coordinates (from_x/from_y or start_x/start_y)
    start_x_val = payload.get("start_x")
    start_y_val = payload.get("start_y")
    if start_x_val is None or start_y_val is None:
        start_x_val = payload.get("from_x")
        start_y_val = payload.get("from_y")
    try:
        start_x = int(start_x_val)
        start_y = int(start_y_val)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid start coordinates (start_x/start_y)")

    # Optional duration
    duration_val = payload.get("duration")
    move_duration: float = 0.0
    if duration_val is not None:
        try:
            move_duration = max(0.0, float(duration_val))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid 'duration' (must be number)")

    # Perform drag using native pyautogui.dragTo with safe fallback
    try:
        # If explicit start provided, move there first
        try:
            pyautogui.moveTo(int(start_x), int(start_y), duration=0)
        except Exception:
            pass
        # Native drag (absolute)
        pyautogui.dragTo(int(end_x), int(end_y), duration=move_duration, button=button)
        return {}
    except Exception as e:
        # Fallback: manual press/move/release
        try:
            if start_x is not None and start_y is not None:
                try:
                    pyautogui.moveTo(int(start_x), int(start_y), duration=0)
                except Exception:
                    pass
            pyautogui.mouseDown(button=button)
            time.sleep(0.02)
            pyautogui.moveTo(int(end_x), int(end_y), duration=move_duration)
        finally:
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                pass
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
    
    def __init__(self, host: str, port: int, secret: str, target_port: int, config: Config, keepalive_manager: Optional["KeepAliveManager"] = None, remote_keepalive_for_main_id: Optional[str] = None):
        self.host = host
        self.port = port
        self.secret = secret
        self.target_port = target_port
        self.config = config
        self.min_sleep = 1
        self.max_sleep = 16
        self.keepalive_manager = keepalive_manager
        self.remote_keepalive_for_main_id = remote_keepalive_for_main_id
        
    async def run(self):
        """Run the tunnel with exponential backoff reconnection."""
        sleep_time = self.min_sleep
        
        while True:
            try:
                await self._connect_and_run()
                # Reset sleep time on successful connection
                sleep_time = self.min_sleep
            except asyncio.CancelledError:
                # Allow task cancellation to stop the tunnel immediately
                raise
            except Exception as e:
                error_msg = str(e).lower()
                print(f"\n{'='*60}")
                print(f"WebSocket Connection Error: {e}")
                print(f"{'='*60}")
                
                # Check for common error types and provide specific guidance
                if "certificate" in error_msg or "ssl" in error_msg or "tls" in error_msg:
                    print("\n⚠️  TLS/SSL Certificate Error Detected")
                    print("\nThis is usually caused by missing or outdated root certificates.")
                    print("\n📖 Fix: Install the required certificates")
                    print("   → See: https://docs.cyberdesk.io/cyberdriver/quickstart#tls-certificate-errors")
                    print("\nOn Windows, run the PowerShell certificate installation script from the docs.")
                    print("On macOS/Linux, ensure your system's CA certificates are up to date.")
                    
                elif "unauthorized" in error_msg or "401" in error_msg or "403" in error_msg:
                    print("\n⚠️  Authentication Error")
                    print("\n❌ Invalid API Key")
                    print("\nPlease check:")
                    print("   1. Your API key is correct (from Cyberdesk dashboard)")
                    print("   2. The API key hasn't been regenerated recently")
                    
                else:
                    print("\n⚠️  Unknown Connection Error")
                    print("\nCommon fixes:")
                    print("   1. Check your API key: --secret YOUR_KEY")
                    print("   2. Install TLS certificates: https://github.com/cyberdesk-hq/cyberdriver#tls-certificate-errors")
                    print("   3. Check your internet connection")
                
                print(f"\n{'='*60}")
                print(f"Retrying in {sleep_time} seconds...")
                print(f"{'='*60}\n")
                
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
        if self.remote_keepalive_for_main_id:
            headers["X-Remote-Keepalive-For"] = self.remote_keepalive_for_main_id
        
        # Use compatibility wrapper for connection
        try:
            websocket = await connect_with_headers(uri, headers)
        except Exception as e:
            # Re-raise with more context about what failed
            error_type = type(e).__name__
            raise ConnectionError(f"{error_type}: {str(e)} (connecting to {uri})") from e
        async with websocket:
            # Print success message with green checkmark
            # Ensure countdown line (if any) is cleared before printing
            try:
                if self.keepalive_manager is not None and hasattr(self.keepalive_manager, "_clear_countdown_line"):
                    self.keepalive_manager._clear_countdown_line()
            except Exception:
                pass
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
            # Optionally re-print countdown immediately after connect
            try:
                if self.keepalive_manager is not None and hasattr(self.keepalive_manager, "_print_countdown"):
                    self.keepalive_manager._print_countdown()
            except Exception:
                pass
            
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
                                # Note activity (work received)
                                if self.keepalive_manager is not None:
                                    self.keepalive_manager.record_activity()
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
                            # Note activity as soon as we receive metadata
                            if self.keepalive_manager is not None:
                                self.keepalive_manager.record_activity()
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
            # If a keepalive action is currently running, wait for it to finish
            if self.keepalive_manager is not None:
                if self.keepalive_manager.is_busy():
                    print("Keepalive: waiting for current action to finish before handling request…")
                await self.keepalive_manager.wait_until_idle()
                # Record that we are actively processing a request
                self.keepalive_manager.record_activity()
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


class BlackScreenRecoveryManager:
    """Background manager that detects black screens and recovers by switching to console session.
    
    Windows RDP/session issues can cause the screen to go completely black. This manager:
    - Periodically captures screenshots
    - Checks if screen is truly black (no variance)
    - Executes PowerShell script to switch session to console
    
    Note: This feature is Windows-only and will not run on other platforms.
    """
    def __init__(self, enabled: bool, check_interval_seconds: float = 30.0):
        # Gate to Windows only
        if platform.system() != "Windows":
            self.enabled = False
            if enabled:
                print("Note: Black screen recovery is only supported on Windows")
        else:
            self.enabled = enabled
        self.check_interval_seconds = max(5.0, float(check_interval_seconds))
        self._stop = False
        self._task: Optional[asyncio.Task] = None
        self._initial_check_done = False
        
    async def run(self):
        """Main loop that checks for black screens on a cadence."""
        if not self.enabled:
            return
            
        try:
            # Initial check after 5 seconds
            await asyncio.sleep(5.0)
            if not self._stop and self.enabled:
                await self._check_and_recover()
                self._initial_check_done = True
            
            # Regular checks on interval
            while not self._stop and self.enabled:
                await asyncio.sleep(self.check_interval_seconds)
                if not self._stop and self.enabled:
                    await self._check_and_recover()
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"BlackScreenRecovery error: {e}")
    
    async def _check_and_recover(self):
        """Check if screen is black and recover if needed.
        
        Uses a 5-second confirmation check to avoid false positives during
        transient black screens (e.g., during RDP connection).
        """
        try:
            # Initial check
            is_black = await asyncio.to_thread(self._check_if_screen_black)
            
            if is_black:
                print("\n⚠️  Black screen detected! Confirming in 5 seconds...")
                
                # Wait 5 seconds and check again to confirm it's not transient
                await asyncio.sleep(5.0)
                
                # Confirmation check
                still_black = await asyncio.to_thread(self._check_if_screen_black)
                
                if still_black:
                    print("⚠️  Black screen confirmed! Attempting console session recovery...")
                    await self._execute_console_switch()
                else:
                    print("✓ Screen recovered on its own (transient black screen)")
            
        except Exception as e:
            print(f"BlackScreenRecovery check error: {e}")
    
    def _check_if_screen_black(self) -> bool:
        """Check if the screen is truly black (no variance)."""
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                img = sct.grab(monitor)
                
                # Convert to numpy array for variance check
                img_array = np.array(img)
                
                # Check if there's any variance in the image
                # A truly black screen will have variance close to 0
                variance = np.var(img_array)
                
                # Also check if mean is very low (close to black)
                mean = np.mean(img_array)
                
                # Consider screen black if variance is very low (< 1.0) and mean is low (< 10)
                is_black = variance < 1.0 and mean < 10
                
                if is_black:
                    print(f"BlackScreenRecovery: Screen appears black (variance={variance:.2f}, mean={mean:.2f})")
                
                return is_black
                
        except Exception as e:
            print(f"BlackScreenRecovery: Screenshot check failed: {e}")
            return False
    
    async def _execute_console_switch(self):
        """Execute PowerShell script to switch session to console."""
        if platform.system() != "Windows":
            print("BlackScreenRecovery: Console switching is only supported on Windows")
            return
        
        try:
            # PowerShell script to switch session to console with elevation
            ps_script = """
# Get the current PowerShell process's session id
$sessionId = (Get-Process -Id $PID).SessionId

# Helper to run tscon
function Invoke-Tscon {
    param($Id)
    Write-Host "Running: tscon $Id /dest:console"
    & tscon $Id /dest:console
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Error "tscon exited with code $rc"
    }
}

# Check if running elevated
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Not elevated — requesting elevation (UAC)."
    $escaped = $sessionId.ToString()
    # Start an elevated powershell that runs tscon with the captured session id
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -WindowStyle Hidden -Command `"& { tscon $escaped /dest:console }`""
    return
}

# Already elevated — run directly
Invoke-Tscon -Id $sessionId
"""
            
            # Execute the PowerShell script
            result = await asyncio.to_thread(
                execute_powershell_command,
                ps_script,
                str(uuid.uuid4()),
                None,
                True,
                30.0
            )
            
            if result.get("exit_code") == 0:
                print("✓ Console session switch executed successfully")
            else:
                error = result.get("stderr", "Unknown error")
                print(f"BlackScreenRecovery: tscon execution failed: {error}")
                
        except Exception as e:
            print(f"BlackScreenRecovery: Failed to execute console switch: {e}")
    
    def stop(self):
        """Stop the recovery manager."""
        self._stop = True


class KeepAliveManager:
    """Background manager that triggers realistic user activity when idle.
    
    - Tracks last activity timestamp (requests from cloud)
    - If enabled and idle beyond threshold, triggers a short keepalive action
    - While keepalive action runs, requests will wait until it completes
    """
    def __init__(self, enabled: bool, threshold_minutes: float = 3.0, check_interval_seconds: float = 30.0, 
                 click_x: Optional[int] = None, click_y: Optional[int] = None):
        self.enabled = enabled
        self.threshold_seconds = max(10.0, float(threshold_minutes) * 60.0)
        self.check_interval_seconds = max(5.0, float(check_interval_seconds))
        self.last_activity_ts = time.time()
        self._idle_event: Optional[asyncio.Event] = None  # created in run()
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        # After a keepalive completes, wait a random 3-5 min before allowing another
        self._next_allowed_ts = self.last_activity_ts + self.threshold_seconds
        # Live countdown printer state
        self._countdown_task: Optional[asyncio.Task] = None
        self._countdown_active: bool = False
        self._last_countdown_len: int = 0
        # Precise scheduler event - wakes the scheduler when activity occurs
        self._schedule_event: Optional[asyncio.Event] = None
        # Customizable click coordinates (None = use default bottom-left)
        self.click_x = click_x
        self.click_y = click_y

    def record_activity(self):
        self.last_activity_ts = time.time()
        # Also push out next allowed time based on fresh activity
        self._next_allowed_ts = self.last_activity_ts + self.threshold_seconds
        # Update countdown immediately
        self._print_countdown()
        # Wake scheduler to recompute deadline
        try:
            if self._schedule_event is not None:
                self._schedule_event.set()
        except Exception:
            pass

    async def wait_until_idle(self):
        if not self.enabled:
            return
        if self._idle_event is None:
            return
        await self._idle_event.wait()

    async def run(self):
        if not self.enabled:
            return
        self._idle_event = asyncio.Event()
        self._idle_event.set()  # initially idle
        # Start live countdown printer
        self._ensure_countdown_printer_started()
        # Initialize scheduler event
        self._schedule_event = asyncio.Event()
        self._schedule_event.clear()
        try:
            while not self._stop:
                # Compute next due time precisely
                now = time.time()
                deadline = max(self.last_activity_ts + self.threshold_seconds, self._next_allowed_ts)
                remaining = max(0.0, deadline - now)

                if remaining > 0.0:
                    # Wait for either deadline or a schedule event (activity or config change)
                    sleep_task = asyncio.create_task(asyncio.sleep(remaining))
                    wait_task = asyncio.create_task(self._schedule_event.wait())
                    done, pending = await asyncio.wait({sleep_task, wait_task}, return_when=asyncio.FIRST_COMPLETED)
                    # Cancel whichever didn't fire
                    for p in pending:
                        p.cancel()
                    # If schedule event fired, clear it and loop to recompute deadline
                    if wait_task in done:
                        try:
                            self._schedule_event.clear()
                        except Exception:
                            pass
                        continue
                    # Else, deadline sleep finished, proceed to action

                # Time reached; only proceed if still eligible
                now = time.time()
                if now >= max(self.last_activity_ts + self.threshold_seconds, self._next_allowed_ts):
                    # Mark busy so requests will wait
                    self._idle_event.clear()
                    # Clear countdown line before printing status
                    self._clear_countdown_line()
                    start_ts = time.time()
                    print("\nKeepalive: starting simulated activity…")
                    try:
                        await asyncio.to_thread(self._perform_keepalive_action)
                    except Exception as e:
                        print(f"Keepalive action error: {e}")
                    finally:
                        # Finished, mark idle and set next random cooldown window
                        self._idle_event.set()
                        duration = time.time() - start_ts
                        # Respect configured threshold for subsequent scheduling with light jitter
                        base = float(self.threshold_seconds)
                        jitter = random.uniform(-min(7.0, base * 0.2), min(7.0, base * 0.2))
                        cooldown = max(0.0, base + jitter)
                        self._next_allowed_ts = time.time() + cooldown
                        print(
                            f"Keepalive: completed in {duration:.1f}s. Next eligible window in "
                            f"{int(cooldown // 60)}m {int(cooldown % 60)}s."
                        )
                        # Resume countdown line
                        self._print_countdown()
                        # Wake scheduler to recompute based on new next_allowed_ts
                        try:
                            self._schedule_event.set()
                        except Exception:
                            pass
        finally:
            if self._idle_event is not None:
                self._idle_event.set()

    def stop(self):
        self._stop = True
        # Stop countdown printer and clear line
        try:
            if self._countdown_task and not self._countdown_task.done():
                self._countdown_task.cancel()
        except Exception:
            pass
        self._clear_countdown_line()

    # --- Internal: perform cross-platform minimal user activity ---
    def _perform_keepalive_action(self):
        system_name = platform.system()
        # Small randomized pauses helper
        def short_pause(a: float = 0.15, b: float = 0.4):
            time.sleep(random.uniform(a, b))
        
        # Expanded, human-like phrases inspired by workspace jiggle script
        phrases = [
            "cookies",
            "checking notes",
            "be right back",
            "just a sec",
            "one moment",
            "thinking",
            "hmm",
            "on it",
            "almost there",
            "nearly done",
            "okay",
            "ok",
            "sure",
            "yep",
            "cool",
            "thanks",
            "working",
            # utility/search-like terms to feel natural in Start/Spotlight
            "system settings",
            "logs",
            "utilities",
            "reports",
            "status",
            "calendar",
            "updates",
            "notepad",
            "calculator",
            "network",
        ]
        # Number of phrases to type in this run
        num_phrases = random.randint(2, 5)
        chosen = random.sample(phrases, k=num_phrases)
        
        try:
            # Determine click coordinates
            screen_width, screen_height = pyautogui.size()
            
            if self.click_x is not None and self.click_y is not None:
                # Use custom coordinates
                click_x = self.click_x
                click_y = self.click_y
            else:
                # Default: click near bottom-left to focus shell/taskbar
                click_x = random.randint(1, 3)
                click_y = screen_height - random.randint(1, 3)
            
            try:
                pyautogui.moveTo(click_x, click_y, duration=0)
                short_pause(0.05, 0.12)
                pyautogui.click(button="left")
            except Exception:
                pass

            short_pause(0.08, 0.18)
            for p in chosen:
                pyautogui.typewrite(p, interval=random.uniform(0.02, 0.06))
                short_pause(0.06, 0.15)
            pyautogui.press("esc")
        except Exception as e:
            # Never crash due to keepalive
            print(f"Keepalive action failed: {e}")

    # --- Helpers for logging and coordination ---
    def compute_seconds_until_possible_action(self, now: Optional[float] = None) -> float:
        now_ts = time.time() if now is None else now
        earliest = max(self.last_activity_ts + self.threshold_seconds, self._next_allowed_ts)
        return max(0.0, earliest - now_ts)

    def is_busy(self) -> bool:
        return bool(self._idle_event is not None and not self._idle_event.is_set())

    def _ensure_countdown_printer_started(self):
        if self._countdown_task is None or self._countdown_task.done():
            self._countdown_task = asyncio.create_task(self._countdown_printer())

    async def _countdown_printer(self):
        try:
            while not self._stop:
                if not self.enabled:
                    await asyncio.sleep(1.0)
                    continue
                # Only show countdown when idle (not currently running an action)
                if self.is_busy():
                    # Ensure countdown line is cleared while busy
                    self._clear_countdown_line()
                    await asyncio.sleep(0.5)
                    continue
                remaining = self.compute_seconds_until_possible_action()
                if remaining > 0.0:
                    mins = int(remaining // 60)
                    secs = int(remaining % 60)
                    print(self._format_countdown_line(mins, secs), end="", flush=True)
                    self._countdown_active = True
                else:
                    # Clear once we reach 0
                    self._clear_countdown_line()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Don't crash keepalive due to printer
            try:
                print(f"Keepalive countdown error: {e}")
            except Exception:
                pass

    def _format_countdown_line(self, mins: int, secs: int) -> str:
        text = f"\rKeepalive: next action in {mins}m {secs:02d}s"
        # Pad with spaces to erase previous longer content if any
        pad_len = max(0, self._last_countdown_len - len(text))
        self._last_countdown_len = len(text)
        return text + (" " * pad_len)

    def _clear_countdown_line(self):
        if self._countdown_active:
            # Overwrite the line with spaces and return cursor
            clear = "\r" + (" " * max(self._last_countdown_len, 0)) + "\r"
            print(clear, end="", flush=True)
            self._countdown_active = False
            self._last_countdown_len = 0

    def _print_countdown(self):
        if not self.enabled or self.is_busy():
            return
        remaining = self.compute_seconds_until_possible_action()
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        print(self._format_countdown_line(mins, secs), end="", flush=True)
        self._countdown_active = True


async def run_join(host: str, port: int, secret: str, target_port: int, keepalive_enabled: bool = False, 
                   keepalive_threshold_minutes: float = 3.0, interactive: bool = False, 
                   register_as_keepalive_for: Optional[str] = None,
                   keepalive_click_x: Optional[int] = None, keepalive_click_y: Optional[int] = None,
                   black_screen_recovery_enabled: bool = False,
                   black_screen_check_interval: float = 30.0):
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
    
    # Prepare keepalive manager (optional)
    keepalive_manager = KeepAliveManager(
        enabled=keepalive_enabled,
        threshold_minutes=keepalive_threshold_minutes,
        check_interval_seconds=30.0,
        click_x=keepalive_click_x,
        click_y=keepalive_click_y,
    )
    # Expose manager to API routes
    try:
        app.state.keepalive_manager = keepalive_manager
    except Exception:
        pass
    keepalive_task: Optional[asyncio.Task] = None
    tunnel_task: Optional[asyncio.Task] = None
    
    # Prepare black screen recovery manager (optional)
    black_screen_manager = BlackScreenRecoveryManager(
        enabled=black_screen_recovery_enabled,
        check_interval_seconds=black_screen_check_interval,
    )
    black_screen_task: Optional[asyncio.Task] = None

    async def start_keepalive_if_enabled():
        nonlocal keepalive_task
        if keepalive_enabled and keepalive_task is None:
            keepalive_manager.enabled = True
            keepalive_task = asyncio.create_task(keepalive_manager.run())

    async def stop_keepalive():
        nonlocal keepalive_task
        if keepalive_task is not None:
            # Disable and allow the task to finish its loop
            keepalive_manager.enabled = False
            try:
                await asyncio.wait_for(keepalive_task, timeout=2.0)
            except Exception:
                pass
            keepalive_task = None
    
    async def start_black_screen_recovery_if_enabled():
        nonlocal black_screen_task
        if black_screen_recovery_enabled and black_screen_task is None:
            black_screen_manager.enabled = True
            black_screen_task = asyncio.create_task(black_screen_manager.run())
            if black_screen_recovery_enabled:
                print(f"✓ Black screen recovery enabled (check interval: {black_screen_check_interval}s)")
    
    async def stop_black_screen_recovery():
        nonlocal black_screen_task
        if black_screen_task is not None:
            black_screen_manager.stop()
            try:
                await asyncio.wait_for(black_screen_task, timeout=2.0)
            except Exception:
                pass
            black_screen_task = None

    def make_tunnel():
        return TunnelClient(
            host, port, secret, actual_target_port, config,
            keepalive_manager=keepalive_manager if keepalive_enabled else None,
            remote_keepalive_for_main_id=register_as_keepalive_for
        )

    async def start_tunnel():
        nonlocal tunnel_task
        if tunnel_task is None:
            tunnel = make_tunnel()
            tunnel_task = asyncio.create_task(tunnel.run())

    async def stop_tunnel():
        nonlocal tunnel_task
        if tunnel_task is not None:
            tunnel_task.cancel()
            try:
                await asyncio.wait_for(tunnel_task, timeout=2.0)
            except Exception:
                pass
            tunnel_task = None

    # Start initially enabled
    await start_tunnel()
    await start_keepalive_if_enabled()
    await start_black_screen_recovery_if_enabled()

    if not interactive:
        # Run server and tunnel until interrupted
        try:
            await asyncio.gather(server_task, tunnel_task)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            await stop_keepalive()
            await stop_black_screen_recovery()
    else:
        # Interactive CLI loop to Disable/Re-enable without killing process
        print("\nInteractive controls: [d] Disable, [e] Re-enable, [q] Quit, [h] Help")
        async def interactive_cli():
            while True:
                try:
                    sys.stdout.write("cyberdriver> ")
                    sys.stdout.flush()
                    line = await asyncio.to_thread(sys.stdin.readline)
                    if not line:
                        await asyncio.sleep(0.1)
                        continue
                    cmd = line.strip().lower()
                    if cmd in ("h", "help", "?"):
                        print("Commands: d=Disable, e=Enable, q=Quit")
                    elif cmd in ("d", "disable"):
                        print("Disabling Cyberdriver tunnel…")
                        await stop_tunnel()
                        await stop_keepalive()
                        await stop_black_screen_recovery()
                        print("Disabled. The local server is still running.")
                    elif cmd in ("e", "enable", "reenable", "re-enable"):
                        print("Enabling Cyberdriver tunnel…")
                        await start_tunnel()
                        await start_keepalive_if_enabled()
                        await start_black_screen_recovery_if_enabled()
                        print("Enabled and connected (reconnection may take a moment).")
                    elif cmd in ("q", "quit", "exit"):
                        print("Exiting…")
                        break
                    else:
                        print("Unknown command. Type 'h' for help.")
                except KeyboardInterrupt:
                    print("\nExiting…")
                    break

        try:
            await asyncio.gather(server_task, interactive_cli())
        finally:
            await stop_tunnel()
            await stop_keepalive()
            await stop_black_screen_recovery()


def run_coords_capture():
    """Run interactive coordinate capture utility."""
    try:
        from pynput import mouse, keyboard
        from pynput.keyboard import Key
    except ImportError:
        print("Error: pynput library is required for coords capture.")
        print("Install it with: pip install pynput")
        sys.exit(1)
    
    print("Right-click anywhere to capture coordinates. Press Esc to exit.\n")
    
    # Track running state
    running = [True]  # Use list so nested functions can modify it
    
    def on_press(key):
        # Exit on Escape key
        if key == Key.esc:
            running[0] = False
            return False  # Stop listener
    
    def on_release(key):
        pass
    
    def on_click(x, y, button, pressed):
        if not running[0]:
            return False  # Stop listener
            
        # Only capture right-click (button.right)
        if pressed and button == mouse.Button.right:
            # Print captured coordinates with colors
            if platform.system() == "Windows":
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                    green = '\033[92m'
                    white = '\033[97m'
                    blue = '\033[94m'
                    reset = '\033[0m'
                    print(f"\n{green}✓{reset} {white}Click captured:{reset} X={blue}{x}{reset}, Y={blue}{y}{reset}\n")
                except:
                    print(f"\n✓ Click captured: X={x}, Y={y}\n")
            else:
                green = '\033[92m'
                white = '\033[97m'
                blue = '\033[94m'
                reset = '\033[0m'
                print(f"\n{green}✓{reset} {white}Click captured:{reset} X={blue}{x}{reset}, Y={blue}{y}{reset}\n")
            
            # Print usage example
            print(f"Use with keepalive:")
            print(f"  cyberdriver join --secret YOUR_KEY --keepalive \\")
            print(f"    --keepalive-click-x {x} --keepalive-click-y {y}\n")
        
        return True  # Continue listening
    
    try:
        # Start global keyboard and mouse listeners
        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        mouse_listener = mouse.Listener(on_click=on_click)
        
        keyboard_listener.start()
        mouse_listener.start()
        
        # Keep running until Escape is pressed or KeyboardInterrupt
        while running[0]:
            time.sleep(0.1)
        
        # Clean up
        keyboard_listener.stop()
        mouse_listener.stop()
        print("\nCoordinate capture stopped.")
        
    except KeyboardInterrupt:
        print("\n\nCoordinate capture stopped.")
        try:
            keyboard_listener.stop()
            mouse_listener.stop()
        except:
            pass


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
        print("→ Join: cyberdriver join --secret YOUR_API_KEY")
        print("→ Keepalive: cyberdriver join --secret YOUR_API_KEY --keepalive")
        print("→ Black screen recovery: cyberdriver join --secret YOUR_API_KEY --black-screen-recovery")
        print("→ Persistent display: cyberdriver join --secret YOUR_API_KEY --add-persistent-display")
    
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
        print(f"{white}→ {blue}Join:{reset} cyberdriver join --secret YOUR_API_KEY")
        print(f"{white}→ {blue}Keepalive:{reset} cyberdriver join --secret YOUR_API_KEY --keepalive")
        print(f"{white}→ {blue}Black screen recovery:{reset} cyberdriver join --secret YOUR_API_KEY --black-screen-recovery")
        print(f"{white}→ {blue}Persistent display:{reset} cyberdriver join --secret YOUR_API_KEY --add-persistent-display")
        print(f"{white}→ {blue}Remote keepalive:{reset} cyberdriver join --secret YOUR_API_KEY --keepalive --register-as-keepalive-for MAIN_MACHINE_ID")
    
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
    
    # coords command
    coords_parser = subparsers.add_parser(
        "coords",
        help="Capture screen coordinates by clicking",
        description="Interactive utility to capture X,Y coordinates for keepalive configuration"
    )
    join_parser.add_argument("--secret", required=True, help="Your API key from Cyberdesk")
    join_parser.add_argument("--host", default="api.cyberdesk.io", help="Control server (default: api.cyberdesk.io)")
    join_parser.add_argument("--port", type=int, default=443, help="Control server port (default: 443)")
    join_parser.add_argument("--target-port", type=int, default=3000, help="Local port (default: 3000)")
    join_parser.add_argument("--keepalive", action="store_true", help="Enable keepalive actions when idle")
    join_parser.add_argument("--keepalive-threshold-minutes", type=float, default=3.0, help="Idle minutes before keepalive (default: 3)")
    join_parser.add_argument("--keepalive-click-x", type=int, default=None, help="X coordinate for keepalive click (default: bottom-left)")
    join_parser.add_argument("--keepalive-click-y", type=int, default=None, help="Y coordinate for keepalive click (default: bottom-left)")
    join_parser.add_argument("--black-screen-recovery", action="store_true", help="Enable black screen detection and console recovery (Windows only)")
    join_parser.add_argument("--black-screen-check-interval", type=float, default=30.0, help="Seconds between black screen checks (default: 30)")
    join_parser.add_argument("--add-persistent-display", action="store_true", help="Install and enable Amyuni virtual display driver for persistent display (Windows only, requires admin)")
    join_parser.add_argument("--interactive", action="store_true", help="Interactive CLI to Disable/Re-enable without exiting")
    join_parser.add_argument("--register-as-keepalive-for", type=str, default=None, help="Register this instance as the remote keepalive (host) for MAIN_MACHINE_ID")
    
    # Parse arguments with error handling
    try:
        args = parser.parse_args()
    except SystemExit as e:
        # argparse calls sys.exit() on error, catch it to provide better context
        if e.code != 0:
            print("\nError: Invalid arguments. Use -h for help.")
        sys.exit(e.code)

    # Handle help or no command
    if not args.command or args.help:
        if not (len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ['-h', '--help'])):
            print_banner()
        print("Commands:")
        print("  join --secret KEY                         Connect to Cyberdesk Cloud")
        print("  join --secret KEY --keepalive             Enable keepalive")
        print("  join --secret KEY --black-screen-recovery Enable black screen detection (Windows)")
        print("  join --secret KEY --add-persistent-display Install virtual display driver (Windows)")
        print("  coords                                    Capture screen coordinates (for keepalive)")
        print()
        print("For more info: cyberdriver join -h")
        sys.exit(0)
    
    # Show banner for join command
    if args.command == "join":
        print_banner(mode="connecting")
    
    # Check for admin privileges if black screen recovery or persistent display is enabled
    needs_admin = False
    if args.command == "join":
        if getattr(args, "black_screen_recovery", False):
            needs_admin = True
        if getattr(args, "add_persistent_display", False):
            needs_admin = True
    
    if needs_admin and platform.system() == "Windows":
        if not is_running_as_admin():
            # Request elevation and exit (will restart with admin privileges)
            request_admin_elevation()
            # If we're still here, elevation failed or was cancelled
            print("\nWarning: Running without administrator privileges.")
            
            if getattr(args, "black_screen_recovery", False):
                print("Black screen recovery may not work properly without elevation.")
            if getattr(args, "add_persistent_display", False):
                print("Persistent display setup requires administrator privileges.")
            
            print("You can:")
            print("  1. Restart Cyberdriver from an Administrator PowerShell")
            if getattr(args, "black_screen_recovery", False):
                print("  2. Accept the UAC prompt when black screen is detected")
            print("  3. Continue anyway (press Enter)")
            input()
        else:
            # Running as admin - show confirmation
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                green = '\033[92m'
                reset = '\033[0m'
                print(f"{green}✓{reset} Running with administrator privileges\n")
            except:
                print("✓ Running with administrator privileges\n")
    
    # Setup persistent display if requested
    if args.command == "join" and getattr(args, "add_persistent_display", False):
        if platform.system() == "Windows":
            if not setup_persistent_display_if_needed():
                print("\nWarning: Failed to setup persistent display")
                print("Cyberdriver will continue, but the virtual display may not be available")
                print("Press Enter to continue...")
                input()
            else:
                print()  # Add spacing after successful setup
    
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
        
        elif args.command == "coords":
            run_coords_capture()

        elif args.command == "join":
            asyncio.run(run_join(
                args.host,
                args.port,
                args.secret,
                args.target_port,
                keepalive_enabled=bool(getattr(args, "keepalive", False)),
                keepalive_threshold_minutes=float(getattr(args, "keepalive_threshold_minutes", 3.0)),
                interactive=bool(getattr(args, "interactive", False)),
                register_as_keepalive_for=getattr(args, "register_as_keepalive_for", None),
                keepalive_click_x=getattr(args, "keepalive_click_x", None),
                keepalive_click_y=getattr(args, "keepalive_click_y", None),
                black_screen_recovery_enabled=bool(getattr(args, "black_screen_recovery", False)),
                black_screen_check_interval=float(getattr(args, "black_screen_check_interval", 30.0)),
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
