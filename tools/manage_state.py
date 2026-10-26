"""Offline export, SQLite backup and verified restore. Never overwrites a target."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from endocrine.model import Config
from endocrine.store import Repository
from tools.replay_events import replay


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["export", "backup", "restore-copy"])
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    args = p.parse_args()
    if not args.source.is_file() or args.destination.exists():
        raise SystemExit("Source must exist and destination must be new")
    repo = Repository(args.source, Config(), readonly=True, create=False)
    if args.command == "export":
        args.destination.write_text(json.dumps(repo.export(), ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        repo.backup(args.destination)
        copy = Repository(args.destination, Config(), readonly=True, create=False)
        # Validate the copy against its own consistent event stream. Comparing
        # against a later live source snapshot would reject a valid backup.
        data = copy.export()
        if args.command == "restore-copy" and replay(data) != data["states"]:
            raise SystemExit("Replay verification failed")
    print(args.command, "complete; source preserved")


if __name__ == "__main__":
    main()
