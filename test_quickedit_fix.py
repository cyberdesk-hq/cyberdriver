#!/usr/bin/env python3
"""
Test to verify the Windows QuickEdit mode fix is working.

Run this AFTER starting cyberdriver to test if responses work properly
even when the PowerShell window has focus.
"""

import time
import requests

BASE_URL = "http://localhost:3000"

print("Windows QuickEdit Mode Fix Test")
print("=" * 50)
print("Instructions:")
print("1. Make sure cyberdriver is running")
print("2. Click INTO the PowerShell window where cyberdriver is running")
print("3. This test will make requests - they should NOT hang")
print("=" * 50)
print("\nStarting tests in 3 seconds...")
print("CLICK INTO THE POWERSHELL WINDOW NOW!")
time.sleep(3)

# Test multiple rapid requests
print("\nTesting 5 rapid requests with PowerShell focused...")
for i in range(5):
    start = time.time()
    try:
        response = requests.get(f"{BASE_URL}/computer/input/mouse/position", timeout=2)
        elapsed = time.time() - start
        print(f"Request {i+1}: {elapsed*1000:.1f}ms ✓")
    except requests.exceptions.Timeout:
        print(f"Request {i+1}: TIMEOUT! ✗ (QuickEdit mode is still active)")
        print("\nThe fix didn't work. PowerShell is still blocking output.")
        exit(1)

print("\n" + "=" * 50)
print("SUCCESS! All requests completed quickly.")
print("The QuickEdit mode fix is working properly!")
print("=" * 50) 