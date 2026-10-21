import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from starlette.requests import Request

from openbachelors.util.building_state import (
    BUILDING_SCHEMA_VERSION,
    EMPTY_OUTPUT_ROOM,
    BuildingRuleError,
    TUTORIAL_CLEAN_ORDER,
    build_or_upgrade_room,
    clean_room_slot,
    deliver_trade_order,
    ensure_tutorial_first_trade_order,
    repair_empty_room_type,
    reconcile_interrupted_clean_tutorial,
    tutorial_trade_readiness,
    validate_building_structure,
)
from openbachelors.util.player_data import DeltaJson, OverlayJson, post_tutorial_player_data_template
from openbachelors.util.player_data import sandbox_player_data_template
from openbachelors.util.player_data import PlayerData
from openbachelors.util import player_data as player_data_module
from openbachelors.tool.building_save import (
    inspect_save,
    migrate_save,
    repair_interrupted_slot33_tutorial,
    seed_tutorial_trade_order,
)
from openbachelors.bp.bp_building import building_deliveryOrder, router as building_router
from openbachelors.util.mission_lifecycle import update_has_room_missions


def fresh_player():
    return OverlayJson(post_tutorial_player_data_template, DeltaJson())


def make_request(payload, path="/"):
    body = json.dumps(payload).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def test_newbie_building_has_only_level_one_control_centre():
    player = fresh_player()
    building = player["building"]
    slots = building["roomSlots"].copy()
    assert set(slots) == {"slot_34"}
    assert slots["slot_34"] == {
        "level": 1,
        "state": 2,
        "roomId": "CONTROL",
        "charInstIds": [-1],
        "completeConstructTime": -1,
    }
    assert set(building["rooms"]["CONTROL"].copy()) == {"slot_34"}
    assert all(
        not rooms
        for room_type, rooms in building["rooms"].copy().items()
        if room_type != "CONTROL"
    )
    assert building["status"]["labor"]["value"] == 32
    assert building["status"]["labor"]["maxValue"] == 32


def test_sandbox_building_remains_fully_open():
    control = sandbox_player_data_template["building"]["roomSlots"]["slot_34"]
    assert control["level"] == 5
    assert control["state"] == 2
    assert len(sandbox_player_data_template["building"]["roomSlots"]) > 40


def test_building_structure_validator_is_read_only_and_accepts_newbie_state():
    player = fresh_player()
    before = player.copy()
    assert validate_building_structure(player) == []
    assert player.copy() == before


def test_building_structure_validator_reports_without_repairing_unknown_room():
    player = fresh_player()
    player["building"]["roomSlots"]["slot_33"] = {
        "level": 0,
        "state": 2,
        "roomId": "FUTURE_UNKNOWN_ROOM",
        "charInstIds": [],
        "completeConstructTime": -1,
    }
    before = player.copy()
    issues = validate_building_structure(player)
    assert {issue["code"] for issue in issues} == {"unknown_room_type"}
    assert player.copy() == before


def test_newbie_building_routes_are_registered():
    paths = {route.path for route in building_router.routes}
    assert {
        "/building/sync",
        "/building/cleanRoomSlot",
        "/building/buildRoom",
        "/building/upgradeRoom",
        "/building/completeUpgradeRoom",
        "/building/assignChar",
        "/building/changeManufactureSolution",
        "/building/changeStrategy",
        "/building/deliveryOrder",
    } <= paths


def test_confirmed_structural_cleaning_uses_level_one_room_model():
    player = fresh_player()
    player["inventory"]["3131"] = 15

    for slot_id in ("slot_33", "slot_27"):
        clean_room_slot(player, slot_id)

    slots = player["building"]["roomSlots"]
    assert slots["slot_33"]["level"] == 1
    assert slots["slot_33"]["roomId"] == "ELEVATOR"
    assert slots["slot_27"]["level"] == 1
    assert slots["slot_27"]["roomId"] == "ELEVATOR"


@pytest.mark.parametrize("slot_id", ("slot_26", "slot_25", "slot_24"))
def test_output_cleaning_uses_empty_none_intermediate_and_is_idempotent(slot_id):
    player = fresh_player()
    player["inventory"]["3131"] = 15
    before_inventory = player["inventory"]["3131"]
    before_labor = player["building"]["status"]["labor"]["value"]
    result = clean_room_slot(player, slot_id)
    assert result["providedLabor"] == 2
    assert player["building"]["roomSlots"][slot_id].copy() == EMPTY_OUTPUT_ROOM
    assert player["inventory"]["3131"] < before_inventory
    assert player["building"]["status"]["labor"]["value"] == before_labor + 2
    assert validate_building_structure(player) == []
    retry = clean_room_slot(player, slot_id)
    assert retry == {"items": [], "providedLabor": 0, "alreadyApplied": True}
    assert player["building"]["status"]["labor"]["value"] == before_labor + 2


def test_output_clean_and_build_chain_is_resource_driven_and_valid():
    player = fresh_player()
    player["inventory"]["3131"] = 30
    for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
        clean_room_slot(player, slot_id)
        assert validate_building_structure(player) == []

    for slot_id, room_type in (
        ("slot_26", "POWER"),
        ("slot_25", "MANUFACTURE"),
        ("slot_24", "TRADING"),
    ):
        unrelated_before = player["troop"].copy()
        build_or_upgrade_room(player, slot_id, room_type, 1)
        slot = player["building"]["roomSlots"][slot_id]
        assert slot["level"] == 1
        assert slot["state"] == 2
        assert slot["roomId"] == room_type
        assert player["building"]["rooms"][room_type][slot_id]
        assert validate_building_structure(player) == []
        assert player["troop"].copy() == unrelated_before


def prepare_part11_player():
    player = fresh_player()
    player["inventory"]["3131"] = 30
    for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
        clean_room_slot(player, slot_id)
    for slot_id, room_type in (
        ("slot_26", "POWER"),
        ("slot_25", "MANUFACTURE"),
        ("slot_24", "TRADING"),
    ):
        build_or_upgrade_room(player, slot_id, room_type, 1)
    player["building"]["roomSlots"]["slot_24"]["charInstIds"] = [502]
    player["building"]["chars"]["502"]["roomSlotId"] = "slot_24"
    player["building"]["chars"]["502"]["index"] = 0
    player["status"]["progress"] = 1111
    player["status"]["flags"]["obt/guide/l0-11/3c_station_trade"] = 1
    return player


def test_part11_trade_readiness_and_order_seed_are_exact_and_idempotent():
    player = prepare_part11_player()
    before_status = player["status"].copy()
    before_inventory = player["inventory"].copy()

    readiness = tutorial_trade_readiness(player)
    assert readiness["canSeedTutorialOrder"] is True
    assert readiness["stockCount"] == 0
    result = ensure_tutorial_first_trade_order(player)

    assert result["status"] == "seeded"
    assert player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == [
        {
            "type": "O_GOLD",
            "delivery": [{"type": "MATERIAL", "id": "3003", "count": 2}],
            "gain": {"type": "GOLD", "id": "4001", "count": 1000},
            "buff": [],
            "instId": 1,
            "isViolated": 0,
            "specGoldTag": {"activated": 0, "from": None},
        }
    ]
    assert validate_building_structure(player) == []
    assert player["status"].copy() == before_status
    assert player["inventory"].copy() == before_inventory
    assert ensure_tutorial_first_trade_order(player)["status"] == "already_present"
    assert len(player["building"]["rooms"]["TRADING"]["slot_24"]["stock"]) == 1


def test_custom_slot_clean_is_empty_buildable_and_atomic():
    player = fresh_player()
    player["inventory"]["3131"] = 2
    before = player["inventory"].copy().get("3131", 0)
    result = clean_room_slot(player, "slot_28")
    assert result["providedLabor"] == 2
    assert player["building"]["roomSlots"]["slot_28"].copy() == EMPTY_OUTPUT_ROOM
    assert player["inventory"]["3131"] == before - 1
    assert clean_room_slot(player, "slot_28")["alreadyApplied"] is True
    assert player["inventory"]["3131"] == before - 1


def test_part11_order_is_not_seeded_without_exact_tutorial_state():
    player = prepare_part11_player()
    player["status"]["progress"] = 1110
    before = player.copy()
    result = ensure_tutorial_first_trade_order(player)
    assert result["status"] == "not_applicable"
    assert result["reasons"] == ["progress_not_1111"]
    assert player.copy() == before


def test_trade_order_delivery_uses_persisted_order_and_is_idempotent():
    player = prepare_part11_player()
    ensure_tutorial_first_trade_order(player)
    player["inventory"]["3003"] = 3
    gold_before = player["status"]["gold"]

    result = deliver_trade_order(player, "slot_24", 1)

    assert result["delivery"] == [{"type": "MATERIAL", "id": "3003", "count": 2}]
    assert result["gain"] == {"type": "GOLD", "id": "4001", "count": 1000}
    assert player["inventory"]["3003"] == 1
    assert player["status"]["gold"] == gold_before + 1000
    assert player["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == []
    with pytest.raises(BuildingRuleError, match="trade_order_not_found"):
        deliver_trade_order(player, "slot_24", 1)
    assert player["inventory"]["3003"] == 1
    assert player["status"]["gold"] == gold_before + 1000


def test_trade_order_delivery_failure_is_atomic():
    player = prepare_part11_player()
    ensure_tutorial_first_trade_order(player)
    player["inventory"]["3003"] = 1
    before = player.copy()
    with pytest.raises(BuildingRuleError, match="insufficient_item:3003"):
        deliver_trade_order(player, "slot_24", 1)
    assert player.copy() == before


@pytest.mark.asyncio
async def test_delivery_order_endpoint_returns_delta_for_actual_protocol_fields():
    player = prepare_part11_player()
    ensure_tutorial_first_trade_order(player)
    player["inventory"]["3003"] = 3
    gold_before = player["status"]["gold"]
    response = await building_deliveryOrder.__wrapped__(
        player,
        make_request(
            {"slotId": "slot_24", "orderId": 1},
            path="/building/deliveryOrder",
        ),
    )
    assert response == {"result": 0}
    assert player["inventory"]["3003"] == 1
    assert player["status"]["gold"] == gold_before + 1000


def test_output_clean_failure_is_atomic():
    player = fresh_player()
    player["inventory"]["3131"] = 0
    before = player.copy()
    with pytest.raises(BuildingRuleError, match="insufficient_item"):
        clean_room_slot(player, "slot_26")
    assert player.copy() == before


@pytest.mark.parametrize(
    "progress,slot_id,story_id,next_progress",
    (
        (1102, "slot_26", "obt/guide/l0-11/1b_slot_clean", 1103),
        (1103, "slot_25", "obt/guide/l0-11/1c_slot_clean", 1104),
        (1104, "slot_24", "obt/guide/l0-11/1d_slot_clean", 1105),
    ),
)
def test_interrupted_output_clean_recovery_is_exact_and_idempotent(
    progress, slot_id, story_id, next_progress
):
    player = fresh_player()
    player["status"]["progress"] = progress
    player["building"]["roomSlots"][slot_id] = dict(EMPTY_OUTPUT_ROOM)
    unrelated = player["troop"].copy()
    result = reconcile_interrupted_clean_tutorial(player)
    assert result == {
        "storyId": story_id,
        "roomSlotId": slot_id,
        "progress": next_progress,
    }
    assert player["status"]["progress"] == next_progress
    assert player["status"]["flags"][story_id] == 1
    assert reconcile_interrupted_clean_tutorial(player) is None
    assert player["troop"].copy() == unrelated


def test_interrupted_clean_recovery_does_not_guess_unknown_state():
    player = fresh_player()
    player["status"]["progress"] = 1102
    player["building"]["roomSlots"]["slot_26"] = {
        "level": 0,
        "state": 2,
        "roomId": "NONE",
        "charInstIds": [],
        "completeConstructTime": -1,
    }
    before = player.copy()
    assert reconcile_interrupted_clean_tutorial(player) is None
    assert player.copy() == before


@pytest.mark.asyncio(loop_scope="session")
async def test_output_clean_and_build_survive_save_reload(monkeypatch):
    root = Path("test") / f".building_output_reload_{uuid4().hex}"
    monkeypatch.setattr(player_data_module, "MULTI_USER_SAV_DIRPATH", str(root))
    try:
        player = await PlayerData.create(player_id="output-reload", preset="post_tutorial")
        player["inventory"]["3131"] = 30
        for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
            clean_room_slot(player, slot_id)
        for slot_id, room_type in (
            ("slot_26", "POWER"),
            ("slot_25", "MANUFACTURE"),
            ("slot_24", "TRADING"),
        ):
            build_or_upgrade_room(player, slot_id, room_type, 1)
        await player.save()

        reloaded = await PlayerData.create(player_id="output-reload", preset="post_tutorial")
        assert validate_building_structure(reloaded) == []
        assert {
            slot_id: reloaded["building"]["roomSlots"][slot_id]["roomId"]
            for slot_id in ("slot_26", "slot_25", "slot_24")
        } == {
            "slot_26": "POWER",
            "slot_25": "MANUFACTURE",
            "slot_24": "TRADING",
        }
        assert reloaded["building"]["rooms"]["POWER"]["slot_26"]
        assert reloaded["building"]["rooms"]["MANUFACTURE"]["slot_25"]
        assert reloaded["building"]["rooms"]["TRADING"]["slot_24"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_tutorial_trade_order_survives_reload_without_duplication(monkeypatch):
    root = Path("test") / f".building_trade_reload_{uuid4().hex}"
    monkeypatch.setattr(player_data_module, "MULTI_USER_SAV_DIRPATH", str(root))
    try:
        player = await PlayerData.create(player_id="trade-reload", preset="post_tutorial")
        player["inventory"]["3131"] = 30
        for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
            clean_room_slot(player, slot_id)
        for slot_id, room_type in (
            ("slot_26", "POWER"),
            ("slot_25", "MANUFACTURE"),
            ("slot_24", "TRADING"),
        ):
            build_or_upgrade_room(player, slot_id, room_type, 1)
        player["building"]["roomSlots"]["slot_24"]["charInstIds"] = [502]
        player["building"]["chars"]["502"]["roomSlotId"] = "slot_24"
        player["building"]["chars"]["502"]["index"] = 0
        player["status"]["progress"] = 1111
        player["status"]["flags"]["obt/guide/l0-11/3c_station_trade"] = 1
        assert ensure_tutorial_first_trade_order(player)["status"] == "seeded"
        await player.save()

        reloaded = await PlayerData.create(player_id="trade-reload", preset="post_tutorial")
        stock = reloaded["building"]["rooms"]["TRADING"]["slot_24"]["stock"]
        assert len(stock) == 1
        assert stock[0]["delivery"].copy() == [
            {"type": "MATERIAL", "id": "3003", "count": 2}
        ]
        assert ensure_tutorial_first_trade_order(reloaded)["status"] == "already_present"
        await reloaded.save()
        final = await PlayerData.create(player_id="trade-reload", preset="post_tutorial")
        assert final["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == stock.copy()
        assert validate_building_structure(final) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_trade_order_delivery_survives_reload_and_cannot_repeat(monkeypatch):
    root = Path("test") / f".building_delivery_reload_{uuid4().hex}"
    monkeypatch.setattr(player_data_module, "MULTI_USER_SAV_DIRPATH", str(root))
    try:
        player = await PlayerData.create(player_id="delivery-reload", preset="post_tutorial")
        player["inventory"]["3131"] = 30
        for slot_id in ("slot_33", "slot_27", "slot_26", "slot_25", "slot_24"):
            clean_room_slot(player, slot_id)
        for slot_id, room_type in (
            ("slot_26", "POWER"),
            ("slot_25", "MANUFACTURE"),
            ("slot_24", "TRADING"),
        ):
            build_or_upgrade_room(player, slot_id, room_type, 1)
        player["building"]["roomSlots"]["slot_24"]["charInstIds"] = [502]
        player["building"]["chars"]["502"]["roomSlotId"] = "slot_24"
        player["building"]["chars"]["502"]["index"] = 0
        player["status"]["progress"] = 1111
        player["status"]["flags"]["obt/guide/l0-11/3c_station_trade"] = 1
        player["inventory"]["3003"] = 3
        ensure_tutorial_first_trade_order(player)
        gold_before = player["status"]["gold"]
        deliver_trade_order(player, "slot_24", 1)
        await player.save()

        reloaded = await PlayerData.create(player_id="delivery-reload", preset="post_tutorial")
        assert reloaded["inventory"]["3003"] == 1
        assert reloaded["status"]["gold"] == gold_before + 1000
        assert reloaded["building"]["rooms"]["TRADING"]["slot_24"]["stock"].copy() == []
        with pytest.raises(BuildingRuleError, match="trade_order_not_found"):
            deliver_trade_order(reloaded, "slot_24", 1)
        assert reloaded["inventory"]["3003"] == 1
        assert reloaded["status"]["gold"] == gold_before + 1000
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cleaned_slot_uses_client_room_type_enum_and_retry_is_idempotent():
    player = fresh_player()
    before_labor = player["building"]["status"]["labor"]["value"]
    first = clean_room_slot(player, "slot_33")
    assert first["providedLabor"] == 4
    assert player["building"]["roomSlots"]["slot_33"]["roomId"] == "ELEVATOR"
    assert player["building"]["roomSlots"]["slot_33"]["level"] == 1
    assert player["building"]["status"]["labor"]["value"] == before_labor + 4

    retry = clean_room_slot(player, "slot_33")
    assert retry["alreadyApplied"] is True
    assert retry["providedLabor"] == 0
    assert player["building"]["status"]["labor"]["value"] == before_labor + 4


def test_schema_v1_cleaned_structural_slot_is_repaired_without_touching_rooms():
    player = fresh_player()
    player["building"]["roomSlots"]["slot_33"] = {
        "level": 0,
        "state": 2,
        "roomId": "",
        "charInstIds": [],
        "completeConstructTime": -1,
    }
    player["building"]["roomSlots"]["slot_26"] = {
        "level": 1,
        "state": 2,
        "roomId": "POWER",
        "charInstIds": [-1],
        "completeConstructTime": -1,
    }

    assert repair_empty_room_type(player) == ["slot_33"]
    assert player["building"]["roomSlots"]["slot_33"]["roomId"] == "ELEVATOR"
    assert player["building"]["roomSlots"]["slot_33"]["level"] == 1
    assert player["building"]["roomSlots"]["slot_26"]["roomId"] == "POWER"


def test_interrupted_slot33_tutorial_repair_is_explicit_backed_up_and_minimal(monkeypatch):
    tmp_path = Path("test") / f".building_tutorial_repair_{uuid4().hex}"
    save_dir = (tmp_path / "multi_sav" / "presets" / "post_tutorial" / "copy").resolve()
    save_dir.mkdir(parents=True)
    delta = {
        "modified": {
            "status": {"progress": 1100, "gold": 123, "flags": {"existing": 1}},
            "building": {
                "roomSlots": {
                    "slot_33": {
                        "level": 1,
                        "state": 2,
                        "roomId": "ELEVATOR",
                        "charInstIds": [],
                        "completeConstructTime": -1,
                    }
                }
            },
            "troop": {"curCharInstId": 999},
        },
        "deleted": {},
    }
    (save_dir / "delta.json").write_text(json.dumps(delta), encoding="utf-8")
    (save_dir / "pending_delta.json").write_text(
        json.dumps({"modified": {}, "deleted": {}}), encoding="utf-8"
    )
    (save_dir / "extra.json").write_text(
        json.dumps({"player_preset": "post_tutorial", "building_schema_version": 2}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="without --yes"):
            repair_interrupted_slot33_tutorial(save_dir)
        archive = repair_interrupted_slot33_tutorial(save_dir, yes=True)
        repaired = json.loads((save_dir / "delta.json").read_text(encoding="utf-8"))
        assert archive.is_file()
        assert repaired["modified"]["status"]["progress"] == 1101
        assert repaired["modified"]["status"]["flags"]["existing"] == 1
        assert repaired["modified"]["status"]["flags"][
            "obt/guide/l0-11/0_building_init"
        ] == 1
        assert repaired["modified"]["building"] == delta["modified"]["building"]
        assert repaired["modified"]["troop"] == {"curCharInstId": 999}
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_path)


def test_trade_order_save_repair_is_explicit_backed_up_and_idempotent(monkeypatch):
    tmp_path = Path("test") / f".building_trade_repair_{uuid4().hex}"
    save_dir = (tmp_path / "multi_sav" / "presets" / "post_tutorial" / "copy").resolve()
    save_dir.mkdir(parents=True)
    player = prepare_part11_player().copy()
    delta = {
        "modified": {
            "status": player["status"],
            "building": player["building"],
            "inventory": player["inventory"],
            "troop": {"curCharInstId": 777},
        },
        "deleted": {},
    }
    (save_dir / "delta.json").write_text(json.dumps(delta), encoding="utf-8")
    (save_dir / "pending_delta.json").write_text(
        json.dumps({"modified": {}, "deleted": {}}), encoding="utf-8"
    )
    (save_dir / "extra.json").write_text(
        json.dumps({"player_preset": "post_tutorial", "building_schema_version": 2}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="without --yes"):
            seed_tutorial_trade_order(save_dir)
        archive, result = seed_tutorial_trade_order(save_dir, yes=True)
        repaired = json.loads((save_dir / "delta.json").read_text(encoding="utf-8"))
        assert archive.is_file()
        assert result["status"] == "seeded"
        assert repaired["modified"]["status"] == delta["modified"]["status"]
        assert repaired["modified"]["inventory"] == delta["modified"]["inventory"]
        assert repaired["modified"]["troop"] == {"curCharInstId": 777}
        assert len(
            repaired["modified"]["building"]["rooms"]["TRADING"]["slot_24"]["stock"]
        ) == 1
        with pytest.raises(RuntimeError, match="sole missing state"):
            seed_tutorial_trade_order(save_dir, yes=True)
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_explicit_legacy_migration_backs_up_and_only_marks_schema(monkeypatch):
    tmp_path = Path("test") / f".building_save_{uuid4().hex}"
    save_dir = (tmp_path / "multi_sav" / "presets" / "post_tutorial" / "legacy").resolve()
    save_dir.mkdir(parents=True)
    delta = {
        "modified": {
            "status": {"progress": 1100, "gold": 123},
            "building": {"chars": {"504": {"roomSlotId": "", "index": -1}}},
        },
        "deleted": {},
    }
    (save_dir / "delta.json").write_text(json.dumps(delta), encoding="utf-8")
    (save_dir / "extra.json").write_text(
        json.dumps({"player_preset": "post_tutorial"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    try:
        report = inspect_save(save_dir)
        assert report["safeAutomaticCandidate"] is True
        archive = migrate_save(save_dir, yes=True)

        assert archive.is_file()
        assert json.loads((save_dir / "delta.json").read_text()) == delta
        assert json.loads((save_dir / "extra.json").read_text())["building_schema_version"] == BUILDING_SCHEMA_VERSION
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_migration_refuses_accounts_with_real_building_history():
    tmp_path = Path("test") / f".building_save_{uuid4().hex}"
    save_dir = tmp_path / "legacy"
    save_dir.mkdir(parents=True)
    (save_dir / "delta.json").write_text(
        json.dumps({"modified": {"building": {"roomSlots": {"slot_24": {}}}}, "deleted": {}}),
        encoding="utf-8",
    )
    (save_dir / "extra.json").write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="real building structural changes"):
            migrate_save(save_dir, yes=True)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_migration_refuses_accounts_with_assigned_building_chars():
    tmp_path = Path("test") / f".building_save_{uuid4().hex}"
    save_dir = tmp_path / "legacy"
    save_dir.mkdir(parents=True)
    (save_dir / "delta.json").write_text(
        json.dumps(
            {
                "modified": {
                    "building": {
                        "chars": {
                            "2": {
                                "charInstId": 2,
                                "roomSlotId": "slot_24",
                                "index": 0,
                            }
                        }
                    }
                },
                "deleted": {},
            }
        ),
        encoding="utf-8",
    )
    (save_dir / "extra.json").write_text("{}", encoding="utf-8")
    try:
        report = inspect_save(save_dir)
        assert report["assignedBuildingChars"] == ["2"]
        assert report["safeAutomaticCandidate"] is False
        with pytest.raises(RuntimeError, match="real building structural changes"):
            migrate_save(save_dir, yes=True)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio(loop_scope="session")
async def test_legacy_account_is_not_silently_migrated(monkeypatch):
    root = Path("test") / f".building_legacy_{uuid4().hex}"
    monkeypatch.setattr(player_data_module, "MULTI_USER_SAV_DIRPATH", str(root))
    try:
        player = await PlayerData.create(player_id="legacy-building", preset="post_tutorial")
        player["status"]["progress"] = 1100
        await player.save()
        extra_path = Path(player.extra_save.filepath)
        extra = json.loads(extra_path.read_text(encoding="utf-8"))
        extra.pop("building_schema_version")
        extra_path.write_text(json.dumps(extra), encoding="utf-8")

        legacy = await PlayerData.create(player_id="legacy-building", preset="post_tutorial")
        assert legacy["status"]["progress"] == 1100
        assert legacy["building"]["roomSlots"]["slot_34"]["level"] == 5
        assert "building_schema_version" not in legacy.extra_save.save_obj

        legacy.extra_save.save_obj["building_schema_version"] = 1
        await legacy.save()
        migrated = await PlayerData.create(player_id="legacy-building", preset="post_tutorial")
        assert migrated["status"]["progress"] == 1100
        assert migrated["building"]["roomSlots"]["slot_34"]["level"] == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
