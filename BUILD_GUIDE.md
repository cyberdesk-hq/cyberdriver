# Building Cyberdriver as a Single Executable

This guide walks you through creating a standalone executable from Cyberdriver.

## Prerequisites

- Python 3.9 or later
- All dependencies installed (`pip install -r requirements.txt`)
- PyInstaller (`pip install pyinstaller`)

## Quick Build

### Option 1: Using PyInstaller directly

The simplest approach:

```bash
# Install PyInstaller
pip install pyinstaller

# Build the executable
pyinstaller --onefile --name cyberdriver cyberdriver.py
```

This creates:
- `dist/cyberdriver` - Your executable
- `build/` - Temporary build files
- `cyberdriver.spec` - Build specification

### Option 2: Using a spec file (Recommended)

For more control and consistent builds:

```bash
pyinstaller \
    --onefile \
    --name cyberdriver \
    --console \
    --clean \
    --noconfirm \
    --hidden-import uvicorn.logging \
    --hidden-import uvicorn.loops \
    --hidden-import uvicorn.loops.auto \
    --hidden-import uvicorn.protocols \
    --hidden-import uvicorn.protocols.http \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.websockets \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.lifespan \
    --hidden-import uvicorn.lifespan.on \
    --hidden-import PIL._tkinter_finder \
    --hidden-import websockets.legacy \
    --hidden-import websockets.legacy.client \
    --collect-all fastapi \
    --collect-all uvicorn \
    --collect-all mss \
    cyberdriver.py
```

## Running the Executable

### macOS/Linux
```bash
# Make it executable
chmod +x dist/cyberdriver

# Run it
./dist/cyberdriver start --port 3000
# or
./dist/cyberdriver join --secret YOUR_API_KEY
```

### Windows
```cmd
dist\cyberdriver.exe start --port 3000
```

## Platform-Specific Notes

### macOS

1. **Code signing issues**: If you get "unidentified developer" warnings:
   ```bash
   # Remove quarantine attribute
   xattr -d com.apple.quarantine dist/cyberdriver
   ```

2. **App Bundle**: To create a proper macOS app:
   ```bash
   pyinstaller \
       --onefile \
       --windowed \
       --name cyberdriver \
       --osx-bundle-identifier com.cyberdesk.cyberdriver \
       --collect-all fastapi \
       --collect-all uvicorn \
       cyberdriver.py
   ```

3. **Universal Binary**: For Apple Silicon + Intel:
   ```bash
   pyinstaller \
       --onefile \
       --name cyberdriver \
       --icon=cyberdriver.icns \
       cyberdriver.py
   ```

### Windows

1. **Windows Defender**: May flag the executable. Add an exclusion or sign the code.

2. **Hidden Console**: To hide the console window:
   ```bash
   pyinstaller \
       --onefile \
       --windowed \
       --name cyberdriver \
       --icon=cyberdriver.ico \
       cyberdriver.py
   ```

### Linux

1. **AppImage**: Consider creating an AppImage for better portability:
   ```bash
   # Build normally first
   pyinstaller --onefile cyberdriver.py
   
   # Then package as AppImage
   # (requires additional tools)
   ```

2. **Permissions**: Ensure the executable has proper permissions:
   ```bash
   chmod +x dist/cyberdriver
   ```

## Optimization

### Reduce File Size

1. **Use UPX** (optional):
   ```bash
   # Install UPX first
   brew install upx  # macOS
   # or download from https://upx.github.io/
   
   # Compress the executable
   upx --best dist/cyberdriver
   ```

2. **Strip debug symbols** (Linux/macOS):
   ```bash
   strip dist/cyberdriver
   ```

## Troubleshooting

1. **Import errors**: Add missing modules with `--hidden-import`
2. **Large file size**: Use `--exclude-module` to remove unused packages
3. **Slow startup**: Consider using `--onedir` instead of `--onefile`
4. **Missing dependencies**: Check with `pyinstaller --debug=imports` 