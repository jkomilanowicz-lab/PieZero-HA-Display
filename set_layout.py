#!/usr/bin/env python3
"""
Set layout preference for Pi0 Info Display.
Run via SSH: python3 set_layout.py horizontal|horizontal-alt|vertical
"""

import sys
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
VALID_LAYOUTS = ["horizontal", "horizontal-alt", "vertical"]

def show_help():
    print("Pi0 Display Layout Switcher")
    print("=" * 40)
    print("\nUsage: python3 set_layout.py <layout>")
    print("\nAvailable layouts:")
    print("  horizontal     - Time/Weather left, Today center, Tasks right")
    print("  horizontal-alt - Time/Weather left, Today/Tasks stacked right")
    print("  vertical       - All tiles stacked vertically (portrait mode)")
    print("\nAfter changing, restart the service:")
    print("  sudo systemctl restart pi0display.service")

def get_current_layout():
    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            return config.get("display", {}).get("layout", "horizontal")
    except Exception as e:
        print(f"Error reading config: {e}")
        return None

def set_layout(layout: str):
    if layout not in VALID_LAYOUTS:
        print(f"Error: Invalid layout '{layout}'")
        print(f"Valid options: {', '.join(VALID_LAYOUTS)}")
        return False

    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)

        config["display"]["layout"] = layout

        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=4)

        print(f"Layout set to: {layout}")
        print("\nRestart the service to apply:")
        print("  sudo systemctl restart pi0display.service")
        return True

    except Exception as e:
        print(f"Error updating config: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        current = get_current_layout()
        if current:
            print(f"Current layout: {current}")
        print()
        show_help()
        return

    arg = sys.argv[1].lower()

    if arg in ["-h", "--help", "help"]:
        show_help()
    elif arg == "current":
        current = get_current_layout()
        if current:
            print(f"Current layout: {current}")
    else:
        set_layout(arg)

if __name__ == "__main__":
    main()
