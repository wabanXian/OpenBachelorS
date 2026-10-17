from math import ceil
from hashlib import sha256
import json

from fastapi import APIRouter, HTTPException
from fastapi import Request

from ..const.json_const import true, false, null
from ..const.filepath import (
    CONFIG_JSON,
    VERSION_JSON,
    CHARWORD_TABLE,
    CHARACTER_TABLE,
    ITEM_TABLE,
    GAMEDATA_CONST,
    MISSION_TABLE,
)
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator, char_id_lst, player_data_template
from ..util.helper import get_char_num_id
from ..util.battle_log_logger import log_battle_log_if_necessary
from ..util.tutorial_debug import event

router = APIRouter()


def _exp_item_values():
    # expItems is the authoritative item -> EXP mapping shipped by the client.
    item_table = const_json_loader[ITEM_TABLE]
    return {
        item_id: item["gainExp"]
        for item_id, item in item_table["expItems"]
    }


def _level_cost(character_exp_map, character_cost_map, phase, level, exp):
    """Return cumulative LMD spent inside the current level.

    Client resources provide a whole-level EXP requirement and whole-level LMD
    cost.  Partial-level cost is therefore the proportional ceiling.  This is
    resource-derived; it remains called out in the API audit until a live
    request/response confirms the client's rounding boundary.
    """
    required = character_exp_map[phase][level - 1]
    whole_cost = character_cost_map[phase][level - 1]
    return ceil(whole_cost * exp / required) if exp else 0


def calculate_char_upgrade(char, supplied_exp):
    """Calculate an upgrade without mutating player data."""
    char_table = const_json_loader[CHARACTER_TABLE]
    game_const = const_json_loader[GAMEDATA_CONST]
    char_id = char["charId"]
    phase = char["evolvePhase"]
    if char_id not in char_table:
        raise ValueError("unknown_character_resource")
    phases = char_table[char_id]["phases"]
    if phase < 0 or phase >= len(phases):
        raise ValueError("invalid_evolve_phase")

    level = char["level"]
    exp = char["exp"]
    max_level = phases[phase]["maxLevel"]
    if level < 1 or level > max_level or exp < 0:
        raise ValueError("invalid_character_progress")
    if level == max_level:
        if exp != 0:
            raise ValueError("invalid_max_level_exp")
        raise ValueError("character_at_level_cap")

    exp_map = game_const["characterExpMap"]
    cost_map = game_const["characterUpgradeCostMap"]
    if phase >= len(exp_map) or phase >= len(cost_map):
        raise ValueError("missing_phase_upgrade_resource")

    old_cost = _level_cost(exp_map, cost_map, phase, level, exp)
    remaining = supplied_exp
    gold_cost = 0
    while remaining and level < max_level:
        required = exp_map[phase][level - 1]
        if exp >= required:
            raise ValueError("invalid_character_exp")
        used = min(remaining, required - exp)
        new_exp = exp + used
        gold_cost += _level_cost(exp_map, cost_map, phase, level, new_exp) - old_cost
        exp = new_exp
        remaining -= used
        if exp == required:
            level += 1
            exp = 0
            old_cost = 0
        else:
            old_cost = _level_cost(exp_map, cost_map, phase, level, exp)

    # The official client normally prevents selecting records whose EXP would
    # overflow the current elite-phase cap. Reject instead of silently eating
    # an unknown amount of material.
    if remaining:
        raise ValueError("exp_exceeds_level_cap")
    return {"level": level, "exp": exp, "goldCost": gold_cost}


def _advance_upgrade_char_mission(player_data):
    """Advance existing UpgradeChar missions; never fabricate a mission row."""
    mission_table = const_json_loader[MISSION_TABLE]["missions"]
    changed = []
    if "GUIDE" not in player_data["mission"]["missions"]:
        return changed
    guide = player_data["mission"]["missions"]["GUIDE"]
    for mission_id, mission in guide:
        resource = mission_table[mission_id] if mission_id in mission_table else None
        if resource is None or resource["template"] != "UpgradeChar":
            continue
        if mission["state"] == 3:
            continue
        target = int(resource["param"][-1])
        progress_rows = mission["progress"].copy()
        progress = progress_rows[0]
        before = progress["value"]
        progress["value"] = min(target, before + 1)
        mission["progress"] = progress_rows
        if progress["value"] >= progress["target"]:
            mission["state"] = 2
        changed.append(
            {
                "missionId": mission_id,
                "before": before,
                "after": progress["value"],
                "state": mission["state"],
            }
        )
    return changed


def _upgrade_state(char, player_data, requested):
    inventory = player_data["inventory"]
    return {
        "level": char["level"],
        "exp": char["exp"],
        "gold": player_data["status"]["gold"],
        "materials": {
            item_id: inventory[item_id] if item_id in inventory else 0
            for item_id in sorted(requested)
        },
    }


@router.post("/charBuild/upgradeChar")
@player_data_decorator
async def charBuild_upgradeChar(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    exp_mats = request_json.get("expMats")
    if not isinstance(char_inst_id, int) or not isinstance(exp_mats, list) or not exp_mats:
        raise HTTPException(status_code=400, detail="invalid_upgrade_char_request")

    chars = player_data["troop"]["chars"]
    key = str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")

    exp_values = _exp_item_values()
    requested = {}
    for entry in exp_mats:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail="invalid_exp_mat")
        item_id = entry.get("id")
        count = entry.get("count")
        if item_id not in exp_values or not isinstance(count, int) or count <= 0:
            raise HTTPException(status_code=400, detail="invalid_exp_mat")
        requested[item_id] = requested.get(item_id, 0) + count

    supplied_exp = sum(exp_values[item_id] * count for item_id, count in requested.items())
    char = chars[key]
    request_fingerprint = sha256(
        json.dumps(
            {"charInstId": char_inst_id, "expMats": sorted(requested.items())},
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    journal = getattr(player_data, "extra_save", None)
    journal_row = (
        journal.save_obj.get("last_char_upgrade") if journal is not None else None
    )
    if (
        journal_row is not None
        and journal_row.get("fingerprint") == request_fingerprint
        and journal_row.get("afterState") == _upgrade_state(char, player_data, requested)
    ):
        event(
            request,
            "character_upgrade_replay_ignored",
            charInstId=char_inst_id,
            charId=char["charId"],
            fingerprint=request_fingerprint[:16],
        )
        return {}

    inventory = player_data["inventory"]
    for item_id, count in requested.items():
        owned = inventory[item_id] if item_id in inventory else 0
        if owned < count:
            raise HTTPException(status_code=400, detail="insufficient_exp_material")

    before = {
        "level": char["level"],
        "exp": char["exp"],
        "gold": player_data["status"]["gold"],
    }
    try:
        result = calculate_char_upgrade(char, supplied_exp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if player_data["status"]["gold"] < result["goldCost"]:
        raise HTTPException(status_code=400, detail="insufficient_gold")

    # All validation and arithmetic complete before the first mutation.
    for item_id, count in requested.items():
        inventory[item_id] = inventory[item_id] - count
    player_data["status"]["gold"] = before["gold"] - result["goldCost"]
    char["level"] = result["level"]
    char["exp"] = result["exp"]
    mission_changes = _advance_upgrade_char_mission(player_data)
    if journal is not None:
        journal.save_obj["last_char_upgrade"] = {
            "fingerprint": request_fingerprint,
            "afterState": _upgrade_state(char, player_data, requested),
        }

    event(
        request,
        "character_upgraded",
        charInstId=char_inst_id,
        charId=char["charId"],
        expMats=[{"id": item_id, "count": count} for item_id, count in requested.items()],
        suppliedExp=supplied_exp,
        goldCost=result["goldCost"],
        before=before,
        after={"level": result["level"], "exp": result["exp"], "gold": player_data["status"]["gold"]},
        missionChanges=mission_changes,
        costRoundingEvidence="resource_derived_proportional_ceiling_pending_live_confirmation",
    )
    return {}


@router.post("/charBuild/setDefaultSkill")
@player_data_decorator
async def charBuild_setDefaultSkill(player_data, request: Request):
    request_json = await request.json()

    char_num_id = request_json["charInstId"]
    default_skill_index = request_json["defaultSkillIndex"]

    if (
        "currentTmpl" in player_data["troop"]["chars"][str(char_num_id)]
        and player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"] is not None
    ):
        tmpl_id = player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"]
        player_data["troop"]["chars"][str(char_num_id)]["tmpl"][tmpl_id][
            "defaultSkillIndex"
        ] = default_skill_index
    else:
        player_data["troop"]["chars"][str(char_num_id)]["defaultSkillIndex"] = (
            default_skill_index
        )

    response = {}
    return response


@router.post("/charBuild/setEquipment")
@player_data_decorator
async def charBuild_setEquipment(player_data, request: Request):
    request_json = await request.json()

    char_num_id = request_json["charInstId"]
    equip_id = request_json["equipId"]

    if (
        "currentTmpl" in player_data["troop"]["chars"][str(char_num_id)]
        and player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"] is not None
    ):
        tmpl_id = player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"]
        player_data["troop"]["chars"][str(char_num_id)]["tmpl"][tmpl_id][
            "currentEquip"
        ] = equip_id
    else:
        player_data["troop"]["chars"][str(char_num_id)]["currentEquip"] = equip_id

    response = {}
    return response


@router.post("/charBuild/setCharVoiceLan")
@player_data_decorator
async def charBuild_setCharVoiceLan(player_data, request: Request):
    request_json = await request.json()

    for char_num_id in request_json["charList"]:
        player_data["troop"]["chars"][str(char_num_id)]["voiceLan"] = request_json[
            "voiceLan"
        ]

    response = {}
    return response


@router.post("/charBuild/changeCharSkin")
@player_data_decorator
async def charBuild_changeCharSkin(player_data, request: Request):
    request_json = await request.json()

    char_num_id = request_json["charInstId"]
    skin_id = request_json["skinId"]

    if (
        "currentTmpl" in player_data["troop"]["chars"][str(char_num_id)]
        and player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"] is not None
    ):
        tmpl_id = player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"]
        player_data["troop"]["chars"][str(char_num_id)]["tmpl"][tmpl_id]["skinId"] = (
            skin_id
        )
    else:
        player_data["troop"]["chars"][str(char_num_id)]["skin"] = skin_id

    response = {}
    return response


@router.post("/charBuild/batchSetCharVoiceLan")
@player_data_decorator
async def charBuild_batchSetCharVoiceLan(player_data, request: Request):
    request_json = await request.json()
    target_voice_lan = request_json["voiceLan"]

    charword_table = const_json_loader[CHARWORD_TABLE]

    char_id_set = set(char_id_lst.copy())

    for char_id, voice_lan_obj in charword_table["voiceLangDict"]:
        if char_id not in char_id_set:
            continue

        char_num_id = get_char_num_id(char_id)

        if target_voice_lan in voice_lan_obj["dict"]:
            voice_lan = target_voice_lan
        else:
            voice_lan = player_data_template["troop"]["chars"][str(char_num_id)][
                "voiceLan"
            ]

        player_data["troop"]["chars"][str(char_num_id)]["voiceLan"] = voice_lan

    response = {}
    return response


@router.post("/charBuild/changeCharTemplate")
@player_data_decorator
async def charBuild_changeCharTemplate(player_data, request: Request):
    request_json = await request.json()

    char_num_id = request_json["charInstId"]
    tmpl_id = request_json["templateId"]

    player_data["troop"]["chars"][str(char_num_id)]["currentTmpl"] = tmpl_id

    response = {}
    return response


@router.post("/charBuild/addonStage/battleStart")
@player_data_decorator
async def charBuild_addonStage_battleStart(player_data, request: Request):
    request_json = await request.json()

    response = {
        "result": 0,
        "battleId": "00000000-0000-0000-0000-000000000000",
    }
    return response


@router.post("/charBuild/addonStage/battleFinish")
@player_data_decorator
async def charBuild_addonStage_battleFinish(player_data, request: Request):
    request_json = await request.json()

    log_battle_log_if_necessary(player_data, request_json["data"])

    response = {
        "result": 0,
        "firstRewards": [],
    }
    return response


@router.post("/charBuild/changeSkinSpState")
@player_data_decorator
async def charBuild_changeSkinSpState(player_data, request: Request):
    request_json = await request.json()

    skin_id = request_json["skinId"]

    player_data["skin"]["skinSp"][skin_id] = request_json["isSpecial"]

    response = {}
    return response
