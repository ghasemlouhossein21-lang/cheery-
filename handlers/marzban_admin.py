"""
handlers/marzban_admin.py
اتصال پنل مرزبان (Marzban Reseller VaaS) به ربات.

⚠️ نکته‌ی مهم درباره‌ی طراحی: این ماژول هیچ رفتار قبلی ربات را تغییر یا
حذف نمی‌کند مگر جایی که صراحتاً خواسته شده.

📌 قانون فعلی ارسال VIP بر اساس روش پرداخت:
- **کیف پول** و **پرداخت آنلاین (یونیک‌پی)**: اگر مرزبان فعال باشد و دسته‌بندی
  آن پلن به یک بسته‌ی مرزبان نگاشت شده باشد، سرویس بلافاصله و کاملاً خودکار
  ساخته و برای مشتری ارسال می‌شود — بدون هیچ دکمه‌ای برای انتخاب دستی/خودکار.
  اگر مرزبان غیرفعال باشد یا نگاشتی برای آن دسته‌بندی نباشد، دقیقاً مثل قبل
  به ادمین اطلاع داده می‌شود تا خودش دستی ارسال کند.
- **کارت‌به‌کارت**: تغییری نکرده — بعد از تأیید رسید توسط ادمین، همان دو دکمه‌ی
  «ارسال دستی» و «ارسال خودکار از پنل مرزبان» نشان داده می‌شود و انتخاب با
  ادمین است.

همین قانون عیناً برای «بساز سرویس خودت» هم پیاده شده (با یک نگاشت پیش‌فرض
واحد برای کل این بخش، چون حجم/مدت هر سفارش متغیر است، نه بر اساس دسته‌بندی).

⚠️ Gaming کاملاً و عمداً خارج از این ماژول نگه داشته شده: هیچ نگاشتی، هیچ
دکمه‌ی خودکاری و هیچ تماس API‌ای برای گیمینگ وجود ندارد. ارسال کانفیگ گیمینگ
همیشه ۱۰۰٪ دستی می‌ماند، دقیقاً مثل قبل، فارغ از روش پرداخت.

درباره‌ی اینکه «کدام دسته‌بندی از کدام ترافیک (اقتصادی/CDN) تغذیه شود»:
این تصمیم در سطح planSlug گرفته می‌شود — یعنی وقتی در پنل مرزبان یک بسته
تعریف می‌کنید، همان‌جا مشخص می‌کنید آن بسته از کدام استخر ترافیک بخورد.
اینجا (بخش «نگاشت دسته‌بندی‌ها») فقط تعیین می‌کنیم که هر دسته‌بندی ربات
(مثلاً «پرسرعت») باید کدام planSlug مرزبان را صدا بزند؛ چون آن planSlug از
قبل در خود پنل مرزبان به ترافیک درست وصل شده، همین یک نگاشت برای کنترل
کامل کافی است.
"""

import asyncio
import html
import json
import logging
import random
import re
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

import database as db
import alerts
import crypto
import marzban
import vpn_panel
import panels
import fsm_storage
import bot_info
from text_catalog import text as t
from utils import send_photo_rich, edit_caption_rich
from utils import send_rich
from utils import answer_rich, edit_rich
from config import MARZBAN_ENABLED, ADMIN_ID, FREE_TEST_PLAN_KEY
from states import AdminStates
from keyboards import (
    admin_marzban_menu,
    marzban_back_keyboard,
    marzban_map_category_pick_keyboard,
    marzban_map_vip_category_pick_keyboard,
    marzban_map_vip_plans_keyboard,
    marzban_plan_pick_keyboard,
    config_delivery_keyboard,
    main_reply_keyboard,
    admin_panel_menu,
    InlineKeyboardButton,

)
from utils import is_duplicate_action, now_tehran_naive, parse_int_in_range
from handlers.admin import _admin_perm, _is_admin, _log_fulfilled_order

router = Router(name="marzban_admin")


def _generate_service_username() -> str:
    """نام کاربری یکتا برای سرویس جدید در پنل VPN می‌سازد: پیشوند دلخواه ادمین (از بخش اطلاعات ربات) + یک کد عددی رندوم. قبل از استفاده، عدم تکراری بودنش در جدول configs بررسی می‌شود تا هرگز دو سرویس یک نام یکسان نگیرند."""
    raw_prefix = (bot_info.get("config_name_prefix") or "tg").strip()
    prefix = re.sub(r"[^A-Za-z0-9_]+", "", raw_prefix) or "tg"
    for _ in range(50):
        candidate = f"{prefix}_{random.randint(100000, 999999)}"
        if not db.is_service_id_taken(candidate):
            return candidate
    # این حالت عملاً ناممکن است و فقط تضمین یک فالبک مطمئن است
    return f"{prefix}_{int(datetime.now().timestamp())}"


def _format_volume_gb_label(volume_gb) -> str:
    if volume_gb is None:
        return "نامشخص"
    try:
        v = float(volume_gb)
    except (TypeError, ValueError):
        return str(volume_gb)
    if v <= 0:
        return "نامحدود"
    if v < 1:
        mb = round(v * 1024)
        return f"{mb} مگابایت"
    return f"{int(v) if v.is_integer() else v:g} گیگ"


def _actual_volume_gb_from_panel_response(data, fallback=None):
    """حجم واقعی سرویس ساخته‌شده را از پاسخ خود پنل استخراج می‌کند.

    Marzban مقدار data_limit را برحسب بایت در پاسخ ساخت کاربر برمی‌گرداند.
    بنابراین برای متن تحویلی مشتری، اولویت با مقدار واقعی ثبت‌شده در پنل است،
    نه صرفاً حجمی که از پلن ربات ارسال شده است. اگر پنل مقدار را برنگرداند،
    از fallback (حجم پلن/سفارش) استفاده می‌کنیم.
    """
    if isinstance(data, dict):
        raw = data.get("data_limit")
        if raw is not None:
            try:
                raw = int(raw)
                if raw <= 0:
                    return 0
                return raw / (1024 ** 3)
            except (TypeError, ValueError):
                pass

    return fallback
logger = logging.getLogger(__name__)


def _english_digits(value) -> str:
    text = str(value if value is not None else "")
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _delivery_service_label(plan_name, volume_gb, days, user_limit, plan_key=None) -> str:
    if plan_key == FREE_TEST_PLAN_KEY:
        return "تست رایگان"

    volume_text = _format_volume_gb_label(volume_gb) if volume_gb is not None else "نامشخص"
    days_text = f"زمان {days} روزه" if days else "زمان نامحدود"
    user_limit_text = "نامحدود کاربر" if not user_limit else f"{user_limit} کاربر"

    return _english_digits(f"{volume_text} | {days_text} | {user_limit_text}")


try:
    import qrcode
except ImportError:
    qrcode = None  # اگر نصب نباشد، لینک به‌جای عکس QR به‌صورت متنی فرستاده می‌شود.


def _make_qr_bytes(link: str) -> bytes:
    img = qrcode.make(link)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pretty(data, limit: int = 1500) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        text = str(data)
    if len(text) > limit:
        text = text[:limit] + "\n... (بریده‌شد)"
    return html.escape(text)


def _admin_fsm(bot) -> FSMContext | None:
    """FSMContext مربوط به چت خود ادمین، برای زمانی که می‌خواهیم از یک مسیر
    غیرتعاملی (مثل بعد از پرداخت کیف‌پول/آنلاین که در چت مشتری اتفاق می‌افتد)
    همان مکانیزم «لطفاً لینک رو دستی بفرست» را روی چت ادمین صدا بزنیم.
    اگر هنوز storage ثبت نشده باشد (مثلاً در تست) None برمی‌گرداند."""
    if fsm_storage.storage is None:
        return None
    return FSMContext(
        storage=fsm_storage.storage,
        key=StorageKey(bot_id=bot.id, chat_id=ADMIN_ID, user_id=ADMIN_ID),
    )


# ---------------------------------------------------------------------------
# 🤖 ارسال کاملاً خودکار بعد از پرداخت کیف‌پول/آنلاین (بدون دخالت ادمین)
# فقط VIP و «بساز سرویس خودت» — Gaming هرگز از این مسیر عبور نمی‌کند.
# توجه: این تابع از طریق vpn_panel کار می‌کند، پس روی هرکدام از پنل مرزبان یا پاسارگارد
# (هرکدام که به‌عنوان پنل فعال انتخاب شده باشد) کار می‌کند.
# اگر هیچ پنلی متصل/فعال نباشد یا نگاشتی نباشد، False برمی‌گرداند تا مسیر همیشگی
# (اطلاع دستی به ادمین) دنبال شود و هیچ سفارشی گم نشود.
# ---------------------------------------------------------------------------
async def auto_fulfill_vip_via_marzban(bot, uid, plan_key: str, order_id: int | None) -> bool:
    mapping = db.get_panel_map_for_plan_key(plan_key)
    if not mapping or not mapping.get("panel_id"):
        return False

    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if not plan or not user:
        return False

    panel_id = int(mapping["panel_id"])
    # در نگاشت پاسارگارد، remote_ref همان ID تمپلیت پنل است. برای
    # سازگاری با رکوردهای قدیمی، plan_slug هم به‌عنوان fallback پذیرفته می‌شود.
    template_id = mapping.get("remote_ref") or mapping.get("plan_slug")
    if template_id is None:
        return False
    panel_label = vpn_panel.panel_label(panel_id)
    username = _generate_service_username()
    # Template فقط تنظیمات پنل (پروتکل/این‌باند/گروه) را تأمین می‌کند؛
    # حجم، مدت و HWID دقیقاً از پلن فروش ربات اعمال می‌شوند.
    ok, data, msg = await vpn_panel.create_user_custom(
        int(template_id), username, plan.get("volume_gb"), plan.get("days"),
        device_limit=plan.get("user_limit"),
        panel_id=panel_id,
    )
    if not ok:
        await send_rich(bot, 
            ADMIN_ID,
            f"⚠️ خرید VIP (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از پنل {panel_label} ارسال شود ولی "
            f"ساخت سرویس در پنل ناموفق بود:\n{msg}\n"
            f"🔑 Template ID ارسال‌شده: {template_id}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی زیر همین سفارش استفاده کن. "
            "اگه خطا NOT_FOUND بود، احتمالاً باید این نگاشت رو دوباره از منوی پنل فعال تنظیم کنی (توجه: نگاشت بسته به پنل فعلی بستگی دارد — اگر پنل فعال را عوض کردید، باید دوباره از روی همان پنل نگاشت کنید).",
        )
        return False

    link, slug = vpn_panel.extract_link_and_username(data)
    actual_volume_gb = _actual_volume_gb_from_panel_response(data, plan.get("volume_gb"))
    snapshot = {"name": plan.get("name"), "volume_gb": actual_volume_gb, "days": plan.get("days"), "user_limit": plan.get("user_limit")}
    ctx = {"uid": uid, "plan_key": plan_key, "order_id": order_id, "order_kind": "plan",
           "slug": slug, "snapshot": snapshot, "panel_id": panel_id}

    if not link:
        await send_rich(bot, 
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(marzban_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_marzban_manual_link)
        await send_rich(bot, 
            ADMIN_ID,
            "⚠️ سرویس در پنل مرزبان ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل (فقط برای رفع اشکال ادمین) با ارسال واقعی کانفیگ برای مشتری
    # کاملاً مستقل است؛ به‌جای پشت‌سرهم، همزمان اجرا می‌شوند تا مشتری منتظر این پیام‌ای
    # فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(send_rich(bot, 
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )),
        asyncio.ensure_future(_deliver_marzban_link(bot, ctx, link)),
    )
    return True


async def auto_fulfill_custom_via_marzban(bot, user: dict, order_id: int, volume, days, custom_name) -> bool:
    """معادل تابع بالا، ولی برای سفارش‌های «بساز سرویس خودت». چون این سفارش‌ها
    دسته‌بندی ثابت ندارند (حجم/مدت دلخواه مشتری‌ست)، یک نگاشت پیش‌فرض واحد
    (scope='custom_build') استفاده می‌شود که از منوی مرزبان/اتصال پنل قابل تنظیم است،
    و از طریق vpn_panel روی هرکدام از پنل‌های مرزبان/پاسارگارد (هرکدام پنل فعال) اجرا می‌شود."""
    mapping = db.get_panel_plan_map("custom_build", 0)
    if not mapping or not mapping.get("panel_id"):
        return False

    panel_id = int(mapping["panel_id"])
    panel_label = vpn_panel.panel_label(panel_id)
    username = _generate_service_username()
    template_id = mapping.get("remote_ref")
    # 🆕 فیکس: حجم/مدت دقیقاً همانی است که مشتری سفارش داده (volume/days)، نه از روی تمپلیت نگاشت‌شده.
    ok, data, msg = await vpn_panel.create_user_custom(int(mapping["remote_ref"]), username, volume, days, panel_id=panel_id)
    if not ok:
        await send_rich(bot, 
            ADMIN_ID,
            f"⚠️ سفارش «بساز سرویس خودت» (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از پنل {panel_label} ارسال "
            f"شود ولی ساخت سرویس ناموفق بود:\n{msg}\n"
            f"🔑 Template ID ارسال‌شده: {template_id}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی این سفارش استفاده کن. "
            "اگه خطا NOT_FOUND بود، از «🧩 نگاشت پیش‌فرض بساز سرویس خودت» دوباره یه بسته‌ی معتبر از روی همان پنل فعلی انتخاب کن.",
        )
        return False

    link, slug = vpn_panel.extract_link_and_username(data)
    snapshot = {"name": custom_name or "سرویس سفارشی", "volume_gb": volume, "days": days}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "slug": slug, "snapshot": snapshot, "panel_id": panel_id}

    if not link:
        await send_rich(bot, 
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(marzban_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_marzban_manual_link)
        await send_rich(bot, 
            ADMIN_ID,
            "⚠️ سرویس در پنل مرزبان ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل (فقط برای رفع اشکال ادمین) با ارسال واقعی کانفیگ برای مشتری
    # کاملاً مستقل است؛ به‌جای پشت‌سرهم، همزمان اجرا می‌شوند تا مشتری منتظر این پیام‌ای
    # فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(send_rich(bot, 
            ADMIN_ID, f"📨 پاسخ پنل {panel_label} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
            parse_mode="HTML",
        )),
        asyncio.ensure_future(_deliver_marzban_link(bot, ctx, link)),
    )
    return True


async def _fetch_plan_choices() -> tuple[list[dict], str]:
    """لیست تمپلیت‌های کاربر (User Template) پنل مرزبان را می‌گیرد و به همان
    شکل choices قبلی (idx/slug/name/label) تبدیل می‌کند؛ اینجا slug همان
    شناسه‌ی عددی تمپلیت (template_id) به‌صورت رشته است."""
    ok, data, msg = await vpn_panel.get_templates()
    if not ok:
        return [], msg
    items = data if isinstance(data, list) else []
    choices = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        template_id = it.get("id")
        if template_id is None:
            continue
        name = it.get("name") or f"template-{template_id}"
        label = f"📦 {name} (ID: {template_id})"
        if len(label) > 60:
            label = label[:57] + "..."
        choices.append({"idx": i, "slug": str(template_id), "name": name, "label": label})
    if not choices:
        return [], "هیچ تمپلیتی در پنل مرزبان پیدا نشد. ابتدا از پنل مرزبان یک «User Template» بسازید."
    return choices, "موفق"


# ---------------------------------------------------------------------------
# 📋 صفحه‌ی اصلی
# ---------------------------------------------------------------------------
def _marzban_hub_warn() -> str:
    """هشدار وضعیت اتصال — بر اساس پنل واقعاً فعال (مرزبان یا پاسارگارد)، نه
    فقط مرزبان؛ چون این هاب برای هرکدام از دو پنل که فعال باشد کار می‌کند."""
    panel = vpn_panel.active_panel()
    if panel:
        label = vpn_panel.PANEL_LABELS.get(panel, panel)
        return f"\n\n✅ پنل فعال فعلی: {label}"
    return "\n\n⚠️ هیچ پنل VPNی وصل نیست. اول از «🔗 اتصال پنل مرزبان» یا «🛡️ اتصال پنل پاسارگارد» یکی رو وصل کن."


@router.callback_query(F.data == "admin_marzban")
async def open_marzban_menu(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    warn = _marzban_hub_warn()
    await edit_rich(callback.message, f"📦 نگاشت پلن‌ها به پنل فعال{warn}", reply_markup=admin_marzban_menu())
    await callback.answer()


@router.message(F.text == "📦 نگاشت پلن‌ها به پنل فعال")
async def menu_admin_marzban(message: types.Message):
    if not _admin_perm(message.from_user.id, "vpn_panel"):
        return
    warn = _marzban_hub_warn()
    await answer_rich(message, f"📦 نگاشت پلن‌ها به پنل فعال{warn}", reply_markup=admin_marzban_menu())


@router.callback_query(F.data == "marzban_test")
async def marzban_test(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال تست اتصال...")
    ok, data, msg = await vpn_panel.test_connection()
    if not ok:
        await answer_rich(callback.message, f"❌ اتصال ناموفق: {msg}", reply_markup=marzban_back_keyboard())
        return
    await answer_rich(callback.message, 
        f"✅ اتصال برقرار است.\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML", reply_markup=marzban_back_keyboard(),
    )


@router.callback_query(F.data == "marzban_traffic")
async def marzban_traffic(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت اطلاعات ترافیک...")
    ok, data, msg = await vpn_panel.get_system_stats()
    if not ok:
        await answer_rich(callback.message, f"❌ خطا: {msg}", reply_markup=marzban_back_keyboard())
        return
    await answer_rich(callback.message, 
        f"🚦 ترافیک/مصرف برند:\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML", reply_markup=marzban_back_keyboard(),
    )


@router.callback_query(F.data == "marzban_plans")
async def marzban_plans(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت بسته‌ها...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    panel_label = vpn_panel.panel_label(vpn_panel.active_panel())
    lines = [f"• {c['name']}\n  slug: <code>{html.escape(c['slug'])}</code>" for c in choices]
    text = f"📦 بسته‌های فعال برند در پنل {panel_label}:\n\n" + "\n\n".join(lines)
    text += (
        "\n\n💡 این‌که هر بسته از کدام ترافیک (اقتصادی/CDN و ...) تغذیه می‌شود در خود "
        "پنل مرزبان هنگام ساخت بسته مشخص شده؛ اینجا فقط برای انتخاب/نگاشت نمایش داده می‌شود."
    )
    await answer_rich(callback.message, text[:4000], parse_mode="HTML", reply_markup=marzban_back_keyboard())


# ---------------------------------------------------------------------------
# 🗂 نگاشت دسته‌بندی VIP/Gaming → planSlug مرزبان
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "marzban_map_vip")
async def marzban_map_vip(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cats = db.get_vip_categories()
    if not cats:
        await callback.answer("هنوز هیچ دسته‌بندی VIP‌ای ساخته نشده.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "🗂 یک دسته‌بندی VIP رو انتخاب کن تا پلن‌های داخلش رو ببینی و برای هرکدوم "
        "جداگانه بسته‌ی متناظرش در مرزبان رو مشخص کنی (چون هر پلن حجم/مدت خودش رو داره):",
        reply_markup=marzban_map_vip_category_pick_keyboard(cats),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapvipcat_"))
async def marzban_map_vip_cat_pick(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cat_id = int(callback.data.replace("marzbanmapvipcat_", ""))
    plans = db.get_vip_plans(cat_id)
    if not plans:
        await callback.answer("این دسته‌بندی هنوز هیچ پلنی نداره.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "یک پلن رو انتخاب کن تا بسته‌ی متناظرش در مرزبان رو مشخص کنی "
        "(هر پلن با حجم/مدت خودش باید به بسته‌ی درست وصل بشه):",
        reply_markup=marzban_map_vip_plans_keyboard(cat_id, plans),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapvipplan_"))
async def marzban_map_vip_plan_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    raw = callback.data.replace("marzbanmapvipplan_", "")
    cat_id_str, _, plan_id_str = raw.partition("_")
    if not cat_id_str.isdigit() or not plan_id_str.isdigit():
        await callback.answer("❌ خطای داخلی.", show_alert=True)
        return

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    # از scope اختصاصی "vip_plan" با scope_id = id دقیق همین پلن استفاده می‌کنیم
    # (نه category_id) تا هر پلن، مستقل از بقیه‌ی پلن‌های همون دسته، بسته‌ی
    # متناظر خودش رو داشته باشه.
    await state.update_data(marzban_map_scope="vip_plan", marzban_map_scope_id=int(plan_id_str),
                             marzban_map_choices=choices)
    await answer_rich(callback.message, 
        "یک بسته‌ی مرزبان رو انتخاب کن تا به این پلن مشخص وصل بشه:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapplan"),
    )


@router.callback_query(F.data == "marzban_map_custom_build")
async def marzban_map_custom_build(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    await state.update_data(marzban_map_choices=choices)
    await answer_rich(callback.message, 
        "🧩 این بسته، پیش‌فرضِ همه‌ی سفارش‌های «بساز سرویس خودت» که با کیف‌پول یا "
        "پرداخت آنلاین پرداخت می‌شوند خواهد بود (چون این سفارش‌ها حجم/مدت متغیر دارند "
        "و دسته‌بندی ثابتی مثل VIP ندارند). یک بسته انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapcustombuild"),
    )


@router.callback_query(F.data.startswith("marzbanmapcustombuild_"))
async def marzban_map_custom_build_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapcustombuild_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map("custom_build", 0, chosen["slug"], chosen["name"])
    await edit_rich(callback.message, 
        f"✅ ذخیره شد: از این پس سفارش‌های «بساز سرویس خودت» (کیف‌پول/آنلاین) با بسته‌ی "
        f"«{chosen['name']}» (slug: {chosen['slug']}) در پنل مرزبان ساخته می‌شوند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "marzban_map_free_test")
async def marzban_map_free_test(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return
    await state.update_data(marzban_map_choices=choices)
    await answer_rich(callback.message, 
        "🧪 این بسته برای همه‌ی سفارش‌های «تست رایگان» (۱ گیگ/۷ روزه) استفاده خواهد شد. "
        "یه بسته‌ی کوچیک و مناسب برای تست انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapfreetest"),
    )


@router.callback_query(F.data.startswith("marzbanmapfreetest_"))
async def marzban_map_free_test_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapfreetest_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map("free_test", 0, chosen["slug"], chosen["name"])
    await edit_rich(callback.message, 
        f"✅ ذخیره شد: از این پس سفارش‌های «تست رایگان» با بسته‌ی "
        f"«{chosen['name']}» (slug: {chosen['slug']}) در پنل مرزبان ساخته می‌شوند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("marzbanmapcat_"))
async def marzban_map_cat_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    raw = callback.data.replace("marzbanmapcat_", "", 1)
    scope, _, cat_id_str = raw.rpartition("_")
    if not scope or not cat_id_str.isdigit():
        await callback.answer("❌ خطای داخلی.", show_alert=True)
        return
    cat_id = int(cat_id_str)

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    await state.update_data(marzban_map_scope=scope, marzban_map_scope_id=cat_id, marzban_map_choices=choices)
    await answer_rich(callback.message, 
        "یک بسته‌ی مرزبان رو انتخاب کن تا به این دسته‌بندی وصل بشه:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbanmapplan"),
    )


@router.callback_query(F.data.startswith("marzbanmapplan_"))
async def marzban_map_plan_set(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    idx = int(callback.data.replace("marzbanmapplan_", ""))
    data = await state.get_data()
    choices = data.get("marzban_map_choices") or []
    scope = data.get("marzban_map_scope")
    scope_id = data.get("marzban_map_scope_id")
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen or not scope or scope_id is None:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_marzban_plan_map(scope, int(scope_id), chosen["slug"], chosen["name"])
    await edit_rich(callback.message, 
        f"✅ ذخیره شد: از این پس این دسته‌بندی از بسته‌ی «{chosen['name']}» (slug: {chosen['slug']}) "
        "در پنل مرزبان استفاده می‌کند.",
        reply_markup=admin_marzban_menu(),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 📤 ارسال خودکار بعد از تأیید رسید (فقط VIP — بر اساس دسته‌بندی نگاشت‌شده.
# Gaming عمداً پشتیبانی نمی‌شود؛ همیشه ۱۰۰٪ دستی می‌ماند)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("marzbansend|"))
async def marzban_send_service(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 فیکس: این دکمه دقیقاً کنار دکمه‌ی دستی «🚀 ارسال کانفیگ VIP (QR) — دستی» (sendvip_)
    # در همان پیام نمایش داده می‌شود و آن دکمه فقط با _is_admin چک می‌شد؛ قبلاً اینجا مجوز
    # جداگانه‌ای "vpn_panel" چک می‌شد و برای ادمین فرعی‌ای که فقط مجوز پیگیری سفارشات (requests)
    # داشت و مجوز جداگانه‌ی تنظیمات پنل VPN را نداشت، این دکمه بی‌پاسخ می‌ماند. حالا
    # مثل همان دکمه‌ی دستی، فقط _is_admin (ادمین اصلی یا هر ادمین فرعی) چک می‌شود.
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    if is_duplicate_action(f"marzbansend_{callback.data}"):
        await callback.answer("⚠️ این عملیات چند لحظه پیش انجام شد.", show_alert=True)
        return

    _, uid, plan_key, order_id_str = callback.data.split("|")
    order_id = int(order_id_str) if order_id_str and order_id_str != "0" else None

    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if not plan or not user:
        await callback.answer("❌ کاربر یا پلن یافت نشد.", show_alert=True)
        return

    mapping = db.get_panel_map_for_plan_key(plan_key)
    if not mapping or not mapping.get("panel_id") or mapping.get("remote_ref") is None:
        await callback.answer("❌ برای این پلن هنوز Template پنل نگاشت نشده.", show_alert=True)
        return

    await callback.answer("⏳ در حال ساخت سرویس در پنل نگاشت‌شده...")
    panel_id = int(mapping["panel_id"])
    panel = vpn_panel.get_panel(panel_id)
    if not panel:
        await callback.answer("❌ پنل نگاشت‌شده دیگر فعال/موجود نیست.", show_alert=True); return
    username = _generate_service_username()
    # 🆕 فیکس: حجم/مدت دقیقاً از روی خود پلن (plan['volume_gb']/plan['days']) گرفته می‌شود، نه از روی تمپلیت نگاشت‌شده.
    # 🆕 فیکس HWID Limit: سقف کاربر همزمان خود پلن (plan['user_limit']) همراه با ساخت سرویس به پنل فرستاده می‌شود.
    ok, data, msg = await vpn_panel.create_user_custom(
        int(mapping["remote_ref"]), username, plan.get("volume_gb"), plan.get("days"),
        device_limit=plan.get("user_limit"), panel_id=panel_id,
    )
    if not ok:
        await answer_rich(callback.message, 
            f"❌ ساخت سرویس در پنل مرزبان ناموفق بود: {msg}\n\n"
            f"🔑 planSlug ارسال‌شده: <code>{html.escape(str(mapping.get('remote_ref')))}</code> "
            f"(نگاشت‌شده به‌عنوان «{html.escape(mapping.get('remote_name') or '')}»)\n"
            "اگه این پیام SERVICE_NOT_FOUND/NOT_FOUND می‌ده، احتمالاً این بسته توی پنل مرزبان حذف/rename شده؛ "
            "از «📦 مشاهده بسته‌های مرزبان» یک بار slug فعلی رو چک کن و در صورت نیاز دوباره نگاشت کن.",
            parse_mode="HTML",
        )
        return

    link, slug = vpn_panel.extract_link_and_username(data)
    actual_volume_gb = _actual_volume_gb_from_panel_response(data, plan.get("volume_gb"))
    snapshot = {"name": plan.get("name"), "volume_gb": actual_volume_gb, "days": plan.get("days"), "user_limit": plan.get("user_limit")}
    ctx = {"uid": uid, "plan_key": plan_key, "order_id": order_id, "order_kind": "plan",
           "slug": slug, "snapshot": snapshot, "panel_id": panel_id}

    if not link:
        await answer_rich(callback.message, f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")
        await state.update_data(marzban_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_marzban_manual_link)
        await answer_rich(callback.message, 
            "⚠️ سرویس در پنل مرزبان ساخته شد ولی نتونستم لینک ساب رو خودکار پیدا کنم.\n"
            "لطفاً لینک ساب رو از پاسخ بالا کپی و همینجا ارسال کن:"
        )
        return

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل را همزمان با ارسال واقعی کانفیگ برای مشتری اجرا می‌کنیم
    # تا مشتری منتظر پیام فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(callback.message.answer(f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")),
        asyncio.ensure_future(_deliver_marzban_link(callback.bot, ctx, link)),
    )


@router.callback_query(F.data.startswith("marzbancustom_"))
async def marzban_custom_start(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 فیکس: همین باگ که در marzban_send_service بالا توضیح داده شد — دکمه‌ی «🚀 ساخت خودکار از پنل فعال» کنار دکمه‌ی دستی «📤
    # شروع ارسال کانفیگ — دستی» (sendcustomorder_) نمایش داده می‌شود و هر دو برای همان ادمین‌های فرعی
    # مسئول پیگیری سفارشات (مجوز requests) فرستاده می‌شود. قبلاً این دکمه مجوز "vpn_panel" می‌خواست
    # (جداگانه از مجوز دکمه‌ی دستی کنارش)، برای همین برای ادمین فرعیای که فقط مجوز پیگیری
    # سفارشات داشت، این دکمه کاملاً بی‌پاسخ می‌ماند (بدون هیچ پیام/پاسخی به کاربر)؛ از نظر کاربر
    # دقیقاً همان «این دکمه کار نمی‌کند» بود. حالا مثل دکمه‌ی دستی، فقط _is_admin چک می‌شود.
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.replace("marzbancustom_", ""))
    order = db.get_custom_order(order_id)
    if order is None:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return

    await callback.answer("⏳ در حال دریافت بسته‌های مرزبان...")
    choices, msg = await _fetch_plan_choices()
    if not choices:
        await answer_rich(callback.message, f"❌ {msg}", reply_markup=marzban_back_keyboard())
        return

    await state.update_data(marzban_custom_order_id=order_id, marzban_map_choices=choices)
    await answer_rich(callback.message, 
        f"سفارش «بساز سرویس خودت» #{order_id} — {order['volume_gb']} گیگ / {order['days']} روز\n"
        "نزدیک‌ترین بسته‌ی مرزبان رو انتخاب کن:",
        reply_markup=marzban_plan_pick_keyboard(choices, "marzbancustompick"),
    )


@router.callback_query(F.data.startswith("marzbancustompick_"))
async def marzban_custom_pick(callback: types.CallbackQuery, state: FSMContext):
    # 🐛 فیکس: مطابق marzban_custom_start بالا — فقط _is_admin چک می‌شود.
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    idx = int(callback.data.replace("marzbancustompick_", ""))
    data_state = await state.get_data()
    choices = data_state.get("marzban_map_choices") or []
    order_id = data_state.get("marzban_custom_order_id")
    chosen = next((c for c in choices if c["idx"] == idx), None)
    order = db.get_custom_order(order_id) if order_id else None
    if not chosen or not order:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره تلاش کن.", show_alert=True)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    await callback.answer("⏳ در حال ساخت سرویس در پنل پاسارگارد...")
    panel = vpn_panel.get_panel()
    if not panel:
        await callback.answer("❌ هیچ پنل پاسارگارد فعالی ثبت نشده.", show_alert=True); return
    panel_id = int(panel["id"])
    username = _generate_service_username()
    # 🆕 فیکس: حجم/مدت دقیقاً از روی خود سفارش (order['volume_gb']/order['days']) گرفته می‌شود؛ تمپلیت انتخاب‌شده فقط برای تعیین پروتکل/استخر استفاده می‌شود.
    ok, data, msg = await vpn_panel.create_user_custom(int(chosen["slug"]), username, order["volume_gb"], order["days"], panel_id=panel_id)
    if not ok:
        await answer_rich(callback.message, 
            f"❌ ساخت سرویس در پنل مرزبان ناموفق بود: {msg}\n"
            f"🔑 planSlug ارسال‌شده: <code>{html.escape(chosen['slug'])}</code>",
            parse_mode="HTML",
        )
        return

    link, slug = vpn_panel.extract_link_and_username(data)
    actual_volume_gb = _actual_volume_gb_from_panel_response(data, order["volume_gb"])
    snapshot = {"name": order.get("custom_name") or "سرویس سفارشی",
                "volume_gb": actual_volume_gb, "days": order["days"]}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "slug": slug, "snapshot": snapshot, "panel_id": panel_id}

    if not link:
        await answer_rich(callback.message, f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")
        await state.update_data(marzban_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_marzban_manual_link)
        await answer_rich(callback.message, 
            "⚠️ سرویس در پنل مرزبان ساخته شد ولی نتونستم لینک ساب رو خودکار پیدا کنم.\n"
            "لطفاً لینک ساب رو از پاسخ بالا کپی و همینجا ارسال کن:"
        )
        return

    # 🆕 فیکس سرعت: دامپ خام پاسخ پنل را همزمان با ارسال واقعی کانفیگ برای مشتری اجرا می‌کنیم
    # تا مشتری منتظر پیام فقط-ادمینی نماند.
    await asyncio.gather(
        asyncio.ensure_future(callback.message.answer(f"📨 پاسخ پنل مرزبان:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")),
        asyncio.ensure_future(_deliver_marzban_link(callback.bot, ctx, link)),
    )


@router.message(AdminStates.waiting_marzban_manual_link)
async def marzban_manual_link_received(message: types.Message, state: FSMContext):
    link = (message.text or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        await answer_rich(message, "❌ این یک لینک معتبر نیست؛ لطفاً لینک ساب رو با http یا https ارسال کن:")
        return
    data = await state.get_data()
    ctx = data.get("marzban_pending_ctx")
    if not ctx:
        await answer_rich(message, "❌ مشکلی پیش آمد؛ لطفاً از ابتدا دکمه‌ی ارسال خودکار رو دوباره بزن.")
        await state.clear()
        return
    await _deliver_marzban_link(message.bot, ctx, link)
    await state.clear()


async def _deliver_marzban_link(bot, ctx: dict, link: str):
    """سرویس ساخته‌شده از طریق مرزبان را در دیتابیس ذخیره و برای کاربر ارسال می‌کند
    (دقیقاً همان قالب/تجربه‌ی ارسال دستی، فقط بدون نیاز به آپلود دستی عکس/لینک).
    توجه: به‌جای گرفتن یک پیام از چت ادمین، مستقیماً bot می‌گیرد و پیام‌های
    وضعیت را با send_message به ADMIN_ID می‌فرستد — چون این تابع هم از داخل
    یک callback تعاملی ادمین صدا زده می‌شود و هم از مسیر کاملاً خودکار بعد از
    پرداخت کیف‌پول/آنلاین (که اصلاً در چت ادمین اتفاق نمی‌افتد)."""
    uid = ctx["uid"]
    plan_key = ctx.get("plan_key")
    order_id = ctx.get("order_id")
    order_kind = ctx.get("order_kind")
    slug = ctx.get("slug")
    snap = ctx.get("snapshot") or {}

    user = db.get_user(uid)
    if user is None:
        await send_rich(bot, ADMIN_ID, "❌ کاربر یافت نشد؛ سرویس در پنل مرزبان ساخته شد ولی ارسال نشد.")
        return

    name = snap.get("username") or snap.get("name") or "کاربر"
    volume_gb = snap.get("volume_gb")
    days = snap.get("days")
    volume_text = _format_volume_gb_label(volume_gb) if volume_gb is not None else "نامشخص"
    days_text = f"{days} روز" if days else "نامحدود"
    user_limit = snap.get("user_limit")
    expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d") if days else None

    delivery_label = _delivery_service_label(name, volume_gb, days, user_limit, plan_key)
    is_test_delivery = plan_key == FREE_TEST_PLAN_KEY
    delivery_text_key = "service_delivery_test_text" if is_test_delivery else "service_delivery_text"
    caption = t(delivery_text_key, service_label=delivery_label, link=link)

    encrypted = crypto.encrypt_config(link)
    plan_name = f"{name} | {volume_text} | {days_text}"
    config_type = db.plan_type(plan_key) if plan_key else "vip"
    if config_type == "test":
        config_type = "vip"

    config_id = db.add_config(
        user["id"], plan_name, encrypted, expiry=expiry_date,
        config_type=config_type, service_id=slug, source="pasargad", panel_id=(ctx.get("panel_id") if isinstance(ctx, dict) else None),
        category_id=((db.get_vip_plan(plan_key) or {}).get("category_id") if plan_key else None),
        plan_key=plan_key,
    )

    if order_kind == "plan" and order_id:
        db.set_order_status(order_id, "fulfilled")
    elif order_kind == "custom" and order_id:
        db.set_custom_order_status(order_id, "fulfilled")

    async def _send_to_customer():
        # منوی پایینی کاربر نباید هنگام تحویل سرویس حذف شود.
        db.set_keyboard_hidden(int(uid), False)
        if qrcode:
            photo = types.BufferedInputFile(_make_qr_bytes(link), filename="qr.png")
            sent = await send_photo_rich(bot, 
                int(uid), photo, caption=caption, reply_markup=config_delivery_keyboard(is_test=is_test_delivery)
            )
            await send_rich(bot, int(uid), "⬇️ منوی اصلی در پایین صفحه قابل دسترسی است.", reply_markup=main_reply_keyboard())
            return sent.photo[-1].file_id if sent.photo else None
        await send_rich(bot, 
            int(uid), caption, reply_markup=config_delivery_keyboard(is_test=is_test_delivery)
        )
        await send_rich(bot, int(uid), "⬇️ منوی اصلی در پایین صفحه قابل دسترسی است.", reply_markup=main_reply_keyboard())
        return None

    # 🆕 فیکس سرعت: قبلاً ارسال کانفیگ به مشتری، پیام تأیید به ادمین، و ثبت لاگ سفارش در کانال
    # «اعتماد» (که خودش شامل یک get_chat + یک send_message جداست) کاملاً پشت‌سرهم ارسال می‌شدند؛ همین زنجیره‌ی رفت‌وبرگشت‌های متوالی، اصلی‌ترین عامل کند بودن
    # (۱۰-۱۵ ثانیه) کل فرآیند «ساخت و ارسال سرویس» بود، نه فقط ارتباط با پنل. چون این پیام‌ها کاملاً
    # مستقل از هم‌اند، حالا همزمان (concurrent) اجرا می‌شوند تا زمانشان روی هم جمع نشود.
    send_result, log_result = await asyncio.gather(
        _send_to_customer(),
        _log_fulfilled_order(
            bot, user,
            plan_order_id=order_id if order_kind == "plan" else None,
            custom_order_id=order_id if order_kind == "custom" else None,
            service_id=slug, service_name=name,
            package_text=f"{volume_text} | {days_text}", expiry_text=expiry_date or "نامحدود",
        ),
        return_exceptions=True,
    )

    if isinstance(send_result, Exception):
        logger.exception("ارسال کانفیگ به کاربر ناموفق بود", exc_info=send_result)
        await send_rich(bot, ADMIN_ID, f"⚠️ سرویس ساخته و ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {send_result}")
    else:
        if send_result:
            db.set_config_qr(config_id, send_result)
        order_obj = db.get_order(order_id) if order_id else None
        if plan_key and db.get_effective_plan(plan_key):
            admin_package_name = db.get_effective_plan(plan_key).get("name")
        else:
            admin_package_name = f"{volume_text} | {days_text}"
        admin_amount = order_obj.get("price", 0) if order_obj else 0
        admin_summary = alerts.admin_delivery_summary(user, name, admin_package_name, admin_amount)
        await send_rich(bot, ADMIN_ID, admin_summary)

    if isinstance(log_result, Exception):
        logger.exception("ثبت لاگ سفارش در کانال اعتماد ناموفق بود", exc_info=log_result)


def _admin_renewal_settings_for_config(cfg: dict) -> dict:
    """تنظیمات مؤثر تمدید سرویس؛ ابتدا تنظیمات اختصاصی پلن و سپس دسته را در نظر می‌گیرد."""
    try: cid=int(cfg.get("category_id") or 0)
    except Exception: cid=0
    plan_key=str(cfg.get("plan_key") or "").strip()
    if not cid and plan_key:
        plan=db.get_vip_plan(plan_key); cid=int(plan.get("category_id") or 0) if plan else 0
    if not cid:
        try:
            raw_name=str(cfg.get("plan") or "").split("|",1)[0].strip(); cur=db.get_connection().cursor(); cur.execute("SELECT category_id FROM vip_plans WHERE name = ? LIMIT 1",(raw_name,)); row=cur.fetchone(); cid=int(row[0] if row else 0)
        except Exception: cid=0
    defaults={"mode":"day","price_day":0,"price_gb":5500,"min_day":1,"max_day":0,"min_gb":1,"max_gb":0,"day_options":"30,60,90","gb_options":"10,20,50"}
    if cid:
        try: defaults=bot_info.get_renewal_plan_settings(plan_key,cid) if plan_key else bot_info.get_renewal_settings(cid)
        except Exception: pass
    return defaults


# ---------------------------------------------------------------------------
# 🔁 مدیریت سرویس‌های ساخته‌شده از طریق مرزبان (از صفحه‌ی جزئیات سرویس)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("marzbanrenew_"))
async def marzban_renew_start(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbanrenew_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل VPN ساخته نشده.", show_alert=True)
        return

    # همان mode دسته‌بندی فروشگاه روی تمدید ادمین هم اعمال می‌شود.
    settings = _admin_renewal_settings_for_config(cfg)
    mode = settings.get("mode", "day")
    await callback.answer()
    await state.update_data(marzban_renew_cfg_id=cfg_id, marzban_renew_mode=mode, marzban_renew_settings=settings)
    if mode == "day":
        await state.update_data(marzban_renew_volume_gb=0)
        await state.set_state(AdminStates.waiting_marzban_renew_days)
        prompt = "تعداد روز اضافه را وارد کن (۰ = نامحدود):"
    elif mode == "gb":
        await state.set_state(AdminStates.waiting_marzban_renew_volume)
        prompt = "حجم اضافه را به گیگابایت وارد کن (۰ = نامحدود):"
    else:
        # حالت ترکیبی دقیقاً مثل تمدید مشتری: ابتدا روز، سپس حجم.
        await state.update_data(marzban_renew_volume_gb=0)
        await state.set_state(AdminStates.waiting_marzban_renew_days)
        prompt = "ابتدا تعداد روز اضافه را وارد کن (۰ = نامحدود):"
    await answer_rich(callback.message, prompt, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"marzbanrenewback_{cfg_id}", style="danger")]]))


@router.message(AdminStates.waiting_marzban_renew_volume)
async def marzban_renew_volume_received(message: types.Message, state: FSMContext):
    volume_gb = parse_int_in_range((message.text or "").strip(), 0, 100000)
    if volume_gb is None:
        await answer_rich(message, "❌ یک عدد معتبر برای حجم (گیگابایت) وارد کن (۰ = نامحدود):")
        return
    settings = (await state.get_data()).get("marzban_renew_settings") or {}
    if volume_gb != 0 and (volume_gb < int(settings.get("min_gb") or 1) or (settings.get("max_gb") and volume_gb > int(settings["max_gb"]))):
        await answer_rich(message, f"❌ حجم باید بین {settings.get('min_gb', 1)} و {settings.get('max_gb') or 'نامحدود'} گیگ باشد.")
        return
    await state.update_data(marzban_renew_volume_gb=volume_gb)
    data = await state.get_data()
    if data.get("marzban_renew_mode") == "gb":
        await _apply_admin_panel_renew(message, state, volume_gb, 0)
        return
    if data.get("marzban_renew_mode") == "both" and data.get("marzban_renew_days") is not None:
        await _apply_admin_panel_renew(message, state, volume_gb, int(data.get("marzban_renew_days") or 0))
        return
    # حالت ترکیبی اگر از مسیر دیگری وارد شود، ابتدا روز را می‌گیرد.
    await state.set_state(AdminStates.waiting_marzban_renew_days)
    await answer_rich(message, "تعداد روز اضافه را وارد کن (۰ = نامحدود):", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"marzbanrenewback_{data.get('marzban_renew_cfg_id')}")]]))


async def _apply_admin_panel_renew(message: types.Message, state: FSMContext, volume_gb, days):
    data_state = await state.get_data()
    cfg_id = data_state.get("marzban_renew_cfg_id")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await answer_rich(message, "❌ این سرویس دیگر یافت نشد؛ دوباره از ابتدا تلاش کن.")
        await state.clear()
        return
    panel = db.get_vpn_panel(cfg["panel_id"])
    if not panel:
        await answer_rich(message, "❌ پنل این سرویس پیدا نشد.")
        await state.clear()
        return
    await answer_rich(message, "⏳ در حال تمدید همان سرویس، بدون Revoke و بدون تغییر لینک...")
    mode = data_state.get("marzban_renew_mode") or "day"
    unlimited_volume = bool(mode in ("gb", "both") and int(volume_gb or 0) == 0)
    unlimited_days = bool(mode in ("day", "both") and int(days or 0) == 0)
    ok, snapshot, msg = await panels.renew_existing_service(
        panel,
        cfg["service_id"],
        volume_gb or 0,
        days or 0,
        unlimited_volume=unlimited_volume,
        unlimited_days=unlimited_days,
    )
    if not ok:
        await answer_rich(message, f"❌ تمدید ناموفق بود: {msg}")
        await state.clear()
        return
    exp = snapshot.get("expire") if isinstance(snapshot, dict) else None
    try:
        expiry = datetime.fromtimestamp(int(exp), tz=TEHRAN_TZ).replace(tzinfo=None).strftime("%Y-%m-%d") if exp else cfg.get("expiry")
    except Exception:
        expiry = cfg.get("expiry")
    db.update_config_expiry(cfg["id"], expiry)
    volume_result = "نامحدود" if unlimited_volume else f"{float(volume_gb or 0):g} گیگ"
    days_result = "نامحدود" if unlimited_days else f"{int(days or 0)} روز"
    await answer_rich(message, f"✅ همان سرویس تمدید شد.\n\n📦 حجم: {volume_result}\n⏳ زمان: {days_result}\n🔗 لینک Subscription و شناسه سرویس تغییر نکرد.")
    await state.clear()


@router.message(AdminStates.waiting_marzban_renew_days)
async def marzban_renew_days_received(message: types.Message, state: FSMContext):
    days = parse_int_in_range((message.text or "").strip(), 0, 100000)
    if days is None:
        await answer_rich(message, "❌ یک عدد معتبر برای تعداد روز وارد کن (۰ = نامحدود):")
        return
    data_state = await state.get_data()
    settings = data_state.get("marzban_renew_settings") or {}
    if days != 0 and (days < int(settings.get("min_day") or 1) or (settings.get("max_day") and days > int(settings["max_day"]))):
        await answer_rich(message, f"❌ زمان باید بین {settings.get('min_day', 1)} و {settings.get('max_day') or 'نامحدود'} روز باشد.")
        return
    if data_state.get("marzban_renew_mode") == "both":
        await state.update_data(marzban_renew_days=days)
        await state.set_state(AdminStates.waiting_marzban_renew_volume)
        await answer_rich(
            message,
            "حالا حجم اضافه را به گیگابایت وارد کن:",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(
                    text="🔙 بازگشت",
                    callback_data=f"marzbanrenewback_{data_state.get('marzban_renew_cfg_id')}",
                )]]
            ),
        )
        return
    volume_gb = data_state.get("marzban_renew_volume_gb") or 0
    await _apply_admin_panel_renew(message, state, volume_gb, days)


@router.callback_query(F.data.startswith("marzbanrenewback_"))
async def marzban_renew_back(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "vpn_panel"): return
    cfg_id=int(callback.data.replace("marzbanrenewback_","")); await state.clear()
    cfg=db.get_config_by_id(cfg_id)
    if not cfg: await callback.answer("❌ سرویس پیدا نشد.",show_alert=True); return
    from handlers.admin import _render_service_detail
    await _render_service_detail(callback,cfg_id); await callback.answer()

@router.callback_query(F.data.startswith("marzbandisable_"))
async def marzban_disable(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbandisable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل مرزبان ساخته نشده.", show_alert=True)
        return
    ok, data, msg = await vpn_panel.disable_user(cfg["service_id"])
    if ok:
        db.set_config_disabled(cfg_id, True)
    await callback.answer("✅ در پنل مرزبان غیرفعال شد." if ok else f"❌ {msg}", show_alert=True)


@router.callback_query(F.data.startswith("marzbanenable_"))
async def marzban_enable(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("marzbanenable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل مرزبان ساخته نشده.", show_alert=True)
        return
    ok, data, msg = await vpn_panel.enable_user(cfg["service_id"])
    if ok:
        db.set_config_disabled(cfg_id, False)
    await callback.answer("✅ در پنل مرزبان فعال شد." if ok else f"❌ {msg}", show_alert=True)


@router.callback_query(F.data.startswith("svcrevokesub_"))
async def marzban_revoke_sub(callback: types.CallbackQuery):
    """🆕 برای ادمین: لینک ساب فعلی سرویس را باطل می‌کند و یک لینک کاملاً جدید از پنل می‌سازد (برای وقتی لینک قبلی لو رفته یا نیاز به تعویض دارد)."""
    if not _admin_perm(callback.from_user.id, "vpn_panel"):
        return
    cfg_id = int(callback.data.replace("svcrevokesub_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از پنل VPN ساخته نشده.", show_alert=True)
        return
    await callback.answer("⏳ در حال ساخت لینک ساب جدید...")
    ok, data, msg = await vpn_panel.revoke_sub(cfg["service_id"])
    if not ok:
        await answer_rich(callback.message, f"❌ ساخت لینک ساب جدید ناموفق بود: {msg}")
        return
    link, _slug = vpn_panel.extract_link_and_username(data)
    if not link:
        await answer_rich(callback.message, "⚠️ لینک ساب جدید در پاسخ پنل پیدا نشد.")
        return
    db.update_config_link(cfg_id, crypto.encrypt_config(link))
    await answer_rich(callback.message, 
        "✅ لینک ساب جدید ساخته و ذخیره شد. لینک قبلی دیگر کار نمی‌کند؛ اگر لازم است به کاربر هم اطلاع بده."
    )
