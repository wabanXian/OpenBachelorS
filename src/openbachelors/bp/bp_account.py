import os
import json
from collections import Counter

from fastapi import APIRouter
from fastapi import HTTPException, Request
from fastapi import Response
import aiofiles
import orjson

from ..const.json_const import true, false, null
from ..const.filepath import CONFIG_JSON, VERSION_JSON, TMP_DIRPATH, GAMEDATA_CONST
from ..util.const_json_loader import const_json_loader
from ..util.player_data import PlayerData, player_data_decorator
from ..util.mail_helper import get_player_mailbox
from ..util.faketime import faketime
from ..util.log_helper import IS_DEBUG
from ..util.tutorial_debug import event, player_snapshot
from ..util.historical_release import (
    content_date,
    content_timestamp,
    enabled_content,
    reconcile_historical_release,
)

router = APIRouter()


def mission_state_counts(player_data):
    result = {}
    for mission_type in ("MAIN", "SUB", "GUIDE", "DAILY", "WEEKLY"):
        missions = player_data["mission"]["missions"][mission_type]
        counts = Counter(mission["state"] for _, mission in missions)
        result[mission_type] = {
            "total": len(missions),
            "states": {str(state): count for state, count in sorted(counts.items())},
        }
    return result


def recover_ap(player_data, now):
    """Apply resource-defined natural AP recovery without discarding remainder."""
    status = player_data["status"]
    last = status["lastApAddTime"]
    if last == 1700000000:
        status["lastApAddTime"] = now
        return {"kind": "initialized", "before": status["ap"], "after": status["ap"]}
    before = status["ap"]
    if before >= status["maxAp"] or now <= last:
        return {"kind": "unchanged", "before": before, "after": before}
    interval = int(const_json_loader[GAMEDATA_CONST]["playerApRegenSpeed"]) * 60
    gained = min(status["maxAp"] - before, (now - last) // interval)
    if gained <= 0:
        return {"kind": "unchanged", "before": before, "after": before}
    status["ap"] = before + gained
    status["lastApAddTime"] = now if status["ap"] >= status["maxAp"] else last + gained * interval
    return {
        "kind": "recovered",
        "before": before,
        "after": status["ap"],
        "gained": gained,
        "intervalSeconds": interval,
        "lastApAddTimeBefore": last,
        "lastApAddTimeAfter": status["lastApAddTime"],
    }


@router.post("/account/login")
async def account_login(request: Request):
    request_json = await request.json()
    token = request_json["token"]

    response = {
        "result": 0,
        "uid": "123456789",
        "secret": token,
        "serviceLicenseVersion": 0,
        "majorVersion": "354",
    }

    return response


@router.post("/account/syncData")
async def account_syncData(request: Request):
    player_data = await PlayerData.create(request=request)

    event(
        request,
        "sync_data_initial_state",
        preset=getattr(player_data, "preset", None),
        state=player_snapshot(player_data),
        missionStateCounts=mission_state_counts(player_data),
        responseBodyLogging="suppressed",
    )

    server_t = int(faketime())
    historical_changes = reconcile_historical_release(player_data)
    player_data["status"]["lastRefreshTs"] = server_t
    ap_recovery = recover_ap(player_data, server_t)
    if ap_recovery["kind"] != "unchanged":
        event(
            request,
            "ap_recovery_applied",
            maxAp=player_data["status"]["maxAp"],
            **ap_recovery,
        )

    battle_replay_lst = await player_data.battle_replay_manager.get_battle_replay_lst()
    for stage_id in battle_replay_lst:
        player_data["dungeon"]["stages"][stage_id]["hasBattleReplay"] = 1

    mail_json_obj, pending_mail_set = get_player_mailbox(player_data)
    player_data["pushFlags"]["hasGifts"] = int(bool(pending_mail_set))

    delta_response = player_data.build_delta_response()
    await player_data.save()

    event(
        request,
        "sync_data_persisted",
        preset=getattr(player_data, "preset", None),
        modifiedTopLevel=sorted(delta_response["modified"]),
        deletedTopLevel=sorted(delta_response["deleted"]),
        state=player_snapshot(player_data),
        missionStateCounts=mission_state_counts(player_data),
    )

    player_data_json_obj = player_data.copy()

    if IS_DEBUG:
        os.makedirs(TMP_DIRPATH, exist_ok=True)
        async with aiofiles.open(
            os.path.join(TMP_DIRPATH, "player_data.json"), "w", encoding="utf-8"
        ) as f:
            await f.write(
                json.dumps(player_data_json_obj, ensure_ascii=False, indent=4)
            )

    response = {
        "result": 0,
        # Never bend the global/account clock to select historical content:
        # several archived activities and pools may be enabled concurrently.
        "ts": server_t,
        "user": player_data_json_obj,
        "playerDataDelta": {"modified": {}, "deleted": {}},
    }
    if historical_changes:
        event(
            request,
            "historical_release_reconciled",
            contentDate=str(content_date(player_data)) if content_date(player_data) else None,
            enabledActivities=sorted(enabled_content(player_data)["activities"]),
            enabledPools=sorted(enabled_content(player_data)["pools"]),
            permanentRetroCount=len(enabled_content(player_data)["permanentRetros"]),
            changes=historical_changes,
            serverTimestamp=server_t,
            clientTimestamp=response["ts"],
        )
    return Response(content=orjson.dumps(response), media_type="application/json")


@router.post("/account/syncStatus")
@player_data_decorator
async def account_syncStatus(player_data, request: Request):
    request_json = await request.json()
    response = {"result": {}}
    return response


@router.post("/account/historicalContentDate")
async def account_historicalContentDate(request: Request):
    """Return the server-local content clock to a loopback launcher only."""
    client_host = request.client.host if request.client is not None else None
    if client_host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="loopback_only")
    selected = content_date()
    return {
        "result": 0,
        "mode": "historical" if selected is not None else "realtime",
        "date": selected.isoformat() if selected is not None else None,
        "contentTs": content_timestamp() if selected is not None else None,
    }


@router.post("/account/syncPushMessage")
@player_data_decorator
async def account_syncPushMessage(player_data, request: Request):
    request_json = await request.json()
    response = {}
    return response
