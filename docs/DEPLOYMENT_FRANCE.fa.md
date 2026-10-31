# استقرار سرور فرانسه

این سند وضعیت استقرار آزمایشی پایدار Espadna Sim Content Agent روی Ubuntu فرانسه را ثبت می‌کند. هیچ توکن، شناسه خصوصی، موجودی یا داده مشتری در مخزن نگهداری نمی‌شود.

## مسیرها

- کد خواندنی: `/opt/espadna/app`
- محیط Python: `/opt/espadna/venv`
- دیتابیس خصوصی: `/var/lib/espadna/espadna.db`
- خروجی‌های خصوصی: `/var/lib/espadna/artifacts`
- بکاپ خصوصی: `/var/backups/espadna`
- تنظیمات خصوصی: `/etc/espadna`

## سرویس‌ها

`espadna-queue-worker.timer` هر دقیقه worker را اجرا می‌کند. زمان سررسید هر کار در دیتابیس با منطقه زمانی `Asia/Tehran` محاسبه می‌شود؛ بنابراین تغییر ساعت فرانسه روی ساعت 08:00 ایران اثر ندارد. timer با `Persistent=true` پس از reboot بازیابی می‌شود.

`espadna-bot.service` برای polling ربات مدیریت خصوصی آماده شده، اما فقط پس از قرار گرفتن توکن در `/etc/espadna/telegram.env` و بررسی هویت ربات فعال می‌شود.

## امنیت

هر دو سرویس با کاربر محدود `espadna`، `NoNewPrivileges`، فایل‌سیستم محافظت‌شده و umask خصوصی اجرا می‌شوند. توکن فقط در فایل محیطی root-owned روی سرور قرار می‌گیرد و وارد Git یا آرگومان خط فرمان نمی‌شود.

## عملیات

```sh
sudo systemctl status espadna-queue-worker.timer
sudo journalctl -u espadna-queue-worker.service --since today
sudo systemctl status espadna-bot.service
```

پیش از ارسال واقعی باید موجودی وارد، برنامه ماهانه ساخته و تأیید، گیرنده مجاز تعیین و job در حالت `telegram` enqueue شود. ارسال آزمایشی واقعی فقط برای حساب مالک انجام می‌شود. نتیجه مبهم Telegram به‌صورت `uncertain` ثبت می‌شود و خودکار دوباره ارسال نمی‌شود.
