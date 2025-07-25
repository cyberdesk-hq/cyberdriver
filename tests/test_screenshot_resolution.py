#!/usr/bin/env python3
"""Test to verify screenshot resolution matches Piglet's fixed 1024x768."""

import requests
from PIL import Image
from io import BytesIO
import sys

def test_screenshot_resolution():
    """Test that screenshots are returned at 1024x768 by default."""
    try:
        # Make request to screenshot endpoint without specifying dimensions
        response = requests.get("http://localhost:3000/computer/display/screenshot")
        response.raise_for_status()
        
        # Load the image and check dimensions
        img = Image.open(BytesIO(response.content))
        width, height = img.size
        
        print(f"Screenshot dimensions: {width}x{height}")
        
        if width == 1024 and height == 768:
            print("✓ Screenshot is correctly sized at 1024x768 (matching Piglet)")
            return True
        else:
            print(f"✗ Screenshot dimensions {width}x{height} don't match expected 1024x768")
            return False
            
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to server. Make sure cyberdriver is running on port 3000")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    success = test_screenshot_resolution()
    sys.exit(0 if success else 1) 