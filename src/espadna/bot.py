"""Private-chat Telegram scaffold; no publishing or network calls at import time."""
import logging
import os
from datetime import datetime
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo
from .core import connect, require, set_role, revoke_user, set_sale_status, caption
from .importer import (MAX_BYTES, apply_saved_sync, cancel_saved_sync,
                       prepare_saved_sync, save_sync_upload)


def build_application(token, database):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    from .transports import request_client

    def authorize(update, db, roles):
        if not update.effective_chat or update.effective_chat.type != 'private' or not update.effective_user:
            raise PermissionError('private authorized chat required')
        actor = update.effective_user.id
        require(db, actor, roles)
        return actor

    async def notify_owners(db, bot, text, keyboard=None):
        from .members import owners
        for owner in owners(db):
            await bot.send_message(chat_id=owner, text=text, reply_markup=keyboard)

    def approved_plan(db, month):
        row = db.execute("SELECT id FROM content_plans WHERE month=? AND state='approved'", (month,)).fetchone()
        if row is None:
            raise ValueError('approved content is not ready for this month')
        return row['id']

    async def command(update, context):
        db = connect(database)
        try:
            name = update.effective_message.text.split()[0].split('@')[0]
            if name == '/start' and update.effective_chat.type == 'private' and update.effective_user:
                from .members import request_membership
                user = update.effective_user
                known = db.execute('SELECT role,enabled FROM users WHERE telegram_id=?', (user.id,)).fetchone()
                if known and known['enabled'] and known['role'] in ('owner', 'admin'):
                    await update.effective_message.reply_text('پنل مدیریتی فعال است. برای فهرست دستورات /help را بزنید.')
                    return
                status = request_membership(db, user.id, user.username, user.first_name)
                if status == 'approved':
                    await update.effective_message.reply_text('عضویت شما فعال است. برای محتوای امروز /today و برای انتخاب روزهای ماه /month را بزنید.')
                elif status == 'pending':
                    await update.effective_message.reply_text('درخواست عضویت شما در انتظار بررسی مالک است.')
                else:
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton('تأیید', callback_data=f'member:approve:{user.id}'),
                        InlineKeyboardButton('رد', callback_data=f'member:reject:{user.id}')
                    ]])
                    label = ('@' + user.username) if user.username else (user.first_name or str(user.id))
                    await notify_owners(db, context.bot, f'درخواست عضویت جدید اسپادنا سیم\n{label}\nشناسه: {user.id}', keyboard)
                    await update.effective_message.reply_text('درخواست عضویت برای مالک ارسال شد.')
                return
            if name == '/myid' and update.effective_chat.type == 'private' and update.effective_user:
                await update.effective_message.reply_text(f'شناسهٔ تلگرام شما: {update.effective_user.id}')
                return  # Reveals only the caller's ID; never grants a role.
            actor = authorize(update, db, {'owner', 'admin', 'viewer'})
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
                rows = db.execute("SELECT phone_normalized,phone_display,sim_type,sim_status,operator,sale_status,active FROM inventory WHERE active=1 AND sale_status='available' ORDER BY phone_normalized LIMIT 10").fetchall()
                text = '\n\n'.join(caption(row) for row in rows) or 'موجودی قابل انتشار نداریم.'
            elif name in ('/today', '/month'):
                from .members import employee_allowed
                if not employee_allowed(db, actor):
                    raise PermissionError('approved employee required')
                now = datetime.now(ZoneInfo('Asia/Tehran'))
                plan_id = approved_plan(db, now.strftime('%Y-%m'))
                if name == '/month':
                    from .content import _payload
                    payload, _, _ = _payload(db, actor, plan_id)
                    days = sorted({item['scheduled_at'][:10] for item in payload['items']})
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(day, callback_data=f'content:day:{day}:{plan_id}')] for day in days])
                    await update.effective_message.reply_text('روز موردنظر از محتوای آماده ماه را انتخاب کنید:', reply_markup=keyboard)
                    return
                await update.effective_message.reply_text('محتوای امروز در حال آماده‌سازی است.')
                from .delivery import deliver_manual
                from .transports import TelegramSender
                try:
                    result = await deliver_manual(db, actor, plan_id, now.date().isoformat(), TelegramSender(context.bot),
                                                  Path(database).resolve().parent / 'manual-deliveries')
                    text = 'شش استوری امروز با کیفیت اصلی ارسال شد.'
                    await notify_owners(db, context.bot, f"ارسال دستی موفق برای کارمند {actor}، تاریخ {result['day']}")
                except Exception:
                    await notify_owners(db, context.bot, f'خطا در ارسال دستی برای کارمند {actor}. وضعیت در گزارش داخلی ثبت شد.')
                    raise ValueError('manual delivery failed') from None
            elif name == '/alerts':
                from .content import invalid_exports
                alerts = invalid_exports(db, actor)
                text = '\n'.join(f"خروجی نامعتبر: {item['export_id']} (برنامه {item['plan_id']})" for item in alerts[:20]) or 'خروجی نامعتبر ثبت نشده است.'
            elif name in ('/route', '/unroute', '/enqueue', '/queue', '/canceljob'):
                from .delivery import set_route, enqueue_plan, list_jobs, cancel_job
                if name in ('/route', '/unroute'):
                    if len(context.args) != 1:
                        raise ValueError('usage')
                    set_route(db, actor, int(context.args[0]), name == '/route')
                    text = 'مسیر تحویل خصوصی ثبت شد.'
                elif name == '/enqueue':
                    if len(context.args) not in (1, 2):
                        raise ValueError('usage')
                    mode = context.args[1] if len(context.args) == 2 else 'dry_run'
                    counts = enqueue_plan(db, actor, int(context.args[0]), mode)
                    text = f"کار جدید {counts['created']}، به‌روزرسانی {counts['updated']}؛ حالت {mode}. هیچ ارسال مستقیمی انجام نشد."
                elif name == '/canceljob':
                    if len(context.args) != 1:
                        raise ValueError('usage')
                    cancel_job(db, actor, context.args[0])
                    text = 'کار صف لغو شد.'
                else:
                    jobs = list_jobs(db, actor)
                    text = '\n'.join(f"{j['day']} / {j['mode']} / {j['state']}\n{j['id']}" for j in jobs[:15]) or 'کاری در صف نیست.'
            elif name in ('/staff', '/staffadd', '/staffenable', '/staffdisable', '/staffremove', '/staffdaily', '/dailystatus'):
                from .members import (add_employee, daily_globally_enabled, list_employees,
                                      set_employee_daily, set_employee_state)
                require(db, actor, {'owner'})
                if name == '/staff':
                    rows = list_employees(db, actor)
                    text = '\n'.join(f"{r['telegram_id']} / {r['status']} / daily={'on' if r['daily_enabled'] else 'off'}" for r in rows) or 'کارمندی ثبت نشده است.'
                elif name == '/dailystatus':
                    text = 'ارسال روزانه عمومی فعال است.' if daily_globally_enabled(db) else 'ارسال روزانه عمومی تا تأیید نهایی مالک غیرفعال است.'
                elif name == '/staffadd':
                    if len(context.args) != 1: raise ValueError('usage')
                    add_employee(db, actor, int(context.args[0])); text = 'کارمند با نقش Viewer فعال شد.'
                elif name == '/staffenable':
                    if len(context.args) != 1: raise ValueError('usage')
                    set_employee_state(db, actor, int(context.args[0]), 'approved'); text = 'کارمند دوباره فعال شد.'
                elif name == '/staffdisable':
                    if len(context.args) != 1: raise ValueError('usage')
                    set_employee_state(db, actor, int(context.args[0]), 'disabled'); text = 'کارمند غیرفعال شد.'
                elif name == '/staffremove':
                    if len(context.args) != 1: raise ValueError('usage')
                    set_employee_state(db, actor, int(context.args[0]), 'removed'); text = 'دسترسی کارمند حذف شد و تاریخچه حفظ شد.'
                else:
                    if len(context.args) != 2 or context.args[1] not in ('on','off'): raise ValueError('usage')
                    set_employee_daily(db, actor, int(context.args[0]), context.args[1] == 'on'); text = 'وضعیت ارسال روزانه کارمند ثبت شد.'
            elif name in ('/packs', '/packapprove', '/packdisable', '/packdefault', '/packuse'):
                from .packs import list_packs, approve_pack, disable_pack, set_default_pack, select_plan_pack
                if name == '/packs':
                    packs = list_packs(db, actor)
                    text = '\n'.join(f"{p['name']} / {p['version']} / {p['state']}\n{p['id']}" for p in packs[:10]) or 'پک شخصی ثبت نشده است؛ قالب‌های فعلی در دسترس‌اند.'
                elif name == '/packapprove':
                    if len(context.args) != 2 or context.args[1] != 'reviewed':
                        raise ValueError('owner must review images first')
                    approve_pack(db, actor, context.args[0], True)
                    text = 'بررسی محتوای پک ثبت شد؛ اکنون می‌توان آن را انتخاب کرد.'
                elif name == '/packdisable':
                    if len(context.args) != 1:
                        raise ValueError('usage')
                    disable_pack(db, actor, context.args[0])
                    text = 'پک غیرفعال شد؛ خروجی‌های وابسته نیازمند بررسی دوباره‌اند.'
                elif name == '/packdefault':
                    if len(context.args) != 1:
                        raise ValueError('usage')
                    set_default_pack(db, actor, None if context.args[0] == 'builtin' else context.args[0])
                    text = 'پک پیش‌فرض برنامه‌های بعدی ثبت شد.'
                else:
                    if len(context.args) != 2:
                        raise ValueError('usage')
                    select_plan_pack(db, actor, int(context.args[0]), None if context.args[1] == 'builtin' else context.args[1])
                    text = 'پک برنامه انتخاب شد؛ در صورت تغییر، تأیید دوبارهٔ برنامه لازم است.'
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
                text = '/today\n/month\n/staff\n/staffadd USER_ID\n/staffenable USER_ID\n/staffdisable USER_ID\n/staffremove USER_ID\n/staffdaily USER_ID on|off\n/dailystatus\n/myid\n/catalog\n/alerts\n/queue\n/route USER_ID\n/unroute USER_ID\n/enqueue PLAN_ID [dry_run|telegram]\n/canceljob JOB_ID\n/packs\n/packapprove PACK_ID reviewed\n/packdisable PACK_ID\n/packdefault PACK_ID|builtin\n/packuse PLAN_ID PACK_ID|builtin\n/sale NUMBER available|reserved|sold\n/role USER_ID owner|admin|viewer\n/revoke USER_ID\n/plan YYYY-MM\n/planstatus ID\n/approve ID\n/refresh ID\nاکسل: ارسال xlsx توسط مالک یا ادمین. پک شخصی: ZIP شامل pack.json و عکس‌ها، فقط مالک در چت خصوصی.'
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
            if not doc.file_name or not doc.file_name.lower().endswith(('.xlsx', '.zip')) or not doc.file_size or doc.file_size > MAX_BYTES:
                raise ValueError('invalid document')
            is_pack = doc.file_name.lower().endswith('.zip')
            storage = os.environ.get('ESPADNA_PACK_STORE')
            if is_pack:
                require(db, actor, {'owner'})
                if not storage:
                    raise ValueError('private design storage is not configured')
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ('pack.zip' if is_pack else 'inventory.xlsx')
                file = await context.bot.get_file(doc.file_id)
                await file.download_to_drive(path)
                # Local parser checks archive size, formulas, all rows and conflicts.
                if is_pack:
                    from .packs import import_pack, pack_row
                    pack_id = import_pack(db, actor, path, storage)
                    state = pack_row(db, pack_id)['state']
                    text = f'پک شخصی به صورت خصوصی ثبت شد. وضعیت: {state}\nشناسه: {pack_id}'
                    if state == 'draft':
                        text += '\nپیش از استفاده تصاویر را از نظر قیمت و شمارهٔ ثابت بررسی کنید.'
                else:
                    preview_id = save_sync_upload(db, actor, path)
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton('افزودن و به‌روزرسانی', callback_data=f'inv:preview:merge:{preview_id}'),
                        InlineKeyboardButton('همگام‌سازی کامل موجودی', callback_data=f'inv:preview:full:{preview_id}')
                    ]])
                    await update.effective_message.reply_text('فایل بررسی شد. روش اعمال را انتخاب کنید؛ هنوز هیچ تغییری در موجودی انجام نشده است.', reply_markup=keyboard)
                    return
            await update.effective_message.reply_text(text)
        except ValueError as exc:
            await update.effective_message.reply_text(f'فایل پذیرفته نشد؛ داده‌ای تغییر نکرد.\n{str(exc)[:3500]}')
        except PermissionError:
            await update.effective_message.reply_text('فایل پذیرفته نشد یا دسترسی ندارید؛ داده‌ای تغییر نکرد.')
        finally:
            db.close()

    def report_text(report):
        text = (f"پیش‌نمایش موجودی\nجدید: {report['inserted']}\nتغییرکرده: {report['updated']}\n"
                f"بدون تغییر: {report['unchanged']}\nخارج‌شونده: {report['removed']}\n"
                f"تکراری: {report['duplicates']}\nشیت‌ها: {report['sheets']}، ردیف‌های پردازش‌شده: {report['rows']}")
        if report['duplicate_rows']:
            locations = '، '.join(f"{item['sheet']} ردیف {item['row']}" for item in report['duplicate_rows'][:20])
            text += '\nردیف‌های تکراری: ' + locations
        return text

    async def inventory_callback(update, context):
        query = update.callback_query
        await query.answer()
        db = connect(database)
        try:
            actor = authorize(update, db, {'owner', 'admin'})
            parts = query.data.split(':')
            if len(parts) != 4 or parts[0] != 'inv' or parts[1] not in ('preview', 'apply', 'cancel'):
                raise ValueError('invalid inventory action')
            action, mode, preview_id = parts[1], parts[2], parts[3]
            if action == 'preview':
                report = prepare_saved_sync(db, actor, preview_id, mode)
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton('تأیید و اعمال', callback_data=f'inv:apply:{mode}:{preview_id}'),
                    InlineKeyboardButton('لغو', callback_data=f'inv:cancel:{mode}:{preview_id}')
                ]])
                await query.edit_message_text(report_text(report) + '\n\nبرای اعمال این تغییرات تأیید کنید.', reply_markup=keyboard)
            elif action == 'apply':
                backup_dir = os.environ.get('ESPADNA_BACKUP_DIR', str(Path(database).resolve().parent / 'backups'))
                result = apply_saved_sync(db, actor, preview_id, backup_dir)
                await query.edit_message_text(report_text(result) + '\n\nهمگام‌سازی با موفقیت و به‌صورت اتمیک اعمال شد.')
            else:
                cancel_saved_sync(db, actor, preview_id)
                await query.edit_message_text('عملیات لغو شد و هیچ تغییری در موجودی انجام نشد.')
        except (ValueError, PermissionError) as exc:
            await query.edit_message_text(f'عملیات انجام نشد و موجودی تغییر نکرد.\n{str(exc)[:3500]}')
        finally:
            db.close()

    async def membership_callback(update, context):
        query = update.callback_query
        await query.answer()
        db = connect(database)
        try:
            actor = authorize(update, db, {'owner'})
            _, decision, target = query.data.split(':')
            from .members import review_membership
            status = review_membership(db, actor, int(target), decision == 'approve')
            await query.edit_message_text(f'درخواست کاربر {target}: {status}')
            await context.bot.send_message(chat_id=int(target), text='عضویت شما تأیید شد. /today و /month در دسترس است.' if status == 'approved' else 'درخواست عضویت شما تأیید نشد.')
        except (ValueError, PermissionError):
            await query.edit_message_text('این درخواست دیگر قابل بررسی نیست یا دسترسی ندارید.')
        finally:
            db.close()

    async def content_callback(update, context):
        query = update.callback_query
        await query.answer()
        db = connect(database)
        try:
            actor = authorize(update, db, {'viewer'})
            _, _, day, plan_id = query.data.split(':')
            from .delivery import deliver_manual
            from .transports import TelegramSender
            await query.edit_message_text(f'محتوای {day} در حال آماده‌سازی است.')
            try:
                result = await deliver_manual(db, actor, int(plan_id), day, TelegramSender(context.bot),
                                              Path(database).resolve().parent / 'manual-deliveries')
                await query.edit_message_text(f"شش استوری {result['day']} با کیفیت اصلی ارسال شد.")
                await notify_owners(db, context.bot, f"ارسال دستی موفق برای کارمند {actor}، تاریخ {result['day']}")
            except Exception:
                await notify_owners(db, context.bot, f'خطا در ارسال دستی برای کارمند {actor}. وضعیت در گزارش داخلی ثبت شد.')
                raise ValueError('manual delivery failed') from None
        except (ValueError, PermissionError):
            await query.edit_message_text('ارسال انجام نشد؛ عضویت، برنامه یا موجودی معتبر نیست.')
        finally:
            db.close()

    async def error(update, context):
        # Never log Telegram updates, URLs (contain token), exception text or workbook data.
        logging.getLogger(__name__).error('Telegram operation failed (%s)', type(context.error).__name__)

    app = Application.builder().token(token).request(request_client()).get_updates_request(request_client()).build()
    app.add_handler(CommandHandler(['start', 'help', 'today', 'month', 'staff', 'staffadd', 'staffenable', 'staffdisable', 'staffremove', 'staffdaily', 'dailystatus', 'myid', 'role', 'revoke', 'sale', 'catalog', 'plan', 'planstatus', 'approve', 'refresh', 'alerts', 'packs', 'packapprove', 'packdisable', 'packdefault', 'packuse', 'route', 'unroute', 'enqueue', 'queue', 'canceljob'], command))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern=r'^inv:(preview|apply|cancel):(merge|full):[0-9a-f]{32}$'), group=1)
    app.add_handler(CallbackQueryHandler(membership_callback, pattern=r'^member:(approve|reject):[1-9][0-9]{0,19}$'), group=1)
    app.add_handler(CallbackQueryHandler(content_callback, pattern=r'^content:day:[0-9]{4}-[0-9]{2}-[0-9]{2}:[1-9][0-9]*$'), group=1)
    app.add_handler(MessageHandler(filters.Document.ALL, document))
    app.add_error_handler(error)
    return app


def run(database):
    from .credentials import get_token
    token = get_token(database)
    if not token:
        raise ValueError('TELEGRAM_BOT_TOKEN is required')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    build_application(token, database).run_polling()
