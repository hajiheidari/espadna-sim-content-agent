import argparse
import json
from .core import bootstrap_owner, connect
from .importer import import_workbook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True, help='private database outside repository')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--owner', required=True, type=int)
    imp = commands.add_parser('import')
    imp.add_argument('file')
    imp.add_argument('--actor', required=True, type=int)
    imp.add_argument('--dry-run', action='store_true')
    commands.add_parser('bot')
    args = parser.parse_args()
    if args.command == 'bot':
        from .bot import run
        run(args.db)
        return
    db = connect(args.db)
    try:
        if args.command == 'init':
            bootstrap_owner(db, args.owner)
        else:
            print(json.dumps(import_workbook(db, args.actor, args.file, args.dry_run)))
    except (ValueError, PermissionError) as exc:
        parser.exit(1, str(exc) + '\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
