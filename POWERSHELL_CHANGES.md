# PowerShell Implementation Changes

## Overview
The PowerShell execution in cyberdriver has been completely rewritten to provide clean, prompt-free output without the issues of hanging sessions or command echoes.

## Key Changes

### 1. Stateless Execution
- **Old**: Maintained persistent PowerShell sessions that could hang
- **New**: Each command runs in a fresh PowerShell process for clean output

### 2. Direct Command Execution
- **Old**: Used stdin to pipe commands to an interactive PowerShell session
- **New**: Uses `-Command` parameter for direct execution without prompts

### 3. Clean Output
- **Old**: Output included PowerShell prompts, command echoes, and initialization commands
- **New**: Output contains only the actual command results

## Technical Details

### Command Execution
```python
# PowerShell is invoked with these flags:
ps_args = [
    powershell_cmd,
    "-NoLogo",           # No startup banner
    "-NoProfile",        # Don't load profile
    "-NonInteractive",   # No prompts
    "-ExecutionPolicy", "Bypass",
    "-OutputFormat", "Text",  # Plain text output
    "-Command", full_script   # Execute directly
]
```

### Benefits
1. **No hanging sessions**: Each command runs independently
2. **Clean output**: No PS prompts or command echoes
3. **Predictable behavior**: No session state to manage
4. **Better performance**: No overhead of maintaining sessions
5. **Simpler code**: Removed complex session management logic

### Working Directory Support
Working directory changes are handled by prepending `Set-Location` to the command:
```powershell
Set-Location -Path 'C:\desired\path'; Your-Command-Here
```

## API Compatibility
The API remains unchanged:
- `/computer/shell/powershell/exec` - Execute PowerShell commands
- `/computer/shell/powershell/session` - Create/destroy sessions (now no-op for compatibility)

## Testing
Use the provided `test_powershell.py` script to verify the implementation:
```bash
python test_powershell.py
```

## Migration Notes
- The `same_session` parameter is now ignored (kept for API compatibility)
- Session IDs are still returned but have no effect
- Each command runs in isolation - no state is preserved between commands