from fastapi import APIRouter, Request

from ..util.historical_release import unlock_retro_block
from ..util.player_data import player_data_decorator
from ..util.tutorial_debug import event

router = APIRouter()


@router.post("/retro/unlockRetroBlock")
@player_data_decorator
async def retro_unlockRetroBlock(player_data, request: Request):
    request_json = await request.json()
    retro_id = request_json.get("retroId")
    if not isinstance(retro_id, str):
        return {"result": 1, "error": "invalid_retro_id"}
    error, changes = unlock_retro_block(player_data, retro_id)
    event(request, "retro_unlock", retroId=retro_id, result=error or "ok", changes=changes)
    return {"result": 1 if error else 0, **({"error": error} if error else {})}
