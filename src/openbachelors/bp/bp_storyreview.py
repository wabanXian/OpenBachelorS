from fastapi import APIRouter
from fastapi import Request

from ..const.json_const import true, false, null
from ..const.filepath import CONFIG_JSON, VERSION_JSON, STORY_TABLE
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.tutorial_debug import event, player_snapshot

router = APIRouter()


@router.post("/storyreview/readStory")
@player_data_decorator
async def storyreview_readStory(player_data, request: Request):
    request_json = await request.json()
    response = {"readCount": 0}
    return response


@router.post("/story/finishStory")
@player_data_decorator
async def story_finishStory(player_data, request: Request):
    request_json = await request.json()

    story_id = request_json["storyId"]
    story = const_json_loader[STORY_TABLE][story_id].copy()
    before = player_snapshot(player_data)
    already_finished = story_id in player_data["status"]["flags"]
    player_data["status"]["flags"][story_id] = 1

    for flag in story["setFlags"]:
        player_data["status"]["flags"][flag] = 1

    set_progress = story["setProgress"]
    if set_progress >= 0:
        player_data["status"]["progress"] = max(
            player_data["status"]["progress"], set_progress
        )

    event(
        request,
        "tutorial_story_finished",
        storyId=story_id,
        alreadyFinished=already_finished,
        serverCondition={
            "minProgress": story["condition"]["minProgress"],
            "requiredFlags": story["condition"]["requiredFlags"].copy(),
            "requiredStages": story["condition"]["requiredStages"].copy(),
        },
        clientInternalCondition="unknown",
        before=before,
        after=player_snapshot(player_data),
        completedRewardCount=len(story["completedRewards"]),
        rewardApplication="not_implemented",
    )

    response = {"items": []}
    return response
