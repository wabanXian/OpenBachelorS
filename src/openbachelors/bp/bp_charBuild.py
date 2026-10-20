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
    BUILDING_DATA,
    UNIEQUIP_TABLE,
)
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator, char_id_lst, player_data_template
from ..util.helper import get_char_num_id
from ..util.battle_log_logger import log_battle_log_if_necessary
from ..util.tutorial_debug import event
from ..util.mission_lifecycle import apply_mission_event, reconcile_visible_missions
from ..util.operator_skin import default_evolve_skin, default_template_skin, OperatorSkinError
from ..util.faketime import faketime

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


def _potential_item_plan(char_resource, item_id):
    """Return the GameData-authorized item count for one potential rank."""
    exclusive = {
        char_resource.get("potentialItemId"),
        char_resource.get("activityPotentialItemId"),
        char_resource.get("classicPotentialItemId"),
    }
    exclusive.discard(None)
    if item_id in exclusive:
        return {item_id: 1}
    if not char_resource.get("canUseGeneralPotentialItem"):
        raise ValueError("general_potential_item_not_allowed")
    rarity = _rarity_number(char_resource.get("rarity"))
    profession = char_resource.get("profession")
    potential_items = const_json_loader[ITEM_TABLE]["potentialItems"]
    rarity_row = potential_items[str(rarity - 1)] if str(rarity - 1) in potential_items else None
    expected = rarity_row[profession] if rarity_row is not None and profession in rarity_row else None
    if item_id != expected:
        raise ValueError("invalid_potential_item")
    return {item_id: 4}


def _potential_state(char, player_data, item_id):
    return {
        "potentialRank": char["potentialRank"],
        "itemId": item_id,
        "itemCount": player_data["inventory"][item_id]
        if item_id in player_data["inventory"]
        else 0,
    }


def _training_room(player_data):
    slots = player_data["building"]["roomSlots"]
    rooms = player_data["building"]["rooms"]
    training_rooms = rooms["TRAINING"] if "TRAINING" in rooms else None
    if training_rooms is None:
        raise ValueError("training_room_not_built")
    found = []
    for slot_id, slot in slots:
        if slot["roomId"] == "TRAINING" and slot["state"] == 2 and slot["level"] > 0:
            if slot_id not in training_rooms:
                raise ValueError("training_room_schema_missing")
            found.append((slot_id, slot, training_rooms[slot_id]))
    if len(found) != 1:
        raise ValueError("training_room_not_built" if not found else "multiple_training_rooms")
    return found[0]


def _specialization_skill(char, skill_index):
    loadout, resource = _current_loadout_and_resource(char)
    skills = loadout["skills"] if "skills" in loadout else None
    definitions = resource.get("skills") or []
    if (
        not isinstance(skill_index, int)
        or isinstance(skill_index, bool)
        or skills is None
        or skill_index < 0
        or skill_index >= len(skills)
        or skill_index >= len(definitions)
    ):
        raise ValueError("invalid_skill_index")
    return loadout, skills[skill_index], definitions[skill_index]


def _set_specialization_skill_field(loadout, skill_index, field, value):
    skills = loadout["skills"].copy()
    row = skills[skill_index].copy()
    row[field] = value
    skills[skill_index] = row
    loadout["skills"] = skills


def _specialization_state(char, player_data, item_ids, slot_id, skill_index):
    _, stored_skill, _ = _specialization_skill(char, skill_index)
    room = player_data["building"]["rooms"]["TRAINING"][slot_id]
    return {
        "specializeLevel": stored_skill["specializeLevel"],
        "completeUpgradeTime": stored_skill["completeUpgradeTime"],
        "trainee": room["trainee"].copy(),
        "gold": player_data["status"]["gold"],
        "materials": {
            item_id: player_data["inventory"][item_id]
            if item_id in player_data["inventory"]
            else 0
            for item_id in sorted(item_ids)
        },
    }


def _module_loadout(char, template_id):
    char_id = char["charId"]
    if "tmpl" in char and len(char["tmpl"]):
        if not isinstance(template_id, str) or template_id not in char["tmpl"]:
            raise ValueError("invalid_module_template")
        return char["tmpl"][template_id], template_id
    if template_id not in (None, "", char_id):
        raise ValueError("invalid_module_template")
    return char, None


def _module_resource(char, template_id, equip_id):
    table = const_json_loader[UNIEQUIP_TABLE]
    if equip_id not in table["equipDict"]:
        raise ValueError("unknown_module")
    resource = table["equipDict"][equip_id].copy()
    owner = template_id if resource.get("tmplId") else char["charId"]
    expected_owner = resource.get("tmplId") or resource.get("charId")
    if owner != expected_owner:
        raise ValueError("module_not_compatible")
    char_equips = table["charEquip"]
    list_key = template_id if template_id in char_equips else char["charId"]
    if list_key not in char_equips:
        raise ValueError("module_not_compatible")
    # ConstJson's list wrapper intentionally only implements dictionary-style
    # membership.  Materialise the resource list before checking the equip id.
    if equip_id not in char_equips[list_key].copy():
        raise ValueError("module_not_compatible")
    return resource


def _module_missions_complete(player_data, resource):
    missions = player_data["equipment"]["missions"]
    for mission_id in resource.get("missionList") or []:
        if mission_id not in missions:
            return False
        row = missions[mission_id]
        if row["value"] < row["target"]:
            return False
    return True


def _module_state(loadout, player_data, equip_id, item_ids):
    equip = loadout["equip"]
    return {
        "equip": equip[equip_id].copy() if equip_id in equip else None,
        "currentEquip": loadout["currentEquip"],
        "gold": player_data["status"]["gold"],
        "materials": {
            item_id: player_data["inventory"][item_id]
            if item_id in player_data["inventory"]
            else 0
            for item_id in sorted(item_ids)
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


@router.post("/charBuild/boostPotential")
@player_data_decorator
async def charBuild_boostPotential(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    item_id = request_json.get("itemId")
    target_rank = request_json.get("targetRank")
    if (
        not isinstance(char_inst_id, int)
        or isinstance(char_inst_id, bool)
        or not isinstance(item_id, str)
        or not item_id
        or not isinstance(target_rank, int)
        or isinstance(target_rank, bool)
    ):
        raise HTTPException(status_code=400, detail="invalid_boost_potential_request")

    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    fingerprint = sha256(
        json.dumps(
            {"charInstId": char_inst_id, "itemId": item_id, "targetRank": target_rank},
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    journal = getattr(player_data, "extra_save", None)
    journal_row = journal.save_obj.get("last_char_potential") if journal is not None else None
    if (
        journal_row is not None
        and journal_row.get("fingerprint") == fingerprint
        and journal_row.get("afterState") == _potential_state(char, player_data, item_id)
    ):
        event(
            request,
            "character_potential_replay_ignored",
            charInstId=char_inst_id,
            charId=char["charId"],
            fingerprint=fingerprint[:16],
        )
        return {"resultOneSuc": 1}

    try:
        resource = _character_resource(char)
        current_rank = int(char["potentialRank"])
        max_rank = int(resource.get("maxPotentialLevel", len(resource.get("potentialRanks") or [])))
        plan = _potential_item_plan(resource, item_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if current_rank < 0 or current_rank > max_rank:
        raise HTTPException(status_code=400, detail="invalid_potential_rank")
    if current_rank >= max_rank:
        raise HTTPException(status_code=400, detail="potential_at_rank_cap")
    if target_rank != current_rank + 1:
        raise HTTPException(status_code=400, detail="invalid_potential_rank_transition")
    count = plan[item_id]
    owned = player_data["inventory"][item_id] if item_id in player_data["inventory"] else 0
    if owned < count:
        raise HTTPException(status_code=400, detail="insufficient_potential_item")

    before = _potential_state(char, player_data, item_id)
    player_data["inventory"][item_id] = owned - count
    char["potentialRank"] = target_rank
    mission_changes = reconcile_visible_missions(player_data)
    after = _potential_state(char, player_data, item_id)
    if journal is not None:
        journal.save_obj["last_char_potential"] = {
            "fingerprint": fingerprint,
            "afterState": after,
        }
    event(
        request,
        "character_potential_boosted",
        charInstId=char_inst_id,
        charId=char["charId"],
        fromRank=current_rank,
        toRank=target_rank,
        itemId=item_id,
        itemCount=count,
        before=before,
        after=after,
        missionChanges=mission_changes,
    )
    return {"resultOneSuc": 1}


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


@router.post("/charBuild/upgradeSpecialization")
@player_data_decorator
async def charBuild_upgradeSpecialization(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    skill_index = request_json.get("skillIndex")
    target_level = request_json.get("targetLevel")
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (char_inst_id, skill_index, target_level)
    ):
        raise HTTPException(status_code=400, detail="invalid_specialization_request")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    fingerprint = sha256(
        json.dumps(request_json, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    journal = getattr(player_data, "extra_save", None)
    try:
        slot_id, slot, room = _training_room(player_data)
        loadout, stored_skill, definition = _specialization_skill(char, skill_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    journal_row = journal.save_obj.get("last_specialization_start") if journal is not None else None
    if journal_row is not None and journal_row.get("fingerprint") == fingerprint:
        after = journal_row.get("afterState")
        if isinstance(after, dict) and after == _specialization_state(
            char, player_data, set(after.get("materials", {})), slot_id, skill_index
        ):
            event(request, "specialization_start_replay_ignored", charInstId=char_inst_id, skillIndex=skill_index)
            return {}

    current_level = stored_skill["specializeLevel"]
    if char["evolvePhase"] < 2 or char["mainSkillLvl"] < 7:
        raise HTTPException(status_code=400, detail="specialization_condition_not_met")
    if not stored_skill["unlock"]:
        raise HTTPException(status_code=400, detail="skill_not_unlocked")
    if current_level >= 3:
        raise HTTPException(status_code=400, detail="specialization_at_level_cap")
    if target_level != current_level + 1:
        raise HTTPException(status_code=400, detail="invalid_specialization_transition")
    levelups = definition.get("levelUpCostCond") or []
    if current_level >= len(levelups):
        raise HTTPException(status_code=400, detail="missing_specialization_resource")
    row = levelups[current_level]
    condition = row.get("unlockCond") or {}
    try:
        required_phase = _phase_number(condition.get("phase", "PHASE_2"))
        required_level = int(condition.get("level", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_specialization_resource") from exc
    if char["evolvePhase"] < required_phase or (
        char["evolvePhase"] == required_phase and char["level"] < required_level
    ):
        raise HTTPException(status_code=400, detail="specialization_condition_not_met")
    room_level = int(slot["level"])
    phases = const_json_loader[BUILDING_DATA]["trainingData"]["phases"]
    if room_level < 1 or room_level > len(phases) or phases[room_level - 1]["specSkillLvlLimit"] < target_level:
        raise HTTPException(status_code=400, detail="training_room_level_insufficient")
    trainee = room["trainee"]
    if trainee["state"] not in (0, 3) or (
        trainee["state"] == 3 and trainee["charInstId"] not in (-1, char_inst_id)
    ):
        raise HTTPException(status_code=400, detail="training_room_busy")
    building_char = player_data["building"]["chars"][key] if key in player_data["building"]["chars"] else None
    if building_char is None:
        raise HTTPException(status_code=400, detail="building_character_missing")
    occupied_slot = building_char["roomSlotId"]
    if occupied_slot not in ("", slot_id):
        raise HTTPException(status_code=400, detail="training_operator_assigned_elsewhere")
    try:
        duration = int(row.get("lvlUpTime"))
        if duration <= 0:
            raise ValueError
        plan = _cost_plan(player_data, row.get("levelUpCost"), 0)
    except (TypeError, ValueError) as exc:
        detail = str(exc) if str(exc) else "invalid_specialization_resource"
        raise HTTPException(status_code=400, detail=detail) from exc

    item_ids = set(plan["items"])
    before = _specialization_state(char, player_data, item_ids, slot_id, skill_index)
    now = int(faketime())
    complete_time = now + duration
    _apply_cost_plan(player_data, plan)
    _set_specialization_skill_field(loadout, skill_index, "completeUpgradeTime", complete_time)
    room["state"] = 1
    room["lastUpdateTime"] = now
    room["completeWorkTime"] = complete_time
    room["trainee"] = {
        "charInstId": char_inst_id,
        "state": 1,
        "targetSkill": skill_index,
        "processPoint": 0,
        "speed": 1,
    }
    slot_ids = slot["charInstIds"].copy()
    while len(slot_ids) < 2:
        slot_ids.append(-1)
    slot_ids[1] = char_inst_id
    slot["charInstIds"] = slot_ids
    building_char["roomSlotId"] = slot_id
    building_char["index"] = 1
    after = _specialization_state(char, player_data, item_ids, slot_id, skill_index)
    if journal is not None:
        journal.save_obj["last_specialization_start"] = {
            "fingerprint": fingerprint,
            "afterState": after,
            "slotId": slot_id,
        }
    event(
        request,
        "specialization_started",
        charInstId=char_inst_id,
        charId=char["charId"],
        skillIndex=skill_index,
        fromLevel=current_level,
        targetLevel=target_level,
        roomSlotId=slot_id,
        roomLevel=room_level,
        duration=duration,
        completeUpgradeTime=complete_time,
        materialCosts=[{"id": item_id, "count": count} for item_id, count in plan["items"].items()],
        goldCost=plan["gold"],
        before=before,
        after=after,
    )
    return {}


@router.post("/charBuild/completeUpgradeSpecialization")
@player_data_decorator
async def charBuild_completeUpgradeSpecialization(player_data, request: Request):
    await request.json()
    journal = getattr(player_data, "extra_save", None)
    try:
        slot_id, slot, room = _training_room(player_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trainee = room["trainee"]
    char_inst_id = trainee["charInstId"]
    skill_index = trainee["targetSkill"]
    if trainee["state"] != 1 or char_inst_id < 0 or skill_index < 0:
        last = journal.save_obj.get("last_specialization_complete") if journal is not None else None
        if last is not None:
            event(request, "specialization_complete_replay_ignored", **last["identity"])
            return {}
        raise HTTPException(status_code=400, detail="no_specialization_in_progress")
    key = str(char_inst_id)
    if key not in player_data["troop"]["chars"]:
        raise HTTPException(status_code=400, detail="specialization_character_missing")
    char = player_data["troop"]["chars"][key]
    try:
        loadout, stored_skill, _ = _specialization_skill(char, skill_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    complete_time = int(stored_skill["completeUpgradeTime"])
    now = int(faketime())
    if complete_time < 0 or now < complete_time:
        raise HTTPException(status_code=400, detail="specialization_not_complete")
    current_level = int(stored_skill["specializeLevel"])
    target_level = current_level + 1
    if target_level > 3:
        raise HTTPException(status_code=400, detail="specialization_at_level_cap")

    _set_specialization_skill_field(loadout, skill_index, "specializeLevel", target_level)
    _set_specialization_skill_field(loadout, skill_index, "completeUpgradeTime", -1)
    room["state"] = 0
    room["lastUpdateTime"] = now
    room["completeWorkTime"] = -1
    room["trainee"] = {
        "charInstId": char_inst_id,
        "state": 3,
        "targetSkill": skill_index,
        "processPoint": -1,
        "speed": 1,
    }
    identity = {"charInstId": char_inst_id, "charId": char["charId"], "skillIndex": skill_index, "targetLevel": target_level}
    if journal is not None:
        journal.save_obj["last_specialization_complete"] = {"identity": identity}
    event(request, "specialization_completed", roomSlotId=slot_id, **identity)
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
    char_inst_id = request_json.get("charInstId")
    template_id = request_json.get("templateId")
    equip_id = request_json.get("equipId")
    if not isinstance(char_inst_id, int) or isinstance(char_inst_id, bool) or equip_id is not None and not isinstance(equip_id, str):
        raise HTTPException(status_code=400, detail="invalid_set_equipment_request")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    try:
        loadout, normalized_template = _module_loadout(char, template_id)
        if equip_id:
            _module_resource(char, normalized_template, equip_id)
            if equip_id not in loadout["equip"] or loadout["equip"][equip_id]["locked"] or loadout["equip"][equip_id]["level"] < 1:
                raise ValueError("module_not_unlocked")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    loadout["currentEquip"] = equip_id or None
    event(request, "module_equipment_changed", charInstId=char_inst_id, charId=char["charId"], templateId=normalized_template, equipId=equip_id or None)
    return {}


@router.post("/charBuild/unlockEquipment")
@player_data_decorator
async def charBuild_unlockEquipment(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    template_id = request_json.get("templateId")
    equip_id = request_json.get("equipId")
    if not isinstance(char_inst_id, int) or isinstance(char_inst_id, bool) or not isinstance(equip_id, str):
        raise HTTPException(status_code=400, detail="invalid_unlock_equipment_request")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    try:
        loadout, normalized_template = _module_loadout(char, template_id)
        resource = _module_resource(char, normalized_template, equip_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resource.get("type") != "ADVANCED":
        raise HTTPException(status_code=400, detail="module_does_not_use_unlock_flow")
    if equip_id in loadout["equip"] and not loadout["equip"][equip_id]["locked"]:
        raise HTTPException(status_code=400, detail="module_already_unlocked")
    try:
        required_phase = _phase_number(resource.get("unlockEvolvePhase"))
        required_level = int(resource.get("unlockLevel", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_module_resource") from exc
    if char["evolvePhase"] < required_phase or char["evolvePhase"] == required_phase and char["level"] < required_level:
        raise HTTPException(status_code=400, detail="module_level_condition_not_met")
    favor_required = int((resource.get("unlockFavors") or {}).get("1", 0))
    if char["favorPoint"] < favor_required:
        raise HTTPException(status_code=400, detail="module_favor_condition_not_met")
    if not _module_missions_complete(player_data, resource):
        raise HTTPException(status_code=400, detail="module_missions_incomplete")
    costs = (resource.get("itemCost") or {}).get("1")
    try:
        plan = _cost_plan(player_data, costs, 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    before = _module_state(loadout, player_data, equip_id, set(plan["items"]))
    _apply_cost_plan(player_data, plan)
    equips = loadout["equip"].copy()
    equips[equip_id] = {"hide": 0, "locked": 0, "level": 1}
    loadout["equip"] = equips
    after = _module_state(loadout, player_data, equip_id, set(plan["items"]))
    event(request, "module_unlocked", charInstId=char_inst_id, charId=char["charId"], templateId=normalized_template, equipId=equip_id, before=before, after=after)
    return {}


@router.post("/charBuild/upgradeEquipment")
@player_data_decorator
async def charBuild_upgradeEquipment(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    template_id = request_json.get("templateId")
    equip_id = request_json.get("equipId")
    target_level = request_json.get("targetLevel")
    if not isinstance(char_inst_id, int) or isinstance(char_inst_id, bool) or not isinstance(equip_id, str) or not isinstance(target_level, int) or isinstance(target_level, bool):
        raise HTTPException(status_code=400, detail="invalid_upgrade_equipment_request")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    try:
        loadout, normalized_template = _module_loadout(char, template_id)
        resource = _module_resource(char, normalized_template, equip_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if equip_id not in loadout["equip"] or loadout["equip"][equip_id]["locked"]:
        raise HTTPException(status_code=400, detail="module_not_unlocked")
    current_level = int(loadout["equip"][equip_id]["level"])
    costs_by_level = resource.get("itemCost") or {}
    max_level = max((int(level) for level in costs_by_level), default=0)
    if current_level >= max_level:
        raise HTTPException(status_code=400, detail="module_at_level_cap")
    if target_level != current_level + 1:
        raise HTTPException(status_code=400, detail="invalid_module_level_transition")
    favor_required = int((resource.get("unlockFavors") or {}).get(str(target_level), 0))
    if char["favorPoint"] < favor_required:
        raise HTTPException(status_code=400, detail="module_favor_condition_not_met")
    try:
        plan = _cost_plan(player_data, costs_by_level.get(str(target_level)), 0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    before = _module_state(loadout, player_data, equip_id, set(plan["items"]))
    _apply_cost_plan(player_data, plan)
    equips = loadout["equip"].copy()
    equip = equips[equip_id].copy()
    equip["level"] = target_level
    equips[equip_id] = equip
    loadout["equip"] = equips
    after = _module_state(loadout, player_data, equip_id, set(plan["items"]))
    event(request, "module_upgraded", charInstId=char_inst_id, charId=char["charId"], templateId=normalized_template, equipId=equip_id, fromLevel=current_level, toLevel=target_level, before=before, after=after)
    return {}


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
    event(
        request,
        "charbuild_addon_stage_rejected",
        stageId=request_json.get("stageId"),
        charId=request_json.get("charId"),
        reason="addon_stage_settlement_not_verified",
    )
    raise HTTPException(status_code=400, detail="addon_stage_settlement_not_verified")


@router.post("/charBuild/addonStage/battleFinish")
@player_data_decorator
async def charBuild_addonStage_battleFinish(player_data, request: Request):
    request_json = await request.json()
    if "data" in request_json:
        log_battle_log_if_necessary(player_data, request_json["data"])
    event(
        request,
        "charbuild_addon_stage_rejected",
        reason="addon_stage_settlement_not_verified",
    )
    raise HTTPException(status_code=400, detail="addon_stage_settlement_not_verified")


@router.post("/charBuild/changeSkinSpState")
@player_data_decorator
async def charBuild_changeSkinSpState(player_data, request: Request):
    request_json = await request.json()

    skin_id = request_json["skinId"]

    player_data["skin"]["skinSp"][skin_id] = request_json["isSpecial"]

    response = {}
    return response
