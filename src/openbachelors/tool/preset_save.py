import argparse
from datetime import datetime
from pathlib import Path
import shutil


PRESET = "post_tutorial"


def get_save_dir(username, single_user):
    if single_user:
        return Path("sav") / "presets" / PRESET
    if not username:
        raise ValueError("--username is required unless --single-user is used")
    return Path("multi_sav") / "presets" / PRESET / username


def backup(save_dir, backup_root):
    if not save_dir.is_dir():
        raise FileNotFoundError(f"save does not exist: {save_dir}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_base = backup_root / f"{save_dir.name}-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=True)
    return Path(shutil.make_archive(str(archive_base), "zip", save_dir))


def main():
    parser = argparse.ArgumentParser(
        description="Explicit backup, rebuild and restore for post_tutorial saves"
    )
    parser.add_argument("action", choices=("backup", "rebuild", "restore"))
    parser.add_argument("--username")
    parser.add_argument("--single-user", action="store_true")
    parser.add_argument("--archive")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    save_dir = get_save_dir(args.username, args.single_user)
    backup_root = Path("save_backups") / PRESET

    if args.action == "backup":
        print(backup(save_dir, backup_root))
        return

    if not args.yes:
        raise SystemExit("refusing to modify a save without --yes")

    if args.action == "rebuild":
        if save_dir.exists():
            archive = backup(save_dir, backup_root)
            print(f"backup: {archive}")
            shutil.rmtree(save_dir)
        print(f"rebuild ready: {save_dir} will be initialized on next login")
        return

    if not args.archive:
        raise SystemExit("restore requires --archive")
    archive = Path(args.archive)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if save_dir.exists():
        raise SystemExit(
            f"refusing to overwrite {save_dir}; run rebuild first to create a backup"
        )
    save_dir.mkdir(parents=True)
    shutil.unpack_archive(str(archive), save_dir)
    print(f"restored: {save_dir}")


if __name__ == "__main__":
    main()
