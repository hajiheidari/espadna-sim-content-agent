"""Transport boundary. Constructing adapters performs no network operations."""
from .delivery import DefiniteRejection, DefiniteRetry, DeliveryUnknown, Receipt


class DryRunSender:
    mode = 'dry_run'

    def __init__(self):
        self.receipts = {}
        self.bundles = []

    async def send_bundle(self, bundle):
        if bundle.key not in self.receipts:
            start = len(self.bundles) * 6 + 1
            self.bundles.append(bundle)
            self.receipts[bundle.key] = Receipt(tuple(range(start, start + 6)))
        return self.receipts[bundle.key]


class TelegramSender:
    mode = 'telegram'

    def __init__(self, bot):
        self.bot = bot

    async def send_bundle(self, bundle):
        from telegram import InputMediaDocument
        from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter
        media = [InputMediaDocument(media=x.data, filename=x.filename, caption=x.caption) for x in bundle.media]
        try:
            messages = await self.bot.send_media_group(chat_id=bundle.recipient, media=media, protect_content=False, connect_timeout=20, read_timeout=60, write_timeout=60, pool_timeout=20)
            return Receipt(tuple(message.message_id for message in messages))
        except RetryAfter as exc:
            value = exc.retry_after
            raise DefiniteRetry(value.total_seconds() if hasattr(value, 'total_seconds') else value) from None
        except (BadRequest, Forbidden):
            raise DefiniteRejection() from None
        except NetworkError:
            raise DeliveryUnknown() from None
