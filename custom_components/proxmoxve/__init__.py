"""Support for Proxmox VE."""
from __future__ import annotations
from typing import Any

from proxmoxer import AuthenticationError, ProxmoxAPI
from proxmoxer.core import ResourceException
from requests.exceptions import (
    ConnectionError as connError,
    ConnectTimeout,
    RetryError,
    SSLError,
)
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONTAINERS,
    CONF_LXC,
    CONF_NODE,
    CONF_NODES,
    CONF_QEMU,
    CONF_REALM,
    CONF_VMS,
    COORDINATORS,
    DEFAULT_PORT,
    DEFAULT_REALM,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    PROXMOX_CLIENT,
    VERSION_REMOVE_YAML,
    ProxmoxCommand,
    ProxmoxType,
)
from .coordinator import (
    ProxmoxLXCCoordinator,
    ProxmoxNodeCoordinator,
    ProxmoxQEMUCoordinator,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): cv.string,
                        vol.Required(CONF_USERNAME): cv.string,
                        vol.Required(CONF_PASSWORD): cv.string,
                        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                        vol.Optional(CONF_REALM, default=DEFAULT_REALM): cv.string,
                        vol.Optional(
                            CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL
                        ): cv.boolean,
                        vol.Required(CONF_NODES): vol.All(
                            cv.ensure_list,
                            [
                                vol.Schema(
                                    {
                                        vol.Required(CONF_NODE): cv.string,
                                        vol.Optional(CONF_VMS, default=[]): [
                                            cv.positive_int
                                        ],
                                        vol.Optional(CONF_CONTAINERS, default=[]): [
                                            cv.positive_int
                                        ],
                                    }
                                )
                            ],
                        ),
                    }
                )
            ],
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the platform."""

    # import to config flow
    if DOMAIN in config:
        LOGGER.warning(
            # Proxmox VE config flow added and should be removed.
            "Configuration of the Proxmox in YAML is deprecated and should "
            "be removed in %s. Resolve the import issues and remove the "
            "YAML configuration from your configuration.yaml file",
            VERSION_REMOVE_YAML,
        )
        async_create_issue(
            hass,
            DOMAIN,
            "yaml_deprecated",
            breaks_in_ha_version=VERSION_REMOVE_YAML,
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="yaml_deprecated",
            translation_placeholders={
                "integration": "Proxmox VE",
                "platform": DOMAIN,
                "version": VERSION_REMOVE_YAML,
            },
        )
        for conf in config[DOMAIN]:
            if conf.get(CONF_PORT) > 65535 or conf.get(CONF_PORT) <= 0:
                async_create_issue(
                    hass,
                    DOMAIN,
                    f"{conf.get[CONF_HOST]}_{conf.get[CONF_PORT]}_import_invalid_port",
                    is_fixable=False,
                    severity=IssueSeverity.ERROR,
                    translation_key="import_invalid_port",
                    translation_placeholders={
                        "integration": "Proxmox VE",
                        "platform": DOMAIN,
                        "host": conf.get[CONF_HOST],
                        "port": conf.get[CONF_PORT],
                    },
                )
            else:
                hass.async_create_task(
                    hass.config_entries.flow.async_init(
                        DOMAIN,
                        context={"source": SOURCE_IMPORT},
                        data=conf,
                    )
                )
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        device_identifiers = []
        device_identifiers.append(
            f"{config_entry.data[CONF_HOST]}_{config_entry.data[CONF_PORT]}"
        )
        device_identifiers.append(
            f"{config_entry.data[CONF_HOST]}_{config_entry.data[CONF_PORT]}_{config_entry.data.get(CONF_NODE)}"
        )
        for resource in config_entry.data.get(CONF_QEMU):
            device_identifiers.append(
                f"{config_entry.data[CONF_HOST]}_{config_entry.data[CONF_PORT]}_{config_entry.data.get(CONF_NODE)}_{resource}"
            )
        for resource in config_entry.data.get(CONF_LXC):
            device_identifiers.append(
                f"{config_entry.data[CONF_HOST]}_{config_entry.data[CONF_PORT]}_{config_entry.data.get(CONF_NODE)}_{resource}"
            )

        node = []
        node.append(config_entry.data.get(CONF_NODE))
        data_new = {
            CONF_HOST: config_entry.data.get(CONF_HOST),
            CONF_PORT: config_entry.data.get(CONF_PORT),
            CONF_USERNAME: config_entry.data.get(CONF_USERNAME),
            CONF_PASSWORD: config_entry.data.get(CONF_PASSWORD),
            CONF_REALM: config_entry.data.get(CONF_REALM),
            CONF_VERIFY_SSL: config_entry.data.get(CONF_VERIFY_SSL),
            CONF_NODES: node,
            CONF_QEMU: config_entry.data.get(CONF_QEMU),
            CONF_LXC: config_entry.data.get(CONF_LXC),
        }

        config_entry.version = 2
        hass.config_entries.async_update_entry(config_entry, data=data_new, options={})

        LOGGER.debug("Migration - remove devices: %s", device_identifiers)
        for device_identifier in device_identifiers:
            device_identifiers = {
                (
                    DOMAIN,
                    device_identifier,
                )
            }
            dev_reg = dr.async_get(hass)
            device = dev_reg.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers=device_identifiers,
            )

            dev_reg.async_update_device(
                device_id=device.id,
                remove_config_entry_id=config_entry.entry_id,
            )

    if config_entry.version == 2:
        device_identifiers = []
        for resource in config_entry.data.get(CONF_NODES):
            device_identifiers.append(f"{ProxmoxType.Node.upper()}_{resource}")
        for resource in config_entry.data.get(CONF_QEMU):
            device_identifiers.append(f"{ProxmoxType.QEMU.upper()}_{resource}")
        for resource in config_entry.data.get(CONF_LXC):
            device_identifiers.append(f"{ProxmoxType.LXC.upper()}_{resource}")

        config_entry.version = 3
        hass.config_entries.async_update_entry(
            config_entry, data=config_entry.data, options={}
        )

        LOGGER.debug("Migration - remove devices: %s", device_identifiers)
        for device_identifier in device_identifiers:
            device_identifiers = {
                (
                    DOMAIN,
                    device_identifier,
                )
            }
            dev_reg = dr.async_get(hass)
            device = dev_reg.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers=device_identifiers,
            )

            dev_reg.async_update_device(
                device_id=device.id,
                remove_config_entry_id=config_entry.entry_id,
            )

    LOGGER.info("Migration to version %s successful", config_entry.version)

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the platform."""

    hass.data.setdefault(DOMAIN, {})
    entry_data = config_entry.data

    host = entry_data[CONF_HOST]
    port = entry_data[CONF_PORT]
    user = entry_data[CONF_USERNAME]
    realm = entry_data[CONF_REALM]
    password = entry_data[CONF_PASSWORD]
    verify_ssl = entry_data[CONF_VERIFY_SSL]

    # Construct an API client with the given data for the given host
    proxmox_client = ProxmoxClient(
        host=host,
        port=port,
        user=user,
        realm=realm,
        password=password,
        verify_ssl=verify_ssl,
    )
    try:
        await hass.async_add_executor_job(proxmox_client.build_client)
    except AuthenticationError as error:
        raise ConfigEntryAuthFailed from error
    except SSLError as error:
        raise ConfigEntryNotReady(
            "Unable to verify proxmox server SSL. Try using 'verify_ssl: false' "
            f"for proxmox instance {host}:{port}"
        ) from error
    except ConnectTimeout as error:
        raise ConfigEntryNotReady(
            f"Connection to host {host} timed out during setup"
        ) from error
    except RetryError as error:
        raise ConfigEntryNotReady(
            f"Connection is unreachable to host {host}"
        ) from error
    except connError as error:
        raise ConfigEntryNotReady(
            f"Connection is unreachable to host {host}"
        ) from error
    except ResourceException as error:
        raise ConfigEntryNotReady from error

    proxmox = await hass.async_add_executor_job(proxmox_client.get_api_client)

    coordinators: dict[
        str | int,
        ProxmoxNodeCoordinator | ProxmoxQEMUCoordinator | ProxmoxLXCCoordinator,
    ] = {}
    nodes_add_device = []

    resources = await hass.async_add_executor_job(proxmox.cluster.resources.get)
    LOGGER.debug("API Response - Resources: %s", resources)

    for node in config_entry.data[CONF_NODES]:
        if node in [
            node_proxmox["node"]
            for node_proxmox in await hass.async_add_executor_job(proxmox.nodes().get)
        ]:
            async_delete_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{node}_resource_nonexistent",
            )
            coordinator_node = ProxmoxNodeCoordinator(
                hass=hass,
                proxmox=proxmox,
                host_name=config_entry.data[CONF_HOST],
                node_name=node,
            )
            await coordinator_node.async_refresh()
            coordinators[node] = coordinator_node
            if coordinator_node.data is not None:
                nodes_add_device.append(node)
        else:
            async_create_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{node}_resource_nonexistent",
                is_fixable=False,
                severity=IssueSeverity.ERROR,
                translation_key="resource_nonexistent",
                translation_placeholders={
                    "integration": "Proxmox VE",
                    "platform": DOMAIN,
                    "host": config_entry.data[CONF_HOST],
                    "port": config_entry.data[CONF_PORT],
                    "resource_type": "Node",
                    "resource": node,
                },
            )

    for vm_id in config_entry.data[CONF_QEMU]:
        if int(vm_id) in [
            (int(resource["vmid"]) if "vmid" in resource else None)
            for resource in resources
        ]:
            async_delete_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{vm_id}_resource_nonexistent",
            )
            coordinator_qemu = ProxmoxQEMUCoordinator(
                hass=hass,
                proxmox=proxmox,
                host_name=config_entry.data[CONF_HOST],
                qemu_id=vm_id,
            )
            await coordinator_qemu.async_refresh()
            coordinators[vm_id] = coordinator_qemu
        else:
            async_create_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{vm_id}_resource_nonexistent",
                is_fixable=False,
                severity=IssueSeverity.ERROR,
                translation_key="resource_nonexistent",
                translation_placeholders={
                    "integration": "Proxmox VE",
                    "platform": DOMAIN,
                    "host": config_entry.data[CONF_HOST],
                    "port": config_entry.data[CONF_PORT],
                    "resource_type": "QEMU",
                    "resource": vm_id,
                },
            )

    for container_id in config_entry.data[CONF_LXC]:
        if int(container_id) in [
            (int(resource["vmid"]) if "vmid" in resource else None)
            for resource in resources
        ]:
            async_delete_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{container_id}_resource_nonexistent",
            )
            coordinator_lxc = ProxmoxLXCCoordinator(
                hass=hass,
                proxmox=proxmox,
                host_name=config_entry.data[CONF_HOST],
                container_id=container_id,
            )
            await coordinator_lxc.async_refresh()
            coordinators[container_id] = coordinator_lxc
        else:
            async_create_issue(
                hass,
                DOMAIN,
                f"{config_entry.entry_id}_{container_id}_resource_nonexistent",
                is_fixable=False,
                severity=IssueSeverity.ERROR,
                translation_key="resource_nonexistent",
                translation_placeholders={
                    "integration": "Proxmox VE",
                    "platform": DOMAIN,
                    "host": config_entry.data[CONF_HOST],
                    "port": config_entry.data[CONF_PORT],
                    "resource_type": "LXC",
                    "resource": container_id,
                },
            )

    hass.data[DOMAIN][config_entry.entry_id] = {
        PROXMOX_CLIENT: proxmox_client,
        COORDINATORS: coordinators,
    }

    for node in nodes_add_device:
        device_info(
            hass=hass,
            config_entry=config_entry,
            api_category=ProxmoxType.Node,
            node=node,
            create=True,
        )

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(config_entry, platform)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    dev_reg = dr.async_get(hass)
    dev_reg.async_update_device(
        device_id=device_entry.id,
        remove_config_entry_id=config_entry.entry_id,
    )
    LOGGER.debug("Device %s (%s) removed", device_entry.name, device_entry.id)
    return True


def device_info(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    api_category: ProxmoxType,
    node: str | None = None,
    vm_id: int | None = None,
    create: bool | None = False,
):
    """Return the Device Info."""

    coordinators = hass.data[DOMAIN][config_entry.entry_id][COORDINATORS]

    host = config_entry.data[CONF_HOST]
    port = config_entry.data[CONF_PORT]

    proxmox_version = None
    if api_category in (ProxmoxType.QEMU, ProxmoxType.LXC):
        coordinator = coordinators[vm_id]
        if (coordinator_data := coordinator.data) is not None:
            vm_name = coordinator_data.name
            node = coordinator_data.node

        name = f"{api_category.upper()} {vm_name} ({vm_id})"
        identifier = f"{config_entry.entry_id}_{api_category.upper()}_{vm_id}"
        url = f"https://{host}:{port}/#v1:0:={api_category}/{vm_id}"
        via_device = (
            DOMAIN,
            f"{config_entry.entry_id}_{ProxmoxType.Node.upper()}_{node}",
        )
        default_model = api_category.upper()

    elif api_category is ProxmoxType.Node:
        coordinator = coordinators[node]
        if (coordinator_data := coordinator.data) is not None:
            model_processor = coordinator_data.model
            proxmox_version = f"Proxmox {coordinator_data.version}"

        name = f"Node {node}"
        identifier = f"{config_entry.entry_id}_{api_category.upper()}_{node}"
        url = f"https://{host}:{port}/#v1:0:=node/{node}"
        via_device = ("", "")
        default_model = model_processor

    if create:
        device_registry = dr.async_get(hass)
        return device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=url,
            identifiers={(DOMAIN, identifier)},
            default_manufacturer="Proxmox VE",
            name=name,
            default_model=default_model,
            sw_version=proxmox_version,
            hw_version=None,
            via_device=via_device,
        )
    return DeviceInfo(
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=url,
        identifiers={(DOMAIN, identifier)},
        default_manufacturer="Proxmox VE",
        name=name,
        default_model=default_model,
        sw_version=proxmox_version,
        hw_version=None,
        via_device=via_device,
    )


class ProxmoxClient:
    """A wrapper for the proxmoxer ProxmoxAPI client."""

    _proxmox: ProxmoxAPI

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        port: int | None = DEFAULT_PORT,
        realm: str | None = DEFAULT_REALM,
        verify_ssl: bool | None = DEFAULT_VERIFY_SSL,
    ) -> None:
        """Initialize the ProxmoxClient."""

        self._host = host
        self._port = port
        self._user = user
        self._realm = realm
        self._password = password
        self._verify_ssl = verify_ssl

    def build_client(self) -> None:
        """Construct the ProxmoxAPI client.

        Allows inserting the realm within the `user` value.
        """

        if "@" in self._user:
            user_id = self._user
        else:
            user_id = f"{self._user}@{self._realm}"

        self._proxmox = ProxmoxAPI(
            self._host,
            port=self._port,
            user=user_id,
            password=self._password,
            verify_ssl=self._verify_ssl,
        )

    def get_api_client(self) -> ProxmoxAPI:
        """Return the ProxmoxAPI client."""
        return self._proxmox


def call_api_post_status(
    proxmox: ProxmoxAPI,
    api_category: ProxmoxType,
    command: str,
    node: str,
    vm_id: int | None = None,
) -> Any:
    """Make proper api post status calls to set state."""
    result = None
    if command not in ProxmoxCommand:
        raise ValueError("Invalid Command")

    try:
        # Only the START_ALL and STOP_ALL are not part of status API
        if api_category is ProxmoxType.Node and command in [
            ProxmoxCommand.START_ALL,
            ProxmoxCommand.STOP_ALL,
        ]:
            result = proxmox.nodes(node).post(command)
        elif api_category is ProxmoxType.Node:
            result = proxmox(["nodes", node, "status"]).post(command=command)
        else:
            result = proxmox(
                ["nodes", node, api_category, vm_id, "status", command]
            ).post()

    except (ResourceException, ConnectTimeout) as err:
        raise ValueError(
            f"Proxmox {api_category} {command} error - {err}",
        ) from err

    return result
