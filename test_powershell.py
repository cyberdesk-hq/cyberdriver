#!/usr/bin/env python3
"""Test script for the new PowerShell implementation."""

import requests
import json

# API endpoint
url = "http://localhost:3000/computer/shell/powershell/exec"

# Test cases
test_commands = [
    {
        "name": "Simple echo",
        "payload": {
            "command": "echo 'Hello World'",
            "same_session": False
        }
    },
    {
        "name": "Get current directory",
        "payload": {
            "command": "pwd",
            "same_session": False
        }
    },
    {
        "name": "JSON output test",
        "payload": {
            "command": "Invoke-RestMethod -Uri 'https://jsonplaceholder.typicode.com/posts/1' | ConvertTo-Json",
            "same_session": False
        }
    },
    {
        "name": "Multi-line output",
        "payload": {
            "command": "Get-Process | Select-Object -First 5 | Format-Table Name, Id, CPU",
            "same_session": False
        }
    }
]

print("Testing new PowerShell implementation...\n")

for test in test_commands:
    print(f"Test: {test['name']}")
    print(f"Command: {test['payload']['command']}")
    
    try:
        response = requests.post(url, json=test['payload'], timeout=10)
        response.raise_for_status()
        
        result = response.json()
        
        print(f"Exit Code: {result.get('exit_code', 'N/A')}")
        print(f"Session ID: {result.get('session_id', 'N/A')}")
        
        if result.get('stdout'):
            print("STDOUT:")
            print("---")
            print(result['stdout'])
            print("---")
        else:
            print("STDOUT: (empty)")
            
        if result.get('stderr'):
            print("STDERR:")
            print("---")
            print(result['stderr'])
            print("---")
        else:
            print("STDERR: (empty)")
            
        if result.get('error'):
            print(f"ERROR: {result['error']}")
            
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
    
    print("\n" + "="*60 + "\n")

print("Test complete!")