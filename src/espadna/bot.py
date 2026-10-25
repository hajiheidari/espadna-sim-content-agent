"""Private-chat Telegram scaffold; no publishing or network calls at import time."""
import logging
import os
from pathlib import Path
import tempfile
from .core import connect, require, set_role, revoke_user, set_sale_status, caption
from .importer import MAX_BYTES, import_workbook


def build_application(token, database):
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    def authorize(update, db, roles):
        if not update.effective_chat or update.effective_chat.type != 'private' or not update.effective_user:
            raise PermissionError('private authorized chat required')
        actor = update.effective_user.id
        require(db, actor, roles)
        return actor

    async def command(update, context):
        db = connect(database)
        try:
            actor = authorize(update, db, {'owner', 'admin', 'viewer'})
            name = update.effective_message.text.split()[0].split('@')[0]
            if name == '/role':
                if len(context.args) != 2:
                    raise ValueError('usage')
                set_role(db, actor, int(context.args[0]), context.args[1])
                text = 'نقش ثبت شد.'
            elif name == '/revoke':
                if len(context.args) != 1:
                    raise ValueError('usage')
                revoke_user(db, actor, int(context.args[0]))
                text = 'دسترسی کاربر لغو شد؛ سوابق تغییرات حفظ شد.'
            elif name == '/sale':
                if len(context.args) != 2:
                    raise ValueError('usage')
                set_sale_status(db, actor, *context.args)
                text = 'وضعیت فروش ثبت شد.'
            elif name == '/catalog':
                rows = db.execute("SELECT phone,sim_type,sim_status,operator,sale_status FROM inventory WHERE sale_status='available' ORDER BY phone LIMIT 10").fetchall()
                text = '\n\n'.join(caption(row) for row in rows) or 'موجودی قابل انتشار نداریم.'
            elif name == '/alerts':
                from .content import invalid_exports
                alerts = invalid_exports(db, actor)
                text = '\n'.join(f"خروجی نامعتبر: {item['export_id']} (برنامه {item['plan_id']})" for item in alerts[:20]) or 'خروجی نامعتبر ثبت نشده است.'
            elif name in ('/plan', '/planstatus', '/approve', '/refresh'):
                from .content import create_plan, inspect_plan, approve_plan, refresh_plan
                if len(context.args) != 1:
                    raise ValueError('usage')
                if name == '/plan':
                    plan_id = create_plan(db, actor, context.args[0])
                    text = f'پیش‌نویس برنامه ساخته شد. شناسه: {plan_id}'
                elif name == '/approve':
                    approve_plan(db, actor, int(context.args[0]))
                    text = 'برنامه تأیید شد؛ هیچ ارسال عمومی انجام نشده است.'
                elif name == '/refresh':
                    count = refresh_plan(db, actor, int(context.args[0]))
                    text = f'برنامه بازسازی شد؛ {count} مورد تغییر کرد و تأیید دوباره لازم است.'
                else:
                    status = inspect_plan(db, actor, int(context.args[0]))
                    text = f"وضعیت: {status['state']}، تعداد: {status['slots']}، موارد نیازمند بازسازی: {status['stale_slots']}"
            else:
                text = '/catalog\n/alerts\n/sale NUMBER available|reserved|sold\n/role USER_ID owner|admin|viewer\n/revoke USER_ID\n/plan YYYY-MM\n/planstatus ID\n/approve ID\n/refresh ID\nورود اکسل برای مالک و ادمین: ارسال فایل xlsx در گفت‌وگوی خصوصی.'
            await update.effective_message.reply_text(text)
        except (ValueError, PermissionError):
            await update.effective_message.reply_text('درخواست نامعتبر است یا دسترسی ندارید.')
        finally:
            db.close()

    async def document(update, context):
        db = connect(database)
        try:
            actor = authorize(update, db, {'owner', 'admin'})
            doc = update.effective_message.document
            if not doc.file_name or not doc.file_name.lower().endswith('.xlsx') or not doc.file_size or doc.file_size > MAX_BYTES:
                raise ValueError('invalid document')
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'inventory.xlsx'
                file = await context.bot.get_file(doc.file_id)
                await file.download_to_drive(path)
                # Local parser checks archive size, formulas, all rows and conflicts.
                result = import_workbook(db, actor, path)
            await update.effective_message.reply_text(f"ورود انجام شد: جدید {result['inserted']}، تغییر {result['updated']}، بدون تغییر {result['unchanged']}")
        except (ValueError, PermissionError):
            await update.effective_message.reply_text('فایل پذیرفته نشد یا دسترسی ندارید؛ داده‌ای تغییر نکرد.')
        finally:
            db.close()

    async def error(update, context):
        # Never log Telegram updates, URLs (contain token), exception text or workbook data.
        logging.getLogger(__name__).error('Telegram operation failed (%s)', type(context.error).__name__)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help', 'role', 'revoke', 'sale', 'catalog', 'plan', 'planstatus', 'approve', 'refresh', 'alerts'], command))
    app.add_handler(MessageHandler(filters.Document.ALL, document))
    app.add_error_handler(error)
    return app


def run(database):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('TELEGRAM_BOT_TOKEN is required')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    build_application(token, database).run_polling()
