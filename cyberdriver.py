"""
Cyberdriver: A comprehensive remote computer control tool
=========================================================
This module provides a feature-complete implementation for remote computer control.
It includes all features from the original Zig implementation:

- HTTP API server with all endpoints
- WebSocket tunnel client for remote control
- XDO keyboard input support (e.g., 'ctrl+c ctrl+v')
- Screenshot with scaling modes (Exact, AspectFit, AspectFill)
- Smooth mouse movement with interpolation
- Mouse button press/release control
- Configuration persistence (fingerprint, version)
- Cursor overlay (cross-platform)
- Exponential backoff reconnection

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
    python cyberdriver.py start [--port 3000] [--cursor-overlay]
    python cyberdriver.py join --secret API_KEY [--host example.com] [--port 443] [--cursor-overlay]
"""

import argparse
import asyncio
import base64
import json
import os
import platform
import pathlib
import re
import subprocess
import sys
import time
import uuid
from typing import Dict, List, Optional, Tuple, Union, Any
from enum import Enum
from dataclasses import dataclass
from io import BytesIO

import httpx
import mss
import mss.tools
import numpy as np
import pyautogui
from PIL import Image, ImageDraw
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, JSONResponse
import uvicorn
import websockets

# Define websocket compatibility helper inline
async def connect_with_headers(uri, headers_dict):
    """Compatibility wrapper for websocket connections with headers."""
    # Try websockets v15+ API (uses additional_headers)
    try:
        return await websockets.connect(uri, additional_headers=headers_dict)
    except TypeError:
        pass
    
    # Try websockets v10-14 API (uses extra_headers)
    try:
        return await websockets.connect(uri, extra_headers=headers_dict)
    except TypeError:
        pass
    
    # Try list of tuples format (websockets 8.x - 9.x)
    try:
        return await websockets.connect(uri, extra_headers=list(headers_dict.items()))
    except TypeError:
        pass
    
    # Last resort - connect without headers
    print("WARNING: Could not send custom headers with WebSocket connection")
    print("This may cause authentication to fail")
    return await websockets.connect(uri)

# -----------------------------------------------------------------------------
# Configuration Management
# -----------------------------------------------------------------------------

CONFIG_DIR = ".cyberdriver"
CONFIG_FILE = "config.json"
VERSION = "0.0.7"

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
    
    if config_path.exists():
        with open(config_path, 'r') as f:
            data = json.load(f)
        return Config.from_dict(data)
    
    # Create new config
    config_dir.mkdir(parents=True, exist_ok=True)
    config = Config(version=VERSION, fingerprint=str(uuid.uuid4()))
    
    with open(config_path, 'w') as f:
        json.dump(config.to_dict(), f, indent=2)
    
    return config


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
            if event.down:
                pyautogui.keyDown(event.key)
            else:
                pyautogui.keyUp(event.key)
        # Small delay between command groups
        time.sleep(0.01)


# -----------------------------------------------------------------------------
# Mouse Movement
# -----------------------------------------------------------------------------

def smooth_mouse_move(x: int, y: int, steps: int = 10, duration: float = 0.1):
    """Move mouse smoothly to target position with interpolation."""
    # IMPORTANT: PyAutoGUI's built-in duration parameter is extremely slow on macOS
    # (takes ~2.5s minimum). So we always use manual interpolation with instant moves.
    
    # Temporarily disable PAUSE for this function to ensure speed
    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0
    
    try:
        current_x, current_y = pyautogui.position()
        
        # Skip if already at target
        if abs(current_x - x) < 2 and abs(current_y - y) < 2:
            return
        
        # Use fewer steps for faster movement (10 steps @ 10ms = 100ms total)
        # This matches Piglet's 100ms timing but with fewer, larger steps
        sleep_time = duration / steps
        
        # Calculate step increments
        dx = (x - current_x) / steps
        dy = (y - current_y) / steps
        
        # Perform smooth movement with instant moves
        for i in range(steps):
            new_x = current_x + dx * (i + 1)
            new_y = current_y + dy * (i + 1)
            pyautogui.moveTo(int(new_x), int(new_y), duration=0, _pause=False)
            time.sleep(sleep_time)
        
        # Final move to ensure exact position
        pyautogui.moveTo(x, y, duration=0, _pause=False)
    finally:
        # Restore original PAUSE setting
        pyautogui.PAUSE = original_pause


# -----------------------------------------------------------------------------
# Cursor Overlay
# -----------------------------------------------------------------------------

class CursorOverlay:
    """Cross-platform cursor overlay implementation."""
    
    def __init__(self):
        self.running = False
        self.overlay_thread = None
        self.color = (0xFF, 0x00, 0xE5)  # Pig Peach color
        
    def start(self):
        """Start the cursor overlay in a separate thread."""
        if self.running:
            return
        
        self.running = True
        import threading
        self.overlay_thread = threading.Thread(target=self._overlay_loop, daemon=True)
        self.overlay_thread.start()
    
    def stop(self):
        """Stop the cursor overlay."""
        self.running = False
        if self.overlay_thread:
            self.overlay_thread.join()
    
    def _overlay_loop(self):
        """Main overlay loop - platform specific implementation."""
        if platform.system() == "Windows":
            self._windows_overlay()
        else:
            # For non-Windows, we'll use a different approach
            print("Cursor overlay is currently only supported on Windows")
    
    def _windows_overlay(self):
        """Windows-specific overlay using tkinter."""
        try:
            import tkinter as tk
            from tkinter import ttk
            
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes('-topmost', True)
            root.attributes('-transparentcolor', 'white')
            root.configure(bg='white')
            
            # Create cursor image
            cursor_size = 30
            canvas = tk.Canvas(root, width=cursor_size, height=cursor_size, 
                             bg='white', highlightthickness=0)
            canvas.pack()
            
            # Draw cursor shape
            canvas.create_oval(2, 2, cursor_size-2, cursor_size-2, 
                             fill=f'#{self.color[0]:02x}{self.color[1]:02x}{self.color[2]:02x}',
                             outline='')
            
            def update_position():
                if not self.running:
                    root.quit()
                    return
                try:
                    x, y = pyautogui.position()
                    root.geometry(f'+{x-cursor_size//2}+{y-cursor_size//2}')
                except:
                    pass
                root.after(16, update_position)  # ~60 FPS
            
            update_position()
            root.mainloop()
            
        except ImportError:
            print("tkinter not available for cursor overlay")


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

app = FastAPI(title="Cyberdriver", version=VERSION)

# Global state
cursor_overlay = CursorOverlay()


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
        
        # Apply scaling if requested
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
    """Move the mouse cursor with smooth interpolation."""
    try:
        x = int(payload.get("x"))
        y = int(payload.get("y"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Missing or invalid 'x'/'y'")
    
    smooth_mouse_move(x, y)
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
        smooth_mouse_move(int(x), int(y))
    
    if down is None:
        # Full click
        pyautogui.click(button=button)
    elif down:
        pyautogui.mouseDown(button=button)
    else:
        pyautogui.mouseUp(button=button)
    
    return {}


# File system endpoints (marked as not implemented in original)
@app.get("/computer/fs/list")
async def get_fs_list(path: str = Query(".")):
    """List directory contents."""
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/computer/fs/read")
async def get_fs_read(path: str = Query(...)):
    """Read file contents."""
    raise HTTPException(status_code=501, detail="Not implemented")


@app.post("/computer/fs/write")
async def post_fs_write(payload: Dict[str, str]):
    """Write file contents."""
    raise HTTPException(status_code=501, detail="Not implemented")


# Shell endpoints (marked as not implemented in original)
@app.post("/computer/shell/cmd/exec")
async def post_shell_cmd_exec(payload: Dict[str, str]):
    """Execute shell command."""
    raise HTTPException(status_code=501, detail="Not implemented")


@app.post("/computer/shell/powershell/exec")
async def post_shell_powershell_exec(payload: Dict[str, str]):
    """Execute PowerShell command."""
    raise HTTPException(status_code=501, detail="Not implemented")


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
            print("Connected to control server")
            
            # Message handling state
            request_meta = None
            body_buffer = bytearray()
            
            async with httpx.AsyncClient() as http_client:
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
    
    async def _forward_request(self, meta: dict, body: bytes, client: httpx.AsyncClient) -> dict:
        """Forward request to local API."""
        method = meta["method"].upper()
        path = meta["path"]
        query = meta.get("query", "")
        headers = meta.get("headers", {})
        
        url = f"http://localhost:{self.target_port}{path}"
        if query:
            url += f"?{query}"
        
        try:
            response = await client.request(
                method, url, headers=headers, content=body
            )
            
            print(f"{method} {path} -> {response.status_code}")
            
            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": await response.aread(),
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


async def run_join(host: str, port: int, secret: str, target_port: int):
    """Run both API server and tunnel client."""
    config = get_config()
    
    # Start API server in background thread
    loop = asyncio.get_event_loop()
    server_task = loop.run_in_executor(None, run_server, target_port)
    
    # Start tunnel client
    tunnel = TunnelClient(host, port, secret, target_port, config)
    await tunnel.run()


def main():
    parser = argparse.ArgumentParser(
        description="Cyberdriver: Remote computer control"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # start command
    start_parser = subparsers.add_parser("start", help="Start local server")
    start_parser.add_argument("--port", type=int, default=3000, 
                            help="Port for local API server")
    start_parser.add_argument("--cursor-overlay", action="store_true",
                            help="Enable cursor overlay")
    
    # join command
    join_parser = subparsers.add_parser("join", 
                                      help="Join control plane")
    join_parser.add_argument("--host", default="api.cyberdesk.io", 
                           help="Control server host")
    join_parser.add_argument("--port", type=int, default=443, 
                           help="Control server port")
    join_parser.add_argument("--secret", required=True, 
                           help="API key for authentication")
    join_parser.add_argument("--target-port", type=int, default=3000,
                           help="Local API port")
    join_parser.add_argument("--cursor-overlay", action="store_true",
                           help="Enable cursor overlay")
    
    args = parser.parse_args()
    
    # Start cursor overlay if requested
    if getattr(args, 'cursor_overlay', False):
        cursor_overlay.start()
    
    try:
        if args.command == "start":
            print(f"Local server running at http://localhost:{args.port}")
            run_server(args.port)
        elif args.command == "join":
            asyncio.run(run_join(
                args.host, args.port, args.secret, args.target_port
            ))
    finally:
        # Cleanup
        cursor_overlay.stop()


if __name__ == "__main__":
    main()