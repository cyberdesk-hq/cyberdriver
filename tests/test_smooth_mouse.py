#!/usr/bin/env python3
"""
Test smooth mouse movement improvements
"""

import time
import requests

BASE_URL = "http://localhost:3000"

def test_mouse_movement():
    print("Testing improved mouse movement...")
    print("=" * 50)
    
    # Get current position
    response = requests.get(f"{BASE_URL}/computer/input/mouse/position")
    start_pos = response.json()
    print(f"Starting position: ({start_pos['x']}, {start_pos['y']})")
    
    # Define test positions in a square pattern
    positions = [
        (500, 300),
        (800, 300),
        (800, 600),
        (500, 600),
        (500, 300)  # Back to start
    ]
    
    print("\nMoving mouse in square pattern...")
    start_time = time.time()
    
    for x, y in positions:
        print(f"  Moving to ({x}, {y})...", end="", flush=True)
        move_start = time.time()
        response = requests.post(f"{BASE_URL}/computer/input/mouse/move",
                               json={"x": x, "y": y})
        move_time = time.time() - move_start
        print(f" done in {move_time:.3f}s")
        time.sleep(0.2)  # Brief pause to see the movement
    
    total_time = time.time() - start_time
    print(f"\nTotal time for pattern: {total_time:.2f}s")
    
    print("\n" + "=" * 70)
    print("COMPARISON: Windows vs macOS Implementation")
    print("=" * 70)
    print("""
Windows:
- Uses SendInput API with MOUSEEVENTF_ABSOLUTE flag
- Hardware-level input events processed by Windows
- 20 steps with 5ms sleep = 100ms per movement
- Smooth interpolation handled by the OS

Cyberdriver (macOS):
- Uses PyAutoGUI which wraps CGEventCreateMouseEvent
- Software-level events through Quartz Core Graphics
- PyAutoGUI's built-in duration parameter for interpolation
- Less smooth than Windows due to API differences

Key Limitations on macOS:
1. No direct equivalent to Windows' SendInput API
2. CGEventCreateMouseEvent doesn't provide the same hardware-level smoothing
3. PyAutoGUI adds some overhead for cross-platform compatibility
4. The "Rocket" icon appears as PyAutoGUI creates an app context

For the smoothest experience on macOS:
- We now use PyAutoGUI's built-in duration (0.1s default)
- Disabled PyAutoGUI's pause between commands (PAUSE = 0)
- This is as smooth as we can get with Python on macOS

Note: For truly smooth mouse movement on macOS like Windows,
you'd need to write native Objective-C/Swift code using IOHIDPostEvent
or similar low-level APIs.
""")


if __name__ == "__main__":
    print("Make sure the server is running: python cyberdriver.py start --port 3000")
    print()
    
    try:
        # Test connection
        response = requests.get(f"{BASE_URL}/computer/display/dimensions")
        response.raise_for_status()
        test_mouse_movement()
    except Exception as e:
        print(f"Error: {e}")
        print("Is the server running?") 