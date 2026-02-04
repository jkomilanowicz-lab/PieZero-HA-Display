"""
Home Assistant API Module for Pi0 Info Display
Lightweight HTTP client using only standard library (no requests dependency)
"""

import urllib.request
import urllib.error
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)


class HomeAssistantAPI:
    """Lightweight Home Assistant REST API client."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip('/')
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def _request(self, endpoint: str, method: str = "GET", data: Optional[Dict] = None) -> Optional[Dict]:
        """Make HTTP request to Home Assistant API."""
        url = f"{self.url}{endpoint}"

        try:
            req = urllib.request.Request(url, headers=self.headers, method=method)

            if data:
                req.data = json.dumps(data).encode('utf-8')

            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))

        except urllib.error.HTTPError as e:
            logger.error(f"HTTP Error {e.code} for {endpoint}: {e.reason}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"URL Error for {endpoint}: {e.reason}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

    def get_state(self, entity_id: str) -> Optional[Dict]:
        """Get state of a single entity."""
        return self._request(f"/api/states/{entity_id}")

    def get_weather(self, entity_id: str) -> Optional[Dict]:
        """Get weather data formatted for display."""
        state = self.get_state(entity_id)
        if not state:
            return None

        attrs = state.get("attributes", {})

        # Determine temperature unit
        temp_unit = attrs.get("temperature_unit", "°F")
        if "°" not in temp_unit:
            temp_unit = f"°{temp_unit}"

        return {
            "state": state.get("state", "unknown"),
            "temperature": attrs.get("temperature"),
            "temperature_unit": temp_unit,
            "humidity": attrs.get("humidity"),
            "wind_speed": attrs.get("wind_speed"),
            "wind_speed_unit": attrs.get("wind_speed_unit", "mph"),
            "pressure": attrs.get("pressure"),
            "cloud_coverage": attrs.get("cloud_coverage"),
            "friendly_name": attrs.get("friendly_name", "Weather")
        }

    def get_weather_forecast(self, entity_id: str, forecast_type: str = "daily") -> List[Dict]:
        """Get weather forecast data."""
        payload = {
            "entity_id": entity_id,
            "type": forecast_type
        }

        result = self._request(
            "/api/services/weather/get_forecasts?return_response=true",
            method="POST",
            data=payload
        )

        if not result:
            return []

        service_response = result.get("service_response", {})
        entity_data = service_response.get(entity_id, {})
        forecasts = entity_data.get("forecast", [])

        # Return first 6 days of forecast
        return [
            {
                "date": f.get("datetime", "")[:10],
                "condition": f.get("condition", ""),
                "temperature": f.get("temperature"),
                "templow": f.get("templow"),
                "precipitation_probability": f.get("precipitation_probability"),
                "humidity": f.get("humidity"),
                "wind_speed": f.get("wind_speed")
            }
            for f in forecasts[:6]
        ]

    def get_todo_items(self, entity_id: str, status: str = "needs_action") -> List[Dict]:
        """Get todo items from a todo list entity."""
        payload = {
            "entity_id": entity_id,
            "status": status
        }

        result = self._request(
            "/api/services/todo/get_items?return_response=true",
            method="POST",
            data=payload
        )

        if not result:
            return []

        service_response = result.get("service_response", {})
        entity_data = service_response.get(entity_id, {})
        items = entity_data.get("items", [])

        return [
            {
                "uid": item.get("uid", ""),
                "summary": item.get("summary", ""),
                "status": item.get("status", ""),
                "due": item.get("due"),
                "description": item.get("description")
            }
            for item in items
        ]

    def complete_todo_item(self, entity_id: str, item_uid: str) -> bool:
        """Mark a todo item as completed."""
        payload = {
            "entity_id": entity_id,
            "item": item_uid,
            "status": "completed"
        }

        result = self._request(
            "/api/services/todo/update_item",
            method="POST",
            data=payload
        )

        if result is not None:
            logger.info(f"Completed task: {item_uid}")
            return True
        else:
            logger.error(f"Failed to complete task: {item_uid}")
            return False

    def turn_off_switch(self, entity_id: str) -> bool:
        """Turn off an input_boolean or switch entity."""
        # Determine the correct service domain
        domain = entity_id.split(".")[0] if "." in entity_id else "input_boolean"
        payload = {"entity_id": entity_id}

        result = self._request(
            f"/api/services/{domain}/turn_off",
            method="POST",
            data=payload
        )

        if result is not None:
            logger.info(f"Turned off: {entity_id}")
            return True
        else:
            logger.error(f"Failed to turn off: {entity_id}")
            return False

    def get_calendar_events(self, entity_id: str, days: int = 7) -> List[Dict]:
        """Get upcoming calendar events."""
        now = datetime.now(timezone.utc)
        start = now.strftime("%Y-%m-%dT00:00:00Z")
        end = (now + timedelta(days=days)).strftime("%Y-%m-%dT23:59:59Z")

        result = self._request(f"/api/calendars/{entity_id}?start={start}&end={end}")

        if not result or not isinstance(result, list):
            return []

        events = []
        for event in result:
            start_info = event.get("start", {})
            # Handle both dateTime and date formats
            start_str = start_info.get("dateTime") or start_info.get("date", "")

            events.append({
                "summary": event.get("summary", "No Title"),
                "start": start_str,
                "location": event.get("location"),
                "description": event.get("description")
            })

        return events

    def get_binary_sensor(self, entity_id: str) -> Optional[Dict]:
        """Get binary sensor state with last_changed timestamp."""
        state = self.get_state(entity_id)
        if not state:
            return None

        return {
            "state": state.get("state", "unknown"),
            "last_changed": state.get("last_changed"),
            "friendly_name": state.get("attributes", {}).get("friendly_name", entity_id)
        }

    def get_sensor_history_today(self, entity_id: str, target_state: str = "on") -> Optional[str]:
        """Check if sensor had a specific state today, return first occurrence time."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        result = self._request(f"/api/history/period/{today}?filter_entity_id={entity_id}")
        if not result or not isinstance(result, list) or len(result) == 0:
            return None

        # History returns list of lists, first element is our entity's history
        history = result[0] if result else []
        for entry in history:
            if entry.get("state") == target_state:
                # Return the timestamp of first "on" state
                return entry.get("last_changed")
        return None

    def test_connection(self) -> bool:
        """Test if connection to Home Assistant is working."""
        result = self._request("/api/")
        return result is not None and result.get("message") == "API running."


def format_weather_condition(condition: str) -> str:
    """Convert HA weather condition to display-friendly text."""
    conditions = {
        "clear-night": "Clear",
        "cloudy": "Cloudy",
        "fog": "Foggy",
        "hail": "Hail",
        "lightning": "Lightning",
        "lightning-rainy": "Thunderstorm",
        "partlycloudy": "Partly Cloudy",
        "pouring": "Heavy Rain",
        "rainy": "Rainy",
        "snowy": "Snowy",
        "snowy-rainy": "Snow/Rain Mix",
        "sunny": "Sunny",
        "windy": "Windy",
        "windy-variant": "Windy",
        "exceptional": "Exceptional"
    }
    return conditions.get(condition, condition.replace("-", " ").title())
