"""Tests for the Whodunnit config flow."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.whodunnit.config_flow import WhodunnitConfigFlow
from custom_components.whodunnit.const import DOMAIN


# --------------------------------------------------------------------------- #
# _validate_target (server-side guard against raw websocket submissions)
# --------------------------------------------------------------------------- #


def _flow(hass) -> WhodunnitConfigFlow:
    flow = WhodunnitConfigFlow()
    flow.hass = hass
    return flow


def test_validate_target_rejects_non_string(hass: HomeAssistant):
    assert _flow(hass)._validate_target(123) == "invalid_entity"


def test_validate_target_rejects_malformed_id(hass: HomeAssistant):
    assert _flow(hass)._validate_target("not an entity") == "invalid_entity"


def test_validate_target_rejects_unsupported_domain(hass: HomeAssistant):
    hass.states.async_set("sensor.temperature", "21")
    assert _flow(hass)._validate_target("sensor.temperature") == "invalid_entity"


def test_validate_target_rejects_unknown_entity(hass: HomeAssistant):
    # Supported domain, but no state and no registry entry.
    assert _flow(hass)._validate_target("switch.ghost") == "invalid_entity"


def test_validate_target_accepts_entity_with_state(hass: HomeAssistant):
    hass.states.async_set("switch.real", "on")
    assert _flow(hass)._validate_target("switch.real") is None


def test_validate_target_accepts_registered_disabled_entity(hass: HomeAssistant):
    # Registered but no state (e.g. disabled entity) is still acceptable.
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        "switch", "test", "no_state_unique", suggested_object_id="disabled"
    )
    assert _flow(hass)._validate_target("switch.disabled") is None


# --------------------------------------------------------------------------- #
# Full user flow
# --------------------------------------------------------------------------- #


async def test_user_flow_shows_form(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_flow_creates_entry(hass: HomeAssistant):
    hass.states.async_set("switch.test", "off", {"friendly_name": "Test Switch"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"targets": "switch.test"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"targets": ["switch.test"]}
    assert result["title"] == "Test"
    entry = result["result"]
    assert entry.unique_id == "whodunnit_switch_test"


async def test_user_flow_rejects_invalid_target(hass: HomeAssistant):
    """A supported-domain entity that does not exist re-shows the form with error.

    The EntitySelector enforces the domain constraint itself, so the only
    _validate_target branch reachable through the form is the unknown-entity
    one: a well-formed, supported-domain id with no state and no registry entry.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"targets": "switch.ghost"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"targets": "invalid_entity"}


async def test_user_flow_aborts_on_duplicate(
    hass: HomeAssistant, make_config_entry
):
    """A colliding unique_id aborts as already_configured.

    The selector hides already-tracked entities from the picker, so to reach
    the _abort_if_unique_id_configured guard we register an existing entry that
    carries the same unique_id but tracks a different entity (leaving the
    submitted one selectable).
    """
    hass.states.async_set("switch.test", "off")
    # unique_id == whodunnit_switch_test, but tracking a different entity.
    existing = make_config_entry("switch.test")
    existing.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        existing, data={"targets": ["switch.other"]}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"targets": "switch.test"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
