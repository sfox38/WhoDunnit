"""Shared fixtures for the Whodunnit test suite."""

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.whodunnit.const import DOMAIN

# Wire up the Home Assistant test harness (provides the `hass` fixture,
# `enable_custom_integrations`, socket disabling, etc.).
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ during every test (required by HA's loader)."""
    yield


@pytest.fixture
def make_config_entry():
    """Return a factory that builds a Whodunnit MockConfigEntry.

    Mirrors what config_flow produces: data carries a "targets" list holding a
    single entity_id, and unique_id is derived from that entity.
    """

    def _make(target: str = "switch.test", **kwargs) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            title=target.split(".")[-1].replace("_", " ").title(),
            data={"targets": [target]},
            unique_id=f"whodunnit_{target.replace('.', '_')}",
            **kwargs,
        )

    return _make


@pytest.fixture
def register_target(hass):
    """Return a factory that registers a target entity and sets its state.

    The WhodunnitSensor reports `available` only when its target exists in the
    entity registry (see sensor.py `available`), so tests that exercise the
    sensor must register the target rather than just set a bare state.
    """

    def _register(
        target: str = "switch.test",
        *,
        platform: str = "test",
        state: str = "off",
        attributes: dict | None = None,
        device_id: str | None = None,
    ):
        domain, object_id = target.split(".", 1)
        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get_or_create(
            domain,
            platform,
            f"{object_id}_unique",
            suggested_object_id=object_id,
            device_id=device_id,
        )
        assert entry.entity_id == target, (
            f"expected {target}, registry produced {entry.entity_id}"
        )
        hass.states.async_set(target, state, attributes or {})
        return entry

    return _register
