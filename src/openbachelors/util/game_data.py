import json
from pathlib import Path, PurePosixPath
import re

from ..const.filepath import (
    GAMEDATA_DATA_VERSION,
    GAMEDATA_VERSION_MANIFEST,
    RES_GAMEDATA_DIRPATH,
    VERSION_JSON,
)


class GameDataVersionError(RuntimeError):
    pass


def game_data_path(relative_path):
    """Resolve a path below the vendored GameData root without traversal."""
    relative = PurePosixPath(str(relative_path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("GameData path must stay below the configured resource root")
    return Path(RES_GAMEDATA_DIRPATH).joinpath(*relative.parts)


def load_game_data_json(relative_path):
    path = game_data_path(relative_path)
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def load_game_data_text(relative_path):
    return game_data_path(relative_path).read_text(encoding="utf-8")


def parse_data_version(text):
    match = re.search(r"^VersionControl:([^\r\n]+)$", text, re.MULTILINE)
    if match is None:
        raise GameDataVersionError("data_version.txt has no VersionControl entry")
    return match.group(1).strip()


def validate_game_data_version():
    manifest_path = Path(GAMEDATA_VERSION_MANIFEST)
    data_version_path = Path(GAMEDATA_DATA_VERSION)
    if not manifest_path.is_file():
        raise GameDataVersionError(f"missing GameData version manifest: {manifest_path}")
    if not data_version_path.is_file():
        raise GameDataVersionError(f"missing GameData version file: {data_version_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = parse_data_version(data_version_path.read_text(encoding="utf-8"))
    expected = str(manifest.get("versionControl", ""))
    if actual != expected:
        raise GameDataVersionError(
            f"GameData version mismatch: expected {expected!r}, found {actual!r}; "
            "review the resource update and update conf/gamedata_version.json explicitly"
        )
    server_client_version = json.loads(Path(VERSION_JSON).read_text(encoding="utf-8"))[
        "version"
    ]["clientVersion"]
    expected_client_version = str(manifest.get("clientVersion", ""))
    if server_client_version != expected_client_version:
        raise GameDataVersionError(
            "GameData/client version mismatch: "
            f"manifest expects {expected_client_version!r}, server advertises "
            f"{server_client_version!r}"
        )
    return {"versionControl": actual, "clientVersion": manifest.get("clientVersion")}
