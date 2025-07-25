#!/usr/bin/env python3
"""
Debug mouse movement timing
"""

import time
import pyautogui

# Match server settings
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

def debug_smooth_move(x: int, y: int, steps: int = 10, duration: float = 0.1):
    """Debug version of smooth_mouse_move with timing info"""
    print(f"\nMoving to ({x}, {y})")
    print(f"Steps: {steps}, Total duration: {duration}s")
    
    total_start = time.time()
    
    # Get position timing
    pos_start = time.time()
    current_x, current_y = pyautogui.position()
    pos_time = time.time() - pos_start
    print(f"Get position: {pos_time*1000:.1f}ms - at ({current_x}, {current_y})")
    
    # Skip if already at target
    if abs(current_x - x) < 2 and abs(current_y - y) < 2:
        print("Already at target!")
        return
    
    sleep_time = duration / steps
    print(f"Sleep per step: {sleep_time*1000:.1f}ms")
    
    # Calculate step increments
    dx = (x - current_x) / steps
    dy = (y - current_y) / steps
    
    # Perform smooth movement with instant moves
    for i in range(steps):
        step_start = time.time()
        
        new_x = current_x + dx * (i + 1)
        new_y = current_y + dy * (i + 1)
        
        move_start = time.time()
        pyautogui.moveTo(int(new_x), int(new_y), duration=0, _pause=False)
        move_time = time.time() - move_start
        
        sleep_start = time.time()
        time.sleep(sleep_time)
        actual_sleep = time.time() - sleep_start
        
        step_time = time.time() - step_start
        if i == 0:  # Only print first step to avoid spam
            print(f"Step 1: move={move_time*1000:.1f}ms, sleep={actual_sleep*1000:.1f}ms, total={step_time*1000:.1f}ms")
    
    # Final move
    final_start = time.time()
    pyautogui.moveTo(x, y, duration=0, _pause=False)
    final_time = time.time() - final_start
    
    total_time = time.time() - total_start
    print(f"Final move: {final_time*1000:.1f}ms")
    print(f"TOTAL TIME: {total_time*1000:.1f}ms")
    
    # Verify we arrived
    verify_x, verify_y = pyautogui.position()
    print(f"Arrived at: ({verify_x}, {verify_y})")


# Test the function
print("Testing smooth mouse movement...")
print("=" * 50)

# Test 1: Simple movement
debug_smooth_move(600, 400)

# Test 2: Longer movement
print("\n" + "=" * 50)
debug_smooth_move(300, 200)

# Test 3: Test if pyautogui.position() is slow
print("\n" + "=" * 50)
print("Testing pyautogui.position() speed:")
times = []
for i in range(10):
    start = time.time()
    x, y = pyautogui.position()
    elapsed = time.time() - start
    times.append(elapsed)
    if i == 0:
        print(f"First call: {elapsed*1000:.1f}ms")

avg_time = sum(times) / len(times)
print(f"Average over 10 calls: {avg_time*1000:.1f}ms") 