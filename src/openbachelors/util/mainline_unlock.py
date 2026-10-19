"""Resource-derived implicit unlocks for ordinary mainline chapter entrances."""

from functools import lru_cache

from ..const.filepath import STAGE_TABLE, ZONE_TABLE
from .const_json_loader import const_json_loader


@lru_cache(maxsize=1)
def implicit_mainline_unlock_conditions():
    """Return chapter-entry conditions omitted from ``stage_table``.

    Only a boundary between two ``MAINLINE`` zones, with a unique immediately
    preceding ordinary MAIN/NORMAL stage, is inferred. This excludes 0-1,
    activities, alternate difficulties and unrelated empty conditions.
    """
    stages = const_json_loader[STAGE_TABLE]["stages"]
    zones = const_json_loader[ZONE_TABLE]["zones"].copy()
    by_progress = {}
    for stage_id, stage_value in stages:
        stage = stage_value.copy()
        if stage.get("stageType") != "MAIN" or stage.get("difficulty") != "NORMAL":
            continue
        progress = stage.get("slProgress")
        if isinstance(progress, int) and progress > 0:
            by_progress.setdefault(progress, []).append(stage_id)

    result = {}
    for stage_id, stage_value in stages:
        stage = stage_value.copy()
        progress = stage.get("slProgress")
        if (
            stage.get("stageType") != "MAIN"
            or stage.get("difficulty") != "NORMAL"
            or not isinstance(progress, int)
            or progress <= 1
            or stage.get("unlockCondition")
            or (zones.get(stage.get("zoneId")) or {}).get("type") != "MAINLINE"
        ):
            continue
        predecessors = [
            predecessor_id
            for predecessor_id in by_progress.get(progress - 1, [])
            if (zones.get(stages[predecessor_id]["zoneId"]) or {}).get("type")
            == "MAINLINE"
            and stages[predecessor_id]["zoneId"] != stage.get("zoneId")
        ]
        if len(predecessors) == 1:
            result[stage_id] = {
                "stageId": predecessors[0],
                "completeState": "PASS",
                "source": "stage_table.slProgress+zone_table.MAINLINE",
            }
    return result


def unlock_conditions_for(stage_id, stage):
    explicit = stage["unlockCondition"].copy()
    if explicit:
        return explicit
    implicit = implicit_mainline_unlock_conditions().get(stage_id)
    if implicit is None:
        return []
    return [{"stageId": implicit["stageId"], "completeState": "PASS"}]
