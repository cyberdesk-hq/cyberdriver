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
try {
    Invoke-WebRequest -Uri "https://github.com/cyberdesk-hq/cyberdriver/releases/download/v0.0.38/cyberdriver.exe" -OutFile "$toolDir\cyberdriver.exe" -ErrorAction Stop
} catch {
    Write-Host "ERROR: Failed to download Cyberdriver. Please check your internet connection and try again." -ForegroundColor Red
    return
}

# Verify installation
if (Test-Path "$toolDir\cyberdriver.exe") {
    $fileSize = (Get-Item "$toolDir\cyberdriver.exe").Length
    if ($fileSize -gt 34MB) {
        # Add to PATH if not already there
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath -notlike "*$toolDir*") {
            [Environment]::SetEnvironmentVariable("Path", $userPath + ";" + $toolDir, "User")
        }
        Write-Host "Cyberdriver installed successfully! You may need to restart your terminal for PATH changes to take effect."
    } else {
        Write-Host "ERROR: Download appears incomplete (file too small). Please try again." -ForegroundColor Red
    }
} else {
    Write-Host "ERROR: Download failed. Please try again." -ForegroundColor Red
}
```

### macOS Installation (Bash/Zsh)

```bash
# Choose version and target directory
VERSION=0.0.38
TOOL_DIR="$HOME/.cyberdriver"
mkdir -p "$TOOL_DIR"

# Detect architecture (arm64 or x86_64)
ARCH=$(uname -m)

# Download and install
curl -L "https://github.com/cyberdesk-hq/cyberdriver/releases/download/v${VERSION}/cyberdriver-macos-${ARCH}.zip" -o "$TOOL_DIR/cyberdriver.zip"
unzip -o "$TOOL_DIR/cyberdriver.zip" -d "$TOOL_DIR"
chmod +x "$TOOL_DIR/cyberdriver"

# Add to PATH (Zsh)
if ! echo ":$PATH:" | grep -q ":$TOOL_DIR:"; then
  echo "export PATH=\"$TOOL_DIR:$PATH\"" >> "$HOME/.zshrc"
  echo "Added to PATH. Restart your terminal or run: source $HOME/.zshrc"
fi

# Permissions: grant Terminal/iTerm access in System Settings → Privacy & Security
echo "Please enable:"
echo "- Accessibility"
echo "- Screen Recording"

# Optional: remove quarantine attribute if blocked
# xattr -d com.apple.quarantine "$TOOL_DIR/cyberdriver" || true

# Run it
"$TOOL_DIR/cyberdriver" join --secret YOUR_API_KEY
```

**Note (Windows):** Cyberdriver automatically disables PowerShell's QuickEdit Mode on startup. PowerShell has this dumb quirk where focusing your mouse on a running executable can stall the outputs until you unfocus it (it's called "QuickEdit Mode"). 

**Important - Admin Privileges:** If the desktop application you want to automate requires administrator privileges to start (such as many legacy enterprise applications), you must also run cyberdriver from an Administrator PowerShell terminal:

1. Right-click on PowerShell and select "Run as Administrator"
2. Navigate to your desired directory
3. Run `cyberdriver start` or `cyberdriver join --secret YOUR_KEY`

This ensures cyberdriver has the necessary permissions to interact with elevated applications. If you're only automating regular user-level applications, you can run cyberdriver normally without admin privileges.

Cyberdriver can then be started with:

```bash
cyberdriver start
```

Or subscribed for remote use via Cyberdesk cloud:

```bash
cyberdriver join --secret SK-YOUR-SECRET-KEY
```

## Agent Protection (Preventing Accidental Termination)

When running cyberdriver on Windows, the console window is visible and can be accidentally closed by AI agents during automated workflows. We provide these built in protections:

### Built-in Protections (Automatic)

When you run `cyberdriver join` on Windows, protection is automatically enabled:
- **Close button disabled** - The X button is grayed out and non-functional
- **Invisible background mode (default)** - Cyberdriver relaunches itself detached with **no visible console window**. Closing/Alt+F4'ing the original PowerShell window will **not** kill Cyberdriver.
- **QuickEdit disabled** - Prevents console hanging if clicked

No extra setup needed! Just run:
```bash
cyberdriver join --secret YOUR_API_KEY
```

**Windows UX (recommended):**

- `cyberdriver join ...` starts Cyberdriver **in the background (no window)** and returns you to the prompt.
- To stop Cyberdriver, run: `cyberdriver stop` (or end `cyberdriver.exe` in Task Manager).
- Logs are written to: `%LOCALAPPDATA%\.cyberdriver\logs\cyberdriver-stdio.log`

**Want to tail logs in your current PowerShell?**

```bash
cyberdriver join --secret YOUR_API_KEY --tail
```

**Want a visible console for debugging?**

Run in the foreground:

```bash
cyberdriver join --secret YOUR_API_KEY --foreground
```

**To stop:**

- Foreground mode: `Ctrl+C`
- Background mode: `cyberdriver stop` (or Task Manager)

## Self-Update (Windows)

Cyberdriver can update itself remotely, even while running. This is useful for:
- Updating machines managed through Cyberdesk workflows
- Keeping all your machines on the latest version without manual intervention
- Zero-downtime updates (machine is only offline for ~10 seconds)

### Via execute_terminal_command

Run this PowerShell command through `execute_terminal_command`:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:3000/internal/update" -ContentType "application/json" -Body '{"version":"latest","restart":true}'
```

Or specify a specific version:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:3000/internal/update" -ContentType "application/json" -Body '{"version":"0.0.34","restart":true}'
```

### Via Cyberdesk API

You can also call the update endpoint directly through the Cyberdesk API:

```bash
curl -X POST "https://api.cyberdesk.io/v1/computer/{machine_id}/internal/update" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"version": "latest", "restart": true}'
```

### How It Works

1. Downloads the new version from GitHub releases to a staging location
2. Creates an updater batch script that waits for Cyberdriver to exit
3. Cyberdriver exits gracefully (the updater script runs in the background)
4. The updater replaces `cyberdriver.exe` with the new version
5. If `restart=true`, Cyberdriver automatically restarts with the **same arguments** it was originally started with

**Arguments Preserved:** All original command-line flags are preserved on restart. For example, if you started with:
```bash
cyberdriver join --secret SK-xxx --keepalive --keepalive-threshold-minutes 5 --black-screen-recovery
```

After the update, Cyberdriver will restart with those exact same flags.

**Note:** The machine will be briefly offline (~10 seconds) during the update.

## Common Issues

### TLS Certificate Errors

**v0.0.36+**: Cyberdriver now bundles its own CA certificates, so TLS errors on Windows machines missing root certs should be fixed automatically.

If you're on an older version and encounter TLS certificate errors, update to the latest version:

```powershell
Invoke-WebRequest -Uri "https://github.com/cyberdesk-hq/cyberdriver/releases/download/v0.0.38/cyberdriver.exe" -OutFile "$env:USERPROFILE\.cyberdriver\cyberdriver.exe"
```

#### Corporate Networks with SSL Inspection

If you're on a corporate network that uses SSL inspection (e.g., Zscaler, Palo Alto, Fortinet) and see errors like `self signed certificate in certificate chain`, your IT department's SSL inspection certificate needs to be trusted.

**Option 1: Use System Certificate Store**

If your IT department has installed their CA certificate in the Windows certificate store, tell Cyberdriver to use it:

```powershell
cyberdriver join --secret YOUR_API_KEY --use-system-certs
```

**Option 2: Use a Custom CA File**

If you have your corporate CA certificate as a file:

```powershell
cyberdriver join --secret YOUR_API_KEY --ca-file "C:\path\to\corporate-ca.crt"
```

**Option 3: Disable SSL Verification (Testing Only)**

⚠️ **INSECURE** - Only use this for testing. Your connection will NOT be protected against attacks.

```powershell
cyberdriver join --secret YOUR_API_KEY --no-ssl-verify
```

You can also use environment variables instead: `CYBERDRIVER_USE_SYSTEM_CERTS=true`, `CYBERDRIVER_CA_FILE=/path/to/ca.crt`, or `CYBERDRIVER_SSL_VERIFY=false`.

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

### Keepalive Mode

Some environments suspend or lock when idle, which can interrupt automation. Enable Cyberdriver's keepalive to gently simulate user activity when no work is incoming.

```bash
cyberdriver join --secret YOUR_API_KEY --keepalive
```

**Options:**
- `--keepalive`: Enable keepalive background worker
- `--keepalive-threshold-minutes`: Idle minutes before keepalive runs (default: 3)
- `--keepalive-click-x` and `--keepalive-click-y`: Custom click coordinates (optional)

**⚠️ Virtual Display Warning (RustDesk, RDP, etc.):**
If using keepalive with virtual displays (RustDesk, RDP, VNC), you **must** specify custom click coordinates away from screen edges. The default bottom-left click can trigger issues when the display disconnects/reconnects. Use `cyberdriver coords` to capture safe coordinates (center of screen recommended).

**Example with custom click location:**
```bash
# Click at center of 1024x768 display (recommended for virtual displays)
cyberdriver join --secret YOUR_API_KEY --keepalive \
  --keepalive-click-x 512 \
  --keepalive-click-y 384
```

**Behavior:**
- Tracks last time a cloud request was received
- When idle beyond the threshold, performs a short, realistic action:
  - Clicks at the specified coordinates (or bottom-left if not specified)
  - Types 2–5 short phrases with natural intervals
  - Presses Esc to close any UI
- If work arrives during keepalive, requests wait until keepalive finishes, then execute immediately
- Remote activity signals reset the idle timer with random jitter (±7s)
- After any request, keepalive stays off until idle threshold is reached again

### Interactive Disable/Re-enable

Run `join` with interactive mode to toggle the tunnel without killing the process. This is useful when someone needs to use the machine locally for a moment.

```bash
cyberdriver join --secret YOUR_API_KEY --keepalive --interactive
```

Commands inside the prompt:
- `d` or `disable`: Disconnects the cloud tunnel and pauses keepalive. Local server stays up.
- `e` or `enable`: Reconnects the tunnel and resumes keepalive.
- `q` or `quit`: Exits cyberdriver.
- `h` or `help`: Show commands.

### Remote Keepalive

When automating a VM through remote desktop (RDP, Avatara, AnyDesk, etc.), the VM often locks or shuts off after inactivity. Because this is enforced by the remote desktop software, running keepalive inside the VM may not help. Remote Keepalive runs a second Cyberdriver on the host (where the remote desktop software runs) to keep the VM session alive while your main Cyberdriver inside the VM is idle. This helps you avoid redoing 2FA every time you kick off a workflow.

Remote (host-level) keepalive registers itself to a main machine ID at join time:

```bash
cyberdriver join --secret YOUR_API_KEY --keepalive --register-as-keepalive-for <MAIN_MACHINE_ID>
```

Behavior:
- On connect, the host announces the link (same organization required; self-links rejected).
- The host Cyberdriver won’t interfere while a workflow runs on the VM; it only runs keepalive when the VM has been idle beyond your configured threshold.
 - If a keepalive action is mid-run when work arrives on the VM, Cyberdriver completes that action first to avoid disruptive UI state, then proceeds with the workflow.
 - The host’s remote activity signals reset the VM’s idle timer with a small random jitter (±7s) around the threshold.
- If the host disconnects, the link is cleared automatically; when it reconnects, the link is re-established.

## Utilities

### Coordinate Capture

Find screen coordinates for keepalive configuration:

```bash
cyberdriver coords
```

This starts an interactive tool that captures coordinates when you right-click. Right-click anywhere on your screen:

```
Right-click anywhere to capture coordinates. Press Esc to exit.

✓ Click captured: X=10, Y=1070

Use with keepalive:
  cyberdriver join --secret YOUR_KEY --keepalive \
    --keepalive-click-x 10 --keepalive-click-y 1070
```

Press Esc when done. You can right-click multiple times to try different locations. Regular left-clicks work normally and won't be captured. On trackpad, use two-finger click/tap for right-click.

## Configuration

Configuration is stored in:
- Windows: `%LOCALAPPDATA%\.cyberdriver\config.json`
- Linux/macOS: `~/.config/.cyberdriver/config.json`

The config file contains:
```json
{
  "version": "version-number-string",
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
