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
  - Default: 1024x768 (matching Piglet, recommended for Claude)
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

### Windows PowerShell Installation

The below PowerShell script will install Cyberdriver onto your Windows machine, and add the cyberdriver executable to your PATH.

```powershell
# Create tool directory
$toolDir = "$env:USERPROFILE\.cyberdriver"
New-Item -ItemType Directory -Force -Path $toolDir

# Download cyberdriver
Invoke-WebRequest -Uri "https://github.com/cyberdesk-hq/cyberdriver/releases/download/v0.0.12/cyberdriver.exe" -OutFile "$toolDir\cyberdriver.exe"

# Add to PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$toolDir*") {
    [Environment]::SetEnvironmentVariable("Path", $userPath + ";" + $toolDir, "User")
}

Write-Host "Cyberdriver installed! You may need to restart your terminal for PATH changes to take effect."
```

**Note:** Cyberdriver automatically disables PowerShell's QuickEdit Mode on startup. PowerShell has this dumb quirk where focusing your mouse on a running executable can stall the outputs until you unfocus it (it's called "QuickEdit Mode"). Additionally, cyberdriver requires administrator privileges to automate legacy desktop apps that have to be run as admin.

Cyberdriver can then be started with:

```bash
cyberdriver start
```

Or subscribed for remote use via Cyberdesk cloud:

```bash
cyberdriver join --secret SK-YOUR-SECRET-KEY --host https://cyberdesk-new.fly.dev
```

## Common Issues

### TLS Certificate Errors

If you get an error regarding TLS Certificates, CTRL+C and then run the following:

```powershell
# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    $store = "Cert:\LocalMachine\Root"
    Write-Host "Running as Administrator - installing system-wide" -ForegroundColor Green
} else {
    $store = "Cert:\CurrentUser\Root"
    Write-Host "Running as user - installing for current user only" -ForegroundColor Yellow
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Only the working certificate URLs
$certs = @(
    @{
        Name = "ISRG Root X1"
        Url = "https://letsencrypt.org/certs/isrgrootx1.der"
    }
)

foreach ($cert in $certs) {
    try {
        Write-Host "Downloading $($cert.Name)..." -ForegroundColor Cyan
        $tempFile = "$env:TEMP\$($cert.Name -replace ' ','_').der"
        Invoke-WebRequest -Uri $cert.Url -OutFile $tempFile -UseBasicParsing
        
        $certObj = Import-Certificate -FilePath $tempFile -CertStoreLocation $store
        Write-Host "✓ Installed $($cert.Name)" -ForegroundColor Green
        
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "✗ Failed to install $($cert.Name): $_" -ForegroundColor Red
    }
}
```

Then retry cyberdriver join!

> If you have any other issues, reach out to the team! We'll get on it asap.




### Basic Installation from Source

```bash
pip install -r requirements.txt
```

## Usage

### Start Local Server
```bash
python cyberdriver.py start --port 3000
```

### Join Remote Control Server
```bash
python cyberdriver.py join --secret YOUR_API_KEY --host https://cyberdesk-new.fly.dev
```

## Configuration

Configuration is stored in:
- Windows: `%LOCALAPPDATA%\.cyberdriver\config.json`
- Linux/macOS: `~/.config/.cyberdriver/config.json`

The config file contains:
```json
{
  "version": "0.0.12",
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
