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
    
    # Check if spec file exists
    if not os.path.exists("cyberdriver.spec"):
        print("❌ cyberdriver.spec not found!")
        print("Please ensure cyberdriver.spec exists in the current directory")
        return 1
    
    # Clean previous builds
    for path in ["build", "dist"]:
        if os.path.exists(path):
            print(f"Cleaning {path}...")
            shutil.rmtree(path)
    
    # Build using the spec file
    cmd = [
        "pyinstaller",
        "--clean",  # Clean temp files
        "--noconfirm",  # Overwrite without asking
        "cyberdriver.spec"
    ]
    
    print("\nBuilding executable from cyberdriver.spec...")
    print(f"Command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n✅ Build successful!")
        
        # Check the output
        executable_path = "dist/cyberdriver"
        if sys.platform == "win32":
            executable_path += ".exe"
            
        if os.path.exists(executable_path):
            size = os.path.getsize(executable_path) / (1024 * 1024)
            print(f"\nExecutable created: {executable_path}")
            print(f"Size: {size:.1f} MB")
            print("\nTo run: ./dist/cyberdriver start --port 3000")
        else:
            print(f"\n❌ Executable not found at {executable_path}")
            
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed: {e}")
        print("\nTroubleshooting tips:")
        print("1. Check cyberdriver.spec for configuration errors")
        print("2. Make sure all dependencies are installed")
        print("3. Try running: pyinstaller cyberdriver.spec --debug all")
        print("4. Check for any import errors in cyberdriver.py")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 