"""Resource-driven operator trust helpers.

``favorPoint`` is stored twice by the client schema: once on the owned
operator instance and once on its character group.  They normally advance by
the same earned delta, but they are separate persisted counters.  Never copy
one over the other: doing so would silently repair (and potentially destroy)
an existing save's state.
"""

from bisect import bisect_right

from ..const.filepath import FAVOR_TABLE
from .const_json_loader import const_json_loader


def favor_config():
    table = const_json_loader[FAVOR_TABLE]
    frames = table["favorFrames"].copy()
    max_favor = int(table["maxFavor"])
    if not frames or int(frames[-1]["data"]["favorPoint"]) != max_favor:
        raise ValueError("invalid_favor_table")
    return max_favor, frames


def favor_percent(favor_point):
    """Return the displayed percentage using the resource thresholds."""
    max_favor, frames = favor_config()
    point = min(max(0, int(favor_point)), max_favor)
    thresholds = [int(frame["data"]["favorPoint"]) for frame in frames]
    index = max(0, bisect_right(thresholds, point) - 1)
    return int(frames[index]["data"]["percent"])


def build_squad_favor_snapshot(player_data, request_json):
    """Validate and freeze the account-owned members used by this battle.

    The request squad is authoritative for the run.  Assist units are not
    account-owned and are intentionally excluded.  A ``null`` squad is valid
    for client-provided tutorial formations and produces an empty snapshot.
    """
    squad = request_json.get("squad")
    if squad is None:
        return [], None
    if not isinstance(squad, dict) or not isinstance(squad.get("slots"), list):
        return None, "invalid_squad_snapshot"

    chars = player_data["troop"]["chars"]
    groups = player_data["troop"]["charGroup"]
    snapshot = []
    seen = set()
    for slot in squad["slots"]:
        if slot is None:
            continue
        if not isinstance(slot, dict) or not isinstance(slot.get("charInstId"), int):
            return None, "invalid_squad_snapshot"
        inst_id = slot["charInstId"]
        if inst_id in seen:
            return None, "duplicate_squad_character"
        seen.add(inst_id)
        key = str(inst_id)
        if key not in chars:
            return None, "squad_character_not_owned"
        char = chars[key].copy()
        char_id = char.get("charId")
        if not isinstance(char_id, str) or char_id not in groups:
            return None, "invalid_character_favor_state"

        requested_tmpl = slot.get("currentTmpl")
        templates = char.get("tmpl")
        if templates:
            current_tmpl = requested_tmpl or char.get("currentTmpl")
            if not isinstance(current_tmpl, str) or current_tmpl not in templates:
                return None, "invalid_character_template"
            loadout = templates[current_tmpl]
        elif requested_tmpl not in (None, char_id):
            return None, "invalid_character_template"
        else:
            current_tmpl = None
            loadout = char

        requested_equip = slot.get("currentEquip")
        if templates and isinstance(slot.get("tmpl"), dict):
            requested_loadout = slot["tmpl"].get(current_tmpl)
            if isinstance(requested_loadout, dict):
                requested_equip = requested_loadout.get("currentEquip")
        if requested_equip is not None:
            equips = loadout.get("equip") or {}
            equip = equips.get(requested_equip)
            if not isinstance(equip, dict) or equip.get("locked") or int(equip.get("level", 0)) < 1:
                return None, "squad_module_not_unlocked"

        snapshot.append(
            {
                "charInstId": inst_id,
                "charId": char_id,
                "currentTmpl": current_tmpl,
                "currentEquip": requested_equip,
            }
        )
    return snapshot, None


def plan_battle_favor(player_data, squad_snapshot, actual_ap_cost):
    """Build absolute, replay-safe mutations for a successful settlement."""
    gained = max(0, int(actual_ap_cost))
    max_favor, _ = favor_config()
    mutations = []
    audit = []
    if not gained:
        return mutations, audit

    for member in squad_snapshot:
        inst_key = str(member["charInstId"])
        char_id = member["charId"]
        char = player_data["troop"]["chars"][inst_key].copy()
        group = player_data["troop"]["charGroup"][char_id].copy()
        char_before = int(char.get("favorPoint", 0))
        group_before = int(group.get("favorPoint", 0))
        char_after = min(max_favor, char_before + gained)
        group_after = min(max_favor, group_before + gained)
        if char_after != char_before:
            mutations.append(
                {
                    "path": ["troop", "chars", inst_key, "favorPoint"],
                    "value": char_after,
                }
            )
        if group_after != group_before:
            mutations.append(
                {
                    "path": ["troop", "charGroup", char_id, "favorPoint"],
                    "value": group_after,
                }
            )
        audit.append(
            {
                **member,
                "earned": gained,
                "charBefore": char_before,
                "charAfter": char_after,
                "charPercentBefore": favor_percent(char_before),
                "charPercentAfter": favor_percent(char_after),
                "groupBefore": group_before,
                "groupAfter": group_after,
                "groupPercentBefore": favor_percent(group_before),
                "groupPercentAfter": favor_percent(group_after),
            }
        )
    return mutations, audit
