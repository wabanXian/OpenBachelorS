from copy import deepcopy

from ..const.filepath import GAMEDATA_CONST, TMPL_JSON
from .const_json_loader import ConstJson, const_json_loader
from .helper import get_char_num_id
from .building_state import build_newbie_building


SANDBOX_PRESET = "sandbox"
POST_TUTORIAL_PRESET = "post_tutorial"
PLAYER_PRESETS = {SANDBOX_PRESET, POST_TUTORIAL_PRESET}

# Public compatibility constants for the audit tool. The preset now starts
# before tutorial progression, so none of these collections has entries.
POST_TUTORIAL_COMPLETED_STAGES = []
POST_TUTORIAL_GUIDE_GROUPS = []
POST_TUTORIAL_GUIDE_MISSIONS = []


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


def build_post_tutorial_reward_ledger():
    """Return the intentionally empty reward ledger for a fresh account."""
    return [], []


def _normalise_char(char_obj, level=1):
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


def build_post_tutorial_template(sandbox_template, *, newbie_building=True):
    """Build a fresh account while retaining the preset's isolated save space."""
    sandbox = sandbox_template.copy()
    tmpl = const_json_loader[TMPL_JSON].copy()
    game_const = const_json_loader[GAMEDATA_CONST].copy()

    initial_char_ids = list(game_const["initCharIdList"])
    initial_max_ap = game_const["playerApMap"][0]

    tmpl["inventory"] = {}
    # The first main stage must be present for the chapter screen to expose it.
    # State 0 is unlocked/not cleared; no completion or reward is implied.
    tmpl["dungeon"]["stages"] = {
        "main_00-01": {
            "stageId": "main_00-01",
            "completeTimes": 0,
            "startTimes": 0,
            "practiceTimes": 0,
            "state": 0,
            "hasBattleReplay": 0,
            "noCostCnt": 0,
        }
    }
    tmpl["status"].update(
        {
            "level": 1,
            "exp": 0,
            "socialPoint": 0,
            "gachaTicket": 0,
            "classicGachaTicket": 0,
            "tenGachaTicket": 0,
            "classicTenGachaTicket": 0,
            "instantFinishTicket": 0,
            "hggShard": 0,
            "lggShard": 0,
            "classicShard": 0,
            "recruitLicense": 0,
            "progress": 0,
            "buyApRemainTimes": 0,
            "apLimitUpFlag": 0,
            "ap": initial_max_ap,
            "maxAp": initial_max_ap,
            "androidDiamond": 0,
            "iosDiamond": 0,
            "diamondShard": game_const["initPlayerDiamondShard"],
            "gold": game_const["initPlayerGold"],
            "practiceTicket": game_const["maxPracticeTicket"],
            "mainStageProgress": None,
            "friendNumLimit": game_const["baseMaxFriendNum"],
        }
    )

    # spOperator is account-wide protocol data rather than the owned roster.
    # Preserve it and its string-valued mission pin from the base template.
    for char_id in initial_char_ids:
        char_inst_id = str(get_char_num_id(char_id))
        char_obj = _normalise_char(sandbox["troop"]["chars"][char_inst_id])
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
        get_char_num_id(char_id) for char_id in initial_char_ids
    ) + 1

    if newbie_building:
        tmpl["building"] = build_newbie_building(tmpl["building"])

    return ConstJson(tmpl), ConstJson(initial_char_ids)
