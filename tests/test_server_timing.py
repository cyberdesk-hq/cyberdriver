#!/usr/bin/env python3
"""
Test to isolate the 2.5 second delay issue
"""

import time
import requests
import asyncio
import pyautogui

BASE_URL = "http://localhost:3000"

print("Server Timing Investigation")
print("=" * 50)

# Test 1: Direct function call vs HTTP request
print("\n1. Testing GET request (no mouse movement):")
start = time.time()
response = requests.get(f"{BASE_URL}/computer/display/dimensions")
elapsed = time.time() - start
print(f"   GET /dimensions: {elapsed*1000:.1f}ms")

# Test 2: Mouse position (no movement)
print("\n2. Testing mouse position (no movement):")
start = time.time()
response = requests.get(f"{BASE_URL}/computer/input/mouse/position")
elapsed = time.time() - start
print(f"   GET /mouse/position: {elapsed*1000:.1f}ms")

# Test 3: Very small mouse movement
print("\n3. Testing tiny mouse movement (1 pixel):")
pos = response.json()
start = time.time()
response = requests.post(f"{BASE_URL}/computer/input/mouse/move",
                       json={"x": pos['x'] + 1, "y": pos['y']})
elapsed = time.time() - start
print(f"   POST /mouse/move (+1 pixel): {elapsed*1000:.1f}ms")

# Test 4: Type text (no mouse involved)
print("\n4. Testing keyboard type:")
start = time.time()
response = requests.post(f"{BASE_URL}/computer/input/keyboard/type",
                       json={"text": "a"})
elapsed = time.time() - start
print(f"   POST /keyboard/type: {elapsed*1000:.1f}ms")

# Test 5: Check if it's pyautogui.position() causing delay
print("\n5. Testing pyautogui.position() directly:")
pyautogui.PAUSE = 0
times = []
for i in range(5):
    start = time.time()
    x, y = pyautogui.position()
    elapsed = time.time() - start
    times.append(elapsed)
avg = sum(times) / len(times)
print(f"   pyautogui.position() average: {avg*1000:.1f}ms")

# Test 6: Check if it's the async context
print("\n6. Testing if async is the issue:")

async def test_async_delay():
    start = time.time()
    await asyncio.sleep(0)  # Yield control
    elapsed = time.time() - start
    return elapsed

loop_time = asyncio.run(test_async_delay())
print(f"   Async context switch: {loop_time*1000:.1f}ms")

print("\n" + "=" * 50)
print("DIAGNOSIS:")
print("If all requests take ~2500ms, it's likely a server-side issue.")
print("If only mouse movement takes 2500ms, it's the movement function.")
print("Check if the server has some kind of rate limiting or sleep.") 