from fastapi import APIRouter, HTTPException
from fastapi import Request

from ..const.json_const import true, false, null
from ..const.filepath import CONFIG_JSON, VERSION_JSON
from ..util.const_json_loader import const_json_loader
from ..util.player_data import player_data_decorator
from ..util.character_loadout import skin_target_template, validate_skin_equip

router = APIRouter()


@router.post("/user/changeSecretary")
@player_data_decorator
async def user_changeSecretary(player_data, request: Request):
    request_json = await request.json()
    char_inst_id = request_json.get("charInstId")
    skin_id = request_json.get("skinId")
    if not isinstance(char_inst_id, int) or isinstance(char_inst_id, bool):
        raise HTTPException(status_code=400, detail="invalid_char_inst_id")
    chars, key = player_data["troop"]["chars"], str(char_inst_id)
    if key not in chars:
        raise HTTPException(status_code=400, detail="character_not_found")
    char = chars[key]
    try:
        target_template = skin_target_template(char["charId"], skin_id)
        validate_skin_equip(player_data, char, skin_id, target_template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    player_data["status"]["secretary"] = char["charId"]
    player_data["status"]["secretarySkinId"] = skin_id
    return {}


@router.post("/background/setBackground")
@player_data_decorator
async def background_setBackground(player_data, request: Request):
    request_json = await request.json()

    player_data["background"]["selected"] = request_json["bgID"]

    response = {}
    return response


@router.post("/homeTheme/change")
@player_data_decorator
async def homeTheme_change(player_data, request: Request):
    request_json = await request.json()

    player_data["homeTheme"]["selected"] = request_json["themeId"]

    response = {}
    return response
