#!/usr/bin/env python3
"""
Build script for creating a standalone Cyberdriver executable
"""

import os
import sys
import subprocess
import shutil

def main():
    print("Cyberdriver Executable Builder")
    print("=" * 50)
    
    # Check if PyInstaller is installed
    try:
        import PyInstaller
        print("✅ PyInstaller is installed")
    except ImportError:
        print("❌ PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✅ PyInstaller installed")
    
    # Clean previous builds
    for path in ["build", "dist", "cyberdriver.spec"]:
        if os.path.exists(path):
            print(f"Cleaning {path}...")
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
    
    # PyInstaller command
    cmd = [
        "pyinstaller",
        "--onefile",  # Single executable
        "--name", "cyberdriver",  # Executable name
        "--console",  # Console app (not windowed)
        "--clean",  # Clean temp files
        "--noconfirm",  # Overwrite without asking
        
        # Include all necessary hidden imports
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "websockets.legacy",
        "--hidden-import", "websockets.legacy.client",
        "--hidden-import", "websockets.client",
        
        # Collect all data from packages
        "--collect-all", "fastapi",
        "--collect-all", "uvicorn",
        "--collect-all", "mss",
        
        # macOS specific - include required frameworks
        "--add-binary", "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics:CoreGraphics" if sys.platform == "darwin" else "",
        
        # The main script
        "cyberdriver.py"
    ]
    
    # Remove empty strings from command
    cmd = [arg for arg in cmd if arg]
    
    print("\nBuilding executable...")
    print(f"Command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n✅ Build successful!")
        
        # Check the output
        if os.path.exists("dist/cyberdriver"):
            size = os.path.getsize("dist/cyberdriver") / (1024 * 1024)
            print(f"\nExecutable created: dist/cyberdriver")
            print(f"Size: {size:.1f} MB")
            print("\nTo run: ./dist/cyberdriver start --port 3000")
        else:
            print("\n❌ Executable not found in dist/")
            
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed: {e}")
        print("\nTroubleshooting tips:")
        print("1. Make sure all dependencies are installed")
        print("2. Try running in a clean virtual environment")
        print("3. Check for any import errors in cyberdriver.py")


if __name__ == "__main__":
    main() 