"""
Pi0 Info Display - Version Information

This file contains version information for the application.
Update this file when releasing new versions.
"""

# Version components
VERSION_MAJOR = 0
VERSION_MINOR = 5
VERSION_PATCH = 6
VERSION_STAGE = "beta"  # "alpha", "beta", "rc", or "" for release

# Build the version string
if VERSION_STAGE:
    VERSION = f"v{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}-{VERSION_STAGE}"
else:
    VERSION = f"v{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"

# Short version without 'v' prefix
VERSION_SHORT = VERSION[1:] if VERSION.startswith("v") else VERSION

# Application info
APP_NAME = "Pi0 Info Display"
APP_DESCRIPTION = "Home Assistant Dashboard for Raspberry Pi Zero 2 W"
APP_AUTHOR = "PieZero Contributors"
APP_URL = "https://github.com/jkomilanowicz-lab/PieZero-HA-Display"

# Build info (can be updated by CI/CD)
BUILD_DATE = "2026-02-03"


def get_version():
    """Return the full version string."""
    return VERSION


def get_version_info():
    """Return a dictionary with all version information."""
    return {
        "version": VERSION,
        "major": VERSION_MAJOR,
        "minor": VERSION_MINOR,
        "patch": VERSION_PATCH,
        "stage": VERSION_STAGE,
        "name": APP_NAME,
        "author": APP_AUTHOR,
        "build_date": BUILD_DATE,
    }
