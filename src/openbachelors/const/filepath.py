from .region import GAME_REGION, GameRegion

CONFIG_JSON = "conf/config.json"
VERSION_JSON_CN = "conf/version.json"
VERSION_JSON_EN = "conf/version_en.json"

match GAME_REGION:
    case GameRegion.GAME_REGION_CN:
        VERSION_JSON = VERSION_JSON_CN
    case GameRegion.GAME_REGION_EN:
        VERSION_JSON = VERSION_JSON_EN

VERSION_WINDOWS_JSON_CN = "conf/version_windows.json"
VERSION_WINDOWS_JSON_EN = "conf/version_en_windows.json"

match GAME_REGION:
    case GameRegion.GAME_REGION_CN:
        VERSION_WINDOWS_JSON = VERSION_WINDOWS_JSON_CN
    case GameRegion.GAME_REGION_EN:
        VERSION_WINDOWS_JSON = VERSION_WINDOWS_JSON_EN


ASSIST_JSON = "conf/assist.json"
SQUAD_JSON = "conf/squad.json"

TMPL_JSON = "data/tmpl.json"
RLV2_TMPL_JSON = "data/rlv2_tmpl.json"
SANDBOX_TMPL_JSON = "data/sandbox_tmpl.json"
CRISIS_V2_TMPL_JSON = "data/crisisV2_tmpl.json"
MAIL_JSON = "data/mail.json"
MESSAGE_JSON = "data/message.json"
GACHA_POOL_JSON = "data/gacha_pool.json"
RLV2_DATA = "data/rlv2_data.json"
GACHA_DATA = "data/gacha_data.json"
STAGE_SETTLEMENT_RULES = "data/stage_settlement_rules.json"
GENERATED_STAGE_SETTLEMENT_RULES = "data/stage_settlement_generated.json"
STAGE_SETTLEMENT_COVERAGE_REPORT = "data/stage_settlement_coverage.json"
STAGE_SETTLEMENT_AUDIT_REPORT = "data/stage_settlement_audit.json"
MISSION_LIFECYCLE_AUDIT_REPORT = "data/mission_lifecycle_audit.json"
GENERATED_TRAINING_SETTLEMENT_RULES = "data/training_settlement_generated.json"
TRAINING_SETTLEMENT_COVERAGE_REPORT = "data/training_settlement_coverage.json"
STAGE_DROP_DATASET = "drop_data/stage_drop_dataset_cn.json"
CRISIS_V2_DATA_DIRPATH = "data/crisisV2/"

SAV_DELTA_JSON = "sav/delta.json"
SAV_PENDING_DELTA_JSON = "sav/pending_delta.json"

MULTI_USER_SAV_DIRPATH = "multi_sav/"

REPLAY_DIRPATH = "sav/replay/"

MULTI_REPLAY_DIRPATH = "multi_sav/replay/"

EXTRA_SAVE_FILEPATH = "sav/extra.json"

MULTI_EXTRA_SAVE_DIRPATH = "multi_sav/"

TMP_DIRPATH = "tmp/"

ASSET_DIRPATH = "asset/"

MOD_DIRPATH = "mod/"
MOD_WINDOWS_DIRPATH = "mod_windows/"

GAME_LINK_FILEPATH = "link/game.txt"
PC_GAME_LINK_FILEPATH = "link/pc_game.txt"
PC_GAME_EN_LINK_FILEPATH = "link/pc_game_en.txt"
MUMU_LINK_FILEPATH = "link/mumu12.txt"
MUMU_15_LINK_FILEPATH = "link/mumu15.txt"
LD_LINK_FILEPATH = "link/ld14.txt"

RES_LOCK_FILEPATH = "res_lock"

RES_EXCEL_DIRPATH_CN = "res/excel/"
RES_EXCEL_DIRPATH_EN = "res_en/excel/"
RES_GAMEDATA_DIRPATH_CN = "res/"
RES_GAMEDATA_DIRPATH_EN = "res_en/"


match GAME_REGION:
    case GameRegion.GAME_REGION_CN:
        RES_EXCEL_DIRPATH = RES_EXCEL_DIRPATH_CN
        RES_GAMEDATA_DIRPATH = RES_GAMEDATA_DIRPATH_CN
    case GameRegion.GAME_REGION_EN:
        RES_EXCEL_DIRPATH = RES_EXCEL_DIRPATH_EN
        RES_GAMEDATA_DIRPATH = RES_GAMEDATA_DIRPATH_EN

GAMEDATA_VERSION_MANIFEST = "conf/gamedata_version.json"
GAMEDATA_DATA_VERSION = RES_EXCEL_DIRPATH + "data_version.txt"
BUILDING_LOCAL_DATA = RES_GAMEDATA_DIRPATH + "building/building_local_data.json"
ROOM_OBSTACLE_DATA = RES_GAMEDATA_DIRPATH + "building/room_obstacle_data.json"
BATTLE_LEVEL_SCRIPT_TABLE = RES_GAMEDATA_DIRPATH + "battle/level_script_table.json"
LEVELS_META = RES_GAMEDATA_DIRPATH + "levels/levels_meta.json"


SKIN_TABLE = RES_EXCEL_DIRPATH + "skin_table.json"
CHARWORD_TABLE = RES_EXCEL_DIRPATH + "charword_table.json"
UNIEQUIP_TABLE = RES_EXCEL_DIRPATH + "uniequip_table.json"
CHARACTER_TABLE = RES_EXCEL_DIRPATH + "character_table.json"
ITEM_TABLE = RES_EXCEL_DIRPATH + "item_table.json"
STORY_TABLE = RES_EXCEL_DIRPATH + "story_table.json"
STAGE_TABLE = RES_EXCEL_DIRPATH + "stage_table.json"
MISSION_TABLE = RES_EXCEL_DIRPATH + "mission_table.json"
HANDBOOK_INFO_TABLE = RES_EXCEL_DIRPATH + "handbook_info_table.json"
RETRO_TABLE = RES_EXCEL_DIRPATH + "retro_table.json"
DISPLAY_META_TABLE = RES_EXCEL_DIRPATH + "display_meta_table.json"
MEDAL_TABLE = RES_EXCEL_DIRPATH + "medal_table.json"
STORY_REVIEW_TABLE = RES_EXCEL_DIRPATH + "story_review_table.json"
STORY_REVIEW_META_TABLE = RES_EXCEL_DIRPATH + "story_review_meta_table.json"
ENEMY_HANDBOOK_TABLE = RES_EXCEL_DIRPATH + "enemy_handbook_table.json"
ACTIVITY_TABLE = RES_EXCEL_DIRPATH + "activity_table.json"
CHAR_PATCH_TABLE = RES_EXCEL_DIRPATH + "char_patch_table.json"
CLIMB_TOWER_TABLE = RES_EXCEL_DIRPATH + "climb_tower_table.json"
BUILDING_DATA = RES_EXCEL_DIRPATH + "building_data.json"
SANDBOX_PERM_TABLE = RES_EXCEL_DIRPATH + "sandbox_perm_table.json"
ROGUELIKE_TOPIC_TABLE = RES_EXCEL_DIRPATH + "roguelike_topic_table.json"
GACHA_TABLE = RES_EXCEL_DIRPATH + "gacha_table.json"
CRISIS_V2_TABLE = RES_EXCEL_DIRPATH + "crisis_v2_table.json"
CAMPAIGN_TABLE = RES_EXCEL_DIRPATH + "campaign_table.json"
CHAR_META_TABLE = RES_EXCEL_DIRPATH + "char_meta_table.json"
GAMEDATA_CONST = RES_EXCEL_DIRPATH + "gamedata_const.json"
FAVOR_TABLE = RES_EXCEL_DIRPATH + "favor_table.json"
