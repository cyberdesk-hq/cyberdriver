# Cyberdriver

A comprehensive remote computer control tool with all major features for remote desktop automation and control.

## Features

### Complete Feature Set
- ✅ **HTTP API Server** - All endpoints for display, keyboard, and mouse control
- ✅ **WebSocket Tunnel** - Connect to remote control servers with proper protocol
- ✅ **XDO Keyboard Input** - Support for complex key sequences like `ctrl+c ctrl+v`
- ✅ **Screenshot Scaling** - Three modes: Exact, AspectFit, AspectFill
- ✅ **Smooth Mouse Movement** - 20-step interpolated movement
- ✅ **Mouse Button Control** - Separate press/release for drag operations
- ✅ **Configuration Persistence** - UUID fingerprint and version tracking
- ✅ **Cursor Overlay** - Visual cursor indicator (Windows, with tkinter fallback)
- ✅ **Exponential Backoff** - Robust reconnection with increasing delays

### API Endpoints

#### Display
- `GET /computer/display/screenshot` - Capture screen with optional scaling
  - Query params: `width`, `height`, `mode` (exact|aspect_fit|aspect_fill)
- `GET /computer/display/dimensions` - Get screen dimensions

#### Keyboard
- `POST /computer/input/keyboard/type` - Type text string
- `POST /computer/input/keyboard/key` - Execute XDO key sequence

#### Mouse
- `GET /computer/input/mouse/position` - Get current position
- `POST /computer/input/mouse/move` - Move to position (smooth interpolation)
- `POST /computer/input/mouse/click` - Click with optional press/release control

#### Not Implemented (matching original)
- File system endpoints (list, read, write)
- Shell command execution (cmd, powershell)

## Installation

### Basic Installation
```bash
pip install -r requirements.txt
```

## Usage

### Start Local Server
```bash
python cyberdriver.py start --port 3000 --cursor-overlay
```

### Join Remote Control Server
```bash
python cyberdriver.py join --secret YOUR_API_KEY --host api.cyberdesk.io --cursor-overlay
```

## Configuration

Configuration is stored in:
- Windows: `%LOCALAPPDATA%\.cyberdriver\config.json`
- Linux/macOS: `~/.config/.cyberdriver/config.json`

The config file contains:
```json
{
  "version": "0.0.7",
  "fingerprint": "uuid-v4-string"
}
```

## Building Standalone Executable

### Using PyInstaller
```bash
pip install pyinstaller
pyinstaller cyberdriver.spec
```

The executable will be in the `dist/` directory.

## Key Features

1. **Cross-platform cursor overlay** - Uses tkinter on Windows, prints warning on other platforms
2. **Filesystem/Shell endpoints** - Return 501 Not Implemented
3. **Smooth mouse movement** - Configurable steps and duration
4. **Enhanced error handling** - Better error messages and recovery

## Dependencies

- **fastapi** - HTTP API server
- **uvicorn** - ASGI server
- **websockets** - WebSocket client
- **httpx** - HTTP client
- **mss** - Screen capture
- **pyautogui** - Keyboard/mouse control
- **pillow** - Image processing
- **numpy** - Efficient array operations

## Development

### Running Tests
```bash
python -m pytest tests/
```

### Code Structure
- Configuration management (`.cyberdriver/config.json`)
- Screenshot scaling algorithms (Exact, AspectFit, AspectFill)
- XDO keyboard sequence parser
- Smooth mouse movement interpolation
- Cursor overlay system
- WebSocket tunnel with proper framing
- Exponential backoff reconnection 