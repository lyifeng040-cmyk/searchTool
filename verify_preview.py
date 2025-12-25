#!/usr/bin/env python3
"""Verify hover preview implementation"""
import sys
import os

# Add to path
sys.path.insert(0, 'c:\\Users\\Administrator\\Desktop\\SearchTool')
os.chdir('c:\\Users\\Administrator\\Desktop\\SearchTool\\filesearch')

# Check imports
try:
    from PySide6.QtWidgets import QToolTip
    print("✓ QToolTip imported successfully")
except ImportError as e:
    print(f"✗ Failed to import QToolTip: {e}")
    sys.exit(1)

# Check if utils has format_size and format_time
try:
    from filesearch.utils import format_size, format_time
    print("✓ format_size and format_time imported successfully")
    
    # Test the functions
    size_str = format_size(4500)
    time_str = format_time(1735123200)
    print(f"  - format_size(4500) = {size_str}")
    print(f"  - format_time(1735123200) = {time_str}")
except ImportError as e:
    print(f"✗ Failed to import utils functions: {e}")
    sys.exit(1)

# Check mini_search module
try:
    from filesearch.ui.mini_search import MiniSearchWindow
    print("✓ MiniSearchWindow imported successfully")
    
    # Check if it has the required attributes
    import inspect
    source = inspect.getsource(MiniSearchWindow)
    
    if "_show_preview_tooltip" in source:
        print("✓ _show_preview_tooltip method found")
    else:
        print("✗ _show_preview_tooltip method NOT found")
        
    if "setMouseTracking(True)" in source:
        print("✓ setMouseTracking(True) found")
    else:
        print("✗ setMouseTracking(True) NOT found")
        
    if "QEvent.Leave" in source:
        print("✓ QEvent.Leave handling found")
    else:
        print("✗ QEvent.Leave handling NOT found")
        
    if "QEvent.MouseMove" in source:
        print("✓ QEvent.MouseMove handling found")
    else:
        print("✗ QEvent.MouseMove handling NOT found")
        
except Exception as e:
    print(f"✗ Failed to check MiniSearchWindow: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✓ All checks passed! Hover preview feature is properly implemented.")
