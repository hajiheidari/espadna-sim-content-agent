import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from espadna.credentials import get_token, read_token, save_token, validate_token

# Deliberately synthetic, never a live credential.
TOKEN = '123456:TEST_ONLY_PLACEHOLDER_VALUE'


class CredentialTests(unittest.TestCase):
    def test_bot_check_reads_identity_without_sending_and_sanitizes_errors(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        from telegram.error import NetworkError
        from espadna.core import bootstrap_owner, connect
        from espadna.delivery_runtime import check_bot
        db = connect(':memory:')
        try:
            bootstrap_owner(db, 1)
            bot = SimpleNamespace(username='synthetic_bot', id=123456, get_chat=AsyncMock(return_value=SimpleNamespace(id=1, type='private')), send_message=AsyncMock(), send_media_group=AsyncMock())
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=bot)
            context.__aexit__ = AsyncMock(return_value=False)
            with patch('espadna.credentials.get_token', return_value=TOKEN), patch('telegram.Bot', return_value=context):
                result = asyncio.run(check_bot(db, 1))
                self.assertTrue(result['private_chat_available'])
                bot.send_message.assert_not_called()
                bot.send_media_group.assert_not_called()
                context.__aenter__ = AsyncMock(side_effect=NetworkError('SECRET_VALUE'))
                with self.assertRaises(ValueError) as error:
                    asyncio.run(check_bot(db, 1))
                self.assertNotIn('SECRET_VALUE', str(error.exception))
        finally:
            db.close()

    def test_invalid_formats_never_echo_input(self):
        for value in ('private value', '123:short', None):
            with self.assertRaises(ValueError) as result:
                validate_token(value)
            self.assertNotIn(str(value), str(result.exception))

    def test_storage_rejects_git_checkout(self):
        with self.assertRaises(ValueError):
            save_token(Path(__file__).resolve().parents[1] / 'secret.dpapi', TOKEN)

    def test_loader_uses_environment_without_logging_or_opening_file(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': TOKEN}):
            self.assertEqual(get_token('unused.db'), TOKEN)

    def test_invalid_encrypted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'token.dpapi'
            path.write_bytes(b'not a credential')
            with self.assertRaises(ValueError):
                read_token(path)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows user-bound encryption test')
    def test_windows_round_trip_is_encrypted_and_project_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'telegram-token.dpapi'
            save_token(path, TOKEN)
            self.assertNotIn(TOKEN.encode(), path.read_bytes())
            self.assertEqual(read_token(path), TOKEN)
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '', 'ESPADNA_TOKEN_FILE': str(path)}):
                self.assertEqual(get_token(Path(tmp) / 'test.db'), TOKEN)
