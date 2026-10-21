"""Resource-backed operator loadout, skin, and profession-template checks."""

from copy import deepcopy

from ..const.filepath import CHARACTER_TABLE, CHAR_PATCH_TABLE, SKIN_TABLE
from .const_json_loader import const_json_loader
from .faketime import faketime
from .operator_skin import default_template_skin


PASS_STATES = {"NONE": 0, "PASS": 2, "COMPLETE": 3}


def _field(value, key, default=None):
    return value[key] if key in value else default


def active_loadout(char, template_id=None):
    templates = _field(char, "tmpl")
    current = _field(char, "currentTmpl")
    if templates is not None and current:
        selected = template_id or current
        if not isinstance(selected, str) or selected not in templates:
            raise ValueError("character_template_not_unlocked")
        return templates[selected], selected
    if template_id not in (None, _field(char, "charId")):
        raise ValueError("invalid_character_template")
    return char, None


def validate_skill_index(char, skill_index, template_id=None):
    if not isinstance(skill_index, int) or isinstance(skill_index, bool):
        raise ValueError("invalid_default_skill_index")
    loadout, selected = active_loadout(char, template_id)
    skills = _field(loadout, "skills") or []
    if not skills:
        if skill_index != -1:
            raise ValueError("default_skill_index_out_of_range")
        return loadout, selected
    if skill_index < 0 or skill_index >= len(skills):
        raise ValueError("default_skill_index_out_of_range")
    if int(_field(skills[skill_index], "unlock", 0)) != 1:
        raise ValueError("default_skill_not_unlocked")
    resource_id = selected or char["charId"]
    if resource_id == char["charId"]:
        resource = const_json_loader[CHARACTER_TABLE].copy().get(resource_id)
    else:
        resource = const_json_loader[CHAR_PATCH_TABLE]["patchChars"].copy().get(resource_id)
    definitions = (resource or {}).get("skills") or []
    if skill_index >= len(definitions):
        raise ValueError("invalid_character_skill_schema")
    condition = definitions[skill_index].get("unlockCond") or {}
    required_phase = int(str(condition.get("phase", "PHASE_0")).removeprefix("PHASE_"))
    required_level = int(condition.get("level", 1))
    phase, level = int(char["evolvePhase"]), int(char["level"])
    if phase < required_phase or phase == required_phase and level < required_level:
        raise ValueError("default_skill_not_unlocked")
    return loadout, selected


def skin_target_template(owner_char_id, skin_id):
    if not isinstance(skin_id, str) or not skin_id:
        raise ValueError("invalid_skin_id")
    table = const_json_loader[SKIN_TABLE]
    resource = table["charSkins"].copy().get(skin_id)
    if not isinstance(resource, dict) or resource.get("charId") != owner_char_id:
        raise ValueError("skin_character_mismatch")
    for phase_skin in (table["buildinEvolveMap"].copy().get(owner_char_id) or {}).values():
        if phase_skin == skin_id:
            return owner_char_id
    for tmpl_id, patch_skin in (table["buildinPatchMap"].copy().get(owner_char_id) or {}).items():
        if patch_skin == skin_id:
            return tmpl_id
    prefix = skin_id.partition("@")[0].partition("#")[0]
    infos = const_json_loader[CHAR_PATCH_TABLE]["infos"]
    templates = (infos.copy().get(owner_char_id) or {}).get("tmplIds") or [owner_char_id]
    if prefix not in templates:
        raise ValueError("skin_template_mismatch")
    return prefix


def validate_skin_equip(player_data, char, skin_id, template_id=None):
    if not isinstance(skin_id, str) or not skin_id:
        raise ValueError("invalid_skin_id")
    loadout, selected = active_loadout(char, template_id)
    selected_resource = selected or char["charId"]
    target = skin_target_template(char["charId"], skin_id)
    if target != selected_resource:
        raise ValueError("skin_template_mismatch")

    skin_table = const_json_loader[SKIN_TABLE]
    evolve = skin_table["buildinEvolveMap"].copy().get(char["charId"]) or {}
    required_phase = next(
        (int(phase) for phase, builtin in evolve.items() if builtin == skin_id),
        None,
    )
    patch_builtin = (
        skin_table["buildinPatchMap"].copy().get(char["charId"]) or {}
    ).get(selected_resource)
    if required_phase is not None:
        if int(_field(char, "evolvePhase", 0)) < required_phase:
            raise ValueError("builtin_skin_not_unlocked")
    elif patch_builtin == skin_id:
        if selected_resource not in (_field(char, "tmpl") or {}):
            raise ValueError("character_template_not_unlocked")
    elif not int((player_data["skin"]["characterSkins"].copy()).get(skin_id, 0)):
        raise ValueError("skin_not_owned")
    return loadout, selected


def _condition_met(player_data, condition, future_stage_states=None):
    stage_id = condition.get("stageId")
    stages = player_data["dungeon"]["stages"]
    state = None
    if future_stage_states and stage_id in future_stage_states:
        state = future_stage_states[stage_id]
    elif stage_id in stages:
        state = int(_field(stages[stage_id], "state", 0))
    required = PASS_STATES.get(condition.get("completeState"))
    if state is None or required is None or state < required:
        return False
    unlock_ts = int(condition.get("unlockTs", 0) or 0)
    return not unlock_ts or int(faketime()) >= unlock_ts


def template_is_unlocked(player_data, char, template_id, future_stage_states=None):
    owner = _field(char, "charId")
    info = const_json_loader[CHAR_PATCH_TABLE]["infos"].copy().get(owner)
    if not isinstance(info, dict) or template_id not in info.get("tmplIds", []):
        return False
    if template_id == info.get("default", owner):
        return True
    unlock = const_json_loader[CHAR_PATCH_TABLE]["unlockConds"].copy().get(template_id)
    conditions = (unlock or {}).get("conds")
    return bool(conditions) and all(
        _condition_met(player_data, condition, future_stage_states)
        for condition in conditions
    )


def build_template_loadout(char, template_id):
    resource = const_json_loader[CHAR_PATCH_TABLE]["patchChars"].copy().get(template_id)
    if not isinstance(resource, dict):
        raise ValueError("unknown_character_template_resource")
    phase, level = int(_field(char, "evolvePhase", 0)), int(_field(char, "level", 1))
    skills = []
    for definition in resource.get("skills") or []:
        condition = definition.get("unlockCond") or {}
        required_phase = int(str(condition.get("phase", "PHASE_0")).removeprefix("PHASE_"))
        required_level = int(condition.get("level", 1))
        skills.append(
            {
                "skillId": definition["skillId"],
                "unlock": int(phase > required_phase or phase == required_phase and level >= required_level),
                "state": 0,
                "specializeLevel": 0,
                "completeUpgradeTime": -1,
            }
        )
    default_skill = next(
        (index for index, skill in enumerate(skills) if skill["unlock"]), -1
    )
    return {
        "skinId": default_template_skin(char["charId"], template_id, 0),
        "defaultSkillIndex": default_skill,
        "skills": skills,
        "currentEquip": None,
        "equip": {},
    }


def plan_template_unlocks(player_data, future_stage_states=None):
    mutations, changes = [], []
    for inst_id, char_value in player_data["troop"]["chars"]:
        char = char_value.copy()
        info = const_json_loader[CHAR_PATCH_TABLE]["infos"].copy().get(char.get("charId"))
        if not isinstance(info, dict):
            continue
        templates = char.get("tmpl") or {}
        for template_id in info.get("tmplIds", []):
            if template_id in templates or not template_is_unlocked(
                player_data, char, template_id, future_stage_states
            ):
                continue
            loadout = build_template_loadout(char, template_id)
            skin_id = loadout["skinId"]
            mutations.extend(
                [
                    {"path": ["troop", "chars", str(inst_id), "tmpl", template_id], "value": loadout},
                    {"path": ["skin", "characterSkins", skin_id], "value": 1},
                    {"path": ["skin", "skinTs", skin_id], "value": int(faketime())},
                ]
            )
            changes.append({"charInstId": int(inst_id), "charId": char["charId"], "templateId": template_id})
    return mutations, changes


def reconcile_template_unlocks(player_data):
    mutations, changes = plan_template_unlocks(player_data)
    for mutation in mutations:
        target = player_data
        for key in mutation["path"][:-1]:
            target = target[key]
        target[mutation["path"][-1]] = deepcopy(mutation["value"])
    return changes
