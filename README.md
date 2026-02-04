# Pi0 Info Display

A lightweight Home Assistant dashboard designed for Raspberry Pi Zero 2 W with a 7" touchscreen display.

![Pi0 Info Display](images/Loading.png)

## Features

- **Real-time Home Assistant Integration** - Weather, calendars, tasks, and sensors
- **Optimized for Pi Zero 2 W** - Runs smoothly on 512MB RAM using Pygame (no browser needed)
- **Multiple Layout Modes** - Horizontal, horizontal-alt, and vertical orientations
- **Time-of-Day Themes** - Dynamic background images based on sunrise/sunset
- **Touch Interface** - Task scrolling, interactive elements
- **MQTT Support** - Optional device monitoring and remote control via Home Assistant
- **Offline Resilience** - Data caching prevents blank screens during connectivity issues
- **US Holiday Display** - Shows federal and national holidays

## Display Tiles

| Tile | Description |
|------|-------------|
| **Time** | Large clock with time-of-day image, date, and holiday indicator |
| **Weather** | Current temperature, conditions, humidity from Home Assistant |
| **Today** | Calendar events for today with dynamic "Upcoming" section |
| **Tasks** | Todo list items with scrolling support |
| **Status Bar** | Mailbox sensor status or daily inspirational quote |
| **Indicator** | Home Assistant and internet connectivity status |

## Hardware Requirements

- Raspberry Pi Zero 2 W (or newer Pi with more RAM)
- 7" Display (tested with 1024x600 IPS touchscreen)
- HDMI connection to display
- WiFi network connectivity
- Home Assistant instance on your network

## Software Requirements

- Raspberry Pi OS (Lite recommended)
- Python 3.9+
- Pygame
- Home Assistant with REST API access

## Before You Begin

**Important:** Configure Home Assistant before installing the display.

See the **[Home Assistant Setup Guide](SETUP.md)** for detailed instructions on:
- Creating a Long-Lived Access Token
- Setting up required integrations (Weather, Calendar, Todo)
- Optional MQTT broker configuration for device monitoring
- Optional mailbox sensor setup

## Quick Start

### 1. Clone the Repository

```bash
cd ~
git clone https://github.com/jkomilanowicz-lab/PieZero-HA-Display.git pi0display
cd pi0display
```

### 2. Install Dependencies

```bash
sudo apt update
sudo apt install -y python3-pygame python3-pip
pip3 install paho-mqtt  # Optional: for MQTT support
```

### 3. Configure Home Assistant

Follow the **[Home Assistant Setup Guide](SETUP.md)** to configure required integrations.

At minimum, you need:
- Long-Lived Access Token
- Weather integration (e.g., Met.no)
- At least one calendar
- At least one todo list

### 4. Create Configuration

```bash
cp config.example.json config.json
nano config.json
```

Edit the configuration with your values:

```json
{
    "home_assistant": {
        "url": "http://YOUR_HA_IP:8123",
        "token": "YOUR_LONG_LIVED_ACCESS_TOKEN"
    }
}
```

See [Configuration Guide](#configuration) for all options.

### 5. Test the Display

```bash
python3 display.py
```

Press `ESC` to exit.

### 6. Setup Auto-Start Service

```bash
sudo nano /etc/systemd/system/pi0display.service
```

Add the following content:

```ini
[Unit]
Description=Pi0 Info Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/pi0display
ExecStart=/usr/bin/python3 /home/pi/pi0display/display.py
Restart=always
RestartSec=10
Environment=SDL_VIDEODRIVER=kmsdrm

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pi0display.service
sudo systemctl start pi0display.service
```

## Configuration

### config.json Structure

```json
{
    "home_assistant": {
        "url": "http://192.168.1.x:8123",
        "token": "your_long_lived_access_token"
    },
    "mqtt": {
        "enabled": false,
        "broker": "192.168.1.x",
        "port": 1883,
        "username": "mqtt_user",
        "password": "mqtt_password"
    },
    "network": {
        "keepalive_target": "192.168.1.1",
        "keepalive_port": 53,
        "internet_check_host": "8.8.8.8",
        "internet_check_port": 53
    },
    "display": {
        "width": 1024,
        "height": 600,
        "fullscreen": true,
        "layout": "horizontal"
    },
    "entities": {
        "weather": "weather.forecast_home",
        "task_lists": ["todo.shopping_list"],
        "calendars": ["calendar.personal"],
        "mailbox": "binary_sensor.mailbox_door"
    },
    "refresh_interval": {
        "weather_seconds": 300,
        "tasks_seconds": 60,
        "calendar_seconds": 300
    },
    "theme": {
        "background": "#1a1a2e",
        "accent": "#e94560",
        "text_primary": "#eaeaea"
    }
}
```

### Home Assistant Entities

The display requires these Home Assistant integrations:

| Entity Type | Example | Purpose |
|-------------|---------|---------|
| Weather | `weather.forecast_home` | Temperature, conditions (Met.no recommended) |
| Calendar | `calendar.personal` | Today's events and upcoming |
| Todo | `todo.shopping_list` | Task list items |
| Sun | `sun.sun` | Sunrise/sunset for time-of-day images |
| Binary Sensor | `binary_sensor.mailbox_door` | Optional mailbox status |

### Layout Modes

Change layout via SSH:

```bash
python3 set_layout.py horizontal      # Time/Weather left, Today center, Tasks right
python3 set_layout.py horizontal-alt  # Time/Weather left, Today/Tasks stacked right
python3 set_layout.py vertical        # All tiles stacked (portrait mode)
sudo systemctl restart pi0display.service
```

## MQTT Integration (Optional)

Enable MQTT for device monitoring in Home Assistant:

1. Set `mqtt.enabled: true` in config.json
2. Configure broker credentials
3. Restart the service

MQTT provides:
- CPU/memory usage sensors
- CPU temperature
- WiFi/Ethernet IP addresses
- Remote restart buttons
- Online/offline status

## Troubleshooting

### Display won't start

```bash
# Check service status
sudo systemctl status pi0display.service

# View logs
sudo journalctl -u pi0display.service -n 50

# Test manually
python3 display.py
```

### No data showing

1. Verify Home Assistant URL is reachable from the Pi
2. Check the access token is valid
3. Ensure entities exist in Home Assistant
4. Check `logs/pi0display.log` for errors

### Touch not working

Ensure your display's touch driver is installed. For most USB touch displays:

```bash
sudo apt install xserver-xorg-input-evdev
```

## File Structure

```
pi0display/
├── display.py          # Main application
├── ha_api.py           # Home Assistant API client
├── mqtt_client.py      # MQTT client (optional)
├── version.py          # Version information
├── set_layout.py       # Layout switching utility
├── config.json         # Your configuration (create from example)
├── config.example.json # Configuration template
├── us_holidays.json    # US federal/national holidays
├── images/             # Display images
│   ├── Loading.png     # Splash screen
│   ├── Morning.png     # Morning background
│   ├── afternoon.png   # Afternoon background
│   ├── night.png       # Night background
│   └── logos/          # Status icons
├── Poppins-Regular.ttf # Primary font
└── seguisym.ttf        # Symbol font
```

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE.md) file for details.

## Acknowledgments

- Inspired by [Pi-Dashboard](https://github.com/TechTalkies/Pi-Dashboard) by TechTalkies - the original concept that started it all
- Weather icons adapted from [WeatherPi_TFT](https://github.com/LoveBootCaptain/WeatherPi_TFT) by LoveBootCaptain
- [Poppins](https://fonts.google.com/specimen/Poppins) font by Indian Type Foundry
- Built for integration with [Home Assistant](https://www.home-assistant.io/)
- Developed with assistance from [Claude Code](https://claude.ai/claude-code) by Anthropic
