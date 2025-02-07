"""Binary sensor to read Proxmox VE data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import COORDINATORS, DOMAIN, device_info
from .const import CONF_LXC, CONF_NODES, CONF_QEMU, ProxmoxKeyAPIParse, ProxmoxType
from .entity import ProxmoxEntity
from .models import ProxmoxEntityDescription


@dataclass
class ProxmoxBinarySensorEntityDescription(
    ProxmoxEntityDescription, BinarySensorEntityDescription
):
    """Class describing Proxmox binarysensor entities."""

    on_value: Any | None = None
    inverted: bool | None = False
    api_category: ProxmoxType | None = None  # Set when the sensor applies to only QEMU or LXC, if None applies to both.


PROXMOX_BINARYSENSOR_NODES: Final[tuple[ProxmoxBinarySensorEntityDescription, ...]] = (
    ProxmoxBinarySensorEntityDescription(
        key=ProxmoxKeyAPIParse.STATUS,
        name="Status",
        device_class=BinarySensorDeviceClass.RUNNING,
        on_value="online",
    ),
)

PROXMOX_BINARYSENSOR_VM: Final[tuple[ProxmoxBinarySensorEntityDescription, ...]] = (
    ProxmoxBinarySensorEntityDescription(
        key=ProxmoxKeyAPIParse.STATUS,
        name="Status",
        device_class=BinarySensorDeviceClass.RUNNING,
        on_value="running",
    ),
    ProxmoxBinarySensorEntityDescription(
        key=ProxmoxKeyAPIParse.HEALTH,
        name="Health",
        device_class=BinarySensorDeviceClass.PROBLEM,
        on_value="running",
        inverted=True,
        api_category=ProxmoxType.QEMU,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""

    sensors = []
    coordinators = hass.data[DOMAIN][config_entry.entry_id][COORDINATORS]

    for node in config_entry.data[CONF_NODES]:
        coordinator = coordinators[node]
        # unfound node case
        if coordinator.data is not None:
            for description in PROXMOX_BINARYSENSOR_NODES:
                sensors.append(
                    create_binary_sensor(
                        coordinator=coordinator,
                        config_entry=config_entry,
                        info_device=device_info(
                            hass=hass,
                            config_entry=config_entry,
                            api_category=ProxmoxType.Node,
                            node=node,
                        ),
                        description=description,
                        vm_id=None,
                    )
                )

    for vm_id in config_entry.data[CONF_QEMU]:
        coordinator = coordinators[vm_id]
        # unfound vm case
        if coordinator.data is None:
            continue
        for description in PROXMOX_BINARYSENSOR_VM:
            if description.api_category in (None, ProxmoxType.QEMU):
                sensors.append(
                    create_binary_sensor(
                        coordinator=coordinator,
                        config_entry=config_entry,
                        info_device=device_info(
                            hass=hass,
                            config_entry=config_entry,
                            api_category=ProxmoxType.QEMU,
                            vm_id=vm_id,
                        ),
                        description=description,
                        vm_id=vm_id,
                    )
                )

    for container_id in config_entry.data[CONF_LXC]:
        coordinator = coordinators[container_id]
        # unfound container case
        if coordinator.data is None:
            continue
        for description in PROXMOX_BINARYSENSOR_VM:
            if description.api_category in (None, ProxmoxType.LXC):
                sensors.append(
                    create_binary_sensor(
                        coordinator=coordinator,
                        config_entry=config_entry,
                        info_device=device_info(
                            hass=hass,
                            config_entry=config_entry,
                            api_category=ProxmoxType.LXC,
                            vm_id=container_id,
                        ),
                        description=description,
                        vm_id=container_id,
                    )
                )

    async_add_entities(sensors)


def create_binary_sensor(
    coordinator,
    vm_id,
    config_entry,
    info_device,
    description,
) -> ProxmoxBinarySensorEntity:
    """Create a binary sensor based on the given data."""
    return ProxmoxBinarySensorEntity(
        coordinator=coordinator,
        unique_id=f"{config_entry.entry_id}_{vm_id}_{description.key}",
        description=description,
        info_device=info_device,
    )


class ProxmoxBinarySensorEntity(ProxmoxEntity, BinarySensorEntity):
    """A binary sensor for reading Proxmox VE data."""

    entity_description: ProxmoxBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        unique_id: str,
        info_device: DeviceInfo,
        description: ProxmoxBinarySensorEntityDescription,
    ) -> None:
        """Create the binary sensor for vms or containers."""
        super().__init__(coordinator, unique_id, description)

        self._attr_device_info = info_device

    @property
    def is_on(self) -> bool:
        """Return the state of the binary sensor."""
        if (data := self.coordinator.data) is None:
            return False

        if not getattr(data, self.entity_description.key):
            return False

        if self.entity_description.inverted:
            return (
                getattr(data, self.entity_description.key)
                != self.entity_description.on_value
            )
        return (
            getattr(data, self.entity_description.key)
            == self.entity_description.on_value
        )

    @property
    def available(self) -> bool:
        """Return sensor availability."""

        return super().available and self.coordinator.data is not None
