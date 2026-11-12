"""Data collectors. Each function returns plain dicts/lists and never raises."""

from .battery_health import compute_wear, get_battery_health, wear_level
from .bios import get_bios_info, redact
from .disk_health import assess_disk, get_disk_health
from .hardware import (
    collect_hardware,
    get_battery_info,
    get_cpu_info,
    get_gpu_info,
    get_memory_info,
    get_os_info,
    get_storage_info,
    get_system_info,
)
from .network import (
    get_current_wifi,
    get_network_interfaces,
    get_saved_wifi,
    latency_level,
    ping_host,
    run_speed_test,
)
from .temperature import get_temperatures, temp_level

__all__ = [
    "assess_disk",
    "collect_hardware",
    "compute_wear",
    "get_battery_health",
    "get_battery_info",
    "get_bios_info",
    "get_cpu_info",
    "get_current_wifi",
    "get_disk_health",
    "get_gpu_info",
    "get_memory_info",
    "get_network_interfaces",
    "get_os_info",
    "get_saved_wifi",
    "get_storage_info",
    "get_system_info",
    "get_temperatures",
    "latency_level",
    "ping_host",
    "redact",
    "run_speed_test",
    "temp_level",
    "wear_level",
]
