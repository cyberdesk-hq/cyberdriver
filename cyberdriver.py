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
import re
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
import atexit
from typing import Dict, List, Optional, Tuple, Union, Any
from enum import Enum
from dataclasses import dataclass
from io import BytesIO
from contextlib import asynccontextmanager

import certifi
import httpx
import mss
import numpy as np
import pyautogui
import pyperclip
from PIL import Image
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import Response, JSONResponse
import uvicorn
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus
from datetime import datetime

# -----------------------------------------------------------------------------
# Debug Logging System
# -----------------------------------------------------------------------------

class DebugLogger:
    """Debug logger that writes semantic logs to daily files."""
    
    _instance: Optional["DebugLogger"] = None
    
    def __init__(self, enabled: bool = False, log_dir: Optional[str] = None):
        self.enabled = enabled
        self.log_dir = pathlib.Path(log_dir) if log_dir else pathlib.Path.home() / ".cyberdriver" / "logs"
        self._current_date: Optional[str] = None
        self._log_file: Optional[pathlib.Path] = None
        self._connection_count = 0
        self._start_time = time.time()
        
        if self.enabled:
            self._ensure_log_dir()
            self._write_session_start()
    
    @classmethod
    def get_instance(cls) -> "DebugLogger":
        """Get the global debug logger instance."""
        if cls._instance is None:
            cls._instance = cls(enabled=False)
        return cls._instance
    
    @classmethod
    def initialize(cls, enabled: bool = False, log_dir: Optional[str] = None) -> "DebugLogger":
        """Initialize the global debug logger. """
        cls._instance = cls(enabled=enabled, log_dir=log_dir)
        return cls._instance
    
    def _ensure_log_dir(self):
        """Ensure log directory exists."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_log_file(self) -> pathlib.Path:
        """Get the current log file path, rotating by day."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._current_date != today:
            self._current_date = today
            self._log_file = self.log_dir / f"cyberdriver-{today}.log"
        return self._log_file
    
    def _format_timestamp(self) -> str:
        """Get formatted timestamp for log entries."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    def _write(self, level: str, category: str, message: str, **context):
        """Write a log entry."""
        if not self.enabled:
            return
        
        log_file = self._get_log_file()
        timestamp = self._format_timestamp()
        
        # Format context as key=value pairs
        context_str = ""
        if context:
            context_parts = [f"{k}={v}" for k, v in context.items()]
            context_str = f" | {' | '.join(context_parts)}"
        
        log_line = f"[{timestamp}] [{level}] [{category}] {message}{context_str}\n"
        
        # Also print to console in debug mode
        print(f"[DEBUG] [{category}] {message}", flush=True)
        
        # Write to file
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            print(f"[DEBUG] Failed to write log: {e}")
    
    def _write_session_start(self):
        """Write session start marker."""
        self._write("INFO", "SESSION", "=" * 60)
        self._write("INFO", "SESSION", "Cyberdriver debug session started", 
                    version=VERSION if 'VERSION' in globals() else "unknown",
                    platform=platform.system(),
                    python=sys.version.split()[0])
        self._write("INFO", "SESSION", "=" * 60)
    
    # High-level semantic logging methods
    def connection_attempt(self, uri: str, attempt_num: int):
        """Log a connection attempt."""
        self._connection_count += 1
        self._write("INFO", "CONNECTION", f"Attempting connection #{self._connection_count}",
                    uri=uri, attempt=attempt_num)
    
    def connection_established(self, uri: str):
        """Log successful connection."""
        self._write("INFO", "CONNECTION", "WebSocket connection established", uri=uri)
    
    def connection_failed(self, error: str, duration_seconds: float, error_type: str = "unknown"):
        """Log connection failure."""
        self._write("ERROR", "CONNECTION", f"Connection failed after {duration_seconds:.2f}s",
                    error=error, error_type=error_type, duration=f"{duration_seconds:.2f}s")
    
    def connection_closed(self, reason: str, duration_seconds: float, close_code: Optional[int] = None):
        """Log connection closed."""
        self._write("INFO", "CONNECTION", f"Connection closed after {duration_seconds:.2f}s",
                    reason=reason, duration=f"{duration_seconds:.2f}s", close_code=close_code)
    
    def message_loop_entered(self):
        """Log entering the message loop."""
        self._write("DEBUG", "MESSAGE_LOOP", "Entered message receive loop, waiting for requests")
    
    def message_received(self, msg_type: str, size: int = 0):
        """Log message received."""
        self._write("DEBUG", "MESSAGE_LOOP", f"Received {msg_type} message", size=size)
    
    def request_forwarded(self, method: str, path: str, status: int, duration_ms: float):
        """Log HTTP request forwarded to local server."""
        self._write("INFO", "REQUEST", f"{method} {path} -> {status}",
                    method=method, path=path, status=status, duration_ms=f"{duration_ms:.1f}ms")
    
    def ping_sent(self):
        """Log ping sent."""
        self._write("DEBUG", "PING", "WebSocket ping sent")
    
    def pong_received(self, latency_ms: float):
        """Log pong received."""
        self._write("DEBUG", "PING", f"WebSocket pong received", latency_ms=f"{latency_ms:.1f}ms")
    
    def keepalive_action(self, action: str):
        """Log keepalive action."""
        self._write("INFO", "KEEPALIVE", f"Keepalive action: {action}")
    
    def error(self, category: str, message: str, **context):
        """Log an error."""
        self._write("ERROR", category, message, **context)
    
    def warning(self, category: str, message: str, **context):
        """Log a warning."""
        self._write("WARN", category, message, **context)
    
    def info(self, category: str, message: str, **context):
        """Log info."""
        self._write("INFO", category, message, **context)
    
    def debug(self, category: str, message: str, **context):
        """Log debug info."""
        self._write("DEBUG", category, message, **context)
    
    def resource_stats(self):
        """Log current resource statistics."""
        uptime = time.time() - self._start_time
        stats = {
            "uptime_seconds": f"{uptime:.1f}",
            "asyncio_tasks": len(asyncio.all_tasks()) if asyncio.get_event_loop().is_running() else "N/A",
            "thread_count": threading.active_count(),
            "connection_attempts": self._connection_count,
        }
        
        # Try to get more stats with psutil
        try:
            import psutil
            process = psutil.Process()
            stats["memory_mb"] = f"{process.memory_info().rss / (1024 * 1024):.1f}"
            stats["open_files"] = len(process.open_files())
            stats["connections"] = len(process.net_connections())
        except ImportError:
            pass
        except Exception:
            pass
        
        # Add GDI handle count on Windows (critical for detecting leaks)
        if platform.system() == "Windows":
            try:
                import ctypes
                GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
                GetGuiResources = ctypes.windll.user32.GetGuiResources
                handle = GetCurrentProcess()
                stats["gdi_objects"] = GetGuiResources(handle, 0)  # GR_GDIOBJECTS
                stats["user_objects"] = GetGuiResources(handle, 1)  # GR_USEROBJECTS
            except Exception:
                pass
        
        self._write("INFO", "RESOURCES", "Resource statistics", **stats)


# Global debug logger - initialized in main()
debug_logger = DebugLogger.get_instance()


# -----------------------------------------------------------------------------
# Output Truncation for Terminal Commands
# -----------------------------------------------------------------------------

# Maximum length for terminal command output to prevent token limit issues
MAX_OUTPUT_LEN: int = 15000

def maybe_truncate_output(content: str, max_length: int = MAX_OUTPUT_LEN) -> str:
    """Truncate content in the middle if it exceeds the specified length. 
    
    Shows the beginning and end of the output with a clear truncation marker in the middle.
    This allows seeing both the start (usually important context) and end (usually the result).
    """
    if not content or len(content) <= max_length:
        return content
    
    # Calculate how much to keep from start and end
    truncation_message = "\n\n... [OUTPUT TRUNCATED - {} characters hidden] ...\n\n"
    
    # Reserve space for the truncation message (estimate ~60 chars)
    available_space = max_length - 60
    chars_per_side = available_space // 2
    
    # Get the start and end portions
    start = content[:chars_per_side]
    end = content[-chars_per_side:]
    hidden_chars = len(content) - (chars_per_side * 2)
    
    return start + truncation_message.format(hidden_chars) + end

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
# Windows Console Fix and Protection
# -----------------------------------------------------------------------------

def disable_windows_console_close_button():
    """Disable the close button (X) on Windows console to prevent accidental termination.
    
    This prevents the agent from accidentally closing the cyberdriver window,
    which would disconnect the machine. The console can still be closed via Ctrl+C.
    Minimize and maximize buttons remain functional.
    
    Returns:
        True if successful, False otherwise
    """
    if platform.system() != "Windows":
        return False
    
    try:
        import ctypes
        
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        
        # Get console window handle
        console_window = kernel32.GetConsoleWindow()
        
        if console_window:
            # Get system menu
            hmenu = user32.GetSystemMenu(console_window, False)
            
            if hmenu:
                # Disable close button (SC_CLOSE = 0xF060) without removing system menu
                # This keeps minimize/maximize buttons functional
                SC_CLOSE = 0xF060
                MF_GRAYED = 0x00000001
                MF_BYCOMMAND = 0x00000000
                user32.EnableMenuItem(hmenu, SC_CLOSE, MF_GRAYED | MF_BYCOMMAND)
                
                print("✓ Console close button disabled (use Ctrl+C to exit)")
                return True
        
        return False
            
    except Exception as e:
        print(f"Note: Could not disable close button: {e}")
        return False


def restore_windows_console_close_button():
    """Restore the close button (X) on Windows console.
    
    Called during shutdown to restore normal console behavior.
    """
    if platform.system() != "Windows":
        return False
    
    try:
        import ctypes
        
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        
        # Get console window handle
        console_window = kernel32.GetConsoleWindow()
        
        if console_window:
            # Get system menu
            hmenu = user32.GetSystemMenu(console_window, False)
            
            if hmenu:
                # Re-enable close button (SC_CLOSE = 0xF060)
                SC_CLOSE = 0xF060
                MF_ENABLED = 0x00000000
                MF_BYCOMMAND = 0x00000000
                user32.EnableMenuItem(hmenu, SC_CLOSE, MF_ENABLED | MF_BYCOMMAND)
                return True
        
        return False
            
    except Exception:
        # Silently fail - this is cleanup, not critical
        return False


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


def _setup_detached_stdio_if_configured():
    """If --_stdio-log or CYBERDRIVER_STDIO_LOG is set, redirect stdout/stderr to that file.

    This is primarily used for Windows "invisible/background" mode, where there is
    no console window to show output.
    """
    # Check command line first (VBScript launcher can't pass env vars to children)
    log_path = None
    for arg in sys.argv:
        if arg.startswith("--_stdio-log="):
            log_path = arg.split("=", 1)[1]
            break
    
    # Fall back to env var
    if not log_path:
        log_path = os.environ.get("CYBERDRIVER_STDIO_LOG")
    
    if not log_path:
        return

    # Ensure downstream code knows logging is redirected to a file and should avoid
    # ANSI color sequences. We set these in-process because the VBScript launcher
    # does not reliably propagate env vars from the parent process.
    try:
        os.environ["CYBERDRIVER_STDIO_LOG"] = str(log_path)
        os.environ["CYBERDRIVER_NO_COLOR"] = "1"
    except Exception:
        pass

    # Hard cap for stdio log size to avoid filling disk on long-lived VMs.
    # Requirement: keep this file at or under 10MB total.
    STDIO_LOG_MAX_BYTES = 10 * 1024 * 1024

    class _SizeCappedTextWriter:
        """A minimal file-like wrapper that caps a log file's size.

        When a write would push the file over the limit, the file is truncated and
        a short header line is written, then logging continues. This keeps total
        disk usage bounded to <= STDIO_LOG_MAX_BYTES.
        """

        def __init__(self, path_str: str, max_bytes: int):
            self._path = pathlib.Path(path_str)
            self._max_bytes = int(max_bytes)
            self._encoding = "utf-8"
            self._lock = threading.Lock()
            self._file = None
            self._size_bytes = 0
            self._open_append()

        @property
        def encoding(self):  # type: ignore[override]
            return self._encoding

        def isatty(self) -> bool:  # type: ignore[override]
            return False

        def writable(self) -> bool:  # type: ignore[override]
            return True

        def _open_append(self) -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Ensure file exists; open in append mode.
            self._file = open(self._path, "a", encoding=self._encoding, buffering=1)
            try:
                self._size_bytes = int(self._path.stat().st_size)
            except Exception:
                self._size_bytes = 0

        def _truncate_and_reopen(self) -> None:
            # Close current handle (best-effort) before truncating.
            try:
                if self._file is not None:
                    self._file.flush()
                    self._file.close()
            except Exception:
                pass

            # Truncate in place and write a small marker.
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "w", encoding=self._encoding) as f:
                    f.write(f"[{datetime.now().isoformat()}] Log truncated (max 10MB)\n")
                    f.flush()
            except Exception:
                # If truncation fails, fall back to append to whatever exists.
                pass

            self._open_append()

        def write(self, s):  # type: ignore[override]
            if s is None:
                return 0
            text = str(s)
            # Estimate bytes written for UTF-8. This is fast enough for our logs.
            encoded: Optional[bytes] = None
            try:
                encoded = text.encode(self._encoding, errors="replace")
                bytes_len = len(encoded)
            except Exception:
                bytes_len = len(text)

            with self._lock:
                if self._file is None:
                    self._open_append()
                # Enforce cap *before* writing so the file never exceeds max size.
                if (self._size_bytes + bytes_len) > self._max_bytes:
                    self._truncate_and_reopen()

                # If the next write is still too large (e.g. a massive message), trim it
                # so we never exceed the cap.
                try:
                    remaining = max(0, int(self._max_bytes - self._size_bytes))
                except Exception:
                    remaining = 0
                if remaining <= 0:
                    self._truncate_and_reopen()
                    try:
                        remaining = max(0, int(self._max_bytes - self._size_bytes))
                    except Exception:
                        remaining = 0

                if remaining and bytes_len > remaining:
                    # Best-effort: keep the tail of the message so the most recent context
                    # is preserved, and prepend a small marker.
                    try:
                        if encoded is None:
                            encoded = text.encode(self._encoding, errors="replace")
                        marker_str = f"\n[{datetime.now().isoformat()}] Log chunk truncated to fit 10MB cap\n"
                        marker_b = marker_str.encode(self._encoding, errors="replace")
                        if len(marker_b) >= remaining:
                            tail_b = encoded[-remaining:]
                            text = tail_b.decode(self._encoding, errors="ignore")
                        else:
                            tail_b = encoded[-(remaining - len(marker_b)):]
                            text = marker_str + tail_b.decode(self._encoding, errors="ignore")
                        encoded = text.encode(self._encoding, errors="replace")
                        bytes_len = len(encoded)
                        # Absolute final guard: if we still don't fit, drop to last `remaining` bytes.
                        if bytes_len > remaining:
                            tail_b2 = encoded[-remaining:]
                            text = tail_b2.decode(self._encoding, errors="ignore")
                            encoded = text.encode(self._encoding, errors="replace")
                            bytes_len = len(encoded)
                    except Exception:
                        # Fall back to writing whatever fits by truncating characters.
                        text = text[-min(len(text), 4096):]
                        try:
                            bytes_len = len(text.encode(self._encoding, errors="replace"))
                        except Exception:
                            bytes_len = len(text)

                try:
                    n_chars = self._file.write(text)  # type: ignore[union-attr]
                    try:
                        self._file.flush()  # type: ignore[union-attr]
                    except Exception:
                        pass
                    # Update size using our bytes estimate
                    self._size_bytes = min(self._max_bytes, self._size_bytes + bytes_len)
                    return n_chars
                except Exception:
                    return 0

        def flush(self):  # type: ignore[override]
            with self._lock:
                try:
                    if self._file is not None:
                        self._file.flush()
                except Exception:
                    pass

        def close(self):  # type: ignore[override]
            with self._lock:
                try:
                    if self._file is not None:
                        self._file.flush()
                        self._file.close()
                except Exception:
                    pass
                self._file = None

    try:
        writer = _SizeCappedTextWriter(str(log_path), STDIO_LOG_MAX_BYTES)
        atexit.register(lambda: writer.close())
        sys.stdout = writer  # type: ignore[assignment]
        sys.stderr = writer  # type: ignore[assignment]
        print(f"\n[{datetime.now().isoformat()}] Cyberdriver detached logging started")
        sys.stdout.flush()
    except Exception:
        # If we can't redirect logs, just continue silently.
        return


def _build_relaunch_command(child_argv: List[str]) -> List[str]:
    """Build a relaunch command that works for both source + PyInstaller."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + child_argv
    return [sys.executable, os.path.abspath(__file__)] + child_argv


def _windows_relaunch_detached(child_argv: List[str], stdio_log_path: pathlib.Path) -> None:
    """Relaunch Cyberdriver as a detached/background process on Windows.

    This ensures Cyberdriver keeps running even if the launching terminal window
    (PowerShell/cmd/etc.) is closed (e.g., by an automation agent hitting Alt+F4).
    
    We use a VBScript wrapper because it's the most reliable way to launch a console
    application truly hidden on Windows. Direct subprocess flags (DETACHED_PROCESS,
    CREATE_NO_WINDOW) have compatibility issues with PyInstaller frozen executables.
    """
    if platform.system() != "Windows":
        raise RuntimeError("_windows_relaunch_detached called on non-Windows")

    cmd = _build_relaunch_command(child_argv)

    # PyInstaller onefile spawns a "child" process that can inherit special env vars
    # from the parent process. If those env vars leak into our hidden launcher (wscript)
    # and then into the detached Cyberdriver process, the child may incorrectly reuse
    # the parent's extraction directory. When the parent exits, PyInstaller attempts to
    # delete that directory and you'll see:
    #   [PYI-xxxx:WARNING] Failed to remove temporary directory: ...\_MEI...
    # and the detached process may crash with FileNotFoundError for base_library.zip.
    #
    # Historically this was `_MEIPASS2`; in newer PyInstaller versions it is
    # `_PYI_APPLICATION_HOME_DIR` (and there are other `_PYI_*` vars).
    #
    # CRITICAL FIX (PyInstaller 6.9+): Setting PYINSTALLER_RESET_ENVIRONMENT=1 tells
    # the bootloader to ignore inherited _PYI_* variables and create a fresh _MEI
    # extraction directory. This prevents the parent's cleanup from deleting files
    # that the long-lived child process needs.
    # See: https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html
    #
    # Make sure wscript starts with a clean environment so the detached process
    # creates its *own* extraction directory.
    wscript_env = os.environ.copy()
    for k in list(wscript_env.keys()):
        if k == "_MEIPASS2" or k.startswith("_PYI_"):
            wscript_env.pop(k, None)
    
    # Force child to create its own extraction directory (PyInstaller 6.9+)
    wscript_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    # PyInstaller also adjusts the process DLL search path in onefile mode, which can
    # leak into child processes on Windows. Reset it before launching `wscript`.
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception:
        pass
    
    # Build the command string for VBScript.
    # VBScript string escaping: double-quotes inside a string become ""
    # The WshShell.Run method takes a string, so we need:
    #   WshShell.Run "command line here", 0, False
    # If the command line itself needs quotes (for paths with spaces), we double them.
    
    # Build the command line using Windows' canonical quoting rules.
    # This avoids subtle bugs around backslashes/quotes when embedding the command
    # inside a VBScript string literal.
    raw_cmd_line = subprocess.list2cmdline(cmd)

    # Escape for VBScript string literals: double all quotes.
    vbs_cmd_line = raw_cmd_line.replace('"', '""')
    
    # CRITICAL FIX (PyInstaller 6.9+): We need to set PYINSTALLER_RESET_ENVIRONMENT=1
    # so the child process creates its own _MEI extraction directory instead of reusing
    # the parent's. When the parent exits, it would otherwise delete files the child needs.
    #
    # We use PowerShell's Start-Process with explicit environment variable setup.
    # This is more reliable than VBScript/cmd.exe for environment variable propagation.
    
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Build PowerShell script that sets env vars and launches cyberdriver hidden
    # We use Start-Process -WindowStyle Hidden for a truly hidden window
    ps_script_path = config_dir / "launch-hidden.ps1"
    
    # Escape the exe path and args for PowerShell
    exe_path = cmd[0] if cmd else sys.executable
    exe_args = cmd[1:] if len(cmd) > 1 else []
    
    # Build argument string for Start-Process
    args_for_ps = subprocess.list2cmdline(exe_args)
    
    ps_content = f'''# Use .NET ProcessStartInfo for explicit control over environment variables
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "{exe_path}"
$psi.Arguments = '{args_for_ps}'
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

# Clear PyInstaller environment variables from the child's environment
$psi.EnvironmentVariables.Remove("_MEIPASS2")
$psi.EnvironmentVariables.Remove("_PYI_APPLICATION_HOME_DIR") 
$psi.EnvironmentVariables.Remove("_PYI_PARENT_PROCESS_LEVEL")

# CRITICAL: Force child to create its own _MEI extraction directory (PyInstaller 6.9+)
$psi.EnvironmentVariables["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

# Start the process
[System.Diagnostics.Process]::Start($psi) | Out-Null
'''
    
    try:
        with open(ps_script_path, "w", encoding="utf-8") as f:
            f.write(ps_content)
    except Exception as e:
        raise RuntimeError(f"Failed to write PowerShell launcher: {e}")
    
    # Also write debug info
    debug_path = config_dir / "launch-hidden-debug.txt"
    try:
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"Raw command: {raw_cmd_line}\n")
            f.write(f"Exe path: {exe_path}\n")
            f.write(f"Args: {args_for_ps}\n")
            f.write(f"\nPowerShell script ({ps_script_path}):\n{ps_content}\n")
    except Exception:
        pass
    
    # Use VBScript to run PowerShell hidden (PowerShell itself might show a window briefly)
    vbs_path = config_dir / "launch-hidden.vbs"
    ps_path_escaped = str(ps_script_path).replace('"', '""')
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{ps_path_escaped}""", 0, False
'''
    
    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        
        # Run the VBS with wscript (silent VBS executor)
        # wscript runs VBScript without showing any console window
        CREATE_NO_WINDOW = 0x08000000
        result = subprocess.run(
            ["wscript", "//NoLogo", str(vbs_path)],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
            env=wscript_env,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"VBScript launcher failed (exit {result.returncode}): {result.stderr or result.stdout}")
        
        # Give the child a moment to start and verify it's running
        import time as _time
        _time.sleep(1.0)
        
        # Check if the child process is running
        # When frozen (PyInstaller), it's cyberdriver.exe; when running from source, it's python.exe
        is_frozen = getattr(sys, "frozen", False)
        expected_image = "cyberdriver.exe" if is_frozen else "python.exe"
        
        try:
            check = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {expected_image}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if expected_image.lower() not in check.stdout.lower():
                # Child didn't start - check if log file has any errors
                if stdio_log_path.exists():
                    try:
                        log_content = stdio_log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                        if log_content.strip():
                            raise RuntimeError(f"Child process failed to stay running. Log:\n{log_content}")
                    except Exception:
                        pass
                raise RuntimeError(f"Child process did not start ({expected_image} not found in tasklist)")
        except subprocess.TimeoutExpired:
            pass  # tasklist timed out, assume it's fine
        except RuntimeError:
            raise
        except Exception:
            pass  # Other errors checking, assume it's fine
        
    except Exception as e:
        # Don't delete VBS on error so user can debug
        raise RuntimeError(f"Failed to launch background process: {e}")
    
    # Clean up VBS file on success
    try:
        vbs_path.unlink(missing_ok=True)
    except Exception:
        pass


def _windows_try_enable_ansi() -> bool:
    """Best-effort enable ANSI escape processing on Windows consoles.

    If this fails, we should avoid printing ANSI sequences (they render as garbage).
    """
    if platform.system() != "Windows":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if not handle:
            return False

        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False

        ENABLE_PROCESSED_OUTPUT = 0x0001
        ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        new_mode = (
            mode.value
            | ENABLE_PROCESSED_OUTPUT
            | ENABLE_WRAP_AT_EOL_OUTPUT
            | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
        if kernel32.SetConsoleMode(handle, new_mode) == 0:
            return False
        return True
    except Exception:
        return False


def _follow_log_file(path: pathlib.Path) -> None:
    """Tail a log file to stdout until Ctrl+C or Enter."""
    # On Windows, prevent the console from freezing when clicked (QuickEdit selection mode).
    try:
        disable_windows_console_quickedit()
    except Exception:
        pass

    print("\n--- Cyberdriver logs (tail) ---")
    print("(Press Ctrl+C or Enter to stop watching logs. Cyberdriver will continue running.)\n")

    # Allow "press Enter to stop" without breaking Ctrl+C behavior.
    # We only enable this when stdin is interactive (tty).
    stop_event = None
    try:
        import threading

        if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            stop_event = threading.Event()

            def _wait_for_enter() -> None:
                try:
                    sys.stdin.readline()
                except Exception:
                    return
                try:
                    stop_event.set()  # type: ignore[union-attr]
                except Exception:
                    pass

            threading.Thread(target=_wait_for_enter, daemon=True).start()
    except Exception:
        stop_event = None

    # Wait briefly for the child to create the log file.
    start = time.time()
    while not path.exists() and (time.time() - start) < 5.0:
        try:
            if stop_event is not None and stop_event.is_set():  # type: ignore[union-attr]
                print("\n\nStopped watching logs.")
                return
        except Exception:
            pass
        time.sleep(0.1)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # Show a bit of recent history so the user sees startup context.
            try:
                f.seek(0, os.SEEK_END)
                end = f.tell()
                # Read up to last ~16KB
                start_pos = max(0, end - 16 * 1024)
                f.seek(start_pos, os.SEEK_SET)
                if start_pos > 0:
                    # Discard partial first line
                    f.readline()
                history = f.read()
                if history:
                    print(history, end="" if history.endswith("\n") else "\n")
            except Exception:
                pass

            # Now follow new lines.
            f.seek(0, os.SEEK_END)
            while True:
                try:
                    if stop_event is not None and stop_event.is_set():  # type: ignore[union-attr]
                        print("\n\nStopped watching logs.")
                        return
                except Exception:
                    pass
                # If the writer truncated the file (size cap), reset our read position.
                try:
                    if path.exists():
                        cur_size = path.stat().st_size
                        if f.tell() > cur_size:
                            f.seek(0, os.SEEK_SET)
                except Exception:
                    pass
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopped watching logs.")
    except Exception as e:
        print(f"\n\nLog tail stopped: {e}")


def _default_stdio_log_path() -> pathlib.Path:
    return get_config_dir() / "logs" / "cyberdriver-stdio.log"


def _should_use_color() -> bool:
    """Return True if we should emit ANSI color sequences to stdout."""
    try:
        if os.environ.get("CYBERDRIVER_NO_COLOR") or os.environ.get("NO_COLOR"):
            return False
        # If logging is redirected to a file, avoid ANSI escapes.
        if os.environ.get("CYBERDRIVER_STDIO_LOG"):
            return False
        if any(str(a).startswith("--_stdio-log=") for a in sys.argv):
            return False
        if hasattr(sys.stdout, "isatty") and not sys.stdout.isatty():
            return False
        if platform.system() == "Windows":
            return _windows_try_enable_ansi()
        return True
    except Exception:
        return False


def _print_prominent_stop_hint() -> None:
    """Print a prominent 'how to stop' hint for background mode."""
    if _should_use_color():
        bold = "\033[1m"
        red = "\033[91m"
        reset = "\033[0m"
        print(f"{bold}{red}To stop Cyberdriver:{reset} {bold}cyberdriver stop{reset}")
        print(f"(Optional): view logs with {bold}cyberdriver logs{reset}")
    else:
        print("To stop Cyberdriver: cyberdriver stop")
        print("(Optional): view logs with cyberdriver logs")


def _get_running_instance_pid_info() -> Optional[Dict[str, Any]]:
    """Return pidfile info if a running Cyberdriver instance is detected."""
    pid_path = get_pid_file_path()
    if not pid_path.exists():
        return None
    try:
        info = json.loads(pid_path.read_text(encoding="utf-8"))
    except Exception:
        info = {}
    pid = info.get("pid")
    try:
        pid_int = int(pid)
    except Exception:
        pid_int = -1
    if pid_int <= 0:
        return None
    if not _pid_is_running(pid_int):
        return None

    # Safety: On Windows, verify the image name (or fall back to argv heuristic) so
    # we don't treat a recycled PID as a running Cyberdriver instance.
    if platform.system() == "Windows":
        image = _windows_tasklist_image_name(pid_int)
        if image:
            image_l = image.lower()
            if image_l == "cyberdriver.exe":
                return info
            if image_l in ("python.exe", "pythonw.exe"):
                return info if _pidfile_looks_like_cyberdriver(info) else None
            return None
        return info if _pidfile_looks_like_cyberdriver(info) else None

    return info


# Define websocket compatibility helper inline
async def connect_with_headers(uri, headers_dict):
    """Compatibility wrapper for websocket connections with headers and keepalive settings.
    
    IMPORTANT: Creates a fresh SSL context for EVERY connection to avoid cached
    session issues that can cause reconnection failures. This mimics what happens
    when you Ctrl+C and restart the process.
    """
    import ssl
    
    # Create a FRESH SSL context for every connection attempt
    # This is critical - the default context caches SSL sessions, and if a session
    # gets into a bad state (e.g., server closed unexpectedly), it can poison
    # future connections. Creating a fresh context ensures we start clean.
    #
    # We use certifi's CA bundle to ensure we have up-to-date root certificates,
    # which fixes TLS errors on Windows machines missing Let's Encrypt's ISRG Root X1.
    try:
        ca_file = certifi.where()
        if os.path.exists(ca_file):
            ssl_context = ssl.create_default_context(cafile=ca_file)
            # Best-effort debug signal (no-op unless debug logger is enabled).
            try:
                debug_logger.debug("SSL", f"Using certifi CA bundle: {ca_file}")
            except Exception:
                pass
        else:
            # Certifi bundle not found (PyInstaller bundling issue?) - fall back to system
            print(f"[WARNING] Certifi CA bundle not found at: {ca_file}")
            print("[WARNING] Falling back to system CA store")
            ssl_context = ssl.create_default_context()
    except Exception as e:
        # Any error with certifi - fall back to system defaults
        print(f"[WARNING] Certifi error: {e}")
        print("[WARNING] Falling back to system CA store")
        ssl_context = ssl.create_default_context()
    
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
        # Use our fresh SSL context (this is the key fix!)
        "ssl": ssl_context,
    }
    
    debug_logger.debug("WEBSOCKET", f"Connecting to {uri}",
                       ping_interval=ws_kwargs['ping_interval'],
                       ping_timeout=ws_kwargs['ping_timeout'])
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
PID_FILE = "cyberdriver.pid.json"
VERSION = "0.0.37"

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


def get_pid_file_path() -> pathlib.Path:
    return get_config_dir() / PID_FILE


def _remove_pid_file_safely() -> None:
    try:
        # Python 3.8+
        get_pid_file_path().unlink(missing_ok=True)  # type: ignore[arg-type]
        return
    except TypeError:
        # Python <3.8 compatibility
        pass
    except Exception:
        return

    try:
        p = get_pid_file_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass


def write_pid_info(info: Dict[str, Any]) -> None:
    """Write a PID file so `cyberdriver stop` can find the running instance."""
    try:
        pid_path = get_pid_file_path()
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        payload = dict(info)
        payload.setdefault("pid", os.getpid())
        payload.setdefault("version", VERSION)
        payload.setdefault("started_at", datetime.now().isoformat())
        payload.setdefault("frozen", bool(getattr(sys, "frozen", False)))
        payload.setdefault("argv", sys.argv[:])

        tmp = pid_path.with_suffix(pid_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, pid_path)

        atexit.register(_remove_pid_file_safely)
    except Exception:
        # Best-effort only
        pass


def _pid_is_running(pid: int) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
    except Exception:
        return False

    if platform.system() == "Windows":
        try:
            # Use CSV output so we can reliably detect "not found".
            # When no match exists, tasklist prints:
            #   INFO: No tasks are running which match the specified criteria.
            CREATE_NO_WINDOW = 0x08000000
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            if not out:
                return False
            if "No tasks are running" in out:
                return False
            # If a row exists, it's running.
            return True
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def _get_process_cmdline(pid: int) -> Optional[str]:
    if platform.system() == "Windows":
        # Avoid spawning PowerShell windows; prefer tasklist parsing elsewhere.
        return None
    else:
        try:
            proc_cmdline = pathlib.Path(f"/proc/{pid}/cmdline")
            if proc_cmdline.exists():
                raw = proc_cmdline.read_bytes()
                parts = [p.decode(errors="ignore") for p in raw.split(b"\x00") if p]
                return " ".join(parts) if parts else None
        except Exception:
            pass
        return None


def _cmdline_looks_like_cyberdriver(cmdline: str) -> bool:
    s = (cmdline or "").lower()
    return (
        "cyberdriver" in s
        or "cyberdriver.py" in s
        or "cyberdriver.exe" in s
    )


def _windows_tasklist_image_name(pid: int) -> Optional[str]:
    """Return the image name for PID, or None if not found."""
    if platform.system() != "Windows":
        return None
    try:
        import csv
        from io import StringIO

        CREATE_NO_WINDOW = 0x08000000
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if not out or "No tasks are running" in out:
            return None
        row = next(csv.reader(StringIO(out)))
        # CSV format: "Image Name","PID","Session Name","Session#","Mem Usage"
        if not row:
            return None
        return (row[0] or "").strip().strip('"') or None
    except Exception:
        return None


def _pidfile_looks_like_cyberdriver(info: Dict[str, Any]) -> bool:
    try:
        argv = info.get("argv") or []
        s = " ".join(str(a) for a in argv).lower()
        return "cyberdriver" in s
    except Exception:
        return False


def stop_running_instance(force: bool = False, timeout_seconds: float = 10.0) -> int:
    """Stop the running Cyberdriver instance found via PID file."""
    pid_path = get_pid_file_path()
    if not pid_path.exists():
        print("Cyberdriver is already stopped.")
        return 0

    try:
        info = json.loads(pid_path.read_text(encoding="utf-8"))
    except Exception:
        info = {}

    pid = info.get("pid")
    try:
        pid_int = int(pid)
    except Exception:
        print("PID file exists but is invalid. Removing it.")
        _remove_pid_file_safely()
        return 0

    if not _pid_is_running(pid_int):
        print("Cyberdriver is already stopped (removing stale pid file).")
        _remove_pid_file_safely()
        return 0

    if platform.system() == "Windows" and not force:
        image = _windows_tasklist_image_name(pid_int)
        # In release builds we expect cyberdriver.exe; in source/dev we may see python.exe.
        if image:
            image_l = image.lower()
            if image_l == "cyberdriver.exe":
                # Definitely ours - proceed
                pass
            elif image_l in ("python.exe", "pythonw.exe"):
                # Could be cyberdriver running from source, or a completely different Python script.
                # Verify via PID file argv to avoid killing the wrong process if PID was recycled.
                if not _pidfile_looks_like_cyberdriver(info):
                    print("Refusing to stop: python.exe PID does not look like Cyberdriver.")
                    print("Use `cyberdriver stop --force` if you're sure.")
                    return 2
            else:
                # Unexpected image name - refuse unless force
                print(f"Refusing to stop: PID {pid_int} is {image}, not cyberdriver.exe.")
                print("Use `cyberdriver stop --force` if you're sure.")
                return 2
        else:
            # If we can't determine the image name, fall back to pidfile argv heuristic.
            if not _pidfile_looks_like_cyberdriver(info):
                print("Refusing to stop: PID does not look like Cyberdriver.")
                print("Use `cyberdriver stop --force` if you're sure.")
                return 2

    print(f"Stopping Cyberdriver (PID {pid_int})...")

    if platform.system() == "Windows":
        # For Windows, go straight to force kill (/F). A hidden background process
        # can't receive graceful termination signals, so there's no point trying.
        try:
            CREATE_NO_WINDOW = 0x08000000
            r = subprocess.run(
                ["taskkill", "/PID", str(pid_int), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            if r.returncode != 0 and "Access is denied" in out:
                print("Access denied. If Cyberdriver was started from an Administrator shell, run `cyberdriver stop` as Administrator.")
                return 1
        except Exception:
            pass

        time.sleep(0.3)
        if not _pid_is_running(pid_int):
            _remove_pid_file_safely()
            print("Cyberdriver stopped.")
            return 0
        print("Failed to stop Cyberdriver.")
        print("Please check Windows Task Manager for cyberdriver.exe. You can kill the task there.")
        return 1
    
    # For non-Windows, try graceful SIGTERM first
    deadline = time.time() + max(0.0, float(timeout_seconds))

    try:
        os.kill(pid_int, signal.SIGTERM)
    except Exception:
        pass

    while time.time() < deadline:
        if not _pid_is_running(pid_int):
            _remove_pid_file_safely()
            print("Cyberdriver stopped.")
            return 0
        time.sleep(0.2)

    try:
        os.kill(pid_int, signal.SIGKILL)
    except Exception:
        pass
    time.sleep(0.3)
    if not _pid_is_running(pid_int):
        _remove_pid_file_safely()
        print("Cyberdriver stopped (killed).")
        return 0
    print("Failed to stop Cyberdriver.")
    print("Please check your system's process manager to manually kill the cyberdriver process.")
    return 1


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
    app.state.start_time = time.time()
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


@app.get("/internal/diagnostics")
async def get_diagnostics():
    """Get diagnostic information for debugging connection issues."""
    import gc
    
    diagnostics = {
        "timestamp": time.time(),
        "uptime_seconds": time.time() - app.state.start_time if hasattr(app.state, "start_time") else None,
        "platform": platform.system(),
        "python_version": sys.version,
        "asyncio_tasks": len(asyncio.all_tasks()),
        "thread_count": threading.active_count(),
        "gc_counts": gc.get_count(),
    }
    
    # Add process-level info if available
    try:
        import psutil
        process = psutil.Process()
        diagnostics["memory_mb"] = process.memory_info().rss / (1024 * 1024)
        diagnostics["open_files"] = len(process.open_files())
        diagnostics["num_fds"] = process.num_fds() if hasattr(process, "num_fds") else None
        diagnostics["connections"] = len(process.net_connections())
    except ImportError:
        diagnostics["psutil"] = "not installed (pip install psutil for more diagnostics)"
    except Exception as e:
        diagnostics["psutil_error"] = str(e)
    
    # Add GDI handle count on Windows (important for detecting leaks)
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes
            # GetGuiResources returns the count of GDI or USER objects
            GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
            GetGuiResources = ctypes.windll.user32.GetGuiResources
            GR_GDIOBJECTS = 0
            GR_USEROBJECTS = 1
            handle = GetCurrentProcess()
            diagnostics["gdi_objects"] = GetGuiResources(handle, GR_GDIOBJECTS)
            diagnostics["user_objects"] = GetGuiResources(handle, GR_USEROBJECTS)
        except Exception as e:
            diagnostics["gdi_error"] = str(e)
    
    return diagnostics


# -----------------------------------------------------------------------------
# Self-Update System (Windows)
# -----------------------------------------------------------------------------

GITHUB_RELEASES_API_URL = "https://api.github.com/repos/cyberdesk-hq/cyberdriver/releases"
GITHUB_DOWNLOAD_BASE_URL = "https://github.com/cyberdesk-hq/cyberdriver/releases/download"

# Global connection info (set when run_join is called)
_connection_info: dict = {
    "host": None,  # e.g., "api.cyberdesk.io"
    "port": None,  # e.g., 443
}


def _set_connection_info(host: str, port: int) -> None:
    """Store connection info for use by update endpoint."""
    _connection_info["host"] = host
    _connection_info["port"] = port


def _get_api_base_url() -> Optional[str]:
    """Get the API base URL if connection info is available."""
    if _connection_info["host"] and _connection_info["port"]:
        protocol = "https" if _connection_info["port"] == 443 else "http"
        return f"{protocol}://{_connection_info['host']}"
    return None


async def _fetch_latest_version_from_api() -> Optional[str]:
    """
    Fetch the latest Cyberdriver version from Cyberdesk API.
    Returns None if API is unavailable.
    """
    api_base = _get_api_base_url()
    if not api_base:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_base}/v1/internal/cyberdriver-version",
                follow_redirects=True
            )
            if resp.status_code == 200:
                data = resp.json()
                version = data.get("latest_version")
                if version:
                    print(f"Got latest version from Cyberdesk API: {version}")
                    return version
    except Exception as e:
        print(f"Failed to fetch version from Cyberdesk API: {e}")
    
    return None


async def _fetch_latest_version_from_github() -> Optional[str]:
    """
    Fetch the latest Cyberdriver version from GitHub releases.
    Returns None if GitHub is unavailable or rate limited.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                GITHUB_RELEASES_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
                follow_redirects=True
            )
            if resp.status_code != 200:
                print(f"GitHub API returned {resp.status_code}")
                return None
            
            releases = resp.json()
            if not releases:
                return None
            
            # Find the latest version by parsing semantic versions
            def parse_version(tag: str) -> tuple:
                tag = tag.lstrip("v")
                try:
                    parts = tag.split(".")
                    return tuple(int(p) for p in parts)
                except (ValueError, AttributeError):
                    return (0, 0, 0)
            
            # Filter to only version-like tags
            version_releases = []
            for release in releases:
                tag = release.get("tag_name", "")
                clean_tag = tag.lstrip("v")
                if clean_tag and all(p.isdigit() for p in clean_tag.split(".")):
                    version_releases.append(release)
            
            if not version_releases:
                return None
            
            # Sort by version and get the latest
            version_releases.sort(key=lambda r: parse_version(r.get("tag_name", "")), reverse=True)
            latest_version = version_releases[0].get("tag_name", "").lstrip("v")
            
            if latest_version:
                print(f"Got latest version from GitHub: {latest_version}")
            return latest_version
            
    except Exception as e:
        print(f"Failed to fetch version from GitHub: {e}")
        return None


async def _resolve_latest_version() -> Optional[str]:
    """
    Resolve the latest Cyberdriver version.
    Tries Cyberdesk API first, falls back to GitHub.
    """
    # Try Cyberdesk API first (has caching and higher rate limits)
    version = await _fetch_latest_version_from_api()
    if version:
        return version
    
    # Fall back to GitHub
    print("Falling back to GitHub for version info...")
    return await _fetch_latest_version_from_github()


class UpdateRequest(BaseModel):
    version: str = Field(default="latest", description="Target version (e.g. '0.0.34') or 'latest'")
    restart: bool = Field(default=True, description="Whether to restart Cyberdriver after update")


@app.post("/internal/update")
async def post_update(payload: UpdateRequest = UpdateRequest()):
    """
    Self-update Cyberdriver on Windows.
    
    This endpoint:
    1. Downloads the new version to a staging location
    2. Creates an updater script that waits for this process to exit
    3. The updater script replaces the executable and optionally restarts
    4. This process exits gracefully
    
    Request body (optional):
    {
        "version": "0.0.34",  // Target version (without 'v' prefix), or "latest" (default)
        "restart": true       // Whether to restart after update (default: true)
    }
    
    Returns:
    {
        "status": "update_initiated",
        "current_version": "0.0.34",
        "target_version": "0.0.34",
        "message": "Updating to v0.0.34. Cyberdriver will restart automatically."
    }
    """
    if platform.system() != "Windows":
        return JSONResponse(
            status_code=501, 
            content={"error": "Self-update is currently only supported on Windows"}
        )
    
    target_version = payload.version
    restart_after = payload.restart
    
    try:
        # Get the current executable path
        if getattr(sys, 'frozen', False):
            current_exe = sys.executable
        else:
            # Running as script - this won't work for self-update
            return JSONResponse(
                status_code=400,
                content={"error": "Self-update only works with compiled executable, not Python script"}
            )
        
        # Resolve "latest" version - tries Cyberdesk API first, falls back to GitHub
        if target_version == "latest":
            resolved_version = await _resolve_latest_version()
            if not resolved_version:
                return JSONResponse(
                    status_code=502,
                    content={"error": "Could not determine latest version (API and GitHub both unavailable)"}
                )
            target_version = resolved_version
            print(f"Resolved 'latest' to version {target_version}")
        
        # Check if already at target version or running newer
        def parse_version(v: str) -> tuple:
            return tuple(int(p) for p in v.replace("v", "").split("."))
        
        try:
            current_parts = parse_version(VERSION)
            target_parts = parse_version(target_version)
            
            if current_parts >= target_parts:
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "already_up_to_date",
                        "current_version": VERSION,
                        "target_version": target_version,
                        "message": "Cyberdriver is already running the requested version" if current_parts == target_parts 
                                   else f"Cyberdriver is already running a newer version ({VERSION})"
                    }
                )
        except (ValueError, AttributeError):
            # If version parsing fails, fall back to string comparison
            if target_version == VERSION:
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "already_up_to_date",
                        "current_version": VERSION,
                        "target_version": target_version,
                        "message": "Cyberdriver is already running the requested version"
                    }
                )
        
        # Build download URL for Windows executable 
        download_url = f"{GITHUB_DOWNLOAD_BASE_URL}/v{target_version}/cyberdriver.exe"
        
        # Get staging paths in the same directory as the current executable
        tool_dir = os.path.dirname(current_exe)
        staging_exe = os.path.join(tool_dir, "cyberdriver-update.exe")
        
        print(f"\n{'='*60}")
        print(f"SELF-UPDATE INITIATED")
        print(f"{'='*60}")
        print(f"Current version: {VERSION}")
        print(f"Target version:  {target_version}")
        print(f"Download URL:    {download_url}")
        print(f"Staging path:    {staging_exe}")
        print(f"Restart after:   {restart_after}")
        print(f"{'='*60}\n")
        
        # Download new version to staging location
        print(f"Downloading cyberdriver v{target_version}...")
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(download_url, follow_redirects=True)
            if resp.status_code == 404:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Version v{target_version} not found on GitHub releases"}
                )
            if resp.status_code != 200:
                return JSONResponse(
                    status_code=502,
                    content={"error": f"Failed to download update: HTTP {resp.status_code}"}
                )
            
            # Write to staging file
            with open(staging_exe, "wb") as f:
                f.write(resp.content)
        
        download_size_mb = os.path.getsize(staging_exe) / (1024 * 1024)
        print(f"Downloaded: {download_size_mb:.2f} MB")
        
        # Get current process ID for the updater to wait on
        current_pid = os.getpid()
        
        # Build restart command with original arguments (only if restart_after is True)
        # Preserve all command-line arguments so the new instance runs with the same config
        restart_args: Optional[List[str]] = None
        if restart_after:
            # Get original arguments (skip argv[0] which is the executable path)
            raw_args = sys.argv[1:] if len(sys.argv) > 1 else []

            # IMPORTANT (Stealth Mode): background instances include internal flags like
            # --_detached-child and --_stdio-log=... that should NOT be persisted across a
            # self-update restart. If we keep them, the restarted process may skip the
            # normal background-launch flow, or attempt to tail logs in a non-interactive
            # context.
            restart_args = []
            skip_next = False
            for arg in raw_args:
                if skip_next:
                    skip_next = False
                    continue
                if arg in ("--_detached-child", "--detach", "--tail"):
                    continue
                if arg == "--_stdio-log":
                    skip_next = True
                    continue
                if arg.startswith("--_stdio-log="):
                    continue
                restart_args.append(arg)

            def _obfuscate_sensitive_args(args_list: List[str]) -> List[str]:
                obfuscated: List[str] = []
                skip = False
                for a in args_list:
                    if skip:
                        obfuscated.append("***")
                        skip = False
                        continue
                    if a in ("--secret", "-s"):
                        obfuscated.append(a)
                        skip = True
                        continue
                    if a.startswith("--secret="):
                        obfuscated.append("--secret=***")
                        continue
                    if a.startswith("-s="):
                        obfuscated.append("-s=***")
                        continue
                    obfuscated.append(a)
                return obfuscated
            
            # Escape arguments that contain spaces or special characters for batch file
            escaped_args = []
            for arg in restart_args:
                # If arg contains spaces, quotes, or special chars, wrap in quotes
                if ' ' in arg or '"' in arg or '&' in arg or '|' in arg or '<' in arg or '>' in arg or '^' in arg:
                    # Escape any existing quotes by doubling them
                    escaped_arg = arg.replace('"', '""')
                    escaped_args.append(f'"{escaped_arg}"')
                else:
                    escaped_args.append(arg)
            
            args_str = ' '.join(escaped_args)
            
            print(f"Restart arguments: {_obfuscate_sensitive_args(restart_args)}")
            print(f"Escaped arguments for restart: {_obfuscate_sensitive_args(escaped_args)}")
        
        # Create updater PowerShell script (more reliable than batch on modern Windows)
        # Log file for update process
        update_log = os.path.join(tool_dir, "cyberdriver-update.log")
        updater_ps1 = os.path.join(tool_dir, "cyberdriver-updater.ps1")
        
        # Build restart command for PowerShell
        if restart_after:
            # Escape paths and args for embedding in PowerShell
            exe_escaped = current_exe.replace("'", "''")
            args_escaped = args_str.replace("'", "''")
            
            # Generate a unique task name
            task_name = f"CyberdriverRestart_{uuid.uuid4().hex[:8]}"
            
            # Check if we're running elevated - if so, preserve elevation in the scheduled task
            # This avoids UAC prompts when restarting with --black-screen-recovery or any other optional flags that need admin rights
            needs_elevation = is_running_as_admin()
            run_level = "Highest" if needs_elevation else "Limited"
            
            ps_restart_cmd = f'''
# Restart cyberdriver using scheduled task (most reliable method for hidden process -> visible console)
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Starting cyberdriver via scheduled task..."
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Exe: {exe_escaped}"
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Args: {args_escaped}"
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] RunLevel: {run_level} (was_elevated: {str(needs_elevation).lower()})"

try {{
    $exePath = '{exe_escaped}'
    $arguments = '{args_escaped}'
    $taskName = '{task_name}'
    
    # Create a scheduled task that runs immediately
    # This creates an entirely new process tree with proper console allocation
    # Execute cyberdriver directly - scheduled tasks create their own console for console apps
    $action = New-ScheduledTaskAction -Execute $exePath -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(1)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    # Use Highest run level if original process was elevated, to preserve admin rights without UAC prompt
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive -RunLevel {run_level}
    
    # Register the task
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Scheduled task '$taskName' created with RunLevel {run_level}"
    
    # Start the task immediately (don't wait for trigger)
    Start-ScheduledTask -TaskName $taskName
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Scheduled task started"
    
    # Wait a moment for task to fully start
    Start-Sleep -Seconds 2
    
    # Clean up the scheduled task
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Scheduled task cleaned up"
    
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Start command executed successfully"
}} catch {{
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] ERROR starting cyberdriver: $($_.Exception.Message)"
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Stack trace: $($_.ScriptStackTrace)"
}}
'''
        else:
            ps_restart_cmd = '''
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Restart skipped (restart=false)"
'''
        
        updater_content = f'''# Cyberdriver Self-Updater - PowerShell version
# Generated automatically - do not edit

$logFile = "{update_log}"
$stagingExe = "{staging_exe}"
$currentExe = "{current_exe}"
$targetPid = {current_pid}

Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Cyberdriver Self-Updater started (PowerShell)"
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Waiting for cyberdriver (PID $targetPid) to exit..."

# Wait for the process to exit (up to 30 seconds)
$maxWait = 30
$waited = 0
while ($waited -lt $maxWait) {{
    try {{
        $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
        if ($null -eq $proc) {{
            Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Process exited"
            break
        }}
    }} catch {{
        Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Process exited (exception)"
        break
    }}
    Start-Sleep -Seconds 1
    $waited++
}}

if ($waited -ge $maxWait) {{
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Timeout - force killing process"
    try {{ Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue }} catch {{}}
    Start-Sleep -Seconds 2
}}

Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Applying update..."

# Wait a bit more for file handles to release
Start-Sleep -Seconds 2

# Replace the executable
Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Copying new version..."
$copySuccess = $false
for ($i = 0; $i -lt 3; $i++) {{
    try {{
        Copy-Item -Path $stagingExe -Destination $currentExe -Force -ErrorAction Stop
        $copySuccess = $true
        Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Copy successful"
        break
    }} catch {{
        Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Copy attempt $($i+1) failed: $($_.Exception.Message)"
        Start-Sleep -Seconds 2
    }}
}}

if (-not $copySuccess) {{
    Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] ERROR: Failed to apply update after 3 attempts"
    exit 1
}}

Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Update successful! Updated to version {target_version}"

# Clean up staging file
try {{
    Remove-Item -Path $stagingExe -Force -ErrorAction SilentlyContinue
}} catch {{}}

{ps_restart_cmd}

Add-Content -Path $logFile -Value "[$((Get-Date).ToString())] Self-updater completed"

# Clean up scripts (self-delete with delay)
Start-Sleep -Seconds 2
$vbsLauncher = $MyInvocation.MyCommand.Path -replace '\.ps1$', '-launcher.vbs'
Remove-Item -Path $vbsLauncher -Force -ErrorAction SilentlyContinue
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
'''
        
        with open(updater_ps1, "w", encoding="utf-8") as f:
            f.write(updater_content)
        
        print(f"Created updater script: {updater_ps1}")
        
        # Create a VBScript wrapper to launch PowerShell truly hidden
        # This is the industry-standard way to run scripts invisibly on Windows
        # PowerShell's -WindowStyle Hidden is unreliable and can flash windows
        vbs_launcher = os.path.join(tool_dir, "cyberdriver-updater-launcher.vbs")
        vbs_content = f'''Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell -NoProfile -ExecutionPolicy Bypass -File ""{updater_ps1}""", 0, False
'''
        with open(vbs_launcher, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        
        print(f"Created VBS launcher: {vbs_launcher}")
        
        # Launch the VBScript which will launch PowerShell truly hidden
        # wscript runs VBScript files without any console window
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        
        subprocess.Popen(
            ["wscript", vbs_launcher],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        print(f"Updater script launched successfully.")
        print(f"Cyberdriver will exit in 2 seconds to allow update...")
        
        # Schedule graceful shutdown after returning response
        async def delayed_exit():
            await asyncio.sleep(2.0)
            print(f"\n{'='*60}")
            print(f"Exiting for self-update...")
            if restart_after:
                print(f"Cyberdriver will restart automatically after update.")
            print(f"{'='*60}\n")
            os._exit(0)  # Force exit to release all file handles
        
        asyncio.create_task(delayed_exit())
        
        # Build response with preserved arguments info
        response_content = {
            "status": "update_initiated",
            "current_version": VERSION,
            "target_version": target_version,
            "restart": restart_after,
            "message": f"Updating to v{target_version}. Cyberdriver will restart automatically."
        }
        
        # Include preserved arguments in response if restarting (obfuscate secrets)
        if restart_after:
            # Use sanitized restart args (no internal stealth-mode flags).
            response_content["preserved_arguments"] = _obfuscate_sensitive_args(restart_args or [])
        
        return JSONResponse(status_code=200, content=response_content)
        
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": "Timeout while downloading update from GitHub"}
        )
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"Update failed: {error_msg}")
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Update failed: {error_msg}",
                "details": traceback.format_exc()
            }
        )


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
    
    # Retry logic for transient mss failures (Windows Desktop Duplication API can fail randomly)
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
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
        
        except Exception as e:
            last_error = e
            error_msg = str(e) if str(e) else type(e).__name__
            if attempt < max_retries - 1:
                # Brief delay before retry
                await asyncio.sleep(0.05)
                print(f"Screenshot capture failed (attempt {attempt + 1}/{max_retries}): {error_msg}, retrying...")
            else:
                print(f"Screenshot capture failed after {max_retries} attempts: {error_msg}")
    
    # All retries exhausted
    error_detail = str(last_error) if last_error and str(last_error) else f"Screen capture failed ({type(last_error).__name__})"
    raise HTTPException(status_code=500, detail=error_detail)


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

def _ensure_capslock_off_linux_sync():
    """Linux-specific caps lock check (runs in thread pool due to subprocess)."""
    try:
        result = subprocess.run(['xset', 'q'], capture_output=True, text=True)
        if 'Caps Lock:   on' in result.stdout:
            pyautogui.press('capslock')
            print("Caps Lock was ON - toggled OFF before typing")
    except Exception:
        pass


async def _ensure_capslock_off():
    """Ensure Caps Lock is in the OFF state before typing.
    
    This prevents case inversion issues when Caps Lock is accidentally left on
    in the VM/remote session.
    
    Windows/macOS: Synchronous (fast API calls, no thread overhead)
    Linux: Runs in thread pool (subprocess.run would block event loop)
    """
    if platform.system() == "Windows":
        import ctypes
        VK_CAPITAL = 0x14
        # GetKeyState is essentially instant - no need for thread pool
        if ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 1:
            pyautogui.press('capslock')
            print("Caps Lock was ON - toggled OFF before typing")
    elif platform.system() == "Darwin":
        # macOS: Quartz is fast, no thread pool needed
        try:
            import Quartz
            flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState)
            if flags & Quartz.kCGEventFlagMaskAlphaShift:
                pyautogui.press('capslock')
                print("Caps Lock was ON - toggled OFF before typing")
        except ImportError:
            pass
    else:
        # Linux: subprocess.run blocks, so use thread pool
        await asyncio.to_thread(_ensure_capslock_off_linux_sync)


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
    # Normalize key name: lowercase and remove underscores
    # This allows both "Page_Down" and "pagedown" to work
    key_lower = key.lower().replace('_', '')
    
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
    
    # Ensure Caps Lock is OFF to prevent case inversion
    await _ensure_capslock_off()
    
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
            print("⚠ Warning: Clipboard is empty after all retry attempts ")
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
        # Escape single quotes by doubling them (PowerShell single-quote escape)
        escaped_dir = working_directory.replace("'", "''")
        script_lines.append(f"Set-Location -Path '{escaped_dir}'")
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
        
        # Truncate output to prevent token limit issues
        stdout_output = maybe_truncate_output("\n".join(stdout_lines))
        stderr_output = maybe_truncate_output("\n".join(stderr_lines))
        
        return {
            "stdout": stdout_output,
            "stderr": stderr_output,
            "exit_code": process.returncode,
            "session_id": session_id
        }
        
    except subprocess.TimeoutExpired:
        # Don't kill the process - let it continue in background
        # Just return a message indicating timeout while command continues
        return {
            "stdout": "",
            "stderr": f"Command timeout reached after {timeout} seconds. Process continues in background.",
            "exit_code": 0,  # Return success code since we're allowing it to continue
            "session_id": session_id,
            "timeout_reached": True  # New flag to indicate timeout (not error)
        }
    except Exception as e:
        error_msg = maybe_truncate_output(str(e))
        return {
            "stdout": "",
            "stderr": error_msg,
            "exit_code": -1,
            "session_id": session_id,
            "error": error_msg
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
            "stdout": maybe_truncate_output(result.stdout),
            "stderr": maybe_truncate_output(result.stderr)
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except Exception as e:
        return {"error": maybe_truncate_output(str(e))}

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
    
    # Idempotency cache settings
    IDEMPOTENCY_CACHE_TTL = 60.0  # Seconds to keep cached responses
    IDEMPOTENCY_CACHE_MAX_SIZE = 1000  # Maximum number of cached responses
    
    def __init__(self, host: str, port: int, secret: str, target_port: int, config: Config, keepalive_manager: Optional["KeepAliveManager"] = None, remote_keepalive_for_main_id: Optional[str] = None):
        self.host = host
        self.port = port
        self.secret = secret
        self.target_port = target_port
        self.config = config
        self.min_sleep = 1
        self.max_sleep = 16
        self._connection_attempt = 0
        self._consecutive_failures = 0  # Track consecutive short-lived connections for diagnostics
        self.keepalive_manager = keepalive_manager
        self.remote_keepalive_for_main_id = remote_keepalive_for_main_id
        
        # Idempotency cache: key -> (timestamp, response)
        # Used to prevent duplicate execution of actions when retries occur
        self._idempotency_cache: Dict[str, Tuple[float, dict]] = {}
        
    def _cleanup_before_retry(self):
        """Clean up state before each connection retry.
        
        This runs on EVERY retry to ensure we start as fresh as possible,
        mimicking what happens during Ctrl+C + restart.
        
        Cleanup includes:
        - Reset ThreadPoolExecutor (clears any stuck threads)
        - Clear idempotency cache (removes stale entries)
        - Multiple GC passes to clean up circular references
        - SSL context is created fresh per-connection in connect_with_headers()
        
        This is cheap (~100-250ms) compared to retry delays (1-16s).
        """
        import gc
        
        # Clear the keepalive countdown line before printing debug logs
        # (the countdown uses \r to overwrite itself, so we need to clear it first)
        try:
            if self.keepalive_manager is not None and hasattr(self.keepalive_manager, "_clear_countdown_line"):
                self.keepalive_manager._clear_countdown_line()
        except Exception:
            pass
        
        debug_logger.debug("CLEANUP", "Cleaning up before retry",
                          consecutive_failures=self._consecutive_failures)
        
        # 1. Reset the global ThreadPoolExecutor
        # This clears any stuck or leaked threads
        # Using wait=True ensures cancelled futures finish before creating new executor
        global executor
        try:
            executor.shutdown(wait=True, cancel_futures=True)
            executor = ThreadPoolExecutor(max_workers=5)
        except Exception as e:
            debug_logger.debug("CLEANUP", f"ThreadPoolExecutor reset failed: {e}")
        
        # 2. Clear the idempotency cache (might have stale entries)
        cache_size = len(self._idempotency_cache)
        if cache_size > 0:
            self._idempotency_cache.clear()
            debug_logger.debug("CLEANUP", f"Cleared idempotency cache ({cache_size} entries)")
        
        # 3. Multiple GC passes to clean up circular references
        # This takes ~50-200ms but ensures no lingering objects
        total_collected = 0
        for i in range(3):
            total_collected += gc.collect(i)
        if total_collected > 0:
            debug_logger.debug("CLEANUP", f"GC collected {total_collected} objects")
        
    async def run(self):
        """Run the tunnel with exponential backoff reconnection.
        
        IMPORTANT: Each retry does full cleanup to mimic Ctrl+C + restart:
        - Fresh SSL context (in connect_with_headers)
        - Reset ThreadPoolExecutor
        - Clear caches
        - Garbage collection
        """
        import random
        
        sleep_time = self.min_sleep
        
        while True:
            connection_start = time.time()
            
            # Full cleanup before EVERY connection attempt
            # This mimics what happens when you Ctrl+C and restart
            self._cleanup_before_retry()
            
            try:
                await self._connect_and_run()
                # This line is never reached - _connect_and_run always raises
                sleep_time = self.min_sleep
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                # Allow task cancellation to stop the tunnel immediately
                raise
            except (ConnectionClosed, InvalidStatus) as e:
                # Handle WebSocket close codes specially
                close_code = None
                close_reason = None
                
                if isinstance(e, ConnectionClosed):
                    close_code = (e.rcvd.code if e.rcvd else None) or (e.sent.code if e.sent else None)
                    close_reason = (e.rcvd.reason if e.rcvd else None) or (e.sent.reason if e.sent else None)
                elif isinstance(e, InvalidStatus):
                    # Server rejected connection before WebSocket handshake
                    close_code = e.response.status_code
                    # Try to extract reason from exception message
                    close_reason = str(e)
                
                connection_duration = time.time() - connection_start
                debug_logger.connection_closed(
                    close_reason or "Unknown", 
                    connection_duration,
                    close_code=close_code
                )
                
                # Only reset failure counter if connection lasted more than 10 seconds
                # Short-lived connections indicate an ongoing problem
                if connection_duration > 10:
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures += 1
                    debug_logger.warning("CONNECTION", f"Connection only lasted {connection_duration:.1f}s",
                                        consecutive_failures=self._consecutive_failures)
                
                # Authentication failures should NOT retry
                # - Close code 4001 (WebSocket close after accept)
                # - HTTP 403 (rejected before accept, which is what FastAPI sends)
                is_auth_error = (close_code == 4001 or close_code == 403)
                
                if is_auth_error:
                    print(f"\n{'='*60}")
                    print(f"❌ Authentication Failed")
                    print(f"{'='*60}")
                    print(f"\nReason: {close_reason or 'Invalid or expired API key'}")
                    print("\n⚠️  Cyberdriver will NOT retry to prevent excessive API key validation attempts.")
                    print("\nPlease check:")
                    print("   1. Your API key is correct (from Cyberdesk dashboard)")
                    print("   2. The API key hasn't been revoked or regenerated")
                    print("   3. Your organization has access to this service")
                    print(f"\n{'='*60}\n")
                    # Exit instead of retrying
                    sys.exit(1)
                
                # Rate limit handling - custom close code 4008
                if close_code == 4008:
                    # Extract wait duration from reason (format: "Wait X seconds")
                    wait_seconds = 60  # Default
                    if close_reason:
                        try:
                            # Parse "Wait 60 seconds" or similar
                            match = re.search(r'Wait (\d+) seconds', close_reason)
                            if match:
                                wait_seconds = int(match.group(1))
                        except:
                            pass
                    
                    print(f"\n{'='*60}")
                    print(f"⚠️  Rate Limit Exceeded")
                    print(f"{'='*60}")
                    print(f"\nYou've reconnected too frequently.")
                    print(f"This helps prevent server overload and protects your account.")
                    print(f"\n⏱️  Waiting {wait_seconds} seconds before reconnecting...")
                    print(f"{'='*60}\n")
                    
                    # Wait the exact duration (don't use exponential backoff)
                    await asyncio.sleep(wait_seconds)
                    # Reset sleep time after rate limit wait
                    sleep_time = self.min_sleep
                    continue
                
                # For other close codes, continue with exponential backoff
                error_msg = str(e).lower()
                print(f"\n{'='*60}")
                print(f"WebSocket Connection Error: {e}")
                if close_code:
                    print(f"Close Code: {close_code}")
                if close_reason:
                    print(f"Close Reason: {close_reason}")
                print(f"{'='*60}")
                
                # Provide guidance for common errors
                if close_code in [1008, 1011]:  # Policy violation or server error
                    print("\n⚠️  Server rejected connection")
                    print("\nThis might be a temporary server issue.")
                else:
                    print("\n⚠️  Connection was closed unexpectedly")
                    print("\nCommon fixes:")
                    print("   1. Check your internet connection")
                    print("   2. Verify the server is accessible")
                
                # Add random jitter (0-30%) to avoid thundering herd and give network stack time to clean up
                jittered_sleep = sleep_time * (1 + random.uniform(0, 0.3))
                
                print(f"\n{'='*60}")
                print(f"Retrying in {jittered_sleep:.1f} seconds...")
                print(f"{'='*60}\n")
                
                # Note: _consecutive_failures is already handled above based on connection duration
                # (reset to 0 if >10s, incremented if <10s)
                await asyncio.sleep(jittered_sleep)
                sleep_time = min(sleep_time * 2, self.max_sleep)
                
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
                
                elif "errno 2" in error_msg or "no such file or directory" in error_msg:
                    # This is a Windows-specific error that occurs when the server closes
                    # the connection immediately after the handshake completes
                    print("\n⚠️  Connection Closed by Server")
                    print("\nThe server accepted the connection but then closed it.")
                    print("\nPossible causes:")
                    print("   1. Server is temporarily overloaded or restarting")
                    print("   2. Network proxy/firewall interference")
                    print("   3. Rapid reconnection attempts triggered rate limiting")
                    print("\nThis usually resolves after a few retries.")
                    
                else:
                    print("\n⚠️  Unknown Connection Error")
                    print("\nCommon fixes:")
                    print("   1. Check your API key: --secret YOUR_KEY")
                    print("   2. Install TLS certificates: https://github.com/cyberdesk-hq/cyberdriver#tls-certificate-errors")
                    print("   3. Check your internet connection")
                
                # Add random jitter (0-30%) to avoid thundering herd and give network stack time to clean up
                jittered_sleep = sleep_time * (1 + random.uniform(0, 0.3))
                
                print(f"\n{'='*60}")
                print(f"Retrying in {jittered_sleep:.1f} seconds...")
                print(f"{'='*60}\n")
                
                # Apply same connection duration logic as ConnectionClosed handler
                # Only count as failure if connection was short-lived
                connection_duration = time.time() - connection_start
                if connection_duration > 10:
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures += 1
                    debug_logger.warning("CONNECTION", f"Connection only lasted {connection_duration:.1f}s",
                                        consecutive_failures=self._consecutive_failures)
                
                await asyncio.sleep(jittered_sleep)
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
        self._connection_attempt += 1
        debug_logger.connection_attempt(uri, self._connection_attempt)
        
        try:
            websocket = await connect_with_headers(uri, headers)
        except Exception as e:
            # Re-raise with more context about what failed
            error_type = type(e).__name__
            debug_logger.error("CONNECTION", f"Failed to connect: {e}", error_type=error_type)
            raise ConnectionError(f"{error_type}: {str(e)} (connecting to {uri})") from e
        
        # Track connection timing for debugging
        connection_start_time = time.time()
        debug_logger.connection_established(uri)
        
        async with websocket:
            # Print a success message. If we're logging to a file (detached/background mode),
            # avoid ANSI escape sequences so logs stay readable.
            # Ensure countdown line (if any) is cleared before printing.
            try:
                if self.keepalive_manager is not None and hasattr(self.keepalive_manager, "_clear_countdown_line"):
                    self.keepalive_manager._clear_countdown_line()
            except Exception:
                pass
            connected_msg = f"Connected! Forwarding to http://127.0.0.1:{self.target_port}"
            if _should_use_color():
                green = "\033[92m"
                white = "\033[97m"
                reset = "\033[0m"
                print(f"{green}✓{reset} {white}{connected_msg}{reset}")
            else:
                print(f"✓ {connected_msg}")
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
                # Log entering message loop
                debug_logger.message_loop_entered()
                
                try:
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
                except OSError as e:
                    # Capture detailed info about the OS-level error
                    import errno
                    error_code = getattr(e, 'errno', None)
                    error_name = errno.errorcode.get(error_code, 'UNKNOWN') if error_code else 'UNKNOWN'
                    connection_duration = time.time() - connection_start_time
                    
                    debug_logger.connection_failed(
                        str(e), 
                        connection_duration, 
                        error_type=f"OSError({error_code}:{error_name})"
                    )
                    
                    # Also log resource stats on failure to help diagnose
                    debug_logger.resource_stats()
                    
                    # Always print duration for immediate failures (< 5 seconds)
                    if connection_duration < 5.0:
                        print(f"[Connection failed after {connection_duration:.2f}s]")
                    raise

                # If we exit the async for without an exception, the server closed gracefully
                connection_duration = time.time() - connection_start_time
                
                # Get close info from the websocket object
                ws_close_code = getattr(websocket, 'close_code', None)
                ws_close_reason = getattr(websocket, 'close_reason', None) or "Server closed connection"
                
                debug_logger.connection_closed(ws_close_reason, connection_duration, close_code=ws_close_code)
                
                # Ensure we signal this to the reconnection loop by raising to trigger backoff
                raise RuntimeError(f"WebSocket closed by server (code={ws_close_code})")
    
    def _cleanup_idempotency_cache(self) -> None:
        """Remove expired entries from the idempotency cache."""
        now = time.time()
        # Use list() to snapshot items - cache may be modified concurrently
        expired_keys = [
            key for key, (timestamp, _) in list(self._idempotency_cache.items())
            if now - timestamp > self.IDEMPOTENCY_CACHE_TTL
        ]
        for key in expired_keys:
            # Use pop() to safely handle concurrent deletions
            self._idempotency_cache.pop(key, None)
        
        # If cache is still too large, remove oldest entries
        if len(self._idempotency_cache) > self.IDEMPOTENCY_CACHE_MAX_SIZE:
            # Snapshot keys and timestamps together to avoid race condition
            snapshot = [(k, v[0]) for k, v in list(self._idempotency_cache.items())]
            sorted_keys = [k for k, _ in sorted(snapshot, key=lambda x: x[1])]
            # Remove oldest 20% of entries
            to_remove = sorted_keys[:len(sorted_keys) // 5]
            for key in to_remove:
                # Use pop() to safely handle concurrent deletions
                self._idempotency_cache.pop(key, None)
    
    async def _forward_request(self, meta: dict, body: bytes, client: httpx.AsyncClient) -> dict:
        """Forward request to local API.
        
        Supports idempotency via the X-Idempotency-Key header. If a request with the
        same idempotency key was processed recently (within IDEMPOTENCY_CACHE_TTL),
        the cached response is returned without re-executing the action. This prevents
        duplicate actions when retries occur due to network issues or timeouts.
        """
        request_start = time.time()
        method = meta["method"].upper()
        path = meta["path"]
        query = meta.get("query", "")
        headers = meta.get("headers", {})
        
        # Check for idempotency key (case-insensitive header lookup)
        idempotency_key: Optional[str] = None
        for key, value in headers.items():
            if key.lower() == "x-idempotency-key":
                idempotency_key = value
                break
        
        # If idempotency key provided, check cache first
        if idempotency_key:
            self._cleanup_idempotency_cache()
            
            if idempotency_key in self._idempotency_cache:
                cached_time, cached_response = self._idempotency_cache[idempotency_key]
                if time.time() - cached_time < self.IDEMPOTENCY_CACHE_TTL:
                    print(f"[Idempotency] Returning cached response for {method} {path} (key: {idempotency_key[:8]}...)")
                    return cached_response
        
        url = f"http://127.0.0.1:{self.target_port}{path}"
        if query:
            url += f"?{query}"
        
        # For PowerShell exec requests, extract timeout from body and use custom client
        # For all other requests, use the default client with 30s timeout
        use_custom_timeout = False
        request_timeout = 30.0
        if path == "/computer/shell/powershell/exec" and body:
            try:
                payload = json.loads(body.decode('utf-8'))
                if "timeout" in payload:
                    # Add buffer to prevent race condition with subprocess timeout
                    # The local FastAPI will timeout the subprocess at exactly `timeout` seconds,
                    # so we need to wait slightly longer to receive that response 
                    request_timeout = float(payload["timeout"]) + 3.0  # 3s buffer for local processing
                    use_custom_timeout = True
            except Exception:
                pass  # Fall back to default client if parsing fails
        
        result: Optional[dict] = None
        
        try:
            # If a keepalive action is currently running, wait for it to finish
            if self.keepalive_manager is not None:
                if self.keepalive_manager.is_busy():
                    print("Keepalive: waiting for current action to finish before handling request…")
                await self.keepalive_manager.wait_until_idle()
                # Record that we are actively processing a request
                self.keepalive_manager.record_activity()
            # IMPORTANT: Use stream=True to avoid buffering the entire response
            # For PowerShell exec with custom timeout, create a new client; otherwise use default
            if use_custom_timeout:
                timeout_obj = httpx.Timeout(
                    connect=5.0,
                    read=request_timeout,  # Now includes buffer to avoid race with subprocess timeout
                    write=30.0,
                    pool=30.0
                )
                async with httpx.AsyncClient(timeout=timeout_obj) as request_client:
                    async with request_client.stream(method, url, headers=headers, content=body) as response:
                        duration_ms = (time.time() - request_start) * 1000
                        print(f"{method} {path} -> {response.status_code}")
                        debug_logger.request_forwarded(method, path, response.status_code, duration_ms)
                        
                        # Read the response body immediately to avoid buffering
                        body_chunks = []
                        async for chunk in response.aiter_bytes():
                            body_chunks.append(chunk)
                        
                        result = {
                            "status": response.status_code,
                            "headers": dict(response.headers),
                            "body": b''.join(body_chunks),
                        }
            else:
                # Use default client for all other requests (30s timeout) 
                async with client.stream(method, url, headers=headers, content=body) as response:
                    duration_ms = (time.time() - request_start) * 1000
                    print(f"{method} {path} -> {response.status_code}")
                    debug_logger.request_forwarded(method, path, response.status_code, duration_ms)
                    
                    # Read the response body immediately to avoid buffering
                    body_chunks = []
                    async for chunk in response.aiter_bytes():
                        body_chunks.append(chunk)
                    
                    result = {
                        "status": response.status_code,
                        "headers": dict(response.headers),
                        "body": b''.join(body_chunks),
                    }
        except Exception as e:
            duration_ms = (time.time() - request_start) * 1000
            # Ensure we always have a meaningful error message
            error_msg = str(e) if str(e) else f"{type(e).__name__}: (no details)"
            debug_logger.error("REQUEST", f"Request failed: {error_msg}", method=method, path=path, duration_ms=f"{duration_ms:.1f}ms")
            result = {
                "status": 500,
                "headers": {"content-type": "text/plain"},
                "body": error_msg.encode(),
            }
        
        # If the local API (or tunnel forwarding layer) returns an error status with an empty body,
        # synthesize a small JSON error so the cloud proxy has something actionable to display/log.
        try:
            if result and isinstance(result.get("status"), int) and result["status"] >= 400:
                body_bytes = result.get("body") or b""
                if isinstance(body_bytes, str):
                    body_bytes = body_bytes.encode("utf-8", errors="replace")
                elif not isinstance(body_bytes, (bytes, bytearray)):
                    body_bytes = str(body_bytes).encode("utf-8", errors="replace")

                if len(body_bytes) == 0:
                    placeholder = {
                        "detail": "Cyberdriver local API returned an error with an empty body",
                        "status": result["status"],
                        "method": method,
                        "path": path,
                    }
                    result["headers"] = dict(result.get("headers") or {})
                    result["headers"]["content-type"] = "application/json"
                    result["body"] = json.dumps(placeholder).encode("utf-8")
        except Exception:
            # Never fail the tunnel due to diagnostics enrichment.
            pass

        # Cache the response if idempotency key was provided and request was successful
        # We cache even 500 errors to prevent retries from re-executing a failed action
        if idempotency_key and result:
            self._idempotency_cache[idempotency_key] = (time.time(), result)
        
        return result
    
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


async def _periodic_resource_logger(interval_seconds: float = 300.0):
    """Background task to log resource stats periodically."""
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            debug_logger.resource_stats()
    except asyncio.CancelledError:
        pass


async def run_join(host: str, port: int, secret: str, target_port: int, keepalive_enabled: bool = False, 
                   keepalive_threshold_minutes: float = 3.0, interactive: bool = False, 
                   register_as_keepalive_for: Optional[str] = None,
                   keepalive_click_x: Optional[int] = None, keepalive_click_y: Optional[int] = None,
                   black_screen_recovery_enabled: bool = False,
                   black_screen_check_interval: float = 30.0,
                   debug_enabled: bool = False):
    """Run both API server and tunnel client."""
    # Store connection info for use by update endpoint
    _set_connection_info(host, port)
    
    config = get_config()
    
    # Log resource stats periodically in debug mode
    resource_logger_task = None
    if debug_enabled:
        debug_logger.info("STARTUP", f"Starting cyberdriver join",
                         host=host, port=port, target_port=target_port,
                         keepalive=keepalive_enabled)
        # Start periodic resource logging (every 5 minutes)
        resource_logger_task = asyncio.create_task(_periodic_resource_logger(300.0))
    
    # Find an available port for the local server, starting with the one provided
    actual_target_port = find_available_port("127.0.0.1", target_port)
    if actual_target_port is None:
        print(f"Error: Could not find an available port starting from {target_port}.")
        sys.exit(1)
    
    if actual_target_port != target_port:
        print(f"Using available port {actual_target_port} for local server.")

    write_pid_info(
        {
            "command": "join",
            "local_port": actual_target_port,
            "cloud_host": host,
            "cloud_port": port,
        }
    )

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
            # Stop tunnel first (graceful disconnect)
            await stop_tunnel()
            await stop_keepalive()
            await stop_black_screen_recovery()
            if resource_logger_task:
                resource_logger_task.cancel()
                try:
                    await asyncio.wait_for(resource_logger_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            # Cancel server task last
            if server_task and not server_task.done():
                server_task.cancel()
                try:
                    await asyncio.wait_for(server_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
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
            if resource_logger_task:
                resource_logger_task.cancel()
                try:
                    await asyncio.wait_for(resource_logger_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            # Cancel server task last
            if server_task and not server_task.done():
                server_task.cancel()
                try:
                    await asyncio.wait_for(server_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass


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
    # Restore close button before shutdown
    restore_windows_console_close_button()
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
    # If we're in detached/background mode (logging to a file) or the user requested it,
    # avoid ANSI codes so logs remain readable in files.
    if os.environ.get("CYBERDRIVER_NO_COLOR") or os.environ.get("CYBERDRIVER_STDIO_LOG"):
        print_banner_no_color(mode)
        return

    # Enable Windows terminal colors if needed
    if platform.system() == "Windows":
        if not _windows_try_enable_ansi():
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


def cleanup_old_mei_folders() -> None:
    """
    Clean up old PyInstaller _MEI extraction folders on startup.
    
    When running as a frozen PyInstaller onefile executable with runtime_tmpdir set,
    each run extracts to a new _MEIxxxxxx folder. If the user kills cyberdriver from
    Task Manager (or it crashes), these folders accumulate. This function removes
    stale _MEI folders from previous runs.
    
    Only runs on Windows when frozen. Silently does nothing otherwise.
    """
    if platform.system() != "Windows":
        return
    
    if not getattr(sys, 'frozen', False):
        return  # Not running as frozen exe
    
    current_mei = getattr(sys, '_MEIPASS', None)
    if not current_mei:
        return
    
    # Get the parent directory where all _MEI folders live
    mei_parent = os.path.dirname(current_mei)
    current_mei_name = os.path.basename(current_mei)
    
    cleaned_count = 0
    for folder_name in os.listdir(mei_parent):
        if not folder_name.startswith('_MEI'):
            continue
        if folder_name == current_mei_name:
            continue  # Don't delete current folder!
        
        folder_path = os.path.join(mei_parent, folder_name)
        if not os.path.isdir(folder_path):
            continue
        
        try:
            shutil.rmtree(folder_path)
            cleaned_count += 1
        except Exception:
            # Folder might be in use by another running instance, or permission denied
            pass
    
    if cleaned_count > 0:
        print(f"[INFO] Cleaned up {cleaned_count} old temporary folder(s)")


def main():
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Clean up old _MEI folders from previous runs (Windows only, PyInstaller frozen only)
    cleanup_old_mei_folders()

    # If we were launched in "detached/invisible" mode, redirect stdout/stderr to a log file.
    _setup_detached_stdio_if_configured()
    
    # Log PyInstaller environment for diagnostics (helps debug _MEI folder issues)
    # Only log in detached/background mode (logging to file) to avoid cluttering terminal output
    if getattr(sys, 'frozen', False) and platform.system() == "Windows" and os.environ.get("CYBERDRIVER_STDIO_LOG"):
        meipass = getattr(sys, '_MEIPASS', 'N/A')
        pyi_reset = os.environ.get('PYINSTALLER_RESET_ENVIRONMENT', 'not set')
        pyi_home = os.environ.get('_PYI_APPLICATION_HOME_DIR', 'not set')
        pyi_level = os.environ.get('_PYI_PARENT_PROCESS_LEVEL', 'not set')
        print(f"[PYINSTALLER] _MEIPASS={meipass}")
        print(f"[PYINSTALLER] PYINSTALLER_RESET_ENVIRONMENT={pyi_reset}")
        print(f"[PYINSTALLER] _PYI_APPLICATION_HOME_DIR={pyi_home}")
        print(f"[PYINSTALLER] _PYI_PARENT_PROCESS_LEVEL={pyi_level}")
    
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

    # stop command
    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop running Cyberdriver",
        description="Stop the running Cyberdriver instance (foreground or background) using the PID file.",
    )
    stop_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait before forcing termination (default: 10).",
    )
    stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Force stop even if the PID can't be verified as Cyberdriver.",
    )

    # logs command
    logs_parser = subparsers.add_parser(
        "logs",
        help="Tail Cyberdriver logs (realtime)",
        description="Tail the Cyberdriver stdio log file in realtime (Ctrl+C stops tailing).",
    )
    logs_parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to log file (default: ~/.cyberdriver/logs/cyberdriver-stdio.log)",
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
    join_parser.add_argument("--debug", action="store_true", help="Enable debug logging to ~/.cyberdriver/logs/ (daily log files)")
    join_parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run in the foreground with a visible console window (Windows).",
    )
    join_parser.add_argument(
        "--detach",
        action="store_true",
        help="On Windows, start in background and return immediately (do not tail logs).",
    )
    join_parser.add_argument(
        "--tail",
        action="store_true",
        help="(Windows) After starting in background, tail the log file in this terminal.",
    )
    join_parser.add_argument(
        "--_detached-child",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    join_parser.add_argument(
        "--_stdio-log",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    
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
        print("  join --secret KEY --debug                 Enable debug logging to ~/.cyberdriver/logs/")
        print("  coords                                    Capture screen coordinates (for keepalive)")
        print("  stop                                     Stop running Cyberdriver")
        print("  logs                                     Tail Cyberdriver logs (realtime)")
        print()
        print("For more info: cyberdriver join -h")
        sys.exit(0)

    if args.command == "stop":
        print("Stopping Cyberdriver...")
        sys.exit(stop_running_instance(force=bool(getattr(args, "force", False)),
                                       timeout_seconds=float(getattr(args, "timeout", 10.0))))

    if args.command == "logs":
        print("Loading Cyberdriver logs...")
        default_path = _default_stdio_log_path()
        log_path = pathlib.Path(getattr(args, "path", None) or default_path)
        if not log_path.exists():
            print(f"No log file found at: {log_path}")
            print("Start Cyberdriver with `cyberdriver join` first, then retry.")
            sys.exit(0)
        # Ensure Ctrl+C behaves like users expect for tailing (KeyboardInterrupt).
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except Exception:
            pass
        _follow_log_file(log_path)
        sys.exit(0)

    # Idempotency: if join is invoked while Cyberdriver is already running, do not
    # start another instance. This applies to both background (default) and --foreground.
    if args.command == "join" and not getattr(args, "_detached_child", False):
        info = _get_running_instance_pid_info()
        if info:
            pid_int = int(info.get("pid", -1))
            print_banner(mode="connecting")
            print(f"Cyberdriver is already running (PID {pid_int}).")
            print(f"Logs: {_default_stdio_log_path()}")
            _print_prominent_stop_hint()
            print("\nIf you want to restart with different flags, run `cyberdriver stop` first.")
            sys.exit(0)

    # On Windows, default to running `join` invisibly in a detached process so closing the
    # launching terminal (or an agent hitting Alt+F4) cannot kill Cyberdriver.
    #
    # Use `--foreground` to disable this behavior.
    if (
        platform.system() == "Windows"
        and args.command == "join"
        and not getattr(args, "foreground", False)
        and os.environ.get("CYBERDRIVER_DETACHED") != "1"
        and not getattr(args, "_detached_child", False)
    ):
        try:
            logs_dir = get_config_dir() / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            stdio_log_path = logs_dir / "cyberdriver-stdio.log"

            child_argv = sys.argv[1:]
            # Remove --foreground if present (shouldn't be, but be safe).
            child_argv = [a for a in child_argv if a != "--foreground"]
            # Remove legacy flags so they don't propagate to child.
            child_argv = [a for a in child_argv if a not in ("--detach", "--tail")]
            # Mark child so argparse doesn't try to re-detach via sys.argv alone.
            child_argv.append("--_detached-child")
            # Pass log file path to child (env vars don't work with VBScript launcher)
            child_argv.append(f"--_stdio-log={stdio_log_path}")

            # Show the nice banner in the *current* terminal (this is not the detached child).
            print_banner(mode="connecting")
            print("Cyberdriver is now running in the background.")
            print("You can close PowerShell.")
            print(f"Logs: {stdio_log_path}")
            _print_prominent_stop_hint()
            print("(You can also end cyberdriver.exe in Task Manager.)")
            print()
            _windows_relaunch_detached(child_argv, stdio_log_path)

            # Default UX: return immediately. If the user wants logs in this terminal,
            # they can opt-in with --tail.
            if bool(getattr(args, "tail", False)):
                # Make Ctrl+C stop tailing (not print the join shutdown message).
                try:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                except Exception:
                    pass
                _follow_log_file(stdio_log_path)
            return
        except Exception as e:
            print(f"\nWarning: Failed to start background process: {e}")
            print("Continuing in foreground mode instead.\n")
    
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
                if _should_use_color():
                    green = "\033[92m"
                    reset = "\033[0m"
                    print(f"{green}✓{reset} Running with administrator privileges\n")
                else:
                    print("✓ Running with administrator privileges\n")
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
    
    # For "join" command, protect the console window from being closed by the agent
    if args.command == "join" and platform.system() == "Windows":
        # Disable close button
        if disable_windows_console_close_button():
            print("✓ Console close button disabled (use Ctrl+C to exit)")
    
    try:
        if args.command == "start":
            actual_port = find_available_port("0.0.0.0", args.port)
            if actual_port is None:
                print(f"Error: Could not find an available port starting from {args.port}.")
                sys.exit(1)

            write_pid_info({"command": "start", "local_port": actual_port})
            
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
                print(f"✓ Cyberdriver server starting on http://0.0.0.0:{actual_port} ")
            run_server(actual_port)
        
        elif args.command == "coords":
            run_coords_capture()

        elif args.command == "join":
            # Initialize debug logger if --debug flag is set
            debug_enabled = bool(getattr(args, "debug", False))
            if debug_enabled:
                global debug_logger
                debug_logger = DebugLogger.initialize(enabled=True)
                print(f"✓ Debug logging enabled. Logs will be written to: {debug_logger.log_dir}")
            
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
                debug_enabled=debug_enabled,
            ))
    except KeyboardInterrupt:
        print("\n\nKeyboard interrupt received. Shutting down...")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        sys.exit(1)
    finally:
        # Restore close button if it was disabled
        try:
            if hasattr(args, 'command') and args.command == "join" and platform.system() == "Windows":
                restore_windows_console_close_button()
        except:
            pass  # Silently fail if args not available
        print("Cleanup complete.")


if __name__ == "__main__":
    main()
