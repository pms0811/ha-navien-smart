"""Sensor platform for Navien Smart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NavienDevice
from .const import DOMAIN
from .coordinator import NavienSmartDataUpdateCoordinator


@dataclass(frozen=True, slots=True)
class NavienSmartSensorDescription:
    """Describe one air sensor value."""

    key: str
    name: str
    native_unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT


AIR_SENSOR_DESCRIPTIONS: tuple[NavienSmartSensorDescription, ...] = (
    NavienSmartSensorDescription(
        key="temperature",
        name="온도",
        native_unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
    ),
    NavienSmartSensorDescription(
        key="humidity",
        name="습도",
        native_unit=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
    ),
    NavienSmartSensorDescription(key="pm1Dot0", name="극초미세먼지", native_unit="µg/m³"),
    NavienSmartSensorDescription(key="pm2Dot5", name="초미세먼지", native_unit="µg/m³"),
    NavienSmartSensorDescription(key="pm10", name="미세먼지", native_unit="µg/m³"),
    NavienSmartSensorDescription(key="co2", name="이산화탄소", native_unit="ppm"),
    NavienSmartSensorDescription(key="tvoc", name="휘발성 유기화합물", native_unit="µg/m³"),
    NavienSmartSensorDescription(key="total", name="공기질점수", native_unit="pts"),
    NavienSmartSensorDescription(key="radon", name="라돈", native_unit="Bq/m³"),
)

AIR_MONITOR_NAME = "에어모니터"
AIR_MONITOR_MODEL = "NAA-21DM"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Navien Smart sensor entities."""
    coordinator: NavienSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for device in coordinator.devices:
        air_sensors = device.air_sensors or {}
        entities.extend(
            NavienSmartAirSensor(coordinator, device, description)
            for description in AIR_SENSOR_DESCRIPTIONS
            if description.key in air_sensors
        )
        if device.modes:
            entities.append(NavienSmartOperationStateSensor(coordinator, device))
            entities.append(NavienSmartErrorStateSensor(coordinator, device))
        entities.extend(
            NavienSmartFilterSensor(coordinator, device, index)
            for index, _filter in enumerate(device.filters)
        )
    async_add_entities(entities)


class NavienSmartAirSensor(
    CoordinatorEntity[NavienSmartDataUpdateCoordinator],
    SensorEntity,
):
    """Air quality sensor for a Navien device."""

    def __init__(
        self,
        coordinator: NavienSmartDataUpdateCoordinator,
        device: NavienDevice,
        description: NavienSmartSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.id
        self._description = description
        self._attr_unique_id = f"{device.id}_{description.key}"
        self._attr_name = description.name
        self._attr_native_unit_of_measurement = description.native_unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class

    @property
    def device(self) -> NavienDevice | None:
        """Return the latest device snapshot."""
        return self.coordinator.device_by_id(self._device_id)

    @property
    def native_value(self) -> float | str | None:
        """Return the air sensor value."""
        if self.device is None or self.device.air_sensors is None:
            return None
        value = (self.device.air_sensors.get(self._description.key) or {}).get("value")
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return Navien metadata for the sensor value."""
        if self.device is None or self.device.air_sensors is None:
            return None
        data = self.device.air_sensors.get(self._description.key) or {}
        attrs = {
            key: data.get(key)
            for key in (
                "level",
                "zone_id",
                "update_time",
                "source",
                "air_monitor_support",
                "air_monitor_paired",
                "air_monitor_connected",
                "air_monitor_model_code",
            )
            if data.get(key) not in (None, "")
        }
        profile = self._sensor_profile
        for key, attr_key in (
            ("sourceName", "sensor_configuration"),
            ("modelCode", "sensor_model_code"),
            ("modelName", "sensor_model_name"),
            ("deviceId", "sensor_device_id"),
        ):
            value = profile.get(key)
            if value not in (None, ""):
                attrs[attr_key] = value
        if profile.get("radonSupported") is True:
            attrs["radon_supported"] = True
        return attrs or None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Group all air values under one Home Assistant device."""
        if self.device is None:
            return None
        raw = self.device.raw or {}
        profile = self._sensor_profile
        source = profile.get("source")
        sensor_device_id = str(profile.get("deviceId") or raw.get("deviceId") or self.device.id)
        if source == "external_air_monitor":
            identifiers = {(DOMAIN, f"{self.device.id}_air_monitor_{sensor_device_id}")}
            name = AIR_MONITOR_NAME
            model_code = profile.get("modelCode")
            if profile.get("modelName"):
                model = profile["modelName"]
            elif model_code:
                model = "Air Monitor"
            else:
                model = AIR_MONITOR_MODEL
            if model_code:
                model = f"{model} ({profile['modelCode']})"
            serial_number = sensor_device_id
        else:
            identifiers = {(DOMAIN, self.device.id)}
            name = self.device.name
            model = raw.get("modelDisplayName") or raw.get("modelCode")
            serial_number = str(raw.get("deviceId")) if raw.get("deviceId") else None
        return DeviceInfo(
            identifiers=identifiers,
            manufacturer="KyungDong Navien",
            name=name,
            model=str(model) if model else None,
            serial_number=serial_number,
        )

    @property
    def _sensor_profile(self) -> dict[str, Any]:
        """Return the latest classified sensor profile."""
        if self.device is not None and self.device.sensor_profile:
            return self.device.sensor_profile
        if self.device is not None and self.device.raw:
            profile = self.device.raw.get("sensorProfile")
            if isinstance(profile, dict):
                return profile
        return {}


class NavienSmartOperationStateSensor(
    CoordinatorEntity[NavienSmartDataUpdateCoordinator],
    SensorEntity,
):
    """Human-readable operation state for a Navien ventilation device."""

    _attr_name = "운전 상태"
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: NavienSmartDataUpdateCoordinator,
        device: NavienDevice,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"{device.id}_operation_state"

    @property
    def device(self) -> NavienDevice | None:
        """Return the latest device snapshot."""
        return self.coordinator.device_by_id(self._device_id)

    @property
    def native_value(self) -> str | None:
        """Return operation state text."""
        return self.device.running_name if self.device else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return state metadata useful for diagnostics and automations."""
        device = self.device
        if device is None:
            return None
        attrs = {
            "running": device.running,
            "power": device.power,
            "mode": device.current_mode_key,
            "fan": device.current_fan_key,
            "target_humidity": device.target_humidity,
        }
        return {key: value for key, value in attrs.items() if value is not None} or None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device registry information."""
        if self.device is None:
            return None
        raw = self.device.raw or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            manufacturer="KyungDong Navien",
            name=self.device.name,
            model=str(raw.get("modelDisplayName") or raw.get("modelCode"))
            if raw.get("modelDisplayName") or raw.get("modelCode")
            else None,
            serial_number=str(raw.get("deviceId")) if raw.get("deviceId") else None,
        )


class NavienSmartErrorStateSensor(
    CoordinatorEntity[NavienSmartDataUpdateCoordinator],
    SensorEntity,
):
    """Human-readable error state for a Navien ventilation device."""

    _attr_name = "오류"
    _attr_icon = "mdi:check-circle-outline"

    def __init__(
        self,
        coordinator: NavienSmartDataUpdateCoordinator,
        device: NavienDevice,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.id
        self._attr_unique_id = f"{device.id}_error_state"

    @property
    def device(self) -> NavienDevice | None:
        """Return the latest device snapshot."""
        return self.coordinator.device_by_id(self._device_id)

    @property
    def native_value(self) -> str | None:
        """Return error state text."""
        device = self.device
        if device is None or device.error_code is None:
            return None
        if device.error_code == 0:
            return "문제없음"
        return device.error_text or f"오류 코드 {device.error_code}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return raw error metadata."""
        device = self.device
        if device is None:
            return None
        attrs = {
            "error_code": device.error_code,
            "error_text": device.error_text,
        }
        return {key: value for key, value in attrs.items() if value is not None} or None

    @property
    def icon(self) -> str:
        """Return an icon matching the error state."""
        device = self.device
        if device is not None and device.error_code not in (None, 0):
            return "mdi:alert-circle-outline"
        return "mdi:check-circle-outline"

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device registry information."""
        if self.device is None:
            return None
        raw = self.device.raw or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            manufacturer="KyungDong Navien",
            name=self.device.name,
            model=str(raw.get("modelDisplayName") or raw.get("modelCode"))
            if raw.get("modelDisplayName") or raw.get("modelCode")
            else None,
            serial_number=str(raw.get("deviceId")) if raw.get("deviceId") else None,
        )


class NavienSmartFilterSensor(
    CoordinatorEntity[NavienSmartDataUpdateCoordinator],
    SensorEntity,
):
    """Filter usage sensor for a Navien ventilation device."""

    _attr_icon = "mdi:air-filter"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NavienSmartDataUpdateCoordinator,
        device: NavienDevice,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device.id
        self._index = index
        self._attr_unique_id = f"{device.id}_filter_{index}"
        count = len(device.filters)
        self._attr_name = "필터 사용률" if count == 1 else f"필터 {index + 1} 사용률"

    @property
    def device(self) -> NavienDevice | None:
        """Return the latest device snapshot."""
        return self.coordinator.device_by_id(self._device_id)

    @property
    def native_value(self) -> int | None:
        """Return filter usage percentage."""
        data = self._filter
        if data is None:
            return None
        return data.get("percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return filter metadata."""
        data = self._filter
        if data is None:
            return None
        attrs = {
            "filter_type": data.get("type"),
            "replace_period": data.get("replace_period"),
        }
        return {key: value for key, value in attrs.items() if value is not None} or None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device registry information."""
        if self.device is None:
            return None
        raw = self.device.raw or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.id)},
            manufacturer="KyungDong Navien",
            name=self.device.name,
            model=str(raw.get("modelDisplayName") or raw.get("modelCode"))
            if raw.get("modelDisplayName") or raw.get("modelCode")
            else None,
            serial_number=str(raw.get("deviceId")) if raw.get("deviceId") else None,
        )

    @property
    def _filter(self) -> dict[str, Any] | None:
        """Return the latest filter snapshot."""
        device = self.device
        if device is None or self._index >= len(device.filters):
            return None
        return device.filters[self._index]
