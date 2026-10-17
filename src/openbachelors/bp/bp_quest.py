from fastapi import APIRouter
from fastapi import Request

from ..const.json_const import true, false, null
from ..const.filepath import CONFIG_JSON, VERSION_JSON, ASSIST_JSON
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.helper import (
    get_char_num_id,
    get_friend_uid_from_assist_lst_idx,
    get_assist_lst_idx_from_friend_uid,
    convert_char_obj_to_assist_char_obj,
    get_friend_uid_from_assist_lst_idx_ext,
    get_assist_lst_idx_from_friend_uid_ext,
    decode_battle_log,
)
from ..util.battle_protocol_audit import battle_protocol_audit
from ..util.tutorial_debug import event, player_snapshot
from ..util.battle_settlement import (
    CLIENT_BATTLE_ID,
    activate_battle_start,
    commit_battle_finish,
    prepare_battle_finish,
    prepare_battle_start,
    reject_response,
)
from ..util.player_preset import POST_TUTORIAL_PRESET
from ..util.assist_ext import profession_lst, profession_assist_lst_dict


router = APIRouter()


@router.post("/quest/squadFormation")
@player_data_decorator
async def quest_squadFormation(player_data, request: Request):
    request_json = await request.json()

    squad_id = request_json["squadId"]
    before_slots = player_data["troop"]["squads"][squad_id]["slots"].copy()
    player_data["troop"]["squads"][squad_id]["slots"] = request_json["slots"]

    event(
        request,
        "tutorial_squad_formation",
        squadId=squad_id,
        beforeCharInstIds=[
            slot["charInstId"] for slot in before_slots if slot is not None
        ],
        afterCharInstIds=[
            slot["charInstId"] for slot in request_json["slots"] if slot is not None
        ],
    )

    response = {}
    return response


@router.post("/quest/changeSquadName")
@player_data_decorator
async def quest_changeSquadName(player_data, request: Request):
    request_json = await request.json()

    squad_id = request_json["squadId"]
    player_data["troop"]["squads"][squad_id]["name"] = request_json["name"]

    response = {}
    return response


@router.post("/quest/battleStart")
@player_data_decorator
async def quest_battleStart(player_data, request: Request):
    request_json = await request.json()
    stage_id = request_json["stageId"]
    player_data.extra_save.save_obj["cur_stage_id"] = stage_id

    if getattr(player_data, "preset", None) == POST_TUTORIAL_PRESET:
        session, correlation = prepare_battle_start(player_data, request_json)
        if session is None:
            response = {
                "result": 1,
                "battleId": CLIENT_BATTLE_ID,
                "apFailReturn": 0,
                "isApProtect": 0,
                "inApProtectPeriod": false,
                "notifyPowerScoreNotEnoughIfFailed": false,
                "settlementError": correlation,
            }
            event(
                request,
                "battle_session_rejected",
                stageId=stage_id,
                reason=correlation,
            )
            await battle_protocol_audit.record_battle_start(
                request, request_json, response
            )
            return response
        if correlation == "new_session":
            # Write-ahead session journal. The plan uses absolute values and is
            # safe for PlayerData.create() to replay after a process restart.
            await player_data.extra_save.save()
            activate_battle_start(player_data, session)

        response = {
            "result": 0,
            "battleId": session["battleId"],
            "apFailReturn": session["apFailReturn"],
            "isApProtect": 0,
            "inApProtectPeriod": false,
            "notifyPowerScoreNotEnoughIfFailed": false,
        }
        event(
            request,
            "battle_session_started",
            battleId=session["battleId"],
            internalSessionId=session["sessionId"],
            stageId=stage_id,
            correlation=correlation,
            apBefore=session["apBefore"],
            apCost=session["apCost"],
            apAfter=session["apBefore"] - session["apCost"],
            stageStartCount=player_data["dungeon"]["stages"][stage_id][
                "startTimes"
            ],
            ruleSource=session.get("ruleSource"),
            ruleCompleteness=session.get("ruleCompleteness"),
            squadSnapshot=session.get("squadSnapshot", []),
        )
        await battle_protocol_audit.record_battle_start(
            request, request_json, response
        )
        return response

    # Preserve sandbox behaviour while the settlement engine is validated on
    # the isolated fresh-account preset.
    response = {
        "result": 0,
        "battleId": CLIENT_BATTLE_ID,
        "apFailReturn": 0,
        "isApProtect": 0,
        "inApProtectPeriod": false,
        "notifyPowerScoreNotEnoughIfFailed": false,
    }
    event(
        request,
        "tutorial_battle_start",
        stageId=stage_id,
        state=player_snapshot(player_data),
        serverEntryDecision="accepted",
        clientInternalCondition="unknown",
    )
    await battle_protocol_audit.record_battle_start(request, request_json, response)
    return response


@router.post("/quest/battleFinish")
@player_data_decorator
async def quest_battleFinish(player_data, request: Request):
    request_json = await request.json()

    response = {
        "result": 0,
        "apFailReturn": 0,
        "expScale": 1.2,
        "goldScale": 1.2,
        "rewards": [],
        "firstRewards": [],
        "unlockStages": [],
        "unusualRewards": [],
        "additionalRewards": [],
        "furnitureRewards": [],
        "overrideRewards": [],
        "alert": [],
        "suggestFriend": false,
        "pryResult": [],
        "itemReturn": [],
        "extra": {
            "sixStar": {
                "groupId": "main_15",
                "before": 32,
                "after": 32,
                "stageBefore": 2,
            }
        },
    }
    settlement = None
    decoded = None
    decode_error = None
    before_snapshot = player_snapshot(player_data)

    if getattr(player_data, "preset", None) == POST_TUTORIAL_PRESET:
        try:
            decoded = decode_battle_log(player_data, request_json["data"])
        except Exception as exc:
            decode_error = type(exc).__name__
            response = reject_response("battle_log_decode_failed")
        else:
            settlement = prepare_battle_finish(
                player_data, decoded, request_json["data"]
            )
            if settlement["kind"] == "prepared":
                session = settlement["session"]
                # Persist the absolute settlement plan before changing assets.
                await player_data.extra_save.save()
                commit_battle_finish(player_data, session)
                response = settlement["response"]
            elif settlement["kind"] == "duplicate":
                response = settlement["response"]
            else:
                response = reject_response(settlement["reason"])

        active = player_data.extra_save.save_obj.get(
            "quest_battle_sessions_v1", {}
        ).get("active")
        settled_session = (settlement or {}).get("session", {})
        decoded_stage_id = (decoded or {}).get("stageId")
        event(
            request,
            "battle_settlement",
            battleId=(decoded or {}).get("battleId"),
            internalSessionId=settled_session.get("sessionId")
            or (active or {}).get("sessionId"),
            stageId=decoded_stage_id,
            decodeError=decode_error,
            settlementKind=(settlement or {}).get("kind", "decode_failed"),
            rejectionReason=(settlement or {}).get("reason"),
            resultEvidence=settled_session.get("evidence"),
            outcome=settled_session.get("outcome"),
            stars=settled_session.get("stars"),
            stageBefore=settled_session.get("beforeStage"),
            stageAfter=(
                player_data["dungeon"]["stages"][decoded_stage_id].copy()
                if decoded_stage_id in player_data["dungeon"]["stages"]
                else None
            ),
            rewards=settled_session.get("rewards"),
            favor=settled_session.get("rewards", {}).get("favor", []),
            playerProgression=settled_session.get("rewards", {}).get(
                "playerProgression"
            ),
            ruleSource=settled_session.get("ruleSource"),
            ruleCompleteness=settled_session.get("ruleCompleteness"),
            unlockStages=response.get("unlockStages", []),
        )

    event(
        request,
        "tutorial_battle_finish",
        stageId=player_data.extra_save.save_obj.get("cur_stage_id"),
        stateBefore=before_snapshot,
        stateAfter=player_snapshot(player_data),
        rewardCount=len(response.get("rewards", []))
        + len(response.get("firstRewards", [])),
        progressionApplied=bool(
            settlement and settlement.get("kind") == "prepared"
        ),
        blocker=(
            None
            if settlement and settlement.get("kind") in {"prepared", "duplicate"}
            else response.get("settlementError")
        ),
        battleData={
            "length": len(request_json.get("data", "")),
        },
    )
    await battle_protocol_audit.record_battle_finish(
        player_data, request, request_json, response
    )
    return response


@router.post("/quest/battleContinue")
@player_data_decorator
async def quest_battleContinue(player_data, request: Request):
    request_json = await request.json()

    event(
        request,
        "battle_continue_unsupported",
        battleData={"length": len(request_json.get("data", ""))},
    )

    response = {
        "result": 1,
        "battleId": "00000000-0000-0000-0000-000000000000",
        "apFailReturn": 0,
    }
    return response


NUM_ASSIST_PER_PAGE = 9


def get_num_assist_page(assist_lst):
    # simple math
    return (len(assist_lst) + NUM_ASSIST_PER_PAGE - 1) // NUM_ASSIST_PER_PAGE


def get_assist_page_key(request_json):
    if const_json_loader[CONFIG_JSON]["assist_ext"]:
        profession = request_json["profession"]
        return f"assist_ext_paging_{profession}"

    return "assist_default_paging"


def get_assist_page_idx(player_data, request_json):
    assist_page_key = get_assist_page_key(request_json)
    return player_data.extra_save.save_obj.get(assist_page_key, 0)


def set_assist_page_idx(player_data, request_json, assist_page_idx):
    assist_page_key = get_assist_page_key(request_json)
    player_data.extra_save.save_obj[assist_page_key] = assist_page_idx


@router.post("/quest/getAssistList")
@player_data_decorator
async def quest_getAssistList(player_data, request: Request):
    request_json = await request.json()

    if const_json_loader[CONFIG_JSON]["assist_ext"]:
        profession = request_json["profession"]
        profession_idx = profession_lst.copy().index(profession)
        assist_lst = profession_assist_lst_dict[profession].copy()
        friend_uid_lst = [
            get_friend_uid_from_assist_lst_idx_ext(i, profession_idx)
            for i in range(len(assist_lst))
        ]
    else:
        assist_lst = const_json_loader[ASSIST_JSON]["assist_lst"].copy()
        friend_uid_lst = [
            get_friend_uid_from_assist_lst_idx(i) for i in range(len(assist_lst))
        ]

    num_assist_page = get_num_assist_page(assist_lst)

    # post_tutorial intentionally owns only the operators acquired by that
    # account.  The legacy extended-assist builder derives candidates from the
    # owned-character template, so a profession can legitimately have no
    # configured candidates.  An empty assist page is valid; modulo zero is
    # not.  Do not synthesize a friend or borrow an unowned operator here.
    if num_assist_page == 0:
        event(
            request,
            "assist_list_empty",
            profession=request_json.get("profession"),
            askRefresh=request_json.get("askRefresh"),
            assistExt=bool(const_json_loader[CONFIG_JSON]["assist_ext"]),
            reason="no_configured_assist_candidates",
        )
        return {"allowAskTs": 1700000000, "assistList": []}

    assist_page_idx = get_assist_page_idx(player_data, request_json) % num_assist_page

    if request_json["askRefresh"]:
        assist_page_idx = (assist_page_idx + 1) % num_assist_page
        set_assist_page_idx(player_data, request_json, assist_page_idx)

    lower_bound = assist_page_idx * NUM_ASSIST_PER_PAGE
    upper_bound = (assist_page_idx + 1) * NUM_ASSIST_PER_PAGE
    assist_lst = assist_lst[lower_bound:upper_bound]
    friend_uid_lst = friend_uid_lst[lower_bound:upper_bound]

    response = {"allowAskTs": 1700000000, "assistList": []}

    for assist_char_id, friend_uid in zip(assist_lst, friend_uid_lst):
        assist_char_num_id = get_char_num_id(assist_char_id)
        assist_char_obj = player_data["troop"]["chars"][str(assist_char_num_id)].copy()
        convert_char_obj_to_assist_char_obj(assist_char_obj)
        response["assistList"].append(
            {
                "uid": friend_uid,
                "aliasName": null,
                "nickName": "Undergraduate",
                "nickNumber": "1234",
                "level": 120,
                "avatarId": "0",
                "avatar": {"type": "ICON", "id": "avatar_def_01"},
                "lastOnlineTime": 1700000000,
                "assistCharList": [assist_char_obj, null, null],
                "powerScore": 0,
                "isFriend": true,
                "canRequestFriend": false,
                "assistSlotIndex": 0,
            }
        )

    return response


@router.post("/quest/saveBattleReplay")
@player_data_decorator
async def quest_saveBattleReplay(player_data, request: Request):
    request_json = await request.json()

    response = {}

    if player_data.extra_save.save_obj.get("cur_stage_id", None) is None:
        await battle_protocol_audit.record_replay_save(request, request_json, response)
        return response

    stage_id = player_data.extra_save.save_obj["cur_stage_id"]

    battle_replay = request_json["battleReplay"]

    await player_data.battle_replay_manager.save_battle_replay(stage_id, battle_replay)

    player_data["dungeon"]["stages"][stage_id]["hasBattleReplay"] = 1

    await battle_protocol_audit.record_replay_save(request, request_json, response)
    return response


@router.post("/quest/getBattleReplay")
@player_data_decorator
async def quest_getBattleReplay(player_data, request: Request):
    request_json = await request.json()

    stage_id = request_json["stageId"]

    battle_replay = await player_data.battle_replay_manager.load_battle_replay(stage_id)

    response = {"battleReplay": battle_replay}
    return response


@router.post("/quest/finishStoryStage")
@player_data_decorator
async def quest_finishStoryStage(player_data, request: Request):
    request_json = await request.json()

    response = {
        "result": 0,
        "rewards": [],
        "unlockStages": [],
        "alert": [],
    }
    event(
        request,
        "tutorial_story_stage_finish",
        stageId=request_json.get("stageId"),
        stateBefore=player_snapshot(player_data),
        stateAfter=player_snapshot(player_data),
        progressionApplied=False,
        blocker="story-stage settlement is not implemented",
    )
    await battle_protocol_audit.record_story_finish(request, request_json, response)
    return response


@router.post("/quest/editStageSixStarTag")
@player_data_decorator
async def quest_editStageSixStarTag(player_data, request: Request):
    request_json = await request.json()

    stage_id = request_json["stageId"]
    tag_lst = request_json["selected"]

    player_data["dungeon"]["sixStar"]["stages"][stage_id]["tagSelected"] = tag_lst

    response = {}
    return response
