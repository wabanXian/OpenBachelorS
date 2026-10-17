"""Central reward-to-save and client-response protocol mapping."""

from copy import deepcopy

from .character_grant import build_first_operator_grant


STATUS_REWARD_FIELDS = {
    "GOLD": "gold",
    "TKT_RECRUIT": "recruitLicense",
    "TKT_INST_FIN": "instantFinishTicket",
    "TKT_GACHA": "gachaTicket",
    "TKT_GACHA_10": "tenGachaTicket",
    "DIAMOND_SHD": "diamondShard",
    "HGG_SHD": "hggShard",
    "LGG_SHD": "lggShard",
    "SOCIAL_PT": "socialPoint",
}
INVENTORY_REWARD_TYPES = {
    "MATERIAL",
    "CARD_EXP",
    "AP_ITEM",
    "VOUCHER_PICK",
    "VOUCHER_CGACHA",
}


def reward_target_path(reward):
    explicit = reward.get("target")
    if explicit:
        return explicit.split(".")
    reward_type = reward["type"]
    if reward_type in STATUS_REWARD_FIELDS:
        return ["status", STATUS_REWARD_FIELDS[reward_type]]
    if reward_type in INVENTORY_REWARD_TYPES:
        return ["inventory", reward["id"]]
    return None


def _read_path(player_data, path):
    current = player_data
    for key in path:
        if key not in current:
            return 0
        current = current[key]
    return current


def _planned_value(player_data, mutations, path):
    for mutation in reversed(mutations):
        if mutation["path"] == path:
            return mutation["value"]
    return _read_path(player_data, path)


def plan_reward(player_data, mutations, reward, platform=2):
    """Append absolute mutations and return the client-visible reward item."""
    reward = deepcopy(reward)
    reward_type = reward["type"]
    count = reward.get("count", 1)
    if reward_type == "CHAR":
        char_mutations, char_inst_id = build_first_operator_grant(
            player_data, reward["id"]
        )
        if char_inst_id is None:
            raise ValueError("duplicate_character_reward_not_supported")
        mutations.extend(char_mutations)
        return {
            "type": "CHAR",
            "id": reward["id"],
            "charGet": {
                "charInstId": char_inst_id,
                "charId": reward["id"],
                "isNew": 1,
            },
        }
    if reward_type == "DIAMOND":
        field = "iosDiamond" if platform == 1 else "androidDiamond"
        path = ["status", field]
    else:
        path = reward_target_path(reward)
    if path is None:
        raise ValueError(f"unsupported_reward_type:{reward_type}")
    mutations.append(
        {
            "path": path,
            "value": _planned_value(player_data, mutations, path) + count,
        }
    )
    return {"type": reward_type, "id": reward["id"], "count": count}


def apply_reward_immediate(player_data, reward):
    """Apply a validated mission reward in the decorator's single save unit."""
    path = reward_target_path(reward)
    if path is None:
        raise ValueError(f"unsupported_reward_type:{reward['type']}")
    target = player_data
    for key in path[:-1]:
        target = target[key]
    key = path[-1]
    old = target[key] if key in target else 0
    target[key] = old + reward["count"]
    return deepcopy(reward)
