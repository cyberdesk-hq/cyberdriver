# Cyberdriver

A comprehensive remote computer control tool with all major features for remote desktop automation and control.

## Features

### Complete Feature Set
- ✅ **HTTP API Server** - Local control server used behind the authenticated Cyberdesk tunnel
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

#### Protected Operational Routes
- File system endpoints (`/computer/fs/*`) are available through authenticated tunnel requests only
- PowerShell endpoints (`/computer/shell/powershell/*`) are available through authenticated tunnel requests only
- Internal operational routes (`/internal/*`) are tunnel-only

## Installation

### Windows PowerShell Installation

The below PowerShell script will install Cyberdriver onto your Windows machine, and add the cyberdriver executable to your PATH.

```powershell
# Create tool directory
$toolDir = "$env:USERPROFILE\.cyberdriver"
New-Item -ItemType Directory -Force -Path $toolDir

# Download cyberdriver
try {
    Invoke-WebRequest -Uri "https://github.com/cyberdesk-hq/cyberdriver/releases/download/v0.0.42/cyberdriver.exe" -OutFile "$toolDir\cyberdriver.exe" -ErrorAction Stop
} catch {
    Write-Host "ERROR: Failed to download Cyberdriver. If Cyberdriver is already running, run 'cyberdriver stop' first. Otherwise, check your internet connection and try again." -ForegroundColor Red
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
VERSION=0.0.42
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

### Linux Installation (Bash)

Cyberdriver ships standalone binaries for `linux-amd64` and `linux-arm64`. The
target environment is a desktop session backed by an X11 display - that means
Xvfb (or Xorg) must be running and the `DISPLAY` env var must be set before you
start `cyberdriver join`. Wayland is not supported.

```bash
# 1. Install runtime system deps (Debian / Ubuntu).
#    - xvfb + a window manager (xfce4 or icewm) provide the X session
#    - scrot / xdotool back PyAutoGUI's screenshot + input on X11
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl xvfb xfce4 chromium-browser scrot xdotool python3-tk

# 2. Download the matching binary (auto-detects amd64 vs arm64).
TOOL_DIR="$HOME/.cyberdriver"
mkdir -p "$TOOL_DIR"
case "$(uname -m)" in
  x86_64)  ARCH=amd64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo "Unsupported architecture: $(uname -m)"; exit 1 ;;
esac
curl -fsSL -o "$TOOL_DIR/cyberdriver" \
  "https://github.com/cyberdesk-hq/cyberdriver/releases/latest/download/cyberdriver-linux-${ARCH}"
chmod +x "$TOOL_DIR/cyberdriver"
export PATH="$TOOL_DIR:$PATH"

# 3. Start an X session (skip if you already have one).
Xvfb :99 -screen 0 1280x720x24 &
sleep 1
DISPLAY=:99 startxfce4 &
sleep 2

# 4. Connect.
DISPLAY=:99 cyberdriver join --secret YOUR_API_KEY
```

**Headless container example.** Useful for Cursor Cloud sandboxes / CI / Docker:

```dockerfile
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:99
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xvfb xfce4 chromium-browser scrot xdotool \
      python3 python3-tk \
    && rm -rf /var/lib/apt/lists/*
ARG TARGETARCH=amd64
RUN curl -fsSL -o /usr/local/bin/cyberdriver \
      "https://github.com/cyberdesk-hq/cyberdriver/releases/latest/download/cyberdriver-linux-${TARGETARCH}" \
    && chmod +x /usr/local/bin/cyberdriver
CMD Xvfb :99 -screen 0 1280x720x24 & \
    sleep 2 && startxfce4 & \
    sleep 2 && cyberdriver join --secret "$SECRET" --host https://api.cyberdesk.io
```

**Notes (Linux):**
- The screenshot, mouse, and keyboard endpoints all go through the X server, so anything you can `xdotool` / `scrot` against will work. Wayland sessions are not supported - use Xvfb or Xorg.
- The Windows-specific features (background detach, persistent display via Amyuni, console close-button protection) are no-ops on Linux. If you need to keep `cyberdriver join` running across SSH disconnects, wrap it in `nohup`, `tmux`, `systemd`, or your container's entrypoint.
- The `/computer/shell/*` endpoints execute via `pwsh` (PowerShell Core). Install it with `sudo apt-get install -y powershell` if you need them; otherwise they'll error out, which is fine for the typical screenshot/mouse/keyboard workflow.

**Note (Windows):** Cyberdriver automatically disables PowerShell's QuickEdit Mode on startup. PowerShell has this dumb quirk where focusing your mouse on a running executable can stall the outputs until you unfocus it (it's called "QuickEdit Mode"). 

**Important - Admin Privileges:** If the desktop application you want to automate requires administrator privileges to start (such as many legacy enterprise applications), you must also run cyberdriver from an Administrator PowerShell terminal:

1. Right-click on PowerShell and select "Run as Administrator"
2. Navigate to your desired directory
3. Run `cyberdriver join --secret YOUR_KEY`

This ensures cyberdriver has the necessary permissions to interact with elevated applications. If you're only automating regular user-level applications, you can run cyberdriver normally without admin privileges.

Cyberdriver is started by connecting it to Cyberdesk:

```bash
cyberdriver join --secret SK-YOUR-SECRET-KEY
```

The legacy standalone `cyberdriver start` mode has been removed. Privileged local routes are only accessible through the authenticated `cyberdriver join` tunnel path.

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

### Via Cyberdesk API

Trigger self-update through an authenticated Cyberdesk workflow or API call:

```bash
curl -X POST "https://api.cyberdesk.io/v1/computer/{machine_id}/internal/update" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"version": "latest", "restart": true}'
```

Direct localhost calls to `/internal/update` are intentionally blocked. The route is available only through the authenticated tunnel path.

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

Cyberdriver uses your system's certificate store by default, which works automatically on most machines including corporate networks with SSL inspection (Zscaler, Palo Alto, Fortinet, etc.).

As a fallback, Cyberdriver bundles the `certifi` package which contains up-to-date root CA certificates including Let's Encrypt's ISRG Root X1. This ensures connectivity even on Windows machines missing root certificates.

If you're still experiencing TLS errors, please reach out to the team for assistance.




### Basic Installation from Source

```bash
pip install -r requirements.txt
```

## Usage

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
2. **Filesystem/Shell endpoints** - Protected behind the authenticated tunnel path
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
