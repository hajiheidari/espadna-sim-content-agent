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
    bot_check = commands.add_parser('bot-check')
    bot_check.add_argument('--actor', required=True, type=int)
    alerts = commands.add_parser('export-alerts')
    alerts.add_argument('--actor', required=True, type=int)
    preview = commands.add_parser('plan-preview')
    preview.add_argument('plan_id', type=int)
    preview.add_argument('day', help='Gregorian YYYY-MM-DD')
    preview.add_argument('--actor', required=True, type=int)
    preview.add_argument('--output', required=True, help='new private HTML file outside a Git checkout')
    render = commands.add_parser('plan-render')
    render.add_argument('plan_id', type=int)
    render.add_argument('day')
    render.add_argument('--actor', required=True, type=int)
    render.add_argument('--output', required=True, help='new private output directory')
    render.add_argument('--browser-channel', default=None, choices=('msedge', 'chrome', 'chromium'))
    verify = commands.add_parser('bundle-check')
    verify.add_argument('export_id')
    verify.add_argument('directory')
    verify.add_argument('--actor', required=True, type=int)
    pack_import = commands.add_parser('pack-import')
    pack_import.add_argument('source', help='private directory or flat ZIP containing pack.json and images')
    pack_import.add_argument('--store', required=True, help='private image storage outside Git')
    pack_import.add_argument('--actor', required=True, type=int)
    pack_list = commands.add_parser('packs')
    pack_list.add_argument('--actor', required=True, type=int)
    for name in ('pack-approve', 'pack-disable', 'pack-default'):
        pack_command = commands.add_parser(name)
        pack_command.add_argument('pack_id', help='pack ID; pack-default also accepts builtin')
        pack_command.add_argument('--actor', required=True, type=int)
        if name == 'pack-approve':
            pack_command.add_argument('--reviewed-no-prices', action='store_true', help='owner checked pixels/text for prices and fixed inventory claims')
    pack_use = commands.add_parser('pack-use')
    pack_use.add_argument('plan_id', type=int)
    pack_use.add_argument('pack_id', help='approved pack ID or builtin')
    pack_use.add_argument('--actor', required=True, type=int)
    route = commands.add_parser('queue-route')
    route.add_argument('recipient', type=int)
    route.add_argument('--disable', action='store_true')
    route.add_argument('--actor', required=True, type=int)
    enqueue = commands.add_parser('queue-enqueue')
    enqueue.add_argument('plan_id', type=int)
    enqueue.add_argument('--mode', choices=('dry_run', 'telegram'), default='dry_run')
    enqueue.add_argument('--actor', required=True, type=int)
    queue_list = commands.add_parser('queue-list')
    queue_list.add_argument('--actor', required=True, type=int)
    cancel = commands.add_parser('queue-cancel')
    cancel.add_argument('job_id')
    cancel.add_argument('--actor', required=True, type=int)
    resolve = commands.add_parser('queue-resolve')
    resolve.add_argument('job_id')
    resolve.add_argument('--result', choices=('sent', 'not-sent'), required=True)
    resolve.add_argument('--message-ids', default='', help='six confirmed message IDs separated by commas')
    resolve.add_argument('--actor', required=True, type=int)
    runner = commands.add_parser('queue-run')
    runner.add_argument('--actor', required=True, type=int)
    runner.add_argument('--root', required=True, help='private rendered-bundle storage outside Git')
    runner.add_argument('--mode', choices=('dry_run', 'telegram'), default='dry_run')
    runner.add_argument('--allow-network', action='store_true')
    runner.add_argument('--limit', type=int, default=1)
    runner.add_argument('--browser-channel', choices=('msedge', 'chrome', 'chromium'))
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
            elif args.command == 'plan-render':
                from .raster import render_day
                print(json.dumps(render_day(db, args.actor, args.plan_id, args.day, args.output, args.browser_channel)))
            elif args.command == 'bundle-check':
                from .raster import verify_bundle
                print(json.dumps(verify_bundle(db, args.actor, args.export_id, args.directory)))
            elif args.command in ('pack-import', 'packs', 'pack-approve', 'pack-disable', 'pack-default', 'pack-use'):
                from .packs import import_pack, list_packs, approve_pack, disable_pack, set_default_pack, select_plan_pack, pack_row
                if args.command == 'pack-import':
                    pack_id = import_pack(db, args.actor, args.source, args.store)
                    print(json.dumps({'pack_id': pack_id, 'state': pack_row(db, pack_id)['state']}))
                elif args.command == 'packs':
                    print(json.dumps(list_packs(db, args.actor), ensure_ascii=False))
                elif args.command == 'pack-approve':
                    approve_pack(db, args.actor, args.pack_id, args.reviewed_no_prices)
                    print('pack approved for use')
                elif args.command == 'pack-disable':
                    disable_pack(db, args.actor, args.pack_id)
                    print('pack disabled; previous exports require revalidation')
                elif args.command == 'pack-default':
                    set_default_pack(db, args.actor, None if args.pack_id == 'builtin' else args.pack_id)
                    print('default pack updated for future plans')
                else:
                    select_plan_pack(db, args.actor, args.plan_id, None if args.pack_id == 'builtin' else args.pack_id)
                    print('plan pack selected; check plan status and reapprove if changed')
            elif args.command.startswith('queue-'):
                from .delivery import set_route, enqueue_plan, list_jobs, cancel_job, resolve_uncertain
                if args.command == 'queue-route':
                    set_route(db, args.actor, args.recipient, not args.disable)
                    print('private delivery route updated')
                elif args.command == 'queue-enqueue':
                    print(json.dumps(enqueue_plan(db, args.actor, args.plan_id, args.mode)))
                elif args.command == 'queue-list':
                    print(json.dumps(list_jobs(db, args.actor)))
                elif args.command == 'queue-cancel':
                    cancel_job(db, args.actor, args.job_id)
                    print('delivery cancelled')
                elif args.command == 'queue-resolve':
                    ids = tuple(int(value.strip()) for value in args.message_ids.split(',') if value.strip())
                    resolve_uncertain(db, args.actor, args.job_id, args.result == 'sent', ids)
                    print('delivery result reconciled')
                else:
                    import asyncio
                    from .delivery_runtime import run_ticks
                    print(json.dumps(asyncio.run(run_ticks(db, args.actor, args.root, args.mode, args.allow_network, args.limit, args.browser_channel))))
            elif args.command == 'bot-check':
                import asyncio
                from .delivery_runtime import check_bot
                print(json.dumps(asyncio.run(check_bot(db, args.actor))))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    finally:
        db.close()


if __name__ == '__main__':
    main()
