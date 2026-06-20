"""Tests for the WhodunnitSensor detection cascade and lifecycle."""

import time

from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    mock_restore_cache,
)

from custom_components.whodunnit.const import (
    ATTR_CONFIDENCE,
    ATTR_CONTEXT_ID,
    ATTR_EVENT_TIME,
    ATTR_HISTORY_LOG,
    ATTR_SOURCE_ID,
    ATTR_SOURCE_NAME,
    ATTR_SOURCE_TYPE,
    ATTR_USER_ID,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    DOMAIN,
    EVENT_TRIGGER_DETECTED,
    ID_INDIRECT_AUTOMATION,
    NAME_DEVICE,
    NAME_INDIRECT_AUTOMATION,
    STATE_AUTOMATION,
    STATE_DEVICE,
    STATE_MONITORING,
    STATE_SCRIPT,
    STATE_SERVICE,
    STATE_UI,
)
from custom_components.whodunnit.sensor import WhodunnitSensor


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _setup_sensor(
    hass,
    make_config_entry,
    register_target,
    target="switch.test",
    *,
    platform="test",
    state="off",
    attributes=None,
):
    """Register the target, set up the entry, return (entry, sensor_entity_id)."""
    register_target(target, platform=platform, state=state, attributes=attributes)
    entry = make_config_entry(target)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{target}_whodunnit"
    )
    assert sensor_id is not None
    return entry, sensor_id


async def _fire(hass, target, new_state, *, context=None, attributes=None):
    """Drive a state change on the target entity and let the sensor react."""
    hass.states.async_set(target, new_state, attributes or {}, context=context)
    await hass.async_block_till_done()


def _attrs(hass, sensor_id):
    return hass.states.get(sensor_id).attributes


async def _make_user(hass, name="Alice", *, with_person=True):
    """Create an HA user, optionally with a matching person entity."""
    user = await hass.auth.async_create_user(name)
    if with_person:
        hass.states.async_set(
            "person.alice", "home", {"user_id": user.id, "friendly_name": name}
        )
    return user


# --------------------------------------------------------------------------- #
# Detection cascade
# --------------------------------------------------------------------------- #


async def test_device_internal_change(
    hass: HomeAssistant, make_config_entry, register_target
):
    """No user, no parent, no cache hit -> device-originated."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    await _fire(hass, "switch.test", "on", context=Context())

    state = hass.states.get(sid)
    assert state.state == STATE_DEVICE
    assert state.attributes[ATTR_SOURCE_TYPE] == "device"
    assert state.attributes[ATTR_SOURCE_NAME] == NAME_DEVICE
    assert state.attributes[ATTR_SOURCE_ID] == "switch.test"
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_HIGH


async def test_cache_hit_automation(
    hass: HomeAssistant, make_config_entry, register_target
):
    """Direct context cache hit classifies as the cached source."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context()
    cache[ctx.id] = {
        "id": "automation.morning",
        "name": "Morning Routine",
        "type": STATE_AUTOMATION,
        "timestamp": time.monotonic(),
    }
    await _fire(hass, "switch.test", "on", context=ctx)

    state = hass.states.get(sid)
    assert state.state == STATE_AUTOMATION
    assert state.attributes[ATTR_SOURCE_ID] == "automation.morning"
    assert state.attributes[ATTR_SOURCE_NAME] == "Morning Routine"
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_HIGH


async def test_cache_hit_ui_regular_user(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A UI cache entry for a real user resolves to the person entity."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    user = await _make_user(hass, "Alice", with_person=True)
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context(user_id=user.id)
    cache[ctx.id] = {"id": user.id, "type": STATE_UI, "timestamp": time.monotonic()}
    await _fire(hass, "switch.test", "on", context=ctx)

    state = hass.states.get(sid)
    assert state.state == STATE_UI
    assert state.attributes[ATTR_SOURCE_TYPE] == "user"
    assert state.attributes[ATTR_SOURCE_ID] == "person.alice"
    assert state.attributes[ATTR_SOURCE_NAME] == "Alice"
    assert state.attributes[ATTR_USER_ID] == user.id
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_HIGH


async def test_cache_hit_ui_service_account(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A UI cache entry for a user with no person entity is a service account."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    user = await _make_user(hass, "Node-RED", with_person=False)
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context(user_id=user.id)
    cache[ctx.id] = {"id": user.id, "type": STATE_UI, "timestamp": time.monotonic()}
    await _fire(hass, "switch.test", "on", context=ctx)

    state = hass.states.get(sid)
    assert state.state == STATE_SERVICE
    assert state.attributes[ATTR_SOURCE_TYPE] == "service"
    assert state.attributes[ATTR_SOURCE_ID] == user.id
    assert state.attributes[ATTR_SOURCE_NAME] == "Node-RED"


async def test_step2_user_id_without_cache(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A user_id with no cache entry classifies as a UI action."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    user = await _make_user(hass, "Alice", with_person=True)

    await _fire(hass, "switch.test", "on", context=Context(user_id=user.id))

    state = hass.states.get(sid)
    assert state.state == STATE_UI
    assert state.attributes[ATTR_SOURCE_TYPE] == "user"
    assert state.attributes[ATTR_SOURCE_ID] == "person.alice"


async def test_step2_user_id_service_account(
    hass: HomeAssistant, make_config_entry, register_target
):
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    user = await _make_user(hass, "AppDaemon", with_person=False)

    await _fire(hass, "switch.test", "on", context=Context(user_id=user.id))

    state = hass.states.get(sid)
    assert state.state == STATE_SERVICE
    assert state.attributes[ATTR_SOURCE_ID] == user.id


async def test_step3_parent_in_cache(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A parent context found in the cache resolves to that source (high)."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    cache = hass.data[DOMAIN]["context_cache"]

    parent = Context()
    cache[parent.id] = {
        "id": "script.bedtime",
        "name": "Bedtime",
        "type": STATE_SCRIPT,
        "timestamp": time.monotonic(),
    }
    child = Context(parent_id=parent.id)
    await _fire(hass, "switch.test", "on", context=child)

    state = hass.states.get(sid)
    assert state.state == STATE_SCRIPT
    assert state.attributes[ATTR_SOURCE_ID] == "script.bedtime"
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_HIGH


async def test_step3_parent_not_in_cache(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A parent context that is missing yields an indirect-automation guess."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)

    child = Context(parent_id="missing-parent-context")
    await _fire(hass, "switch.test", "on", context=child)

    state = hass.states.get(sid)
    assert state.state == STATE_AUTOMATION
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_MEDIUM
    assert state.attributes[ATTR_SOURCE_ID] == ID_INDIRECT_AUTOMATION
    assert state.attributes[ATTR_SOURCE_NAME] == NAME_INDIRECT_AUTOMATION


# --------------------------------------------------------------------------- #
# Bleed handling (ESPHome)
# --------------------------------------------------------------------------- #


async def test_bleed_platform_downgrades_repeat_ui_hit(
    hass: HomeAssistant, make_config_entry, register_target
):
    """On ESPHome, the second hit on a UI context is low confidence (bleed)."""
    _, sid = await _setup_sensor(
        hass, make_config_entry, register_target,
        target="switch.esp", platform="esphome",
    )
    user = await _make_user(hass, "Alice", with_person=True)
    cache = hass.data[DOMAIN]["context_cache"]

    ctx = Context(user_id=user.id)
    cache[ctx.id] = {"id": user.id, "type": STATE_UI, "timestamp": time.monotonic()}

    # First hit: genuine dashboard action -> high confidence.
    await _fire(hass, "switch.esp", "on", context=ctx)
    assert _attrs(hass, sid)[ATTR_CONFIDENCE] == CONFIDENCE_HIGH

    # Second hit reusing the same context -> bleed suspected -> low.
    await _fire(hass, "switch.esp", "off", context=ctx)
    assert _attrs(hass, sid)[ATTR_CONFIDENCE] == CONFIDENCE_LOW


# --------------------------------------------------------------------------- #
# Change filtering
# --------------------------------------------------------------------------- #


async def test_no_classification_when_nothing_meaningful_changed(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A non-watched attribute change with an unchanged state is ignored."""
    _, sid = await _setup_sensor(
        hass, make_config_entry, register_target, state="on"
    )
    events = async_capture_events(hass, EVENT_TRIGGER_DETECTED)

    # State stays "on"; only an unwatched attribute changes.
    await _fire(hass, "switch.test", "on", attributes={"foo": "bar"})

    assert hass.states.get(sid).state == STATE_MONITORING
    assert len(events) == 0


async def test_watched_attribute_change_triggers_classification(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A watched attribute change (light brightness) is classified."""
    _, sid = await _setup_sensor(
        hass, make_config_entry, register_target,
        target="light.test", state="on", attributes={"brightness": 100},
    )
    await _fire(
        hass, "light.test", "on", context=Context(), attributes={"brightness": 200}
    )
    assert hass.states.get(sid).state == STATE_DEVICE


async def test_repeat_attribute_change_is_throttled(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A second watched-attr change within 2s (same state) is throttled."""
    _, sid = await _setup_sensor(
        hass, make_config_entry, register_target,
        target="light.test", state="on", attributes={"brightness": 100},
    )
    events = async_capture_events(hass, EVENT_TRIGGER_DETECTED)

    await _fire(
        hass, "light.test", "on", context=Context(), attributes={"brightness": 200}
    )
    await _fire(
        hass, "light.test", "on", context=Context(), attributes={"brightness": 250}
    )
    assert len(events) == 1  # second change suppressed by the 2s attr throttle


async def test_missing_old_state_is_ignored(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A state change with no old_state (entity (re)appearing) is ignored."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    events = async_capture_events(hass, EVENT_TRIGGER_DETECTED)

    hass.states.async_remove("switch.test")
    await hass.async_block_till_done()
    await _fire(hass, "switch.test", "on", context=Context())  # old_state is None

    assert len(events) == 0
    assert hass.states.get(sid).state == STATE_MONITORING


async def test_initial_name_uses_clean_target_name(
    hass: HomeAssistant, make_config_entry, register_target
):
    """At setup the sensor name reflects the target's friendly name, not the slug.

    Same cached-name root cause as the rename case: HA caches Entity.name during
    entity-id generation (using the __init__ slug placeholder) before
    async_added_to_hass sets the clean name, so the cache must be invalidated.
    """
    _, sid = await _setup_sensor(
        hass, make_config_entry, register_target,
        target="switch.garage_main",
        attributes={"friendly_name": "Garage Door"},
    )
    friendly = hass.states.get(sid).attributes["friendly_name"]
    assert "Garage Door" in friendly
    assert "Garage Main" not in friendly  # the raw slug must not leak through


async def test_registry_non_name_change_is_ignored(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A registry update that does not change the name leaves the title alone."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    before = hass.states.get(sid).attributes["friendly_name"]

    er.async_get(hass).async_update_entity("switch.test", icon="mdi:flash")
    await hass.async_block_till_done()

    assert hass.states.get(sid).attributes["friendly_name"] == before


async def test_classification_error_is_caught(
    hass: HomeAssistant, make_config_entry, register_target, monkeypatch, caplog
):
    """An unexpected error during classification is logged, not raised."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)

    async def _boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    # Force _get_person_cached -> hass.auth.async_get_user to blow up.
    monkeypatch.setattr(hass.auth, "async_get_user", _boom)

    await _fire(hass, "switch.test", "on", context=Context(user_id="user-x"))

    # The handler swallows the error: no crash, state unchanged, error logged.
    assert hass.states.get(sid).state == STATE_MONITORING
    assert "error classifying switch.test" in caplog.text


async def test_registry_rename_updates_sensor_name(
    hass: HomeAssistant, make_config_entry, register_target
):
    """Renaming the target in the registry refreshes the sensor's name.

    Regression guard for the cached-name bug: Entity.name is a @cached_property,
    so _refresh_name() must invalidate it for the new placeholder to take effect.
    """
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)

    er.async_get(hass).async_update_entity("switch.test", name="Renamed Target")
    await hass.async_block_till_done()

    assert "Renamed Target" in hass.states.get(sid).attributes["friendly_name"]


async def test_duplicate_context_is_skipped(
    hass: HomeAssistant, make_config_entry, register_target
):
    """The same context on a non-bleed platform is classified only once."""
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)
    events = async_capture_events(hass, EVENT_TRIGGER_DETECTED)

    ctx = Context()
    await _fire(hass, "switch.test", "on", context=ctx)
    await _fire(hass, "switch.test", "off", context=ctx)

    assert len(events) == 1


# --------------------------------------------------------------------------- #
# Event firing, history, attributes
# --------------------------------------------------------------------------- #


async def test_trigger_event_payload(
    hass: HomeAssistant, make_config_entry, register_target
):
    """A classification fires whodunnit_trigger_detected with a full payload."""
    await _setup_sensor(hass, make_config_entry, register_target)
    events = async_capture_events(hass, EVENT_TRIGGER_DETECTED)

    await _fire(hass, "switch.test", "on", context=Context())

    assert len(events) == 1
    data = events[0].data
    assert data["entity_id"] == "switch.test"
    assert data["state"] == STATE_DEVICE
    assert data["source_type"] == "device"
    assert data["confidence"] == CONFIDENCE_HIGH
    assert "event_time" in data


async def test_history_log_accumulates(
    hass: HomeAssistant, make_config_entry, register_target
):
    _, sid = await _setup_sensor(hass, make_config_entry, register_target)

    await _fire(hass, "switch.test", "on", context=Context())
    await _fire(hass, "switch.test", "off", context=Context())

    log = _attrs(hass, sid)[ATTR_HISTORY_LOG]
    assert len(log) == 2
    # Newest entry is prepended.
    assert all(ATTR_SOURCE_TYPE in entry for entry in log)
    assert all(ATTR_CONTEXT_ID in entry for entry in log)


async def test_sensor_unavailable_when_target_not_registered(
    hass: HomeAssistant, make_config_entry
):
    """Without a registry entry for the target, the sensor is unavailable."""
    hass.states.async_set("switch.bare", "off")  # state only, not registered
    entry = make_config_entry("switch.bare")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "switch.bare_whodunnit"
    )
    assert hass.states.get(sensor_id).state == "unavailable"


# --------------------------------------------------------------------------- #
# State restoration
# --------------------------------------------------------------------------- #


async def _setup_with_restore(hass, make_config_entry, register_target, restored):
    """Pre-register the sensor (for a deterministic id) and seed restore cache."""
    register_target("switch.test")
    entry = make_config_entry("switch.test")
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "switch.test_whodunnit",
        suggested_object_id="restored", config_entry=entry,
    )
    mock_restore_cache(hass, [restored])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return "sensor.restored"


async def test_restores_previous_state(
    hass: HomeAssistant, make_config_entry, register_target
):
    sid = await _setup_with_restore(
        hass, make_config_entry, register_target,
        State(
            "sensor.restored", STATE_AUTOMATION,
            {
                ATTR_SOURCE_TYPE: "automation",
                ATTR_SOURCE_ID: "automation.x",
                ATTR_SOURCE_NAME: "Auto X",
                ATTR_CONFIDENCE: CONFIDENCE_MEDIUM,
                ATTR_HISTORY_LOG: [{ATTR_EVENT_TIME: "t", ATTR_SOURCE_TYPE: "x"}],
            },
        ),
    )
    state = hass.states.get(sid)
    assert state.state == STATE_AUTOMATION
    assert state.attributes[ATTR_SOURCE_ID] == "automation.x"
    assert state.attributes[ATTR_CONFIDENCE] == CONFIDENCE_MEDIUM
    assert len(state.attributes[ATTR_HISTORY_LOG]) == 1


async def test_ignores_invalid_restored_state(
    hass: HomeAssistant, make_config_entry, register_target
):
    sid = await _setup_with_restore(
        hass, make_config_entry, register_target,
        State("sensor.restored", "bogus_state", {}),
    )
    assert hass.states.get(sid).state == STATE_MONITORING


async def test_ignores_unavailable_restored_state(
    hass: HomeAssistant, make_config_entry, register_target
):
    sid = await _setup_with_restore(
        hass, make_config_entry, register_target,
        State("sensor.restored", "unavailable", {}),
    )
    assert hass.states.get(sid).state == STATE_MONITORING


# --------------------------------------------------------------------------- #
# Pure helper methods
# --------------------------------------------------------------------------- #


def test_build_cache_debug_before_any_classification():
    sensor = WhodunnitSensor("switch.test", {"name": "Dev"}, {}, {})
    debug = sensor._build_cache_debug()
    assert debug == {
        "last_classification_ago": None,
        "total_cache_entries": 0,
        "matched_entry": None,
    }


def test_clean_target_name_strips_device_prefix(hass: HomeAssistant):
    sensor = WhodunnitSensor("switch.lamp", {"name": "Living Room"}, {}, {})
    sensor.hass = hass
    hass.states.async_set(
        "switch.lamp", "on", {"friendly_name": "Living Room Lamp"}
    )
    assert sensor._get_clean_target_name() == "Lamp"


def test_clean_target_name_prefers_registry_name(hass: HomeAssistant):
    sensor = WhodunnitSensor("switch.lamp", {"name": "Living Room"}, {}, {})
    sensor.hass = hass
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "switch", "test", "lamp_unique", suggested_object_id="lamp"
    )
    ent_reg.async_update_entity("switch.lamp", name="Living Room Lamp")
    # The user-set registry name wins over state friendly_name.
    assert sensor._get_clean_target_name() == "Lamp"


def test_clean_target_name_without_prefix(hass: HomeAssistant):
    sensor = WhodunnitSensor("switch.lamp", {"name": "Garage"}, {}, {})
    sensor.hass = hass
    hass.states.async_set(
        "switch.lamp", "on", {"friendly_name": "Living Room Lamp"}
    )
    assert sensor._get_clean_target_name() == "Living Room Lamp"


def test_clean_target_name_equal_to_device_falls_back(hass: HomeAssistant):
    sensor = WhodunnitSensor("switch.lamp", {"name": "Lamp"}, {}, {})
    sensor.hass = hass
    hass.states.async_set("switch.lamp", "on", {"friendly_name": "Lamp"})
    assert sensor._get_clean_target_name() == "Lamp"


def test_icon_reflects_state():
    sensor = WhodunnitSensor("switch.test", {"name": "Dev"}, {}, {})
    sensor._state = STATE_DEVICE
    assert sensor.icon == "mdi:gesture-tap"
    sensor._state = "something_unmapped"
    assert sensor.icon == "mdi:help-circle-outline"
