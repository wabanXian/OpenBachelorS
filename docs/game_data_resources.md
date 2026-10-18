# Complete GameData resources

OpenBachelorS reads the copied `zh_CN/gamedata` tree directly below `res/`.
The `res/excel/...` paths used by existing code are unchanged. JSON resources
under `building`, `story`, `levels`, `levelscripts`, and `battle` are registered
in the same lazy `const_json_loader`; text story scripts can be accessed through
`game_data_path()` / `load_game_data_text()`.

`conf/gamedata_version.json` pins the reviewed resource version. Startup fails
with a clear `GameDataVersionError` when `res/excel/data_version.txt` no longer
matches that pin. Updating GameData therefore requires an explicit review and
manifest update instead of silently mixing versions.

Useful entry points include:

- `BUILDING_LOCAL_DATA`: client room/furniture obstacle and LOD metadata.
- `ROOM_OBSTACLE_DATA`: obstacle masks keyed by layout set.
- `BATTLE_LEVEL_SCRIPT_TABLE`: battle level-script lookup metadata.
- `LEVELS_META`: level asset metadata.
- `game_data_path("story/...")`: original story/tutorial scripts.
- `game_data_path("levelscripts/...")`: original level scripts.

The two building auxiliary JSON files are client presentation/layout resources.
They contain neither tutorial slot IDs such as `slot_26` nor the
`PlayerBuilding.roomSlots` save schema. They can validate visual room geometry,
but they are not evidence for the server-side cleared-slot representation.
