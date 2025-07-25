#!/usr/bin/env python3
"""
Test script to demonstrate Cyberdriver enhanced features.
Run this after starting the server with: python cyberdriver.py start --port 3000
"""

import requests
import json
import time

BASE_URL = "http://localhost:3000"

def test_screenshot_scaling():
    """Test different screenshot scaling modes."""
    print("Testing screenshot scaling modes...")
    
    # Test exact scaling
    response = requests.get(f"{BASE_URL}/computer/display/screenshot", 
                          params={"width": 800, "height": 600, "mode": "exact"})
    print(f"  Exact mode (800x600): {response.status_code}, size: {len(response.content)} bytes")
    
    # Test aspect fit
    response = requests.get(f"{BASE_URL}/computer/display/screenshot", 
                          params={"width": 1024, "height": 768, "mode": "aspect_fit"})
    print(f"  Aspect fit mode: {response.status_code}, size: {len(response.content)} bytes")
    
    # Test aspect fill
    response = requests.get(f"{BASE_URL}/computer/display/screenshot", 
                          params={"width": 1920, "height": 1080, "mode": "aspect_fill"})
    print(f"  Aspect fill mode: {response.status_code}, size: {len(response.content)} bytes")


def test_xdo_keyboard():
    """Test XDO keyboard sequences."""
    print("\nTesting XDO keyboard input...")
    
    # Test simple key combo
    response = requests.post(f"{BASE_URL}/computer/input/keyboard/key",
                           json={"text": "ctrl+a"})
    print(f"  ctrl+a: {response.status_code}")
    time.sleep(0.5)
    
    # Test complex sequence
    response = requests.post(f"{BASE_URL}/computer/input/keyboard/key",
                           json={"text": "ctrl+shift+home"})
    print(f"  ctrl+shift+home: {response.status_code}")
    time.sleep(0.5)
    
    # Test multiple commands
    response = requests.post(f"{BASE_URL}/computer/input/keyboard/key",
                           json={"text": "alt+tab alt+tab"})
    print(f"  alt+tab alt+tab: {response.status_code}")


def test_mouse_features():
    """Test enhanced mouse features."""
    print("\nTesting mouse features...")
    
    # Get current position
    response = requests.get(f"{BASE_URL}/computer/input/mouse/position")
    pos = response.json()
    print(f"  Current position: {pos}")
    
    # Test smooth movement
    print("  Testing smooth movement...")
    response = requests.post(f"{BASE_URL}/computer/input/mouse/move",
                           json={"x": 500, "y": 500})
    print(f"    Move to (500,500): {response.status_code}")
    time.sleep(1)
    
    # Test mouse down/up separately
    print("  Testing separate press/release...")
    response = requests.post(f"{BASE_URL}/computer/input/mouse/click",
                           json={"button": "left", "down": True})
    print(f"    Mouse down: {response.status_code}")
    time.sleep(0.5)
    
    response = requests.post(f"{BASE_URL}/computer/input/mouse/click",
                           json={"button": "left", "down": False})
    print(f"    Mouse up: {response.status_code}")


def test_not_implemented():
    """Test that filesystem and shell endpoints return 501."""
    print("\nTesting not-implemented endpoints...")
    
    # File system
    response = requests.get(f"{BASE_URL}/computer/fs/list", params={"path": "."})
    print(f"  fs/list: {response.status_code} (expected 501)")
    
    response = requests.get(f"{BASE_URL}/computer/fs/read", params={"path": "test.txt"})
    print(f"  fs/read: {response.status_code} (expected 501)")
    
    response = requests.post(f"{BASE_URL}/computer/fs/write", 
                           json={"path": "test.txt", "content": "test"})
    print(f"  fs/write: {response.status_code} (expected 501)")
    
    # Shell
    response = requests.post(f"{BASE_URL}/computer/shell/cmd/exec",
                           json={"command": "echo test"})
    print(f"  shell/cmd/exec: {response.status_code} (expected 501)")
    
    response = requests.post(f"{BASE_URL}/computer/shell/powershell/exec",
                           json={"command": "echo test"})
    print(f"  shell/powershell/exec: {response.status_code} (expected 501)")


def main():
    """Run all tests."""
    print("Cyberdriver Feature Tests")
    print("===========================")
    
    try:
        # Test connection
        response = requests.get(f"{BASE_URL}/computer/display/dimensions")
        response.raise_for_status()
        dims = response.json()
        print(f"Connected! Screen dimensions: {dims['width']}x{dims['height']}")
    except Exception as e:
        print(f"Failed to connect to server: {e}")
        print("Make sure server is running: python cyberdriver.py start --port 3000")
        return
    
    test_screenshot_scaling()
    test_xdo_keyboard()
    test_mouse_features()
    test_not_implemented()
    
    print("\nAll tests completed!")


if __name__ == "__main__":
    main() 