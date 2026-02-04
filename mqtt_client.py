"""
Pi0 Info Display - MQTT Client for Home Assistant Integration

Publishes device state and system metrics to MQTT broker.
Uses MQTT Discovery for automatic sensor configuration in Home Assistant.
"""

import json
import logging
import os
import ssl
import subprocess
import threading
import time
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

# Check if paho-mqtt is available
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False
    logger.warning("paho-mqtt not installed - MQTT features disabled")


class MQTTClient:
    """MQTT client for publishing device state and metrics to Home Assistant."""

    def __init__(self, config: Dict[str, Any], get_state_callback: Optional[Callable] = None):
        """
        Initialize MQTT client.

        Args:
            config: MQTT configuration dictionary
            get_state_callback: Optional callback to get current device state
        """
        self.enabled = config.get("enabled", False) and MQTT_AVAILABLE
        if not self.enabled:
            logger.info("MQTT disabled or paho-mqtt not available")
            return

        self.broker = config.get("broker", "localhost")
        self.port = config.get("port", 8883)  # Default to TLS port
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.client_id = config.get("client_id", "pi0display")
        self.base_topic = config.get("base_topic", "pi0display")
        self.discovery_prefix = config.get("discovery_prefix", "homeassistant")
        self.publish_interval = config.get("publish_interval", 30)

        # TLS settings
        self.use_tls = config.get("use_tls", True)
        self.ca_cert = config.get("ca_cert", "")  # Path to CA certificate (optional)
        self.verify_ssl = config.get("verify_ssl", False)  # Set False for self-signed certs

        self.get_state_callback = get_state_callback
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self._stop_event = threading.Event()
        self._publish_thread: Optional[threading.Thread] = None

        # Device info for MQTT Discovery
        self.device_info = {
            "identifiers": [self.client_id],
            "name": "Pi0 Info Display",
            "model": "Raspberry Pi Zero 2 W",
            "manufacturer": "PieZero Community",
            "sw_version": self._get_version()
        }

    def _get_version(self) -> str:
        """Get application version."""
        try:
            from version import VERSION
            return VERSION
        except ImportError:
            return "unknown"

    def connect(self) -> bool:
        """Connect to MQTT broker."""
        if not self.enabled:
            return False

        try:
            # Create client with callback API version
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id
            )

            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # Set credentials if provided
            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)

            # Configure TLS if enabled
            if self.use_tls:
                if self.ca_cert and os.path.exists(self.ca_cert):
                    # Use provided CA certificate
                    self.client.tls_set(
                        ca_certs=self.ca_cert,
                        cert_reqs=ssl.CERT_REQUIRED if self.verify_ssl else ssl.CERT_NONE
                    )
                else:
                    # Use system CA or skip verification for self-signed
                    self.client.tls_set(cert_reqs=ssl.CERT_NONE if not self.verify_ssl else ssl.CERT_REQUIRED)

                if not self.verify_ssl:
                    self.client.tls_insecure_set(True)
                logger.info("MQTT TLS enabled")

            # Set last will (offline status)
            self.client.will_set(
                f"{self.base_topic}/status",
                payload="offline",
                qos=1,
                retain=True
            )

            # Connect
            logger.info(f"Connecting to MQTT broker at {self.broker}:{self.port}")
            self.client.connect(self.broker, self.port, keepalive=60)

            # Start network loop in background thread
            self.client.loop_start()

            # Wait briefly for connection
            time.sleep(1)

            return self.connected

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Callback when connected to broker."""
        if reason_code == 0:
            self.connected = True
            logger.info("Connected to MQTT broker")

            # Publish online status
            self.client.publish(
                f"{self.base_topic}/status",
                payload="online",
                qos=1,
                retain=True
            )

            # Configure MQTT Discovery
            self._setup_discovery()

            # Start publishing thread
            self._start_publish_thread()
        else:
            logger.error(f"MQTT connection failed with code: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        """Callback when disconnected from broker."""
        self.connected = False
        logger.warning(f"Disconnected from MQTT broker (code: {reason_code})")

    def _on_message(self, client, userdata, message):
        """Callback when message received - handles commands."""
        topic = message.topic
        payload = message.payload.decode('utf-8').strip()
        logger.info(f"MQTT command received: {topic} = {payload}")

        # Handle commands
        if topic == f"{self.base_topic}/command":
            self._handle_command(payload)

    def _handle_command(self, command: str):
        """Execute received command."""
        if command == "restart_service":
            logger.info("Executing: Restart display service")
            # Run in separate thread to allow MQTT to respond
            threading.Thread(target=self._restart_service, daemon=True).start()

        elif command == "reboot":
            logger.info("Executing: Reboot device")
            # Run in separate thread to allow MQTT to respond
            threading.Thread(target=self._reboot_device, daemon=True).start()

        else:
            logger.warning(f"Unknown command: {command}")

    def _restart_service(self):
        """Restart the pi0display service."""
        import time
        time.sleep(1)  # Brief delay to allow MQTT message to complete
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'pi0display.service'], check=True)
        except Exception as e:
            logger.error(f"Failed to restart service: {e}")

    def _reboot_device(self):
        """Reboot the entire device."""
        import time
        time.sleep(1)  # Brief delay to allow MQTT message to complete
        try:
            subprocess.run(['sudo', 'reboot'], check=True)
        except Exception as e:
            logger.error(f"Failed to reboot: {e}")

    def _setup_discovery(self):
        """Configure MQTT Discovery for Home Assistant auto-configuration."""
        sensors = [
            {
                "id": "display_mode",
                "name": "Display Mode",
                "icon": "mdi:monitor",
                "value_template": "{{ value_json.mode }}"
            },
            {
                "id": "cpu_usage",
                "name": "CPU Usage",
                "icon": "mdi:cpu-64-bit",
                "unit": "%",
                "value_template": "{{ value_json.cpu_percent }}"
            },
            {
                "id": "memory_usage",
                "name": "Memory Usage",
                "icon": "mdi:memory",
                "unit": "%",
                "value_template": "{{ value_json.memory_percent }}"
            },
            {
                "id": "memory_used",
                "name": "Memory Used",
                "icon": "mdi:memory",
                "unit": "MB",
                "value_template": "{{ value_json.memory_used_mb }}"
            },
            {
                "id": "cpu_temperature",
                "name": "CPU Temperature",
                "icon": "mdi:thermometer",
                "unit": "°C",
                "device_class": "temperature",
                "value_template": "{{ value_json.cpu_temp }}"
            },
            {
                "id": "uptime",
                "name": "Uptime",
                "icon": "mdi:clock-outline",
                "unit": "min",
                "value_template": "{{ value_json.uptime_minutes }}"
            },
            {
                "id": "wifi_ssid",
                "name": "WiFi SSID",
                "icon": "mdi:wifi",
                "value_template": "{{ value_json.wifi_ssid }}"
            },
            {
                "id": "wifi_ip",
                "name": "WiFi IP",
                "icon": "mdi:ip-network",
                "value_template": "{{ value_json.wifi_ip }}"
            },
            {
                "id": "ethernet_ip",
                "name": "Ethernet IP",
                "icon": "mdi:ethernet",
                "value_template": "{{ value_json.ethernet_ip }}"
            }
        ]

        for sensor in sensors:
            config_topic = f"{self.discovery_prefix}/sensor/{self.client_id}/{sensor['id']}/config"
            config_payload = {
                "name": sensor["name"],
                "unique_id": f"{self.client_id}_{sensor['id']}",
                "state_topic": f"{self.base_topic}/state",
                "value_template": sensor["value_template"],
                "icon": sensor.get("icon"),
                "device": self.device_info,
                "availability_topic": f"{self.base_topic}/status"
            }

            if "unit" in sensor:
                config_payload["unit_of_measurement"] = sensor["unit"]
            if "device_class" in sensor:
                config_payload["device_class"] = sensor["device_class"]

            self.client.publish(
                config_topic,
                payload=json.dumps(config_payload),
                qos=1,
                retain=True
            )
            logger.debug(f"Published discovery config for {sensor['id']}")

        # Setup log sensors
        log_sensors = [
            {
                "id": "last_event",
                "name": "Last Event",
                "icon": "mdi:message-text",
                "topic": "last_event"
            },
            {
                "id": "last_error",
                "name": "Last Error",
                "icon": "mdi:alert-circle",
                "topic": "last_error"
            }
        ]

        for log_sensor in log_sensors:
            log_config = {
                "name": log_sensor["name"],
                "unique_id": f"{self.client_id}_{log_sensor['id']}",
                "state_topic": f"{self.base_topic}/{log_sensor['topic']}",
                "icon": log_sensor["icon"],
                "device": self.device_info,
                "availability_topic": f"{self.base_topic}/status"
            }
            self.client.publish(
                f"{self.discovery_prefix}/sensor/{self.client_id}/{log_sensor['id']}/config",
                payload=json.dumps(log_config),
                qos=1,
                retain=True
            )

        # Setup button entities for device control
        buttons = [
            {
                "id": "restart_service",
                "name": "Restart Service",
                "icon": "mdi:restart",
                "payload": "restart_service"
            },
            {
                "id": "reboot_device",
                "name": "Reboot Device",
                "icon": "mdi:restart-alert",
                "payload": "reboot"
            }
        ]

        for button in buttons:
            button_config = {
                "name": button["name"],
                "unique_id": f"{self.client_id}_{button['id']}",
                "command_topic": f"{self.base_topic}/command",
                "payload_press": button["payload"],
                "icon": button["icon"],
                "device": self.device_info,
                "availability_topic": f"{self.base_topic}/status"
            }
            self.client.publish(
                f"{self.discovery_prefix}/button/{self.client_id}/{button['id']}/config",
                payload=json.dumps(button_config),
                qos=1,
                retain=True
            )

        # Subscribe to command topic
        self.client.subscribe(f"{self.base_topic}/command", qos=1)
        logger.info("Subscribed to command topic")

        logger.info("MQTT Discovery configuration published")

    def _start_publish_thread(self):
        """Start background thread for periodic publishing."""
        if self._publish_thread and self._publish_thread.is_alive():
            return

        self._stop_event.clear()
        self._publish_thread = threading.Thread(target=self._publish_loop, daemon=True)
        self._publish_thread.start()
        logger.info("MQTT publish thread started")

    def _publish_loop(self):
        """Background loop to publish device state periodically."""
        while not self._stop_event.is_set():
            if self.connected:
                try:
                    self.publish_state()
                    self.publish_logs()
                except Exception as e:
                    logger.error(f"Error publishing MQTT state: {e}")

            self._stop_event.wait(self.publish_interval)

    def publish_state(self):
        """Publish current device state and metrics."""
        if not self.connected or not self.client:
            return

        state = self._get_system_state()

        self.client.publish(
            f"{self.base_topic}/state",
            payload=json.dumps(state),
            qos=0,
            retain=False
        )
        logger.debug(f"Published state: {state}")

    def _get_system_state(self) -> Dict[str, Any]:
        """Gather current system state and metrics."""
        state = {
            "mode": "sleep" if self._is_sleep_mode() else "interactive",
            "cpu_percent": self._get_cpu_usage(),
            "memory_percent": self._get_memory_percent(),
            "memory_used_mb": self._get_memory_used_mb(),
            "cpu_temp": self._get_cpu_temperature(),
            "uptime_minutes": self._get_uptime_minutes(),
            "wifi_ssid": self._get_wifi_ssid(),
            "wifi_ip": self._get_interface_ip("wlan0"),
            "ethernet_ip": self._get_interface_ip("eth0"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        return state

    def _is_sleep_mode(self) -> bool:
        """Check if display is in sleep mode."""
        if self.get_state_callback:
            try:
                return self.get_state_callback("sleep_mode")
            except:
                pass
        return False

    def _get_cpu_usage(self) -> float:
        """Get CPU usage percentage."""
        try:
            # Read from /proc/stat
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            fields = line.split()
            idle = float(fields[4])
            total = sum(float(f) for f in fields[1:8])

            # Store for next calculation
            if not hasattr(self, '_last_cpu'):
                self._last_cpu = (idle, total)
                return 0.0

            last_idle, last_total = self._last_cpu
            idle_delta = idle - last_idle
            total_delta = total - last_total
            self._last_cpu = (idle, total)

            if total_delta == 0:
                return 0.0

            usage = 100.0 * (1.0 - idle_delta / total_delta)
            return round(usage, 1)
        except Exception as e:
            logger.debug(f"Could not get CPU usage: {e}")
            return 0.0

    def _get_memory_percent(self) -> float:
        """Get memory usage percentage."""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()

            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    mem_info[key] = int(parts[1])

            total = mem_info.get('MemTotal', 1)
            available = mem_info.get('MemAvailable', 0)
            used_percent = 100.0 * (1.0 - available / total)
            return round(used_percent, 1)
        except Exception as e:
            logger.debug(f"Could not get memory percent: {e}")
            return 0.0

    def _get_memory_used_mb(self) -> int:
        """Get memory used in MB."""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()

            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    mem_info[key] = int(parts[1])

            total = mem_info.get('MemTotal', 0)
            available = mem_info.get('MemAvailable', 0)
            used_kb = total - available
            return used_kb // 1024
        except Exception as e:
            logger.debug(f"Could not get memory used: {e}")
            return 0

    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature in Celsius."""
        try:
            # Raspberry Pi thermal zone
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
            return round(temp, 1)
        except Exception as e:
            logger.debug(f"Could not get CPU temperature: {e}")
            return 0.0

    def _get_uptime_minutes(self) -> int:
        """Get system uptime in minutes."""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
            return int(uptime_seconds / 60)
        except Exception as e:
            logger.debug(f"Could not get uptime: {e}")
            return 0

    def _get_wifi_ssid(self) -> str:
        """Get the connected WiFi SSID."""
        try:
            result = subprocess.run(
                ['iwgetid', '-r'],
                capture_output=True,
                text=True,
                timeout=5
            )
            ssid = result.stdout.strip()
            return ssid if ssid else "Not connected"
        except Exception as e:
            logger.debug(f"Could not get WiFi SSID: {e}")
            return "Unknown"

    def _get_interface_ip(self, interface: str) -> str:
        """Get IP address for a network interface."""
        try:
            result = subprocess.run(
                ['ip', '-4', 'addr', 'show', interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Parse output for inet line
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('inet '):
                    # Format: inet 192.168.1.200/24 ...
                    ip_cidr = line.split()[1]
                    ip = ip_cidr.split('/')[0]
                    return ip
            return "Not connected"
        except Exception as e:
            logger.debug(f"Could not get IP for {interface}: {e}")
            return "Unknown"

    def publish_logs(self):
        """Publish last event and last error from logs."""
        if not self.connected or not self.client:
            return

        try:
            log_path = os.path.join(os.path.dirname(__file__), "logs", "display.log")
            if not os.path.exists(log_path):
                return

            # Read all lines
            with open(log_path, 'r') as f:
                lines = f.readlines()

            if not lines:
                return

            # Get last event (most recent log line)
            last_event = self._format_log_entry(lines[-1]) if lines else "No events"

            # Find last error or warning
            last_error = "No errors"
            for line in reversed(lines):
                if " - ERROR - " in line or " - WARNING - " in line:
                    last_error = self._format_log_entry(line)
                    break

            # Publish last event
            self.client.publish(
                f"{self.base_topic}/last_event",
                payload=last_event,
                qos=0,
                retain=True
            )

            # Publish last error
            self.client.publish(
                f"{self.base_topic}/last_error",
                payload=last_error,
                qos=0,
                retain=True
            )
        except Exception as e:
            logger.debug(f"Could not publish logs: {e}")

    def _format_log_entry(self, line: str) -> str:
        """Format a log line for display in HA (truncate and clean up)."""
        line = line.strip()
        # Extract just the time and message part
        # Format: 2026-02-03 11:48:01,189 - INFO - Message here
        try:
            parts = line.split(" - ", 2)
            if len(parts) >= 3:
                timestamp = parts[0].split()[1].split(",")[0]  # Get HH:MM:SS
                level = parts[1]
                message = parts[2]
                formatted = f"{timestamp} [{level}] {message}"
            else:
                formatted = line
        except:
            formatted = line

        # Truncate to 250 chars (HA sensor state limit is ~255)
        if len(formatted) > 250:
            formatted = formatted[:247] + "..."

        return formatted

    def publish_full_log(self):
        """Publish full log file content (called on demand)."""
        if not self.connected or not self.client:
            return

        try:
            log_path = os.path.join(os.path.dirname(__file__), "logs", "display.log")
            if not os.path.exists(log_path):
                return

            with open(log_path, 'r') as f:
                log_text = f.read()

            # Split into chunks if needed (MQTT has size limits)
            chunk_size = 4000
            chunks = [log_text[i:i+chunk_size] for i in range(0, len(log_text), chunk_size)]

            for i, chunk in enumerate(chunks):
                self.client.publish(
                    f"{self.base_topic}/logs/full/{i}",
                    payload=chunk,
                    qos=1,
                    retain=True
                )

            self.client.publish(
                f"{self.base_topic}/logs/full/count",
                payload=str(len(chunks)),
                qos=1,
                retain=True
            )

            logger.info(f"Published full log in {len(chunks)} chunks")
        except Exception as e:
            logger.error(f"Could not publish full log: {e}")

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if not self.enabled:
            return

        self._stop_event.set()

        if self._publish_thread:
            self._publish_thread.join(timeout=2)

        if self.client and self.connected:
            # Publish offline status before disconnecting
            self.client.publish(
                f"{self.base_topic}/status",
                payload="offline",
                qos=1,
                retain=True
            )
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("Disconnected from MQTT broker")

        self.connected = False
