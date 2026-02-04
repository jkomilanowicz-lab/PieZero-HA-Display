#!/usr/bin/env python3
"""
Pi0 Info Display - Lightweight Home Assistant Dashboard
For Raspberry Pi Zero W with 7" 1024x600 display

Optimized for low memory usage - no browser, no Flask server.
Uses Pygame for direct framebuffer rendering.
"""

import sys
import subprocess
import os
import time
import json
import logging
import logging.handlers
import random
import socket
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Import MQTT client (optional - gracefully handles if not available)
try:
    from mqtt_client import MQTTClient, MQTT_AVAILABLE
except ImportError:
    MQTTClient = None
    MQTT_AVAILABLE = False

# Simple quotes for status bar (max 70 characters)
DAILY_QUOTES = [
    "The best time to plant a tree was 20 years ago. The second best is now.",
    "Stay hungry, stay foolish.",
    "Simplicity is the ultimate sophistication.",
    "Well done is better than well said.",
    "The only way to do great work is to love what you do.",
    "Innovation distinguishes between a leader and a follower.",
    "Life is what happens when you're busy making other plans.",
    "In the middle of difficulty lies opportunity.",
    "The journey of a thousand miles begins with one step.",
    "Be the change you wish to see in the world.",
    "Every moment is a fresh beginning.",
    "What we think, we become.",
    "The best preparation for tomorrow is doing your best today.",
    "It always seems impossible until it's done.",
    "Believe you can and you're halfway there.",
    "Quality is not an act, it is a habit.",
    "Dream big and dare to fail.",
    "Act as if what you do makes a difference. It does.",
    "Success is not final, failure is not fatal.",
    "Keep your face always toward the sunshine.",
]

# Set SDL to use framebuffer (for Pi Zero headless boot)
os.environ.setdefault('SDL_VIDEODRIVER', 'kmsdrm')

import pygame

# Weather condition to icon code mapping
# Maps Home Assistant/Met.no conditions to WeatherPi icon codes
# Icons have 'd' (day) and 'n' (night) variants
WEATHER_ICON_MAP = {
    # Clear conditions
    "sunny": "c01",
    "clear-night": "c01",
    # Cloudy conditions
    "partlycloudy": "c02",
    "cloudy": "c04",
    # Fog
    "fog": "f01",
    # Rain conditions
    "rainy": "r02",
    "pouring": "r03",
    "hail": "r05",
    # Snow conditions
    "snowy": "s02",
    "snowy-rainy": "s05",
    # Thunderstorm conditions
    "lightning": "t01",
    "lightning-rainy": "t02",
    # Wind conditions (use partly cloudy icons as best match)
    "windy": "c02",
    "windy-variant": "c03",
    # Exceptional/unknown
    "exceptional": "unknown",
}

from ha_api import HomeAssistantAPI, format_weather_condition
from version import VERSION

# Configure logging with rotating file handler
def setup_logging():
    """Configure logging with console and rotating file output."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'pi0display.log')

    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Rotating file handler (5MB max, keep 3 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3
    )
    file_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return logging.getLogger(__name__)

logger = setup_logging()


class Config:
    """Configuration loader."""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.data = self._load()

    def _load(self) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config: {e}")
            sys.exit(1)

    def get(self, *keys, default=None):
        """Get nested config value."""
        value = self.data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            if value is None:
                return default
        return value


class HolidayManager:
    """Manage US federal and national holidays."""

    def __init__(self, holidays_path: str = "us_holidays.json"):
        self.holidays = {}
        self.federal_holidays = []
        self._load(holidays_path)

    def _load(self, path: str):
        """Load holidays from JSON file."""
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                self.holidays = data.get("holidays", {})
                self.federal_holidays = data.get("federal_holidays", [])
        except FileNotFoundError:
            logger.warning(f"Holidays file not found: {path}")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in holidays file: {e}")

    def get_today_holiday(self) -> Optional[str]:
        """Get holiday name if today is a holiday, otherwise None."""
        today_str = date.today().strftime("%Y-%m-%d")
        return self.holidays.get(today_str)

    def is_federal_holiday(self, holiday_name: str) -> bool:
        """Check if a holiday is a federal holiday."""
        return holiday_name in self.federal_holidays


class DataCache:
    """Persistent data cache to prevent screen blanking during updates.

    Uses RAM disk (/run) by default to minimize SD card/SSD writes.
    Cache is lost on reboot but reconstructed quickly from HA.
    """

    def __init__(self, cache_path: str = None, use_ramdisk: bool = True):
        # Default to RAM disk location, fallback to /tmp
        if cache_path is None:
            if use_ramdisk:
                # /run is typically tmpfs (RAM) on Linux
                cache_dir = "/run/pi0display"
                self.cache_path = f"{cache_dir}/cache.json"
            else:
                self.cache_path = "/tmp/pi0display_cache.json"
        else:
            self.cache_path = cache_path

        # Ensure cache directory exists
        cache_dir = os.path.dirname(self.cache_path)
        if cache_dir and not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except PermissionError:
                # Fall back to /tmp if we can't create /run/pi0display
                logger.warning(f"Cannot create {cache_dir}, falling back to /tmp")
                self.cache_path = "/tmp/pi0display_cache.json"

        self.data = {
            "weather": None,
            "tasks": [],
            "calendar_today": [],
            "calendar_upcoming": [],
            "mailbox": None,
            "mailbox_opened_today": False,
            "mailbox_opened_time": None,
            "last_update": {}
        }
        self._load()
        logger.info(f"Cache initialized at: {self.cache_path}")

    def _load(self):
        """Load cache from disk."""
        try:
            if os.path.exists(self.cache_path):
                with open(self.cache_path, 'r') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
        except Exception as e:
            logger.warning(f"Could not load cache: {e}")

    def save(self):
        """Save cache to disk."""
        try:
            with open(self.cache_path, 'w') as f:
                json.dump(self.data, f)
        except Exception as e:
            logger.warning(f"Could not save cache: {e}")

    def get(self, key, default=None):
        """Get cached value."""
        return self.data.get(key, default)

    def set(self, key, value):
        """Set cached value and save."""
        self.data[key] = value
        self.save()


class Theme:
    """Color theme manager."""

    def __init__(self, config: Config):
        self.bg = self._hex_to_rgb(config.get("theme", "background", default="#1a1a2e"))
        self.tile_time = self._hex_to_rgb(config.get("theme", "tile_time", default="#16213e"))
        self.tile_weather = self._hex_to_rgb(config.get("theme", "tile_weather", default="#0f3460"))
        self.tile_indicator = self._hex_to_rgb(config.get("theme", "tile_indicator", default="#1a3a5c"))
        self.tile_today = self._hex_to_rgb(config.get("theme", "tile_today", default="#533483"))
        self.tile_today_upcoming = self._hex_to_rgb(config.get("theme", "tile_today_upcoming", default="#634993"))
        self.tile_tasks = self._hex_to_rgb(config.get("theme", "tile_tasks", default="#1e5128"))
        self.tile_status = self._hex_to_rgb(config.get("theme", "tile_status", default="#16213e"))
        self.accent = self._hex_to_rgb(config.get("theme", "accent", default="#e94560"))
        self.accent_date = self._hex_to_rgb(config.get("theme", "accent_date", default="#d35400"))
        self.text_primary = self._hex_to_rgb(config.get("theme", "text_primary", default="#eaeaea"))
        self.text_secondary = self._hex_to_rgb(config.get("theme", "text_secondary", default="#a0a0a0"))

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


class Pi0Display:
    """Main display application."""

    def __init__(self, config_path: str = "config.json"):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.config = Config(config_path)
        self.theme = Theme(self.config)
        self.holidays = HolidayManager(os.path.join(self.script_dir, "us_holidays.json"))

        # Initialize cache with RAM disk option
        use_ramdisk = self.config.get("cache", "use_ramdisk", default=True)
        cache_path = self.config.get("cache", "path", default=None)
        self.cache = DataCache(cache_path=cache_path, use_ramdisk=use_ramdisk)

        # Initialize Home Assistant API
        ha_url = self.config.get("home_assistant", "url")
        ha_token = self.config.get("home_assistant", "token")
        self.ha = HomeAssistantAPI(ha_url, ha_token)

        # Display settings
        self.width = self.config.get("display", "width", default=1024)
        self.height = self.config.get("display", "height", default=600)
        self.fullscreen = self.config.get("display", "fullscreen", default=True)
        self.hide_cursor = self.config.get("display", "hide_cursor", default=True)
        self.layout = self.config.get("display", "layout", default="horizontal")

        # Entity configuration
        self.weather_entity = self.config.get("entities", "weather")
        self.task_lists = self.config.get("entities", "task_lists", default=[])
        self.calendars = self.config.get("entities", "calendars", default=[])
        self.mailbox_entity = self.config.get("entities", "mailbox", default="binary_sensor.mailbox_door")
        self.mailbox_check_switch = self.config.get("entities", "mailbox_check", default="input_boolean.check_mailbox")

        # Refresh intervals
        self.weather_interval = self.config.get("refresh_interval", "weather_seconds", default=300)
        self.tasks_interval = self.config.get("refresh_interval", "tasks_seconds", default=60)
        self.calendar_interval = self.config.get("refresh_interval", "calendar_seconds", default=300)
        self.mailbox_interval = self.config.get("refresh_interval", "mailbox_seconds", default=60)

        # Data from cache (prevents blank screen)
        self.weather_data: Optional[Dict] = self.cache.get("weather")
        self.task_items: List[Dict] = self.cache.get("tasks", [])
        self.calendar_today: List[Dict] = self.cache.get("calendar_today", [])
        self.calendar_upcoming: List[Dict] = self.cache.get("calendar_upcoming", [])
        self.mailbox_data: Optional[Dict] = self.cache.get("mailbox")
        self.mailbox_opened_today: bool = self.cache.get("mailbox_opened_today", False)
        self.mailbox_opened_time: Optional[str] = self.cache.get("mailbox_opened_time")
        self.mailbox_cleared: bool = self.cache.get("mailbox_cleared", False)  # User acknowledged mailbox
        self.sun_data: Optional[Dict] = self.cache.get("sun_data")

        # Daily quote - only changes once per day, persists across restarts
        cached_quote = self.cache.get("daily_quote")
        cached_quote_date = self.cache.get("quote_date")
        if cached_quote and cached_quote_date == str(date.today()):
            self.daily_quote = cached_quote
        else:
            self.daily_quote = random.choice(DAILY_QUOTES)
            self.cache.set("daily_quote", self.daily_quote)
            self.cache.set("quote_date", str(date.today()))

        # Last update timestamps
        self.last_weather_update = 0
        self.last_tasks_update = 0
        self.last_calendar_update = 0
        self.last_mailbox_update = 0
        self.last_sun_update = 0

        # Track last mailbox state for edge detection
        self.last_mailbox_state = None
        # Load mailbox_check_date from cache (stored as ISO string)
        cached_date = self.cache.get("mailbox_check_date")
        self.mailbox_check_date = date.fromisoformat(cached_date) if cached_date else None
        self.mailbox_check_on = False  # True when check_mailbox switch is ON (mail waiting)
        self.mailbox_icon_rect = None  # Clickable area for mailbox envelope icon

        # Task scrolling state
        self.task_scroll_offset = 0  # Current scroll position (task index)
        self.task_last_interaction = 0  # Timestamp of last scroll interaction
        self.task_scroll_reset_delay = 45  # Seconds before auto-reset to top
        self.task_arrow_rects = {"up": None, "down": None}  # Clickable arrow areas
        self.task_touch_areas = []  # List of (rect, task_dict) for touch detection

        # Task completion confirmation state
        self.task_confirm_pending = None  # Task dict awaiting confirmation
        self.task_confirm_rects = {"yes": None, "no": None}  # Confirm/cancel button areas

        # Weather forecast mode
        self.forecast_mode = False  # True when showing 6-day forecast
        self.forecast_data: List[Dict] = self.cache.get("forecast", [])
        self.last_forecast_update = 0
        self.forecast_interval = 1800  # Update forecast every 30 minutes

        # Offline action queue - stores actions to process when connection is restored
        self.pending_actions: List[Dict] = self.cache.get("pending_actions", [])

        # Image cache to avoid repeated disk loads and scaling
        self._image_cache: Dict[str, pygame.Surface] = {}

        # Home Assistant connection status
        self.ha_connected = True  # Assume connected initially
        self.last_ha_check = 0
        self.ha_check_interval = 30  # Check every 30 seconds
        self._ha_host, self._ha_port = self._parse_ha_url(self.ha.url)

        # Internet connection status
        self.internet_connected = True  # Assume connected initially
        self.last_internet_check = 0
        self.internet_check_interval = 30  # Check every 30 seconds

        # WiFi keepalive settings (prevents WiFi power save from dropping connection)
        self.last_keepalive = 0
        self.keepalive_interval = self.config.get("network", "keepalive_interval", default=30)
        self.keepalive_target = self.config.get("network", "keepalive_target", default="192.168.1.1")
        self.keepalive_port = self.config.get("network", "keepalive_port", default=53)
        self.internet_check_host = self.config.get("network", "internet_check_host", default="8.8.8.8")
        self.internet_check_port = self.config.get("network", "internet_check_port", default=53)

        # Sleep mode (full-screen clock with dimmed display)
        self.sleep_mode = False
        self.last_tap_time = 0
        self.double_tap_threshold = 0.5  # Seconds between taps for double-tap detection

        # MQTT client for Home Assistant integration
        self.mqtt_client: Optional[MQTTClient] = None
        if MQTTClient and MQTT_AVAILABLE:
            mqtt_config = self.config.data.get("mqtt", {})
            if mqtt_config.get("enabled", False):
                self.mqtt_client = MQTTClient(mqtt_config, self._get_mqtt_state)
                logger.info("MQTT client initialized")

        # Initialize Pygame
        self._init_pygame()

    def _get_loading_image(self) -> Optional[str]:
        """Get the loading image path from images folder."""
        image_path = os.path.join(self.script_dir, "images", "Loading.png")
        if os.path.exists(image_path):
            return image_path
        return None

    def _show_splash_screen(self):
        """Show splash screen with loading image."""
        image_path = self._get_loading_image()
        if not image_path:
            # No splash image, just show loading text
            self.screen.fill(self.theme.bg)
            self._draw_text("Loading...", self.font_large, self.theme.text_primary,
                           pygame.Rect(0, 0, self.width, self.height))
            pygame.display.flip()
            return

        try:
            splash_img = pygame.image.load(image_path)
            # Scale to fit screen
            splash_img = pygame.transform.scale(splash_img, (self.width, self.height))
            # Kill boot splash (fbi) right before taking over display
            subprocess.run(["killall", "-q", "fbi"], capture_output=True)
            self.screen.blit(splash_img, (0, 0))
            pygame.display.flip()
            logger.info(f"Showing splash screen: {os.path.basename(image_path)}")
        except Exception as e:
            logger.warning(f"Could not load splash image: {e}")
            self.screen.fill(self.theme.bg)
            pygame.display.flip()

    def _draw_loading_progress(self, progress: float, status: str):
        """Draw loading progress bar overlay."""
        bar_height = 30
        bar_y = self.height - bar_height - 20
        bar_margin = 50
        bar_width = self.width - 2 * bar_margin

        # Background bar
        pygame.draw.rect(self.screen, (50, 50, 50),
                        (bar_margin, bar_y, bar_width, bar_height), border_radius=10)

        # Progress bar
        progress_width = int(bar_width * progress)
        if progress_width > 0:
            pygame.draw.rect(self.screen, self.theme.accent,
                            (bar_margin, bar_y, progress_width, bar_height), border_radius=10)

        # Status text
        status_surface = self.font_small.render(status, True, self.theme.text_primary)
        status_rect = status_surface.get_rect(center=(self.width // 2, bar_y + bar_height // 2))
        self.screen.blit(status_surface, status_rect)

        # Version text in lower right corner
        version_text = self.font_version.render(VERSION, True, self.theme.text_secondary)
        self.screen.blit(version_text, (self.width - version_text.get_width() - 5,
                                        self.height - version_text.get_height() - 3))

        pygame.display.flip()

    def _parse_ha_url(self, url: str) -> Tuple[str, int]:
        """Parse Home Assistant URL to extract host and port."""
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 8123)
        return host, port

    def _check_ha_connection(self) -> bool:
        """Check if Home Assistant is reachable using a lightweight socket connect."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self._ha_host, self._ha_port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"HA connection check failed: {e}")
            return False

    def _check_internet_connection(self) -> bool:
        """Check internet connectivity using configured DNS server."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((self.internet_check_host, self.internet_check_port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Internet connection check failed: {e}")
            return False

    def _get_mqtt_state(self, key: str):
        """Callback for MQTT client to get current display state."""
        if key == "sleep_mode":
            return self.sleep_mode
        elif key == "forecast_mode":
            return self.forecast_mode
        elif key == "ha_connected":
            return self.ha_connected
        elif key == "internet_connected":
            return self.internet_connected
        return None

    def _send_keepalive(self):
        """Send a keepalive packet to prevent WiFi from going idle."""
        try:
            # Use UDP socket for lightweight keepalive (no connection overhead)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1)
            # Send a small DNS-like query packet to the gateway
            # This keeps the WiFi radio active without generating much traffic
            sock.sendto(b'\x00', (self.keepalive_target, self.keepalive_port))
            sock.close()
            logger.debug(f"Keepalive sent to {self.keepalive_target}:{self.keepalive_port}")
        except Exception as e:
            logger.debug(f"Keepalive failed: {e}")

    def _load_status_icons(self):
        """Load status icons for the indicator box (HA and Internet)."""
        # Different sizes for different icon types to match envelope (36px)
        icon_sizes = {
            "ha": 36,
            "internet": 28,
        }

        # Define all icons to load: (cache_prefix, folder, [(name, filename), ...])
        icon_sets = [
            ("ha", "HA_Logos", [("light", "HA-Light.png"), ("dark", "HA-Dark.png")]),
            ("internet", "Internet", [("up", "Internet-Up.png"), ("down", "Internet-Down.png")]),
        ]

        for prefix, folder, icons in icon_sets:
            icon_size = icon_sizes.get(prefix, 30)
            icons_dir = os.path.join(self.script_dir, "images", "logos", folder)
            for name, filename in icons:
                cache_key = f"{prefix}_{name}_{icon_size}"
                if cache_key not in self._image_cache:
                    icon_path = os.path.join(icons_dir, filename)
                    if os.path.exists(icon_path):
                        try:
                            icon = pygame.image.load(icon_path)
                            icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
                            self._image_cache[cache_key] = icon
                        except Exception as e:
                            logger.warning(f"Could not load icon {filename}: {e}")

    def _get_status_icon(self, icon_type: str, is_connected: bool) -> Optional[pygame.Surface]:
        """Get a status icon based on connection state."""
        # Match sizes from _load_status_icons
        icon_sizes = {"ha": 36, "internet": 28}
        icon_size = icon_sizes.get(icon_type, 30)

        if icon_type == "ha":
            name = "light" if is_connected else "dark"
        else:  # internet
            name = "up" if is_connected else "down"

        cache_key = f"{icon_type}_{name}_{icon_size}"
        return self._image_cache.get(cache_key)

    def _init_pygame(self):
        """Initialize Pygame display and fonts."""
        # Try multiple video drivers in order of preference
        video_drivers = ['kmsdrm', 'fbcon', 'directfb', 'x11', 'dummy']

        display_initialized = False
        for driver in video_drivers:
            try:
                os.environ['SDL_VIDEODRIVER'] = driver
                pygame.init()

                flags = pygame.FULLSCREEN if self.fullscreen else 0
                self.screen = pygame.display.set_mode((self.width, self.height), flags)
                logger.info(f"Display initialized with {driver} driver")
                display_initialized = True
                break
            except pygame.error as e:
                logger.warning(f"Failed to initialize with {driver}: {e}")
                pygame.quit()
                continue

        if not display_initialized:
            # Last resort - try without specifying driver
            if 'SDL_VIDEODRIVER' in os.environ:
                del os.environ['SDL_VIDEODRIVER']
            pygame.init()
            try:
                self.screen = pygame.display.set_mode((self.width, self.height))
                logger.info("Display initialized with default driver")
            except pygame.error as e:
                logger.error(f"Could not initialize any display: {e}")
                raise

        pygame.display.set_caption("Pi0 Info Display")

        if self.hide_cursor:
            pygame.mouse.set_visible(False)

        self.clock = pygame.time.Clock()

        # Load fonts
        font_locations = [
            self.script_dir,
            os.path.join(self.script_dir, "Pi-Dashboard"),
            os.path.expanduser("~/pi0display"),
        ]

        font_path = None
        symbol_font_path = None

        for loc in font_locations:
            if os.path.exists(os.path.join(loc, "Poppins-Regular.ttf")):
                font_path = os.path.join(loc, "Poppins-Regular.ttf")
            if os.path.exists(os.path.join(loc, "seguisym.ttf")):
                symbol_font_path = os.path.join(loc, "seguisym.ttf")

        try:
            self.font_clock = pygame.font.Font(font_path, 180)
            self.font_clock.set_bold(True)
            self.font_xlarge = pygame.font.Font(font_path, 72)
            self.font_large = pygame.font.Font(font_path, 48)
            self.font_medium = pygame.font.Font(font_path, 32)
            self.font_medium_bold = pygame.font.Font(font_path, 32)
            self.font_medium_bold.set_bold(True)
            self.font_small = pygame.font.Font(font_path, 24)
            self.font_small_bold = pygame.font.Font(font_path, 24)
            self.font_small_bold.set_bold(True)
            self.font_xsmall = pygame.font.Font(font_path, 18)
            self.font_xsmall_bold = pygame.font.Font(font_path, 18)
            self.font_xsmall_bold.set_bold(True)
            self.font_version = pygame.font.Font(font_path, 12)
            self.font_symbol = pygame.font.Font(symbol_font_path, 24)
            self.font_symbol_large = pygame.font.Font(symbol_font_path, 36)
        except (FileNotFoundError, TypeError):
            logger.warning("Custom fonts not found, using system fonts")
            self.font_clock = pygame.font.SysFont("sans-serif", 180, bold=True)
            self.font_xlarge = pygame.font.SysFont("sans-serif", 72)
            self.font_large = pygame.font.SysFont("sans-serif", 48)
            self.font_medium = pygame.font.SysFont("sans-serif", 32)
            self.font_medium_bold = pygame.font.SysFont("sans-serif", 32, bold=True)
            self.font_small = pygame.font.SysFont("sans-serif", 24)
            self.font_small_bold = pygame.font.SysFont("sans-serif", 24, bold=True)
            self.font_xsmall = pygame.font.SysFont("sans-serif", 18)
            self.font_xsmall_bold = pygame.font.SysFont("sans-serif", 18, bold=True)
            self.font_version = pygame.font.SysFont("sans-serif", 12)
            self.font_symbol = pygame.font.SysFont("sans-serif", 24)
            self.font_symbol_large = pygame.font.SysFont("sans-serif", 36)

        # Layout calculations
        self.padding = 15
        self._calculate_layout()

    def _calculate_layout(self):
        """Calculate tile positions and sizes based on layout mode."""
        p = self.padding

        # Status bar height
        status_bar_height = 50

        if self.layout == "vertical":
            # Vertical orientation (600x1024 effectively)
            # Swap width/height conceptually
            main_height = self.height - status_bar_height - 3 * p

            # Top row: Time tile
            time_width = self.width - 2 * p
            time_height = 150
            self.time_rect = pygame.Rect(p, p, time_width, time_height)

            # Second row: Weather
            weather_height = 100
            self.weather_rect = pygame.Rect(p, time_height + 2 * p, time_width, weather_height)

            # Third row: Indicator box
            indicator_height = 50
            self.indicator_rect = pygame.Rect(p, time_height + weather_height + 3 * p,
                                               time_width, indicator_height)

            # Fourth row: Today's events
            today_y = time_height + weather_height + indicator_height + 4 * p
            remaining_height = main_height - time_height - weather_height - indicator_height - 3 * p
            today_height = remaining_height // 2
            self.today_rect = pygame.Rect(p, today_y, time_width, today_height)

            # Fifth row: Tasks
            tasks_y = today_y + today_height + p
            tasks_height = remaining_height - today_height - p
            self.tasks_rect = pygame.Rect(p, tasks_y, time_width, tasks_height)

        elif self.layout == "horizontal-alt":
            # Alternative horizontal: Time/Weather/Indicator left, Today/Tasks stacked right
            main_height = self.height - status_bar_height - 3 * p
            left_col_width = 320
            right_col_width = self.width - left_col_width - 3 * p

            # Left column: Time 45%, Weather 35%, Indicator 20%
            time_height = int(main_height * 0.45)
            weather_height = int(main_height * 0.35)
            indicator_height = main_height - time_height - weather_height - 2 * p

            self.time_rect = pygame.Rect(p, p, left_col_width, time_height)
            self.weather_rect = pygame.Rect(p, p + time_height + p, left_col_width, weather_height)
            self.indicator_rect = pygame.Rect(p, p + time_height + p + weather_height + p,
                                               left_col_width, indicator_height)

            # Right column
            right_x = left_col_width + 2 * p
            right_tile_height = (main_height - p) // 2
            self.today_rect = pygame.Rect(right_x, p, right_col_width, right_tile_height)
            self.tasks_rect = pygame.Rect(right_x, p * 2 + right_tile_height, right_col_width, right_tile_height)

        else:
            # Default horizontal: Time/Weather/Indicator left, Today center, Tasks right
            main_height = self.height - status_bar_height - 3 * p

            # Left column (Time + Weather + Indicator stacked)
            left_col_width = 280
            gap = 5  # Small gap between left column cards

            # Distribute left column height: Time 52%, Weather 38%, Indicator ~10%
            time_height = int(main_height * 0.52)
            weather_height = int(main_height * 0.38)
            indicator_height = main_height - time_height - weather_height - 2 * gap

            self.time_rect = pygame.Rect(p, p, left_col_width, time_height)
            self.weather_rect = pygame.Rect(p, p + time_height + gap, left_col_width, weather_height)
            self.indicator_rect = pygame.Rect(p, p + time_height + gap + weather_height + gap,
                                               left_col_width, indicator_height)

            # Right area split equally between Today and Tasks
            right_area_x = left_col_width + 2 * p
            right_area_width = self.width - right_area_x - p
            tile_width = (right_area_width - p) // 2

            # Middle column (Today)
            self.today_rect = pygame.Rect(right_area_x, p, tile_width, main_height)

            # Right column (Tasks) - equal width to Today
            self.tasks_rect = pygame.Rect(right_area_x + tile_width + p, p, tile_width, main_height)

        # Status bar at bottom
        self.status_rect = pygame.Rect(p, self.height - status_bar_height - p,
                                        self.width - 2 * p, status_bar_height)

    def _draw_rounded_rect(self, rect: pygame.Rect, color: Tuple[int, int, int], radius: int = 15):
        """Draw a rounded rectangle."""
        pygame.draw.rect(self.screen, color, rect, border_radius=radius)

    def _truncate_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        """Truncate text with ellipsis if it exceeds max_width."""
        if font.size(text)[0] <= max_width:
            return text

        while len(text) > 0 and font.size(text + "...")[0] > max_width:
            text = text[:-1]
        return text + "..." if text else ""

    def _draw_text(self, text: str, font: pygame.font.Font, color: Tuple[int, int, int],
                   rect: pygame.Rect, align: str = "center", v_align: str = "center",
                   y_offset: int = 0, truncate: bool = True) -> int:
        """Draw text within a rectangle. Returns the height used."""
        max_width = rect.width - 2 * self.padding
        if truncate:
            text = self._truncate_text(text, font, max_width)

        surface = font.render(text, True, color)
        text_rect = surface.get_rect()

        # Horizontal alignment
        if align == "center":
            text_rect.centerx = rect.centerx
        elif align == "left":
            text_rect.left = rect.left + self.padding
        elif align == "right":
            text_rect.right = rect.right - self.padding

        # Vertical alignment
        if v_align == "center":
            text_rect.centery = rect.centery + y_offset
        elif v_align == "top":
            text_rect.top = rect.top + self.padding + y_offset
        elif v_align == "bottom":
            text_rect.bottom = rect.bottom - self.padding + y_offset

        self.screen.blit(surface, text_rect)
        return text_rect.height

    def _draw_header_with_icons(self, text: str, icon: str, font: pygame.font.Font,
                                 color: Tuple[int, int, int], rect: pygame.Rect,
                                 icon_color: Tuple[int, int, int] = None) -> int:
        """Draw a header with icons on either side. Returns height used."""
        if icon_color is None:
            icon_color = color

        # Render the text
        text_surface = font.render(text, True, color)

        # Render the icons using symbol font
        icon_surface = self.font_symbol.render(icon, True, icon_color)

        # Calculate total width: icon + spacing + text + spacing + icon
        spacing = 12
        total_width = icon_surface.get_width() * 2 + text_surface.get_width() + spacing * 2

        # Calculate starting x position to center everything
        start_x = rect.centerx - total_width // 2
        y = rect.top + self.padding

        # Draw left icon
        self.screen.blit(icon_surface, (start_x, y + (text_surface.get_height() - icon_surface.get_height()) // 2))

        # Draw text
        text_x = start_x + icon_surface.get_width() + spacing
        self.screen.blit(text_surface, (text_x, y))

        # Draw right icon
        right_icon_x = text_x + text_surface.get_width() + spacing
        self.screen.blit(icon_surface, (right_icon_x, y + (text_surface.get_height() - icon_surface.get_height()) // 2))

        return text_surface.get_height()

    def _draw_text_wrapped(self, lines: List[str], font: pygame.font.Font,
                           color: Tuple[int, int, int], rect: pygame.Rect,
                           align: str = "left", start_y: int = 0, line_spacing: int = 5) -> int:
        """Draw multiple lines of text. Returns total height used."""
        y = rect.top + self.padding + start_y
        total_height = 0
        max_width = rect.width - 2 * self.padding

        for line in lines:
            if y + font.get_height() > rect.bottom - self.padding:
                break  # Stop if we'd overflow

            # Truncate line if needed
            line = self._truncate_text(line, font, max_width)

            surface = font.render(line, True, color)
            text_rect = surface.get_rect()

            if align == "center":
                text_rect.centerx = rect.centerx
            elif align == "left":
                text_rect.left = rect.left + self.padding
            elif align == "right":
                text_rect.right = rect.right - self.padding

            text_rect.top = y
            self.screen.blit(surface, text_rect)

            y += font.get_height() + line_spacing
            total_height = y - rect.top - self.padding

        return total_height

    def _wrap_text(self, text: str, font: pygame.font.Font, max_width: int) -> List[str]:
        """Wrap text to fit within max_width, breaking on word boundaries."""
        words = text.split(' ')
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip() if current_line else word
            if font.size(test_line)[0] <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                # If single word is too long, truncate it
                if font.size(word)[0] > max_width:
                    word = self._truncate_text(word, font, max_width)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines

    def _draw_tasks_wrapped(self, items: List[Dict], font: pygame.font.Font,
                            color: Tuple[int, int, int], rect: pygame.Rect,
                            start_y: int = 0, line_spacing: int = 5) -> int:
        """Draw task items with word wrapping."""
        y = rect.top + self.padding + start_y
        max_width = rect.width - 2 * self.padding
        checkbox_width = font.size("[ ] ")[0]

        for item in items:
            if y + font.get_height() > rect.bottom - self.padding:
                break

            summary = item.get("summary", "")
            checkbox = "[ ]" if item.get("status") == "needs_action" else "[x]"

            # Wrap the summary text (accounting for checkbox width)
            wrapped_lines = self._wrap_text(summary, font, max_width - checkbox_width)

            for i, line in enumerate(wrapped_lines):
                if y + font.get_height() > rect.bottom - self.padding:
                    break

                if i == 0:
                    # First line includes checkbox
                    display_text = f"{checkbox} {line}"
                else:
                    # Continuation lines are indented
                    display_text = f"    {line}"

                surface = font.render(display_text, True, color)
                text_rect = surface.get_rect(left=rect.left + self.padding, top=y)
                self.screen.blit(surface, text_rect)
                y += font.get_height() + line_spacing

        return y - rect.top - self.padding - start_y

    def _get_time_of_day(self) -> str:
        """Determine time of day based on sun data: morning, afternoon, or night."""
        now = datetime.now()

        if self.sun_data:
            try:
                sun_state = self.sun_data.get("state", "")
                rising = self.sun_data.get("rising", False)
                noon_str = self.sun_data.get("next_noon", "")

                # Parse noon time
                noon_hour = 12  # default
                if noon_str:
                    noon_dt = datetime.fromisoformat(noon_str.replace("Z", "+00:00"))
                    noon_local = noon_dt.astimezone()
                    noon_hour = noon_local.hour

                current_hour = now.hour

                if sun_state == "above_horizon":
                    # Sun is up
                    if current_hour < noon_hour:
                        return "morning"
                    else:
                        return "afternoon"
                else:
                    # Sun is below horizon
                    if rising:
                        # Approaching sunrise = dawn/morning
                        return "morning"
                    else:
                        # After sunset = night
                        return "night"
            except Exception as e:
                logger.debug(f"Error parsing sun data: {e}")

        # Fallback based on hour
        hour = now.hour
        if 6 <= hour < 12:
            return "morning"
        elif 12 <= hour < 18:
            return "afternoon"
        else:
            return "night"

    def _load_time_image(self, time_of_day: str, size: int) -> Optional[pygame.Surface]:
        """Load and cache time-of-day image at specified size."""
        cache_key = f"time_{time_of_day}_{size}"

        # Return cached image if available
        if cache_key in self._image_cache:
            return self._image_cache[cache_key]

        image_map = {
            "morning": "Morning.png",
            "afternoon": "afternoon.png",
            "night": "night.png"
        }
        filename = image_map.get(time_of_day, "Morning.png")
        image_path = os.path.join(self.script_dir, "images", filename)

        try:
            if os.path.exists(image_path):
                img = pygame.image.load(image_path)
                # Scale and cache the image
                scaled_img = pygame.transform.scale(img, (size, size))
                self._image_cache[cache_key] = scaled_img
                return scaled_img
        except Exception as e:
            logger.warning(f"Could not load time image {filename}: {e}")
        return None

    def _is_daytime(self) -> bool:
        """Check if the sun is above the horizon (daytime)."""
        if self.sun_data:
            return self.sun_data.get("state", "") == "above_horizon"
        # Fallback based on hour (6am-6pm = day)
        hour = datetime.now().hour
        return 6 <= hour < 18

    def _get_weather_icon_code(self, condition: str, is_day: bool = None) -> str:
        """Map HA weather condition to WeatherPi icon code."""
        if is_day is None:
            is_day = self._is_daytime()

        # Get base icon code from mapping
        base_code = WEATHER_ICON_MAP.get(condition.lower(), "unknown")

        # Add day/night suffix if not 'unknown'
        if base_code != "unknown":
            suffix = "d" if is_day else "n"
            return f"{base_code}{suffix}"
        return base_code

    def _load_weather_icon(self, condition: str, size: Tuple[int, int],
                          is_day: bool = None, alpha: int = 180) -> Optional[pygame.Surface]:
        """Load and cache weather icon for a condition."""
        icon_code = self._get_weather_icon_code(condition, is_day)
        cache_key = f"weather_{icon_code}_{size[0]}x{size[1]}_{alpha}"

        # Return cached image if available
        if cache_key in self._image_cache:
            return self._image_cache[cache_key]

        # Try to load the icon
        icon_path = os.path.join(self.script_dir, "images", "weather", f"{icon_code}.png")
        if not os.path.exists(icon_path):
            # Try unknown fallback
            icon_path = os.path.join(self.script_dir, "images", "weather", "unknown.png")
            if not os.path.exists(icon_path):
                return None

        try:
            img = pygame.image.load(icon_path).convert_alpha()
            # Scale to requested size
            scaled_img = pygame.transform.scale(img, size)
            # Apply alpha (transparency) for background use
            if alpha < 255:
                scaled_img.set_alpha(alpha)
            self._image_cache[cache_key] = scaled_img
            return scaled_img
        except Exception as e:
            logger.warning(f"Could not load weather icon {icon_code}: {e}")
            return None

    def draw_time_tile(self):
        """Draw the time and date tile with time-of-day image."""
        self._draw_rounded_rect(self.time_rect, self.theme.tile_time)

        now = datetime.now()
        margin = 4  # Small margin from tile edge

        # === TIME BOX (top section) ===
        time_box_x = self.time_rect.left + margin
        time_box_width = self.time_rect.width - margin * 2
        time_box_height = 85
        time_box_y = self.time_rect.top + margin

        time_box = pygame.Rect(time_box_x, time_box_y, time_box_width, time_box_height)
        pygame.draw.rect(self.screen, self.theme.tile_weather, time_box, border_radius=10)

        # Time-of-day image on the left inside the time box
        time_of_day = self._get_time_of_day()
        img_size = time_box_height - 10
        time_img = self._load_time_image(time_of_day, img_size)
        if time_img:
            img_x = time_box.left + 5
            img_y = time_box.top + (time_box_height - img_size) // 2
            self.screen.blit(time_img, (img_x, img_y))

        # Current time (no AM/PM) - RIGHT-JUSTIFIED within time box
        hour = now.hour % 12
        if hour == 0:
            hour = 12
        current_time = f"{hour}:{now.strftime('%M')}"

        time_surface = self.font_xlarge.render(current_time, True, self.theme.text_primary)
        time_text_rect = time_surface.get_rect(
            right=time_box.right - 10,
            centery=time_box.centery
        )
        self.screen.blit(time_surface, time_text_rect)

        # === DATE BOX (middle section with orangish accent) ===
        # Check for holiday to determine layout
        holiday = self.holidays.get_today_holiday()

        # Calculate date box dimensions
        date_box_x = self.time_rect.left + margin
        date_box_width = self.time_rect.width - margin * 2
        date_box_y = time_box.bottom + 8

        # Reserve space for holiday at bottom if present
        if holiday:
            holiday_height = self.font_xsmall.get_height() + 8
            date_box_height = self.time_rect.bottom - margin - holiday_height - date_box_y
        else:
            # No holiday - date box extends closer to bottom
            date_box_height = self.time_rect.bottom - margin - date_box_y

        date_box = pygame.Rect(date_box_x, date_box_y, date_box_width, date_box_height)
        pygame.draw.rect(self.screen, self.theme.accent_date, date_box, border_radius=10)

        # Day name (BOLD) and date - centered in date box
        day_name = now.strftime("%A")
        us_date = now.strftime("%m/%d/%Y")

        day_surface = self.font_small_bold.render(day_name, True, self.theme.text_primary)
        date_surface = self.font_small.render(us_date, True, self.theme.text_primary)

        # Calculate total height of day + date text
        text_spacing = 5
        total_text_height = day_surface.get_height() + text_spacing + date_surface.get_height()

        # Center both lines vertically in date box
        text_start_y = date_box.centery - total_text_height // 2

        day_rect = day_surface.get_rect(centerx=date_box.centerx, top=text_start_y)
        self.screen.blit(day_surface, day_rect)

        date_rect = date_surface.get_rect(centerx=date_box.centerx, top=day_rect.bottom + text_spacing)
        self.screen.blit(date_surface, date_rect)

        # === HOLIDAY (bottom section, if applicable) ===
        if holiday:
            holiday_surface = self.font_xsmall.render(holiday, True, self.theme.accent)
            holiday_rect = holiday_surface.get_rect(
                centerx=self.time_rect.centerx,
                bottom=self.time_rect.bottom - margin
            )
            self.screen.blit(holiday_surface, holiday_rect)

    def draw_weather_tile(self):
        """Draw the weather tile with weather icon background."""
        self._draw_rounded_rect(self.weather_rect, self.theme.tile_weather)

        if not self.weather_data:
            self._draw_text("Weather Unavailable", self.font_medium,
                           self.theme.text_secondary, self.weather_rect)
            return

        # Get weather condition for icon
        condition_raw = self.weather_data.get("state", "")

        # Load and draw weather icon as background (semi-transparent)
        icon_size = min(self.weather_rect.width, self.weather_rect.height) - 10
        weather_icon = self._load_weather_icon(condition_raw, (icon_size, icon_size), alpha=100)
        if weather_icon:
            # Center the icon in the tile
            icon_x = self.weather_rect.centerx - icon_size // 2
            icon_y = self.weather_rect.centery - icon_size // 2
            self.screen.blit(weather_icon, (icon_x, icon_y))

        # Temperature (large) - with drop shadow for better visibility over icon
        temp = self.weather_data.get("temperature")
        unit = self.weather_data.get("temperature_unit", "°F")
        if temp is not None:
            temp_str = f"{int(temp)}{unit}"
            # Draw shadow
            self._draw_text(temp_str, self.font_xlarge, (0, 0, 0),
                           self.weather_rect, y_offset=-33)
            # Draw text
            self._draw_text(temp_str, self.font_xlarge, self.theme.text_primary,
                           self.weather_rect, y_offset=-35)

        # Condition
        condition = format_weather_condition(condition_raw)
        # Draw shadow
        self._draw_text(condition, self.font_medium, (0, 0, 0),
                       self.weather_rect, y_offset=27)
        self._draw_text(condition, self.font_medium, self.theme.text_secondary,
                       self.weather_rect, y_offset=25)

        # Humidity
        humidity = self.weather_data.get("humidity")
        if humidity is not None:
            humidity_str = f"Humidity: {humidity}%"
            # Draw shadow
            self._draw_text(humidity_str, self.font_small, (0, 0, 0),
                           self.weather_rect, y_offset=62)
            self._draw_text(humidity_str, self.font_small, self.theme.text_secondary,
                           self.weather_rect, y_offset=60)

        # Draw glowing border when in forecast mode (visual indicator it's a toggle)
        if self.forecast_mode:
            self._draw_glow_border(self.weather_rect)

    def _draw_glow_border(self, rect: pygame.Rect, border_width: int = 3):
        """Draw a glowing border that fades lighter inward around a rounded rect."""
        # Glow color - light blue-purple matching the forecast header
        base_color = (160, 180, 255)

        # Draw multiple layers, getting lighter/more transparent going inward
        for i in range(border_width):
            # Calculate alpha/brightness - outer is brightest, inner fades
            alpha_factor = 1.0 - (i * 0.3)  # 1.0, 0.7, 0.4
            color = tuple(min(255, int(c * alpha_factor + 255 * (1 - alpha_factor) * 0.3)) for c in base_color)

            # Inset rect for each layer
            inset = i
            border_rect = pygame.Rect(
                rect.left + inset,
                rect.top + inset,
                rect.width - 2 * inset,
                rect.height - 2 * inset
            )
            pygame.draw.rect(self.screen, color, border_rect, 1, border_radius=15 - inset)

    def draw_sleep_mode(self):
        """Draw full-screen clock display for sleep mode with dimmed background."""
        # Fill with black background for nighttime
        self.screen.fill((0, 0, 0))

        now = datetime.now()

        # Format time as 12:35 PM (alarm clock style)
        hour = now.hour
        am_pm = "AM" if hour < 12 else "PM"
        if hour == 0:
            hour = 12
        elif hour > 12:
            hour -= 12
        time_str = f"{hour}:{now.strftime('%M')}"

        # Red colors for nighttime viewing (easier on eyes, preserves night vision)
        clock_red = (180, 40, 40)  # Dim red for time
        clock_red_dim = (120, 30, 30)  # Dimmer red for secondary text

        # Draw time centered on screen (large alarm clock style)
        time_surface = self.font_clock.render(time_str, True, clock_red)
        time_rect = time_surface.get_rect(center=(self.width // 2, self.height // 2 - 20))
        self.screen.blit(time_surface, time_rect)

        # Draw AM/PM to the right of time
        ampm_surface = self.font_large.render(am_pm, True, clock_red_dim)
        ampm_rect = ampm_surface.get_rect(left=time_rect.right + 10, centery=time_rect.centery + 30)
        self.screen.blit(ampm_surface, ampm_rect)

        # Draw date below time (smaller, dimmer red)
        date_str = now.strftime("%A, %B %d")
        date_surface = self.font_medium.render(date_str, True, clock_red_dim)
        date_rect = date_surface.get_rect(centerx=self.width // 2, top=time_rect.bottom + 15)
        self.screen.blit(date_surface, date_rect)

        pygame.display.flip()

    def draw_indicator_box(self):
        """Draw the indicator/status box below weather with status icons."""
        if not hasattr(self, 'indicator_rect'):
            return

        self._draw_rounded_rect(self.indicator_rect, self.theme.tile_indicator)

        # Draw mailbox envelope icon on the left side
        # Highlighted (bright) when check_mailbox switch is ON (mail waiting)
        # Dark when switch is OFF (no mail or already checked)
        envelope_char = "✉"  # Envelope Unicode character
        if self.mailbox_check_on:
            # Bright yellow/gold when mail is waiting
            envelope_color = (255, 220, 100)
        else:
            # Dark gray when no mail or already checked
            envelope_color = (80, 80, 80)

        envelope_surface = self.font_symbol_large.render(envelope_char, True, envelope_color)
        envelope_x = self.indicator_rect.left + 8
        envelope_y = self.indicator_rect.centery - envelope_surface.get_height() // 2
        self.screen.blit(envelope_surface, (envelope_x, envelope_y))

        # Store envelope rect for touch detection (make it a bit larger for easier tapping)
        self.mailbox_icon_rect = pygame.Rect(
            envelope_x - 4,
            envelope_y - 4,
            envelope_surface.get_width() + 8,
            envelope_surface.get_height() + 8
        )

        # Draw HA status icon on the right side
        ha_icon = self._get_status_icon("ha", self.ha_connected)
        if ha_icon:
            icon_x = self.indicator_rect.right - ha_icon.get_width() - 8
            icon_y = self.indicator_rect.centery - ha_icon.get_height() // 2
            self.screen.blit(ha_icon, (icon_x, icon_y))

        # Draw Internet status icon to the left of HA icon
        internet_icon = self._get_status_icon("internet", self.internet_connected)
        if internet_icon:
            icon_x = self.indicator_rect.right - internet_icon.get_width() - 8 - (ha_icon.get_width() + 8 if ha_icon else 0)
            icon_y = self.indicator_rect.centery - internet_icon.get_height() // 2
            self.screen.blit(internet_icon, (icon_x, icon_y))

    def _draw_events_wrapped(self, events: List[Dict], font: pygame.font.Font,
                              color: Tuple[int, int, int], rect: pygame.Rect,
                              start_y: int = 0, line_spacing: int = 5,
                              max_y: int = None, show_date: bool = False) -> int:
        """Draw calendar events with word wrapping."""
        y = rect.top + self.padding + start_y
        max_width = rect.width - 2 * self.padding
        bottom_limit = max_y if max_y else rect.bottom - self.padding

        for event in events:
            if y + font.get_height() > bottom_limit:
                break

            summary = event.get("summary", "")
            if show_date:
                prefix = event.get("date_str", "")
            else:
                prefix = event.get("start_time", "") or "All Day"

            # Wrap the event text
            full_text = f"{prefix} - {summary}" if prefix else summary
            wrapped_lines = self._wrap_text(full_text, font, max_width)

            for line in wrapped_lines:
                if y + font.get_height() > bottom_limit:
                    break
                surface = font.render(line, True, color)
                text_rect = surface.get_rect(left=rect.left + self.padding, top=y)
                self.screen.blit(surface, text_rect)
                y += font.get_height() + line_spacing

        return y - rect.top - self.padding - start_y

    def _calc_events_height(self, events: List[Dict], font: pygame.font.Font,
                             max_width: int, show_date: bool = False) -> int:
        """Calculate the height needed to display events with wrapping."""
        total_height = 0
        line_spacing = 5

        for event in events:
            summary = event.get("summary", "")
            if show_date:
                prefix = event.get("date_str", "")
            else:
                prefix = event.get("start_time", "") or "All Day"

            full_text = f"{prefix} - {summary}" if prefix else summary
            wrapped_lines = self._wrap_text(full_text, font, max_width)
            total_height += len(wrapped_lines) * (font.get_height() + line_spacing)

        return total_height

    def draw_today_tile(self):
        """Draw today's calendar events with dynamic upcoming events box."""
        self._draw_rounded_rect(self.today_rect, self.theme.tile_today)

        # Header with calendar icons
        header_height = self._draw_header_with_icons("TODAY", "📅", self.font_medium,
                                                      self.theme.text_primary, self.today_rect,
                                                      icon_color=(255, 200, 100))

        margin = 8
        available_height = self.today_rect.height - header_height - self.padding * 3
        max_width = self.today_rect.width - 2 * self.padding - 2 * margin

        # Calculate height needed for today's events (priority)
        today_events_height = 0
        if self.calendar_today:
            today_events_height = self._calc_events_height(
                self.calendar_today[:6], self.font_xsmall, max_width, show_date=False
            )
        else:
            today_events_height = self.font_xsmall.get_height() + 10  # "No events today"

        # Calculate height needed for upcoming events
        upcoming_header_height = self.font_xsmall.get_height() + self.padding
        min_upcoming_height = 60  # Minimum height for upcoming box
        max_upcoming_height = 200  # Maximum height for upcoming box

        upcoming_events_height = 0
        if self.calendar_upcoming:
            upcoming_events_height = self._calc_events_height(
                self.calendar_upcoming[:7], self.font_xsmall, max_width, show_date=True
            )

        # Desired upcoming box height
        desired_upcoming_height = upcoming_header_height + upcoming_events_height + self.padding * 2
        desired_upcoming_height = max(min_upcoming_height, min(desired_upcoming_height, max_upcoming_height))

        # Check if we have enough space for both
        today_area_needed = today_events_height + 20  # Some padding
        space_for_upcoming = available_height - today_area_needed - margin

        # Adjust upcoming height based on available space (today gets priority)
        if space_for_upcoming < min_upcoming_height:
            # Not enough space, use minimum for upcoming
            upcoming_height = min_upcoming_height
        elif space_for_upcoming >= desired_upcoming_height:
            # Plenty of space, use desired height
            upcoming_height = desired_upcoming_height
        else:
            # Use what's available
            upcoming_height = space_for_upcoming

        # Calculate max_y for today's events (don't overlap upcoming box)
        max_today_y = self.today_rect.bottom - upcoming_height - margin * 2

        # Draw today's events
        if not self.calendar_today:
            self._draw_text("No events today", self.font_xsmall, self.theme.text_secondary,
                           self.today_rect, y_offset=-30)
        else:
            self._draw_events_wrapped(self.calendar_today[:6], self.font_xsmall,
                                      self.theme.text_primary, self.today_rect,
                                      start_y=header_height + 10, max_y=max_today_y)

        # Nested upcoming events box (dynamic height)
        upcoming_rect = pygame.Rect(
            self.today_rect.left + margin,
            self.today_rect.bottom - upcoming_height - margin,
            self.today_rect.width - 2 * margin,
            upcoming_height
        )

        # Draw nested box with slightly lighter color
        self._draw_rounded_rect(upcoming_rect, self.theme.tile_today_upcoming, radius=10)

        # Upcoming header
        up_header_h = self._draw_text("UPCOMING", self.font_xsmall,
                                       self.theme.text_secondary, upcoming_rect, v_align="top")

        # Upcoming events with wrapping (show as many as fit)
        if self.calendar_upcoming:
            self._draw_events_wrapped(self.calendar_upcoming[:7], self.font_xsmall,
                                      self.theme.text_primary, upcoming_rect,
                                      start_y=up_header_h + 5, show_date=True)

    def _get_condition_short(self, condition: str) -> str:
        """Get abbreviated weather condition for forecast display."""
        # Only abbreviate if really needed - we have more space now
        abbreviations = {
            "Thunderstorm": "T-Storm",
            "Snow/Rain Mix": "Snow/Rain",
        }
        return abbreviations.get(condition, condition)

    def _get_temp_color(self, temp: float) -> Tuple[int, int, int]:
        """Get color based on temperature (cold=blue, mild=white, hot=orange/red)."""
        if temp is None:
            return self.theme.text_primary
        if temp <= 32:
            return (100, 180, 255)  # Cold blue
        elif temp <= 50:
            return (150, 200, 255)  # Cool blue
        elif temp <= 65:
            return (200, 230, 200)  # Mild green
        elif temp <= 80:
            return (255, 220, 150)  # Warm yellow
        elif temp <= 90:
            return (255, 180, 100)  # Orange
        else:
            return (255, 130, 100)  # Hot red

    def _get_condition_color(self, condition: str) -> Tuple[int, int, int]:
        """Get accent color based on weather condition."""
        condition_lower = condition.lower()
        if "rain" in condition_lower or "thunder" in condition_lower:
            return (100, 150, 220)  # Blue for rain
        elif "snow" in condition_lower:
            return (200, 220, 255)  # Light blue for snow
        elif "cloud" in condition_lower:
            return (180, 180, 190)  # Gray for clouds
        elif "sun" in condition_lower or "clear" in condition_lower:
            return (255, 220, 100)  # Yellow for sunny
        elif "fog" in condition_lower:
            return (160, 160, 170)  # Gray for fog
        else:
            return self.theme.text_secondary

    def draw_forecast_view(self):
        """Draw 6-day weather forecast in place of Today/Tasks tiles."""
        # Calculate the combined area of Today + Tasks tiles
        forecast_rect = pygame.Rect(
            self.today_rect.left,
            self.today_rect.top,
            self.tasks_rect.right - self.today_rect.left,
            self.tasks_rect.bottom - self.today_rect.top
        )

        # Draw background with gradient effect (darker at edges)
        self._draw_rounded_rect(forecast_rect, self.theme.tile_today)

        # Header - bold with light blue-purple color
        header_text = "6-DAY FORECAST"
        header_color = (160, 180, 255)  # Light blue-purple that fits the color scheme
        header_surface = self.font_medium_bold.render(header_text, True, header_color)
        header_rect = header_surface.get_rect(centerx=forecast_rect.centerx,
                                               top=forecast_rect.top + self.padding)
        self.screen.blit(header_surface, header_rect)

        if not self.forecast_data:
            self._draw_text("Forecast unavailable", self.font_small,
                           self.theme.text_secondary, forecast_rect, y_offset=30)
            return

        # Calculate layout for 6 forecast days (2 rows of 3)
        content_top = header_rect.bottom + 12
        content_height = forecast_rect.bottom - content_top - self.padding
        available_width = forecast_rect.width - 2 * self.padding

        # 2 rows, 3 columns
        cols = 3
        rows = 2
        cell_width = available_width // cols
        cell_height = content_height // rows
        cell_padding = 6

        for i, day in enumerate(self.forecast_data[:6]):
            row = i // cols
            col = i % cols

            cell_x = forecast_rect.left + self.padding + col * cell_width
            cell_y = content_top + row * cell_height

            cell_rect = pygame.Rect(cell_x + cell_padding // 2,
                                    cell_y + cell_padding // 2,
                                    cell_width - cell_padding,
                                    cell_height - cell_padding)

            # Get condition for color theming
            condition_raw = day.get("condition", "")
            condition = format_weather_condition(condition_raw)
            condition_color = self._get_condition_color(condition)

            # Draw cell background (black for better icon contrast)
            pygame.draw.rect(self.screen, (0, 0, 0),
                           cell_rect, border_radius=10)

            # Draw weather icon as cell background (forecast is always daytime)
            icon_size = min(cell_rect.width, cell_rect.height) - 16
            weather_icon = self._load_weather_icon(condition_raw, (icon_size, icon_size),
                                                   is_day=True, alpha=100)
            if weather_icon:
                icon_x = cell_rect.centerx - icon_size // 2
                icon_y = cell_rect.centery - icon_size // 2
                self.screen.blit(weather_icon, (icon_x, icon_y))

            # Draw colored border after icon
            pygame.draw.rect(self.screen, condition_color,
                           cell_rect, 2, border_radius=10)

            # Parse and format date
            date_str = day.get("date", "")
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                day_name = dt.strftime("%a").upper()
                date_display = dt.strftime("%m/%d")
            except:
                day_name = "???"
                date_display = date_str

            # Day name (bold, with condition-based color accent) - with shadow
            shadow_offset = 1
            day_shadow = self.font_small_bold.render(day_name, True, (0, 0, 0))
            day_surface = self.font_small_bold.render(day_name, True, condition_color)
            day_rect = day_surface.get_rect(centerx=cell_rect.centerx,
                                            top=cell_rect.top + 6)
            self.screen.blit(day_shadow, (day_rect.x + shadow_offset, day_rect.y + shadow_offset))
            self.screen.blit(day_surface, day_rect)

            # Date - bold with shadow
            date_shadow = self.font_xsmall_bold.render(date_display, True, (0, 0, 0))
            date_surface = self.font_xsmall_bold.render(date_display, True, self.theme.text_primary)
            date_rect = date_surface.get_rect(centerx=cell_rect.centerx,
                                              top=day_rect.bottom + 1)
            self.screen.blit(date_shadow, (date_rect.x + shadow_offset, date_rect.y + shadow_offset))
            self.screen.blit(date_surface, date_rect)

            # Temperature (high/low) with temperature-based colors
            temp_high = day.get("temperature")
            temp_low = day.get("templow")
            temp_bottom_y = date_rect.bottom + 6  # Track where temp ends for condition placement

            if temp_high is not None:
                high_color = self._get_temp_color(temp_high)
                low_color = self._get_temp_color(temp_low) if temp_low else high_color

                if temp_low is not None:
                    # Draw high temp with shadow (bold)
                    high_str = f"{int(temp_high)}°"
                    high_shadow = self.font_small_bold.render(high_str, True, (0, 0, 0))
                    high_surface = self.font_small_bold.render(high_str, True, high_color)

                    # Draw separator (bold)
                    sep_shadow = self.font_small_bold.render(" / ", True, (0, 0, 0))
                    sep_surface = self.font_small_bold.render(" / ", True, self.theme.text_secondary)

                    # Draw low temp with shadow (bold)
                    low_str = f"{int(temp_low)}°"
                    low_shadow = self.font_small_bold.render(low_str, True, (0, 0, 0))
                    low_surface = self.font_small_bold.render(low_str, True, low_color)

                    # Calculate total width and position
                    total_width = high_surface.get_width() + sep_surface.get_width() + low_surface.get_width()
                    start_x = cell_rect.centerx - total_width // 2
                    temp_y = date_rect.bottom + 6

                    # Draw shadows first
                    self.screen.blit(high_shadow, (start_x + shadow_offset, temp_y + shadow_offset))
                    self.screen.blit(sep_shadow, (start_x + high_surface.get_width() + shadow_offset, temp_y + shadow_offset))
                    self.screen.blit(low_shadow, (start_x + high_surface.get_width() + sep_surface.get_width() + shadow_offset, temp_y + shadow_offset))
                    # Draw text
                    self.screen.blit(high_surface, (start_x, temp_y))
                    self.screen.blit(sep_surface, (start_x + high_surface.get_width(), temp_y))
                    self.screen.blit(low_surface, (start_x + high_surface.get_width() + sep_surface.get_width(), temp_y))
                    temp_bottom_y = temp_y + high_surface.get_height()
                else:
                    temp_str = f"{int(temp_high)}°"
                    temp_shadow = self.font_small_bold.render(temp_str, True, (0, 0, 0))
                    temp_surface = self.font_small_bold.render(temp_str, True, high_color)
                    temp_rect = temp_surface.get_rect(centerx=cell_rect.centerx,
                                                      top=date_rect.bottom + 6)
                    self.screen.blit(temp_shadow, (temp_rect.x + shadow_offset, temp_rect.y + shadow_offset))
                    self.screen.blit(temp_surface, temp_rect)
                    temp_bottom_y = temp_rect.bottom

            # Condition (bold with color) - positioned right below temperature, with shadow
            condition_short = self._get_condition_short(condition)
            cond_shadow = self.font_xsmall_bold.render(condition_short, True, (0, 0, 0))
            cond_surface = self.font_xsmall_bold.render(condition_short, True, condition_color)
            cond_rect = cond_surface.get_rect(centerx=cell_rect.centerx,
                                              top=temp_bottom_y + 4)
            self.screen.blit(cond_shadow, (cond_rect.x + shadow_offset, cond_rect.y + shadow_offset))
            self.screen.blit(cond_surface, cond_rect)

    def _calc_tasks_visible_count(self, items: List[Dict], font: pygame.font.Font,
                                     rect: pygame.Rect, start_y: int, reserve_bottom: int = 0) -> int:
        """Calculate how many tasks can fit in the visible area."""
        y = rect.top + self.padding + start_y
        max_width = rect.width - 2 * self.padding
        checkbox_width = font.size("[ ] ")[0]
        line_spacing = 5
        bottom_limit = rect.bottom - self.padding - reserve_bottom
        count = 0

        for item in items:
            summary = item.get("summary", "")
            wrapped_lines = self._wrap_text(summary, font, max_width - checkbox_width)
            item_height = len(wrapped_lines) * (font.get_height() + line_spacing)

            if y + item_height > bottom_limit:
                break
            y += item_height
            count += 1

        return max(1, count)  # At least 1 task visible

    def draw_tasks_tile(self):
        """Draw the reminders/tasks tile with scroll support."""
        self._draw_rounded_rect(self.tasks_rect, self.theme.tile_tasks)

        # Header with checkmark icons
        header_height = self._draw_header_with_icons("TASKS", "✓", self.font_medium,
                                                      self.theme.text_primary, self.tasks_rect,
                                                      icon_color=(100, 255, 150))

        if not self.task_items:
            self._draw_text("No tasks", self.font_xsmall, self.theme.text_secondary,
                           self.tasks_rect)
            # Clear arrow rects when no tasks
            self.task_arrow_rects = {"up": None, "down": None}
            return

        # Arrow button dimensions
        arrow_height = 30
        arrow_margin = 8
        arrow_font_size = 24

        # Calculate how many tasks fit without arrows first
        visible_without_arrows = self._calc_tasks_visible_count(
            self.task_items, self.font_xsmall, self.tasks_rect,
            start_y=header_height + 10, reserve_bottom=0
        )

        # Determine if we need scrolling at all
        total_tasks = len(self.task_items)
        needs_scrolling = total_tasks > visible_without_arrows

        # Calculate visible count with space reserved for arrows if needed
        if needs_scrolling:
            visible_count = self._calc_tasks_visible_count(
                self.task_items, self.font_xsmall, self.tasks_rect,
                start_y=header_height + 10, reserve_bottom=arrow_height + arrow_margin
            )
        else:
            visible_count = visible_without_arrows

        # Clamp scroll offset to valid range
        max_offset = max(0, total_tasks - visible_count)
        self.task_scroll_offset = max(0, min(self.task_scroll_offset, max_offset))

        # Get the slice of tasks to display
        display_tasks = self.task_items[self.task_scroll_offset:self.task_scroll_offset + visible_count]

        # Draw items with word wrapping
        bottom_reserve = (arrow_height + arrow_margin) if needs_scrolling else 0
        self._draw_tasks_wrapped_scrollable(
            display_tasks, self.font_xsmall, self.theme.text_primary,
            self.tasks_rect, start_y=header_height + 10,
            max_y=self.tasks_rect.bottom - self.padding - bottom_reserve
        )

        # Draw scroll arrows if needed
        if needs_scrolling:
            self._draw_task_scroll_arrows(
                self.tasks_rect, arrow_height, arrow_margin,
                show_up=self.task_scroll_offset > 0,
                show_down=self.task_scroll_offset < max_offset
            )
        else:
            # Clear arrow rects when no scrolling needed
            self.task_arrow_rects = {"up": None, "down": None}

        # Draw confirmation dialog if pending (on top of everything)
        if self.task_confirm_pending:
            self._draw_task_confirm_dialog(self.tasks_rect)

    def _draw_tasks_wrapped_scrollable(self, items: List[Dict], font: pygame.font.Font,
                                        color: Tuple[int, int, int], rect: pygame.Rect,
                                        start_y: int = 0, line_spacing: int = 5,
                                        max_y: int = None) -> int:
        """Draw task items with word wrapping, respecting max_y boundary."""
        y = rect.top + self.padding + start_y
        max_width = rect.width - 2 * self.padding
        checkbox_size = 16  # Size of the checkbox square
        checkbox_padding = 8  # Space after checkbox
        text_indent = checkbox_size + checkbox_padding
        bottom_limit = max_y if max_y else rect.bottom - self.padding

        # Clear previous touch areas
        self.task_touch_areas = []

        for item in items:
            if y + font.get_height() > bottom_limit:
                break

            summary = item.get("summary", "")
            is_completed = item.get("status") != "needs_action"

            # Wrap the summary text (accounting for checkbox width)
            wrapped_lines = self._wrap_text(summary, font, max_width - text_indent)

            # Track the start y for this task's touch area
            task_start_y = y

            for i, line in enumerate(wrapped_lines):
                if y + font.get_height() > bottom_limit:
                    break

                text_x = rect.left + self.padding + text_indent

                if i == 0:
                    # First line - draw checkbox
                    checkbox_x = rect.left + self.padding
                    checkbox_y = y + (font.get_height() - checkbox_size) // 2

                    # Draw checkbox box
                    checkbox_rect = pygame.Rect(checkbox_x, checkbox_y, checkbox_size, checkbox_size)
                    pygame.draw.rect(self.screen, color, checkbox_rect, 2, border_radius=3)

                    # If completed, draw checkmark inside
                    if is_completed:
                        # Draw a simple checkmark
                        check_color = (100, 200, 100)  # Green
                        pygame.draw.line(self.screen, check_color,
                                        (checkbox_x + 3, checkbox_y + checkbox_size // 2),
                                        (checkbox_x + checkbox_size // 3, checkbox_y + checkbox_size - 4), 2)
                        pygame.draw.line(self.screen, check_color,
                                        (checkbox_x + checkbox_size // 3, checkbox_y + checkbox_size - 4),
                                        (checkbox_x + checkbox_size - 3, checkbox_y + 3), 2)

                # Draw text
                surface = font.render(line, True, color)
                text_rect = surface.get_rect(left=text_x, top=y)
                self.screen.blit(surface, text_rect)
                y += font.get_height() + line_spacing

            # Create touch area for this task (full width of tile for easier tapping)
            task_height = y - task_start_y
            touch_rect = pygame.Rect(rect.left, task_start_y, rect.width, task_height)
            self.task_touch_areas.append((touch_rect, item))

        return y - rect.top - self.padding - start_y

    def _draw_task_confirm_dialog(self, rect: pygame.Rect):
        """Draw confirmation dialog for completing a task."""
        if not self.task_confirm_pending:
            return

        task_name = self.task_confirm_pending.get("summary", "this task")

        # Dialog dimensions - positioned at bottom, above scroll arrows
        dialog_height = 80
        dialog_margin = 10
        arrow_area_height = 45  # Space for scroll arrows below

        dialog_rect = pygame.Rect(
            rect.left + dialog_margin,
            rect.bottom - dialog_height - arrow_area_height - dialog_margin,
            rect.width - 2 * dialog_margin,
            dialog_height
        )

        # Draw dialog background (darker, slightly transparent feel)
        pygame.draw.rect(self.screen, (30, 50, 30), dialog_rect, border_radius=10)
        pygame.draw.rect(self.screen, (80, 120, 80), dialog_rect, 2, border_radius=10)

        # Draw prompt text - centered in top portion
        prompt = "Complete task?"
        prompt_surface = self.font_small.render(prompt, True, self.theme.text_primary)
        prompt_rect = prompt_surface.get_rect(centerx=dialog_rect.centerx, top=dialog_rect.top + 12)
        self.screen.blit(prompt_surface, prompt_rect)

        # Button dimensions - with more spacing
        button_size = 36
        button_spacing = 60  # Increased spacing between buttons
        button_y = dialog_rect.top + 12 + prompt_surface.get_height() + 12  # Below text with padding

        # Yes button (green circle with checkmark)
        yes_x = dialog_rect.centerx - button_spacing - button_size // 2
        yes_rect = pygame.Rect(yes_x, button_y, button_size, button_size)
        pygame.draw.circle(self.screen, (50, 150, 50),
                          (yes_rect.centerx, yes_rect.centery), button_size // 2)
        pygame.draw.circle(self.screen, (80, 200, 80),
                          (yes_rect.centerx, yes_rect.centery), button_size // 2, 2)
        # Draw checkmark
        cx, cy = yes_rect.centerx, yes_rect.centery
        pygame.draw.line(self.screen, (255, 255, 255),
                        (cx - 8, cy), (cx - 2, cy + 6), 3)
        pygame.draw.line(self.screen, (255, 255, 255),
                        (cx - 2, cy + 6), (cx + 10, cy - 8), 3)

        # No button (red circle with X)
        no_x = dialog_rect.centerx + button_spacing - button_size // 2
        no_rect = pygame.Rect(no_x, button_y, button_size, button_size)
        pygame.draw.circle(self.screen, (150, 50, 50),
                          (no_rect.centerx, no_rect.centery), button_size // 2)
        pygame.draw.circle(self.screen, (200, 80, 80),
                          (no_rect.centerx, no_rect.centery), button_size // 2, 2)
        # Draw X
        cx, cy = no_rect.centerx, no_rect.centery
        pygame.draw.line(self.screen, (255, 255, 255),
                        (cx - 7, cy - 7), (cx + 7, cy + 7), 3)
        pygame.draw.line(self.screen, (255, 255, 255),
                        (cx + 7, cy - 7), (cx - 7, cy + 7), 3)

        # Store button rects for touch detection
        self.task_confirm_rects["yes"] = yes_rect
        self.task_confirm_rects["no"] = no_rect

    def _draw_task_scroll_arrows(self, rect: pygame.Rect, arrow_height: int,
                                  margin: int, show_up: bool, show_down: bool):
        """Draw up/down scroll arrows at the bottom of the tasks tile."""
        # Arrow styling - darker gray color for visibility without being too bright
        arrow_color = (120, 120, 120)  # Medium gray, darker than white text
        arrow_hover_color = (160, 160, 160)  # Slightly lighter for active state

        # Calculate arrow button positions (side by side at bottom)
        button_width = 60
        button_spacing = 20
        total_width = button_width * 2 + button_spacing
        start_x = rect.centerx - total_width // 2
        button_y = rect.bottom - arrow_height - margin

        # Up arrow (left button)
        up_rect = pygame.Rect(start_x, button_y, button_width, arrow_height)
        # Down arrow (right button)
        down_rect = pygame.Rect(start_x + button_width + button_spacing, button_y,
                                button_width, arrow_height)

        # Store rects for click detection (only if visible)
        self.task_arrow_rects["up"] = up_rect if show_up else None
        self.task_arrow_rects["down"] = down_rect if show_down else None

        # Draw up arrow if scrolled down
        if show_up:
            # Draw button background (subtle)
            pygame.draw.rect(self.screen, (40, 70, 40), up_rect, border_radius=8)
            # Draw arrow character using symbol font
            arrow_surface = self.font_symbol.render("\u25B2", True, arrow_color)
            arrow_rect = arrow_surface.get_rect(center=up_rect.center)
            self.screen.blit(arrow_surface, arrow_rect)

        # Draw down arrow if more tasks below
        if show_down:
            # Draw button background (subtle)
            pygame.draw.rect(self.screen, (40, 70, 40), down_rect, border_radius=8)
            # Draw arrow character using symbol font
            arrow_surface = self.font_symbol.render("\u25BC", True, arrow_color)
            arrow_rect = arrow_surface.get_rect(center=down_rect.center)
            self.screen.blit(arrow_surface, arrow_rect)

    def _handle_touch(self, pos: Tuple[int, int]):
        """Main touch handler - routes to appropriate handler based on touch location."""
        x, y = pos
        current_time = time.time()

        # If in sleep mode, check for double-tap to wake
        if self.sleep_mode:
            time_since_last_tap = current_time - self.last_tap_time
            self.last_tap_time = current_time

            if time_since_last_tap < self.double_tap_threshold:
                # Double-tap detected - exit sleep mode
                self.sleep_mode = False
                self.last_tap_time = 0  # Reset to prevent triple-tap issues
                logger.info("Sleep mode disabled (double-tap)")
            return

        # Check if touch is on time tile (enter sleep mode)
        if self.time_rect.collidepoint(x, y):
            self.sleep_mode = True
            self.last_tap_time = 0  # Reset tap tracking
            logger.info("Sleep mode enabled")
            return

        # Check if touch is on weather tile (toggle forecast mode)
        if self.weather_rect.collidepoint(x, y):
            self.forecast_mode = not self.forecast_mode
            if self.forecast_mode:
                # Load forecast data if not already loaded
                if not self.forecast_data:
                    self.last_forecast_update = 0  # Force update
                logger.info("Forecast mode enabled")
            else:
                logger.info("Forecast mode disabled")
            return

        # Check if touch is on mailbox icon (mark mail as checked)
        if self.mailbox_icon_rect and self.mailbox_icon_rect.collidepoint(x, y):
            if self.mailbox_check_on and self.ha_connected:
                # Turn off the check_mailbox switch to acknowledge
                if self.ha.turn_off_switch(self.mailbox_check_switch):
                    self.mailbox_check_on = False
                    self.mailbox_cleared = True
                    self.mailbox_opened_today = False
                    self.mailbox_opened_time = None
                    self.cache.set("mailbox_cleared", True)
                    self.cache.set("mailbox_opened_today", False)
                    self.cache.set("mailbox_opened_time", None)
                    logger.info("Mailbox acknowledged via touch - switch turned off")
            return

        # Handle task-related touches (only when not in forecast mode)
        if not self.forecast_mode:
            self._handle_task_scroll_touch(pos)

    def _handle_task_scroll_touch(self, pos: Tuple[int, int]):
        """Handle touch/click on task scroll arrows and task items."""
        x, y = pos

        # If confirmation dialog is showing, only handle dialog buttons
        if self.task_confirm_pending:
            yes_rect = self.task_confirm_rects.get("yes")
            no_rect = self.task_confirm_rects.get("no")

            if yes_rect and yes_rect.collidepoint(x, y):
                # Confirm - complete the task
                self._complete_task(self.task_confirm_pending)
                self.task_confirm_pending = None
                self.task_confirm_rects = {"yes": None, "no": None}
            elif no_rect and no_rect.collidepoint(x, y):
                # Cancel - dismiss dialog
                self.task_confirm_pending = None
                self.task_confirm_rects = {"yes": None, "no": None}
            # Ignore all other touches while dialog is showing
            return

        # Check up arrow first
        up_rect = self.task_arrow_rects.get("up")
        if up_rect and up_rect.collidepoint(x, y):
            if self.task_scroll_offset > 0:
                self.task_scroll_offset -= 1
                self.task_last_interaction = time.time()
            return

        # Check down arrow
        down_rect = self.task_arrow_rects.get("down")
        if down_rect and down_rect.collidepoint(x, y):
            self.task_scroll_offset += 1
            self.task_last_interaction = time.time()
            return

        # Check if touch is on a task item - show confirmation dialog
        for touch_rect, task in self.task_touch_areas:
            if touch_rect.collidepoint(x, y):
                # Only show confirmation for incomplete tasks
                if task.get("status") == "needs_action":
                    self.task_confirm_pending = task
                    self.task_last_interaction = time.time()
                return

    def _complete_task(self, task: Dict):
        """Mark a task as completed via Home Assistant (or queue if offline)."""
        task_uid = task.get("uid")
        task_summary = task.get("summary", "Unknown task")

        if not task_uid:
            logger.warning(f"Cannot complete task without UID: {task_summary}")
            return

        # Find which entity this task belongs to
        if not self.task_lists:
            logger.warning("No task lists configured")
            return

        entity_id = self.task_lists[0]

        # Remove task from local list immediately for responsive UI
        self._remove_task_from_local_list(task_uid)

        # Try to complete via HA if connected
        if self.ha_connected:
            success = self.ha.complete_todo_item(entity_id, task_uid)
            if success:
                logger.info(f"Task completed: {task_summary}")
                # Force refresh of tasks on next update cycle
                self.last_tasks_update = 0
                return
            else:
                logger.warning(f"Failed to complete task online, queueing: {task_summary}")

        # Queue the action for later if offline or failed
        self._queue_pending_action("complete_task", {
            "entity_id": entity_id,
            "task_uid": task_uid,
            "task_summary": task_summary
        })
        logger.info(f"Task completion queued (offline): {task_summary}")

    def _remove_task_from_local_list(self, task_uid: str):
        """Remove a task from the local task list for immediate UI update."""
        self.task_items = [t for t in self.task_items if t.get("uid") != task_uid]
        self.cache.set("tasks", self.task_items)

    def _queue_pending_action(self, action_type: str, data: Dict):
        """Add an action to the pending queue for processing when online."""
        action = {
            "type": action_type,
            "data": data,
            "timestamp": time.time()
        }
        self.pending_actions.append(action)
        self.cache.set("pending_actions", self.pending_actions)

    def _process_pending_actions(self):
        """Process any queued actions now that we're back online."""
        if not self.pending_actions:
            return

        logger.info(f"Processing {len(self.pending_actions)} pending actions")
        actions_to_remove = []

        for i, action in enumerate(self.pending_actions):
            action_type = action.get("type")
            data = action.get("data", {})

            success = False
            if action_type == "complete_task":
                entity_id = data.get("entity_id")
                task_uid = data.get("task_uid")
                task_summary = data.get("task_summary", "Unknown")

                if entity_id and task_uid:
                    success = self.ha.complete_todo_item(entity_id, task_uid)
                    if success:
                        logger.info(f"Pending task completed: {task_summary}")
                    else:
                        logger.warning(f"Failed to complete pending task: {task_summary}")

            if success:
                actions_to_remove.append(i)

        # Remove successfully processed actions (in reverse to maintain indices)
        for i in sorted(actions_to_remove, reverse=True):
            self.pending_actions.pop(i)

        self.cache.set("pending_actions", self.pending_actions)

        if self.pending_actions:
            logger.info(f"{len(self.pending_actions)} pending actions remaining")

    def draw_status_bar(self):
        """Draw the status bar with connection status, mailbox status, or quote of the day."""
        self._draw_rounded_rect(self.status_rect, self.theme.tile_status)

        status_text = ""
        status_color = self.theme.text_primary

        # Show connection status messages (priority: HA down > pending actions > Internet down > mailbox > quote)
        if not self.ha_connected:
            pending_count = len(self.pending_actions)
            if pending_count > 0:
                status_text = f"Offline - {pending_count} action(s) queued"
                status_color = (255, 200, 100)  # Orange/yellow for pending
            else:
                status_text = "Home Assistant Offline"
                status_color = (255, 150, 150)  # Light red for offline
        elif self.pending_actions:
            # Connected but still have pending actions (processing)
            status_text = f"Syncing {len(self.pending_actions)} queued action(s)..."
            status_color = (150, 200, 255)  # Light blue for syncing
        elif not self.internet_connected:
            status_text = "Internet Down - HA Limited Functionality"
            status_color = (255, 180, 100)  # Orange for warning
        # Check if mailbox was opened today
        elif self.mailbox_opened_today and self.mailbox_opened_time:
            status_text = f"Mailbox Opened at {self.mailbox_opened_time}"

        # If no status message, show quote of the day (changes once per day)
        if not status_text:
            status_color = self.theme.text_primary
            # Check if day changed and pick new quote
            today_str = str(date.today())
            if self.cache.get("quote_date") != today_str:
                self.daily_quote = random.choice(DAILY_QUOTES)
                self.cache.set("daily_quote", self.daily_quote)
                self.cache.set("quote_date", today_str)
            status_text = self.daily_quote

        self._draw_text(status_text, self.font_small, status_color, self.status_rect)

    def _check_mailbox_opened_today(self):
        """Check if mailbox sensor was 'on' at any point today using HA history."""
        # Reset tracking on new day
        today = date.today()
        if self.mailbox_check_date != today:
            self.mailbox_check_date = today
            self.mailbox_opened_today = False
            self.mailbox_opened_time = None
            self.mailbox_cleared = False
            self.mailbox_check_on = False
            self.cache.set("mailbox_check_date", today.isoformat())
            self.cache.set("mailbox_cleared", False)
            self.cache.set("mailbox_opened_today", False)
            self.cache.set("mailbox_opened_time", None)

        # Always check the current state of the check_mailbox switch for the indicator icon
        if self.mailbox_check_switch:
            switch_state = self.ha.get_state(self.mailbox_check_switch)
            if switch_state:
                self.mailbox_check_on = (switch_state.get("state") == "on")

        # If already cleared by user today, stop all checks - nothing more to do
        if self.mailbox_cleared:
            return

        # Check if user has acknowledged via HA switch (switch OFF = user checked mail)
        # Only clear if mailbox was opened AND switch is now OFF
        if self.mailbox_check_switch and self.mailbox_opened_today and not self.mailbox_check_on:
            logger.info("Check Mailbox switch is off - user acknowledged, clearing notification")
            self.mailbox_cleared = True
            self.mailbox_opened_today = False
            self.mailbox_opened_time = None
            self.cache.set("mailbox_cleared", True)
            self.cache.set("mailbox_opened_today", False)
            self.cache.set("mailbox_opened_time", None)
            return

        # Already detected today, no need to check again
        if self.mailbox_opened_today:
            return

        # Use history API to check if mailbox was opened today
        first_open_time = self.ha.get_sensor_history_today(self.mailbox_entity, "on")
        if first_open_time:
            try:
                open_dt = datetime.fromisoformat(first_open_time.replace("Z", "+00:00"))
                open_local = open_dt.astimezone()
                hour = open_local.hour % 12
                if hour == 0:
                    hour = 12
                self.mailbox_opened_time = f"{hour}:{open_local.strftime('%M %p')}"
                self.mailbox_opened_today = True
                logger.info(f"Mailbox was opened today at {self.mailbox_opened_time}")
            except Exception as e:
                logger.debug(f"Error parsing mailbox history timestamp: {e}")

        # Update cache
        self.cache.set("mailbox_opened_today", self.mailbox_opened_today)
        self.cache.set("mailbox_opened_time", self.mailbox_opened_time)

    def update_data(self):
        """Update data from Home Assistant based on refresh intervals."""
        current_time = time.time()

        # Send WiFi keepalive to prevent connection from going idle
        if current_time - self.last_keepalive > self.keepalive_interval:
            self._send_keepalive()
            self.last_keepalive = current_time

        # Check internet connection status periodically
        if current_time - self.last_internet_check > self.internet_check_interval:
            was_connected = self.internet_connected
            self.internet_connected = self._check_internet_connection()
            self.last_internet_check = current_time

            if was_connected and not self.internet_connected:
                logger.warning("Internet connection lost")
            elif not was_connected and self.internet_connected:
                logger.info("Internet connection restored")

        # Check HA connection status periodically
        if current_time - self.last_ha_check > self.ha_check_interval:
            was_connected = self.ha_connected
            self.ha_connected = self._check_ha_connection()
            self.last_ha_check = current_time

            if was_connected and not self.ha_connected:
                logger.warning("Home Assistant connection lost")
            elif not was_connected and self.ha_connected:
                logger.info("Home Assistant connection restored")
                # Process any pending offline actions
                self._process_pending_actions()
                # Reset update timers to fetch fresh data
                self.last_weather_update = 0
                self.last_tasks_update = 0
                self.last_calendar_update = 0
                self.last_mailbox_update = 0
                self.last_sun_update = 0
                self.last_forecast_update = 0

        # Skip data updates if HA is not connected (keep displaying cached data)
        if not self.ha_connected:
            return

        # Update weather
        if current_time - self.last_weather_update > self.weather_interval:
            if self.weather_entity:
                new_data = self.ha.get_weather(self.weather_entity)
                if new_data:
                    self.weather_data = new_data
                    self.cache.set("weather", new_data)
                    logger.info(f"Weather updated: {new_data.get('temperature')}°")
            self.last_weather_update = current_time

        # Update forecast (less frequently)
        if current_time - self.last_forecast_update > self.forecast_interval:
            if self.weather_entity:
                new_forecast = self.ha.get_weather_forecast(self.weather_entity)
                if new_forecast:
                    self.forecast_data = new_forecast
                    self.cache.set("forecast", new_forecast)
                    logger.info(f"Forecast updated: {len(new_forecast)} days")
            self.last_forecast_update = current_time

        # Update tasks
        if current_time - self.last_tasks_update > self.tasks_interval:
            new_items = []
            for task_entity in self.task_lists:
                items = self.ha.get_todo_items(task_entity)
                new_items.extend(items)
            if new_items or not self.task_items:
                self.task_items = new_items
                self.cache.set("tasks", new_items)
            logger.info(f"Tasks updated: {len(self.task_items)} items")
            self.last_tasks_update = current_time

        # Update calendar
        if current_time - self.last_calendar_update > self.calendar_interval:
            all_events = []
            for cal_entity in self.calendars:
                events = self.ha.get_calendar_events(cal_entity, days=7)
                all_events.extend(events)

            # Sort by start time
            all_events.sort(key=lambda x: x.get("start", ""))

            # Split into today and upcoming
            today_str = date.today().isoformat()
            today_events = []
            upcoming_events = []

            for event in all_events:
                start = event.get("start", "")
                event_date = start[:10] if start else ""

                # Parse time for display
                if "T" in start:
                    try:
                        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        local_dt = dt.astimezone()
                        hour = local_dt.hour % 12
                        if hour == 0:
                            hour = 12
                        event["start_time"] = f"{hour}:{local_dt.strftime('%M %p')}"
                    except:
                        event["start_time"] = ""
                else:
                    event["start_time"] = ""

                # Format date for upcoming display
                if event_date:
                    try:
                        d = datetime.strptime(event_date, "%Y-%m-%d")
                        event["date_str"] = d.strftime("%a %m/%d")
                    except:
                        event["date_str"] = event_date

                if event_date == today_str:
                    today_events.append(event)
                elif event_date > today_str:
                    upcoming_events.append(event)

            self.calendar_today = today_events
            self.calendar_upcoming = upcoming_events[:7]
            self.cache.set("calendar_today", today_events)
            self.cache.set("calendar_upcoming", upcoming_events[:7])
            logger.info(f"Calendar updated: {len(today_events)} today, {len(upcoming_events)} upcoming")
            self.last_calendar_update = current_time

        # Update mailbox sensor
        if current_time - self.last_mailbox_update > self.mailbox_interval:
            if self.mailbox_entity:
                new_data = self.ha.get_binary_sensor(self.mailbox_entity)
                if new_data:
                    self.mailbox_data = new_data
                    self.cache.set("mailbox", new_data)
                    self._check_mailbox_opened_today()
                    logger.info(f"Mailbox: {new_data.get('state')}, opened_today: {self.mailbox_opened_today}")
            self.last_mailbox_update = current_time

        # Update sun data (every 5 minutes)
        if current_time - self.last_sun_update > 300:
            sun_state = self.ha.get_state("sun.sun")
            if sun_state:
                # Store both state and attributes
                self.sun_data = sun_state.get("attributes", {})
                self.sun_data["state"] = sun_state.get("state", "")
                self.cache.set("sun_data", self.sun_data)
            self.last_sun_update = current_time

    def draw_version(self):
        """Draw version number in the lower right corner."""
        version_text = self.font_version.render(VERSION, True, self.theme.text_secondary)

        # Position in lower right corner with small padding
        x = self.width - version_text.get_width() - 5
        y = self.height - version_text.get_height() - 3

        self.screen.blit(version_text, (x, y))

    def draw(self):
        """Draw all display elements."""
        # If in sleep mode, draw the full-screen clock instead
        if self.sleep_mode:
            self.draw_sleep_mode()
            return

        # Clear screen
        self.screen.fill(self.theme.bg)

        # Draw left column tiles (always visible)
        self.draw_time_tile()
        self.draw_weather_tile()
        self.draw_indicator_box()

        # Draw right area - either forecast or Today/Tasks
        if self.forecast_mode:
            self.draw_forecast_view()
        else:
            self.draw_today_tile()
            self.draw_tasks_tile()

        self.draw_status_bar()

        # Draw version in corner
        self.draw_version()

        # Update display
        pygame.display.flip()

    def run(self):
        """Main display loop."""
        logger.info("Starting Pi0 Info Display")

        # Show splash screen
        self._show_splash_screen()

        # Load status icons (needs pygame initialized and layout calculated)
        self._load_status_icons()

        # Initial data load with progress
        self._draw_loading_progress(0.1, "Checking connectivity...")
        time.sleep(0.5)

        # Check internet and HA connection, set initial state
        self.internet_connected = self._check_internet_connection()
        self.last_internet_check = time.time()
        self.ha_connected = self._check_ha_connection()
        self.last_ha_check = time.time()

        if not self.ha_connected:
            logger.warning("Could not connect to Home Assistant - will retry")
            self._draw_loading_progress(0.2, "HA connection failed, using cache...")
        else:
            self._draw_loading_progress(0.2, "Connected to Home Assistant")

        time.sleep(0.3)
        self._draw_loading_progress(0.4, "Loading weather data...")
        self.last_weather_update = 0
        self.update_data()  # This will update weather

        self._draw_loading_progress(0.6, "Loading calendar...")
        self.last_calendar_update = 0
        self.update_data()  # Calendar

        self._draw_loading_progress(0.8, "Loading tasks...")
        self.last_tasks_update = 0
        self.update_data()  # Tasks

        # Connect to MQTT broker
        if self.mqtt_client:
            self._draw_loading_progress(0.9, "Connecting to MQTT...")
            if self.mqtt_client.connect():
                logger.info("MQTT connected successfully")
            else:
                logger.warning("MQTT connection failed - will continue without MQTT")

        self._draw_loading_progress(1.0, "Ready!")
        time.sleep(0.5)

        running = True
        while running:
            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_r:
                        # Force refresh on 'r' key
                        self.last_weather_update = 0
                        self.last_tasks_update = 0
                        self.last_calendar_update = 0
                        self.last_mailbox_update = 0
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    # Handle mouse click events
                    if event.button == 1:  # Left click
                        self._handle_touch(event.pos)
                elif event.type == pygame.FINGERDOWN:
                    # Handle touch events (touchscreen sends FINGERDOWN, not MOUSEBUTTONDOWN)
                    # FINGERDOWN gives normalized coordinates (0-1), convert to screen pixels
                    touch_x = int(event.x * self.width)
                    touch_y = int(event.y * self.height)
                    self._handle_touch((touch_x, touch_y))

            # Auto-reset task scroll after inactivity
            if self.task_scroll_offset > 0:
                if time.time() - self.task_last_interaction > self.task_scroll_reset_delay:
                    self.task_scroll_offset = 0
                    logger.debug("Task scroll auto-reset to top")

            # Update data from Home Assistant
            self.update_data()

            # Draw display
            self.draw()

            # Cap at 1 FPS to save CPU
            self.clock.tick(1)

        # Disconnect MQTT before shutdown
        if self.mqtt_client:
            self.mqtt_client.disconnect()

        pygame.quit()
        logger.info("Display stopped")


def main():
    """Entry point."""
    # Determine config path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")

    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    display = Pi0Display(config_path)
    display.run()


if __name__ == "__main__":
    main()
