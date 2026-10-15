from copy import deepcopy

from ..const.filepath import (
    MISSION_TABLE,
    STAGE_TABLE,
    STORY_TABLE,
    TMPL_JSON,
)
from .const_json_loader import ConstJson, const_json_loader
from .helper import get_char_num_id


SANDBOX_PRESET = "sandbox"
POST_TUTORIAL_PRESET = "post_tutorial"
PLAYER_PRESETS = {SANDBOX_PRESET, POST_TUTORIAL_PRESET}

POST_TUTORIAL_MAIN_STAGES = [
    *(f"main_00-{i:02d}" for i in range(1, 12)),
    *(f"main_01-{i:02d}" for i in range(1, 11)),
]
POST_TUTORIAL_TRAINING_STAGES = [f"tr_{i:02d}" for i in range(1, 11)]
POST_TUTORIAL_COMPLETED_STAGES = [
    *POST_TUTORIAL_MAIN_STAGES,
    *POST_TUTORIAL_TRAINING_STAGES,
]

POST_TUTORIAL_GUIDE_GROUPS = ["guide_g_1", "guide_g_2"]
POST_TUTORIAL_GUIDE_MISSIONS = [f"guide_{i}" for i in range(1, 19)]

# Amiya is part of the base account identity. The remaining operators are the
# deterministic ONCE rewards of the completed tutorial/main stages.
POST_TUTORIAL_BASE_CHARS = ["char_002_amiya"]

# Deliberate, non-official compensation for resources consumed by the skipped
# mandatory upgrade prompts. Keeping this separate makes the balance choice
# auditable instead of pretending the resource tables contain those costs.
POST_TUTORIAL_SUBSIDY = [
    {"type": "GOLD", "id": "4001", "count": 40000},
    {"type": "CARD_EXP", "id": "2001", "count": 20},
    {"type": "CARD_EXP", "id": "2002", "count": 10},
]


REWARD_TARGETS = {
    "GOLD": "status.gold",
    "DIAMOND": "status.androidDiamond",
    "DIAMOND_SHD": "status.diamondShard",
    "TKT_GACHA": "status.gachaTicket",
    "TKT_RECRUIT": "status.recruitLicense",
    "TKT_INST_FIN": "status.instantFinishTicket",
    "CARD_EXP": "inventory",
    "MATERIAL": "inventory",
    "CHAR": "troop.chars",
}


def get_player_preset(config):
    try:
        preset = config["player_preset"]
    except KeyError:
        preset = SANDBOX_PRESET
    if preset not in PLAYER_PRESETS:
        raise ValueError(
            f"unknown player_preset {preset!r}; expected one of {sorted(PLAYER_PRESETS)}"
        )
    return preset


def _reward_entry(source, reward, quantity_basis):
    reward = deepcopy(reward)
    reward.setdefault("count", 1)
    return {
        "source": source,
        "type": reward["type"],
        "id": reward["id"],
        "count": reward["count"],
        "target": REWARD_TARGETS.get(reward["type"]),
        "quantityBasis": quantity_basis,
    }


def build_post_tutorial_reward_ledger():
    mission_table = const_json_loader[MISSION_TABLE].copy()
    stage_table = const_json_loader[STAGE_TABLE].copy()
    ledger = []
    unresolved = []

    for stage_id in POST_TUTORIAL_COMPLETED_STAGES:
        stage = stage_table["stages"][stage_id]
        display_rewards = (stage.get("stageDropInfo") or {}).get(
            "displayRewards"
        ) or []
        for reward in display_rewards:
            drop_type = reward.get("dropType")
            if drop_type not in {"ONCE", "COMPLETE"}:
                continue
            if reward["type"] not in REWARD_TARGETS:
                unresolved.append(
                    {
                        "source": f"stage:{stage_id}:{drop_type}",
                        "reward": deepcopy(reward),
                        "reason": "unsupported reward type",
                    }
                )
                continue
            ledger.append(
                _reward_entry(
                    f"stage:{stage_id}:{drop_type}",
                    reward,
                    "one displayRewards entry denotes one deterministic reward",
                )
            )

    completed_main_stages = set(POST_TUTORIAL_MAIN_STAGES)
    for mission_id, mission in mission_table["missions"].items():
        if mission.get("type") != "MAIN":
            continue
        params = mission.get("param") or []
        if not any(param in completed_main_stages for param in params):
            continue
        for reward in mission.get("rewards") or []:
            ledger.append(
                _reward_entry(
                    f"mission:{mission_id}", reward, "mission_table.rewards.count"
                )
            )

    for mission_id in POST_TUTORIAL_GUIDE_MISSIONS:
        mission = mission_table["missions"][mission_id]
        for reward in mission.get("rewards") or []:
            ledger.append(
                _reward_entry(
                    f"mission:{mission_id}", reward, "mission_table.rewards.count"
                )
            )

    for group_id in POST_TUTORIAL_GUIDE_GROUPS:
        group = mission_table["missionGroups"][group_id]
        for reward in group.get("rewards") or []:
            ledger.append(
                _reward_entry(
                    f"mission_group:{group_id}",
                    reward,
                    "mission_table.missionGroups.rewards.count",
                )
            )

    for reward in POST_TUTORIAL_SUBSIDY:
        ledger.append(
            _reward_entry(
                "preset:skipped_upgrade_compensation",
                reward,
                "explicit post_tutorial opening subsidy",
            )
        )

    return ledger, unresolved


def _normalise_char(char_obj, level):
    char_obj = deepcopy(char_obj)
    char_obj["favorPoint"] = 0
    char_obj["potentialRank"] = 0
    char_obj["mainSkillLvl"] = 1
    char_obj["level"] = level
    char_obj["exp"] = 0
    char_obj["evolvePhase"] = 0

    def normalise_loadout(loadout):
        loadout["defaultSkillIndex"] = 0
        loadout["currentEquip"] = None
        loadout["equip"] = {}
        for index, skill in enumerate(loadout.get("skills", [])):
            skill["unlock"] = int(index == 0)
            skill["specializeLevel"] = 0
            skill["completeUpgradeTime"] = -1

    normalise_loadout(char_obj)
    for loadout in char_obj.get("tmpl", {}).values():
        normalise_loadout(loadout)
    return char_obj


def _add_reward(tmpl, sandbox, entry, owned_char_ids):
    reward_type = entry["type"]
    reward_id = entry["id"]
    count = entry["count"]

    if reward_type == "GOLD":
        tmpl["status"]["gold"] += count
    elif reward_type == "DIAMOND":
        tmpl["status"]["androidDiamond"] += count
    elif reward_type == "DIAMOND_SHD":
        tmpl["status"]["diamondShard"] += count
    elif reward_type == "TKT_GACHA":
        tmpl["status"]["gachaTicket"] += count
    elif reward_type == "TKT_RECRUIT":
        tmpl["status"]["recruitLicense"] += count
    elif reward_type == "TKT_INST_FIN":
        tmpl["status"]["instantFinishTicket"] += count
    elif reward_type in {"CARD_EXP", "MATERIAL"}:
        tmpl["inventory"][reward_id] = tmpl["inventory"].get(reward_id, 0) + count
    elif reward_type == "CHAR":
        owned_char_ids.add(reward_id)
    else:
        raise ValueError(f"unsupported post_tutorial reward type: {reward_type}")


def build_post_tutorial_template(sandbox_template):
    sandbox = sandbox_template.copy()
    tmpl = const_json_loader[TMPL_JSON].copy()
    ledger, unresolved = build_post_tutorial_reward_ledger()
    if unresolved:
        raise ValueError(f"unresolved deterministic post_tutorial rewards: {unresolved}")

    owned_char_ids = set(POST_TUTORIAL_BASE_CHARS)
    tmpl["inventory"] = {}
    tmpl["troop"]["spOperator"] = {}
    tmpl["mission"]["pinnedSpecialOperator"] = None
    for field in (
        "gold",
        "androidDiamond",
        "iosDiamond",
        "diamondShard",
        "gachaTicket",
        "recruitLicense",
        "instantFinishTicket",
    ):
        tmpl["status"][field] = 0
    for entry in ledger:
        _add_reward(tmpl, sandbox, entry, owned_char_ids)

    # A modest account state after chapter 1. The reward ledger is applied on
    # top of these zeroed counters, once, as part of the immutable base template.
    tmpl["status"].update(
        {
            "level": 10,
            "exp": 0,
            "ap": 120,
            "maxAp": 120,
            "progress": 20300,
            "mainStageProgress": "main_01-10",
            "gold": tmpl["status"]["gold"],
            "gachaTicket": tmpl["status"]["gachaTicket"],
            "recruitLicense": tmpl["status"]["recruitLicense"],
            "instantFinishTicket": tmpl["status"]["instantFinishTicket"],
            "diamondShard": tmpl["status"]["diamondShard"],
            "androidDiamond": tmpl["status"]["androidDiamond"],
            "iosDiamond": 0,
        }
    )

    # Mark only story/tutorial records whose prerequisites are entirely inside
    # the skipped range. Later tutorials, stories and activities remain fresh.
    completed_stage_set = set(POST_TUTORIAL_COMPLETED_STAGES)
    for story_id, story in const_json_loader[STORY_TABLE].copy().items():
        if not story_id.lower().startswith(("obt/guide/", "obt/main/", "obt/tutorial/")):
            continue
        required_stages = {
            item["stageId"] for item in story["condition"].get("requiredStages", [])
        }
        if required_stages and required_stages.issubset(completed_stage_set):
            tmpl["status"]["flags"][story_id] = 1

    for stage_id in POST_TUTORIAL_COMPLETED_STAGES:
        tmpl["dungeon"]["stages"][stage_id] = {
            "stageId": stage_id,
            "completeTimes": 1,
            "startTimes": 1,
            "practiceTimes": 0,
            "state": 3,
            "hasBattleReplay": 0,
            "noCostCnt": 0,
        }

    for char_id in sorted(owned_char_ids, key=get_char_num_id):
        char_inst_id = str(get_char_num_id(char_id))
        level = 20 if char_id == "char_002_amiya" else 10
        char_obj = _normalise_char(sandbox["troop"]["chars"][char_inst_id], level)
        if char_id == "char_002_amiya":
            char_obj["mainSkillLvl"] = 3
        tmpl["troop"]["chars"][char_inst_id] = char_obj
        tmpl["troop"]["charGroup"][char_id] = {"favorPoint": 0}
        tmpl["dexNav"]["character"][char_id] = {
            "charInstId": int(char_inst_id),
            "count": 1,
            "classicCount": 0,
        }
        if char_inst_id in sandbox["building"]["chars"]:
            building_char = deepcopy(sandbox["building"]["chars"][char_inst_id])
            building_char["roomSlotId"] = ""
            building_char["index"] = -1
            tmpl["building"]["chars"][char_inst_id] = building_char

        skin_id = char_obj.get("skin")
        if skin_id is None and char_obj.get("currentTmpl"):
            skin_id = char_obj["tmpl"][char_obj["currentTmpl"]]["skinId"]
        if skin_id:
            tmpl["skin"]["characterSkins"][skin_id] = 1
            tmpl["skin"]["skinTs"][skin_id] = 1700000000

    tmpl["troop"]["curCharInstId"] = max(
        get_char_num_id(char_id) for char_id in owned_char_ids
    ) + 1

    squad_char_ids = [
        "char_123_fang",
        "char_192_falco",
        "char_208_melan",
        "char_124_kroos",
        "char_211_adnach",
        "char_002_amiya",
        "char_009_12fce",
        "char_278_orchid",
        "char_122_beagle",
        "char_235_jesica",
        "char_120_hibisc",
        "char_212_ansel",
    ]
    slots = [
        {"charInstId": get_char_num_id(char_id), "skillIndex": 0, "currentEquip": None}
        for char_id in squad_char_ids
    ]
    for squad in tmpl["troop"]["squads"].values():
        squad["slots"] = deepcopy(slots)

    mission_table = const_json_loader[MISSION_TABLE].copy()
    completed_main_stages = set(POST_TUTORIAL_MAIN_STAGES)
    completed_main_missions = []
    for mission_id, mission in mission_table["missions"].items():
        if mission.get("type") != "MAIN":
            continue
        if any(param in completed_main_stages for param in (mission.get("param") or [])):
            completed_main_missions.append(mission_id)

    for mission_id in POST_TUTORIAL_GUIDE_MISSIONS:
        tmpl["mission"]["missions"]["GUIDE"][mission_id] = {
            "value": 1,
            "target": 1,
            "status": 3,
        }
    for mission_id in completed_main_missions:
        tmpl["mission"]["missions"]["MAIN"][mission_id] = {
            "value": 1,
            "target": 1,
            "status": 3,
        }
    for group_id in POST_TUTORIAL_GUIDE_GROUPS:
        tmpl["mission"]["missionGroups"][group_id] = {
            "groupId": group_id,
            "state": 3,
        }

    return ConstJson(tmpl), ConstJson(sorted(owned_char_ids, key=get_char_num_id))
