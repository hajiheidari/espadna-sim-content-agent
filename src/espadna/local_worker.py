"""Hidden Windows trial entry point; private config, owner-only recipient scope."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from .core import connect, require
from .delivery_runtime import run_ticks
from .packs import private_path


def read_config(config_file):
    path = private_path(config_file)
    with path.open('rb') as file:
        raw = file.read(16385)
    if len(raw) > 16384:
        raise ValueError('local worker config too large')
    config = json.loads(raw)
    if not isinstance(config, dict) or set(config) != {'database', 'actor', 'artifact_root', 'mode', 'allow_network', 'owner_only', 'browser_channel'}:
        raise ValueError('invalid local worker config')
    if type(config['actor']) is not int or config['actor'] <= 0 or config['mode'] not in ('dry_run', 'telegram') or type(config['allow_network']) is not bool or config['owner_only'] is not True or config['browser_channel'] not in (None, 'msedge', 'chrome', 'chromium'):
        raise ValueError('invalid trial worker settings')
    database = private_path(config['database'])
    if not database.is_file():
        raise ValueError('private database must already exist')
    root = private_path(config['artifact_root'])
    return config, database, root


def run_config(config_file):
    config, database, root = read_config(config_file)
    db = connect(database)
    try:
        require(db, config['actor'], {'owner'})
        routes = [row[0] for row in db.execute('SELECT recipient FROM delivery_routes WHERE enabled=1')]
        if routes != [config['actor']]:
            raise ValueError('trial worker must deliver only to its owner')
        results = asyncio.run(run_ticks(db, config['actor'], root, config['mode'], config['allow_network'], 1, config['browser_channel']))
        return {'checked_at': datetime.now(timezone.utc).isoformat(), 'ok': True, 'handled': len(results), 'states': [row['state'] for row in results]}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    path = private_path(args.config)
    try:
        result = run_config(path)
    except Exception as error:
        # Never write raw exceptions, tokens, IDs, paths or inventory to logs.
        result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'ok': False, 'error_type': type(error).__name__}
    status = path.with_name('last-worker-status.json')
    temporary = status.with_name(status.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(status)
    if not result['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
