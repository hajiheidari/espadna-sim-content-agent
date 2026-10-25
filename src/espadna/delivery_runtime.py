"""Explicit local worker entry point. Never launches a service at import time."""
import logging
from .core import require
from .delivery import epoch, expire_waiting, run_once, utc_now
from .transports import DryRunSender, TelegramSender, request_client


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
        expire_waiting(db, actor, mode)
        stamp = epoch(utc_now())
        pending = db.execute("SELECT 1 FROM delivery_jobs WHERE mode=? AND ((state IN ('queued','retry') AND due_at<=? AND next_attempt_at<=?) OR (state IN ('leased','sending') AND lease_until<=?)) LIMIT 1", (mode, stamp, stamp, stamp)).fetchone()
        if pending is None:
            return []  # Minute ticks outside delivery time need no token/network.
        from .credentials import get_token
        token = get_token(db.execute('PRAGMA database_list').fetchone()[2])
        if not token:
            raise ValueError('TELEGRAM_BOT_TOKEN must be set privately before launch')
        # HTTP request URLs can contain the bot token. Do not enable HTTP logs.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        from telegram import Bot
        from telegram.error import TelegramError
        try:
            async with Bot(token, request=request_client()) as bot:
                await process(TelegramSender(bot))
        except TelegramError:
            raise ValueError('Telegram worker connection failed; inspect queue status before restarting') from None
    return results


async def check_bot(db, actor):
    """Read-only identity/chat check; never sends a Telegram message."""
    require(db, actor, {'owner'})
    from .credentials import get_token
    token = get_token(db.execute('PRAGMA database_list').fetchone()[2])
    if not token:
        raise ValueError('private Telegram credential is not configured')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    from telegram import Bot
    from telegram.error import BadRequest, Forbidden, InvalidToken, TelegramError, TimedOut
    try:
        async with Bot(token, request=request_client()) as bot:
            identity = {'bot_username': bot.username, 'bot_id': bot.id, 'private_chat_available': False}
            try:
                chat = await bot.get_chat(actor)
                identity['private_chat_available'] = chat.type == 'private' and chat.id == actor
            except (BadRequest, Forbidden):
                pass
            return identity
    except InvalidToken:
        raise ValueError('Telegram rejected bot authentication; update the private credential') from None
    except TimedOut:
        raise ValueError('Telegram connection timed out; check local connectivity or proxy') from None
    except TelegramError:
        raise ValueError('Telegram identity check failed; credential values are not logged') from None
