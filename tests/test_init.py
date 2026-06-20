"""Tests for custom_components.whodunnit.__init__ (setup/teardown & listeners)."""

import time

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.whodunnit import _get_friendly, async_remove_entry
from custom_components.whodunnit.const import (
    CACHE_MAX_SIZE,
    CACHE_TTL,
    DOMAIN,
    STATE_UI,
)


async def _setup(hass, entry):
    """Add and set up a config entry, returning the setup result."""
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


# --------------------------------------------------------------------------- #
# _get_friendly helper
# --------------------------------------------------------------------------- #


def test_get_friendly_uses_friendly_name(hass: HomeAssistant):
    hass.states.async_set("switch.x", "on", {"friendly_name": "Kitchen Switch"})
    assert _get_friendly(hass, "switch.x") == "Kitchen Switch"


def test_get_friendly_slug_fallback(hass: HomeAssistant):
    hass.states.async_set("switch.kitchen_light", "on", {})
    assert _get_friendly(hass, "switch.kitchen_light") == "Kitchen Light"


def test_get_friendly_no_state_returns_entity_id(hass: HomeAssistant):
    assert _get_friendly(hass, "switch.missing") == "switch.missing"


# --------------------------------------------------------------------------- #
# async_setup_entry
# --------------------------------------------------------------------------- #


async def test_setup_with_no_targets_fails(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={"targets": []}, title="Empty")
    assert await _setup(hass, entry) is False
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_initialises_shared_state(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    entry = make_config_entry("switch.test")
    assert await _setup(hass, entry) is True

    data = hass.data[DOMAIN]
    assert "context_cache" in data
    assert "user_cache" in data
    assert data["entry_count"] == 1
    assert "listener_unsubs" in data
    assert entry.entry_id in data["entries"]
    assert data["entries"][entry.entry_id]["targets"] == ["switch.test"]


async def test_setup_syncs_title_to_friendly_name(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test", attributes={"friendly_name": "Hallway Lamp"})
    entry = make_config_entry("switch.test")
    await _setup(hass, entry)
    assert entry.title == "Hallway Lamp"


async def test_title_updates_when_friendly_name_changes(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test", attributes={"friendly_name": "Old Name"})
    entry = make_config_entry("switch.test")
    await _setup(hass, entry)
    assert entry.title == "Old Name"

    hass.states.async_set("switch.test", "on", {"friendly_name": "New Name"})
    await hass.async_block_till_done()
    assert entry.title == "New Name"


# --------------------------------------------------------------------------- #
# Device info / virtual device creation
# --------------------------------------------------------------------------- #


async def test_helper_target_creates_virtual_device(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A target with no parent device gets a virtual Whodunnit device."""
    register_target("input_boolean.test", platform="input_boolean")
    entry = make_config_entry("input_boolean.test")
    await _setup(hass, entry)

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.model == "Whodunnit Virtual Device"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_physical_target_reuses_parent_device(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A target attached to a real device reuses that device (no virtual one)."""
    source_entry = MockConfigEntry(domain="demo", data={})
    source_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("demo", "abc123")},
        name="Real Device",
    )
    register_target("switch.test", device_id=device.id)

    entry = make_config_entry("switch.test")
    await _setup(hass, entry)

    # No virtual device should have been created for this entry.
    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is None
    stored = hass.data[DOMAIN]["entries"][entry.entry_id]["device_info"]
    assert ("demo", "abc123") in stored["identifiers"]


# --------------------------------------------------------------------------- #
# Shared listeners populate the context cache
# --------------------------------------------------------------------------- #


async def test_automation_triggered_is_cached(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"entity_id": "automation.morning", "name": "Morning"},
        context=ctx,
    )
    await hass.async_block_till_done()

    assert cache[ctx.id]["id"] == "automation.morning"
    assert cache[ctx.id]["name"] == "Morning"
    assert cache[ctx.id]["type"] == "automation"


async def test_script_started_is_cached(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        "script_started",
        {"entity_id": "script.bedtime", "name": "Bedtime"},
        context=ctx,
    )
    await hass.async_block_till_done()
    assert cache[ctx.id]["type"] == "script"
    assert cache[ctx.id]["id"] == "script.bedtime"


async def test_service_call_scene_uses_target_entity(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {
            "domain": "scene",
            "service": "turn_on",
            "target": {"entity_id": "scene.movie_night"},
        },
        context=ctx,
    )
    await hass.async_block_till_done()
    assert cache[ctx.id]["id"] == "scene.movie_night"
    assert cache[ctx.id]["type"] == "scene"


async def test_service_call_script_uses_service_data(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.foo"},
        },
        context=ctx,
    )
    await hass.async_block_till_done()
    assert cache[ctx.id]["id"] == "script.foo"
    assert cache[ctx.id]["type"] == "script"


async def test_service_call_scene_accepts_list_target(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {
            "domain": "scene",
            "service": "turn_on",
            "target": {"entity_id": ["scene.a", "scene.b"]},
        },
        context=ctx,
    )
    await hass.async_block_till_done()
    # First entity of the list is used as the logic source.
    assert cache[ctx.id]["id"] == "scene.a"


async def test_service_call_automation_accepts_list_entity(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {
            "domain": "automation",
            "service": "trigger",
            "service_data": {"entity_id": ["automation.x", "automation.y"]},
        },
        context=ctx,
    )
    await hass.async_block_till_done()
    assert cache[ctx.id]["id"] == "automation.x"
    assert cache[ctx.id]["type"] == "automation"


async def test_service_call_with_user_id_records_ui(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context(user_id="user-123")
    hass.bus.async_fire(
        EVENT_CALL_SERVICE,
        {"domain": "light", "service": "turn_on"},
        context=ctx,
    )
    await hass.async_block_till_done()
    assert cache[ctx.id]["type"] == STATE_UI
    assert cache[ctx.id]["id"] == "user-123"


# --------------------------------------------------------------------------- #
# Cache cleanup
# --------------------------------------------------------------------------- #


async def test_cleanup_evicts_expired_entries(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    # An entry older than CACHE_TTL should be purged on the next event.
    cache["stale"] = {
        "id": "automation.old",
        "name": "Old",
        "type": "automation",
        "timestamp": time.monotonic() - (CACHE_TTL + 50),
    }

    ctx = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"entity_id": "automation.fresh", "name": "Fresh"},
        context=ctx,
    )
    await hass.async_block_till_done()

    assert "stale" not in cache
    assert ctx.id in cache


async def test_cleanup_is_throttled_within_interval(
    hass: HomeAssistant, make_config_entry, register_target
):
    """Two events in quick succession: the second cleanup pass is skipped."""
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    ctx1 = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"entity_id": "automation.one", "name": "One"},
        context=ctx1,
    )
    await hass.async_block_till_done()

    # Insert a stale entry; the next event fires within CACHE_CLEANUP_INTERVAL,
    # so the throttle short-circuits cleanup and the stale entry survives.
    cache["stale"] = {
        "id": "automation.old",
        "type": "automation",
        "timestamp": time.monotonic() - (CACHE_TTL + 50),
    }
    ctx2 = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"entity_id": "automation.two", "name": "Two"},
        context=ctx2,
    )
    await hass.async_block_till_done()

    assert "stale" in cache
    assert ctx2.id in cache


async def test_cleanup_enforces_max_size(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    await _setup(hass, make_config_entry("switch.test"))
    cache = hass.data[DOMAIN]["context_cache"]

    now = time.monotonic()
    # Oldest sentinel (still within TTL) plus a flood past the size cap.
    cache["oldest"] = {"id": "a.0", "type": "automation", "timestamp": now - 1}
    for i in range(CACHE_MAX_SIZE + 9):
        cache[f"k{i}"] = {"id": f"a.{i}", "type": "automation", "timestamp": now}

    ctx = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"entity_id": "automation.fresh", "name": "Fresh"},
        context=ctx,
    )
    await hass.async_block_till_done()

    # Trim runs before the new entry is inserted, leaving CACHE_MAX_SIZE + 1.
    assert len(cache) == CACHE_MAX_SIZE + 1
    assert "oldest" not in cache
    assert ctx.id in cache


# --------------------------------------------------------------------------- #
# Unload / remove
# --------------------------------------------------------------------------- #


async def test_unload_tears_down_shared_state(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.test")
    entry = make_config_entry("switch.test")
    await _setup(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert "context_cache" not in hass.data[DOMAIN]
    assert "listener_unsubs" not in hass.data[DOMAIN]
    assert hass.data[DOMAIN]["entry_count"] == 0


async def test_unload_keeps_shared_state_with_other_entries(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("switch.a")
    register_target("switch.b")
    entry_a = make_config_entry("switch.a")
    entry_b = make_config_entry("switch.b")
    await _setup(hass, entry_a)
    await _setup(hass, entry_b)
    assert hass.data[DOMAIN]["entry_count"] == 2

    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()

    # Listeners and cache stay alive while entry_b is still loaded.
    assert "context_cache" in hass.data[DOMAIN]
    assert hass.data[DOMAIN]["entry_count"] == 1
    assert entry_b.entry_id in hass.data[DOMAIN]["entries"]


async def test_remove_entry_deletes_virtual_device(
    hass: HomeAssistant, make_config_entry, register_target
):
    register_target("input_boolean.test", platform="input_boolean")
    entry = make_config_entry("input_boolean.test")
    await _setup(hass, entry)

    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is None


async def test_remove_entry_skips_shared_device(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A virtual device shared with another integration must not be removed."""
    register_target("input_boolean.test", platform="input_boolean")
    entry = make_config_entry("input_boolean.test")
    await _setup(hass, entry)

    other = MockConfigEntry(domain="demo", data={})
    other.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    # Attach a second config entry to the virtual device so it is "shared".
    dev_reg.async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )

    await async_remove_entry(hass, entry)

    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)}) is not None


async def test_remove_entry_without_virtual_device_is_noop(
    hass: HomeAssistant, make_config_entry, register_target
):
    """Removing a physical-device entry should not raise (no virtual device)."""
    source_entry = MockConfigEntry(domain="demo", data={})
    source_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("demo", "xyz")},
        name="Real Device",
    )
    register_target("switch.test", device_id=device.id)
    entry = make_config_entry("switch.test")
    await _setup(hass, entry)

    # Should return cleanly without touching the physical device.
    await async_remove_entry(hass, entry)
    assert dev_reg.async_get_device(identifiers={("demo", "xyz")}) is not None
