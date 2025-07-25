#!/usr/bin/env python3
"""
Benchmark mouse movement speed
"""

import time
import requests

BASE_URL = "http://localhost:3000"

print("Mouse Movement Speed Benchmark")
print("=" * 50)
print("Expected: ~100ms per movement")
print("Previous: ~2500ms per movement (PyAutoGUI duration bug)")
print()

# Test positions
positions = [
    (400, 300),
    (800, 300),
    (800, 700),
    (400, 700),
    (600, 500)  # Center
]

try:
    # Warm up
    requests.post(f"{BASE_URL}/computer/input/mouse/move", json={"x": 600, "y": 500})
    
    print("Testing mouse movement speed:")
    print("-" * 30)
    
    times = []
    for i, (x, y) in enumerate(positions):
        start = time.time()
        response = requests.post(f"{BASE_URL}/computer/input/mouse/move",
                               json={"x": x, "y": y})
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"Move {i+1} to ({x}, {y}): {elapsed*1000:.1f}ms")
    
    avg_time = sum(times) / len(times)
    print("-" * 30)
    print(f"Average: {avg_time*1000:.1f}ms per movement")
    print(f"Speed improvement: {2500/avg_time/1000:.1f}x faster!")
    
except Exception as e:
    print(f"Error: {e}")
    print("Make sure server is running with the updated code!") 