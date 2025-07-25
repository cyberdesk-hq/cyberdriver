#!/usr/bin/env python3
"""
Test if the mouse movement fix works
"""

import time
import requests

BASE_URL = "http://localhost:3000"

print("Testing Mouse Movement Fix")
print("=" * 50)

# Check PyAutoGUI settings
print("\n1. Checking PyAutoGUI settings in server:")
response = requests.get(f"{BASE_URL}/debug/pyautogui")
settings = response.json()
for key, value in settings.items():
    print(f"   {key}: {value}")

# Test mouse movement speed
print("\n2. Testing mouse movement speed:")
positions = [(400, 300), (800, 300), (600, 500)]

times = []
for x, y in positions:
    start = time.time()
    response = requests.post(f"{BASE_URL}/computer/input/mouse/move",
                           json={"x": x, "y": y})
    elapsed = time.time() - start
    times.append(elapsed)
    print(f"   Move to ({x}, {y}): {elapsed*1000:.1f}ms")

avg = sum(times) / len(times)
print(f"\nAverage: {avg*1000:.1f}ms")
print(f"Expected: ~100-250ms")
print(f"Status: {'✅ FIXED!' if avg < 0.5 else '❌ Still slow'}") 