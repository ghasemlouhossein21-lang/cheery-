"""
handlers/admin.py
پنل کامل ادمین: آمار، لیست کاربران، جستجوی حرفه‌ای، شارژ کیف پول، ارسال
کانفیگ، تأیید/رد پرداخت کارت‌به‌کارت خرید سرویس، مدیریت تخفیف (ساخت گام‌به‌گام)،
مدیریت دعوت‌ها، پیام همگانی، بکاپ.

تمام handlerهای این فایل فقط برای ADMIN_ID فعال هستند.
"""

from datetime import datetime, timedelta
import asyncio
import html
import json
import logging
import os
import re

from aiogram import Router, F, types, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

import database as db
import crypto
import alerts
from subscription import extract_meta, days_remaining, format_bytes, usage_bar, fetch_subscription_info, format_expire, enrich_configs_with_subscription_names, get_live_service_status
from utils import parse_int_in_range, is_duplicate_action, now_tehran_naive, TEHRAN_TZ, STICKER_SECTION_LABELS, STICKER_FILES, STICKERS_DIR, invalidate_section_sticker_cache, send_notification_sticker, clean_numeric_id, TELEGRAM_TEXT_LIMIT, truncate_for_telegram, is_message_too_long_error, serialize_message_entities, message_entities_from_dicts, telegram_utf16_length
from states import AdminStates, UserStates
import bot_info
import payments
import marzban
import pasargad
import vpn_panel
import panels
from config import (
    ADMIN_ID,
    DATABASE_PATH,
    AGENCY_VIP_DISCOUNT_PERCENT,
    FREE_TEST_PLAN_KEY,
    MARZBAN_ENABLED,
    PASARGAD_ENABLED,
)
from keyboards import (
    InlineKeyboardButton,
    admin_panel_menu,
    admin_reply_keyboard,
    admin_back_button,
    admin_discount_menu,
    admin_user_actions_keyboard,
    admin_delete_user_confirm_keyboard,
    admin_purchase_notify_keyboard,
    admin_userlist_menu,
    config_delivery_keyboard,
    main_reply_keyboard,
    admin_services_list_keyboard,
    admin_service_detail_keyboard,
    admin_order_queue_keyboard,
    admin_clear_orders_confirm_keyboard,
    admin_request_queue_menu,
    admin_pending_receipts_keyboard,
    admin_clear_receipts_confirm_keyboard,
    admin_purge_confirm_keyboard,
    admin_userlist_page_keyboard,
    admin_accounting_keyboard,
    discount_type_keyboard,
    discount_plans_select_keyboard,
    discount_plans_edit_keyboard,
    discount_detail_keyboard,
    discount_delete_confirm_keyboard,
    admin_agency_menu,
    admin_agent_row_keyboard,
    admin_agent_actions_keyboard,
    admin_vip_categories_keyboard,
    vip_categories_keyboard,
    admin_vip_category_detail_keyboard,
    admin_vip_plan_detail_keyboard,
    admin_pm_cancel_keyboard,
    admin_referrers_page_keyboard,
    admin_referred_detail_keyboard,
    admin_guides_menu,
    admin_guide_detail_keyboard,
    admin_guide_delete_confirm_keyboard,
    admin_guide_cancel_keyboard,
    admin_error_logs_keyboard,
    admin_error_log_detail_keyboard,
    admin_error_logs_clear_confirm_keyboard,
    admin_stickers_menu,
    admin_sticker_detail_keyboard,
    admin_sticker_cancel_keyboard,
    admin_botinfo_menu,
    admin_botinfo_field_keyboard,
    admin_botinfo_channels_menu,
    admin_renewal_categories_menu,
    admin_renewal_category_menu,
    admin_renewal_mode_menu,
    admin_renewal_scope_menu,
    admin_renewal_plan_menu,
    admin_manage_admins_keyboard,
    admin_permissions_keyboard,
)

plan_type = db.plan_type  # نسخه‌ی DB-aware (دسته‌بندی‌های VIP را هم می‌شناسد)

from text_catalog import TEXT_CATEGORIES, TEXTS, CATEGORY_BY_KEY, text as user_text, refresh as refresh_user_text
from utils import send_photo_rich, edit_caption_rich
from utils import send_rich
from utils import answer_rich, edit_rich, _repair_custom_emoji_entities, _sanitize_entities_for_text

router = Router(name="admin")
logger = logging.getLogger(__name__)


def _permission_for_message_text(text: str | None) -> str | None:
    mapping = {
        "📊 آمار": "stats",
        "📥 صف درخواست‌ها": "requests",
        "👥 لیست کاربران": "users",
        "🔍 جستجوی کاربر": "users",
        "🔎 جستجوی کانفیگ": "users",
        "📢 پیام همگانی": "broadcast",
        "🎟 مدیریت تخفیف": "discounts",
        "🤝 نمایندگی (تخفیف VIP)": "agency",
        "🗂 دسته‌بندی‌های VIP": "plans",
        "🛒 خرید اشتراک برای خودم": "plans",
                "🛡️ مدیریت پنل‌های پاسارگارد": "vpn_panel",
                "🤝 مدیریت دعوت‌ها": "referrals",
        "📚 مدیریت راهنما": "guides",
        "🦖 لاگ خطاها": "logs",
        "ℹ️ اطلاعات ربات": "botinfo",
        "🎬 استیکرهای منو": "stickers",
        "💾 بکاپ": "backup",
        "🎁 تنظیم تست رایگان": "settings",
        "🔁 تنظیمات تمدید": "settings",
        "📝 مدیریت متن‌های کاربر": "texts",
        "مدیریت تیکت": "tickets",
        "🔴 خاموش کردن سفارشات": "orders_toggle",
        "🟢 روشن کردن سفارشات": "orders_toggle",
    }
    return mapping.get(text or "")


def _permission_for_callback(data: str | None) -> str | None:
    d = data or ""
    if d in {"admin_back"}:
        return None
    if d.startswith(("approve_", "reject_", "approvepay|", "rejectpay|", "approvecustom_", "rejectcustom_", "clearreceipts")) or d == "admin_pending_receipts":
        return "receipts"
    groups = [
        (("admin_stats",), "stats"),
        (("admin_request_queue", "admin_order_queue", "dismissorder_", "clearorders", "marzbansend|"), "requests"),
        (("admin_tickets", "admintickets_", "adminticket_", "ticketreply_", "ticketclose_", "ticketreopen_", "replyticket_"), "tickets"),
        (("admin_userlist", "userpage_", "useropen_", "accounting_", "admin_search", "admin_config_search", "useractions_", "pm_", "toggleblock_", "deleteuser_", "deleteuserconfirm_", "svcs_", "svcdetail_", "svcdelete_", "svcrestore_", "svcpurge", "svcedit_"), "users"),
        (("admin_broadcast",), "broadcast"),
        (("admin_discount", "discdetail_", "discdelete", "discedit_", "discplan", "new_discount", "disctype_"), "discounts"),
        (("admin_agency", "new_agent", "deleteagent_", "agentopen_", "editagentpercent_"), "agency"),
        (("admin_vip_categories", "admin_buy_subscription", "newvip", "vip"), "plans"),
        (("admin_pasargad_panels", "pp_", "pasargad_"), "vpn_panel"),
        (("admin_botinfo", "botinfo", "channel"), "botinfo"),
        (("admin_stickers", "sticker"), "stickers"),
        (("admin_referrals", "refpage_", "refdetail_"), "referrals"),
        (("admin_guides", "guide"), "guides"),
        (("admin_texts", "textedit_", "textdelete_"), "texts"),
        (("errlog",), "logs"),
        (("admin_backup",), "backup"),
        (("admin_orders_off", "admin_orders_on"), "orders_toggle"),
        (("free_test", "admin_free_test_settings", "admin_renewal_settings", "renewset", "botinforenewal", "botinforenewcat_", "botinforenewgb_", "botinforenewday_"), "settings"),
    ]
    for prefixes, perm in groups:
        if any(d == x or d.startswith(x) for x in prefixes):
            return perm
    return None


class AdminPermissionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or user.id == ADMIN_ID:
            return await handler(event, data)
        if not db.is_sub_admin(str(user.id)):
            return await handler(event, data)
        # مدیریت خود ادمین‌های فرعی فقط برای ادمین اصلی است.
        cb_data = getattr(event, "data", None)
        if cb_data and (cb_data == "admin_manage_admins" or cb_data.startswith("subadm")):
            await event.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True)
            return
        perm = _permission_for_callback(cb_data) if cb_data is not None else _permission_for_message_text(getattr(event, "text", None))
        # اگر دکمه‌ای داخل یک بخش مجاز ساخته شده باشد، همان مجوز بخش مادر کافی است.
        if user.id != ADMIN_ID and perm == "receipts" and db.sub_admin_has_permission(str(user.id), "requests"):
            return await handler(event, data)
        if perm and not db.sub_admin_has_permission(str(user.id), perm):
            if cb_data is not None:
                await event.answer("⛔ شما به این قابلیت دسترسی ندارید.", show_alert=True)
            else:
                await event.answer("⛔ شما به این قابلیت دسترسی ندارید.")
            return
        return await handler(event, data)


router.message.middleware(AdminPermissionMiddleware())
router.callback_query.middleware(AdminPermissionMiddleware())


def _is_main_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def _is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID or db.is_sub_admin(str(user_id))

def _admin_perm(user_id: int, permission: str) -> bool:
    return user_id == ADMIN_ID or db.sub_admin_has_permission(str(user_id), permission)

def _current_admin_permissions(user_id: int) -> set[str] | None:
    if user_id == ADMIN_ID:
        return None
    adm = db.get_sub_admin(str(user_id)) or {}
    return set(adm.get("permissions") or [])

def _admin_panel_kb_for(user_id: int):
    return admin_panel_menu(db.is_orders_enabled(), permissions=_current_admin_permissions(user_id), is_main_admin=(user_id == ADMIN_ID))

def _admin_reply_kb_for(user_id: int, orders_enabled: bool | None = None):
    return admin_reply_keyboard(orders_enabled, permissions=_current_admin_permissions(user_id), is_main_admin=(user_id == ADMIN_ID))


async def _notify_main_admin_action(bot, actor, action: str, target: str = "", details: str = ""):
    name = getattr(actor, "full_name", None) or getattr(actor, "first_name", None) or str(getattr(actor, "id", ""))
    actor_id = str(getattr(actor, "id", ""))
    try:
        db.log_admin_action(actor_id, name, action, target, details)
    except Exception:
        logger.exception("admin activity log failed")
    if actor_id != str(ADMIN_ID):
        try:
            await send_rich(bot, ADMIN_ID, f"📝 گزارش عملکرد ادمین فرعی\n\n👤 {name}\n🆔 {actor_id}\n✅ عملیات: {action}\n🎯 مورد: {target or '-'}\nℹ️ جزئیات: {details or '-'}")
        except Exception:
            logger.exception("notify main admin failed")

async def _deny_no_perm(obj, text="⛔ شما به این بخش دسترسی ندارید."):
    if hasattr(obj, "answer"):
        try:
            await obj.answer(text, show_alert=True)
        except TypeError:
            await obj.answer(text)


async def _reply_with_user_actions(target, text: str, uid, is_blocked: bool, *, edit: bool = False):
    """پیام همراه با کیبورد اقدامات کاربر (admin_user_actions_keyboard) را می‌فرستد یا ویرایش می‌کند.
    اگر تلگرام به‌خاطر تنظیمات حریم‌خصوصی محدودتر همان کاربر خاص، دکمه‌ی «رفتن به پیوی کاربر»
    (لینک tg://user) را رد کند (خطای BUTTON_USER_PRIVACY_RESTRICTED)، به‌جای کرش کردن کل پیام،
    همان پیام را بدون این دکمه‌ی خاص دوباره می‌فرستد؛ برای بقیه‌ی کاربران دکمه همچنان نمایش داده می‌شود."""
    try:
        kb = admin_user_actions_keyboard(uid, is_blocked)
        if edit:
            await edit_rich(target, text, reply_markup=kb)
        else:
            await answer_rich(target, text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "BUTTON_USER_PRIVACY_RESTRICTED" in str(e):
            kb = admin_user_actions_keyboard(uid, is_blocked, show_pm_link=False)
            if edit:
                await edit_rich(target, text, reply_markup=kb)
            else:
                await answer_rich(target, text, reply_markup=kb)
        else:
            raise


_RECEIPTS_QUEUE_MARKER = "🧾 رسیدهای در انتظار تایید"


async def _finish_receipt_message(message: types.Message, note: str, queue_refresh=None):
    """پیام رسید (چه پیام متنی معمولی از ربات کلاسیک، چه پیام عکس+کپشن از
    Mini App که دکمه‌ها مستقیم روی خودِ عکس هستند) را با یک خط نتیجه
    (تأیید/رد) نهایی می‌کند و دکمه‌ها را حذف می‌کند.
    توجه: روی پیام‌های عکس‌دار، edit_text خطا می‌دهد (تلگرام برای عکس‌ها
    caption دارد نه text)؛ برای همین باید edit_caption صدا زده شود.

    queue_refresh: یک تابع async بدون آرگومان. اگر همین دکمه‌ی تأیید/رد از
    داخل پیام «صف درخواست‌ها → رسیدهای در انتظار تایید» زده شده باشد (نه از
    پیام تک‌رسیدیِ اصلی)، به‌جای خالی‌کردن دکمه‌های کل لیست، همان لیست
    رفرش می‌شود تا آیتم‌های دیگرِ هنوز-در-انتظار از بین نروند."""
    text_or_caption = message.caption if message.photo else message.text
    if queue_refresh is not None and text_or_caption and text_or_caption.startswith(_RECEIPTS_QUEUE_MARKER):
        await queue_refresh()
        return
    empty_kb = types.InlineKeyboardMarkup(inline_keyboard=[])
    if message.photo:
        await edit_caption_rich(message, caption=(message.caption or "") + note, reply_markup=empty_kb)
    else:
        await edit_rich(message, (message.text or "") + note, reply_markup=empty_kb)




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



def _english_digits(value) -> str:
    text = str(value if value is not None else "")
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _delivery_service_label(plan_name, volume_gb, days, user_limit, plan_key=None) -> str:
    """نام نمایشی تحویل سرویس؛ برای خرید VIP دقیقاً نام همان پلن خریداری‌شده است."""
    if plan_key == FREE_TEST_PLAN_KEY:
        return "تست رایگان"

    # نام پلن خریداری‌شده منبع حقیقت است؛ نباید از مشخصات فنی دوباره ساخته شود.
    if plan_name and str(plan_name).strip():
        return str(plan_name).strip()

    # فقط برای مسیرهای قدیمی/ارسال دستی که پلن واقعی ندارند fallback نگه می‌داریم.
    volume_text = _format_volume_gb_label(volume_gb) if volume_gb is not None else "نامشخص"
    days_text = f"زمان {days} روزه" if days else "زمان نامحدود"
    user_limit_text = "نامحدود کاربر" if not user_limit else f"{user_limit} کاربر"
    return _english_digits(f"{volume_text} | {days_text} | {user_limit_text}")

def _gb_from_bytes(num_bytes) -> float | None:
    if not num_bytes:
        return None
    try:
        return round(int(num_bytes) / (1024 ** 3), 1)
    except (TypeError, ValueError):
        return None


@router.message(Command("admin"))
async def admin_entry(message: types.Message):
    if not _is_admin(message.from_user.id):
        return  # کاربر عادی هیچ پاسخی نمی‌گیرد (نه حتی پیام خطا) - امنیتی
    await answer_rich(message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(message.from_user.id))


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await edit_rich(callback.message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "admin_manage_admins")
async def admin_manage_admins(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True)
        return
    # رفع باگ: اگر متن/دکمه دقیقاً همان چیزی باشد که الان روی پیام هست (مثلاً دوبار زدن روی دکمه)، تلگرام خطای "message is not modified"
    # می‌دهد که قبلاً گرفته نمی‌شد و به هندلر سراسری خطا می‌رفت و به کاربر "خطایی پیش آمد" نشان داده می‌شد.
    try:
        await edit_rich(callback.message, "👮 مدیریت ادمین‌های فرعی\n\nادمین را انتخاب کنید یا ادمین جدید اضافه کنید:", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))
    except TelegramBadRequest:
        pass
    await callback.answer()

# 🐛 فیکس: دکمه‌ی «مدیریت ادمین‌ها» قبلاً فقط داخل پنل اینلاین بود، طبق درخواست کاربر الان به منوی پایین صفحه (reply keyboard) هم منتقل شد.
@router.message(F.text == "👮 مدیریت ادمین‌ها")
async def admin_manage_admins_from_menu(message: types.Message, state: FSMContext):
    if not _is_main_admin(message.from_user.id):
        return
    await state.clear()
    await answer_rich(message, "👮 مدیریت ادمین‌های فرعی\n\nادمین را انتخاب کنید یا ادمین جدید اضافه کنید:", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))


@router.callback_query(F.data == "subadm_add")
async def subadm_add(callback: types.CallbackQuery, state: FSMContext):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    await state.set_state(AdminStates.waiting_sub_admin_id)
    try:
        await edit_rich(callback.message, 
            "آیدی عددی تلگرام ادمین فرعی را بفرستید. اگر خواستید نام هم ثبت شود این‌طور بفرستید:\n"
            "نام | آیدی\n"
            "مثال: علی پشتیبان | 123456789",
            reply_markup=admin_back_button(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.message(AdminStates.waiting_sub_admin_id)
async def subadm_add_id(message: types.Message, state: FSMContext):
    if not _is_main_admin(message.from_user.id): return
    raw = clean_numeric_id(message.text or "")
    if "|" in raw:
        name, tid = [x.strip() for x in raw.split("|", 1)]
        tid = clean_numeric_id(tid)
    else:
        tid, name = raw, raw
    if not tid.isdigit():
        await answer_rich(message, "❌ فرمت درست نیست. فقط آیدی عددی یا «نام | آیدی» بفرستید.", reply_markup=admin_back_button()); return
    try:
        db.add_sub_admin(tid, name=name or tid, permissions=[])
    except Exception:
        logger.exception("خطا در ثبت ادمین فرعی")
        await answer_rich(message, "⚠️ ثبت آیدی با خطا مواجه شد. لطفاً دوباره تلاش کنید.", reply_markup=admin_back_button())
        return
    await _notify_main_admin_action(message.bot, message.from_user, "افزودن ادمین فرعی", tid, name or tid)
    await state.clear()
    await answer_rich(message, "✅ ادمین فرعی اضافه شد. حالا قابلیت‌هایش را با تیک انتخاب کنید:", reply_markup=admin_permissions_keyboard(tid, []))

@router.callback_query(F.data.startswith("subadm_") & ~F.data.startswith("subadmperm_") & ~F.data.startswith("subadmdel_"))
async def subadm_open(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    tid = callback.data.split("_",1)[1]
    adm = db.get_sub_admin(tid)
    if not adm:
        await callback.answer("❌ ادمین یافت نشد.", show_alert=True); return
    try:
        await edit_rich(callback.message, f"👤 ادمین فرعی: {tid}\n\nقابلیت‌ها را تیک بزنید/بردارید:", reply_markup=admin_permissions_keyboard(tid, adm.get("permissions") or []))
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("subadmperm_"))
async def subadm_toggle_perm(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    rest = callback.data.removeprefix("subadmperm_")
    # رفع باگ: برخی کلیدهای قابلیت خودشان زیرخط دارند (مثل "vpn_panel"، "orders_toggle")، با rsplit("_", 1) قبلی
    # آیدی و نام قابلیت اشتباه جدا می‌شدند (مثلاً آیدی "...vpn" و قابلیت "panel") و ادمین پیدا نمی‌شد. الان با ':' (که در هیچ‌کدام وجود ندارد) جدا می‌شوند.
    if ":" in rest:
        tid, perm = rest.split(":", 1)
    else:
        # سازگاری با دکمه‌های قدیمی (اگر callback زیرخطدار قبلی هنوز جایی فعال باشد)
        tid, perm = rest.rsplit("_", 1)
    adm = db.get_sub_admin(tid)
    if not adm: await callback.answer("❌ یافت نشد.", show_alert=True); return
    perms = set(adm.get("permissions") or [])
    if perm in perms: perms.remove(perm)
    else: perms.add(perm)
    db.update_sub_admin_permissions(tid, sorted(perms))
    await _notify_main_admin_action(callback.bot, callback.from_user, "تغییر دسترسی ادمین فرعی", tid, perm)
    try:
        await callback.message.edit_reply_markup(reply_markup=admin_permissions_keyboard(tid, sorted(perms)))
    except TelegramBadRequest:
        pass
    # فیکس: قبلاً فقط کیبورد تأیید خود ادمین اصلی به‌روز می‌شد، ولی منوی پایین صفحه‌ی خود ادمین فرعی
    # (که از آخرین /start او ساخته شده بود) همچنان قدیمی می‌ماند تا دوباره /start بزند. حالا
    # همینجا منوی پایین صفحه‌اش را با دسترسی‌های تازه دوباره برایش می‌فرستیم تا فوراً به‌روز شود.
    try:
        await send_rich(callback.bot, 
            int(tid),
            "🔄 دسترسی‌های شما توسط ادمین اصلی به‌روزرسانی شد. منوی پایین صفحه شما براساس دسترسی‌های جدید به‌روز شد.",
            reply_markup=admin_reply_keyboard(permissions=set(perms), is_main_admin=False),
        )
    except Exception:
        logger.exception("اطلاع ادمین فرعی از تغییر دسترسی ناموفق بود")
    await callback.answer("✅ بروزرسانی شد")

@router.callback_query(F.data.startswith("subadmdel_"))
async def subadm_delete(callback: types.CallbackQuery):
    if not _is_main_admin(callback.from_user.id):
        await callback.answer("⛔ فقط ادمین اصلی دسترسی دارد.", show_alert=True); return
    tid = callback.data.removeprefix("subadmdel_")
    db.delete_sub_admin(tid)
    await _notify_main_admin_action(callback.bot, callback.from_user, "حذف ادمین فرعی", tid, "")
    try:
        await edit_rich(callback.message, "✅ ادمین حذف شد.", reply_markup=admin_manage_admins_keyboard(db.get_all_sub_admins()))
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.message(F.text == "📥 صف درخواست‌ها")
async def menu_admin_request_queue(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    order_count = len(db.get_pending_orders(limit=200))
    receipt_count = len(db.get_pending_receipts(limit=200))
    await answer_rich(message, 
        "📥 صف درخواست‌ها\n\nچه چیزی رو می‌خوای بررسی کنی؟ 👇",
        reply_markup=admin_request_queue_menu(order_count, receipt_count),
    )


@router.message(F.text == "🤝 نمایندگی (تخفیف VIP)")
async def menu_admin_agency(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    agents = db.get_all_agents()
    text = (
        "🤝 هنوز هیچ نماینده‌ای ثبت نشده.\n\nبرای افزودن، دکمه‌ی زیر را بزنید 👇"
        if not agents else
        "🤝 نمایندگان فعلی (تخفیف خودکار روی VIP)\n\nروی هرکدام بزنید تا مثل بخش «کاربران» مدیریتش کنید 👇"
    )
    await answer_rich(message, text, reply_markup=admin_agency_menu(agents))


@router.message(F.text == "🗂 دسته‌بندی‌های VIP")
async def menu_admin_vip_categories(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    await answer_rich(message, 
        "🗂 دسته‌بندی‌های VIP\n\n"
        "این دسته‌ها همان چیزی هستند که کاربر موقع «خرید اشتراک → سرور VIP» می‌بیند.\n"
        "برای مدیریت پلن‌های داخل هر دسته، روی آن بزنید 👇",
        reply_markup=admin_vip_categories_keyboard(),
    )




@router.message(F.text == "🔴 خاموش کردن سفارشات")
async def menu_admin_orders_off(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    db.set_orders_enabled(False)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await answer_rich(message, f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await send_rich(message.bot, 
                int(u["telegram_id"]),
                db.get_text_override("orders_closed", "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.") + "\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد.",
            )
            sent += 1
        except Exception:
            failed += 1
    await edit_rich(status_msg, f"🔴 بخش سفارشات خاموش شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await answer_rich(message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_reply_kb_for(message.from_user.id, False))


@router.message(F.text == "🟢 روشن کردن سفارشات")
async def menu_admin_orders_on(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    db.set_orders_enabled(True)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await answer_rich(message, f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await send_rich(message.bot, 
                int(u["telegram_id"]),
                db.get_text_override("orders_opened", "🟢 ربات مجدداً فعال شد!") + "\n\nبا زدن /start می‌توانید دوباره سفارش ثبت کنید.",
            )
            sent += 1
        except Exception:
            failed += 1
    await edit_rich(status_msg, f"🟢 بخش سفارشات روشن شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await answer_rich(message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_reply_kb_for(message.from_user.id, True))


def _error_logs_text(logs: list, total: int) -> str:
    if not logs:
        return "🦖 لاگ خطاها\n\n✅ تا این لحظه هیچ خطای ثبت نشده."
    return f"🦖 لاگ خطاها — {total} خطای ثبت‌شده\n\nروی هرکدام بزنید تا جزئیاتش رو ببینید 👇"


async def _open_error_logs(target, edit: bool = False):
    logs = db.get_error_logs(limit=15)
    total = db.count_error_logs()
    text = _error_logs_text(logs, total)
    kb = admin_error_logs_keyboard(logs)
    if edit:
        await edit_rich(target.message, text, reply_markup=kb)
    else:
        await answer_rich(target, text, reply_markup=kb)


@router.message(F.text == "🦖 لاگ خطاها")
async def menu_admin_error_logs(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    await _open_error_logs(message, edit=False)


@router.callback_query(F.data == "errlogrefresh")
async def admin_error_logs_refresh(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _open_error_logs(callback, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("errlogdetail_"))
async def admin_error_log_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    log_id = int(callback.data.split("_", 1)[1])
    log = db.get_error_log(log_id)
    if log is None:
        await callback.answer("❌ یافت نشد.", show_alert=True)
        return
    # 🆕 فیکس: قبلاً پیام خطا جداگانه از یک طول ثابت (message تا ۲۰۰۰ کاراکتر + traceback تا ۳۵۰۰ کاراکتر) ساخته می‌شد؛ اگر خطایی با پیام (message) طولانی ثبت می‌شد (مثلاً همین خطای «MESSAGE_TOO_LONG»)، مجموع این دو می‌توانست از سقف تلگرام (۴۰۹۶) رد شود و خود صفحه‌ی «لاگ خطاها» هم با همان خطا مواجه می‌شد و ادمین اصلاً نمی‌توانست جزئیات را ببیند. حالا طول قابل‌نمایش traceback پویا بر اساس طول واقعی بقیه محاسبه می‌شود تا مجموع همیشه زیر سقف تلگرام بماند، و بازهم یک try/except محافظتی اضافه شده تا اگر بازهم محاسبه جایی کم بیافتاد، پیام با یک نسخه‌ی کاملاً مختصر‌شده بازهم فرستاده شود تا این بخش از پنل ادمین هرگز با ارور متوقف نشود.
    error_type_display = html.escape(str(log["error_type"]))
    occurred_at_display = html.escape(str(log.get("occurred_at") or ""))
    message_display = html.escape(str(log.get("message") or "")[:300])
    header = (
        f"⚠️ {error_type_display}\n"
        f"🕐 {occurred_at_display}\n\n"
        f"📝 {message_display}\n\n"
    )
    wrapper_len = len("<pre></pre>")
    max_tb_len = max(TELEGRAM_TEXT_LIMIT - len(header) - wrapper_len - 20, 200)
    tb = html.escape(str(log.get("traceback") or "")[:max_tb_len])
    text = f"{header}<pre>{tb}</pre>"
    try:
        await edit_rich(callback.message, text, reply_markup=admin_error_log_detail_keyboard())
    except TelegramBadRequest as e:
        if is_message_too_long_error(e):
            fallback_text = truncate_for_telegram(
                f"⚠️ {error_type_display}\n🕐 {occurred_at_display}\n\n📝 {message_display}"
            )
            await edit_rich(callback.message, fallback_text, reply_markup=admin_error_log_detail_keyboard())
        else:
            raise
    await callback.answer()


@router.callback_query(F.data == "errlogclear")
async def admin_error_logs_clear_ask(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "🗑 مطمئنید می‌خواهید همه‌ی لاگ ها پاک شوند؟", reply_markup=admin_error_logs_clear_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "errlogclearconfirm")
async def admin_error_logs_clear_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    db.clear_error_logs()
    await _open_error_logs(callback, edit=True)
    await callback.answer("✅ پاک شد.")


# ---------------------------------------------------------------------------
# 📊 آمار
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    text = (
        f"📊 آمار ربات\n\n"
        f"💰 فروش امروز: {db.sales_since(1):,} تومان\n"
        f"💰 فروش هفته: {db.sales_since(7):,} تومان\n"
        f"💰 فروش ماه: {db.sales_since(30):,} تومان\n"
        f"💰 کل فروش: {db.total_sales():,} تومان\n\n"
        f"👥 تعداد کاربران: {db.count_users()}\n"
        f"🟢 کاربران فعال (۳۰ روز اخیر): {db.count_active_users(30)}"
    )
    await edit_rich(callback.message, text, reply_markup=admin_back_button())
    await callback.answer()


# ---------------------------------------------------------------------------
# 👥 لیست کاربران
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_userlist")
async def admin_user_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    text = (
        f"👥 مدیریت کاربران\n\n"
        f"👥 کل کاربران: {db.count_users()}\n"
        f"🟢 کاربران فعال (خریدکرده): {db.count_customers()}\n"
        f"👤 کاربران بدون خرید: {db.count_non_customers()}\n\n"
        f"یکی از گزینه‌های زیر را انتخاب کنید 👇"
    )
    await edit_rich(callback.message, text, reply_markup=admin_userlist_menu())
    await callback.answer()


async def _render_userlist_page(callback: types.CallbackQuery, list_kind: str, page: int):
    per_page = 10
    if list_kind == "active":
        users = db.get_customers_page(page, per_page)
        total = db.count_customers()
        title = "🟢 مشتریان فعال (خریدکرده)"
    else:
        users = db.get_all_users_page(page, per_page)
        total = db.count_non_customers()
        title = "👤 کاربران بدون خرید"

    has_next = (page + 1) * per_page < total
    if not users and page == 0:
        text = f"{title}\n\nهنوز هیچ کاربری در این لیست نیست."
    else:
        start = page * per_page + 1
        text = f"{title} — {total} نفر (مرتب‌شده بر اساس بیشترین خرید)\nنمایش {start} تا {start + len(users) - 1}:\n\n"
        text += "برای مدیریت هرکدام روی نامش بزن 👇"

    await edit_rich(callback.message, 
        text, reply_markup=admin_userlist_page_keyboard(users, page, has_next, list_kind)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_userlist_active")
async def admin_userlist_active(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_userlist_page(callback, "active", 0)


@router.callback_query(F.data == "admin_userlist_all")
async def admin_userlist_all(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_userlist_page(callback, "all", 0)


@router.callback_query(F.data.startswith("userpage_"))
async def admin_userlist_page_nav(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    _, list_kind, page_str = callback.data.split("_")
    await _render_userlist_page(callback, list_kind, int(page_str))


@router.callback_query(F.data.startswith("useropen_"))
async def admin_user_open(callback: types.CallbackQuery):
    """با زدن روی هرکدام از کاربران در لیست، مستقیم وارد صفحه‌ی مدیریت همان کاربر می‌شویم
    (همان صفحه‌ای که از طریق «🔍 جستجوی حرفه‌ای» هم بازش می‌شد)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("useropen_", "")
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )
    await _reply_with_user_actions(
        callback.message, text, user["telegram_id"], db.is_user_blocked(user["telegram_id"]), edit=True
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 📒 حسابداری کاربر — تراکنش‌ها (کیف پول، خرید، شارژ، منشأ پول) با صفحه‌بندی
# ---------------------------------------------------------------------------
def _tx_type_label(tx_type: str) -> str:
    return {
        "charge": "💳 شارژ (تأیید کارت‌به‌کارت/دستی توسط ادمین)",
        "purchase": "🛒 خرید سرویس (کسر از کیف پول)",
        "referral_locked": "🔒 پاداش دعوت (در انتظار آزادسازی)",
        "referral_release": "🔓 آزادسازی پاداش دعوت",
        "referral_pending": "🔒 پاداش دعوت (در انتظار)",
    }.get(tx_type, f"📄 {tx_type}")


@router.callback_query(F.data.startswith("accounting_"))
async def admin_user_accounting(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    _, uid, page_str = callback.data.split("_")
    page = int(page_str)
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    per_page = 10
    txs = db.get_transactions_page(user["id"], page, per_page)
    has_next = len(txs) == per_page

    text = (
        f"📒 حسابداری کاربر {user['name']} (🆔 {user['telegram_id']})\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود (پاداش دعوت در انتظار): {user['locked_wallet']:,} تومان\n"
        f"🛒 مجموع خرید: {user['total_purchase']:,} تومان\n\n"
        f"📋 تراکنش‌ها (صفحه {page + 1}):\n\n"
    )
    if not txs:
        text += "— تراکنشی در این صفحه نیست —"
    else:
        for tx in txs:
            sign = "+" if tx["amount"] >= 0 and tx["type"] in ("charge", "referral_release") else "-"
            text += (
                f"{_tx_type_label(tx['type'])}\n"
                f"{sign}{abs(tx['amount']):,} تومان | {tx['status']}\n"
                f"📝 {tx.get('description') or '-'}\n"
                f"🕐 {tx['created_at']}\n\n"
            )

    await edit_rich(callback.message, text, reply_markup=admin_accounting_keyboard(uid, page, has_next))
    await callback.answer()


# ---------------------------------------------------------------------------
# 🔍 جستجوی حرفه‌ای
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_search")
async def admin_search_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "🔍 آیدی عددی یا کد دعوت کاربر را ارسال کنید:", reply_markup=admin_back_button()
    )
    await state.set_state(AdminStates.waiting_search_user)
    await callback.answer()


@router.message(F.text == "🔎 جستجوی کانفیگ")
async def menu_admin_config_search(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "users"):
        return
    await message.answer(
        "🔎 بخشی از اسم سرویس را ارسال کنید تا بین سرویس‌های همه کاربران جستجو کنم.\n\nمثلاً: `520120` یا `Config`",
        reply_markup=admin_back_button(),
        parse_mode="Markdown",
    )
    await state.set_state(AdminStates.waiting_search_config)


@router.message(AdminStates.waiting_search_user)
async def admin_search_result(message: types.Message, state: FSMContext):
    query = clean_numeric_id(message.text)

    # جستجوی آیدی عددی به‌صورت حرفه‌ای: اگر فقط بخشی از آیدی وارد شود،
    # همهٔ کاربران منطبق برگردانده می‌شوند و هر کاربر یک دکمهٔ شیشه‌ای
    # با نام خودش دارد. با زدن دکمه، همان useropen_ قبلی صفحهٔ مدیریت کاربر را باز می‌کند.
    if query.isdigit():
        matches = db.search_users_by_telegram_id_fragment(query, limit=50)
        if not matches:
            await answer_rich(
                message,
                f"❌ هیچ کاربری با بخشی از آیدی عددی «{query}» پیدا نشد.",
                reply_markup=admin_back_button(),
            )
            return

        buttons = []
        for user in matches:
            name = str(user.get("name") or "کاربر").strip() or "کاربر"
            buttons.append([
                InlineKeyboardButton(
                    text=name,
                    callback_data=f"useropen_{user['telegram_id']}",
                    style="primary",
                )
            ])
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
        kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
        await answer_rich(
            message,
            f"🔍 نتیجه جستجوی آیدی «{query}»\n\n👥 {len(matches)} کاربر پیدا شد.\nبرای مدیریت کاربر روی نامش بزن 👇",
            reply_markup=kb,
        )
        await state.clear()
        return

    # کد دعوت همچنان به‌صورت تک‌کاربره کار می‌کند.
    user = db.get_user_by_invite_code(query)
    if user is None:
        await answer_rich(message, "❌ کاربری با این مشخصات یافت نشد.", reply_markup=admin_back_button())
        return

    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )
    await _reply_with_user_actions(
        message, text, user["telegram_id"], db.is_user_blocked(user["telegram_id"]), edit=False
    )
    await state.clear()


# ---------------------------------------------------------------------------
# 🔎 جستجوی کانفیگ/سرویس در کل کاربران
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_config_search")
async def admin_config_search_start(callback: types.CallbackQuery, state: FSMContext):
    if not _admin_perm(callback.from_user.id, "users"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message,
        "🔎 بخشی از اسم سرویس را ارسال کنید تا بین سرویس‌های همه کاربران جستجو کنم.\n\nمثلاً: `520120` یا `Config`",
        reply_markup=admin_back_button(),
        parse_mode="Markdown",
    )
    await state.set_state(AdminStates.waiting_search_config)
    await callback.answer()


@router.message(AdminStates.waiting_search_config)
async def admin_config_search_result(message: types.Message, state: FSMContext):
    if not _admin_perm(message.from_user.id, "users"):
        return
    query = (message.text or "").strip()
    if not query:
        await answer_rich(message, "❌ لطفاً بخشی از نام سرویس را وارد کنید.", reply_markup=admin_back_button())
        return

    configs = db.get_all_configs(include_deleted=True)
    await enrich_configs_with_subscription_names(configs)
    tokens = [x.casefold() for x in query.split() if x.strip()]
    if not tokens:
        tokens = [query.casefold()]
    matches = []
    for cfg in configs:
        display_name = str(cfg.get("_display_name") or cfg.get("plan") or "سرویس")
        plan_name = str(cfg.get("plan") or "")
        service_id = str(cfg.get("service_id") or "")
        owner = db.get_user_by_id(cfg.get("user_id")) or {}
        owner_name = str(owner.get("name") or "")
        haystack = " ".join((display_name, plan_name, service_id, owner_name)).casefold()
        # چند رقم/بخش جداگانه هم‌زمان قابل جست‌وجوست؛ مثلاً «43 05» هر سرویس
        # دارای هر دو قطعه را برمی‌گرداند، و نتیجه مثل قبل نام پروفایل صاحب سرویس را نشان می‌دهد.
        if all(token in haystack for token in tokens):
            matches.append(cfg)

    if not matches:
        await answer_rich(message, f"🔎 برای «{query}» هیچ سرویسی پیدا نشد.", reply_markup=admin_back_button())
        return

    # هر سرویس با نام واقعی استخراج‌شده از Subscription و نام صاحبش نمایش داده می‌شود.
    buttons = []
    for cfg in matches[:50]:
        owner = db.get_user_by_id(cfg.get("user_id"))
        owner_name = (owner or {}).get("name") or (owner or {}).get("telegram_id") or "کاربر"
        icon = "🚀" if cfg.get("type", "vip") == "vip" else "🎮"
        mark = "❌ " if cfg.get("deleted") else ""
        display_name = cfg.get("_display_name") or cfg.get("plan") or "سرویس"
        buttons.append([InlineKeyboardButton(
            text=f"{mark}👤 {owner_name} | {icon} {display_name}",
            callback_data=f"svcdetail_{cfg['id']}", style="primary"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
    suffix = f"\n\nنمایش {min(len(matches), 50)} مورد از {len(matches)} نتیجه." if len(matches) > 50 else ""
    await answer_rich(message, f"🔎 {len(matches)} سرویس برای «{query}» پیدا شد:{suffix}\n\n👇 برای مشاهده جزئیات انتخاب کنید.", reply_markup=kb)
    await state.clear()


# ---------------------------------------------------------------------------
# 💳 شارژ کیف پول (تأیید/رد رسید + شارژ دستی)
# دسترسی از طریق «🔍 جستجوی حرفه‌ای» ← دکمه «💰 شارژ دستی» (برای بهینه شدن فضای منو)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("approve_"))
async def approve_charge(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"approvecharge_{callback.data}") or not db.claim_admin_action(f"approvecharge_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, amount_str, receipt_id_str = callback.data.split("_")
    amount = int(amount_str)
    receipt_id = int(receipt_id_str)

    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    db.add_to_wallet(user["id"], amount, "شارژ کیف پول (تأیید رسید)")
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n✅ تأیید و شارژ شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_wallet_charge")
        await send_rich(callback.bot, int(uid), user_text("notif_wallet_charge_approved", amount=amount))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "تأیید رسید شارژ کیف پول", uid, f"مبلغ {amount:,} تومان")
    await callback.answer("✅ شارژ شد.")


@router.callback_query(F.data.startswith("reject_"))
async def reject_charge(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"rejectcharge_{callback.data}") or not db.claim_admin_action(f"rejectcharge_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, receipt_id_str = callback.data.split("_")
    receipt_id = int(receipt_id_str)
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n❌ رد شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_receipt_rejected")
        await send_rich(callback.bot, int(uid), user_text("notif_receipt_rejected_short"))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "رد رسید", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "")
    await callback.answer("❌ رد شد.")


@router.callback_query(F.data.startswith("custom_"))
async def custom_charge_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("custom_", "")
    await state.update_data(charge_target=uid)
    await state.set_state(AdminStates.waiting_custom_amount)
    await answer_rich(callback.message, f"💵 مبلغ شارژ برای کاربر {uid} را به تومان ارسال کنید:")
    await callback.answer()


@router.message(AdminStates.waiting_custom_amount)
async def custom_charge_apply(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید.")
        return

    data = await state.get_data()
    uid = data.get("charge_target")
    amount = int(clean_numeric_id(message.text))

    user = db.get_user(uid)
    if user is None:
        await answer_rich(message, "❌ کاربر یافت نشد.")
        await state.clear()
        return

    db.add_to_wallet(user["id"], amount, "شارژ دستی توسط ادمین")
    try:
        db.resolve_pending_receipt("charge", uid)
    except Exception:
        pass
    await answer_rich(message, f"✅ {amount:,} تومان به کیف پول کاربر {uid} اضافه شد.")
    try:
        await send_notification_sticker(message.bot, int(uid), "notif_wallet_charge")
        await send_rich(message.bot, int(uid), user_text("notif_wallet_charged", amount=amount))
    except Exception:
        pass
    await state.clear()


# ---------------------------------------------------------------------------
# 💳 تأیید/رد رسید خرید کارت‌به‌کارت (سرویس)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("approvepay|"))
async def approve_purchase(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"approvepay_{callback.data}") or not db.claim_admin_action(f"approvepay_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, plan_key, price_str, receipt_id_str = callback.data.split("|")
    price = int(price_str)
    receipt_id = int(receipt_id_str)
    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if user is None or plan is None:
        await callback.answer("❌ کاربر یا پلن یافت نشد.", show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            "⚠️ این کاربر قبلاً از «تست رایگان» استفاده کرده؛ هر کاربر فقط یک‌بار می‌تواند این پلن را بگیرد.",
            show_alert=True,
        )
        return

    # 🐛 فیکس: کد تخفیف کارت‌به‌کارت حالا فقط همین‌جا (تأیید ادمین) مصرف
    # می‌شود، نه هنگام ارسال رسید؛ اگر ادمین رد کند سهم کد تخفیف مصرف نمی‌شود.
    pending = None
    try:
        pending = db.get_pending_receipt_by_id(receipt_id)
    except Exception:
        pending = None

    db.record_purchase(user["id"], price, f"خرید {plan['name']} (کارت به کارت)")

    if pending and pending.get("discount_code"):
        try:
            db.use_discount(pending["discount_code"], user["id"])
        except Exception:
            logging.getLogger(__name__).exception("خطا در مصرف کد تخفیف کارت‌به‌کارت")

    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), price)

    # تأیید کارت‌به‌کارت = پرداخت واقعی و موفق؛ بنابراین فقط بعد از ساخت
    # سفارشِ همین خرید، پاداش دعوت را آزاد می‌کنیم.
    if price > 0 and plan_key != FREE_TEST_PLAN_KEY:
        try:
            db.complete_referral(user["id"], qualifying_order_id=order_id)
        except ValueError:
            pass
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass

    await _finish_receipt_message(
        callback.message, "\n\n✅ تأیید شد و خرید ثبت شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        _discount_note = f"\n🎟 کد تخفیف {pending['discount_code']} برای این خرید مصرف شد." if pending and pending.get("discount_code") else ""
        _confirm_text = user_text("notif_purchase_approved", plan_name=plan["name"], discount_note=_discount_note)
        # fix: به کاربر بگوییم کد تخفیف همین تأیید مصرف شده تا گیج نشود چرا دیگر قابل‌استفاده نیست.
        if pending and pending.get("discount_code"):
            _confirm_text += f"\n🎟 کد تخفیف {pending['discount_code']} برای این خرید مصرف شد."
        await send_notification_sticker(callback.bot, int(uid), "notif_purchase_approved")
        await send_rich(callback.bot, int(uid), _confirm_text)
    except Exception:
        pass
    await answer_rich(callback.message, 
        "📤 برای ارسال کانفیگ این خرید:", reply_markup=admin_purchase_notify_keyboard(uid, plan_key, order_id)
    )
    await _notify_main_admin_action(callback.bot, callback.from_user, "تأیید خرید/سفارش", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "ثبت شد")
    await callback.answer("✅ تأیید شد.")


@router.callback_query(F.data.startswith("rejectpay|"))
async def reject_purchase(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    if is_duplicate_action(f"rejectpay_{callback.data}") or not db.claim_admin_action(f"rejectpay_{callback.data}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.", show_alert=True)
        return

    _, uid, receipt_id_str = callback.data.split("|")
    receipt_id = int(receipt_id_str)
    try:
        db.resolve_pending_receipt_by_id(receipt_id)
    except Exception:
        pass
    await _finish_receipt_message(
        callback.message, "\n\n❌ رد شد.", queue_refresh=lambda: _render_pending_receipts(callback)
    )
    try:
        await send_notification_sticker(callback.bot, int(uid), "notif_receipt_rejected")
        await send_rich(callback.bot, int(uid), user_text("notif_receipt_rejected"))
    except Exception:
        pass
    await _notify_main_admin_action(callback.bot, callback.from_user, "رد رسید", uid if 'uid' in locals() else str(order_id) if 'order_id' in locals() else "", "")
    await callback.answer("❌ رد شد.")


# ---------------------------------------------------------------------------
# 🛠 تأیید/رد رسید کارت‌به‌کارت برای «بساز سرویس خودت» / «تمدید سرویس»
# ---------------------------------------------------------------------------




async def _log_fulfilled_order(
    bot, user: dict, *, plan_order_id=None, custom_order_id=None,
    target_config_id=None, service_id=None, service_name: str = "-",
    package_text: str = "-", expiry_text: str = "-",
):
    """پیام لاگ استاندارد سفارش را برای «کانال اعتماد» می‌سازد و ارسال می‌کند."""
    label = "🛒 خرید جدید"
    amount_text = "-"

    if plan_order_id:
        order = db.get_order(plan_order_id)
        if order:
            amount_text = f"{order['price']:,} تومان" if order["price"] else "رایگان"
            if order.get("order_type") == "test":
                label = "🎁 تست رایگان"
            elif order.get("order_type") == "renew":
                label = "🔁 تمدید سرویس"
            else:
                label = "🛒 خرید جدید"
    elif custom_order_id:
        order = db.get_custom_order(custom_order_id)
        if order:
            amount_text = f"{order['price']:,} تومان" if order["price"] else "رایگان"
            label = "🔁 تمدید سرویس" if order.get("order_type") == "renew" else "🛠 سرویس سفارشی جدید"
    elif target_config_id:
        label = "🔁 تمدید سرویس"

    username = await alerts.fetch_username(bot, user["telegram_id"])
    await alerts.log_order_to_channel(
        bot,
        order_label=label,
        user=user,
        username=username,
        service_id=service_id,
        service_name=service_name,
        package_text=package_text,
        amount_text=amount_text,
        expiry_text=expiry_text,
    )


# ---------------------------------------------------------------------------
# 🔁 تأیید/رد تمدید کارت‌به‌کارت یا ارزی
# ---------------------------------------------------------------------------
def _renewal_confirmation_values(panel_data: dict | None, added_volume: float, added_days: int, fallback_expiry=None) -> dict:
    """باقی‌مانده واقعی قبل از تمدید، مقدار تمدید و باقی‌مانده جدید را محاسبه می‌کند."""
    import math, time
    data = panel_data or {}

    # total در پنل «حجم کل» است، نه حجم باقی‌مانده؛ بنابراین برای نمایش
    # نتیجه تمدید باید used را کم کنیم تا باقی‌مانده واقعی به دست بیاید.
    total = data.get("total")
    used = data.get("used", 0)
    if total is not None:
        try:
            total_bytes = float(total)
            used_bytes = float(used or 0)
            remaining_bytes = max(0.0, total_bytes - used_bytes)
            remaining_gb = remaining_bytes / (1024 ** 3)
            if total_bytes <= 0:
                previous_volume = new_volume = "نامحدود"
            elif added_volume:
                previous_gb = max(0.0, remaining_gb - float(added_volume))
                previous_volume = f"{previous_gb:g} گیگ"
                new_volume = f"{remaining_gb:g} گیگ"
            else:
                previous_volume = new_volume = f"{remaining_gb:g} گیگ"
        except Exception:
            previous_volume = new_volume = "نامشخص"
    else:
        previous_volume = new_volume = "بدون تغییر" if not added_volume else "نامشخص"

    expire = data.get("expire") if "expire" in data else fallback_expiry
    if expire is None or str(expire).strip().lower() in ("", "none"):
        expire = fallback_expiry
    if str(expire).strip() in ("0", "0.0"):
        previous_days = new_days_text = "نامحدود"
    elif expire:
        try:
            # پنل‌ها ممکن است timestamp ثانیه/میلی‌ثانیه یا تاریخ ISO برگردانند.
            if isinstance(expire, (int, float)) or str(expire).strip().replace(".", "", 1).isdigit():
                exp_value = float(expire)
                if exp_value > 100000000000:
                    exp_value /= 1000.0
                new_days = max(0, int(math.ceil((exp_value - time.time()) / 86400)))
            else:
                from datetime import datetime, date
                from zoneinfo import ZoneInfo
                raw_expire = str(expire).strip().replace("Z", "+00:00")
                try:
                    exp_dt = datetime.fromisoformat(raw_expire)
                except ValueError:
                    exp_dt = datetime.strptime(raw_expire[:10], "%Y-%m-%d")
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=ZoneInfo("Asia/Tehran"))
                new_days = max(0, int(math.ceil((exp_dt.timestamp() - time.time()) / 86400)))
            if added_days:
                previous_days = f"{max(0, new_days - int(added_days))} روز"
                new_days_text = f"{new_days} روز"
            else:
                previous_days = new_days_text = f"{new_days} روز"
        except Exception:
            previous_days = new_days_text = "نامشخص"
    else:
        previous_days = new_days_text = "نامحدود"

    return {
        "added_volume": f"+{float(added_volume):g} گیگ" if added_volume else "بدون تغییر",
        "previous_volume": previous_volume,
        "new_volume": new_volume,
        "added_days": f"+{int(added_days)} روز" if added_days else "بدون تغییر",
        "previous_days": previous_days,
        "new_days": new_days_text,
        "volume": f"{float(added_volume):g} گیگ" if added_volume else "بدون تغییر",
        "days": f"{int(added_days)} روز" if added_days else "بدون تغییر",
    }


def _renewal_confirmation_details(panel_data: dict | None, added_volume: float, added_days: int, fallback_expiry=None) -> str:
    """جزئیات تمدید را بر اساس نوع تمدید، از متن مستقل همان حالت نمایش می‌دهد."""
    values = _renewal_confirmation_values(panel_data, added_volume, added_days, fallback_expiry=fallback_expiry)
    if added_volume and added_days:
        detail = user_text(
            "renew_volume_days_detail",
            previous_volume=values["previous_volume"],
            added_volume=values["added_volume"],
            new_volume=values["new_volume"],
            previous_days=values["previous_days"],
            added_days=values["added_days"],
            new_days=values["new_days"],
        )
    elif added_volume:
        detail = user_text(
            "renew_volume_only_detail",
            previous_volume=values["previous_volume"],
            added_volume=values["added_volume"],
            new_volume=values["new_volume"],
        )
    elif added_days:
        detail = user_text(
            "renew_days_only_detail",
            previous_days=values["previous_days"],
            added_days=values["added_days"],
            new_days=values["new_days"],
        )
    else:
        return user_text("renew_details_empty")
    return user_text("renew_details_title") + "\n\n" + detail


async def _apply_admin_renewal(receipt):
    try:
        payload=json.loads(receipt.get("extra") or "{}")
    except Exception:
        payload={}
    uid=str(receipt.get("telegram_id")); user=db.get_user(uid)
    cfg_id=payload.get("cfg_id")
    cfg=db.get_config_by_id(int(cfg_id)) if cfg_id else None
    if not user or not cfg or cfg.get("user_id") != user.get("id") or not cfg.get("service_id"):
        return False, "سرویس تمدیدی دیگر پیدا نشد."
    volume=float(payload.get("volume_gb") or 0); days=int(payload.get("days") or 0)
    panel = db.get_vpn_panel(cfg.get("panel_id")) if cfg.get("panel_id") else None
    if not panel:
        return False, "پنل این سرویس پیدا نشد."
    ok, panel_data, msg = await panels.renew_existing_service(
        panel, cfg["service_id"], volume, days
    )
    if not ok:
        return False, msg
    try:
        exp = panel_data.get("expire") if isinstance(panel_data, dict) else None
        if exp:
            expiry = datetime.fromtimestamp(int(exp), tz=TEHRAN_TZ).replace(tzinfo=None).strftime("%Y-%m-%d")
        elif isinstance(panel_data, dict) and "expire" in panel_data:
            expiry = "نامحدود"
        else:
            expiry = cfg.get("expiry")
    except Exception:
        expiry = cfg.get("expiry")
    db.update_config_expiry(cfg["id"], expiry)
    return True,(volume,days,expiry,panel_data)

@router.callback_query(F.data.startswith("approverenew|"))
async def approve_renewal(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    receipt_id=int(callback.data.split("|",1)[1])
    if is_duplicate_action(f"approverenew_{receipt_id}") or not db.claim_admin_action(f"approverenew_{receipt_id}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.",show_alert=True); return
    receipt=db.get_pending_receipt_by_id(receipt_id)
    if not receipt: await callback.answer("⚠️ رسید پیدا نشد.",show_alert=True); return
    ok,result=await _apply_admin_renewal(receipt)
    if not ok: await answer_rich(callback.message,f"❌ تمدید انجام نشد: {result}"); await callback.answer("❌ ناموفق",show_alert=True); return
    volume,days,_,panel_data=result; db.resolve_pending_receipt_by_id(receipt_id)
    try:
        payload=json.loads(receipt.get("extra") or "{}")
    except Exception:
        payload={}
    cfg=db.get_config_by_id(payload.get("cfg_id")) if payload.get("cfg_id") else None
    service_username=alerts.get_config_service_username(cfg or {}, panel_data)
    package_name=alerts.get_config_package_name(cfg or {})
    await _finish_receipt_message(callback.message,"\n\nتمدید تأیید و روی پنل اعمال شد.",queue_refresh=lambda:_render_pending_receipts(callback))
    try: await send_rich(callback.bot,int(receipt["telegram_id"]),user_text("renew_done",service_name=service_username,details=_renewal_confirmation_details(panel_data, volume, days, fallback_expiry=cfg.get("expiry") if cfg else None)))
    except Exception: pass
    try:
        renew_user=db.get_user(receipt["telegram_id"]) or {}
        await alerts.log_renewal_to_channel(callback.bot, renew_user, cfg or {}, panel_data, int(receipt.get("amount") or 0), volume, days)
    except Exception:
        logger.exception("ثبت لاگ تمدید کارت‌به‌کارت در کانال ناموفق بود")
    try:
        await send_rich(callback.bot,ADMIN_ID,alerts.admin_delivery_summary(renew_user,service_username,package_name,int(receipt.get("amount") or 0)))
    except Exception:
        logger.exception("ارسال خلاصه تأیید تمدید برای ادمین ناموفق بود")
    await callback.answer("✅ تمدید شد.")

@router.callback_query(F.data.startswith("rejectrenew|"))
async def reject_renewal(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    receipt_id=int(callback.data.split("|",1)[1])
    if is_duplicate_action(f"rejectrenew_{receipt_id}") or not db.claim_admin_action(f"rejectrenew_{receipt_id}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.",show_alert=True); return
    receipt=db.get_pending_receipt_by_id(receipt_id)
    if receipt:
        db.resolve_pending_receipt_by_id(receipt_id)
        await _finish_receipt_message(callback.message,"\n\n❌ رد شد.",queue_refresh=lambda:_render_pending_receipts(callback))
        try: await send_rich(callback.bot,int(receipt["telegram_id"]),user_text("notif_receipt_rejected"))
        except Exception: pass
    await callback.answer("❌ رد شد.")

@router.callback_query(F.data.startswith("rejectcrypto|"))
async def reject_crypto_payment(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    receipt_id=int(callback.data.split("|",1)[1]); receipt=db.get_pending_receipt_by_id(receipt_id)
    if receipt:
        db.resolve_pending_receipt_by_id(receipt_id)
        await _finish_receipt_message(callback.message,"\n\n❌ پرداخت ارزی رد شد.",queue_refresh=lambda:_render_pending_receipts(callback))
        try: await send_rich(callback.bot,int(receipt["telegram_id"]),user_text("notif_receipt_rejected"))
        except Exception: pass
    await callback.answer("❌ رد شد.")

@router.callback_query(F.data.startswith("approvecrypto|"))
async def approve_crypto_payment(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    receipt_id=int(callback.data.split("|",1)[1]); receipt=db.get_pending_receipt_by_id(receipt_id)
    if not receipt: await callback.answer("⚠️ رسید پیدا نشد.",show_alert=True); return
    if is_duplicate_action(f"approvecrypto_{receipt_id}") or not db.claim_admin_action(f"approvecrypto_{receipt_id}"):
        await callback.answer("⚠️ این رسید قبلاً پردازش شده.",show_alert=True); return
    try: payload=json.loads(receipt.get("extra") or "{}")
    except Exception: payload={}
    if receipt.get("kind")=="crypto_renew":
        ok,result=await _apply_admin_renewal(receipt)
        if not ok: await answer_rich(callback.message,f"❌ تمدید انجام نشد: {result}"); await callback.answer("❌ ناموفق",show_alert=True); return
        volume,days,_,panel_data=result; db.resolve_pending_receipt_by_id(receipt_id)
        cfg=db.get_config_by_id(payload.get("cfg_id")) if payload.get("cfg_id") else None
        service_username=alerts.get_config_service_username(cfg or {}, panel_data)
        package_name=alerts.get_config_package_name(cfg or {})
        try: await send_rich(callback.bot,int(receipt["telegram_id"]),user_text("renew_done",service_name=service_username,details=_renewal_confirmation_details(panel_data, volume, days, fallback_expiry=cfg.get("expiry") if cfg else None)))
        except Exception: pass
        try:
            renew_user=db.get_user(receipt["telegram_id"]) or {}
            await alerts.log_renewal_to_channel(callback.bot, renew_user, cfg or {}, panel_data, int(receipt.get("amount") or 0), volume, days)
        except Exception:
            logger.exception("ثبت لاگ تمدید ارزی در کانال ناموفق بود")
        try:
            await send_rich(callback.bot,ADMIN_ID,alerts.admin_delivery_summary(renew_user,service_username,package_name,int(receipt.get("amount") or 0)))
        except Exception:
            logger.exception("ارسال خلاصه تأیید تمدید ارزی برای ادمین ناموفق بود")
        await _finish_receipt_message(callback.message,"\n\nپرداخت ارزی تأیید و تمدید انجام شد.",queue_refresh=lambda:_render_pending_receipts(callback)); await callback.answer("تمدید شد"); return
    plan_key=payload.get("plan_key") or receipt.get("plan_key"); plan=db.get_effective_plan(plan_key); user=db.get_user(receipt["telegram_id"])
    if not plan or not user: await callback.answer("❌ پلن/کاربر پیدا نشد.",show_alert=True); return
    price=int(receipt["amount"]); db.record_purchase(user["id"],price,f"خرید {plan['name']} (ارزی)")
    if payload.get("discount_code"):
        try: db.use_discount(payload["discount_code"], user["id"])
        except Exception: logger.exception("مصرف کد تخفیف پرداخت ارزی ناموفق بود")
    order_id=db.create_order(user["id"],plan_key,plan["name"],plan_type(plan_key),price)
    db.resolve_pending_receipt_by_id(receipt_id)
    await _finish_receipt_message(callback.message,"\n\n✅ پرداخت ارزی تأیید شد و خرید ثبت شد.",queue_refresh=lambda:_render_pending_receipts(callback))
    try: await send_rich(callback.bot,int(receipt["telegram_id"]),user_text("notif_purchase_approved",plan_name=plan["name"],discount_note=""))
    except Exception: pass
    await answer_rich(callback.message,"📤 برای ارسال کانفیگ این خرید:",reply_markup=admin_purchase_notify_keyboard(receipt["telegram_id"],plan_key,order_id)); await callback.answer("✅ خرید ثبت شد.")

# ---------------------------------------------------------------------------
# 🛒 خرید اشتراک برای خود ادمین
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_buy_subscription")
async def admin_buy_subscription(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    if not db.is_orders_enabled():
        await callback.answer("🔴 سفارشات در حال حاضر خاموش است.", show_alert=True)
        return

    await state.clear()
    if db.get_user(callback.from_user.id) is None:
        db.create_user(callback.from_user.id, callback.from_user.full_name or "Admin")

    await answer_rich(callback.message, 
        "🛒 خرید اشتراک برای خودم\n\n🚀 دسته‌بندی سرویس مورد نظر را انتخاب کنید 👇",
        reply_markup=vip_categories_keyboard(),
    )
    await callback.answer()


@router.message(F.text == "🛒 خرید اشتراک برای خودم")
async def admin_buy_subscription_reply(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    if not db.is_orders_enabled():
        await answer_rich(message, "🔴 سفارشات در حال حاضر خاموش است.")
        return
    await state.clear()
    if db.get_user(message.from_user.id) is None:
        db.create_user(message.from_user.id, message.from_user.full_name or "Admin")
    await answer_rich(message, 
        "🛒 خرید اشتراک برای خودم\n\n🚀 دسته‌بندی سرویس مورد نظر را انتخاب کنید 👇",
        reply_markup=vip_categories_keyboard(),
    )


# ---------------------------------------------------------------------------
# 📤 ارسال کانفیگ — عکس کیوآرکد + لینک ساب (نام/حجم/مدت به‌صورت خودکار
# از روی خود لینک تشخیص داده می‌شود؛ اگر تشخیص خودکار جواب نداد، به‌صورت
# دستی از ادمین پرسیده می‌شود)
# ---------------------------------------------------------------------------
async def _start_send_flow(callback: types.CallbackQuery, state: FSMContext, uid: str,
                            target_config_id: int | None, order_id: int | None,
                            plan_order_id: int | None = None, hint: str = ""):
    await state.update_data(
        send_target_uid=uid,
        send_target_config_id=target_config_id,
        send_order_id=order_id,
        send_plan_order_id=plan_order_id,
        qr_file_id=None,
    )
    await state.set_state(AdminStates.waiting_send_qr_photo)
    await answer_rich(callback.message, 
        f"📤 ارسال کانفیگ برای کاربر {uid}{hint}\n\n📸 اول عکس کیوآرکد سرویس رو ارسال کن:"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sendvip_"))
async def send_config_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("sendvip_", "")
    uid, _, plan_order_id = raw.partition("|")
    await _start_send_flow(
        callback, state, uid, target_config_id=None, order_id=None,
        plan_order_id=int(plan_order_id) if plan_order_id else None,
    )




@router.message(AdminStates.waiting_send_qr_photo, F.photo)
async def send_config_qr_received(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(qr_file_id=file_id)
    await state.set_state(AdminStates.waiting_send_qr_link)
    await answer_rich(message, "🔗 حالا لینک ساب (Subscription) این سرویس رو ارسال کن:")


@router.message(AdminStates.waiting_send_qr_photo)
async def send_config_qr_wrong_format(message: types.Message):
    await answer_rich(message, "📸 لطفاً عکس کیوآرکد سرویس رو ارسال کن (نه متن).")


@router.message(AdminStates.waiting_send_qr_link)
async def send_config_link_received(message: types.Message, state: FSMContext):
    sub_link = (message.text or "").strip()
    if not sub_link.lower().startswith(("http://", "https://")):
        await answer_rich(message, "❌ این یک لینک معتبر نیست؛ لطفاً لینک ساب رو با http یا https ارسال کن:")
        return

    data = await state.get_data()
    order_id = data.get("send_order_id")
    order = db.get_custom_order(order_id) if order_id else None

    await answer_rich(message, "⏳ در حال تشخیص خودکار اطلاعات از روی لینک...")
    meta = await extract_meta(sub_link)
    userinfo = (meta or {}).get("userinfo") or {}
    fetched_name = (meta or {}).get("name")

    if order:
        # اطلاعات حجم/مدت از خود سفارش (که کاربر برایش پول پرداخت کرده) قابل‌اعتمادتر است
        volume_gb = order["volume_gb"]
        days = order["days"]
        name = fetched_name or order.get("custom_name") or "کاربر"
        await state.update_data(send_volume_gb=volume_gb, send_days=days, send_name=name, send_sub_link=sub_link)
        await _finalize_send(message, state)
        return

    volume_gb = _gb_from_bytes(userinfo.get("total"))
    days = days_remaining(userinfo.get("expire"))

    if volume_gb is not None and days is not None and fetched_name:
        await state.update_data(send_volume_gb=volume_gb, send_days=days, send_name=fetched_name, send_sub_link=sub_link)
        await _finalize_send(message, state)
        return

    # تشخیص خودکار کامل نبود؛ از ادمین می‌خواهیم دستی وارد کند
    await state.update_data(
        send_sub_link=sub_link,
        send_volume_gb=volume_gb,
        send_days=days,
        send_name=fetched_name,
    )
    await state.set_state(AdminStates.waiting_send_qr_manual)
    known = []
    if fetched_name:
        known.append(f"نام: {fetched_name}")
    if volume_gb is not None:
        known.append(f"حجم: {volume_gb} گیگ")
    if days is not None:
        known.append(f"مدت: {days} روز")
    known_text = ("\n✅ همین مقدار از روی لینک تشخیص داده شد: " + " | ".join(known)) if known else ""
    await answer_rich(message, 
        "⚠️ تشخیص خودکار کامل از روی این لینک ممکن نشد (احتمالاً این پنل هدر استاندارد ساب رو برنمی‌گردونه)."
        + known_text
        + "\n\nلطفاً این ۳ مورد رو هرکدام در یک خط، به همین ترتیب بفرست:\n"
        "نام کاربری سرویس\nحجم به گیگ (فقط عدد)\nمدت به روز (فقط عدد)\n\nمثال:\naminvpn1\n50\n30"
    )


@router.message(AdminStates.waiting_send_qr_manual)
async def send_config_manual_input(message: types.Message, state: FSMContext):
    lines = [l.strip() for l in (message.text or "").splitlines() if l.strip()]
    if len(lines) < 3:
        await answer_rich(message, "❌ باید دقیقاً ۳ خط بفرستی: نام / حجم (گیگ) / مدت (روز). دوباره امتحان کن:")
        return

    name = lines[0]
    volume_gb = parse_int_in_range(lines[1], 0, 100000)
    days = parse_int_in_range(lines[2], 0, 100000)
    if volume_gb is None or days is None:
        await answer_rich(message, "❌ خط دوم و سوم باید عدد ۰ یا بیشتر باشند (۰ = نامحدود). دوباره امتحان کن:")
        return

    await state.update_data(send_name=name, send_volume_gb=volume_gb, send_days=days)
    await _finalize_send(message, state)


async def _finalize_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("send_target_uid")
    qr_file_id = data.get("qr_file_id")
    sub_link = data.get("send_sub_link")
    target_config_id = data.get("send_target_config_id")
    order_id = data.get("send_order_id")
    plan_order_id = data.get("send_plan_order_id")
    name = data.get("send_name") or "کاربر"

    # 🐛 فیکس: قبلاً متن تحویلی همیشه ثابت "نامحدود" نشان می‌داد و سقف کاربر (HWID Limit) واقعی پلن را نادیده می‌گرفت. حالا اگر این ارسال از روی یک سفارش پلن VIP باشد سقف کاربر همان پلن خوانده و نمایش داده می‌شود (0 = نامحدود).
    user_limit = None
    if plan_order_id:
        plan_order = db.get_order(plan_order_id)
        if plan_order and plan_order.get("plan_key"):
            order_plan = db.get_effective_plan(plan_order["plan_key"])
            if order_plan:
                user_limit = order_plan.get("user_limit")
    volume_gb = data.get("send_volume_gb")
    days = data.get("send_days")

    user = db.get_user(uid)
    if user is None or qr_file_id is None or sub_link is None:
        await answer_rich(message, "❌ مشکلی پیش آمد؛ لطفاً از ابتدا (📸 عکس کیوآرکد) دوباره امتحان کن.")
        await state.clear()
        return

    volume_text = _format_volume_gb_label(volume_gb)
    days_text = f"{days} روز" if days is not None else "نامحدود"
    plan_name_for_delivery = None
    if plan_order_id:
        plan_order = db.get_order(plan_order_id)
        if plan_order and plan_order.get("plan_key"):
            plan_obj = db.get_effective_plan(plan_order["plan_key"])
            if plan_obj:
                plan_name_for_delivery = plan_obj.get("name")
    delivery_label = (
        _delivery_service_label(
            plan_name_for_delivery,
            volume_gb,
            days,
            user_limit,
            plan_order.get("plan_key") if plan_order_id and plan_order else None,
        )
        if plan_name_for_delivery or plan_order_id
        else _delivery_service_label(name, volume_gb, days, None)
    )
    is_test_delivery = bool(plan_order_id and plan_order and plan_order.get("plan_key") == FREE_TEST_PLAN_KEY)
    delivery_text_key = "service_delivery_test_text" if is_test_delivery else "service_delivery_text"
    caption = user_text(delivery_text_key, service_label=_english_digits(delivery_label), link=sub_link)

    expiry_date = None
    if days is not None:
        expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d")

    encrypted = crypto.encrypt_config(sub_link)
    plan_name = f"{name} | {volume_text} | {days_text}"

    if target_config_id:
        target_cfg = db.get_config_by_id(int(target_config_id))
        db.update_config(target_config_id, plan_name, encrypted, expiry=expiry_date, qr_file_id=qr_file_id, panel_id=(target_cfg.get("panel_id") if target_cfg else None))
    else:
        manual_plan_key = plan_order.get("plan_key") if plan_order_id and plan_order else None
        plan_for_category = db.get_vip_plan(manual_plan_key) if manual_plan_key else None
        db.add_config(
            user["id"], plan_name, encrypted, expiry=expiry_date, config_type="vip",
            qr_file_id=qr_file_id, category_id=(plan_for_category or {}).get("category_id"),
            plan_key=(plan_order.get("plan_key") if plan_order_id and plan_order else None),
        )

    if order_id:
        db.set_custom_order_status(order_id, "fulfilled")
    if plan_order_id:
        db.set_order_status(plan_order_id, "fulfilled")

    try:
        await send_notification_sticker(message.bot, int(uid), "notif_service_delivery")
        await send_photo_rich(message.bot, 
            int(uid),
            qr_file_id,
            caption=caption,
            reply_markup=config_delivery_keyboard(is_test=is_test_delivery),
        )
        # منوی پایینی دائمی کاربر نباید بعد از تحویل سرویس مخفی شود.
        db.set_keyboard_hidden(int(uid), False)
        await send_rich(message.bot, int(uid), "⬇️ منوی اصلی شما همچنان در دسترس است.", reply_markup=main_reply_keyboard())
        await answer_rich(message, "✅ کانفیگ برای کاربر ارسال شد.")
        await _notify_main_admin_action(message.bot, message.from_user, "ارسال کانفیگ", uid, plan_name)
    except Exception as e:
        await answer_rich(message, f"⚠️ سرویس ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {e}")

    await _log_fulfilled_order(
        message.bot, user, plan_order_id=plan_order_id, custom_order_id=order_id,
        target_config_id=target_config_id, service_name=name,
        package_text=f"{volume_text} | {days_text}", expiry_text=expiry_date or "نامحدود",
    )

    await state.clear()


# ---------------------------------------------------------------------------
# 🎮 ارسال کانفیگ گیمینگ (WireGuard) — بدون کیوآرکد/mirroring/تمدید:
# ادمین ابتدا شناسه سرویس و لینک ساب را وارد می‌کند، سپس هر تعداد فایل
# .conf که بخواهد آپلود می‌کند (هرکدام می‌تواند کپشن/لوکیشن جدا داشته باشد)
# و در پایان با زدن دکمه «✅ پایان ارسال فایل‌ها» همه‌ی فایل‌ها یک‌جا برای
# کاربر ارسال می‌شوند - دقیقاً مطابق فرمت فایلی که در کانال نمونه دیده می‌شود.
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 📝 مدیریت جامع متن‌های کاربر و اعلان‌ها
# همه‌ی متن‌های قابل ویرایش در text_catalog.py تعریف شده‌اند.
TEXT_KEYS = TEXTS


def _chunk2(items):
    rows = []
    for i in range(0, len(items), 2):
        rows.append(items[i:i + 2])
    return rows


_TEXT_EDITOR_PAGE_SIZE = 30  # 15 ردیف دو ستونه؛ از ساخت کیبوردهای خیلی بلند جلوگیری می‌کند.

def _text_manager_keyboard(category: str | None = None, page: int = 0):
    """مدیریت متن‌ها با صفحه‌بندی.

    تلگرام روی تعداد/ارتفاع کیبوردهای Inline محدودیت‌های عملی دارد؛ بنابراین
    دسته‌های بزرگ مثل «📦 سرویس‌های من» دیگر ۸۶ دکمه را یک‌جا نمی‌سازند.
    هر صفحه حداکثر ۳۰ مورد دارد و دکمه‌های قبل/بعد پایین لیست قرار می‌گیرند.
    """
    buttons = []
    if category is None:
        categories = list(TEXT_CATEGORIES.keys())
        for i in range(0, len(categories), 2):
            row = []
            for cat in categories[i:i + 2]:
                count = len(TEXT_CATEGORIES[cat])
                row.append(InlineKeyboardButton(
                    text=f"📝 {cat.split(' ', 1)[-1]} ({count})",
                    callback_data=f"textcat_{categories.index(cat)}",
                    style="primary",
                ))
            buttons.append(row)
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
        return types.InlineKeyboardMarkup(inline_keyboard=buttons)

    items = TEXT_CATEGORIES.get(category, [])
    total_pages = max(1, (len(items) + _TEXT_EDITOR_PAGE_SIZE - 1) // _TEXT_EDITOR_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * _TEXT_EDITOR_PAGE_SIZE
    page_items = items[start:start + _TEXT_EDITOR_PAGE_SIZE]

    delivery_labels = {
        "service_delivery_text": "✏️ تغییر متن تحویل سرویس (بسته‌ها)",
        "service_delivery_apps_button": "📱 دکمه لینک برنامه‌ها (بسته‌ها)",
        "service_delivery_connection_button": "🔧 دکمه اتصال کانفینگ (بسته‌ها)",
        "service_delivery_test_text": "✏️ تغییر متن تحویل سرویس (تست)",
        "service_delivery_test_apps_button": "📱 دکمه لینک برنامه‌ها (تست)",
        "service_delivery_test_connection_button": "🔧 دکمه اتصال کانفینگ (تست)",
    }
    for i in range(0, len(page_items), 2):
        row = []
        for key, default in page_items[i:i + 2]:
            value = user_text(key, default).replace("\n", " ")[:22]
            label = delivery_labels.get(key, f"✏️ {key[:14]} | {value}")
            if key in delivery_labels:
                label = f"✏️ {label}"
            row.append(InlineKeyboardButton(
                text=label,
                callback_data=f"textedit_{key}",
                style="primary",
            ))
        buttons.append(row)

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="⬅️ صفحه قبل",
                callback_data=f"textcatpage_{list(TEXT_CATEGORIES.keys()).index(category)}_{page - 1}",
                style="primary",
            ))
        nav.append(InlineKeyboardButton(
            text=f"📄 {page + 1}/{total_pages}",
            callback_data="noop",
            style="primary",
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="➡️ صفحه بعد",
                callback_data=f"textcatpage_{list(TEXT_CATEGORIES.keys()).index(category)}_{page + 1}",
                style="primary",
            ))
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به دسته‌ها", callback_data="admin_texts", style="primary")])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "admin_texts")
async def admin_texts(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    await state.clear()
    await edit_rich(callback.message, 
        "📝 مدیریت جامع متن‌های کاربر و اعلان‌ها\n\nیک بخش را انتخاب کنید:",
        reply_markup=_text_manager_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("textcatpage_"))
async def admin_text_category_page(callback: types.CallbackQuery, state: FSMContext):
    """جابجایی بین صفحات یک دسته‌ی بزرگ در ویرایشگر متن."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    try:
        _, index_raw, page_raw = callback.data.split("_", 2)
        index = int(index_raw)
        page = int(page_raw)
        category = list(TEXT_CATEGORIES.keys())[index]
    except Exception:
        await callback.answer("❌ صفحه‌ی متن پیدا نشد.", show_alert=True); return
    await state.clear()
    note = "\n\n🔒 متن انقضای فاکتور کارت‌به‌کارت سیستمی است و از اینجا قابل تغییر نیست." if "فاکتور کارت‌به‌کارت" in category else ""
    await edit_rich(
        callback.message,
        f"📝 {category}\n\nمتن موردنظر را برای ویرایش انتخاب کنید:{note}",
        reply_markup=_text_manager_keyboard(category, page=page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("textcat_"))
async def admin_text_category(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    try:
        index = int(callback.data.replace("textcat_", "", 1))
        category = list(TEXT_CATEGORIES.keys())[index]
    except Exception:
        await callback.answer("❌ بخش متن پیدا نشد.", show_alert=True); return
    await state.clear()
    note = "\n\n🔒 متن انقضای فاکتور کارت‌به‌کارت سیستمی است و از اینجا قابل تغییر نیست." if "فاکتور کارت‌به‌کارت" in category else ""
    await edit_rich(callback.message, 
        f"📝 {category}\n\nمتن موردنظر را برای ویرایش انتخاب کنید:{note}",
        reply_markup=_text_manager_keyboard(category, page=0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("textedit_"))
async def admin_text_edit_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    key = callback.data.replace("textedit_", "", 1)
    if key not in TEXT_KEYS:
        await callback.answer("❌ متن پیدا نشد.", show_alert=True); return
    await state.update_data(text_override_key=key)
    await state.set_state(AdminStates.waiting_text_override_value)
    current = user_text(key, TEXT_KEYS[key])
    placeholder_note = "\n\nمتغیرهای قابل استفاده: " + ", ".join("{" + x + "}" for x in _template_vars(TEXT_KEYS[key])) if _template_vars(TEXT_KEYS[key]) else ""
    lock_note = "\n\n🔒 توجه: جمله‌ی زمان انقضای فاکتور در کد سیستمی تولید می‌شود و جزو این متن نیست." if key.startswith("invoice_") else ""
    await edit_rich(callback.message, 
        f"✏️ ویرایش متن: {key}\n\nمتن فعلی:\n{current}{placeholder_note}{lock_note}\n\nمتن جدید را ارسال کنید:",
        reply_markup=admin_back_button(),
    )
    await callback.answer()


def _template_vars(template: str) -> list[str]:
    import string
    vars_found = []
    for _, field, _, _ in string.Formatter().parse(template):
        if field and field not in vars_found:
            vars_found.append(field.split("!", 1)[0].split(":", 1)[0])
    return vars_found


@router.message(AdminStates.waiting_text_override_value)
async def admin_text_edit_save(message: types.Message, state: FSMContext):
    data = await state.get_data(); key = data.get("text_override_key")
    if key not in TEXT_KEYS:
        await state.clear(); await answer_rich(message, "❌ عملیات منقضی شد."); return
    # متن را دقیقاً همان‌طور که Telegram فرستاده نگه می‌داریم.
    # offset/length مربوط به MessageEntity (خصوصاً custom_emoji) بر اساس همین
    # متن و UTF-16 محاسبه شده؛ strip کردن قبل از ذخیره می‌تواند entityها را جابه‌جا کند.
    value = message.text or ""
    if not value.strip():
        await answer_rich(message, "❌ متن نمی‌تواند خالی باشد:"); return
    # 🆕 فیکس: اگر این متن از سقف مجاز تلگرام برای متن پیام (۴۰۹۶ کاراکتر) بلندتر ذخیره شود، بعداً هر‌بار که این متن (مثلاً برای فاکتور، پیام راهنما و غیره) برای کاربر فرستاده شود، تلگرام خطای «MESSAGE_TOO_LONG» برمی‌گرداند. اینجا قبل از ذخیره‌شدن گرفته می‌شود (علاوه بر محافظتی که در show_menu_with_sticker اضافه شد).
    if len(value) > 3800:
        await answer_rich(message, 
            f"❌ این متن خیلی طولانی است ({len(value)} کاراکتر) و ممکن است تلگرام آن را رد کند (سقف تلگرام: ۴۰۹۶ کاراکتر). لطفاً متن کوتاه‌تری بفرست:"
        )
        return
    # جلوگیری از خراب‌شدن فاکتور با حذف متغیرهای سیستمی
    required = set(_template_vars(TEXT_KEYS[key]))
    if required:
        supplied = set(_template_vars(value))
        missing = required - supplied
        if missing:
            await answer_rich(message, "❌ این متغیرهای ضروری حذف شده‌اند: " + ", ".join("{" + x + "}" for x in sorted(missing)))
            return
    # تمام entityهای خود Telegram را ذخیره می‌کنیم؛ از جمله custom_emoji.
    # این ID را جداگانه هم برای دکمه‌ها نگه می‌داریم، اما منبع اصلی نمایش متن
    # همان entities_json است.
    entities = serialize_message_entities(getattr(message, "entities", None))
    custom_emoji_id = next(
        (str(entity["custom_emoji_id"]) for entity in entities
         if entity.get("type") == "custom_emoji" and entity.get("custom_emoji_id")),
        None,
    )
    db.set_text_override(key, value, entities=entities)
    db.set_button_custom_emoji_id(key, custom_emoji_id)
    refresh_user_text(key)
    await state.clear()
    category = CATEGORY_BY_KEY.get(key)
    page = 0
    if category:
        items = TEXT_CATEGORIES.get(category, [])
        for idx, (item_key, _default) in enumerate(items):
            if item_key == key:
                page = idx // _TEXT_EDITOR_PAGE_SIZE
                break
    await answer_rich(message, "✅ متن ذخیره شد.", reply_markup=_text_manager_keyboard(category, page=page))


# 📥 صف سفارشات — لیست خریدهای تأییدشده‌ای که هنوز کانفیگ‌شان ارسال نشده،
# چه خرید پلن معمولی (VIP/گیمینگ) و چه سفارش سفارشی/تمدید.
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_orders_off")
async def admin_orders_off(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    db.set_orders_enabled(False)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await answer_rich(callback.message, f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await send_rich(callback.bot, 
                int(u["telegram_id"]),
                db.get_text_override("orders_closed", "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.") + "\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد.",
            )
            sent += 1
        except Exception:
            failed += 1
    await edit_rich(status_msg, f"🔴 بخش سفارشات خاموش شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await edit_rich(callback.message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "admin_orders_on")
async def admin_orders_on(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    db.set_orders_enabled(True)
    users = db.get_all_users()
    sent, failed = 0, 0
    status_msg = await answer_rich(callback.message, f"⏳ در حال اطلاع‌رسانی به {len(users)} کاربر...")
    for u in users:
        try:
            await send_rich(callback.bot, 
                int(u["telegram_id"]),
                db.get_text_override("orders_opened", "🟢 ربات مجدداً فعال شد!") + "\n\nبا زدن /start می‌توانید دوباره سفارش ثبت کنید.",
            )
            sent += 1
        except Exception:
            failed += 1
    await edit_rich(status_msg, f"🟢 بخش سفارشات روشن شد. اطلاع‌رسانی به {sent} نفر موفق، {failed} نفر ناموفق.")
    await edit_rich(callback.message, "👨‍💻 پنل مدیریت:", reply_markup=_admin_panel_kb_for(callback.from_user.id))
    await callback.answer()


async def _render_order_queue(callback: types.CallbackQuery):
    pending = db.get_pending_orders(limit=25)
    for o in pending:
        u = db.get_user_by_id(o["user_id"])
        o["telegram_id"] = u["telegram_id"] if u else ""


    if not pending:
        text = "📦 سفارش‌های در انتظار\n\n✅ در حال حاضر هیچ سفارش در انتظار ارسالی وجود ندارد."
    else:
        text = f"📦 سفارش‌های در انتظار — {len(pending)} مورد در انتظار ارسال\n\nروی هرکدوم بزن تا مسیر ارسالش شروع بشه 👇"

    await edit_rich(callback.message, text, reply_markup=admin_order_queue_keyboard(pending))


@router.callback_query(F.data == "admin_request_queue")
async def admin_request_queue(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_count = len(db.get_pending_orders(limit=200))
    receipt_count = len(db.get_pending_receipts(limit=200))
    await edit_rich(callback.message, 
        "📥 صف درخواست‌ها\n\nچه چیزی رو می‌خوای بررسی کنی؟ 👇",
        reply_markup=admin_request_queue_menu(order_count, receipt_count),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_order_queue")
async def admin_order_queue(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_order_queue(callback)
    await callback.answer()


# ---------------------------------------------------------------------------
# 🧾 رسیدهای در انتظار تایید — همه‌ی رسیدهای شارژ کیف پول و خرید کارت‌به‌کارت
# (پلن ثابت + بساز سرویس خودت) که هنوز ادمین تایید/رد نکرده، در یک لیست.
# تایید/رد از همینجا دقیقاً همان مسیر همیشگی (پیام فوروارد‌شده در چت ادمین)
# را صدا می‌زند، فقط یک راه میان‌بر برای دیدن همه‌چیز یک‌جاست.
# ---------------------------------------------------------------------------
async def _render_pending_receipts(callback: types.CallbackQuery):
    receipts = db.get_pending_receipts(limit=25)

    total = len(receipts)
    if total == 0:
        text = "🧾 رسیدهای در انتظار تایید\n\n✅ در حال حاضر هیچ رسید در انتظار تاییدی وجود ندارد."
    else:
        text = f"🧾 رسیدهای در انتظار تایید — {total} مورد\n\nروی ✅ برای تایید یا ❌ برای رد بزن 👇"

    await edit_rich(callback.message, text, reply_markup=admin_pending_receipts_keyboard(receipts))


@router.callback_query(F.data == "admin_pending_receipts")
async def admin_pending_receipts(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_pending_receipts(callback)
    await callback.answer()


@router.callback_query(F.data == "clearreceipts_confirm")
async def clear_receipts_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "⚠️ مطمئنی می‌خوای همه‌ی رسیدهای این لیست رو بررسی‌شده علامت بزنی؟\n"
        "(توجه: این کار فقط لیست رو خالی می‌کنه؛ اگر هنوز به کاربری تایید/رد اعلام نکردی، "
        "پیام اصلی رسیدش همچنان توی چتت هست و باید از همونجا اقدام کنی.)",
        reply_markup=admin_clear_receipts_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "clearreceipts_do")
async def clear_receipts_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    db.dismiss_all_pending_receipts()
    await _render_pending_receipts(callback)
    await callback.answer("🧹 لیست خالی شد.")


@router.callback_query(F.data.startswith("dismissorder_"))
async def dismiss_order(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.replace("dismissorder_", ""))
    db.set_order_status(order_id, "dismissed")
    await _render_order_queue(callback)
    await callback.answer("🗑 از صف پاک شد.")




@router.callback_query(F.data == "clearorders_confirm")
async def clear_orders_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "⚠️ مطمئنی می‌خوای همه‌ی سفارش‌های این صف رو پاک کنی؟\n"
        "(این کار فقط سفارش‌ها رو از صف حذف می‌کنه؛ اگه کانفیگ کسی رو نفرستادی، دیگه اینجا یادآوریش نمی‌مونه.)",
        reply_markup=admin_clear_orders_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "clearorders_do")
async def clear_orders_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    pending = db.get_pending_orders(limit=1000)
    for o in pending:
        db.set_order_status(o["id"], "dismissed")
    await _render_order_queue(callback)
    await callback.answer(f"🧹 {len(pending)} سفارش پاک شد.")


# ---------------------------------------------------------------------------
# 📦 مشاهده و مدیریت سرویس‌های یک کاربر (لیست/حذف نرم/ادیت لینک ساب و کیوآرکد/
# مدیریت فایل‌های گیمینگ) — همه از طریق «🔍 جستجوی حرفه‌ای»
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("useractions_"))
async def admin_user_actions_back(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("useractions_", "")
    await _reply_with_user_actions(
        callback.message, f"👤 مدیریت کاربر {uid}", uid, db.is_user_blocked(uid), edit=True
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# ✉️ پیام خصوصی ادمین به یک کاربر خاص (متن/عکس/فیلم/فوروارد)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pm_"))
async def admin_pm_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("pm_", "")
    await state.set_state(AdminStates.waiting_pm_message)
    await state.update_data(pm_target_uid=uid)
    await edit_rich(callback.message, 
        f"✉️ پیامی که می‌خواهید به کاربر {uid} ارسال شود را بفرستید (متن، عکس، فیلم یا فوروارد هم پذیرفه است):",
        reply_markup=admin_pm_cancel_keyboard(uid),
    )
    await callback.answer()


@router.message(AdminStates.waiting_pm_message)
async def admin_pm_send(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    uid = data.get("pm_target_uid")
    if not uid:
        await state.clear()
        return
    try:
        await send_notification_sticker(message.bot, int(uid), "notif_personal_message")
        await message.bot.copy_message(int(uid), message.chat.id, message.message_id)
        await _reply_with_user_actions(message, "✅ پیام خصوصی برای کاربر ارسال شد.", uid, db.is_user_blocked(uid), edit=False)
    except Exception:
        await _reply_with_user_actions(message, "❌ ارسال پیام ناموفق بود (ممکن است کاربر ربات را مسدود کرده باشد).", uid, db.is_user_blocked(uid), edit=False)
    await state.clear()


# ---------------------------------------------------------------------------
# 🚫 مسدود/رفع مسدودیت کاربر — کاربر‌های مسدود نمی‌توانند از ربات استفاده کنند
# (بررسی در middleware/start.py)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("toggleblock_"))
async def admin_toggle_block(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    uid = callback.data.replace("toggleblock_", "")
    currently_blocked = db.is_user_blocked(uid)
    db.set_user_blocked(uid, not currently_blocked)

    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return
    stats = db.get_referral_stats(user["id"])
    text = (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"💛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛍 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}\n\n"
        + ("🚫 وضعیت: مسدود" if not currently_blocked else "✅ وضعیت: رفع مسدودیت شد")
    )
    await _reply_with_user_actions(callback.message, text, user["telegram_id"], not currently_blocked, edit=True)
    await callback.answer("🚫 کاربر مسدود شد." if not currently_blocked else "✅ مسدودیت برداشته شد.")


@router.callback_query(F.data.startswith("deleteuser_"))
async def admin_delete_user_ask(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "users"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("deleteuser_", "", 1)
    if uid == str(ADMIN_ID) or db.is_sub_admin(uid):
        await callback.answer("⛔ حساب ادمین قابل حذف نیست.", show_alert=True)
        return
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    await edit_rich(callback.message, 
        f"⚠️ حذف کامل کاربر\n\n"
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        "این عملیات فقط اطلاعات کاربر را از دیتابیس ربات حذف می‌کند.\n"
        "هیچ درخواستی به پنل Marzban یا PasarGuard ارسال نمی‌شود.\n\n"
        "❗️این عملیات قابل بازگشت نیست.",
        reply_markup=admin_delete_user_confirm_keyboard(uid),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deleteuserconfirm_"))
async def admin_delete_user_confirm(callback: types.CallbackQuery):
    if not _admin_perm(callback.from_user.id, "users"):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("deleteuserconfirm_", "", 1)
    if uid == str(ADMIN_ID) or db.is_sub_admin(uid):
        await callback.answer("⛔ حساب ادمین قابل حذف نیست.", show_alert=True)
        return
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر قبلاً حذف شده یا یافت نشد.", show_alert=True)
        return

    try:
        deleted = db.delete_user_from_bot(uid)
    except Exception:
        logger.exception("خطا در حذف کامل کاربر از دیتابیس ربات: %s", uid)
        await callback.answer("❌ حذف کاربر انجام نشد؛ اطلاعات ربات تغییر نکرد.", show_alert=True)
        return

    if not deleted:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    await edit_rich(callback.message, 
        f"✅ کاربر «{user['name']}» با شناسه {uid} فقط از دیتابیس ربات حذف شد.\n\n"
        "🔒 هیچ تغییری در Marzban یا PasarGuard انجام نشد.",
        reply_markup=admin_back_button(),
    )
    await callback.answer("🗑 کاربر از ربات حذف شد.", show_alert=True)
    await _notify_main_admin_action(
        callback.bot,
        callback.from_user,
        "حذف کامل کاربر از ربات",
        uid,
        f"name={user['name']}",
    )


@router.callback_query(F.data.startswith("svcs_"))
async def admin_view_user_services(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    uid = callback.data.replace("svcs_", "")
    user = db.get_user(uid)
    if user is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    configs = db.get_configs(user["id"], include_deleted=True)
    await enrich_configs_with_subscription_names(configs)
    for cfg in configs:
        try: cfg["_live_status"] = await get_live_service_status(cfg)
        except Exception: cfg["_live_status"] = None
    if not configs:
        await edit_rich(callback.message, 
            f"📦 کاربر {uid} هنوز هیچ سرویسی نداره.",
            reply_markup=admin_back_button(),
        )
    else:
        await edit_rich(callback.message, 
            f"📦 سرویس‌های کاربر {uid}\n\n❌ یعنی توسط خودِ کاربر حذف شده (ولی برای شما همچنان قابل‌مشاهده‌ست).\n\nروی هرکدوم بزن برای جزئیات و مدیریت 👇",
            reply_markup=admin_services_list_keyboard(configs, uid),
        )
    await callback.answer()


def _remaining_days_from_date_str(date_str) -> int | None:
    """تعداد روز باقی‌مانده تا انقضا را از یک رشته تاریخ به فرمت YYYY-MM-DD محاسبه می‌کند."""
    if not date_str:
        return None
    try:
        exp_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        delta = exp_date - now_tehran_naive()
        return delta.days
    except Exception:
        return None


async def _service_detail_text(cfg: dict) -> str:
    """
    توجه: قبلاً این متن با parse_mode="Markdown" (نسخه‌ی قدیمی مارک‌داون
    تلگرام) فرستاده می‌شد و cfg['plan'] بدون هیچ escape‌ای مستقیم داخل متن
    قرار می‌گرفت. نسخه‌ی قدیمی Markdown تلگرام امکان escape کردن کاراکترهای
    خاص رو نداره؛ پس اگر نام پلن یک زیرخط (_) تک و جفت‌نشده داشت (مثل
    "Businesss_vpn - 1090174")، پارسر اون رو شروع ایتالیک در نظر می‌گرفت و
    چون بسته نمی‌شد، کل درخواست ویرایش پیام با خطای "can't parse entities"
    رد می‌شد و صفحه‌ی جزئیات سرویس اصلاً نمایش داده نمی‌شد.
    راه‌حل: استفاده از HTML به‌جای Markdown، چون HTML یک تابع escape رسمی و
    قابل‌اعتماد داره (html.escape) و این مشکل اصلاً پیش نمیاد.
    """
    icon = "🚀" if cfg.get("type", "vip") == "vip" else "🎮"
    live = await get_live_service_status(cfg) if not cfg.get("deleted") else None
    status = "❌ حذف‌شده (توسط کاربر یا ادمین)" if cfg.get("deleted") else ("🟢 فعال (لحظه‌ای از پنل)" if live == "active" else ("🔴 منقضی (لحظه‌ای از پنل)" if live == "expired" else "⚪ وضعیت لحظه‌ای نامشخص"))
    try:
        sub_preview = crypto.decrypt_config(cfg["config"])
    except Exception:
        sub_preview = "⚠️ خطا در رمزگشایی"

    plan_safe = html.escape(str(cfg.get("_display_name") or cfg.get("plan", "")))
    created_safe = html.escape(str(cfg.get("created_at", "")))
    sub_preview_safe = html.escape(sub_preview)

    text = (
        f"{icon} {plan_safe}\n\n"
        f"📌 وضعیت: {status}\n"
        f"📆 تاریخ ایجاد: {created_safe}\n"
    )
    if cfg.get("service_id"):
        text += f"🆔 شناسه سرویس: {html.escape(str(cfg['service_id']))}\n"

    usage = None
    sub_url = sub_preview if sub_preview.lower().startswith(("http://", "https://")) else None
    if cfg.get("type", "vip") == "vip" and sub_url:
        try:
            usage = await fetch_subscription_info(sub_url)
        except Exception:
            usage = None

    if usage:
        total = usage.get("total")
        used = (usage.get("upload") or 0) + (usage.get("download") or 0)
        remaining_bytes = (total - used) if total else None

        text += "\n📊 وضعیت مصرف (لحظه‌ای):\n"
        if total:
            text += f"   • حجم کل: {html.escape(format_bytes(total))}\n"
        text += f"   • مصرف‌شده: {html.escape(format_bytes(used))}\n"
        if remaining_bytes is not None:
            text += f"   • باقی‌مانده: {html.escape(format_bytes(remaining_bytes))}\n"
        if total:
            percent = min(100, round(used / total * 100))
            text += f"\n{usage_bar(percent)} {percent}٪ مصرف شده\n"
        text += f"\n⏰ تاریخ انقضا: {html.escape(format_expire(usage.get('expire')))}\n"
        remaining_days = days_remaining(usage.get("expire"))
        if remaining_days is not None:
            text += "⛔️ منقضی شده\n" if remaining_days <= 0 else f"⌛️ زمان باقی‌مانده: {remaining_days} روز\n"
    elif cfg.get("expiry"):
        text += f"⏰ انقضا: {html.escape(str(cfg['expiry']))}\n"
        remaining_days = _remaining_days_from_date_str(cfg.get("expiry"))
        if remaining_days is not None:
            if remaining_days <= 0:
                text += "⌛️ زمان باقی‌مانده: ⛔️ منقضی شده\n"
            else:
                text += f"⌛️ زمان باقی‌مانده: {remaining_days} روز\n"

    text += f"\n🔗 لینک ساب فعلی:\n<code>{sub_preview_safe}</code>\n"
    if cfg.get("type", "vip") == "vip":
        text += f"\n🎫 کیوآرکد: {'ثبت شده ✅' if cfg.get('qr_file_id') else 'ثبت نشده ❌'}"
    else:
        files = db.get_gaming_files(cfg["id"])
        text += f"\n📁 تعداد فایل کانفیگ: {len(files)}"
    return text


async def _render_service_detail(callback: types.CallbackQuery, cfg_id: int):
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None:
        await callback.answer("❌ سرویس یافت نشد.", show_alert=True)
        return
    owner = db.get_user_by_id(cfg["user_id"])
    uid = owner["telegram_id"] if owner else ""

    await enrich_configs_with_subscription_names([cfg])
    text = await _service_detail_text(cfg)
    kb = admin_service_detail_keyboard(cfg, uid)
    # -----------------------------------------------------------------
    # علت اصلی خطای "خطایی پیش آمد..." روی این صفحه: نام پلن (cfg['plan'])
    # می‌تونه هرچیزی باشه (مثلاً چیزی شبیه "Businesss_vpn - 1090174") و اگر
    # داخلش یک زیرخط (_) تکی و جفت‌نشده باشه، پارسر Markdown قدیمی تلگرام
    # اون رو شروع ایتالیک در نظر می‌گیره و چون بسته نمی‌شه، کل پیام با خطای
    # "can't parse entities" رد می‌شه. قبلاً هیچ try/except اینجا نبود، پس
    # این خطا مستقیم می‌رفت به هندلر سراسری و کاربر فقط "خطایی پیش آمد" رو
    # می‌بیند بدون اینکه جزئیات سرویس اصلاً نمایش داده بشه. حالا اگر مارک‌داون
    # شکست بخوره، همون متن رو بدون فرمت (parse_mode=None) دوباره می‌فرستیم.
    # -----------------------------------------------------------------
    try:
        await edit_rich(callback.message, text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            logger.exception("خطا در نمایش جزئیات سرویس ادمین برای cfg_id=%s", cfg_id)
            try:
                await edit_rich(callback.message, text, parse_mode=None, reply_markup=kb)
            except Exception:
                logger.exception("خطا در fallback بدون فرمت برای جزئیات سرویس cfg_id=%s", cfg_id)
                await callback.answer("❌ خطا در نمایش جزئیات سرویس. دوباره تلاش کنید.", show_alert=True)
                return
    await callback.answer()


@router.callback_query(F.data.startswith("svcdetail_"))
async def admin_service_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcdetail_", ""))
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcdelete_"))
async def admin_service_delete(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcdelete_", ""))
    db.set_config_deleted(cfg_id, True)
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcrestore_"))
async def admin_service_restore(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcrestore_", ""))
    db.set_config_deleted(cfg_id, False)
    await _render_service_detail(callback, cfg_id)


@router.callback_query(F.data.startswith("svcpurge_"))
async def admin_service_purge_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcpurge_", ""))
    await edit_rich(callback.message, 
        "⚠️ این کار غیرقابل بازگشته و کل اطلاعات این سرویس (شامل فایل‌های گیمینگ) برای همیشه پاک می‌شه.\n\nمطمئنی؟",
        reply_markup=admin_purge_confirm_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("svcpurgeconfirm_"))
async def admin_service_purge_apply(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcpurgeconfirm_", ""))
    cfg = db.get_config_by_id(cfg_id)
    owner = db.get_user_by_id(cfg["user_id"]) if cfg else None
    uid = owner["telegram_id"] if owner else ""
    db.delete_config_permanently(cfg_id)
    await edit_rich(callback.message, "✅ سرویس برای همیشه حذف شد.", reply_markup=admin_back_button())
    await callback.answer()


@router.callback_query(F.data.startswith("svcedit_link_"))
async def admin_service_edit_link_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcedit_link_", ""))
    await state.update_data(edit_config_id=cfg_id)
    await state.set_state(AdminStates.waiting_edit_sublink)
    await answer_rich(callback.message, "🔗 لینک ساب جدید این سرویس رو ارسال کن:")
    await callback.answer()


@router.message(AdminStates.waiting_edit_sublink)
async def admin_service_edit_link_apply(message: types.Message, state: FSMContext):
    new_link = (message.text or "").strip()
    if not new_link.lower().startswith(("http://", "https://")):
        await answer_rich(message, "❌ این یک لینک معتبر نیست؛ لطفاً لینک رو با http یا https ارسال کن:")
        return

    data = await state.get_data()
    cfg_id = data.get("edit_config_id")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if cfg is None:
        await answer_rich(message, "❌ سرویس یافت نشد.")
        await state.clear()
        return

    db.update_config_link(cfg_id, crypto.encrypt_config(new_link))
    await answer_rich(message, "✅ لینک ساب سرویس بروزرسانی شد.", reply_markup=admin_back_button())
    await state.clear()


@router.callback_query(F.data.startswith("svcedit_qr_"))
async def admin_service_edit_qr_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    cfg_id = int(callback.data.replace("svcedit_qr_", ""))
    await state.update_data(edit_config_id=cfg_id)
    await state.set_state(AdminStates.waiting_edit_qr)
    await answer_rich(callback.message, "🖼 عکس کیوآرکد جدید این سرویس رو ارسال کن:")
    await callback.answer()


@router.message(AdminStates.waiting_edit_qr, F.photo)
async def admin_service_edit_qr_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cfg_id = data.get("edit_config_id")
    cfg = db.get_config_by_id(cfg_id) if cfg_id else None
    if cfg is None:
        await answer_rich(message, "❌ سرویس یافت نشد.")
        await state.clear()
        return

    db.set_config_qr(cfg_id, message.photo[-1].file_id)
    await answer_rich(message, "✅ عکس کیوآرکد سرویس بروزرسانی شد.", reply_markup=admin_back_button())
    await state.clear()


@router.message(AdminStates.waiting_edit_qr)
async def admin_service_edit_qr_wrong_format(message: types.Message):
    await answer_rich(message, "📸 لطفاً عکس کیوآرکد رو ارسال کن (نه متن).")














# ---------------------------------------------------------------------------
# 🎟 مدیریت تخفیف (ساخت کد تخفیف به‌صورت گام‌به‌گام)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_discount")
async def admin_discount_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    discounts = db.get_all_discounts()
    if not discounts:
        text = "🎟 هیچ کد تخفیفی هنوز ثبت نشده.\n\nبرای ساخت کد جدید، دکمه‌ی زیر را بزنید 👇"
    else:
        text = "🎟 کدهای تخفیف فعال:\n\nبرای مشاهده و ویرایش جزئیات هر کد، روی آن بزنید 👇"

    await edit_rich(callback.message, text, reply_markup=admin_discount_menu(discounts))
    await callback.answer()


def _discount_detail_text(d: dict) -> str:
    if d.get("discount_type") == "amount":
        value_text = f"💵 {d['amount']:,} تومان"
    else:
        value_text = f"💯 {d['percent']}٪"

    plans_raw = d.get("applicable_plans")
    if not plans_raw:
        plans_text = "همه‌ی پلن‌ها"
    else:
        all_plans = db.get_all_plans()
        keys = json.loads(plans_raw)
        plans_text = ", ".join(all_plans.get(k, {}).get("name", k) for k in keys) or "همه‌ی پلن‌ها"

    users_raw = d.get("allowed_user_ids")
    if not users_raw:
        users_text = "همه‌ی کاربران"
    else:
        ids = json.loads(users_raw)
        users_text = "، ".join(f"`{i}`" for i in ids)

    extra = ""
    if d.get("min_order_amount"):
        extra += f"\n💰 حداقل مبلغ سفارش: {d['min_order_amount']:,} تومان"
    if d.get("max_uses_per_user"):
        extra += f"\n🔂 سقف استفاده برای هر کاربر: {d['max_uses_per_user']}"
    if d.get("expires_at"):
        extra += f"\n⏰ انقضا: {d['expires_at']}"

    return (
        f"🎟 کد تخفیف: `{d['code']}`\n"
        f"{value_text}\n"
        f"🔁 تعداد استفاده‌ی باقی‌مانده: {d['uses']}\n"
        f"🎯 پلن‌های مجاز: {plans_text}\n"
        f"👤 کاربران مجاز: {users_text}"
        f"{extra}"
    )


@router.callback_query(F.data.startswith("discdetail_"))
async def admin_discount_detail(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    discount_id = int(callback.data.replace("discdetail_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await edit_rich(callback.message, 
        _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discdelete_"))
async def admin_discount_delete_ask(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discdelete_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await edit_rich(callback.message, 
        f"❗️ آیا از حذف کد `{d['code']}` مطمئن هستید؟",
        parse_mode="Markdown",
        reply_markup=discount_delete_confirm_keyboard(discount_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discdeleteconfirm_"))
async def admin_discount_delete_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discdeleteconfirm_", ""))
    db.delete_discount_by_id(discount_id)
    await callback.answer("✅ کد تخفیف حذف شد.", show_alert=True)
    discounts = db.get_all_discounts()
    text = "🎟 کدهای تخفیف فعال:\n\nبرای مشاهده و ویرایش جزئیات هر کد، روی آن بزنید 👇" if discounts else \
        "🎟 هیچ کد تخفیفی هنوز ثبت نشده.\n\nبرای ساخت کد جدید، دکمه‌ی زیر را بزنید 👇"
    await edit_rich(callback.message, text, reply_markup=admin_discount_menu(discounts))


# --- ویرایش مقدار تخفیف (درصد/مبلغ) ---
@router.callback_query(F.data.startswith("discedit_value_"))
async def admin_discount_edit_value_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_value_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    label = "درصد جدید را وارد کنید (بین ۱ تا ۱۰۰)" if d.get("discount_type") != "amount" else \
        "مبلغ ثابت جدید را به تومان وارد کنید"
    await edit_rich(callback.message, f"✏️ {label}:", reply_markup=admin_back_button())
    await state.set_state(AdminStates.waiting_discount_edit_value)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_value)
async def admin_discount_edit_value_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    d = db.get_discount_by_id(discount_id) if discount_id else None
    if d is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد وارد کنید:")
        return
    value = int(clean_numeric_id(message.text))
    if d.get("discount_type") == "amount":
        if value <= 0:
            await answer_rich(message, "❌ مبلغ باید بزرگ‌تر از صفر باشد:")
            return
        db.update_discount(discount_id, amount=value)
    else:
        if not (1 <= value <= 100):
            await answer_rich(message, "❌ درصد باید بین ۱ تا ۱۰۰ باشد:")
            return
        db.update_discount(discount_id, percent=value)

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, "✅ مقدار تخفیف بروزرسانی شد.", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش تعداد استفاده‌ی باقی‌مانده ---
@router.callback_query(F.data.startswith("discedit_uses_"))
async def admin_discount_edit_uses_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_uses_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await edit_rich(callback.message, 
        "✏️ تعداد دفعات مجاز باقی‌مانده‌ی این کد را وارد کنید (مثلاً 50):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_uses)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_uses)
async def admin_discount_edit_uses_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit() or int(clean_numeric_id(message.text)) < 0:
        await answer_rich(message, "❌ لطفاً یک عدد صحیح غیرمنفی وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, uses=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, "✅ تعداد استفاده بروزرسانی شد.", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش حداقل مبلغ سفارش ---
@router.callback_query(F.data.startswith("discedit_minorder_"))
async def admin_discount_edit_minorder_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_minorder_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await edit_rich(callback.message, 
        "✏️ حداقل مبلغ سفارش (به تومان) برای استفاده از این کد را وارد کنید.\n"
        "برای برداشتن محدودیت، عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_min_order)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_min_order)
async def admin_discount_edit_minorder_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, min_order_amount=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, "✅ حداقل مبلغ سفارش بروزرسانی شد.", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش سقف استفاده‌ی هر کاربر ---
@router.callback_query(F.data.startswith("discedit_maxuser_"))
async def admin_discount_edit_maxuser_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_maxuser_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await edit_rich(callback.message, 
        "✏️ سقف تعداد دفعات استفاده‌ی هر کاربر از این کد را وارد کنید.\n"
        "برای بی‌محدودیت‌کردن، عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_max_per_user)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_max_per_user)
async def admin_discount_edit_maxuser_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد وارد کنید:")
        return
    if db.get_discount_by_id(discount_id) is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    db.update_discount(discount_id, max_uses_per_user=int(clean_numeric_id(message.text)))
    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, "✅ سقف استفاده‌ی هر کاربر بروزرسانی شد.", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش تاریخ انقضا ---
@router.callback_query(F.data.startswith("discedit_expiry_"))
async def admin_discount_edit_expiry_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_expiry_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await edit_rich(callback.message, 
        "✏️ تاریخ انقضا را به‌فرمت `YYYY-MM-DD` (مثلاً 2026-12-31) وارد کنید.\n"
        "برای برداشتن انقضا (کد همیشه معتبر باشد)، عدد 0 را ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_expiry)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_expiry)
async def admin_discount_edit_expiry_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if db.get_discount_by_id(discount_id) is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    raw = (message.text or "").strip()
    if raw == "0":
        db.update_discount(discount_id, expires_at=None)
    else:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            await answer_rich(message, "❌ فرمت نامعتبر است؛ به‌صورت YYYY-MM-DD وارد کنید (یا 0 برای حذف انقضا):")
            return
        db.update_discount(discount_id, expires_at=parsed.strftime("%Y-%m-%d 23:59:59"))

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, "✅ تاریخ انقضا بروزرسانی شد.", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش کاربران مجاز ---
@router.callback_query(F.data.startswith("discedit_users_"))
async def admin_discount_edit_users_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_users_", ""))
    if db.get_discount_by_id(discount_id) is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_discount_id=discount_id)
    await edit_rich(callback.message, 
        "👤 آیدی‌های عددی تلگرام مجاز به استفاده از این کد را وارد کنید "
        "(هرکدام با کاما، فاصله یا خط جدید جدا شود).\n\n"
        "برای برداشتن محدودیت (باز کردن کد برای همه‌ی کاربران) عدد 0 را ارسال کنید.",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_edit_users)
    await callback.answer()


@router.message(AdminStates.waiting_discount_edit_users)
async def admin_discount_edit_users_save(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount_id = data.get("edit_discount_id")
    if db.get_discount_by_id(discount_id) is None:
        await answer_rich(message, "❌ مشکلی پیش آمد.", reply_markup=admin_discount_menu(db.get_all_discounts()))
        await state.clear()
        return

    raw = (message.text or "").strip()
    ids = [p for p in re.split(r"[\s,،]+", raw) if p]

    if not ids or ids == ["0"]:
        db.update_discount(discount_id, allowed_user_ids=None)
        summary = "بدون محدودیت (همه‌ی کاربران)"
    else:
        if not all(p.lstrip("-").isdigit() for p in ids):
            await answer_rich(message, "❌ فقط آیدی‌های عددی معتبر وارد کنید (یا 0 برای باز کردن برای همه):")
            return
        ids = sorted(set(ids))
        db.update_discount(discount_id, allowed_user_ids=ids)
        summary = "، ".join(f"`{i}`" for i in ids)

    await state.clear()
    d = db.get_discount_by_id(discount_id)
    await answer_rich(message, f"✅ کاربران مجاز بروزرسانی شد: {summary}", parse_mode="Markdown", reply_markup=admin_back_button())
    await answer_rich(message, _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id))


# --- ویرایش پلن‌های مجاز ---
@router.callback_query(F.data.startswith("discedit_plans_"))
async def admin_discount_edit_plans_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    discount_id = int(callback.data.replace("discedit_plans_", ""))
    d = db.get_discount_by_id(discount_id)
    if d is None:
        await callback.answer("❌ این کد تخفیف یافت نشد.", show_alert=True)
        return
    selected = db._discount_plans(d) or []
    await state.update_data(edit_discount_id=discount_id, edit_discount_plans=selected)
    await edit_rich(callback.message, 
        "🎯 پلن‌های مجاز برای این کد را انتخاب کنید (هرکدام را بزنید تا انتخاب/لغو شود):",
        reply_markup=discount_plans_edit_keyboard(discount_id, selected),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("discplaned_"))
async def admin_discount_edit_plans_toggle(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    # discplaned_<id>_<key|all|done>
    rest = callback.data.replace("discplaned_", "")
    discount_id_str, _, key = rest.partition("_")
    discount_id = int(discount_id_str)
    data = await state.get_data()
    selected = data.get("edit_discount_plans", [])

    if key == "all":
        selected = []
    elif key == "done":
        db.update_discount(discount_id, applicable_plans=selected or None)
        await state.clear()
        d = db.get_discount_by_id(discount_id)
        await edit_rich(callback.message, 
            _discount_detail_text(d), parse_mode="Markdown", reply_markup=discount_detail_keyboard(discount_id)
        )
        await callback.answer("✅ پلن‌های مجاز ذخیره شد.")
        return
    else:
        selected = [p for p in selected if p != key] if key in selected else selected + [key]

    await state.update_data(edit_discount_plans=selected)
    await callback.message.edit_reply_markup(reply_markup=discount_plans_edit_keyboard(discount_id, selected))
    await callback.answer()


@router.callback_query(F.data == "new_discount")
async def new_discount_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await edit_rich(callback.message, 
        "🎟 ساخت کد تخفیف جدید — مرحله ۱ از ۶\n\n"
        "✏️ کد تخفیف مورد نظر را بدون فاصله وارد کنید (مثلاً SUMMER20):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_discount_code_step)
    await callback.answer()


@router.message(AdminStates.waiting_discount_code_step)
async def new_discount_code_input(message: types.Message, state: FSMContext):
    code = message.text.strip().upper() if message.text else ""
    if not code or " " in code:
        await answer_rich(message, "❌ کد نامعتبر است؛ بدون فاصله دوباره وارد کنید:")
        return
    await state.update_data(new_discount_code=code)
    await answer_rich(message, 
        "🎟 مرحله ۲ از ۶\n\nنوع تخفیف را انتخاب کنید:", reply_markup=discount_type_keyboard()
    )


@router.callback_query(F.data.startswith("disctype_"), AdminStates.waiting_discount_code_step)
async def new_discount_type_chosen(callback: types.CallbackQuery, state: FSMContext):
    disc_type = callback.data.replace("disctype_", "")
    await state.update_data(new_discount_type=disc_type, new_discount_plans=[])
    label = "درصد تخفیف را وارد کنید (عددی بین ۱ تا ۱۰۰، مثلاً 20)" if disc_type == "percent" else \
        "مبلغ ثابت تخفیف را به تومان وارد کنید (مثلاً 20000)"
    await edit_rich(callback.message, f"🎟 مرحله ۳ از ۶\n\n💯 {label}:")
    await state.set_state(AdminStates.waiting_discount_value_step)
    await callback.answer()


@router.message(AdminStates.waiting_discount_value_step)
async def new_discount_value_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    disc_type = data.get("new_discount_type", "percent")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد وارد کنید:")
        return
    value = int(clean_numeric_id(message.text))
    if disc_type == "percent" and not (1 <= value <= 100):
        await answer_rich(message, "❌ درصد باید بین ۱ تا ۱۰۰ باشد:")
        return
    if disc_type == "amount" and value <= 0:
        await answer_rich(message, "❌ مبلغ باید بزرگ‌تر از صفر باشد:")
        return

    await state.update_data(new_discount_value=value)
    await answer_rich(message, 
        "🎟 مرحله ۴ از ۶\n\n"
        "🎯 این کد روی کدام پلن‌ها اعمال شود؟ (هرکدام را بزنید تا انتخاب/لغو شود؛ "
        "اگر «همه‌ی پلن‌ها» را بزنید، هیچ محدودیتی نخواهد داشت):",
        reply_markup=discount_plans_select_keyboard([]),
    )


@router.callback_query(F.data.startswith("discplan_"), AdminStates.waiting_discount_value_step)
async def new_discount_plan_toggle(callback: types.CallbackQuery, state: FSMContext):
    key = callback.data.replace("discplan_", "")
    data = await state.get_data()
    selected = data.get("new_discount_plans", [])

    if key == "all":
        selected = []
    elif key == "done":
        await state.update_data(new_discount_plans=selected)
        await edit_rich(callback.message, 
            "🎟 مرحله ۵ از ۶\n\n"
            "👤 این کد فقط برای چه کاربرانی مجاز باشد؟\n\n"
            "آیدی‌های عددی تلگرام را وارد کنید (هرکدام با کاما، فاصله یا خط جدید جدا شود).\n"
            "اگر می‌خواهید همه‌ی کاربران بتوانند از این کد استفاده کنند، عدد 0 را ارسال کنید."
        )
        await state.set_state(AdminStates.waiting_discount_users_step)
        await callback.answer()
        return
    else:
        selected = [p for p in selected if p != key] if key in selected else selected + [key]

    await state.update_data(new_discount_plans=selected)
    await callback.message.edit_reply_markup(reply_markup=discount_plans_select_keyboard(selected))
    await callback.answer()


@router.message(AdminStates.waiting_discount_users_step)
async def new_discount_users_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    ids = [p for p in re.split(r"[\s,،]+", raw) if p]

    if not ids or ids == ["0"]:
        await state.update_data(new_discount_users=None)
    elif all(p.lstrip("-").isdigit() for p in ids):
        await state.update_data(new_discount_users=sorted(set(ids)))
    else:
        await answer_rich(message, "❌ فقط آیدی‌های عددی معتبر وارد کنید (یا 0 برای باز کردن برای همه):")
        return

    await answer_rich(message, 
        "🎟 مرحله ۶ از ۶\n\n🔁 تعداد دفعات مجاز استفاده از این کد را وارد کنید (مثلاً 50):"
    )
    await state.set_state(AdminStates.waiting_discount_uses_step)


@router.message(AdminStates.waiting_discount_uses_step)
async def new_discount_uses_input(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or int(clean_numeric_id(message.text)) <= 0:
        await answer_rich(message, "❌ لطفاً یک عدد صحیح مثبت وارد کنید:")
        return

    data = await state.get_data()
    code = data.get("new_discount_code")
    disc_type = data.get("new_discount_type", "percent")
    value = data.get("new_discount_value", 0)
    plans = data.get("new_discount_plans") or None
    allowed_users = data.get("new_discount_users") or None
    uses = int(clean_numeric_id(message.text))

    try:
        db.create_discount(
            code,
            percent=value if disc_type == "percent" else 0,
            uses=uses,
            discount_type=disc_type,
            amount=value if disc_type == "amount" else 0,
            applicable_plans=plans,
            allowed_user_ids=allowed_users,
        )
        value_text = f"{value}٪" if disc_type == "percent" else f"{value:,} تومان"
        all_plans = db.get_all_plans()
        plans_text = "همه‌ی پلن‌ها" if not plans else ", ".join(all_plans.get(p, {}).get("name", p) for p in plans)
        users_text = "همه‌ی کاربران" if not allowed_users else "، ".join(f"`{i}`" for i in allowed_users)
        await answer_rich(message, 
            f"✅ کد تخفیف جدید با موفقیت ساخته شد! 🎉\n\n"
            f"🎟 کد: `{code}`\n💯 مقدار: {value_text}\n🎯 پلن‌ها: {plans_text}\n"
            f"👤 کاربران مجاز: {users_text}\n🔁 تعداد استفاده: {uses}",
            parse_mode="Markdown",
            reply_markup=admin_discount_menu(db.get_all_discounts()),
        )
    except Exception:
        await answer_rich(message, "❌ این کد قبلاً ثبت شده.", reply_markup=admin_discount_menu(db.get_all_discounts()))
    await state.clear()


# ---------------------------------------------------------------------------
# 🤝 مدیریت دعوت‌ها
# ---------------------------------------------------------------------------
REFERRERS_PER_PAGE = 10


def _referrers_page_text(page: int, total: int) -> str:
    if total == 0:
        return "🤝 هنوز هیچ دعوتی ثبت نشده."
    return (
        f"🤝 مدیریت دعوت‌شده‌ها — مرتب‌شده بر اساس بیشترین دعوت\n\n"
        f"👥 تعداد کل دعوت‌کننده‌ها: {total}\n\n"
        f"روی هر کدام بزنید تا لیست دعوت‌شده‌هایش و وضعیت کیف پولش رو ببینید 👇"
    )


@router.callback_query(F.data == "admin_referrals")
async def admin_referrals(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _render_referrers_page(callback, 0)
    await callback.answer()


async def _render_referrers_page(callback: types.CallbackQuery, page: int):
    total = db.count_referrers()
    users = db.get_referrers_page(page, REFERRERS_PER_PAGE)
    has_next = total > (page + 1) * REFERRERS_PER_PAGE
    await edit_rich(callback.message, 
        _referrers_page_text(page, total),
        reply_markup=admin_referrers_page_keyboard(users, page, has_next),
    )


@router.callback_query(F.data.startswith("refpage_"))
async def admin_referrers_page_nav(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    page = int(callback.data.replace("refpage_", ""))
    await _render_referrers_page(callback, page)
    await callback.answer()


@router.callback_query(F.data.startswith("refdetail_"))
async def admin_referrer_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    _, uid, page_str = callback.data.split("_")
    page = int(page_str)

    referrer = db.get_user(uid)
    if referrer is None:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    invited = db.get_referred_users(referrer["id"])
    text = (
        f"🤝 دعوت‌شده‌های {referrer['name']} (🆔 {referrer['telegram_id']})\n\n"
        f"💛 کیف پول آزاد دعوت‌کننده: {referrer['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود دعوت‌کننده (در انتظار): {referrer['locked_wallet']:,} تومان\n"
        f"👥 تعداد دعوت: {referrer['invited_count']} | ✅ موفق: {referrer['successful_invites']}\n\n"
        f"📋 لیست افراد دعوت‌شده:\n"
    )
    if not invited:
        text += "— هنوز هیچ کاربری ثبت نشده."
    else:
        for i, u in enumerate(invited, 1):
            reward = u.get("referral_reward") or 0
            status = u.get("referral_status") or "-"
            text += (
                f"{i}. {u['name']} | 🆔 {u['telegram_id']} | "
                f"🎁 پاداش: {reward:,} تومان | وضعیت: {status}\n"
            )

    await edit_rich(callback.message, text, reply_markup=admin_referred_detail_keyboard(uid, page))
    await callback.answer()


# ---------------------------------------------------------------------------
# 🤝 نمایندگی — با ثبت آیدی عددی یک فرد، همه‌ی خریدهای VIP بعدی او به‌صورت
# خودکار با درصد تعیین‌شده تخفیف می‌خورد (بدون نیاز به وارد کردن کد تخفیف).
# ---------------------------------------------------------------------------
def _user_detail_text(user: dict) -> str:
    """همان متن استاندارد صفحه‌ی «مدیریت کاربر» (بخش کاربران)؛ برای اینکه صفحه‌ی
    نماینده هم دقیقاً همین محیط را نشان دهد، این تابع مشترک استفاده می‌شود."""
    stats = db.get_referral_stats(user["id"])
    return (
        f"👤 {user['name']}\n"
        f"🆔 {user['telegram_id']}\n\n"
        f"👛 کیف پول آزاد: {user['wallet']:,} تومان\n"
        f"🔒 کیف پول مسدود: {user['locked_wallet']:,} تومان\n"
        f"🛒 کل خرید: {user['total_purchase']:,} تومان\n"
        f"📅 عضویت: {user['joined']}\n\n"
        f"🔗 کد دعوت: {user['invite_code']}\n"
        f"👥 دعوت: {stats['invited_count']} | موفق: {stats['successful_invites']}"
    )


@router.callback_query(F.data == "admin_agency")
async def admin_agency_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    agents = db.get_all_agents()
    text = (
        "🤝 هنوز هیچ نماینده‌ای ثبت نشده.\n\nبرای افزودن، دکمه‌ی زیر را بزنید 👇"
        if not agents else
        "🤝 نمایندگان فعلی (تخفیف خودکار روی VIP)\n\nروی هرکدام بزنید تا مثل بخش «کاربران» مدیریتش کنید 👇"
    )

    await edit_rich(callback.message, text, reply_markup=admin_agency_menu(agents))
    await callback.answer()


@router.callback_query(F.data == "new_agent")
async def new_agent_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "🤝 افزودن نماینده — مرحله ۱ از ۲\n\n"
        "🆔 آیدی عددی (Telegram ID) فرد را ارسال کنید:",
        reply_markup=admin_back_button(),
    )
    await state.set_state(AdminStates.waiting_agent_id_step)
    await callback.answer()


@router.message(AdminStates.waiting_agent_id_step)
async def new_agent_id_input(message: types.Message, state: FSMContext):
    tid = (message.text or "").strip()
    if not tid.isdigit():
        await answer_rich(message, "❌ آیدی عددی نامعتبر است؛ فقط عدد ارسال کنید:")
        return
    await state.update_data(new_agent_id=tid)
    await answer_rich(message, 
        f"🧾 مرحله ۲ از ۲\n\n💯 درصد تخفیف VIP برای این نماینده را وارد کنید "
        f"(پیش‌فرض پیشنهادی: {AGENCY_VIP_DISCOUNT_PERCENT}):"
    )
    await state.set_state(AdminStates.waiting_agent_percent_step)


@router.message(AdminStates.waiting_agent_percent_step)
async def new_agent_percent_input(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or not (1 <= int(clean_numeric_id(message.text)) <= 100):
        await answer_rich(message, "❌ لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کنید:")
        return
    data = await state.get_data()
    tid = data.get("new_agent_id")
    percent = int(clean_numeric_id(message.text))
    db.add_agent(tid, percent)
    await answer_rich(message, 
        f"✅ نماینده ثبت شد!\n\n🆔 {tid}\n💯 تخفیف VIP: {percent}٪\n\n"
        f"از این به بعد، خریدهای VIP این آیدی به‌صورت خودکار {percent}٪ تخفیف می‌خورد.",
        reply_markup=admin_agency_menu(db.get_all_agents()),
    )
    await state.clear()


@router.callback_query(F.data.startswith("deleteagent_"))
async def delete_agent(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    tid = callback.data.replace("deleteagent_", "")
    db.remove_agent(tid)
    await callback.answer("✅ نماینده حذف شد.")
    await admin_agency_list(callback)


# --- 👤 باز کردن صفحه‌ی یک نماینده — دقیقاً همان صفحه‌ی «مدیریت کاربر»
# (بخش کاربران)، به‌علاوه‌ی دکمه‌ی «💯 تغییر درصد تخفیف نمایندگی» ---
@router.callback_query(F.data.startswith("agentopen_"))
async def admin_agent_open(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    tid = callback.data.replace("agentopen_", "")
    agent = db.get_agent(tid)
    if agent is None:
        await callback.answer("❌ این نماینده یافت نشد.", show_alert=True)
        return

    user = db.get_user(tid)
    if user is None:
        await edit_rich(callback.message, 
            f"🤝 نماینده 🆔 {tid} | 💯 {agent['vip_discount_percent']}٪\n\n"
            "⚠️ این آیدی هنوز ربات را /start نزده؛ اطلاعات کاربری‌ای برایش ثبت نشده.",
            reply_markup=admin_agent_actions_keyboard(tid),
        )
        await callback.answer()
        return

    text = _user_detail_text(user) + f"\n\n🤝 درصد تخفیف نمایندگی (VIP): {agent['vip_discount_percent']}٪"
    await edit_rich(callback.message, text, reply_markup=admin_agent_actions_keyboard(tid))
    await callback.answer()


@router.callback_query(F.data.startswith("editagentpercent_"))
async def admin_agent_edit_percent_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    tid = callback.data.replace("editagentpercent_", "")
    if db.get_agent(tid) is None:
        await callback.answer("❌ این نماینده یافت نشد.", show_alert=True)
        return
    await state.update_data(edit_agent_id=tid)
    await state.set_state(AdminStates.waiting_agent_edit_percent)
    await edit_rich(callback.message, "💯 درصد تخفیف جدید (بین ۱ تا ۱۰۰) را ارسال کنید:")
    await callback.answer()


@router.message(AdminStates.waiting_agent_edit_percent)
async def admin_agent_edit_percent_apply(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit() or not (1 <= int(clean_numeric_id(message.text)) <= 100):
        await answer_rich(message, "❌ لطفاً یک عدد بین ۱ تا ۱۰۰ وارد کنید:")
        return
    data = await state.get_data()
    tid = data.get("edit_agent_id")
    agent = db.get_agent(tid) if tid else None
    if agent is None:
        await answer_rich(message, "❌ مشکلی پیش آمد؛ از ابتدا امتحان کنید.", reply_markup=admin_agency_menu(db.get_all_agents()))
        await state.clear()
        return

    percent = int(clean_numeric_id(message.text))
    db.add_agent(tid, percent, agent.get("note"))
    await answer_rich(message, 
        f"✅ درصد تخفیف نماینده به‌روزرسانی شد:\n🆔 {tid}\n💯 {percent}٪",
        reply_markup=admin_agent_actions_keyboard(tid),
    )
    await state.clear()


# ---------------------------------------------------------------------------
# Premium/Custom Emoji for dynamic VIP category/plan names
# ---------------------------------------------------------------------------
def _save_dynamic_premium_emoji(text: str, message: types.Message):
    """Persist the custom emoji attached to a dynamic category/plan name."""
    try:
        for e in serialize_message_entities(getattr(message, "entities", None)):
            if e.get("type") == "custom_emoji" and e.get("custom_emoji_id"):
                db.set_button_custom_emoji_id("text:" + str(text), str(e["custom_emoji_id"]))
                return
        db.set_button_custom_emoji_id("text:" + str(text), None)
    except Exception:
        logger.exception("ذخیره Premium Emoji نام داینامیک ناموفق بود")

# ---------------------------------------------------------------------------
# 🗂 دسته‌بندی‌های VIP — می‌توان هر تعداد دسته و داخل هرکدام هر تعداد پلن اضافه
# کرد؛ همه‌شان خودکار در «🛒 خرید اشتراک → 🚀 سرور VIP» برای کاربر ظاهر می‌شوند.
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_vip_categories")
async def admin_vip_categories_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "🗂 دسته‌بندی‌های VIP\n\n"
        "این دسته‌ها همان چیزی هستند که کاربر موقع «خرید اشتراک → سرور VIP» می‌بیند.\n"
        "برای مدیریت پلن‌های داخل هر دسته، روی آن بزنید 👇",
        reply_markup=admin_vip_categories_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "newvipcat")
async def admin_new_vip_category_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_vip_category_name)
    await edit_rich(callback.message, "🗂 نام دسته‌بندی جدید را ارسال کنید (مثلاً «💎 حجم بالای ویژه»):")
    await callback.answer()


@router.message(AdminStates.waiting_vip_category_name)
async def admin_new_vip_category_apply(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await answer_rich(message, "❌ نام نمی‌تواند خالی باشد؛ دوباره ارسال کنید:")
        return
    cat = db.create_vip_category(name)
    _save_dynamic_premium_emoji(name, message)
    await answer_rich(message, 
        f"✅ دسته‌بندی «{name}» ساخته شد!\n\nحالا می‌توانید از داخل همین دسته، پلن اضافه کنید 👇",
        reply_markup=admin_vip_category_detail_keyboard(cat["key"]),
    )
    await state.clear()


@router.callback_query(F.data.startswith("admincat_"))
async def admin_vip_category_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("admincat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    plans = db.get_vip_plans(cat["id"])
    text = f"🗂 {cat['name']}\n\n📦 تعداد پلن: {len(plans)}\n\nبرای مدیریت هر پلن روی آن بزنید 👇"
    await edit_rich(callback.message, text, reply_markup=admin_vip_category_detail_keyboard(category_key))
    await callback.answer()


@router.callback_query(F.data.startswith("delvipcat_"))
async def admin_delete_vip_category(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("delvipcat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    ok = db.delete_vip_category(cat["id"])
    if not ok:
        await callback.answer("❌ این دسته پلن دارد؛ اول همه‌ی پلن‌هایش را حذف کنید.", show_alert=True)
        return
    await callback.answer("✅ دسته حذف شد.")
    await admin_vip_categories_list(callback)


# --- افزودن پلن جدید به یک دسته (۴ مرحله: نام / قیمت / حجم گیگ / مدت روز) ---
@router.callback_query(F.data.startswith("newvipplan_"))
async def admin_new_vip_plan_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    category_key = callback.data.replace("newvipplan_", "")
    if db.get_vip_category(category_key) is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    await state.update_data(new_vip_plan_category=category_key)
    await state.set_state(AdminStates.waiting_vip_plan_name)
    await edit_rich(callback.message, 
        "📦 افزودن پلن جدید — مرحله ۱ از ۵\n\n✏️ نام پلن را ارسال کنید (مثلاً «۲۰۰ گیگ | کاربر و زمان ∞»):"
    )
    await callback.answer()


@router.message(AdminStates.waiting_vip_plan_name)
async def admin_new_vip_plan_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await answer_rich(message, "❌ نام نمی‌تواند خالی باشد؛ دوباره ارسال کنید:")
        return
    _save_dynamic_premium_emoji(name, message)
    await state.update_data(new_vip_plan_name=name)
    await state.set_state(AdminStates.waiting_vip_plan_price)
    await answer_rich(message, "📦 مرحله ۲ از ۵\n\n💰 قیمت را به تومان (فقط عدد) ارسال کنید:")


@router.message(AdminStates.waiting_vip_plan_price)
async def admin_new_vip_plan_price(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید:")
        return
    await state.update_data(new_vip_plan_price=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_gb)
    await answer_rich(message, 
        "📦 مرحله ۳ از ۵\n\n🗜 حجم را به گیگابایت ارسال کنید (اگر نامحدود است، عدد 0 را بفرستید):"
    )


@router.message(AdminStates.waiting_vip_plan_gb)
async def admin_new_vip_plan_gb(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید (برای نامحدود، 0):")
        return
    await state.update_data(new_vip_plan_gb=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_days)
    await answer_rich(message, "📦 مرحله ۴ از ۵\n\n⏳ مدت را به روز ارسال کنید (اگر نامحدود است، عدد 0 را بفرستید):")


@router.message(AdminStates.waiting_vip_plan_days)
async def admin_new_vip_plan_days(message: types.Message, state: FSMContext):
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید (برای نامحدود، 0):")
        return
    await state.update_data(new_vip_plan_days=int(clean_numeric_id(message.text)))
    await state.set_state(AdminStates.waiting_vip_plan_userlimit)
    await answer_rich(message, 
        "📦 مرحله ۵ از ۵\n\n"
        "👥 سقف کاربر همزمان (HWID Limit) را به عدد ارسال کنید (۰ تا ۱۰، ۰ = نامحدود):"
    )


@router.message(AdminStates.waiting_vip_plan_userlimit)
async def admin_new_vip_plan_userlimit(message: types.Message, state: FSMContext):
    user_limit = parse_int_in_range((message.text or "").strip(), 0, 10)
    if user_limit is None:
        await answer_rich(message, "❌ عددی بین ۰ تا ۱۰ ارسال کنید (۰ = نامحدود):")
        return
    data = await state.get_data()
    category_key = data.get("new_vip_plan_category")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await answer_rich(message, "❌ مشکلی پیش آمد؛ از ابتدا امتحان کنید.")
        await state.clear()
        return

    days = data.get("new_vip_plan_days", 0)
    name = data.get("new_vip_plan_name")
    price = data.get("new_vip_plan_price")
    volume_gb = data.get("new_vip_plan_gb", 0)

    plan_key = db.add_vip_plan(cat["id"], name, price, days=days, volume_gb=volume_gb, user_limit=user_limit)
    # Emoji was captured from the name message; store it under the stable dynamic-name key.
    # (Calling again is harmless and keeps the flow resilient to FSM serialization.)
    await answer_rich(message, 
        f"✅ پلن جدید اضافه شد! 🎉\n\n📦 {name}\n💰 {price:,} تومان\n🗜 "
        f"{volume_gb if volume_gb else 'نامحدود'} گیگ\n⏳ {days if days else 'نامحدود'} روز\n👥 {'نامحدود' if user_limit == 0 else f'{user_limit} کاربر'} همزمان",
        reply_markup=admin_vip_category_detail_keyboard(category_key),
    )
    await state.clear()


# --- مشاهده/ویرایش/حذف یک پلن مشخص ---
@router.callback_query(F.data.startswith("vipplan_"))
async def admin_vip_plan_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    plan_key = callback.data.replace("vipplan_", "")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    cat = db.get_vip_category(plan["category_id"])
    text = (
        f"📦 {plan['name']}\n\n"
        f"💰 قیمت: {plan['price']:,} تومان\n"
        f"🗜 حجم: {plan['volume_gb'] if plan['volume_gb'] else 'نامحدود'} گیگ\n"
        f"⏳ مدت: {plan['days'] if plan['days'] else 'نامحدود'} روز\n"
        f"👥 سقف کاربر: {'نامحدود' if plan.get('user_limit', 0) == 0 else str(plan['user_limit']) + ' کاربر'}\n"
        f"🗂 دسته: {cat['name'] if cat else '-'}"
    )
    await edit_rich(callback.message, 
        text, reply_markup=admin_vip_plan_detail_keyboard(plan_key, cat["key"] if cat else "")
    )
    await callback.answer()


def _vip_plan_edit_starter(field_state, prompt: str, prefix: str):
    async def handler(callback: types.CallbackQuery, state: FSMContext):
        if not _is_admin(callback.from_user.id):
            await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
            return
        plan_key = callback.data.replace(prefix, "")
        if db.get_vip_plan(plan_key) is None:
            await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
            return
        await state.update_data(edit_vip_plan_key=plan_key)
        await state.set_state(field_state)
        await edit_rich(callback.message, prompt)
        await callback.answer()
    return handler


router.callback_query(F.data.startswith("vipplanname_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_name, "✏️ نام جدید پلن را ارسال کنید:", "vipplanname_")
)
router.callback_query(F.data.startswith("vipplanprice_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_price, "💰 قیمت جدید را به تومان (فقط عدد) ارسال کنید:", "vipplanprice_")
)
router.callback_query(F.data.startswith("vipplangb_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_gb, "🗜 حجم جدید را به گیگ (فقط عدد، 0 = نامحدود) ارسال کنید:", "vipplangb_")
)
router.callback_query(F.data.startswith("vipplandays_"))(
    _vip_plan_edit_starter(AdminStates.waiting_vip_plan_edit_days, "⏳ مدت جدید را به روز (فقط عدد، 0 = نامحدود) ارسال کنید:", "vipplandays_")
)
router.callback_query(F.data.startswith("vipplanuserlimit_"))(
    _vip_plan_edit_starter(
        AdminStates.waiting_vip_plan_edit_userlimit,
        "👥 سقف کاربر همزمان جدید را ارسال کنید (۰ تا ۱۰، ۰ = نامحدود):",
        "vipplanuserlimit_"
    )
)


async def _after_vip_plan_edit(message: types.Message, state: FSMContext, plan_key: str, success_text: str):
    plan = db.get_vip_plan(plan_key)
    cat = db.get_vip_category(plan["category_id"]) if plan else None
    await answer_rich(message, 
        success_text,
        reply_markup=admin_vip_plan_detail_keyboard(plan_key, cat["key"] if cat else ""),
    )
    await state.clear()


@router.message(AdminStates.waiting_vip_plan_edit_name)
async def admin_vip_plan_edit_name_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    new_name = (message.text or "").strip()
    if not plan_key or not new_name:
        await answer_rich(message, "❌ متن نامعتبر است؛ دوباره ارسال کنید:")
        return
    old_plan = db.get_vip_plan(plan_key)
    old_name = (old_plan or {}).get("name")
    db.update_vip_plan(plan_key, name=new_name)
    if old_name and old_name != new_name:
        db.set_button_custom_emoji_id("text:" + str(old_name), None)
    _save_dynamic_premium_emoji(new_name, message)
    await _after_vip_plan_edit(message, state, plan_key, f"✅ نام پلن به‌روزرسانی شد:\n📦 {new_name}")


@router.message(AdminStates.waiting_vip_plan_edit_price)
async def admin_vip_plan_edit_price_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید:")
        return
    price = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, price=price)
    await _after_vip_plan_edit(message, state, plan_key, f"✅ قیمت پلن به‌روزرسانی شد:\n💰 {price:,} تومان")


@router.message(AdminStates.waiting_vip_plan_edit_gb)
async def admin_vip_plan_edit_gb_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید (0 = نامحدود):")
        return
    volume_gb = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, volume_gb=volume_gb)
    await _after_vip_plan_edit(
        message, state, plan_key, f"✅ حجم پلن به‌روزرسانی شد:\n🗜 {volume_gb if volume_gb else 'نامحدود'} گیگ"
    )


@router.message(AdminStates.waiting_vip_plan_edit_days)
async def admin_vip_plan_edit_days_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    if not message.text or not clean_numeric_id(message.text).isdigit():
        await answer_rich(message, "❌ فقط عدد ارسال کنید (0 = نامحدود):")
        return
    days = int(clean_numeric_id(message.text))
    db.update_vip_plan(plan_key, days=days)
    await _after_vip_plan_edit(
        message, state, plan_key, f"✅ مدت پلن به‌روزرسانی شد:\n⏳ {days if days else 'نامحدود'} روز"
    )


@router.message(AdminStates.waiting_vip_plan_edit_userlimit)
async def admin_vip_plan_edit_userlimit_apply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    plan_key = data.get("edit_vip_plan_key")
    user_limit = parse_int_in_range((message.text or "").strip(), 0, 10)
    if user_limit is None:
        await answer_rich(message, "❌ عددی بین ۰ تا ۱۰ ارسال کنید (۰ = نامحدود):")
        return
    db.update_vip_plan(plan_key, user_limit=user_limit)
    await _after_vip_plan_edit(
        message, state, plan_key,
        f"✅ سقف کاربر همزمان پلن به‌روزرسانی شد:\n👥 {'نامحدود' if user_limit == 0 else f'{user_limit} کاربر'}"
    )


@router.callback_query(F.data.startswith("delvipplan_"))
async def admin_delete_vip_plan(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    plan_key = callback.data.replace("delvipplan_", "")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    cat = db.get_vip_category(plan["category_id"])
    db.delete_vip_plan(plan_key)
    await callback.answer("✅ پلن حذف شد.")
    if cat:
        callback.data = f"admincat_{cat['key']}"
        await admin_vip_category_detail(callback)
    else:
        await admin_vip_categories_list(callback)


# --- ↕️ تغییر ترتیب دسته‌بندی‌ها/پلن‌های VIP ---
@router.callback_query(F.data.startswith("movevipcat_"))
async def admin_move_vip_category(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("movevipcat_", "")
    category_key, _, direction = raw.rpartition("_")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await callback.answer("❌ این دسته یافت نشد.", show_alert=True)
        return
    db.move_vip_category(cat["id"], direction)
    await callback.answer()
    await admin_vip_categories_list(callback)


@router.callback_query(F.data.startswith("movevipplan_"))
async def admin_move_vip_plan(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("movevipplan_", "")
    plan_key, _, direction = raw.rpartition("_")
    plan = db.get_vip_plan(plan_key)
    if plan is None:
        await callback.answer("❌ این پلن یافت نشد.", show_alert=True)
        return
    db.move_vip_plan(plan["id"], direction)
    await callback.answer()
    cat = db.get_vip_category(plan["category_id"])
    if cat:
        callback.data = f"admincat_{cat['key']}"
        await admin_vip_category_detail(callback)


# 📢 پیام همگانی
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await edit_rich(callback.message, 
        "📢 پیامی که می‌خواهید برای همه کاربران ارسال شود را بفرستید (متن، عکس، فیلم یا یک پیام فوروارد‌شده هم می‌توانید بفرستید):",
        reply_markup=admin_back_button(),
    )
    await state.set_state(UserStates.waiting_broadcast)
    await callback.answer()


@router.message(UserStates.waiting_broadcast)
async def admin_broadcast_send(message: types.Message, state: FSMContext):
    # 🐛 فیکس: قبلاً این هندلر فقط برای ADMIN_ID (ادمین اصلی) فیلتر شده بود، در حالی که
    # دکمه‌ی «📢 پیام همگانی» (چه از منوی پایین صفحه، چه از پنل ادمین) به ادمین‌های فرعی
    # دارای مجوز broadcast هم اجازه‌ی ورود به همین state را می‌داد. در نتیجه وقتی ادمین
    # فرعی متن پیام همگانی را ارسال می‌کرد، هیچ هندلری آن پیام را نمی‌گرفت (فیلتر رد می‌شد)
    # و کاربر بدون هیچ پاسخی می‌ماند. حالا مجوز را دوباره همینجا (به‌جای فیلتر ADMIN_ID) چک می‌کنیم.
    if not _admin_perm(message.from_user.id, "broadcast"):
        return
    # پیام همگانی به همه‌ی کاربران؛ copy_message هر نوع پیامی (متن، عکس،
    # فیلم، فوروارد‌شده) را عیناً ارسال می‌کند؛ کاربران مسدود حذف می‌شوند و بین
    # هر ارسال یک مکث کوتاه تاخیر برای جلوگیری از محدودیت flood تلگرام گذارده می‌شود.
    # بهینه‌سازی سرعت: قبلاً برای هر کاربر یک کوئری جداگانه به دیتابیس زده می‌شد (is_user_blocked)
    # درحالی که وضعیت بلاک همین الان داخل خروجی get_all_users() موجود بود (N+1 کوئری). حالا مستقیم از همان دیتای بارگذاری‌شده استفاده می‌شود.
    users = [u for u in db.get_all_users() if not u.get("is_blocked")]
    sent, failed = 0, 0

    status_msg = await answer_rich(message, f"📢 در حال ارسال به {len(users)} کاربر...")

    # 🚀 بهینه‌سازی سرعت: به‌جای ارسال یکی‌یکی و توقف ثابت بین هر پیام (که برای یک گروه بزرگ کاربر خیلی کند می‌شود)، حالا تا سقف مجاز تلگرام (حدوداً ۲۵-۳۰ پیام در ثانیه) به‌صورت هم‌زمان ارسال می‌شوند (دهها برابر سریع‌تر از حالت قبلی که یکی‌یکی با تاخیر ۵۰ میلی‌ثانیه ارسال می‌شد).
    semaphore = asyncio.Semaphore(25)
    lock = asyncio.Lock()
    counters = {"sent": 0, "failed": 0, "done": 0}

    async def _send_one(u):
        async with semaphore:
            try:
                await send_notification_sticker(message.bot, int(u["telegram_id"]), "notif_broadcast")
                await message.bot.copy_message(int(u["telegram_id"]), message.chat.id, message.message_id)
                ok = True
            except Exception:
                ok = False
        async with lock:
            counters["done"] += 1
            if ok:
                counters["sent"] += 1
            else:
                counters["failed"] += 1
            if counters["done"] % 200 == 0:
                try:
                    await edit_rich(status_msg, 
                        f"📢 در حال ارسال... ({counters['done']}/{len(users)}) ✅ {counters['sent']} | ❌ {counters['failed']}"
                    )
                except Exception:
                    pass

    await asyncio.gather(*(_send_one(u) for u in users))
    sent, failed = counters["sent"], counters["failed"]

    await edit_rich(status_msg, f"✅ ارسال شد به {sent} نفر. ناموفق: {failed} نفر.")
    await state.clear()


# ---------------------------------------------------------------------------
# 📚 مدیریت راهنما و اموزش‌ها — افزودن/ویرایش/حذف/تغییر ترتیب (متن/عکس/فیلم)
# ---------------------------------------------------------------------------
def _guide_detail_text_and_entities(guide: dict):
    """عنوان و محتوای راهنما را دقیقاً با Entityهای ذخیره‌شده Telegram نمایش می‌دهد.

    هیچ prefix، mapping قدیمی یا بازسازی مصنوعی به متن اضافه نمی‌شود؛ بنابراین
    offset تمام Premium Emojiها همان offset واقعی متن اصلی باقی می‌ماند.
    """
    title = str(guide.get("title") or "")
    body = str(guide.get("body_text") or "")
    text = title + (f"\n\n{body}" if body else "")
    entities = list(message_entities_from_dicts(guide.get("title_entities") or []))

    if body:
        shift = telegram_utf16_length(title + "\n\n")
        for entity in message_entities_from_dicts(guide.get("body_entities") or []):
            try:
                entities.append(entity.model_copy(update={"offset": int(entity.offset) + shift}))
            except Exception:
                pass
    return text, entities


async def _send_guide_detail(target: types.Message, guide_id: int):
    guide = db.get_guide(guide_id)
    if guide is None:
        await answer_rich(target, "❌ این راهنما دیگر موجود نیست.")
        return
    guides = db.get_guides()
    idx = next((i for i, g in enumerate(guides) if g["id"] == guide_id), 0)
    caption, entities = _guide_detail_text_and_entities(guide)
    kb = admin_guide_detail_keyboard(guide_id, idx, len(guides))
    try:
        entities = _sanitize_entities_for_text(caption, entities) if entities else []
        if guide["content_type"] == "photo" and guide.get("file_id"):
            await target.answer_photo(guide["file_id"], caption=caption, caption_entities=entities or None, reply_markup=kb)
        elif guide["content_type"] == "video" and guide.get("file_id"):
            await target.answer_video(guide["file_id"], caption=caption, caption_entities=entities or None, reply_markup=kb)
        else:
            await target.answer(caption, entities=entities or None, reply_markup=kb)
    except Exception:
        # حتی اگر یک entity قدیمی/نامعتبر باشد، صفحه ویرایش راهنما باید باز شود.
        try:
            if guide["content_type"] == "photo" and guide.get("file_id"):
                await target.answer_photo(guide["file_id"], caption=caption, reply_markup=kb)
            elif guide["content_type"] == "video" and guide.get("file_id"):
                await target.answer_video(guide["file_id"], caption=caption, reply_markup=kb)
            else:
                await target.answer(caption, reply_markup=kb)
        except Exception:
            logger.exception("خطا در نمایش راهنمای ادمین guide_id=%s حتی بدون entity", guide_id)

@router.callback_query(F.data == "admin_guides")
async def admin_guides_list(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    guides = db.get_guides()
    text = (
        f"📚 مدیریت راهنما و اموزش‌ها\n\nتعداد: {len(guides)}\n\n"
        "از اینجا می‌تونید راهنما/آموزش جدید اضافه کنید یا موردهای موجود را ویرایش کنید:"
    )
    try:
        await edit_rich(callback.message, text, reply_markup=admin_guides_menu(guides))
    except Exception:
        await answer_rich(callback.message, text, reply_markup=admin_guides_menu(guides))
    await callback.answer()


@router.callback_query(F.data == "guidenew")
async def admin_guide_new_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_title)
    await answer_rich(callback.message, 
        "📚 عنوان راهنما/آموزش جدید را بفرستید:",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_title)
async def admin_guide_new_title(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    title = (message.text or "").strip()
    if not title:
        await answer_rich(message, "❌ عنوان نمی‌تواند خالی باشد. دوباره بفرستید:")
        return
    # Premium/Custom Emoji انتخاب‌شده برای عنوان راهنما را به نام داینامیک متصل می‌کنیم
    # تا همان Emoji در دکمه‌ی راهنما نمایش داده شود و Emoji پیش‌فرضی وجود نداشته باشد.
    _save_dynamic_premium_emoji(title, message)
    await state.update_data(guide_new_title=title, guide_new_title_entities=serialize_message_entities(getattr(message, "entities", None)))
    await state.set_state(AdminStates.waiting_guide_content)
    await answer_rich(message, 
        "📝 حالا محتوای این راهنما را بفرستید (متن، عکس با کپشن، یا فیلم با کپشن):",
        reply_markup=admin_guide_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_guide_content)
async def admin_guide_new_content(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    title = data.get("guide_new_title")
    if not title:
        await state.clear()
        return

    if message.photo:
        content_type, file_id, body_text = "photo", message.photo[-1].file_id, (message.caption or "")
        body_entities = serialize_message_entities(getattr(message, "caption_entities", None))
    elif message.video:
        content_type, file_id, body_text = "video", message.video.file_id, (message.caption or "")
        body_entities = serialize_message_entities(getattr(message, "caption_entities", None))
    else:
        content_type, file_id, body_text = "text", None, (message.text or "")
        body_entities = serialize_message_entities(getattr(message, "entities", None))

    if not body_text and not file_id:
        await answer_rich(message, "❌ محتوا نمی‌تواند خالی باشد. دوباره بفرستید:")
        return

    guide = db.create_guide(
        title=title, content_type=content_type, body_text=body_text or None,
        file_id=file_id, body_entities=body_entities,
        title_entities=data.get("guide_new_title_entities") or [],
    )
    await state.clear()
    await answer_rich(message, f"✅ راهنمای «{title}» اضافه شد.")
    await _send_guide_detail(message, guide["id"])


@router.callback_query(F.data.startswith("guideadminopen_"))
async def admin_guide_open(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideadminopen_", ""))
    await _send_guide_detail(callback.message, guide_id)
    await callback.answer()


@router.callback_query(F.data.startswith("guidemove_"))
async def admin_guide_move(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    raw = callback.data.replace("guidemove_", "")
    guide_id_str, _, direction = raw.rpartition("_")
    guide_id = int(guide_id_str)
    db.move_guide(guide_id, direction)
    await _send_guide_detail(callback.message, guide_id)
    await callback.answer("↕️ ترتیب به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("guideeditname_"))
async def admin_guide_edit_name_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideeditname_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_edit_title)
    await state.update_data(guide_edit_id=guide_id)
    await answer_rich(callback.message, 
        f"✏️ عنوان جدید برای «{guide['title']}» را بفرستید:",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_edit_title)
async def admin_guide_edit_name_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    guide_id = data.get("guide_edit_id")
    if not guide_id:
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        await answer_rich(message, "❌ عنوان نمی‌تواند خالی باشد. دوباره بفرستید:")
        return
    old_guide = db.get_guide(guide_id)
    old_title = (old_guide or {}).get("title")
    if old_title and old_title != title:
        db.set_button_custom_emoji_id("text:" + str(old_title), None)
    _save_dynamic_premium_emoji(title, message)
    db.update_guide(guide_id, title=title, title_entities=serialize_message_entities(getattr(message, "entities", None)))
    await state.clear()
    await answer_rich(message, "✅ عنوان به‌روزرسانی شد.")
    await _send_guide_detail(message, guide_id)


@router.callback_query(F.data.startswith("guideeditcontent_"))
async def admin_guide_edit_content_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guideeditcontent_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_guide_edit_content)
    await state.update_data(guide_edit_id=guide_id)
    await answer_rich(callback.message, 
        f"📝 محتوای جدید برای «{guide['title']}» را بفرستید (متن، عکس با کپشن، یا فیلم با کپشن):",
        reply_markup=admin_guide_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_guide_edit_content)
async def admin_guide_edit_content_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    guide_id = data.get("guide_edit_id")
    if not guide_id:
        await state.clear()
        return

    if message.photo:
        content_type, file_id, body_text = "photo", message.photo[-1].file_id, (message.caption or "")
        body_entities = serialize_message_entities(getattr(message, "caption_entities", None))
    elif message.video:
        content_type, file_id, body_text = "video", message.video.file_id, (message.caption or "")
        body_entities = serialize_message_entities(getattr(message, "caption_entities", None))
    else:
        content_type, file_id, body_text = "text", None, (message.text or "")
        body_entities = serialize_message_entities(getattr(message, "entities", None))

    if not body_text and not file_id:
        await answer_rich(message, "❌ محتوا نمی‌تواند خالی باشد. دوباره بفرستید:")
        return

    db.update_guide(
        guide_id, content_type=content_type, body_text=body_text, file_id=file_id,
        body_entities=body_entities,
    )
    await state.clear()
    await answer_rich(message, "✅ محتوا به‌روزرسانی شد.")
    await _send_guide_detail(message, guide_id)


@router.callback_query(F.data.startswith("guidedelete_"))
async def admin_guide_delete_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guidedelete_", ""))
    guide = db.get_guide(guide_id)
    if guide is None:
        await callback.answer("❌ این راهنما دیگر موجود نیست.", show_alert=True)
        return
    await answer_rich(callback.message, 
        f"❗️ آیا از حذف «{guide['title']}» مطمئن هستید؟",
        reply_markup=admin_guide_delete_confirm_keyboard(guide_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("guidedeleteconfirm_"))
async def admin_guide_delete_do(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    guide_id = int(callback.data.replace("guidedeleteconfirm_", ""))
    guide = db.get_guide(guide_id)
    title = guide["title"] if guide else ""
    db.delete_guide(guide_id)
    guides = db.get_guides()
    await answer_rich(callback.message, 
        f"✅ راهنمای «{title}» حذف شد.\n\n📚 مدیریت راهنما و اموزش‌ها\n\nتعداد: {len(guides)}",
        reply_markup=admin_guides_menu(guides),
    )
    await callback.answer("🗑 حذف شد.")


# ---------------------------------------------------------------------------
# 🎬 مدیریت استیکر/ویدیوی تستی هر بخش از منو
# ---------------------------------------------------------------------------
_STICKER_SECTION_ORDER = list(STICKER_SECTION_LABELS.keys())


def _sticker_status(section_key: str):
    """وضعیت فعلی یک بخش را برمی‌گرداند: (is_enabled, has_custom, file_id_or_None)."""
    row = db.get_section_sticker(section_key)
    if row is None:
        return True, False, None  # پیش‌فرض: فعال، بدون سفارشی‌سازی
    return bool(row["is_enabled"]), True, row.get("file_id")


async def _send_sticker_section_detail(target: types.Message, section_key: str):
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    is_enabled, has_custom, file_id = _sticker_status(section_key)

    if is_enabled:
        status_line = "✅ فعال — سفارشی (آپلود‌شده توسط ادمین)" if has_custom else "➖ فعال — استیکر پیش‌فرض پروژه"
    else:
        status_line = "🛑 غیرفعال — هیچ استیکری نشان داده نمی‌شود"

    text = f"🎬 {label}\n\nوضعیت: {status_line}"
    kb = admin_sticker_detail_keyboard(section_key, has_custom=has_custom, is_enabled=is_enabled)

    try:
        if is_enabled:
            if file_id:
                await target.answer_sticker(file_id)
            else:
                filename = STICKER_FILES.get(section_key)
                if filename:
                    await target.answer_sticker(FSInputFile(os.path.join(STICKERS_DIR, filename)))
    except Exception:
        logger.exception("خطا در پیش‌نمایش استیکر بخش '%s'", section_key)

    await answer_rich(target, text, reply_markup=kb)


@router.callback_query(F.data == "admin_stickers")
async def admin_stickers_list(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    sections = []
    for key in _STICKER_SECTION_ORDER:
        is_enabled, has_custom, _fid = _sticker_status(key)
        if not is_enabled:
            emoji = "🛑"
        elif has_custom:
            emoji = "✅"
        else:
            emoji = "➖"
        sections.append({"key": key, "label": STICKER_SECTION_LABELS.get(key, key), "status_emoji": emoji})
    text = (
        "🎬 مدیریت استیکرهای منو\n\n"
        "✅ = سفارشی‌شده، ➖ = پیش‌فرض پروژه، 🛑 = غیرفعال\n\n"
        "یکی از بخش‌ها رو انتخاب کن:"
    )
    try:
        await edit_rich(callback.message, text, reply_markup=admin_stickers_menu(sections))
    except Exception:
        await answer_rich(callback.message, text, reply_markup=admin_stickers_menu(sections))
    await callback.answer()


@router.message(F.text == "🎬 استیکرهای منو")
async def admin_stickers_list_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    sections = []
    for key in _STICKER_SECTION_ORDER:
        is_enabled, has_custom, _fid = _sticker_status(key)
        if not is_enabled:
            emoji = "🛑"
        elif has_custom:
            emoji = "✅"
        else:
            emoji = "➖"
        sections.append({"key": key, "label": STICKER_SECTION_LABELS.get(key, key), "status_emoji": emoji})
    text = (
        "🎬 مدیریت استیکرهای منو\n\n"
        "✅ = سفارشی‌شده، ➖ = پیش‌فرض پروژه، 🛑 = غیرفعال\n\n"
        "یکی از بخش‌ها رو انتخاب کن:"
    )
    await answer_rich(message, text, reply_markup=admin_stickers_menu(sections))


@router.callback_query(F.data.startswith("stickeropen_"))
async def admin_sticker_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    section_key = callback.data.replace("stickeropen_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer()


@router.callback_query(F.data.startswith("stickerset_"))
async def admin_sticker_set_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickerset_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_sticker_upload)
    await state.update_data(sticker_section_key=section_key)
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    await answer_rich(callback.message, 
        f"📤 استیکر/فایل موردنظرت رو برای «{label}» بفرست.\n\n"
        "فقط یک استیکر متحرک (ویدیویی) معتبر تلگرام قابل قبوله؛ هر ویدیوی معمولی رو تلگرام به‌عنوان استیکر قبول نمی‌کنه.",
        reply_markup=admin_sticker_cancel_keyboard(section_key),
    )
    await callback.answer()


@router.message(AdminStates.waiting_sticker_upload)
async def admin_sticker_upload_receive(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    section_key = data.get("sticker_section_key")
    if not section_key:
        await state.clear()
        return

    file_id = None
    if message.sticker:
        file_id = message.sticker.file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.animation:
        file_id = message.animation.file_id

    if not file_id:
        await answer_rich(message, "❌ فقط استیکر یا فایل/ویدیوی قابل قبوله. دوباره بفرست یا انصراف بده:")
        return

    db.set_section_sticker(section_key, file_id)
    invalidate_section_sticker_cache(section_key)
    await state.clear()
    label = STICKER_SECTION_LABELS.get(section_key, section_key)
    await answer_rich(message, f"✅ استیکر «{label}» ذخیره شد.")
    await _send_sticker_section_detail(message, section_key)


@router.callback_query(F.data.startswith("stickeroff_"))
async def admin_sticker_off(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickeroff_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.set_section_sticker_enabled(section_key, False)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("🛑 غیرفعال شد.")


@router.callback_query(F.data.startswith("stickeron_"))
async def admin_sticker_on(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickeron_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.set_section_sticker_enabled(section_key, True)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("✅ فعال شد.")


@router.callback_query(F.data.startswith("stickerreset_"))
async def admin_sticker_reset(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    section_key = callback.data.replace("stickerreset_", "")
    if section_key not in STICKER_SECTION_LABELS:
        await callback.answer("❌ بخش یافت نشد.", show_alert=True)
        return
    db.reset_section_sticker(section_key)
    invalidate_section_sticker_cache(section_key)
    await _send_sticker_section_detail(callback.message, section_key)
    await callback.answer("♻️ به حالت پیش‌فرض برگشت.")


# ---------------------------------------------------------------------------
# 💾 بکاپ
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "admin_backup")
async def admin_backup(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    try:
        if db.USE_TURSO:
            export_path = "/tmp/backup_export.json"
            db.export_backup_json(export_path)
            backup_file = FSInputFile(export_path, filename="backup.json")
            caption = "💾 بکاپ دیتابیس (Turso — JSON)"
        else:
            backup_file = FSInputFile(DATABASE_PATH)
            caption = "💾 بکاپ دیتابیس"
        await callback.message.answer_document(backup_file, caption=caption)
    except Exception:
        await answer_rich(callback.message, "❌ خطا در ساخت بکاپ.")
    await callback.answer()


# ---------------------------------------------------------------------------
# ℹ️ اطلاعات ربات (قالب فروشی)
# ---------------------------------------------------------------------------
def _botinfo_status_text():
    values = bot_info.all_values()
    labels = bot_info.labels()
    lines = ["ℹ️ اطلاعات ربات\n", "برای ویرایش روی هر کدام بزنید:\n"]
    for key, label in labels.items():
        val = values.get(key) or "—"
        lines.append(f"• {label}: {val}")
    channels = bot_info.get_required_channels()
    lines.append(f"\n• کانال‌های اجباری: {len(channels)} عدد")
    return "\n".join(lines)


@router.callback_query(F.data == "admin_botinfo")
async def admin_botinfo_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    try:
        await edit_rich(callback.message, _botinfo_status_text(), reply_markup=admin_botinfo_menu())
    except TelegramBadRequest:
        await answer_rich(callback.message, _botinfo_status_text(), reply_markup=admin_botinfo_menu())
    await callback.answer()


@router.message(F.text == "ℹ️ اطلاعات ربات")
async def admin_botinfo_open_msg(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await answer_rich(message, _botinfo_status_text(), reply_markup=admin_botinfo_menu())


def _admin_renewal_settings(category_id, plan_key=None):
    try: cid = int(category_id)
    except Exception: cid = 0
    defaults = {"mode":"day","price_day":0,"price_gb":5500,"min_day":1,"max_day":0,"min_gb":1,"max_gb":0,"day_options":"30,60,90","gb_options":"10,20,50"}
    if cid <= 0: return defaults
    for field in defaults:
        raw=db.get_setting(f"renewal_category_{cid}_{field}")
        if raw not in (None, ""):
            try: defaults[field]=raw if field in ("mode","day_options","gb_options") else int(float(raw))
            except (TypeError,ValueError): pass
    try:
        legacy=bot_info.get_renewal_settings(cid)
        if isinstance(legacy,dict):
            for field in defaults:
                if db.get_setting(f"renewal_category_{cid}_{field}") in (None,"") and field in legacy: defaults[field]=legacy[field]
    except Exception: pass
    if plan_key:
        try: defaults=bot_info.get_renewal_plan_settings(plan_key,cid)
        except Exception: pass
    return defaults


def _renewal_scope_parts(target: str):
    if target.startswith("cat_"):
        try: return "cat",int(target[4:]),None
        except Exception: return None,None,None
    if target.startswith("plan_"):
        plan_key=target[5:]; plan=db.get_vip_plan(plan_key)
        if plan and plan.get("category_id"): return "plan",int(plan["category_id"]),plan_key
    return None,None,None


@router.callback_query(F.data == "admin_renewal_settings")
async def admin_renewal_settings_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await edit_rich(
        callback.message,
        "🔁 تنظیمات تمدید سرویس\n\nبرای هر دسته‌بندی می‌توانی حالت تمدید، قیمت و حداقل/حداکثر روز و گیگ را جداگانه تنظیم کنی.",
        reply_markup=admin_renewal_categories_menu(),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("renewsetcat_"))
async def admin_renewal_category(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        category_id = int(callback.data.replace("renewsetcat_", ""))
    except Exception:
        await callback.answer("❌ دسته نامعتبر است.", show_alert=True)
        return
    cat = db.get_vip_category(category_id)
    if not cat:
        await callback.answer("❌ دسته پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await edit_rich(
        callback.message,
        f"تنظیمات تمدید\n\nدسته: {cat['name']}\n\nتغییرات را برای همه پلن‌های این دسته اعمال می‌کنی یا فقط یک پلن؟",
        reply_markup=admin_renewal_scope_menu(category_id),
    )
    await callback.answer()

@router.callback_query(F.data.regexp(r"^renewsetscope_\d+$"))
async def admin_renewal_scope_back(callback: types.CallbackQuery, state: FSMContext):
    if callback.data.startswith("renewsetscope_all_") or callback.data.startswith("renewsetscope_plan_"):
        return
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    try: category_id=int(callback.data.replace("renewsetscope_", ""))
    except Exception: await callback.answer("دسته نامعتبر است.", show_alert=True); return
    await state.clear(); cat=db.get_vip_category(category_id)
    await edit_rich(callback.message,f"تنظیمات تمدید\n\nدسته: {(cat or {}).get('name',category_id)}\n\nتغییرات را برای همه پلن‌ها اعمال می‌کنی یا فقط یک پلن؟",reply_markup=admin_renewal_scope_menu(category_id)); await callback.answer()

@router.callback_query(F.data.startswith("renewsetscope_all_"))
async def admin_renewal_scope_all(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    try: category_id=int(callback.data.replace("renewsetscope_all_", ""))
    except Exception: await callback.answer("دسته نامعتبر است.", show_alert=True); return
    cat=db.get_vip_category(category_id)
    await state.clear()
    await edit_rich(callback.message, f"تنظیمات تمدید\n\nدسته: {(cat or {}).get('name',category_id)}\nمحدوده: همه پلن‌های این دسته", reply_markup=admin_renewal_category_menu(category_id))
    await callback.answer()

@router.callback_query(F.data.startswith("renewsetscope_plan_"))
async def admin_renewal_scope_plan(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    raw=callback.data.replace("renewsetscope_plan_", "", 1)
    try: category_id_str,plan_key=raw.split("_",1); category_id=int(category_id_str)
    except Exception: await callback.answer("پلن نامعتبر است.", show_alert=True); return
    plan=db.get_vip_plan(plan_key)
    if not plan or int(plan.get("category_id") or 0)!=category_id: await callback.answer("پلن پیدا نشد.", show_alert=True); return
    await state.clear()
    await edit_rich(callback.message, f"تنظیمات تمدید\n\nدسته: {(db.get_vip_category(category_id) or {}).get('name',category_id)}\nپلن: {plan['name']}\nمحدوده: فقط این پلن", reply_markup=admin_renewal_plan_menu(category_id,plan_key))
    await callback.answer()

@router.callback_query(F.data.startswith("renewsetmode_"))
async def admin_renewal_mode(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id): await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    target=callback.data.replace("renewsetmode_", "", 1); scope,category_id,plan_key=_renewal_scope_parts(target)
    if not scope: await callback.answer("دسته/پلن نامعتبر است.", show_alert=True); return
    await edit_rich(callback.message,"نوع تمدید را انتخاب کن:",reply_markup=admin_renewal_mode_menu(category_id,plan_key=plan_key)); await callback.answer()

@router.callback_query(F.data.startswith("renewsetmodeval_"))
async def admin_renewal_mode_save(callback: types.CallbackQuery, state: FSMContext):
    raw=callback.data.replace("renewsetmodeval_", "", 1)
    try: mode,target=raw.split("_",1)
    except ValueError: await callback.answer("❌ مقدار نامعتبر.",show_alert=True); return
    scope,category_id,plan_key=_renewal_scope_parts(target)
    if not scope or mode not in ("day","gb","both"): await callback.answer("❌ مقدار نامعتبر.",show_alert=True); return
    key=f"renewal_category_{category_id}_mode" if scope=="cat" else f"renewal_plan_{plan_key}_mode"; db.set_setting(key,mode)
    try:
        if scope=="cat": bot_info.set_renewal_setting(category_id,"mode",mode); bot_info.clear_renewal_plan_overrides_for_category(category_id,"mode")
        else: bot_info.set_renewal_plan_setting(plan_key,"mode",mode)
    except Exception: pass
    await state.clear(); kb=admin_renewal_category_menu(category_id) if scope=="cat" else admin_renewal_plan_menu(category_id,plan_key)
    await edit_rich(callback.message,"تنظیمات تمدید به‌روز شد.",reply_markup=kb); await callback.answer()

@router.callback_query(F.data.startswith("renewset_options_"))
async def admin_renewal_options_start(callback: types.CallbackQuery, state: FSMContext):
    raw=callback.data.replace("renewset_options_", "", 1)
    try: unit,target=raw.split("_",1)
    except ValueError: await callback.answer("❌ گزینه نامعتبر.",show_alert=True); return
    scope,category_id,plan_key=_renewal_scope_parts(target)
    if unit not in ("day","gb") or not scope: await callback.answer("❌ گزینه نامعتبر.",show_alert=True); return
    st=_admin_renewal_settings(category_id,plan_key); current=st.get(f"{unit}_options") or ("30,60,90" if unit=="day" else "10,20,50")
    key=f"renewal_category_{category_id}_{unit}_options" if scope=="cat" else f"renewal_plan_{plan_key}_{unit}_options"
    await state.update_data(botinfo_key=key,renewal_category_id=category_id,renewal_plan_key=plan_key); await state.set_state(AdminStates.waiting_botinfo_value)
    label="روز" if unit=="day" else "گیگ"; await answer_rich(callback.message,f"دکمه‌های {label} را با کاما جدا کن.\nمثال: 10,20,50\nمقدار فعلی: {current}",reply_markup=admin_back_button()); await callback.answer()

@router.callback_query(F.data.startswith("renewset_"))
async def admin_renewal_numeric_start(callback: types.CallbackQuery, state: FSMContext):
    raw=callback.data.replace("renewset_", "", 1); parts=raw.split("_")
    if len(parts)<4: await callback.answer("❌ گزینه نامعتبر.",show_alert=True); return
    field,unit=parts[0],parts[1]; target="_".join(parts[2:]); scope,category_id,plan_key=_renewal_scope_parts(target)
    if field not in ("price","min","max") or unit not in ("day","gb") or not scope: await callback.answer("❌ گزینه نامعتبر.",show_alert=True); return
    st=_admin_renewal_settings(category_id,plan_key); current=st[f"{field}_{unit}"]
    key=f"renewal_category_{category_id}_{field}_{unit}" if scope=="cat" else f"renewal_plan_{plan_key}_{field}_{unit}"
    await state.update_data(botinfo_key=key,renewal_category_id=category_id,renewal_plan_key=plan_key); await state.set_state(AdminStates.waiting_botinfo_value)
    label={"price":"قیمت","min":"حداقل","max":"حداکثر"}[field]; suffix="تومان" if field=="price" else ("روز" if unit=="day" else "گیگ"); shown="نامحدود" if field=="max" and int(current or 0)==0 else current
    await answer_rich(callback.message,f"{label} {suffix} را وارد کن.\nمقدار فعلی: {shown}",reply_markup=admin_back_button()); await callback.answer()

@router.message(AdminStates.waiting_botinfo_value)
async def admin_botinfo_edit_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id): return
    data=await state.get_data(); key=data.get("botinfo_key"); labels=bot_info.labels(); value=(message.text or "").strip()
    trans=str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")
    if key and key.startswith("renewal_category_"):
        try:
            parts=key.split("_")
            if len(parts)==5 and parts[3] in ("day","gb") and parts[4]=="options":
                category_id=int(parts[2]); unit=parts[3]; nums=[]
                for token in value.replace("،",",").split(","):
                    token=token.strip()
                    if token: nums.append(int(token.translate(trans).replace("٬", "")))
                nums=list(dict.fromkeys(nums)); st=_admin_renewal_settings(category_id); minimum=int(st.get(f"min_{unit}") or 1); maximum=int(st.get(f"max_{unit}") or 0)
                if not nums or any(n<=0 or n<minimum or (maximum and n>maximum) for n in nums): raise ValueError
                canonical=",".join(str(n) for n in nums); db.set_setting(f"renewal_category_{category_id}_{unit}_options",canonical); bot_info.set_renewal_setting(category_id,f"{unit}_options",canonical); bot_info.clear_renewal_plan_overrides_for_category(category_id,f"{unit}_options"); await state.clear(); await answer_rich(message,"دکمه‌های انتخاب تمدید به‌روز شد.",reply_markup=admin_renewal_category_menu(category_id)); return
            if len(parts)!=5: raise ValueError
            category_id=int(parts[2]); field=parts[3]; unit=parts[4]; number=int(value.translate(trans).replace(",","").replace("٬", "").strip())
            if field not in ("price","min","max") or unit not in ("day","gb") or number<0 or (field=="min" and number<1): raise ValueError
            if field=="max" and number and number<int(_admin_renewal_settings(category_id).get(f"min_{unit}") or 1): raise ValueError
            db.set_setting(f"renewal_category_{category_id}_{field}_{unit}",str(number)); bot_info.set_renewal_setting(category_id,f"{field}_{unit}",number); bot_info.clear_renewal_plan_overrides_for_category(category_id,f"{field}_{unit}"); await state.clear(); await answer_rich(message,"تنظیمات تمدید به‌روز شد.",reply_markup=admin_renewal_category_menu(category_id)); return
        except Exception:
            await answer_rich(message,"مقدار نامعتبر است؛ فقط عدد صحیح وارد کنید. برای حداکثر، ۰ یعنی نامحدود."); return
    if key and key.startswith("renewal_plan_"):
        try:
            raw=key.replace("renewal_plan_", "", 1)
            if raw.endswith("_day_options") or raw.endswith("_gb_options"):
                unit="day" if raw.endswith("_day_options") else "gb"; suffix=f"_{unit}_options"; plan_key=raw[:-len(suffix)]; plan=db.get_vip_plan(plan_key)
                if not plan: raise ValueError
                nums=[int(x.strip().translate(trans).replace("٬", "")) for x in value.replace("،",",").split(",") if x.strip()]; nums=list(dict.fromkeys(nums)); st=_admin_renewal_settings(plan["category_id"],plan_key); minimum=int(st.get(f"min_{unit}") or 1); maximum=int(st.get(f"max_{unit}") or 0)
                if not nums or any(n<=0 or n<minimum or (maximum and n>maximum) for n in nums): raise ValueError
                canonical=",".join(str(n) for n in nums); db.set_setting(f"renewal_plan_{plan_key}_{unit}_options",canonical); bot_info.set_renewal_plan_setting(plan_key,f"{unit}_options",canonical); await state.clear(); await answer_rich(message,"دکمه‌های انتخاب تمدید به‌روز شد.",reply_markup=admin_renewal_plan_menu(int(plan["category_id"]),plan_key)); return
            import re as _re
            m=_re.fullmatch(r"(.+)_(price|min|max)_(day|gb)",raw)
            if not m: raise ValueError
            plan_key,field,unit=m.groups(); plan=db.get_vip_plan(plan_key)
            if not plan: raise ValueError
            number=int(value.translate(trans).replace(",","").replace("٬", "").strip())
            if number<0 or (field=="min" and number<1): raise ValueError
            st=_admin_renewal_settings(plan["category_id"],plan_key)
            if field=="max" and number and number<int(st.get(f"min_{unit}") or 1): raise ValueError
            db.set_setting(f"renewal_plan_{plan_key}_{field}_{unit}",str(number)); bot_info.set_renewal_plan_setting(plan_key,f"{field}_{unit}",number); await state.clear(); await answer_rich(message,"تنظیمات تمدید به‌روز شد.",reply_markup=admin_renewal_plan_menu(int(plan["category_id"]),plan_key)); return
        except Exception:
            await answer_rich(message,"مقدار نامعتبر است؛ فقط عدد صحیح وارد کنید. برای حداکثر، ۰ یعنی نامحدود."); return
    if not key or key not in labels:
        await state.clear(); await answer_rich(message,"❌ خطای داخلی. دوباره تلاش کنید."); return
    if key == "config_name_prefix":
        cleaned=re.sub(r"[^A-Za-z0-9_]+", "", value)
        if not cleaned:
            await answer_rich(message,"❌ پیشوند باید فقط از حروف/عدد انگلیسی و زیرخط (_) تشکیل شده باشد؛ دوباره وارد کن:"); return
        value=cleaned
    # متن را تا سقف واقعی Telegram Bot API می‌پذیریم. مسیر /start هنگام ارسال
    # متن ثابت پایین را هم حساب می‌کند و اگر مجموع از سقف عبور کند، امن کوتاه می‌شود.
    from utils import telegram_utf16_length
    if telegram_utf16_length(value) > TELEGRAM_TEXT_LIMIT:
        await answer_rich(message, 
            f"❌ متن از سقف تلگرام بیشتر است. سقف واقعی: {TELEGRAM_TEXT_LIMIT} واحد UTF-16. لطفاً متن کوتاه‌تری بفرست:"
        )
        return
    if key == "welcome_text":
        # Custom/Premium Emoji به‌صورت MessageEntity می‌آید؛ ذخیره‌ی صرفِ message.text
        # شناسه‌ی emoji را از بین می‌برد.
        bot_info.set_welcome_text_with_entities(value, serialize_message_entities(message.entities))
    else:
        bot_info.set(key, value)
    await state.clear()
    await answer_rich(message, f"✅ «{labels[key]}» به‌روز شد.", reply_markup=admin_botinfo_menu())


@router.callback_query(F.data.startswith("botinfoedit_"))
async def admin_botinfo_edit_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    key = callback.data.replace("botinfoedit_", "")
    labels = bot_info.labels()
    if key not in labels:
        await callback.answer("❌ یافت نشد.", show_alert=True)
        return
    await state.update_data(botinfo_key=key)
    await state.set_state(AdminStates.waiting_botinfo_value)
    current = bot_info.get(key) or "—"
    await answer_rich(callback.message, 
        f"✏️ مقدار جدید برای «{labels[key]}» را بفرستید.\nمقدار فعلی: {current}",
        reply_markup=admin_botinfo_field_keyboard(key),
    )
    await callback.answer()


@router.callback_query(F.data == "botinfochannels")
async def admin_botinfo_channels_open(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    channels = bot_info.get_required_channels()
    text = "📢 کانال‌های عضویت اجباری\n\nروی هرکدام بزنید تا حذف شود." if channels else "📢 هیچ کانال اجباریثبت نشده."
    try:
        await edit_rich(callback.message, text, reply_markup=admin_botinfo_channels_menu(channels))
    except TelegramBadRequest:
        await answer_rich(callback.message, text, reply_markup=admin_botinfo_channels_menu(channels))
    await callback.answer()


@router.callback_query(F.data == "botinfochadd")
async def admin_botinfo_channel_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_botinfo_channel_add)
    await answer_rich(callback.message, 
        "➕ افزودن کانال اجباری\n\nفرمت زیر را ارسال کنید (با | جدا شده):\nآیدی/یوزرنیم کانال | نام نمایشی | لینک دعوت\nمثال: -1001234567890 | کانال ما | https://t.me/mychannel",
        reply_markup=admin_botinfo_field_keyboard(""),
    )
    await callback.answer()


@router.message(AdminStates.waiting_botinfo_channel_add)
async def admin_botinfo_channel_add_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in (message.text or "").split("|")]
    # 🐛 فیکس: قبلاً فقط خالی‌نبودن آیدی کانال (parts[0]) چک می‌شد؛ اگر ادمین لینک دعوت
    # (parts[2]) را خالی می‌فرستاد یا فراموش می‌کرد، همان مقدار خالی مستقیم به‌عنوان url
    # دکمه‌ی «عضویت» ذخیره می‌شد و بعداً برای هر کاربری که هنوز عضو نشده بود، تلگرام موقع
    # نمایش منوی عضویت اجباری خطای BUTTON_URL_INVALID می‌داد و /start با ارور مواجه می‌شد.
    if len(parts) != 3 or not parts[0] or not parts[2]:
        await answer_rich(message, "❌ فرمت نادرست یا لینک دعوت خالی است. هر سه بخش (آیدی | نام | لینک دعوت) باید پر باشند. دوباره تلاش کنید یا /cancel بزنید.")
        return
    raw_chat_id, name, url = parts
    raw_chat_id = clean_numeric_id(raw_chat_id)

    # 🐛 فیکس: قبلاً هرچی ادمین می‌فرستاد بدون هیچ بررسی ذخیره می‌شد؛ اگر فرمت آیدی/یوزرنیم اشتباه بود (مثلاً بدون پیشوند "-100" برای کانال/سوپرگروه، یا بودن خود لینک دعوت به‌جای آیدی/یوزرنیم) یا ربات هنوز ادمین کانال نشده بود، ربات بی‌صدا ذخیره می‌شد و بعداً تأیید عضویت هیچ‌وقت برای هیچ کاربری درست قبول نمی‌شد. اینجا همان لحظه خود ربات را تست می‌کنیم.
    chat_id_input = raw_chat_id if raw_chat_id.startswith("@") or raw_chat_id.startswith("-") or raw_chat_id.lstrip("-").isdigit() else f"@{raw_chat_id.lstrip('@')}"
    try:
        chat = await message.bot.get_chat(chat_id_input)
    except Exception as e:
        await answer_rich(message, 
            "❌ ربات نتوانست این کانال را پیدا کند. ممکن است:\n"
            "• فرمت آیدی اشتباه باشد (برای کانال باید با «-100» شروع شود، متل -1001234567890)\n"
            "• ربات هنوز به این کانال اضافه/عضو نشده باشد\n\n"
            f"خطای دقیق: {e}\n\nدوباره تلاش کنید یا /cancel بزنید."
        )
        return

    try:
        bot_member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await answer_rich(message, 
                "⚠️ ربات عضو این کانال هست ولی «ادمین» نیست. برای اینکه ربات بتواند عضویت کاربرها را در این کانال ببیند، باید ربات را در آن کانال «ادمین» کنی ‌(نه فقط عضو)، بعد دوباره همین پیام را بفرست."
            )
            return
    except Exception as e:
        await answer_rich(message, f"❌ بررسی وضعیت عضویت ربات در این کانال با خطا مواجه شد: {e}\n\nدوباره تلاش کنید یا /cancel بزنید.")
        return

    # 🐛 فیکس: به‌جای متنی که ادمین خودش تایپ کرده، همیشه از chat.id عددی واقعی که تلگرام برمی‌گرداند استفاده می‌کنیم تا همیشه با همان فرمتی که check_membership انتظار دارد ذخیره شود (نه متنی که ادمین تایکرده و ممکن است فرمتش اشتباه باشد).
    bot_info.add_required_channel(chat.id, name, url)
    await state.clear()
    channels = bot_info.get_required_channels()
    await answer_rich(message, 
        f"✅ کانال اضافه شد و تایید شد که ربات به درستی در آن ادمین است (آیدی واقعی: {chat.id}).",
        reply_markup=admin_botinfo_channels_menu(channels),
    )


@router.callback_query(F.data.startswith("botinfochdel_"))
async def admin_botinfo_channel_del(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    chat_id = callback.data.replace("botinfochdel_", "")
    bot_info.remove_required_channel(chat_id)
    channels = bot_info.get_required_channels()
    text = "📢 کانال‌های عضویت اجباری" if channels else "📢 هیچ کانال اجباریثبت نشده."
    try:
        await edit_rich(callback.message, text, reply_markup=admin_botinfo_channels_menu(channels))
    except TelegramBadRequest:
        await answer_rich(callback.message, text, reply_markup=admin_botinfo_channels_menu(channels))
    await callback.answer("✅ حذف شد.")


@router.message(F.text == "📝 مدیریت متن‌های کاربر")
async def admin_texts_from_menu(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await answer_rich(message, 
        "📝 مدیریت جامع متن‌های کاربر و اعلان‌ها\n\nیک بخش را انتخاب کنید:",
        reply_markup=_text_manager_keyboard(),
    )


@router.callback_query(F.data == "errlog")
async def admin_error_logs_open_callback(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await _open_error_logs(callback, edit=True)
    await callback.answer()


@router.callback_query(F.data == "admin_free_test_settings")
async def admin_free_test_settings_open_callback(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_free_test_settings)
    await edit_rich(callback.message, _free_test_settings_text())
    await callback.answer()


# ---------------------------------------------------------------------------
# 🎁 تنظیم حجم/روز پلن «تست رایگان» از پنل ادمین
# ---------------------------------------------------------------------------
FREE_TEST_MIN_VOLUME_MB = 50
FREE_TEST_MAX_VOLUME_MB = 1024
FREE_TEST_MIN_DAYS = 1
FREE_TEST_MAX_DAYS = 7
FREE_TEST_MIN_PRICE = 0
FREE_TEST_MAX_PRICE = 2000


def _free_test_settings_text() -> str:
    plan = db.get_effective_free_test_plan()
    price = plan.get("price", 0)
    price_label = "رایگان" if price == 0 else f"{price:,} تومان"
    return (
        "🎁 تنظیم پلن «تست رایگان»\n\n"
        f"مقدار فعلی: {plan['name']} — قیمت: {price_label}\n\n"
        "برای تغییر، حجم (مگابایت)، تعداد روز و قیمت (تومان) را با | جدا و ارسال کنید.\n"
        f"محدوده‌ی مجاز: حجم بین {FREE_TEST_MIN_VOLUME_MB} تا {FREE_TEST_MAX_VOLUME_MB} مگابایت، "
        f"روز بین {FREE_TEST_MIN_DAYS} تا {FREE_TEST_MAX_DAYS} روز، "
        f"قیمت بین {FREE_TEST_MIN_PRICE} (رایگان) تا {FREE_TEST_MAX_PRICE} تومان.\n"
        "مثال: 500 | 3 | 1500\n\n"
        "برای انصراف /cancel بزنید."
    )


@router.message(F.text == "🔁 تنظیمات تمدید")
async def admin_renewal_settings_open_message(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    await answer_rich(
        message,
        "🔁 تنظیمات تمدید سرویس\n\nبرای هر دسته‌بندی می‌توانی حالت تمدید، قیمت و حداقل/حداکثر روز و گیگ را جداگانه تنظیم کنی.",
        reply_markup=admin_renewal_categories_menu(),
    )

@router.message(F.text == "🎁 تنظیم تست رایگان")
async def admin_free_test_settings_open(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_free_test_settings)
    await answer_rich(message, _free_test_settings_text())


@router.message(AdminStates.waiting_free_test_settings)
async def admin_free_test_settings_save(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if text == "/cancel":
        await state.clear()
        await answer_rich(message, "❌ لغو شد.", reply_markup=_admin_reply_kb_for(message.from_user.id))
        return
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await answer_rich(message, "❌ فرمت نادرست. مثال درست: 500 | 3 | 1500\n\nدوباره تلاش کنید یا /cancel بزنید.")
        return
    volume_str, days_str, price_str = parts
    volume_mb = parse_int_in_range(volume_str, FREE_TEST_MIN_VOLUME_MB, FREE_TEST_MAX_VOLUME_MB)
    days = parse_int_in_range(days_str, FREE_TEST_MIN_DAYS, FREE_TEST_MAX_DAYS)
    price = parse_int_in_range(price_str, FREE_TEST_MIN_PRICE, FREE_TEST_MAX_PRICE)
    if volume_mb is None or days is None or price is None:
        await answer_rich(message, 
            f"❌ مقدار نامعتبر. حجم باید بین {FREE_TEST_MIN_VOLUME_MB} تا {FREE_TEST_MAX_VOLUME_MB} مگابایت، "
            f"روز باید بین {FREE_TEST_MIN_DAYS} تا {FREE_TEST_MAX_DAYS} و "
            f"قیمت باید بین {FREE_TEST_MIN_PRICE} تا {FREE_TEST_MAX_PRICE} تومان باشد.\n\nدوباره تلاش کنید یا /cancel بزنید."
        )
        return
    try:
        db.set_free_test_override(volume_mb, days, price)
    except Exception as e:
        logger.exception("خطا در ذخیره تنظیمات تست رایگان")
        await answer_rich(message, f"❌ ذخیره تنظیمات ناموفق بود: {e}")
        return
    await state.clear()
    plan = db.get_effective_free_test_plan()
    price_label = "رایگان" if price == 0 else f"{price:,} تومان"
    await answer_rich(message, 
        f"✅ پلن «تست رایگان» به‌روزرسانی شد: {plan['name']} — قیمت: {price_label}",
        reply_markup=_admin_reply_kb_for(message.from_user.id),
    )


# ---------------------------------------------------------------------------
# 🧩 تنظیم قیمت/محدوده‌ی «بساز سرویس خودت» از پنل ادمین
# ---------------------------------------------------------------------------








