"""Persisted RIIC operator morale settlement."""

from ..const.filepath import BUILDING_DATA
from .const_json_loader import const_json_loader
from .faketime import faketime

MAX_AP = 8_640_000
WORK_AP_PER_SECOND = 100


def settle_building_staff(player_data, now=None):
    now = int(faketime() if now is None else now)
    data = const_json_loader[BUILDING_DATA]
    changes = []
    slots = player_data["building"]["roomSlots"].copy()
    for inst_id, value in player_data["building"]["chars"]:
        char = value.copy()
        last = int(char.get("lastApAddTime", now))
        effective_now = max(last, now)
        elapsed = effective_now - last
        room_id = char.get("roomSlotId", "")
        current = int(char.get("ap", MAX_AP))
        updated = current
        if room_id and room_id in slots:
            room_type = slots[room_id].get("roomId")
            if room_type == "DORMITORY":
                runtime = player_data["building"]["rooms"]["DORMITORY"].copy().get(room_id, {})
                comfort = min(int(data["comfortLimit"]), max(0, int(runtime.get("comfort", 0))))
                level = int(slots[room_id].get("level", 1))
                base = int(data["dormData"]["phases"][level - 1]["manpowerRecover"])
                updated = min(MAX_AP, current + elapsed * (base + comfort // int(data["comfortManpowerRecoverFactor"])))
            elif room_type != "TRAINING":
                updated = max(0, current - elapsed * WORK_AP_PER_SECOND)
        target = player_data["building"]["chars"][str(inst_id)]
        target["ap"] = updated
        target["lastApAddTime"] = effective_now
        if updated != current:
            changes.append({"charInstId": int(inst_id), "before": current, "after": updated})
    return changes
