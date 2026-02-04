# Home Assistant Setup Guide

This guide covers the Home Assistant configuration required before installing the Pi0 Info Display.

## Prerequisites

- Home Assistant instance running on your network
- Network access from your Raspberry Pi to Home Assistant
- Administrator access to Home Assistant

---

## Required Integrations

### 1. Long-Lived Access Token

The display uses the Home Assistant REST API and requires an access token.

1. Log into Home Assistant
2. Click your username (bottom-left corner)
3. Scroll to **Long-Lived Access Tokens**
4. Click **Create Token**
5. Name it (e.g., "Pi0 Display")
6. **Copy and save the token immediately** - it won't be shown again

Add this token to your `config.json`:
```json
{
    "home_assistant": {
        "url": "http://YOUR_HA_IP:8123",
        "token": "YOUR_TOKEN_HERE"
    }
}
```

---

### 2. Weather Integration (Required)

The display uses weather data for the Weather tile.

**Recommended: Met.no**
1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Meteorologisk institutt (Met.no)"
3. Configure with your location coordinates
4. The entity will be named `weather.forecast_home` (default)

**Alternative: Any Weather Integration**
- OpenWeatherMap, AccuWeather, etc. will work
- Update `entities.weather` in config.json with your entity ID

---

### 3. Sun Integration (Required)

Used for sunrise/sunset times to display morning/afternoon/night images.

The Sun integration is **enabled by default** in Home Assistant. Verify it exists:
1. Go to **Developer Tools → States**
2. Search for `sun.sun`
3. Should show `above_horizon` or `below_horizon` with sunrise/sunset attributes

If missing, add to `configuration.yaml`:
```yaml
sun:
```

---

### 4. Calendar Integration (Required)

The display shows today's events and upcoming events.

**Option A: Local Calendar**
1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Local Calendar"
3. Create a calendar (e.g., "Personal")

**Option B: Google Calendar**
1. Add the Google Calendar integration
2. Follow OAuth setup instructions
3. Select calendars to import

**Option C: CalDAV (iCloud, Nextcloud, etc.)**
1. Add the CalDAV integration
2. Configure with your provider's URL and credentials

Update config.json with your calendar entities:
```json
{
    "entities": {
        "calendars": [
            "calendar.personal",
            "calendar.work",
            "calendar.family"
        ]
    }
}
```

---

### 5. Todo/Task List Integration (Required)

The display shows tasks from Home Assistant todo lists.

**Option A: Local To-do**
1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Local To-do"
3. Create a list (e.g., "Shopping List")

**Option B: Google Tasks**
- Included with Google Calendar integration

**Option C: CalDAV Reminders**
- Note: CalDAV reminders (iCloud, etc.) are **read-only** in Home Assistant

Update config.json:
```json
{
    "entities": {
        "task_lists": [
            "todo.shopping_list"
        ]
    }
}
```

---

## Optional Integrations

### 6. MQTT Broker (Optional - For Device Monitoring)

MQTT enables the display to publish system stats (CPU, memory, temperature) to Home Assistant and receive remote commands.

#### Step 1: Install MQTT Broker

**Option A: Mosquitto Add-on (Recommended)**
1. Go to **Settings → Add-ons → Add-on Store**
2. Search for "Mosquitto broker"
3. Click **Install**, then **Start**
4. Go to the add-on's **Configuration** tab
5. Add a user:
```yaml
logins:
  - username: mqtt_user
    password: your_secure_password
```
6. Restart the add-on

**Option B: External Broker**
- Use any MQTT broker on your network
- Note the IP, port, and credentials

#### Step 2: Add MQTT Integration
1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "MQTT"
3. Enter broker details:
   - Broker: `localhost` (if using add-on) or broker IP
   - Port: `1883`
   - Username/Password: as configured above

#### Step 3: Configure Display
Update config.json:
```json
{
    "mqtt": {
        "enabled": true,
        "broker": "YOUR_HA_IP",
        "port": 1883,
        "username": "mqtt_user",
        "password": "your_secure_password",
        "client_id": "pi0display",
        "base_topic": "pi0display",
        "discovery_prefix": "homeassistant"
    }
}
```

#### MQTT Features
When enabled, the display publishes:
- CPU usage percentage
- Memory usage percentage
- CPU temperature
- WiFi IP address
- Ethernet IP address
- Online/offline status

And provides buttons in Home Assistant for:
- Restart display service
- Reboot Pi

---

### 7. Mailbox Sensor (Optional)

Display shows "Mail arrived at [time]" in the status bar when mailbox is opened.

#### Requirements
- A binary sensor that detects mailbox door open/close
- Common options: Zigbee door sensor, Z-Wave sensor, ESPHome DIY sensor

#### Configuration
1. Add your sensor to Home Assistant
2. Note the entity ID (e.g., `binary_sensor.mailbox_door`)
3. Update config.json:
```json
{
    "entities": {
        "mailbox": "binary_sensor.mailbox_door"
    }
}
```

#### Optional: Mailbox Check Reset
Create a helper to reset the "check mailbox" reminder:
1. Go to **Settings → Devices & Services → Helpers**
2. Click **Create Helper → Toggle**
3. Name it "Check Mailbox"
4. Entity ID will be `input_boolean.check_mailbox`
5. Add to config.json:
```json
{
    "entities": {
        "mailbox_check": "input_boolean.check_mailbox"
    }
}
```

---

## Verifying Your Setup

### Check Entity IDs
1. Go to **Developer Tools → States**
2. Verify each entity exists:
   - `weather.forecast_home` (or your weather entity)
   - `sun.sun`
   - Your calendar entities
   - Your todo entities

### Test API Access
From your Pi (or any machine on the network):
```bash
curl -X GET \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  http://YOUR_HA_IP:8123/api/states/sun.sun
```

Should return JSON with sun state data.

---

## Troubleshooting

### "Entity not found" errors
- Double-check entity IDs in Developer Tools → States
- Entity IDs are case-sensitive
- Update config.json with correct entity IDs

### Weather not updating
- Verify weather integration is configured
- Check that the entity has `temperature` attribute
- Some integrations require API keys

### Calendars empty
- Verify calendar integration is working in Home Assistant
- Check calendar has events for today/upcoming
- CalDAV may need re-authentication periodically

### MQTT not connecting
- Verify broker is running: `sudo systemctl status mosquitto`
- Check credentials match between HA and config.json
- Ensure port 1883 is not blocked by firewall
- Test with: `mosquitto_pub -h YOUR_HA_IP -u user -P pass -t test -m "hello"`

### Token expired or invalid
- Create a new Long-Lived Access Token
- Tokens do not expire by default, but can be revoked
- Update config.json with new token

---

## Minimum Working Configuration

At minimum, you need:
1. ✅ Long-Lived Access Token
2. ✅ Weather integration (`weather.*`)
3. ✅ Sun integration (`sun.sun`) - usually default
4. ✅ At least one calendar (`calendar.*`)
5. ✅ At least one todo list (`todo.*`)

Optional but recommended:
- ⬜ MQTT for device monitoring
- ⬜ Mailbox sensor for status bar
