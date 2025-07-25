#!/usr/bin/env python3
"""
Test if pyautogui.PAUSE is the culprit
"""

import time
import pyautogui

print("Testing PyAutoGUI PAUSE effect")
print("=" * 50)

# Test with different PAUSE values
pause_values = [0.25, 0.1, 0.01, 0]

for pause in pause_values:
    pyautogui.PAUSE = pause
    print(f"\nTesting with PAUSE = {pause}:")
    
    # Time a simple moveTo
    start = time.time()
    pyautogui.moveTo(500, 500, duration=0)
    elapsed = time.time() - start
    print(f"  Single moveTo: {elapsed*1000:.1f}ms")
    
    # Time 10 moves (like our smooth move)
    start = time.time()
    for i in range(10):
        pyautogui.moveTo(500 + i, 500, duration=0, _pause=False)
    elapsed = time.time() - start
    print(f"  10 moves with _pause=False: {elapsed*1000:.1f}ms")
    
    # Time 10 moves without _pause=False
    start = time.time()
    for i in range(10):
        pyautogui.moveTo(500 + i, 500, duration=0)
    elapsed = time.time() - start
    print(f"  10 moves without _pause=False: {elapsed*1000:.1f}ms")

print("\n" + "=" * 50)
print("FINDING:")
print("If PAUSE=0.25 gives us ~2500ms for 10 moves, that's our issue!")
print("The server might have PAUSE=0.25 instead of PAUSE=0") 