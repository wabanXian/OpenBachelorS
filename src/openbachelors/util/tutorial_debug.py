"""Opt-in structured diagnostics for the client-driven onboarding flow."""

from hashlib import sha256
import json
import logging
from uuid import uuid4

from ..const.filepath import CONFIG_JSON
from .const_json_loader import const_json_loader


logger = logging.getLogger("openbachelors.tutorial")

INITIAL_GUIDE_STORIES = (
    (0, "obt/guide/l0-0/0_home_ui", None, None),
    (10, "obt/guide/l0-0/1_recruit_adv", None, None),
    (20, "obt/guide/l0-0/2_make_squad", None, None),
    (30, "obt/guide/l0-0/3_battle_ui", None, None),
    (100, "obt/guide/l0-1/0_mission_main", "main_00-01", 2),
    (110, "obt/guide/l0-1/1_training_level", None, None),
)


def _enabled():
    config = const_json_loader[CONFIG_JSON].copy()
    return bool(config.get("debug") and config.get("tutorial_debug", {}).get("enabled"))


def account_ref(request):
    token = getattr(request, "headers", {}).get("secret", "_")
    return f"acct_{sha256(token.encode()).hexdigest()[:16]}"


def request_id(request):
    scope = getattr(request, "scope", None)
    if scope is None:
        scope = getattr(request, "_tutorial_scope", None)
        if scope is None:
            scope = {}
            setattr(request, "_tutorial_scope", scope)
    state = scope.setdefault("state", {})
    return state.setdefault("tutorial_request_id", uuid4().hex[:12])


def player_snapshot(player_data):
    if "status" not in player_data:
        return {"available": False}
    status = player_data["status"]
    stages = player_data["dungeon"]["stages"]
    first_stage = "main_00-01" if "main_00-01" in stages else None
    next_guide_story = next(
        (
            story_id
            for min_progress, story_id, required_stage, required_state
            in INITIAL_GUIDE_STORIES
            if status["progress"] >= min_progress
            and story_id not in status["flags"]
            and (
                required_stage is None
                or (
                    required_stage in stages
                    and stages[required_stage]["state"] >= required_state
                )
            )
        ),
        None,
    )
    return {
        "available": True,
        "progress": status["progress"],
        "mainStageProgress": status["mainStageProgress"],
        "flagCount": len(status["flags"]),
        "stageCount": len(stages),
        "firstUnlockedStage": first_stage,
        "stageState": stages[first_stage]["state"] if first_stage else None,
        "nextGuideStory": next_guide_story,
        "charCount": len(player_data["troop"]["chars"]),
    }


def event(request, event_name, **fields):
    if request is None or not _enabled():
        return
    payload = {
        "request_id": request_id(request),
        "account": account_ref(request),
        "event": event_name,
        **fields,
    }
    logger.debug(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
