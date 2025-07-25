#!/usr/bin/env python3
"""
Test PyAutoGUI timing to understand why movements are so slow
"""

import time
import pyautogui

# Disable pause
pyautogui.PAUSE = 0

print("PyAutoGUI Timing Tests")
print("=" * 50)
print(f"PAUSE setting: {pyautogui.PAUSE}")
print(f"FAILSAFE setting: {pyautogui.FAILSAFE}")
print(f"MINIMUM_DURATION: {pyautogui.MINIMUM_DURATION}")
print(f"MINIMUM_SLEEP: {pyautogui.MINIMUM_SLEEP}")

# Test different duration values
durations = [0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0]

print("\nTesting pyautogui.moveTo() with different durations:")
print("-" * 50)

start_x, start_y = 500, 500
pyautogui.moveTo(start_x, start_y, duration=0)  # Start position

for duration in durations:
    # Move right 200 pixels
    target_x = start_x + 200
    
    start_time = time.time()
    pyautogui.moveTo(target_x, start_y, duration=duration)
    actual_time = time.time() - start_time
    
    print(f"Duration={duration}s -> Actual time: {actual_time:.3f}s")
    
    # Move back
    pyautogui.moveTo(start_x, start_y, duration=0)
    time.sleep(0.1)

print("\nTesting instant movements (duration=0, _pause=False):")
print("-" * 50)

positions = [(600, 400), (700, 400), (700, 500), (600, 500), (600, 400)]
start_time = time.time()

for x, y in positions:
    move_start = time.time()
    pyautogui.moveTo(x, y, duration=0, _pause=False)
    move_time = time.time() - move_start
    print(f"  Move to ({x}, {y}): {move_time:.4f}s")

total_time = time.time() - start_time
print(f"Total time for 5 instant moves: {total_time:.3f}s")

print("\nConclusion:")
print("-" * 50)
print("If duration > 0, PyAutoGUI uses its own interpolation")
print("On macOS, this seems to have a minimum time of ~2.5s")
print("Solution: Use duration=0 and implement our own interpolation") 