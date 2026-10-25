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
    plan = commands.add_parser('plan')
    plan.add_argument('month', help='Gregorian YYYY-MM')
    plan.add_argument('--actor', required=True, type=int)
    for name in ('plan-status', 'plan-refresh', 'plan-approve', 'plan-export'):
        command = commands.add_parser(name)
        command.add_argument('plan_id', type=int)
        command.add_argument('--actor', required=True, type=int)
        if name == 'plan-export':
            command.add_argument('--output', required=True, help='new private JSON file outside a Git checkout')
    commands.add_parser('bot')
    alerts = commands.add_parser('export-alerts')
    alerts.add_argument('--actor', required=True, type=int)
    preview = commands.add_parser('plan-preview')
    preview.add_argument('plan_id', type=int)
    preview.add_argument('day', help='Gregorian YYYY-MM-DD')
    preview.add_argument('--actor', required=True, type=int)
    preview.add_argument('--output', required=True, help='new private HTML file outside a Git checkout')
    args = parser.parse_args()
    if args.command == 'bot':
        from .bot import run
        run(args.db)
        return
    db = connect(args.db)
    try:
        if args.command == 'init':
            bootstrap_owner(db, args.owner)
        elif args.command == 'import':
            print(json.dumps(import_workbook(db, args.actor, args.file, args.dry_run)))
        else:
            from .content import create_plan, inspect_plan, refresh_plan, approve_plan, create_export, invalid_exports
            if args.command == 'plan':
                print(json.dumps({'plan_id': create_plan(db, args.actor, args.month)}))
            elif args.command == 'plan-status':
                print(json.dumps(inspect_plan(db, args.actor, args.plan_id)))
            elif args.command == 'plan-refresh':
                print(json.dumps({'changed_slots': refresh_plan(db, args.actor, args.plan_id)}))
            elif args.command == 'plan-approve':
                approve_plan(db, args.actor, args.plan_id)
                print('approved')
            elif args.command == 'plan-export':
                print(json.dumps({'export_id': create_export(db, args.actor, args.plan_id, args.output)}))
            elif args.command == 'export-alerts':
                print(json.dumps(invalid_exports(db, args.actor)))
            elif args.command == 'plan-preview':
                from pathlib import Path
                from .preview import preview_day
                output = Path(args.output).resolve()
                if any((parent / '.git').exists() for parent in output.parents):
                    raise ValueError('preview must be outside a Git checkout')
                html = preview_day(db, args.actor, args.plan_id, args.day)
                with output.open('x', encoding='utf-8') as file:
                    file.write(html)
                print('draft preview created; revalidate before using')
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
