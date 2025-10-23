# Amyuni Virtual Display Driver Setup

The `--add-persistent-display` flag in cyberdriver requires the Amyuni USB Mobile Monitor driver files to be present. This creates a virtual display that persists even when RDP disconnects, allowing cyberdriver to continue functioning.

## Required Files

Download the Amyuni driver from: https://www.amyuni.com/downloads/usbmmidd_v2.zip

After downloading, extract the ZIP file and you'll get several files. You need to place them in a specific location for cyberdriver to find them.

## Setup Instructions

### For Development (Running as Python Script)

1. Create an `amyuni_driver` folder in the same directory as `cyberdriver.py`:
   ```bash
   mkdir amyuni_driver
   ```

2. Extract the contents of `usbmmidd_v2.zip` into the `amyuni_driver` folder. The folder should contain:
   - `deviceinstaller.exe` (for 32-bit Windows)
   - `deviceinstaller64.exe` (for 64-bit Windows)
   - `usbmmidd.inf`
   - `usbmmidd.sys`
   - Other supporting files from the ZIP

3. Your directory structure should look like:
   ```
   cyberdriver/
   ├── cyberdriver.py
   ├── amyuni_driver/
   │   ├── deviceinstaller.exe
   │   ├── deviceinstaller64.exe
   │   ├── usbmmidd.inf
   │   ├── usbmmidd.sys
   │   └── ... (other files from zip)
   └── ...
   ```

### For Production (PyInstaller Executable)

When building the cyberdriver executable with PyInstaller, you need to include the `amyuni_driver` folder using the `--add-data` flag:

```bash
pyinstaller --onefile \
  --name cyberdriver \
  --add-data "amyuni_driver;amyuni_driver" \
  cyberdriver.py
```

Or in your `.spec` file:
```python
datas=[
    ('amyuni_driver', 'amyuni_driver'),
],
```

## Usage

Once the driver files are in place, you can use the `--add-persistent-display` flag:

```bash
# Basic usage
cyberdriver join --secret YOUR_API_KEY --add-persistent-display

# Combined with other features
cyberdriver join --secret YOUR_API_KEY \
  --add-persistent-display \
  --black-screen-recovery \
  --keepalive
```

## What Happens

When you run cyberdriver with `--add-persistent-display`:

1. **Admin Check**: Cyberdriver requests administrator privileges (UAC prompt will appear)

2. **Driver Detection**: Checks if the Amyuni driver is already installed

3. **Installation** (if needed):
   - Runs `deviceinstaller64 install usbmmidd.inf usbmmidd`
   - Runs `deviceinstaller64 enableidd 1`
   - The virtual display is now active

4. **Verification** (if already installed):
   - Ensures the virtual display is enabled
   - Runs `deviceinstaller64 enableidd 1` to make sure it's active

5. **Persistence**: The virtual display will persist across:
   - Cyberdriver restarts
   - System reboots
   - RDP disconnections
   - The display remains even when cyberdriver exits (by design)

## Configuring the Virtual Display

After installation, you can configure the virtual display like any other monitor:

1. Open Windows **Display Settings** (right-click desktop → Display settings)
2. You'll see a new monitor listed (usually as "USB Mobile Monitor Virtual Display")
3. You can:
   - Set the resolution (recommended: 1024×768 for automation)
   - Extend or duplicate your desktop
   - Make it the primary display if desired

## Manual Removal (Optional)

If you want to remove the virtual display:

```bash
# Disable the display (keeps driver installed)
deviceinstaller64 enableidd 0

# Completely uninstall the driver
deviceinstaller64 remove usbmmidd
```

## Benefits with Black Screen Recovery

When combined with `--black-screen-recovery`, you get the ultimate RDP-resistant setup:

```bash
cyberdriver join --secret YOUR_API_KEY \
  --add-persistent-display \
  --black-screen-recovery
```

- **Virtual Display**: Provides a persistent display surface that survives RDP disconnection
- **Black Screen Recovery**: Automatically switches the session to console if the screen goes black
- **Result**: Cyberdriver continues working even when you disconnect RDP

## Troubleshooting

### "Driver files not found" error
- Ensure the `amyuni_driver` folder exists in the same directory as cyberdriver
- Check that all required files are present (especially `deviceinstaller64.exe` and `usbmmidd.inf`)

### "Administrator privileges required" error
- Accept the UAC prompt when it appears
- Or run cyberdriver from an Administrator PowerShell

### Virtual display not showing in Display Settings
- Try restarting Windows
- Run `deviceinstaller64 enableidd 1` manually in the `amyuni_driver` folder
- Check Device Manager for the "USB Mobile Monitor Virtual Display" under "Monitors"

### Installation fails
- Ensure you're running as Administrator
- Check that Windows hasn't blocked the executables (right-click → Properties → Unblock)
- Try extracting the ZIP again in case files were corrupted

## License Note

The Amyuni USB Mobile Monitor driver is provided by Amyuni Technologies. Please review their licensing terms at https://www.amyuni.com/ before redistributing or using in commercial applications.

