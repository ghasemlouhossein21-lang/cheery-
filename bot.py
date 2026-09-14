"""
bot.py
فایل اصلی اجرای ربات. تمام Routerهای پوشه‌ی handlers اینجا به Dispatcher
وصل می‌شوند. یک سرور Flask کوچک هم کنارش اجرا می‌شود تا Render سرویس را
"زنده" تشخیص بدهد.
"""

import asyncio
import logging
import os
import threading

import aiohttp

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent, BotCommand, MenuButtonDefault
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.middlewares.base import BaseMiddleware

import database as db
import uniquepay
import payments
import alerts
import fsm_storage
import bot_loop
import vpn_panel
from config import TOKEN, UNIQUEPAY_ENABLED, ADMIN_ID
from keyboards import all_reply_menu_texts
from handlers import menu, start, wallet, profile, referral, plans, ticket, admin, panel_admin, marzban_admin
from handlers.plans import finalize_online_payment
from handlers.wallet import finalize_wallet_charge_online_payment
from alerts import check_usage_alerts, CHECK_INTERVAL_SECONDS

from flask import Flask

flask_app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BlockedUserMiddleware(BaseMiddleware):
    """اعمال مسدودی روی تمام پیام‌ها و callbackها، نه فقط /start."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)

        if user and user.id != ADMIN_ID and db.is_user_blocked(user.id):
            text = "🚫 دسترسی شما به ربات مسدود شده است. با پشتیبانی در ارتباط باشید."

            if getattr(event, "answer", None):
                try:
                    if event.__class__.__name__ == "CallbackQuery":
                        await event.answer(text, show_alert=True)
                    else:
                        await event.answer(text)
                except Exception:
                    logger.exception("خطا در اعلام مسدودی به کاربر")

            return None

        return await handler(event, data)


class MenuEscapeMiddleware(BaseMiddleware):

    def __init__(self):
        self._menu_texts: set[str] | None = None

    def _texts(self) -> set[str]:
        if self._menu_texts is None:
            try:
                self._menu_texts = all_reply_menu_texts()
            except Exception:
                logger.exception(
                    "خطا در ساخت لیست متن دکمه‌های منو برای فیکس گیرکردن FSM"
                )
                self._menu_texts = set()

        return self._menu_texts

    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None)
        state = data.get("state")

        if text and state is not None and text in self._texts():
            try:
                if await state.get_state() is not None:
                    await state.clear()
            except Exception:
                logger.exception(
                    "خطا در پاک‌کردن state ناتمام هنگام زدن دکمه‌ی ثابت منو"
                )

        return await handler(event, data)


async def global_error_handler(event: ErrorEvent):

    exc = event.exception

    # خطای بی‌ضرر تلگرام
    if (
        isinstance(exc, TelegramBadRequest)
        and "message is not modified" in str(exc).lower()
    ):
        logger.info(
            "نادیده‌گرفتن خطای بی‌ضرر message-is-not-modified"
        )

        try:
            update = event.update

            if update.callback_query:
                try:
                    await update.callback_query.answer()
                except Exception:
                    pass

        except Exception:
            pass

        return True

    logger.exception(
        "خطای پیش‌بینی‌نشده هنگام پردازش آپدیت: %s",
        event.exception,
        exc_info=event.exception,
    )

    try:
        import traceback as _tb

        db.log_error(
            error_type=type(event.exception).__name__,
            message=str(event.exception),
            traceback_text="".join(
                _tb.format_exception(
                    type(event.exception),
                    event.exception,
                    event.exception.__traceback__,
                )
            ),
            context="global_error_handler",
        )

    except Exception:
        pass

    update = event.update

    warning_text = (
        "⚠️ خطایی پیش آمد. لطفاً دوباره تلاش کنید "
        "یا با پشتیبانی تماس بگیرید."
    )

    try:

        if update.callback_query:

            try:
                await update.callback_query.answer(
                    warning_text,
                    show_alert=True
                )

            except Exception:

                logger.warning(
                    "امکان answer دوباره‌ی callback نبود؛ "
                    "ارسال پیام مستقیم به چت."
                )

                if update.callback_query.message:
                    await update.callback_query.message.answer(
                        warning_text
                    )

        elif update.message:

            await update.message.answer(warning_text)

    except Exception:
        logger.exception(
            "خطا حتی در تلاش برای اطلاع‌رسانی خطای اصلی به کاربر"
        )

    return True


@flask_app.route("/")
def health_check():
    return "ربات در حال اجراست ✅", 200


@flask_app.route("/health")
def render_health_check():
    # Endpoint intentionally cheap: Render health checks should not depend on
    # Telegram, Turso, Marzban, or any other external service.
    return "ok", 200


def run_flask():

    port = int(os.environ.get("PORT", 10000))

    flask_app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )


async def run_bot():

    db.init_db()

    recovered = db.recover_stuck_online_payments()

    if recovered:
        logger.warning(
            "%d پرداخت processing قدیمی برای پردازش مجدد بازیابی شد.",
            recovered
        )

    logger.info("Database initialized.")

    bot = Bot(token=TOKEN)

    # تنظیم منوی تلگرام
    try:

        await bot.set_my_commands([
            BotCommand(
                command="start",
                description="شروع / بازکردن منوی اصلی"
            ),
        ])

        await bot.set_chat_menu_button(
            menu_button=MenuButtonDefault()
        )

    except Exception:
        logger.exception(
            "خطا در تنظیم دکمه‌ی منوی بوم"
        )

    # Storage دائمی FSM
    dp = Dispatcher(
        storage=fsm_storage.DBStorage()
    )

    dp.errors.register(
        global_error_handler
    )

    # Middleware مسدودی
    blocked_middleware = BlockedUserMiddleware()

    dp.message.outer_middleware(
        blocked_middleware
    )

    dp.callback_query.outer_middleware(
        blocked_middleware
    )

    # خروج از FSM با دکمه‌های ثابت منو
    dp.message.outer_middleware(
        MenuEscapeMiddleware()
    )

    fsm_storage.storage = dp.storage

    bot_loop.main_loop = (
        asyncio.get_running_loop()
    )

    # Routerها
    dp.include_router(menu.router)
    dp.include_router(admin.router)
    dp.include_router(panel_admin.router)
    dp.include_router(marzban_admin.router)
    dp.include_router(start.router)
    dp.include_router(wallet.router)
    dp.include_router(profile.router)
    dp.include_router(referral.router)
    dp.include_router(plans.router)
    dp.include_router(ticket.router)

    # حلقه‌های پس‌زمینه
    asyncio.create_task(
        usage_alert_loop(bot)
    )

    asyncio.create_task(
        invoice_expiry_loop(bot)
    )

    if UNIQUEPAY_ENABLED:
        asyncio.create_task(
            online_payment_poller(bot)
        )

    # فقط self-ping باقی می‌ماند
    asyncio.create_task(
        self_ping_loop()
    )

    logger.info(
        "Bot starting polling..."
    )

    await dp.start_polling(bot)


async def usage_alert_loop(bot: Bot):

    """
    بررسی دوره‌ای مصرف و انقضای سرویس‌ها.
    """

    while True:

        try:

            await check_usage_alerts(bot)

        except Exception:

            logger.exception(
                "خطا در بررسی دوره‌ای هشدارهای مصرف/انقضا"
            )

        try:

            archived = (
                db.archive_expired_configs()
            )

            if archived > 0:

                logger.info(
                    "کانفیگ‌های منقضی‌شده آرشیو شدند: %d مورد",
                    archived
                )

        except Exception:

            logger.exception(
                "خطا در آرشیو خودکار کانفیگ‌های منقضی‌شده"
            )

        await asyncio.sleep(
            CHECK_INTERVAL_SECONDS
        )


ONLINE_PAYMENT_POLL_SECONDS = 20


async def online_payment_poller(bot: Bot):

    """
    هر ۲۰ ثانیه پرداخت‌های آنلاین در انتظار را بررسی می‌کند.
    """

    while True:

        checked = 0
        failed = 0

        try:

            pending = (
                db.get_pending_online_payments(
                    limit=50
                )
            )

            for payment in pending:

                checked += 1

                try:

                    invoice = await payments.check_invoice(
                        payment
                    )

                    if invoice and invoice.get("isPaid"):

                        payment_kind = payment.get(
                            "kind"
                        )

                        if payment_kind == "wallet_charge":

                            result = (
                                await finalize_wallet_charge_online_payment(
                                    bot,
                                    payment
                                )
                            )

                        else:

                            result = (
                                await finalize_online_payment(
                                    bot,
                                    payment
                                )
                            )

                        if result is None:
                            continue

                        try:

                            if payment_kind == "wallet_charge":

                                confirm_text = (
                                    f"✅ پرداخت آنلاین شما تأیید شد "
                                    f"و کیف پول شما به مبلغ "
                                    f"{payment['price']:,} تومان شارژ شد."
                                )

                            else:

                                confirm_text = (
                                    f"✅ پرداخت آنلاین شما برای "
                                    f"«{payment['plan_name']}» تأیید شد "
                                    f"و سفارش ثبت گردید. "
                                    f"سرویس شما به‌زودی ارسال می‌شود."
                                )

                            await bot.send_message(
                                int(payment["telegram_id"]),
                                confirm_text
                            )

                        except Exception:

                            logger.exception(
                                "ارسال پیام تایید پرداخت خودکار "
                                "به کاربر ناموفق بود"
                            )

                except Exception:

                    failed += 1

                    logger.exception(
                        "خطا در بررسی خودکار اینوویس %s",
                        payment.get("hash_id")
                    )

            if checked:

                await alerts.report_uniquepay_check_cycle(
                    bot,
                    ADMIN_ID,
                    checked,
                    failed
                )

        except Exception:

            logger.exception(
                "خطا در حلقه‌ی پولر پرداخت آنلاین"
            )

        await asyncio.sleep(
            ONLINE_PAYMENT_POLL_SECONDS
        )


INVOICE_EXPIRY_POLL_SECONDS = 60


async def invoice_expiry_loop(bot: Bot):

    """
    انقضای خودکار فاکتورهای پرداخت‌نشده.
    """

    while True:

        try:

            expired_invoices = (
                db.expire_due_invoices()
            )

            for inv in expired_invoices:

                try:

                    await bot.send_message(
                        int(inv["telegram_id"]),
                        (
                            f"⏰ مهلت ۳۰ دقیقه‌ای پرداخت "
                            f"فاکتور تان برای «{inv['label']}» "
                            f"به پایان رسید و به‌طور خودکار منقضی شد. "
                            f"لطفاً دوباره از منوی سرویس‌ها "
                            f"سفارش تان را ثبت کنید."
                        )
                    )

                except Exception:

                    logger.exception(
                        "ارسال پیام انقضای فاکتور "
                        "به کاربر ناموفق بود"
                    )

        except Exception:

            logger.exception(
                "خطا در حلقه‌ی انقضای فاکتورها"
            )

        try:

            expired_online = (
                db.expire_due_online_payments()
            )

            for pay in expired_online:

                try:

                    await bot.send_message(
                        int(pay["telegram_id"]),
                        (
                            "⏰ مهلت ۳۰ دقیقه‌ای پرداخت "
                            "این فاکتور به پایان رسیده و "
                            "به‌طور خودکار منقضی شد. "
                            "لطفاً دوباره از منوی سرویس‌ها "
                            "سفارش تان را ثبت کنید."
                        )
                    )

                except Exception:

                    logger.exception(
                        "ارسال پیام انقضای پرداخت‌های آنلاین "
                        "به کاربر ناموفق بود"
                    )

        except Exception:

            logger.exception(
                "خطا در حلقه‌ی انقضای پرداخت‌های آنلاین"
            )

        await asyncio.sleep(
            INVOICE_EXPIRY_POLL_SECONDS
        )


# ------------------------------------------------------------------
# Self Ping
# ------------------------------------------------------------------

SELF_PING_INTERVAL_SECONDS = 300


def _get_self_ping_url() -> str | None:
    """Build the public Render URL used by the keep-alive probe."""
    raw = (os.environ.get("SELF_PING_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if not raw:
        return None

    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    # RENDER_EXTERNAL_URL is the public root URL. Use a dedicated cheap health
    # endpoint instead of hitting the bot's UI/root page.
    if not os.environ.get("SELF_PING_URL"):
        raw = raw.rstrip("/") + "/health"
    return raw


async def self_ping_loop():
    """
    Keep the public Render web service receiving inbound HTTP traffic.

    The first probe is sent shortly after startup (the old implementation
    waited 10 minutes), then every 5 minutes. RENDER_EXTERNAL_URL is supplied
    by Render automatically for web services; SELF_PING_URL can override it.
    """
    ping_url = _get_self_ping_url()
    if not ping_url:
        logger.warning(
            "self_ping_loop غیرفعال است: SELF_PING_URL/RENDER_EXTERNAL_URL موجود نیست."
        )
        return

    logger.info(
        "self_ping_loop فعال شد؛ پینگ اولیه و سپس هر %d ثانیه به %s",
        SELF_PING_INTERVAL_SECONDS,
        ping_url,
    )

    timeout = aiohttp.ClientTimeout(total=20, connect=10, sock_read=10)
    headers = {
        "User-Agent": "premium-bot-render-keepalive/1.0",
        "Cache-Control": "no-cache",
    }

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        first = True
        while True:
            if not first:
                await asyncio.sleep(SELF_PING_INTERVAL_SECONDS)
            else:
                # Give Flask a moment to bind before the first request.
                await asyncio.sleep(5)
                first = False

            try:
                async with session.get(ping_url, allow_redirects=False) as resp:
                    await resp.read()
                    if 200 <= resp.status < 400:
                        logger.info("self-ping موفق بود (HTTP %s).", resp.status)
                    else:
                        logger.warning("self-ping پاسخ غیرموفق داد (HTTP %s).", resp.status)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("self-ping ناموفق بود؛ در چرخه بعدی دوباره تلاش می‌شود.")


def main():

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    logger.info(
        "Flask keep-alive server started."
    )

    asyncio.run(
        run_bot()
    )


if __name__ == "__main__":
    main()
