#!/usr/bin/env python3
"""
Test script to diagnose and verify the buffering issue fix in cyberdriver.

This script tests:
1. Direct API calls to the local server
2. Multiple rapid requests
3. Response timing
"""

import asyncio
import time
import httpx
import sys

BASE_URL = "http://localhost:3000"

async def test_single_request():
    """Test a single request to see if it returns immediately."""
    print("Testing single request...")
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        start = time.time()
        try:
            response = await client.get(f"{BASE_URL}/computer/input/mouse/position")
            elapsed = time.time() - start
            print(f"✓ Single request completed in {elapsed*1000:.1f}ms")
            print(f"  Response: {response.json()}")
        except httpx.TimeoutException:
            print(f"✗ Request timed out after 5 seconds!")
            return False
    return True


async def test_multiple_requests():
    """Test multiple rapid requests to see if they buffer."""
    print("\nTesting multiple rapid requests...")
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = []
        
        for i in range(5):
            start = time.time()
            try:
                response = await client.get(f"{BASE_URL}/computer/input/mouse/position")
                elapsed = time.time() - start
                results.append(elapsed)
                print(f"  Request {i+1}: {elapsed*1000:.1f}ms")
            except httpx.TimeoutException:
                print(f"✗ Request {i+1} timed out!")
                return False
        
        avg = sum(results) / len(results)
        print(f"✓ Average response time: {avg*1000:.1f}ms")
    return True


async def test_concurrent_requests():
    """Test concurrent requests to check for blocking."""
    print("\nTesting concurrent requests...")
    
    async def make_request(client, num):
        start = time.time()
        try:
            response = await client.get(f"{BASE_URL}/computer/input/mouse/position")
            elapsed = time.time() - start
            return num, elapsed, True
        except httpx.TimeoutException:
            return num, 5.0, False
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Launch 3 concurrent requests
        tasks = [make_request(client, i) for i in range(3)]
        results = await asyncio.gather(*tasks)
        
        for num, elapsed, success in results:
            if success:
                print(f"  Request {num+1}: {elapsed*1000:.1f}ms ✓")
            else:
                print(f"  Request {num+1}: TIMEOUT ✗")
        
        # Check if any request was blocked
        times = [r[1] for r in results if r[2]]
        if times:
            max_time = max(times)
            if max_time > 1.0:
                print(f"⚠️  Longest request took {max_time*1000:.1f}ms - possible blocking")
                return False
            else:
                print(f"✓ All requests completed quickly (max: {max_time*1000:.1f}ms)")
                return True
    return False


async def test_action_requests():
    """Test requests that trigger actions (mouse movement)."""
    print("\nTesting action requests (mouse movement)...")
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Get current position
        pos_response = await client.get(f"{BASE_URL}/computer/input/mouse/position")
        pos = pos_response.json()
        
        # Move mouse slightly
        start = time.time()
        try:
            response = await client.post(
                f"{BASE_URL}/computer/input/mouse/move",
                json={"x": pos["x"] + 10, "y": pos["y"] + 10}
            )
            elapsed = time.time() - start
            print(f"✓ Mouse move completed in {elapsed*1000:.1f}ms")
            
            if elapsed > 500:
                print(f"⚠️  Movement took longer than expected")
        except httpx.TimeoutException:
            print(f"✗ Mouse move request timed out!")
            return False
    return True


async def main():
    print("=" * 60)
    print("Cyberdriver Buffering Test")
    print("=" * 60)
    print(f"Testing server at: {BASE_URL}")
    print("Make sure cyberdriver is running with: python cyberdriver.py start")
    print("=" * 60)
    
    # Give user time to see the message
    await asyncio.sleep(1)
    
    # Run all tests
    tests = [
        test_single_request(),
        test_multiple_requests(),
        test_concurrent_requests(),
        test_action_requests()
    ]
    
    results = []
    for test in tests:
        result = await test
        results.append(result)
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    if all(results):
        print("✓ All tests passed! The buffering issue appears to be fixed.")
    else:
        print("✗ Some tests failed. The buffering issue may still exist.")
        print("\nIf requests are timing out or taking >500ms, try:")
        print("1. Restart cyberdriver")
        print("2. Check for any antivirus/firewall interference")
        print("3. Run cyberdriver with --debug flag if available")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main()) 