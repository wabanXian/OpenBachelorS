"""Build atomic player-data mutations for a first-time operator grant."""

from copy import deepcopy

from .player_preset import _normalise_char
from .operator_skin import equipped_skin_id


def build_first_operator_grant(player_data, char_id):
    for _, char in player_data["troop"]["chars"]:
        if char["charId"] == char_id:
            return [], None

    # Lazy import avoids the player_data -> battle_settlement import cycle.
    from .player_data import sandbox_player_data_template

    source_inst_id = next(
        inst_id
        for inst_id, char in sandbox_player_data_template["troop"]["chars"]
        if char["charId"] == char_id
    )
    new_inst_id = player_data["troop"]["curCharInstId"]
    char_obj = _normalise_char(
        sandbox_player_data_template["troop"]["chars"][source_inst_id].copy()
    )
    char_obj["instId"] = new_inst_id
    mutations = [
        {
            "path": ["troop", "chars", str(new_inst_id)],
            "value": char_obj,
        },
        {
            "path": ["troop", "charGroup", char_id],
            "value": {"favorPoint": 0},
        },
        {
            "path": ["troop", "curCharInstId"],
            "value": new_inst_id + 1,
        },
        {
            "path": ["dexNav", "character", char_id],
            "value": {"charInstId": new_inst_id, "count": 1, "classicCount": 0},
        },
    ]
    if source_inst_id in sandbox_player_data_template["building"]["chars"]:
        building_char = deepcopy(
            sandbox_player_data_template["building"]["chars"][source_inst_id].copy()
        )
        building_char["charInstId"] = new_inst_id
        building_char["roomSlotId"] = ""
        building_char["index"] = -1
        mutations.append(
            {
                "path": ["building", "chars", str(new_inst_id)],
                "value": building_char,
            }
        )
    skin_id = equipped_skin_id(char_obj)
    if skin_id:
        mutations.extend(
            [
                {
                    "path": ["skin", "characterSkins", skin_id],
                    "value": 1,
                },
                {"path": ["skin", "skinTs", skin_id], "value": 1700000000},
            ]
        )
    return mutations, new_inst_id
