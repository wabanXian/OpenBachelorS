"""Resource-driven module mission progress from persisted battle evidence.

Only templates whose conditions are represented unambiguously in the decoded
PC battle log are evaluated here.  Unsupported templates are still exposed to
the client with their real targets, but are never advanced speculatively.
"""

from copy import deepcopy

from ..const.filepath import UNIEQUIP_TABLE
from .const_json_loader import const_json_loader


SUPPORTED_TEMPLATES = {
    "EquipmentSkillCast",
    "EquipmentSkillCastStage",
    "EquipmentSkillCastBattle",
    "EquipmentCharKilled",
    "EquipmentCharKilledStage",
    "EquipmentBattleCharKilled",
    "EquipmentEventTotal",
    "EquipmentEventStageMore",
    "EquipmentEventBattleMore",
    "EquipmentSquadNoAnyDead",
}


def _phase(value):
    if isinstance(value, str) and value.startswith("PHASE_"):
        return int(value.removeprefix("PHASE_"))
    return int(value or 0)


def module_mission_target(resource):
    resource = resource.copy() if hasattr(resource, "copy") else resource
    template, params = resource["template"], resource.get("paramList") or []
    if template in {"EquipmentSkillCast", "EquipmentCharKilled", "EquipmentEventTotal"}:
        index = {"EquipmentSkillCast": 2, "EquipmentCharKilled": 1, "EquipmentEventTotal": 2}[template]
        return int(params[index])
    if template in {
        "EquipmentSkillCastBattle",
        "EquipmentBattleCharKilled",
        "EquipmentEventBattleMore",
        "EquipmentDeployStage",
        "EquipmentSquadProStage",
        "EquipmentSquadStarStage",
    }:
        return int(params[0])
    if template in {
        "EquipmentDeployTotal",
        "EquipmentDamageTotal",
        "EquipmentDamageTotalWithToken",
        "EquipmentDamageTypeTotal",
        "EquipmentElementBurst",
    }:
        return int(params[1] if template != "EquipmentDeployTotal" else params[0])
    # Specific-stage and one-battle predicates complete once.
    return 1


def _owned_module_resources(player_data):
    table = const_json_loader[UNIEQUIP_TABLE]
    owned = set()
    for _, char in player_data["troop"]["chars"]:
        char = char.copy()
        owned.add(char["charId"])
        for tmpl_id in (char.get("tmpl") or {}):
            owned.add(tmpl_id)
    for _, equip in table["equipDict"]:
        equip = equip.copy()
        owner = equip.get("tmplId") or equip.get("charId")
        if (
            equip.get("type") == "ADVANCED"
            and owner in owned
            and equip.get("missionList")
        ):
            yield equip


def _module_is_eligible(player_data, equip):
    owner = equip.get("tmplId") or equip["charId"]
    for _, candidate in player_data["troop"]["chars"]:
        if candidate["charId"] != equip["charId"]:
            continue
        char = candidate.copy()
        loadout = (char.get("tmpl") or {}).get(owner, char)
        phase = int(loadout.get("evolvePhase", char.get("evolvePhase", 0)))
        level = int(loadout.get("level", char.get("level", 0)))
        required_phase = _phase(equip.get("unlockEvolvePhase"))
        return phase > required_phase or (
            phase == required_phase and level >= int(equip.get("unlockLevel", 0))
        )
    return False


def reconcile_module_missions(player_data):
    """Expose real module missions without granting any progress."""
    table = const_json_loader[UNIEQUIP_TABLE]
    missions = player_data["equipment"]["missions"]
    changes = []
    for equip in _owned_module_resources(player_data):
        if not _module_is_eligible(player_data, equip):
            continue
        for mission_id in equip["missionList"]:
            if mission_id in missions or mission_id not in table["missionList"]:
                continue
            target = module_mission_target(table["missionList"][mission_id])
            missions[mission_id] = {"value": 0, "target": target}
            changes.append({"missionId": mission_id, "target": target})
    return changes


def _stats(decoded):
    battle = decoded.get("battleData") or {}
    return battle.get("stats") or {}


def _counter(rows, key_fields, wanted):
    total = 0
    for row in rows or []:
        key = row.get("Key") or {}
        if all(key.get(field) == value for field, value in zip(key_fields, wanted)):
            total += int(row.get("Value", 0) or 0)
    return total


def _deployed(stats, char_id):
    return _counter(stats.get("charStats"), ("charId", "counterType"), (char_id, "SPAWN")) > 0


def _dead_count(stats):
    return sum(
        int(row.get("Value", 0) or 0)
        for row in stats.get("charStats") or []
        if (row.get("Key") or {}).get("counterType") == "DEAD"
    )


def _skill_count(stats, char_id, skill_ids):
    return sum(
        _counter(stats.get("skillTrigStats"), ("charId", "skillId"), (char_id, skill_id))
        for skill_id in skill_ids.split(";")
    )


def _extra_count(stats, key):
    return int((stats.get("extraBattleInfo") or {}).get(key, 0) or 0)


def _mission_increment(resource, decoded, stars, squad_char_ids):
    template, p, stats = resource["template"], resource["paramList"], _stats(decoded)
    stage_id = decoded.get("stageId")
    if template in {"EquipmentSkillCast", "EquipmentCharKilled", "EquipmentEventTotal"}:
        owner = p[0]
    elif template == "EquipmentSkillCastBattle":
        owner = p[3]
    elif template in {"EquipmentBattleCharKilled", "EquipmentEventBattleMore"}:
        owner = p[1]
    else:
        owner = p[-1]
    if owner not in squad_char_ids:
        return 0
    if template == "EquipmentSkillCast":
        owner, skill_ids = p[0], p[1]
        return _skill_count(stats, owner, skill_ids) if _deployed(stats, owner) else 0
    if template == "EquipmentSkillCastStage":
        required_stars, stage, skill_ids, count, owner = int(p[0]), p[1], p[2], int(p[3]), p[4]
        return int(stage_id == stage and stars >= required_stars and _deployed(stats, owner) and _skill_count(stats, owner, skill_ids) >= count)
    if template == "EquipmentSkillCastBattle":
        _, skill_ids, count, owner = p
        return int(_deployed(stats, owner) and _skill_count(stats, owner, skill_ids) >= int(count))
    if template == "EquipmentCharKilled":
        owner = p[0]
        return _extra_count(stats, f"KILL_COUNT,{owner}") if _deployed(stats, owner) else 0
    if template == "EquipmentCharKilledStage":
        required_stars, stage, killer, count, owner = int(p[0]), p[1], p[2], int(p[3]), p[4]
        return int(stage_id == stage and stars >= required_stars and _deployed(stats, owner) and _extra_count(stats, f"KILL_COUNT,{killer}") >= count)
    if template == "EquipmentBattleCharKilled":
        _, owner, count = p
        return int(_deployed(stats, owner) and _extra_count(stats, f"KILL_COUNT,{owner}") >= int(count))
    if template == "EquipmentEventTotal":
        owner, event_key = p[0], p[1]
        return _extra_count(stats, event_key) if _deployed(stats, owner) else 0
    if template == "EquipmentEventStageMore":
        required_stars, stage, owner, event_key, count = int(p[0]), p[1], p[2], p[3], int(p[4])
        return int(stage_id == stage and stars >= required_stars and _deployed(stats, owner) and _extra_count(stats, event_key) >= count)
    if template == "EquipmentEventBattleMore":
        _, owner, event_key, count = p
        return int(_deployed(stats, owner) and _extra_count(stats, event_key) >= int(count))
    if template == "EquipmentSquadNoAnyDead":
        required_stars, stage, owner = int(p[0]), p[1], p[2]
        return int(stage_id == stage and stars >= required_stars and _deployed(stats, owner) and _dead_count(stats) == 0)
    return 0


def plan_module_battle_missions(player_data, decoded, stars, event_id=None, squad_snapshot=None):
    """Plan replay-safe absolute mutations for one successful settlement."""
    table = const_json_loader[UNIEQUIP_TABLE]
    existing = player_data["equipment"]["missions"]
    squad_char_ids = {
        slot.get("currentTmpl") or slot.get("charId")
        for slot in (squad_snapshot or [])
        if isinstance(slot, dict)
    }
    planned, changes = {}, []
    for equip in _owned_module_resources(player_data):
        if not _module_is_eligible(player_data, equip):
            continue
        for mission_id in equip["missionList"]:
            if mission_id not in table["missionList"]:
                continue
            resource = table["missionList"][mission_id].copy()
            target = module_mission_target(resource)
            before_row = existing[mission_id].copy() if mission_id in existing else {"value": 0, "target": target}
            if resource["template"] not in SUPPORTED_TEMPLATES or before_row["value"] >= before_row["target"]:
                continue
            increment = max(0, _mission_increment(resource, decoded, stars, squad_char_ids))
            after = min(before_row["target"], before_row["value"] + increment)
            if after == before_row["value"]:
                continue
            planned[mission_id] = {"value": after, "target": before_row["target"]}
            changes.append({
                "missionId": mission_id,
                "template": resource["template"],
                "before": before_row["value"],
                "after": after,
                "eventId": event_id,
            })
    mutations = [
        {"path": ["equipment", "missions", mission_id], "value": deepcopy(row)}
        for mission_id, row in planned.items()
    ]
    return mutations, changes
