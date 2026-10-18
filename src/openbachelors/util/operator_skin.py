"""Resource-driven default operator skin selection for normal accounts."""

from copy import deepcopy

from ..const.filepath import SKIN_TABLE
from .const_json_loader import const_json_loader


class OperatorSkinError(ValueError):
    pass


def default_evolve_skin(char_id, evolve_phase=0):
    evolve_map = const_json_loader[SKIN_TABLE]["buildinEvolveMap"].copy()
    phases = evolve_map.get(char_id)
    if not isinstance(phases, dict):
        raise OperatorSkinError(f"missing_evolve_skin:{char_id}")
    skin_id = phases.get(str(int(evolve_phase)))
    if skin_id is None:
        raise OperatorSkinError(
            f"missing_evolve_skin:{char_id}:phase_{int(evolve_phase)}"
        )
    return skin_id


def default_template_skin(owner_char_id, tmpl_id, evolve_phase=0):
    if tmpl_id == owner_char_id:
        return default_evolve_skin(owner_char_id, evolve_phase)
    patch_map = const_json_loader[SKIN_TABLE]["buildinPatchMap"].copy()
    skin_id = (patch_map.get(owner_char_id) or {}).get(tmpl_id)
    if skin_id is None:
        # Some template IDs also have their own evolve map. Prefer it only
        # when the authoritative owner->template patch map has no entry.
        skin_id = default_evolve_skin(tmpl_id, evolve_phase)
    return skin_id


def equip_resource_default_skin(char_obj, evolve_phase=0):
    """Return a copy equipped with the resource-defined built-in skin.

    Patch operators keep ``skin=None`` and store the correct skin on each
    template loadout, matching the player-data protocol.
    """
    result = deepcopy(char_obj)
    char_id = result["charId"]
    evolve_phase = int(evolve_phase)
    templates = result.get("tmpl", {})
    current_tmpl = result.get("currentTmpl")
    if current_tmpl and isinstance(templates, dict):
        for tmpl_id, loadout in templates.items():
            loadout["skinId"] = default_template_skin(
                char_id, tmpl_id, evolve_phase if tmpl_id == current_tmpl else 0
            )
        if current_tmpl not in templates:
            raise OperatorSkinError(f"missing_current_template:{char_id}:{current_tmpl}")
        result["skin"] = None
    else:
        result["skin"] = default_evolve_skin(char_id, evolve_phase)
    return result


def equipped_skin_id(char_obj):
    current_tmpl = char_obj.get("currentTmpl")
    if current_tmpl:
        return (char_obj.get("tmpl", {}).get(current_tmpl) or {}).get("skinId")
    return char_obj.get("skin")


def expected_skin_state(char_obj):
    expected = equip_resource_default_skin(
        char_obj, int(char_obj.get("evolvePhase", 0))
    )
    return {
        "equipped": equipped_skin_id(char_obj),
        "expectedEquipped": equipped_skin_id(expected),
        "templates": {
            tmpl_id: {
                "actual": loadout.get("skinId"),
                "expected": expected["tmpl"][tmpl_id].get("skinId"),
            }
            for tmpl_id, loadout in char_obj.get("tmpl", {}).items()
        },
    }
