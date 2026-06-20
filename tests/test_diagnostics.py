"""Tests for the Whodunnit diagnostics dump (privacy redaction & shape)."""

import time

from homeassistant.core import HomeAssistant

from custom_components.whodunnit.const import DOMAIN, STATE_UI
from custom_components.whodunnit.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def _setup(hass, make_config_entry, register_target):
    register_target("switch.test")
    entry = make_config_entry("switch.test")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_diagnostics_redacts_ui_identity(
    hass: HomeAssistant, make_config_entry, register_target
):
    """UI user UUIDs are aliased and person names dropped; the alias is stable."""
    entry = await _setup(hass, make_config_entry, register_target)
    now = time.monotonic()

    cache = hass.data[DOMAIN]["context_cache"]
    cache["ctx-ui"] = {
        "id": "real-user-uuid",
        "type": STATE_UI,
        "seen": True,
        "timestamp": now,
    }
    cache["ctx-auto"] = {
        "id": "automation.morning",
        "name": "Morning",
        "type": "automation",
        "timestamp": now,
    }
    hass.data[DOMAIN]["user_cache"]["real-user-uuid"] = {
        "person_id": "person.alice",
        "name": "Alice",
        "is_service_account": False,
        "timestamp": now,
    }

    diag = await async_get_config_entry_diagnostics(hass, entry)

    ui = diag["context_cache"]["entries"]["ctx-ui"]
    assert ui["id"] == "user_1"          # aliased, not the real UUID
    assert ui["name"] is None            # person name dropped
    assert ui["type"] == STATE_UI
    assert ui["seen"] is True

    auto = diag["context_cache"]["entries"]["ctx-auto"]
    assert auto["id"] == "automation.morning"  # non-identity data preserved
    assert auto["name"] == "Morning"

    # The same real user maps to the same alias in the user_cache section.
    assert "user_1" in diag["user_cache"]["entries"]
    user_row = diag["user_cache"]["entries"]["user_1"]
    assert user_row["has_person_entity"] is True
    assert user_row["is_service_account"] is False
    assert "name" not in user_row        # no person name leaks


async def test_diagnostics_top_level_shape(
    hass: HomeAssistant, make_config_entry, register_target
):
    entry = await _setup(hass, make_config_entry, register_target)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["config_entry"]["entry_id"] == entry.entry_id
    assert diag["config_entry"]["data"] == {"targets": ["switch.test"]}
    assert diag["targets"] == ["switch.test"]
    assert diag["shared_listeners_active"] is True
    assert diag["active_entry_count"] == 1
    assert diag["context_cache"]["total_entries"] == len(
        hass.data[DOMAIN]["context_cache"]
    )


async def test_diagnostics_without_runtime_data(
    hass: HomeAssistant, make_config_entry
):
    """Diagnostics on an entry that was never set up uses safe defaults."""
    entry = make_config_entry("switch.test")
    entry.add_to_hass(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["targets"] == []
    assert diag["context_cache"]["total_entries"] == 0
    assert diag["user_cache"]["total_entries"] == 0
    assert diag["shared_listeners_active"] is False
    assert diag["active_entry_count"] == 0
