from pathlib import Path

import pytest

from openbachelors.const.filepath import (
    BATTLE_LEVEL_SCRIPT_TABLE,
    BUILDING_LOCAL_DATA,
    LEVELS_META,
    ROOM_OBSTACLE_DATA,
)
from openbachelors.util.const_json_loader import const_json_loader
from openbachelors.util.game_data import (
    GameDataVersionError,
    game_data_path,
    load_game_data_json,
    parse_data_version,
    validate_game_data_version,
)


def test_complete_game_data_directories_are_available_through_one_root():
    for directory in ("excel", "building", "story", "levels", "levelscripts", "battle"):
        assert game_data_path(directory).is_dir()


def test_existing_excel_and_new_json_resources_share_lazy_loader():
    for path in (
        "res/excel/stage_table.json",
        BUILDING_LOCAL_DATA,
        ROOM_OBSTACLE_DATA,
        BATTLE_LEVEL_SCRIPT_TABLE,
        LEVELS_META,
    ):
        assert path in const_json_loader
        assert const_json_loader[path].copy()


def test_story_scripts_are_read_directly_without_generated_copy():
    script = game_data_path("story/obt/guide/l0-11/1b_slot_clean.txt")
    assert script.is_file()
    assert 'roomId="slot_26"' in script.read_text(encoding="utf-8")


def test_game_data_version_is_explicitly_pinned():
    assert validate_game_data_version() == {
        "versionControl": "78.2.0",
        "clientVersion": "2.7.81",
    }


def test_version_parser_rejects_unrecognised_or_mismatched_data(monkeypatch, tmp_path):
    with pytest.raises(GameDataVersionError):
        parse_data_version("not a version file")
    with pytest.raises(ValueError):
        game_data_path("../outside.json")


def test_building_auxiliary_tables_are_visual_not_player_save_schema():
    local = load_game_data_json("building/building_local_data.json")
    obstacles = load_game_data_json("building/room_obstacle_data.json")
    assert set(local) == {"furnitureObstacleData", "roomObstacleData", "furnitureLODConfig"}
    assert "room_power_4x2" in local["roomObstacleData"]
    assert "set_test1_8" in obstacles
    # Neither table describes PlayerBuilding.roomSlots or tutorial slot IDs.
    assert "slot_26" not in str(local)
    assert "slot_26" not in str(obstacles)
