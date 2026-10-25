"""Explicit local worker entry point. Never launches a service at import time."""
import logging
import os
from .core import require
from .delivery import run_once
from .transports import DryRunSender, TelegramSender


async def run_ticks(db, actor, root, mode='dry_run', allow_network=False, limit=1, channel=None):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('worker limit must be between 1 and 50')
    require(db, actor, {'owner', 'admin'} if mode == 'dry_run' else {'owner'})
    if mode not in ('dry_run', 'telegram'):
        raise ValueError('unknown delivery mode')
    results = []

    async def process(sender):
        for _ in range(limit):
            result = await run_once(db, actor, sender, root, mode, allow_network, channel=channel)
            if result is None:
                break
            results.append(result)

    if mode == 'dry_run':
        await process(DryRunSender())
    else:
        if allow_network is not True:
            raise PermissionError('explicit network launch is required for real Telegram delivery')
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if not token:
            raise ValueError('TELEGRAM_BOT_TOKEN must be set privately before launch')
        # HTTP request URLs can contain the bot token. Do not enable HTTP logs.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        from telegram import Bot
        from telegram.error import TelegramError
        try:
            async with Bot(token) as bot:
                await process(TelegramSender(bot))
        except TelegramError:
            raise ValueError('Telegram worker connection failed; inspect queue status before restarting') from None
    return results
