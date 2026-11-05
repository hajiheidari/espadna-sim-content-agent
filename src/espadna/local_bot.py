"""Owner-configured local polling, without silently removing an existing webhook."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from .bot import run
from .core import connect, require
from .credentials import get_token
from .local_worker import read_config
from .packs import private_path
from .transports import request_client


async def ensure_no_webhook(token):
    from telegram import Bot
    async with Bot(token, request=request_client()) as bot:
        info = await bot.get_webhook_info()
        if info.url:
            raise ValueError('existing webhook detected; local polling was not started')


def run_config(path):
    config, database, _ = read_config(path)
    if config['mode'] != 'telegram' or config['allow_network'] is not True:
        raise PermissionError('local polling requires explicit network launch')
    db = connect(database)
    try:
        require(db, config['actor'], {'owner'})
    finally:
        db.close()
    token = get_token(database)
    if not token:
        raise ValueError('private credential required')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    store = private_path(database.parent / 'design-packs')
    os.environ['ESPADNA_PACK_STORE'] = str(store)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(ensure_no_webhook(token))
        run(database)
    finally:
        if not loop.is_closed():
            loop.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    path = private_path(args.config)
    try:
        run_config(path)
    except Exception as error:
        result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'ok': False, 'error_type': type(error).__name__}
        path.with_name('last-bot-status.json').write_text(json.dumps(result), encoding='utf-8')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
