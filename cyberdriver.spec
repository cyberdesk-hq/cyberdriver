# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all data/binaries from important packages
datas = []
binaries = []
hiddenimports = []

for package in ['fastapi', 'uvicorn', 'mss']:
    tmp_datas, tmp_binaries, tmp_hiddens = collect_all(package)
    datas += tmp_datas
    binaries += tmp_binaries
    hiddenimports += tmp_hiddens

# Bundle Amyuni virtual display driver files (Windows only, but harmless on other platforms)
if os.path.exists('amyuni_driver'):
    datas.append(('amyuni_driver', 'amyuni_driver'))

# Add macOS-specific CoreGraphics framework if on Darwin
if sys.platform == "darwin":
    binaries.append(('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics', 'CoreGraphics'))

# Analysis
a = Analysis(
    ['cyberdriver.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.config',  # Explicitly include Config class
        'uvicorn.server',  # Explicitly include Server class
        'PIL._tkinter_finder',
        'websockets.legacy',
        'websockets.legacy.client',
        'websockets.client',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# PYZ
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# EXE
_console = True
if sys.platform == "win32":
    # Default Windows builds to "windowed" (no console) to avoid an AI agent
    # accidentally terminating Cyberdriver by closing/Alt+F4'ing the console.
    # Set CYBERDRIVER_CONSOLE=1 to build a console binary for debugging.
    _console = os.environ.get("CYBERDRIVER_CONSOLE", "").lower() in ("1", "true", "yes")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='cyberdriver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_console,
    disable_windowed_traceback=False,
    target_arch=(os.environ.get('TARGET_ARCH') if sys.platform == 'darwin' else None),
    codesign_identity=(os.environ.get('CODESIGN_IDENTITY') if sys.platform == 'darwin' else None),
    entitlements_file=(os.environ.get('ENTITLEMENTS_FILE') if sys.platform == 'darwin' else None),
    icon=None,  # Add icon path here if you have one
    version_file=None,  # Add version info file if needed
    uac_admin=False  # Don't request administrator privileges on Windows
)
