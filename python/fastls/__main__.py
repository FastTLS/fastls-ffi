"""Offline configuration tooling: python -m fastls profile --help."""
import argparse
import json
from .profile_store import ProfileBundle, ProfileStore, _read, _strict
from .profiles import ProfileSpec


def main():
    parser = argparse.ArgumentParser(description="Fastls offline profile tools")
    parser.add_argument("--library")
    commands = parser.add_subparsers(dest="command", required=True)
    profile = commands.add_parser("profile")
    actions = profile.add_subparsers(dest="action", required=True)
    create = actions.add_parser("export")
    create.add_argument("spec")
    create.add_argument("output")
    create.add_argument("--source", required=True)
    adapt = actions.add_parser("adapt")
    adapt.add_argument("base")
    adapt.add_argument("output")
    adapt.add_argument("--ja3", default="")
    adapt.add_argument("--h2", default="")
    adapt.add_argument("--source", required=True)
    check = actions.add_parser("validate")
    check.add_argument("bundle")
    check.add_argument("--sha256", required=True)
    install = actions.add_parser("import")
    install.add_argument("bundle")
    install.add_argument("--sha256", required=True)
    install.add_argument("--store", required=True)
    for action in ("pin", "rollback", "show"):
        sub = actions.add_parser(action)
        sub.add_argument("--store", required=True)
        if action == "pin":
            sub.add_argument("sha256")
    args = parser.parse_args()
    if args.action == "adapt":
        spec = ProfileSpec.from_fingerprints(ProfileSpec(_strict(_read(args.base))), ja3=args.ja3, h2=args.h2, library=args.library)
        bundle = ProfileBundle.create(spec, source=args.source, library=args.library)
        bundle.export(args.output)
    elif args.action == "export":
        bundle = ProfileBundle.create(ProfileSpec(_strict(_read(args.spec))), source=args.source, library=args.library)
        bundle.export(args.output)
    elif args.action == "validate":
        bundle = ProfileBundle(_read(args.bundle), sha256=args.sha256, library=args.library)
    else:
        store = ProfileStore(args.store, library=args.library)
        if args.action == "import":
            bundle = store.install(args.bundle, sha256=args.sha256)
        elif args.action == "pin":
            bundle = store.pin(args.sha256)
        elif args.action == "rollback":
            bundle = store.rollback()
        else:
            bundle = store.load()
    print(json.dumps({"profile_id": bundle.profile_id, "sha256": bundle.sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
