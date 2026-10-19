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
    CHAR_PATCH_TABLE,
)
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator, char_id_lst, player_data_template
from ..util.helper import get_char_num_id
from ..util.battle_log_logger import log_battle_log_if_necessary
from ..util.tutorial_debug import event
from ..util.mission_lifecycle import apply_mission_event, reconcile_visible_missions
from ..util.operator_skin import default_evolve_skin, default_template_skin, OperatorSkinError

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


def _phase_number(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.startswith("PHASE_"):
        try:
            return int(value.removeprefix("PHASE_"))
        except ValueError:
            pass
    raise ValueError("invalid_evolve_phase")


def _rarity_number(value):
    if not isinstance(value, str) or not value.startswith("TIER_"):
        raise ValueError("invalid_character_rarity")
    try:
        return int(value.removeprefix("TIER_"))
    except ValueError as exc:
        raise ValueError("invalid_character_rarity") from exc


def _character_resource(char):
    table = const_json_loader[CHARACTER_TABLE]
    char_id = char["charId"] if "charId" in char else None
    if char_id not in table:
        raise ValueError("unknown_character_resource")
    return table[char_id].copy()


def _current_loadout_and_resource(char):
    """Return the active skill loadout and its authoritative character row."""
    current_tmpl = char["currentTmpl"] if "currentTmpl" in char else None
    if not current_tmpl:
        return char, _character_resource(char)
    templates = char["tmpl"] if "tmpl" in char else None
    if templates is None or current_tmpl not in templates:
        raise ValueError("invalid_current_template")
    if current_tmpl == char["charId"]:
        resource = _character_resource(char)
    else:
        patch_chars = const_json_loader[CHAR_PATCH_TABLE]["patchChars"]
        if current_tmpl not in patch_chars:
            raise ValueError("unknown_current_template_resource")
        resource = patch_chars[current_tmpl].copy()
    return templates[current_tmpl], resource


def _cost_plan(player_data, costs, gold_cost=0):
    """Validate a GameData cost list and return an immutable deduction plan."""
    if not isinstance(costs, list) or not isinstance(gold_cost, int) or gold_cost < 0:
        raise ValueError("invalid_cost_resource")
    inventory_costs = {}
    planned_gold = gold_cost
    known_items = const_json_loader[ITEM_TABLE]["items"]
    for row in costs:
        if not isinstance(row, dict):
            raise ValueError("invalid_cost_resource")
        item_id, count = row.get("id"), row.get("count")
        if not isinstance(item_id, str) or item_id not in known_items:
            raise ValueError("unknown_cost_item")
        if not isinstance(count, int) or count <= 0:
            raise ValueError("invalid_cost_resource")
        if row.get("type") == "GOLD" or item_id == "4001":
            planned_gold += count
        else:
            inventory_costs[item_id] = inventory_costs.get(item_id, 0) + count
    if player_data["status"]["gold"] < planned_gold:
        raise ValueError("insufficient_gold")
    for item_id, count in inventory_costs.items():
        owned = player_data["inventory"][item_id] if item_id in player_data["inventory"] else 0
        if owned < count:
            raise ValueError("insufficient_material")
    return {"gold": planned_gold, "items": inventory_costs}


def _apply_cost_plan(player_data, plan):
    player_data["status"]["gold"] = player_data["status"]["gold"] - plan["gold"]
    for item_id, count in plan["items"].items():
        player_data["inventory"][item_id] = player_data["inventory"][item_id] - count


def _refresh_active_skill_unlocks(char):
    """Reconcile only the active template, never exposing another profession."""
    loadout, resource = _current_loadout_and_resource(char)
    phase, level = int(char["evolvePhase"]), int(char["level"])
    resource_skills = resource.get("skills") or []
    stored_skills = loadout["skills"].copy() if "skills" in loadout else []
    if len(stored_skills) != len(resource_skills):
        raise ValueError("invalid_character_skill_schema")
    for stored, definition in zip(stored_skills, resource_skills):
        condition = definition.get("unlockCond") or {}
        required_phase = _phase_number(condition.get("phase", "PHASE_0"))
        required_level = int(condition.get("level", 1))
        stored["unlock"] = int(
            phase > required_phase
            or (phase == required_phase and level >= required_level)
        )
    loadout["skills"] = stored_skills


def _unlock_evolve_skin(player_data, char, old_phase, new_phase):
    """Unlock the built-in phase art and switch only from prior built-in art."""
    current_tmpl = char["currentTmpl"] if "currentTmpl" in char else None
    try:
        if current_tmpl:
            loadout = char["tmpl"][current_tmpl]
            old_skin = default_template_skin(char["charId"], current_tmpl, old_phase)
            new_skin = default_template_skin(char["charId"], current_tmpl, new_phase)
            if (loadout["skinId"] if "skinId" in loadout else None) == old_skin:
                loadout["skinId"] = new_skin
        else:
            old_skin = default_evolve_skin(char["charId"], old_phase)
            new_skin = default_evolve_skin(char["charId"], new_phase)
            if (char["skin"] if "skin" in char else None) == old_skin:
                char["skin"] = new_skin
    except OperatorSkinError:
        # Some lower-rarity operators have no distinct art for the new phase.
        return None
    player_data["skin"]["characterSkins"][new_skin] = 1
    if new_skin not in player_data["skin"]["skinTs"]:
        from ..util.faketime import faketime

        player_data["skin"]["skinTs"][new_skin] = int(faketime())
    return new_skin


def _charbuild_state(char, player_data, material_ids):
    return {
        "evolvePhase": char["evolvePhase"],
        "level": char["level"],
        "exp": char["exp"],
        "mainSkillLvl": char["mainSkillLvl"],
        "gold": player_data["status"]["gold"],
        "materials": {
            item_id: player_data["inventory"][item_id]
            if item_id in player_data["inventory"]
            else 0
            for item_id in sorted(material_ids)
        },
    }


def _is_charbuild_replay(journal, journal_key, fingerprint, char, player_data):
    if journal is None:
        return False
    row = journal.save_obj.get(journal_key)
    if row is None or row.get("fingerprint") != fingerprint:
        return False
    after = row.get("afterState")
    if not isinstance(after, dict) or not isinstance(after.get("materials"), dict):
        return False
    return after == _charbuild_state(char, player_data, set(after["materials"]))


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
    mission_changes = apply_mission_event(
        player_data,
        "upgrade_char",
        {"count": 1, "charInstId": char_inst_id, "charId": char["charId"]},
        event_id=request_fingerprint,
    )
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


@router.post("/charBuild/evolveChar")
@player_data_decorator
async def charBuild_evolveChar(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    dest_phase = request_json.get("destEvolvePhase")
    if not isinstance(char_inst_id, int) or isinstance(char_inst_id, bool):
        raise HTTPException(status_code=400, detail="invalid_evolve_char_request")
    if not isinstance(dest_phase, int) or isinstance(dest_phase, bool):
        raise HTTPException(status_code=400, detail="invalid_evolve_char_request")

    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    fingerprint = sha256(
        json.dumps(
            {"charInstId": char_inst_id, "destEvolvePhase": dest_phase},
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    journal = getattr(player_data, "extra_save", None)
    if _is_charbuild_replay(
        journal, "last_char_evolve", fingerprint, char, player_data
    ):
        event(request, "character_evolve_replay_ignored", charInstId=char_inst_id, charId=char["charId"])
        return {}
    try:
        resource = _character_resource(char)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        current_phase = int(char["evolvePhase"] if "evolvePhase" in char else -1)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_evolve_phase") from exc
    phases = resource.get("phases") or []
    if current_phase < 0 or current_phase >= len(phases):
        raise HTTPException(status_code=400, detail="invalid_evolve_phase")
    if dest_phase != current_phase + 1:
        raise HTTPException(status_code=400, detail="invalid_evolve_transition")
    if dest_phase >= len(phases):
        raise HTTPException(status_code=400, detail="unsupported_evolve_phase")
    if char["level"] != phases[current_phase].get("maxLevel") or char["exp"] != 0:
        raise HTTPException(status_code=400, detail="character_not_at_phase_cap")

    try:
        rarity = _rarity_number(resource.get("rarity"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    evolve_gold = const_json_loader[GAMEDATA_CONST]["evolveGoldCost"]
    if rarity < 1 or rarity > len(evolve_gold) or current_phase >= len(evolve_gold[rarity - 1]):
        raise HTTPException(status_code=400, detail="missing_evolve_gold_resource")
    gold_cost = evolve_gold[rarity - 1][current_phase]
    if gold_cost < 0:
        raise HTTPException(status_code=400, detail="unsupported_evolve_phase")
    costs = phases[dest_phase].get("evolveCost")
    if costs is None:
        raise HTTPException(status_code=400, detail="missing_evolve_cost_resource")
    try:
        plan = _cost_plan(player_data, costs, gold_cost)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    material_ids = set(plan["items"])
    before = _charbuild_state(char, player_data, material_ids)
    validation_copy = json.loads(json.dumps(char.copy()))
    validation_copy["evolvePhase"] = dest_phase
    validation_copy["level"] = 1
    validation_copy["exp"] = 0
    try:
        _refresh_active_skill_unlocks(validation_copy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # No mutation occurs before every eligibility and resource check above succeeds.
    _apply_cost_plan(player_data, plan)
    char["evolvePhase"] = dest_phase
    char["level"] = 1
    char["exp"] = 0
    _refresh_active_skill_unlocks(char)
    unlocked_skin = _unlock_evolve_skin(
        player_data, char, current_phase, dest_phase
    )
    mission_changes = reconcile_visible_missions(player_data)
    after = _charbuild_state(char, player_data, material_ids)
    if journal is not None:
        journal.save_obj["last_char_evolve"] = {
            "fingerprint": fingerprint,
            "afterState": after,
        }
    event(
        request,
        "character_evolved",
        charInstId=char_inst_id,
        charId=char["charId"],
        fromPhase=current_phase,
        toPhase=dest_phase,
        goldCost=plan["gold"],
        materialCosts=[{"id": item_id, "count": count} for item_id, count in plan["items"].items()],
        unlockedSkin=unlocked_skin,
        before=before,
        after=after,
        missionChanges=mission_changes,
    )
    return {}


@router.post("/charBuild/upgradeSkill")
@player_data_decorator
async def charBuild_upgradeSkill(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    target_level = request_json.get("targetLevel")
    if (
        not isinstance(char_inst_id, int)
        or isinstance(char_inst_id, bool)
        or not isinstance(target_level, int)
        or isinstance(target_level, bool)
    ):
        raise HTTPException(status_code=400, detail="invalid_upgrade_skill_request")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    fingerprint = sha256(
        json.dumps(
            {"charInstId": char_inst_id, "targetLevel": target_level},
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    journal = getattr(player_data, "extra_save", None)
    if _is_charbuild_replay(
        journal, "last_char_skill_upgrade", fingerprint, char, player_data
    ):
        event(request, "character_skill_upgrade_replay_ignored", charInstId=char_inst_id, charId=char["charId"])
        return {}
    try:
        current_level = int(char["mainSkillLvl"] if "mainSkillLvl" in char else 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_skill_level") from exc
    if current_level >= 7:
        raise HTTPException(status_code=400, detail="skill_at_level_cap")
    if target_level != current_level + 1 or target_level > 7:
        raise HTTPException(status_code=400, detail="invalid_skill_level_transition")

    try:
        _, skill_resource = _current_loadout_and_resource(char)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    levelups = skill_resource.get("allSkillLvlup") or []
    if current_level < 1 or current_level - 1 >= len(levelups):
        raise HTTPException(status_code=400, detail="missing_skill_upgrade_resource")
    row = levelups[current_level - 1]
    condition = row.get("unlockCond") or {}
    try:
        required_phase = _phase_number(condition.get("phase", "PHASE_0"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        required_level = int(condition.get("level", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_skill_unlock_resource") from exc
    if (
        char["evolvePhase"] < required_phase
        or (char["evolvePhase"] == required_phase and char["level"] < required_level)
    ):
        raise HTTPException(status_code=400, detail="skill_upgrade_condition_not_met")
    try:
        plan = _cost_plan(player_data, row.get("lvlUpCost"), 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    material_ids = set(plan["items"])
    before = _charbuild_state(char, player_data, material_ids)
    _apply_cost_plan(player_data, plan)
    char["mainSkillLvl"] = target_level
    mission_changes = apply_mission_event(
        player_data,
        "upgrade_skill",
        {"count": 1, "charInstId": char_inst_id, "charId": char["charId"], "targetLevel": target_level},
        event_id=fingerprint,
    )
    after = _charbuild_state(char, player_data, material_ids)
    if journal is not None:
        journal.save_obj["last_char_skill_upgrade"] = {
            "fingerprint": fingerprint,
            "afterState": after,
        }
    event(
        request,
        "character_skill_upgraded",
        charInstId=char_inst_id,
        charId=char["charId"],
        fromLevel=current_level,
        toLevel=target_level,
        materialCosts=[{"id": item_id, "count": count} for item_id, count in plan["items"].items()],
        goldCost=plan["gold"],
        before=before,
        after=after,
        missionChanges=mission_changes,
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
