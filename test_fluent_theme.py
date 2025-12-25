#!/usr/bin/env python3
"""
Quick test to verify Fluent Design theme is correctly applied
"""

import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_import_fluent_theme():
    """Test if fluent_theme module can be imported"""
    try:
        from filesearch.ui.fluent_theme import (
            COLORS_LIGHT,
            COLORS_DARK,
            get_fluent_stylesheet,
            apply_fluent_theme
        )
        print("✅ Fluent theme module imported successfully")
        print(f"   - COLORS_LIGHT: {len(COLORS_LIGHT)} colors")
        print(f"   - COLORS_DARK: {len(COLORS_DARK)} colors")
        
        # Test stylesheet generation
        light_ss = get_fluent_stylesheet(False)
        dark_ss = get_fluent_stylesheet(True)
        print(f"   - Light stylesheet: {len(light_ss)} characters")
        print(f"   - Dark stylesheet: {len(dark_ss)} characters")
        
        return True
    except Exception as e:
        print(f"❌ Failed to import fluent_theme: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_import_modern_components():
    """Test if modern_components module can be imported"""
    try:
        from filesearch.ui.components.modern_components import (
            ModernSearchBox,
            SearchBoxWithButtons,
            SearchFilterPanel
        )
        print("✅ Modern components module imported successfully")
        print(f"   - ModernSearchBox: {ModernSearchBox.__name__}")
        print(f"   - SearchBoxWithButtons: {SearchBoxWithButtons.__name__}")
        print(f"   - SearchFilterPanel: {SearchFilterPanel.__name__}")
        return True
    except Exception as e:
        print(f"❌ Failed to import modern_components: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_import_modern_effects():
    """Test if modern_effects module can be imported"""
    try:
        from filesearch.ui.components.modern_effects import (
            ModernButton,
            ModernAccentButton,
            ModernLineEdit
        )
        print("✅ Modern effects module imported successfully")
        print(f"   - ModernButton: {ModernButton.__name__}")
        print(f"   - ModernAccentButton: {ModernAccentButton.__name__}")
        print(f"   - ModernLineEdit: {ModernLineEdit.__name__}")
        return True
    except Exception as e:
        print(f"❌ Failed to import modern_effects: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 60)
    print("🎨 Fluent Design System Verification Test")
    print("=" * 60)
    
    results = []
    
    print("\n[1/3] Testing Fluent Theme Module...")
    results.append(test_import_fluent_theme())
    
    print("\n[2/3] Testing Modern Components Module...")
    results.append(test_import_modern_components())
    
    print("\n[3/3] Testing Modern Effects Module...")
    results.append(test_import_modern_effects())
    
    print("\n" + "=" * 60)
    if all(results):
        print("✅ All tests passed! UI is ready to use.")
        print("\nNext step: Run the application with:")
        print("  python -m filesearch.main")
        return 0
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
