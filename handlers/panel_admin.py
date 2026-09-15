"""
handlers/panel_admin.py
مدیریت یکپارچه‌شده‌ی پنل‌های مرزبان / پاسارگارد / 3X-UI.

⚠️ جایگزین handlers/shahrah_admin.py قدیمی (یکپنلی، فقط شاهراه). این ماژول:
- هر سه نوع پنل را هم‌زمان پشتیبانی می‌کند (هر سه در یک لحظه می‌توانند فعال باشند).
- هر نوع پنل می‌تواند چند نمونه (Instance) هم‌زمان داشته باشد (مدیریت در دکمه‌های جداگانه).
- نگاشت پلن/بسته در سطح "کدام نمونه‌ی پنل" انجام می‌شود (تا ادمین بتواند برای هر پلن/بسته تعیین
  کند دقیقاً از کدام نمونه‌ی پنل استفاده شود).

پلتفرم واقعی هر پنل فقط از ماژول panels.py صدا می‌شود (هیچ‌وقت مستقیم به shahrah.py /
marzban_panel.py / pasargad_panel.py وصل نمی‌شود) تا رفتار برای هر سه نوع یکسان بماند.

۰️⃣ قانون ارسال VIP بر اساس روش پرداخت (بدون تفاوت با قبل):
- کیف پول و پرداخت آنلاین: اگر پلن/دسته یک نگاشت فعال به یک نمونه‌ی پنل داشته باشد، سرویس
  بلافاصله و کاملاً خودکار از همون نمونه ساخته و برای مشتری ارسال می‌شود. اگر نگاشتی
  وجود ندارد یا نمونه‌ی مقصد غیرفعال است، دقیقاً متل قبل به ادمین اطلاع داده می‌شود تا خودش دستی
  ارسال کند.
- کارت‌به‌کارت: تفاوتی نکرده — بعد از تایید رسید توسط ادمین، دکمه‌ی «ارسال خودکار از پنل»
  همان مقصدی که برای این پلن نگاشت شده را نشان می‌دهد.

"""

import html
import json
import logging
import re
import secrets
import string
from datetime import datetime, timedelta
from subscription import days_remaining

# این تابع در نسخه فعلی subscription.py وجود ندارد؛ برای جلوگیری از ImportError
# و حفظ همان فرمت نسخه‌های قبلی، اینجا به‌صورت سازگار نگه داشته می‌شود.
def format_service_package(volume_gb, days, plan_key=None):
    try:
        from config import FREE_TEST_PLAN_KEY
    except Exception:
        FREE_TEST_PLAN_KEY = None

    if plan_key == FREE_TEST_PLAN_KEY and volume_gb is not None and days is not None:
        volume_mb = round(float(volume_gb) * 1024)
        if volume_mb < 1024:
            volume_text = f"{volume_mb} مگابایت"
        else:
            gb_value = volume_mb / 1024
            volume_text = f"{gb_value:.0f} گیگابایت" if gb_value == int(gb_value) else f"{gb_value:.2f} گیگابایت"
        hours = float(days) * 24
        if hours < 24:
            hv = int(round(hours)) if hours == int(round(hours)) else round(hours, 1)
            days_text = f"{hv} ساعت"
        else:
            dv = int(days) if float(days) == int(float(days)) else round(float(days), 2)
            days_text = f"{dv} روز"
        return volume_text, days_text

    volume_text = f"{volume_gb} گیگابایت" if volume_gb else "طبق بسته‌ی انتخابی"
    days_text = f"{days} روز" if days else "نامحدود"
    return volume_text, days_text
from io import BytesIO

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

import database as db
import alerts
import crypto
import panels
import fsm_storage
import bot_info
from config import ADMIN_ID, FREE_TEST_PLAN_KEY
from states import AdminStates
from keyboards import (
    admin_vpn_panel_types_keyboard,
    admin_vpn_panel_list_keyboard,
    admin_vpn_panel_detail_keyboard,
    admin_vpn_panel_delete_confirm_keyboard,
    admin_vpn_panel_edit_menu_keyboard,
    admin_vpn_panel_auth_choice_keyboard,
    admin_create_service_panel_keyboard,
    admin_create_service_catalog_keyboard,
    vpn_panel_back_keyboard,
    admin_vpn_panel_types_cancel_keyboard,
    admin_vpn_panel_map_menu_keyboard,
    vpn_map_category_pick_keyboard,
    vpn_map_vip_category_pick_keyboard,
    vpn_map_vip_plans_keyboard,
    vpn_catalog_pick_keyboard,
    config_delivery_keyboard,
    vpn_map_mode_keyboard,
    vpn_direct_multiselect_keyboard,
    InlineKeyboardButton,

)
from utils import is_duplicate_action, now_tehran_naive, parse_int_in_range, TEHRAN_TZ, send_rich, send_photo_rich
from text_catalog import text as t

_LATIN_NAME_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
from handlers.admin import _is_admin, _log_fulfilled_order, AdminPermissionMiddleware

router = Router(name="panel_admin")
router.message.middleware(AdminPermissionMiddleware())
router.callback_query.middleware(AdminPermissionMiddleware())
logger = logging.getLogger(__name__)

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
    قیرتعاملی (متل بعد از پرداخت کیف‌پول/آنلاین که در چت مشتری اتفاق می‌افتد)
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
# فقط VIP و «بساز سرویس خودت» — 
# اگر هیچ نمونه‌ی پنلی برای این پلن/بسته نگاشت نشده یا غیرفعال باشد، False برمی‌گرداند
# تا مسیر همیشگی (اطلاع دستی به ادمین) دنبال شود و هیچ سفارشی گم نشود.
# ---------------------------------------------------------------------------
async def auto_fulfill_vip_via_panel(bot, uid, plan_key: str, order_id: int | None) -> bool:
    mapping = db.get_panel_map_for_plan_key(plan_key)
    if not mapping or mapping.get("panel_id") is None or mapping.get("remote_ref") is None:
        return False

    plan = db.get_effective_plan(plan_key)
    user = db.get_user(uid)
    if not plan or not user:
        return False

    panel = db.get_vpn_panel(mapping["panel_id"])
    if not panel or not panel.get("enabled"):
        return False

    # Atomic fulfillment claim: only one worker may create the remote service.
    if order_id:
        current = db.get_order(order_id)
        if current and current.get("status") == "fulfilled":
            return True
        if current and current.get("status") == "processing":
            return True
        if current and current.get("status") not in ("pending", "paid"):
            return False
        if not db.claim_order_for_processing(order_id):
            fresh = db.get_order(order_id)
            return bool(fresh and fresh.get("status") in ("processing", "fulfilled"))

    username = _service_username(uid)
    ok, link, remote_service_id, data, msg = await panels.create_service(
        panel, username, mapping["remote_ref"],
        volume_gb=plan.get("volume_gb"), days=plan.get("days"), device_limit=plan.get("user_limit"),
    )
    if not ok:
        if order_id:
            try: db.set_order_status(order_id, "paid")
            except Exception: logger.exception("failed to release VIP order claim %s", order_id)
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ خرید VIP (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از {panels.panel_label(panel)} ارسال شود ولی "
            f"ساخت سرویس در پنل ناموفق بود:\n{msg}\n"
            f"🔑 مرجع ارسال‌شده: {_display_remote_ref(mapping)}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی زیر همین سفارش استفاده کن. اگه خطا NOT_FOUND بود، احتمالاً باید "
            "این نگاشت رو دوباره از مدیریت این پنل تنظیم کنی.",
        )
        return False

    await bot.send_message(
        ADMIN_ID, f"📨 پاسخ پنل {panels.panel_label(panel)} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML",
    )

    snapshot = {"name": plan.get("name"), "volume_gb": plan.get("volume_gb"), "days": plan.get("days")}
    ctx = {"uid": uid, "plan_key": plan_key, "order_id": order_id, "order_kind": "plan",
           "panel_id": panel["id"], "panel_type": panel["panel_type"], "service_id": remote_service_id,
           "snapshot": snapshot}

    if not link:
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(panel_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_panel_manual_link)
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ سرویس در {panels.panel_label(panel)} ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    await _deliver_panel_link(bot, ctx, link)
    return True


async def auto_fulfill_custom_via_panel(bot, user: dict, order_id: int, volume, days, custom_name) -> bool:
    """Fulfill a custom order/renewal exactly once on the authoritative panel."""
    current = db.get_custom_order(order_id)
    if current and current.get("status") == "fulfilled":
        return True
    if current and current.get("status") == "processing":
        return True
    if current and current.get("status") not in ("pending", "paid"):
        return False
    if not db.claim_custom_order_for_processing(order_id):
        fresh = db.get_custom_order(order_id)
        return bool(fresh and fresh.get("status") in ("processing", "fulfilled"))

    order = db.get_custom_order(order_id)
    if order and order.get("order_type") == "renew" and order.get("target_config_id"):
        ok, msg = await _fulfill_custom_renew(bot, order, order_id, volume, days)
        try:
            await bot.send_message(ADMIN_ID, msg)
        except Exception:
            pass
        return ok

    mapping = db.get_panel_plan_map_with_panel("custom_build", 0)
    if not mapping or not mapping.get("enabled"):
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release custom order claim %s", order_id)
        return False

    panel = db.get_vpn_panel(mapping["panel_id"])
    if not panel or not panel.get("enabled"):
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release custom order claim %s", order_id)
        return False

    username = _service_username(user["telegram_id"])
    ok, link, remote_service_id, data, msg = await panels.create_service(
        panel, username, mapping["remote_ref"], volume_gb=volume, days=days,
    )
    if not ok:
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release custom order claim %s", order_id)
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ سفارش «بساز سرویس خودت» (کیف‌پول/پرداخت آنلاین) قرار بود خودکار از {panels.panel_label(panel)} ارسال "
            f"شود ولی ساخت سرویس ناموفق بود:\n{msg}\n"
            f"🔑 مرجع ارسال‌شده: {_display_remote_ref(mapping)}\n\n"
            "لطفاً از دکمه‌ی ارسال دستی این سفارش استفاده کن.",
        )
        return False

    await bot.send_message(
        ADMIN_ID, f"📨 پاسخ پنل {panels.panel_label(panel)} (ارسال خودکار بعد از پرداخت):\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML",
    )

    snapshot = {"name": custom_name or "سرویس سفارشی", "volume_gb": volume, "days": days}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "panel_id": panel["id"], "panel_type": panel["panel_type"], "service_id": remote_service_id,
           "snapshot": snapshot}

    if not link:
        admin_state = _admin_fsm(bot)
        if admin_state:
            await admin_state.update_data(panel_pending_ctx=ctx)
            await admin_state.set_state(AdminStates.waiting_panel_manual_link)
        await bot.send_message(
            ADMIN_ID,
            f"⚠️ سرویس در {panels.panel_label(panel)} ساخته شد (خودکار، بعد از پرداخت) ولی لینک ساب به‌صورت "
            "خودکار پیدا نشد.\nلطفاً لینک رو از پاسخ بالا کپی و همینجا برام بفرست:",
        )
        return True

    await _deliver_panel_link(bot, ctx, link)
    return True


# ---------------------------------------------------------------------------
# 📂 قدم اول: انتخاب نوع پنل (همه هم‌زمان قابل مدیریت) و لیست نمونه‌ها
# ---------------------------------------------------------------------------

_USERNAME_ALPHABET = string.ascii_lowercase + string.digits  # فقط حروف کوچک+عدد، چون بعضی پنل‌ها حروف بزرگ رو با کوچک یکی می‌بینن
_USERNAME_INVALID_RE = re.compile(r"[^a-z0-9_]+")
_USERNAME_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _to_base36(num: int) -> str:
    """تبدیل عدد به پایه‌ی ۳۶ (حروف کوچک + عدد) برای کوتاه‌تر شدن کد."""
    num = int(num)
    if num == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    n = abs(num)
    while n:
        n, r = divmod(n, 36)
        out.append(digits[r])
    return "".join(reversed(out))


def _service_username(telegram_id) -> str:
    """نام سرویس با پیشوند تنظیم‌شده در «اطلاعات ربات» + دقیقاً ۶ رقم عددی."""
    raw_prefix = (bot_info.get("config_name_prefix") or "tg").strip()
    prefix = re.sub(r"[^A-Za-z0-9_]+", "", raw_prefix) or "tg"
    code = secrets.randbelow(900000) + 100000
    return f"{prefix}_{code}"

@router.callback_query(F.data == "admin_vpn_panels")
async def open_vpn_panel_types(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        "🖥 مدیریت پنل‌های VPN\n\n"
        "مرزبان / پاسارگارد / 3X-UI می‌توانند هم‌زمان فعال باشند و هرکدام می‌تواند چند نمونه داشته باشد.\n"
        "یک نوع رو انتخاب کن:",
        reply_markup=admin_vpn_panel_types_keyboard(),
    )
    await callback.answer()


@router.message(F.text == "🖥 مدیریت پنل‌های VPN")
async def menu_admin_vpn_panels(message: types.Message):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(
        "🖥 مدیریت پنل‌های VPN\n\n"
        "مرزبان / پاسارگارد / 3X-UI می‌توانند هم‌زمان فعال باشند و هرکدام می‌تواند چند نمونه داشته باشد.\n"
        "یک نوع رو انتخاب کن:",
        reply_markup=admin_vpn_panel_types_keyboard(),
    )


@router.callback_query(F.data.startswith("vpntype|"))
async def open_vpn_panel_type_list(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_type = callback.data.split("|")[1]
    if panel_type not in panels.PANEL_TYPES:
        await callback.answer("❌ نوع پنل نامعتبر.", show_alert=True)
        return
    instances = db.list_vpn_panels(panel_type=panel_type)
    label = panels.PANEL_TYPE_LABELS[panel_type]
    text = f"🖥 نمونه‌های پنل {label}"
    if not instances:
        text += "\n\nهنوز هیچ نمونه‌ای از این نوع اضافه نشده. می‌تونی چند نمونه هم‌زمان از این نوع اضافه کنی."
    await callback.message.edit_text(text, reply_markup=admin_vpn_panel_list_keyboard(panel_type, instances))
    await callback.answer()


@router.callback_query(F.data.startswith("vpndetail|"))
async def open_vpn_panel_detail(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    status = "🟢 فعال" if panel.get("enabled") else "🔴 غیرفعال"
    text = (
        f"🖥 {panels.panel_label(panel)}\n"
        f"وضعیت: {status}\n"
        f"🌐 ادرس: <code>{html.escape(panel.get('base_url') or '')}</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_vpn_panel_detail_keyboard(panel))
    await callback.answer()


@router.callback_query(F.data.startswith("vpntest|"))
async def vpn_panel_test(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.answer("⏳ در حال تست اتصال...")
    ok, data, msg = await panels.test_connection(panel)
    if not ok:
        await callback.message.answer(f"❌ اتصال ناموفق: {msg}", reply_markup=vpn_panel_back_keyboard(panel_id))
        return
    await callback.message.answer(
        f"✅ اتصال به {panels.panel_label(panel)} برقرار است.\n<pre>{_pretty(data)}</pre>",
        parse_mode="HTML", reply_markup=vpn_panel_back_keyboard(panel_id),
    )


@router.callback_query(F.data.startswith("vpntoggle|"))
async def vpn_panel_toggle(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    db.update_vpn_panel(panel_id, enabled=not panel.get("enabled"))
    panel = db.get_vpn_panel(panel_id)
    status = "🟢 فعال" if panel.get("enabled") else "🔴 غیرفعال"
    text = (
        f"🖥 {panels.panel_label(panel)}\n"
        f"وضعیت: {status}\n"
        f"🌐 ادرس: <code>{html.escape(panel.get('base_url') or '')}</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_vpn_panel_detail_keyboard(panel))
    await callback.answer("✅ وضعیت به‌روز شد.")


@router.callback_query(F.data.startswith("vpndelete|"))
async def vpn_panel_delete_confirm(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"⚠️ مطمئنی می‌خوای {panels.panel_label(panel)} حذف شود؟\n"
        "تمام نگاشت‌های پلن/بسته مربوط به این نمونه هم حذف می‌شوند (سرویس‌های قبلی سالم می‌مانند).",
        reply_markup=admin_vpn_panel_delete_confirm_keyboard(panel_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpndeleteconfirm|"))
async def vpn_panel_delete(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    panel_type = panel["panel_type"]
    db.delete_vpn_panel(panel_id)
    instances = db.list_vpn_panels(panel_type=panel_type)
    await callback.message.edit_text(
        f"🗑 حذف شد. نمونه‌های فعلی پنل {panels.PANEL_TYPE_LABELS[panel_type]}:",
        reply_markup=admin_vpn_panel_list_keyboard(panel_type, instances),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# ➕ افزودن نمونه‌ی جدید از یک نوع پنل (FSM)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("vpnadd|"))
async def vpn_panel_add_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    panel_type = callback.data.split("|")[1]
    if panel_type not in panels.PANEL_TYPES:
        await callback.answer("❌ نوع پنل نامعتبر.", show_alert=True)
        return
    await state.update_data(new_panel_type=panel_type)
    await state.set_state(AdminStates.waiting_panel_name)
    await callback.message.edit_text(
        f"➕ افزودن پنل {panels.PANEL_TYPE_LABELS[panel_type]} جدید\n\n"
        "یک نام دلخواه برای این نمونه بفرست (فقط برای تشخیص خودت در لیست، مثلاً «سرور 1 المان»):",
        reply_markup=admin_vpn_panel_types_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_panel_name)
async def vpn_panel_add_name(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ نام خالی معتبر نیست. دوباره بفرست:")
        return
    await state.update_data(new_panel_name=name)
    await state.set_state(AdminStates.waiting_panel_base_url)
    await message.answer("🌐 ادرس پایه (base URL) این پنل رو بفرست (مثلاً https://panel.example.com):")


@router.message(AdminStates.waiting_panel_base_url)
async def vpn_panel_add_base_url(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    base_url = (message.text or "").strip()
    if not base_url.lower().startswith(("http://", "https://")):
        await message.answer("❌ این یک ادرس معتبر نیست؛ لطفاً با http یا https بفرست:")
        return
    data = await state.get_data()
    panel_type = data.get("new_panel_type")
    await state.update_data(new_panel_base_url=base_url.rstrip("/"))
    if panel_type == "shahrah":
        await state.update_data(new_panel_auth_method="api_key")
        await state.set_state(AdminStates.waiting_panel_api_key)
        await message.answer("🔑 API Key این نمونه رو بفرست:")
    elif len(panels.PANEL_AUTH_METHODS.get(panel_type, ())) > 1:
        # 🆕 این نوع پنل هم از یوزرنیم/پسورد و هم از API Key پشتیبانی می‌کند؛
        # ادمین انتخاب می‌کند کدام‌یک برای این نمونه استفاده شود.
        await message.answer(
            "🔌 روش اتصال به این پنل را انتخاب کن:",
            reply_markup=admin_vpn_panel_auth_choice_keyboard(panel_type),
        )
    else:
        await state.update_data(new_panel_auth_method="userpass")
        await state.set_state(AdminStates.waiting_panel_username)
        await message.answer("👤 نام کاربری این نمونه رو بفرست:")


@router.callback_query(F.data.startswith("vpnauthadd|"))
async def vpn_panel_add_auth_choice(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_type, auth_method = callback.data.split("|")
    await state.update_data(new_panel_auth_method=auth_method)
    if auth_method == "api_key":
        await state.set_state(AdminStates.waiting_panel_api_key)
        await callback.message.edit_text("🔑 API Key این نمونه رو بفرست:")
    else:
        await state.set_state(AdminStates.waiting_panel_username)
        await callback.message.edit_text("👤 نام کاربری این نمونه رو بفرست:")
    await callback.answer()


@router.message(AdminStates.waiting_panel_api_key)
async def vpn_panel_add_api_key(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    api_key = (message.text or "").strip()
    if not api_key:
        await message.answer("❌ API Key خالی معتبر نیست. دوباره بفرست:")
        return
    data = await state.get_data()
    panel_id = db.create_vpn_panel(
        data["new_panel_type"], data["new_panel_name"], data["new_panel_base_url"], api_key=api_key,
        auth_method=data.get("new_panel_auth_method", "api_key"),
    )
    await state.clear()
    panel = db.get_vpn_panel(panel_id)
    await message.answer(
        f"✅ پنل {panels.panel_label(panel)} اضافه شد.",
        reply_markup=admin_vpn_panel_detail_keyboard(panel),
    )


@router.message(AdminStates.waiting_panel_username)
async def vpn_panel_add_username(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    username = (message.text or "").strip()
    if not username:
        await message.answer("❌ نام کاربری خالی معتبر نیست. دوباره بفرست:")
        return
    await state.update_data(new_panel_username=username)
    await state.set_state(AdminStates.waiting_panel_password)
    await message.answer("🔐 رمز عبور این نمونه رو بفرست:")


@router.message(AdminStates.waiting_panel_password)
async def vpn_panel_add_password(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    password = (message.text or "").strip()
    if not password:
        await message.answer("❌ رمز عبور خالی معتبر نیست. دوباره بفرست:")
        return
    data = await state.get_data()
    panel_id = db.create_vpn_panel(
        data["new_panel_type"], data["new_panel_name"], data["new_panel_base_url"],
        username=data["new_panel_username"], password=password,
        auth_method=data.get("new_panel_auth_method", "userpass"),
    )
    await state.clear()
    panel = db.get_vpn_panel(panel_id)
    await message.answer(
        f"✅ پنل {panels.panel_label(panel)} اضافه شد.",
        reply_markup=admin_vpn_panel_detail_keyboard(panel),
    )


# ---------------------------------------------------------------------------
# ✏️ ویرایش یک نمونه‌ی موجود
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("vpnedit|"))
async def vpn_panel_edit_menu(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ ویرایش {panels.panel_label(panel)}\nیک فیلد رو انتخاب کن:",
        reply_markup=admin_vpn_panel_edit_menu_keyboard(panel),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpnauthswitch|"))
async def vpn_panel_auth_switch(callback: types.CallbackQuery):
    """جابه‌جایی روش اتصال یک نمونه‌ی موجود پنل (مرزبان/پاسارگارد) بین
    یوزرنیم/پسورد و API Key. مقدار فیلدهای طرف مقابل حذف نمی‌شود (اگر قبلاً
    پر شده بود) تا اگر ادمین دوباره برگردد، لازم نباشد از نو واردشان کند؛
    فقط اگر فیلدهای لازم برای روش جدید هنوز خالی باشد، تذکر داده می‌شود."""
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    current = panel.get("auth_method") or "userpass"
    new_method = "api_key" if current == "userpass" else "userpass"
    db.update_vpn_panel(panel_id, auth_method=new_method)
    panel = db.get_vpn_panel(panel_id)
    missing_warning = ""
    if new_method == "api_key" and not panel.get("api_key"):
        missing_warning = "\n\n⚠️ هنوز API Key ثبت نشده؛ از همین منو مقدارش رو وارد کن."
    elif new_method == "userpass" and not (panel.get("username") and panel.get("password")):
        missing_warning = "\n\n⚠️ هنوز نام کاربری/رمز عبور ثبت نشده؛ از همین منو مقدارشون رو وارد کن."
    await callback.message.edit_text(
        f"✅ روش اتصال {panels.panel_label(panel)} تغییر کرد.{missing_warning}\nیک فیلد رو انتخاب کن:",
        reply_markup=admin_vpn_panel_edit_menu_keyboard(panel),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpneditfield|"))
async def vpn_panel_edit_field_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, field = callback.data.split("|")
    panel_id = int(panel_id_str)
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    field_labels = {
        "name": "نام", "base_url": "ادرس پایه (base URL)", "api_key": "API Key",
        "username": "نام کاربری", "password": "رمز عبور",
    }
    await state.update_data(edit_panel_id=panel_id, edit_panel_field=field)
    await state.set_state(AdminStates.waiting_panel_edit_field)
    await callback.message.edit_text(
        f"✅ {field_labels.get(field, field)} جدید رو برای {panels.panel_label(panel)} بفرست:",
        reply_markup=vpn_panel_back_keyboard(panel_id),
    )
    await callback.answer()


@router.message(AdminStates.waiting_panel_edit_field)
async def vpn_panel_edit_field_received(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("❌ مقدار خالی معتبر نیست. دوباره بفرست:")
        return
    data = await state.get_data()
    panel_id = data.get("edit_panel_id")
    field = data.get("edit_panel_field")
    panel = db.get_vpn_panel(panel_id) if panel_id else None
    if not panel or not field:
        await message.answer("❌ مشکلی پیش آمد؛ لطفاً از ابتدا دوباره تلاش کن.")
        await state.clear()
        return
    if field == "base_url":
        value = value.rstrip("/")
    db.update_vpn_panel(panel_id, **{field: value})
    await state.clear()
    panel = db.get_vpn_panel(panel_id)
    await message.answer(
        f"✅ ذخیره شد. {panels.panel_label(panel)} به‌روز شد.",
        reply_markup=admin_vpn_panel_detail_keyboard(panel),
    )


# ---------------------------------------------------------------------------
# 🗂 نگاشت پلن/بسته → این نمونه‌ی مشخص از پنل
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("vpnmap|"))
async def vpn_panel_map_menu(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗂 نگاشت پلن/بسته به {panels.panel_label(panel)}\n\n"
        "کدام بخش رو می‌خوای به این نمونه وصل کنی؟",
        reply_markup=admin_vpn_panel_map_menu_keyboard(panel_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpnmapdelplan|"))
async def vpn_map_delete_plan(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    _, panel_id, plan_id = callback.data.split("|")
    plan = db.get_vip_plan_by_id(int(plan_id))
    if not plan:
        await callback.answer("❌ پلن پیدا نشد.", show_alert=True); return
    db.delete_panel_plan_map("vip_plan", int(plan_id))
    await callback.answer("🗑 نگاشت پلن حذف شد.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=vpn_map_vip_plans_keyboard(plan["category_id"], db.get_vip_plans(plan["category_id"]), int(panel_id)))

@router.callback_query(F.data.startswith("vpnmapdelcat|"))
async def vpn_map_delete_category(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id): return
    _, panel_id, scope, scope_id = callback.data.split("|")
    if scope != "vip_category" or not scope_id.isdigit():
        await callback.answer("❌ نگاشت قابل حذف پیدا نشد.", show_alert=True); return
    db.delete_panel_plan_map("vip_category", int(scope_id))
    await callback.answer("🗑 نگاشت پیش‌فرض دسته حذف شد.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=vpn_map_vip_plans_keyboard(int(scope_id), db.get_vip_plans(int(scope_id)), int(panel_id)))

@router.callback_query(F.data.startswith("vpnmapvip|"))
async def vpn_map_vip(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    cats = db.get_vip_categories()
    if not cats:
        await callback.answer("هنوز هیچ دسته‌بندی VIP‌ای ساخته نشده.", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗂 یک دسته‌بندی VIP رو انتخاب کن تا پلن‌های داخلش رو ببینی و برای هرکدوم جداگانه به {panels.panel_label(panel)} وصلشون کنی:",
        reply_markup=vpn_map_vip_category_pick_keyboard(cats, panel_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpnmapvipcat|"))
async def vpn_map_vip_cat_pick(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, cat_id_str = callback.data.split("|")
    panel_id, cat_id = int(panel_id_str), int(cat_id_str)
    plans_list = db.get_vip_plans(cat_id)
    if not plans_list:
        await callback.answer("این دسته‌بندی هنوز هیچ پلنی نداره.", show_alert=True)
        return
    await callback.message.edit_text(
        "یک پلن رو انتخاب کن تا بسته/تمپلیت متناظرش از این نمونه پنل رو مشخص کنی:",
        reply_markup=vpn_map_vip_plans_keyboard(cat_id, plans_list, panel_id),
    )
    await callback.answer()


def _display_remote_ref(mapping: dict) -> str:
    """برای نگاشت‌های «بدون تمپلیت» به‌جای دیکته‌ی خام JSON، همان خلاصه‌ی خوانا
    (remote_name، مثل «بدون تمپلیت (۳ اینباند)») نشان داده می‌شود."""
    ref = mapping.get("remote_ref") or ""
    if isinstance(ref, str) and ref.startswith("direct:"):
        return mapping.get("remote_name") or "بدون تمپلیت"
    return str(ref)


async def _open_catalog_picker(callback: types.CallbackQuery, state: FSMContext, panel: dict, panel_id: int,
                                scope: str, scope_id: int, prompt: str):
    await callback.answer("⏳ در حال دریافت لیست از پنل...")
    choices, msg = await panels.get_catalog(panel)
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=vpn_panel_back_keyboard(panel_id))
        return
    await state.update_data(panel_map_scope=scope, panel_map_scope_id=scope_id,
                             panel_map_choices=choices, panel_map_panel_id=panel_id)
    await callback.message.answer(prompt, reply_markup=vpn_catalog_pick_keyboard(choices, panel_id))


async def _open_direct_picker(callback: types.CallbackQuery, state: FSMContext, panel: dict, panel_id: int,
                               scope: str, scope_id: int, prompt: str):
    await callback.answer("⏳ در حال دریافت اینباند/گروه‌های زنده‌ی پنل...")
    choices, msg = await panels.get_direct_catalog(panel)
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=vpn_panel_back_keyboard(panel_id))
        return
    await state.update_data(panel_map_scope=scope, panel_map_scope_id=scope_id,
                             panel_map_direct_choices=choices, panel_map_direct_selected=[], panel_map_panel_id=panel_id)
    await callback.message.answer(
        prompt + "\n\nچندتا از موارد زیر رو انتخاب کن (حداقل یکی)، بعد «✅ ذخیره» رو بزن:",
        reply_markup=vpn_direct_multiselect_keyboard(choices, panel_id, set()),
    )


async def _start_mapping(callback: types.CallbackQuery, state: FSMContext, panel: dict, panel_id: int,
                          scope: str, scope_id: int, prompt: str):
    """قدم اول نگاشت: اگر این نوع پنل حالت «بدون تمپلیت» را هم پشتیبانی کند
    (مرزبان/پاسارگارد)، اول از ادمین می‌پرسد کدام روش را می‌خواهد؛ شاهراه
    (که تمام API‌اش plan-محور است، نه inbound/group-محور) همیشه مستقیم می‌رود
    سراغ لیست بسته‌های خودش، دقیقاً مثل قبل."""
    if panel.get("panel_type") not in ("marzban", "pasargad"):
        await _open_catalog_picker(callback, state, panel, panel_id, scope, scope_id, prompt)
        return
    await state.update_data(panel_map_scope=scope, panel_map_scope_id=scope_id,
                             panel_map_panel_id=panel_id, panel_map_prompt=prompt)
    await callback.message.answer(
        "🔌 برای این نگاشت می‌خوای از یک تمپلیت آماده‌ی پنل استفاده کنی، یا مستقیم از اینباند/گروه (بدون نیاز به ساختن تمپلیت در پنل)؟",
        reply_markup=vpn_map_mode_keyboard(panel_id, supports_direct=True),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpnmapmode|"))
async def vpn_map_mode_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, mode = callback.data.split("|")
    panel_id = int(panel_id_str)
    panel = db.get_vpn_panel(panel_id)
    data = await state.get_data()
    scope = data.get("panel_map_scope")
    scope_id = data.get("panel_map_scope_id")
    prompt = data.get("panel_map_prompt") or f"یک گزینه از {panels.panel_label(panel)} رو انتخاب کن:"
    if not panel or not scope or scope_id is None:
        await callback.answer("❌ این مرحله منقضی شده؛ دوباره از منوی نگاشت شروع کن.", show_alert=True)
        return
    if mode == "direct":
        await _open_direct_picker(callback, state, panel, panel_id, scope, scope_id, prompt)
    else:
        await _open_catalog_picker(callback, state, panel, panel_id, scope, scope_id, prompt)


@router.callback_query(F.data.startswith("vpndirecttoggle|"))
async def vpn_direct_toggle(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, idx_str = callback.data.split("|")
    panel_id, idx = int(panel_id_str), int(idx_str)
    data = await state.get_data()
    choices = data.get("panel_map_direct_choices") or []
    if data.get("panel_map_panel_id") != panel_id or not choices:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return
    selected = set(data.get("panel_map_direct_selected") or [])
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(panel_map_direct_selected=list(selected))
    await callback.message.edit_reply_markup(reply_markup=vpn_direct_multiselect_keyboard(choices, panel_id, selected))
    await callback.answer()


@router.callback_query(F.data.startswith("vpndirectconfirm|"))
async def vpn_direct_confirm(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    data = await state.get_data()
    choices = data.get("panel_map_direct_choices") or []
    selected_idx = set(data.get("panel_map_direct_selected") or [])
    scope = data.get("panel_map_scope")
    scope_id = data.get("panel_map_scope_id")
    if data.get("panel_map_panel_id") != panel_id or not scope or scope_id is None:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return
    if not selected_idx:
        await callback.answer("❌ حداقل یک مورد رو انتخاب کن.", show_alert=True)
        return

    chosen = [c for c in choices if c["idx"] in selected_idx]
    panel = db.get_vpn_panel(panel_id)
    if panel.get("panel_type") == "pasargad":
        payload = {"group_ids": [c["raw_id"] for c in chosen]}
        summary = f"بدون تمپلیت ({len(chosen)} گروه)"
    else:
        inbounds: dict[str, list] = {}
        for c in chosen:
            inbounds.setdefault(c["protocol"], []).append(c["raw_id"])
        payload = {"inbounds": inbounds}
        summary = f"بدون تمپلیت ({len(chosen)} اینباند)"

    remote_ref = "direct:" + json.dumps(payload, ensure_ascii=False)
    db.set_panel_plan_map(scope, int(scope_id), panel_id, remote_ref, summary)

    cleared_note = ""
    if scope == "vip_category":
        db.clear_panel_plan_overrides_for_category(int(scope_id))
        cleared_note = (
            "\n\n♻️ نگاشت‌های اختصاصی قدیمی پلن‌های این دسته (اگر وجود داشت) پاک شد تا این "
            "پیش‌فرض واقعاً روی همه‌ی پلن‌های این دسته اعمال شود."
        )

    await callback.message.edit_text(
        f"✅ ذخیره شد: این بخش از این پس بدون هیچ تمپلیتی، مستقیم با «{summary}» از {panels.panel_label(panel)} ساخته می‌شود.{cleared_note}",
        reply_markup=admin_vpn_panel_map_menu_keyboard(panel_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vpnmapvipplan|"))
async def vpn_map_vip_plan_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, cat_id_str, plan_id_str = callback.data.split("|")
    panel_id, plan_id = int(panel_id_str), int(plan_id_str)
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await _start_mapping(
        callback, state, panel, panel_id, "vip_plan", plan_id,
        f"یک بسته/تمپلیت از {panels.panel_label(panel)} رو انتخاب کن تا به این پلن مشخص وصل بشه:",
    )


@router.callback_query(F.data.startswith("vpnmapcustom|"))
async def vpn_map_custom_build(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await _start_mapping(
        callback, state, panel, panel_id, "custom_build", 0,
        f"🧩 این بسته/تمپلیت، پیش‌فرض همه‌ی سفارش‌های «بساز سرویس خودت» از {panels.panel_label(panel)} خواهد بود. یک بسته انتخاب کن:",
    )


@router.callback_query(F.data.startswith("vpnmapfreetest|"))
async def vpn_map_free_test(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.split("|")[1])
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await _start_mapping(
        callback, state, panel, panel_id, "free_test", 0,
        f"🧪 این بسته/تمپلیت برای همه‌ی سفارش‌های «تست رایگان» از {panels.panel_label(panel)} استفاده خواهد شد. یک بسته کوچک انتخاب کن:",
    )


@router.callback_query(F.data.startswith("vpnmapcat|"))
async def vpn_map_cat_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, scope, cat_id_str = callback.data.split("|")
    panel_id, cat_id = int(panel_id_str), int(cat_id_str)
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await _start_mapping(
        callback, state, panel, panel_id, scope, cat_id,
        f"یک بسته/تمپلیت از {panels.panel_label(panel)} رو انتخاب کن تا به این دسته‌بندی وصل بشه:",
    )


@router.callback_query(F.data.startswith("vpncatalogpick|"))
async def vpn_catalog_pick_set(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, idx_str = callback.data.split("|")
    panel_id, idx = int(panel_id_str), int(idx_str)
    data = await state.get_data()
    choices = data.get("panel_map_choices") or []
    scope = data.get("panel_map_scope")
    scope_id = data.get("panel_map_scope_id")
    stored_panel_id = data.get("panel_map_panel_id")
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen or not scope or scope_id is None or stored_panel_id != panel_id:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return

    db.set_panel_plan_map(scope, int(scope_id), panel_id, chosen["ref"], chosen["name"])

    # fix: اگر این یک نگاشت پیش‌فرض برای کل دسته بود (scope=vip_category)، نگاشت‌های اختصاصی
    # قدیمی‌تر تک‌تک پلن‌های همان دسته را هم پاک می‌کنیم تا پیش‌فرض جدید واقعاً
    # روی همه‌ی پلن‌های دسته اعمال شود (ورنه فقط برای پلن‌های بدون نگاشت اختصاصی).
    cleared_note = ""
    if scope == "vip_category":
        db.clear_panel_plan_overrides_for_category(int(scope_id))
        cleared_note = (
            "\n\n♻️ نگاشت‌های اختصاصی قدیمی پلن‌های این دسته (اگر وجود داشت) پاک شد تا این "
            "پیش‌فرض واقعاً روی همه‌ی پلن‌های این دسته اعمال شود."
        )

    panel = db.get_vpn_panel(panel_id)
    await callback.message.edit_text(
        f"✅ ذخیره شد: این بخش از این پس به «{chosen['name']}» (ref: {chosen['ref']}) از {panels.panel_label(panel)} وصل می‌شود.{cleared_note}",
        reply_markup=admin_vpn_panel_map_menu_keyboard(panel_id),
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 📤 ارسال خودکار بعد از تایید رسید (کارت‌به‌کارت — بر اساس نگاشت ازقبل تعیین‌شده)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("panelsend|"))
async def panel_send_service(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    if is_duplicate_action(f"panelsend_{callback.data}"):
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
    # panel_plan_map ستون enabled ندارد؛ وجود panel_id و remote_ref یعنی نگاشت
    # کامل است و وضعیت فعال بودن نمونه از vpn_panels خوانده می‌شود.
    if not mapping or mapping.get("panel_id") is None or mapping.get("remote_ref") is None:
        await callback.answer("❌ برای این پلن/دسته هنوز پنل و تمپلیت (مرجع) به‌طور کامل نگاشت نشده.", show_alert=True)
        return
    panel = db.get_vpn_panel(mapping["panel_id"])
    if not panel:
        await callback.answer("❌ نمونه پنل نگاشت‌شده پیدا نشد.", show_alert=True)
        return
    if not panel.get("enabled"):
        await callback.answer("❌ پنل نگاشت‌شده غیرفعال است.", show_alert=True)
        return

    if order_id:
        current = db.get_order(order_id)
        if current and current.get("status") == "fulfilled":
            await callback.answer("✅ این سفارش قبلاً ارسال شده است.", show_alert=True)
            return
        if current and current.get("status") == "processing":
            await callback.answer("⏳ این سفارش در حال ارسال است.", show_alert=True)
            return
        if current and current.get("status") not in ("pending", "paid"):
            await callback.answer("⚠️ وضعیت این سفارش اجازه ارسال نمی‌دهد.", show_alert=True)
            return
        if not db.claim_order_for_processing(order_id):
            fresh = db.get_order(order_id)
            await callback.answer(
                "⏳ این سفارش قبلاً توسط یک ارسال دیگر در حال پردازش است." if fresh and fresh.get("status") == "processing" else "⚠️ سفارش قابل ارسال نیست.",
                show_alert=True,
            )
            return

    await callback.answer(f"⏳ در حال ساخت سرویس در {panels.panel_label(panel)}...")
    username = _service_username(uid)
    ok, link, remote_service_id, data, msg = await panels.create_service(
        panel, username, mapping["remote_ref"], volume_gb=plan.get("volume_gb"), days=plan.get("days"), device_limit=plan.get("user_limit"),
    )
    if not ok:
        if order_id:
            try:
                db.set_order_status(order_id, "paid")
            except Exception:
                logger.exception("failed to release order claim %s", order_id)
        await callback.message.answer(
            f"❌ ساخت سرویس در {panels.panel_label(panel)} ناموفق بود: {msg}\n\n"
            f"🔑 مرجع ارسال‌شده: <code>{html.escape(_display_remote_ref(mapping))}</code>\n"
            "اگه این خطا مربوط به پیدا‌نشدن بسته/تمپلیت باشد، از مدیریت همین نمونه پنل دوباره نگاشت کن.",
            parse_mode="HTML",
        )
        return

    await callback.message.answer(f"📨 پاسخ {panels.panel_label(panel)}:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")

    snapshot = {"name": plan.get("name"), "volume_gb": plan.get("volume_gb"), "days": plan.get("days")}
    ctx = {"uid": uid, "plan_key": plan_key, "order_id": order_id, "order_kind": "plan",
           "panel_id": panel["id"], "panel_type": panel["panel_type"], "service_id": remote_service_id,
           "snapshot": snapshot}

    if not link:
        await state.update_data(panel_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_panel_manual_link)
        await callback.message.answer(
            "⚠️ سرویس ساخته شد ولی نتونستم لینک ساب رو خودکار پیدا کنم.\n"
            "لطفاً لینک ساب رو از پاسخ بالا کپی و همینجا ارسال کن:"
        )
        return

    await _deliver_panel_link(callback.bot, ctx, link)


# ---------------------------------------------------------------------------
# 🧩 ساخت خودکار «بساز سرویس خودت» از یک پنل انتخابی (کارت‌به‌کارت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("panelcustom_"))
async def panel_custom_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.replace("panelcustom_", ""))
    order = db.get_custom_order(order_id)
    if order is None:
        await callback.answer("❌ سفارش یافت نشد.", show_alert=True)
        return
    if order.get("order_type") == "renew" and order.get("target_config_id"):
        # سفارش تمدید («بساز سرویس خودت» با کارت‌به‌کارت): برخلاف سفارش‌های جدید،
        # اینجا نباید کیبورد انتخاب پنل/تمپلیت نشون داده بشه و یک سرویس تازه
        # ساخته بشه؛ چون سرویس مقصد (و پنل/سرویس‌آیدی‌اش) از قبل روی همون کانفیگ
        # قبلی (target_config_id) مشخصه. قبلاً این مسیر همیشه کیبورد انتخاب پنل
        # رو نشون می‌داد و به panel_custom_pick می‌رسید که بدون توجه به
        # order_type همیشه panels.create_service (ساخت سرویس تازه) رو صدا
        # می‌زد؛ همین باعث می‌شد تمدیدهایی که ادمین با دکمه‌ی «ساخت خودکار از
        # یک پنل» (بعد از تایید رسید کارت‌به‌کارت) ارسال می‌کرد، به‌جای تمدید
        # سرویس قبلی، یک سرویس کاملاً جدید و جدا بسازند.
        await callback.answer("⏳ در حال تمدید همان سرویس قبلی از داخل پنل...")
        ok, msg = await _fulfill_custom_renew(callback.bot, order, order_id, order["volume_gb"], order["days"])
        await callback.message.answer(msg)
        if not ok:
            await callback.message.answer(
                "برای تمدید این سفارش می‌تونی به‌جاش از دکمه‌ی «📤 شروع ارسال کانفیگ — دستی» "
                "روی پیام اصلی سفارش استفاده کنی."
            )
        return
    enabled_panels = db.list_vpn_panels(enabled_only=True)
    if not enabled_panels:
        await callback.answer("❌ هیچ پنل فعالی وجود ندارد.", show_alert=True)
        return
    await state.update_data(panel_custom_order_id=order_id)
    buttons = [
        [InlineKeyboardButton(
            text=f"{panels.panel_label(p)}", callback_data=f"panelcustompanel|{order_id}|{p['id']}",
        )]
        for p in enabled_panels
    ]
    await callback.answer()
    await callback.message.answer(
        f"🧩 سفارش «بساز سرویس خودت» #{order_id} — {order['volume_gb']} گیگ / {order['days']} روز\n"
        "یکی از پنل‌های فعال رو انتخاب کن:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("panelcustompanel|"))
async def panel_custom_pick_panel(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, order_id_str, panel_id_str = callback.data.split("|")
    order_id, panel_id = int(order_id_str), int(panel_id_str)
    order = db.get_custom_order(order_id)
    if order and order.get("order_type") == "renew" and order.get("target_config_id"):
        # محافظتی: اگر این کیبورد قبل از رفع باگ بالا برای ادمین ارسال شده و
        # هنوز روی صفحه‌اش باز مونده، دوباره مسیر ساخت سرویس تازه رو طی نکنه.
        await callback.answer("⏳ در حال تمدید همان سرویس قبلی از داخل پنل...")
        ok, msg = await _fulfill_custom_renew(callback.bot, order, order_id, order["volume_gb"], order["days"])
        await callback.message.answer(msg)
        return
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.answer("⏳ در حال دریافت لیست از پنل...")
    choices, msg = await panels.get_catalog(panel)
    if not choices:
        await callback.message.answer(f"❌ {msg}")
        return
    await state.update_data(panel_custom_order_id=order_id, panel_custom_panel_id=panel_id,
                             panel_custom_choices=choices)
    buttons = [
        [InlineKeyboardButton(text=c["label"], callback_data=f"panelcustompick|{order_id}|{panel_id}|{c['idx']}")]
        for c in choices
    ]
    await callback.message.answer(
        f"نزدیک‌ترین بسته/تمپلیت از {panels.panel_label(panel)} رو انتخاب کن:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("panelcustompick|"))
async def panel_custom_pick(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, order_id_str, panel_id_str, idx_str = callback.data.split("|")
    order_id, panel_id, idx = int(order_id_str), int(panel_id_str), int(idx_str)
    data_state = await state.get_data()
    choices = data_state.get("panel_custom_choices") or []
    chosen = next((c for c in choices if c["idx"] == idx), None)
    order = db.get_custom_order(order_id)
    panel = db.get_vpn_panel(panel_id)
    if not chosen or not order or not panel:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره تلاش کن.", show_alert=True)
        return
    if order.get("order_type") == "renew" and order.get("target_config_id"):
        # محافظتی: اگر این کیبورد قبل از رفع باگ بالا برای ادمین ارسال شده و
        # هنوز روی صفحه‌اش باز مونده، دوباره مسیر ساخت سرویس تازه رو طی نکنه.
        await callback.answer("⏳ در حال تمدید همان سرویس قبلی از داخل پنل...")
        ok, msg = await _fulfill_custom_renew(callback.bot, order, order_id, order["volume_gb"], order["days"])
        await callback.message.answer(msg)
        return
    user = db.get_user_by_id(order["user_id"])
    if not user:
        await callback.answer("❌ کاربر یافت نشد.", show_alert=True)
        return

    await callback.answer(f"⏳ در حال ساخت سرویس در {panels.panel_label(panel)}...")
    username = _service_username(user["telegram_id"])
    ok, link, remote_service_id, data, msg = await panels.create_service(
        panel, username, chosen["ref"], volume_gb=order["volume_gb"], days=order["days"],
    )
    if not ok:
        await callback.message.answer(
            f"❌ ساخت سرویس در {panels.panel_label(panel)} ناموفق بود: {msg}\n"
            f"🔑 مرجع ارسال‌شده: <code>{html.escape(chosen['ref'])}</code>",
            parse_mode="HTML",
        )
        return

    await callback.message.answer(f"📨 پاسخ {panels.panel_label(panel)}:\n<pre>{_pretty(data)}</pre>", parse_mode="HTML")

    snapshot = {"name": order.get("custom_name") or "سرویس سفارشی",
                "volume_gb": order["volume_gb"], "days": order["days"]}
    ctx = {"uid": user["telegram_id"], "plan_key": None, "order_id": order_id, "order_kind": "custom",
           "panel_id": panel["id"], "panel_type": panel["panel_type"], "service_id": remote_service_id,
           "snapshot": snapshot}

    if not link:
        await state.update_data(panel_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_panel_manual_link)
        await callback.message.answer(
            "⚠️ سرویس ساخته شد ولی نتونستم لینک ساب رو خودکار پیدا کنم.\n"
            "لطفاً لینک ساب رو از پاسخ بالا کپی و همینجا ارسال کن:"
        )
        return

    await _deliver_panel_link(callback.bot, ctx, link)


@router.message(AdminStates.waiting_panel_manual_link)
async def panel_manual_link_received(message: types.Message, state: FSMContext):
    link = (message.text or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        await message.answer("❌ این یک لینک معتبر نیست؛ لطفاً لینک ساب رو با http یا https ارسال کن:")
        return
    data = await state.get_data()
    ctx = data.get("panel_pending_ctx")
    if not ctx:
        await message.answer("❌ مشکلی پیش آمد؛ لطفاً از ابتدا دکمه‌ی ارسال خودکار رو دوباره بزن:")
        await state.clear()
        return
    await _deliver_panel_link(message.bot, ctx, link)
    await state.clear()


async def _fulfill_custom_renew(bot, order: dict, order_id: int, volume, days) -> tuple[bool, str]:
    """برای سفارش‌های تمدید (order_type == "renew")، به‌جای ساخت یک سرویس جدید، همون
    سرویس موجود را واقعاً از داخل پنل تمدید می‌کند (همان لینک ساب قبلی حفظ می‌شود).
    خروجی: (True, متن موفقیت) یا (False, پیام خطا)."""
    target_config_id = order.get("target_config_id")
    cfg = db.get_config_by_id(target_config_id) if target_config_id else None
    if not cfg:
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release renew claim %s", order_id)
        return False, "❌ سرویس مقصد برای تمدید یافت نشد (ممکن است حذف شده باشد)."
    if not cfg.get("panel_id") or not cfg.get("service_id"):
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release renew claim %s", order_id)
        return False, "❌ این سرویس از داخل پنل ساخته نشده و قابل تمدید خودکار نیست."
    panel = db.get_vpn_panel(cfg["panel_id"])
    if not panel or not panel.get("enabled"):
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release renew claim %s", order_id)
        return False, "❌ پنل مربوطه به این سرویس یافت نشد یا غیرفعال است."

    if panel.get("panel_type") == "shahrah":
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release renew claim %s", order_id)
        return False, "❌ شاهراه برای تمدید سفارشیِ حجم/زمان API مستقیمی مثل مرزبان/پاسارگارد ندارد؛ برای این عملیات باید بسته‌ی مشخص شاهراه انتخاب شود."
    ok, snap, msg = await panels.renew_existing_service(panel, cfg["service_id"], volume_gb=volume or 0, days=days or 0)
    if not ok:
        try: db.set_custom_order_status(order_id, "paid")
        except Exception: logger.exception("failed to release renew claim %s", order_id)
        return False, f"❌ تمدید از داخل پنل ناموفق بود: {msg}"

    # snapshot همین حالا از خود پنل گرفته شده است؛ لینک/شناسه‌ی سرویس عمداً
    # از cfg قبلی حفظ می‌شوند و هیچ Revoke/تعویض Subscription انجام نمی‌شود.
    snap = snap if isinstance(snap, dict) else {}
    actual_name = alerts.get_config_service_username(cfg, snap)
    actual_expire = snap.get("expire")
    if actual_expire:
        try:
            expiry_date = datetime.fromtimestamp(actual_expire, tz=TEHRAN_TZ).replace(tzinfo=None).strftime("%Y-%m-%d")
        except Exception:
            expiry_date = cfg.get("expiry")
    elif "expire" in snap:
        expiry_date = "نامحدود"
    else:
        expiry_date = cfg.get("expiry")
    total_bytes = snap.get("total")
    volume_gb_actual = round(total_bytes / (1024**3), 2) if total_bytes else volume
    remaining_days_actual = days_remaining(actual_expire) if actual_expire else days
    volume_text, days_text = format_service_package(volume_gb_actual, remaining_days_actual, None)
    db.update_config_expiry(target_config_id, expiry_date)
    # نام/لینک/شناسه‌ی موجود سرویس بازنویسی نمی‌شوند.

    # منشأ مالی تمدید را از خود سفارش بخوان؛ اگر خرید کاملاً از referral_wallet
    # بوده، همین سرویس هدف دوباره رفرالی است، وگرنه فلگ قبلی پاک می‌شود.
    db.set_config_referral_funded(target_config_id, db.custom_order_is_referral_funded(order_id))

    user = db.get_user_by_id(cfg["user_id"])
    if user:
        added_volume = float(volume or 0)
        added_days = int(days or 0)
        if volume_gb_actual and added_volume:
            previous_volume = f"{max(0, float(volume_gb_actual) - added_volume):g} گیگ"
            new_volume = f"{float(volume_gb_actual):g} گیگ"
        elif volume_gb_actual:
            previous_volume = new_volume = f"{float(volume_gb_actual):g} گیگ"
        else:
            previous_volume = new_volume = "نامحدود"
        if remaining_days_actual is not None and added_days:
            previous_days = f"{max(0, int(remaining_days_actual) - added_days)} روز"
            new_days = f"{int(remaining_days_actual)} روز"
        elif remaining_days_actual is not None:
            previous_days = new_days = f"{int(remaining_days_actual)} روز"
        else:
            previous_days = new_days = "نامحدود"
        text = t(
            "renew_done",
            service_name=actual_name,
            added_volume=f"+{added_volume:g} گیگ" if added_volume else "بدون تغییر",
            previous_volume=previous_volume,
            new_volume=new_volume,
            added_days=f"+{added_days} روز" if added_days else "بدون تغییر",
            previous_days=previous_days,
            new_days=new_days,
            volume=f"{added_volume:g} گیگ" if added_volume else "بدون تغییر",
            days=f"{added_days} روز" if added_days else "بدون تغییر",
        )
        try:
            await bot.send_message(int(user["telegram_id"]), text, entities=getattr(text, "entities", None))
        except Exception:
            logger.exception("ارسال پیام تمدید واقعی به کاربر ناموفق بود")

    db.set_custom_order_status(order_id, "fulfilled")

    # تمدیدهایی که از «سرویس‌های من» شروع می‌شوند هم باید مثل خرید جدید،
    # بعد از موفقیت واقعی پنل در کانال لاگ ثبت شوند. قبلاً این مسیر فقط
    # وضعیت سفارش را fulfilled می‌کرد و _log_fulfilled_order را صدا نمی‌زد؛
    # بنابراین تمدید انجام می‌شد ولی هیچ رکوردی در کانال لاگ نمی‌آمد.
    try:
        await _log_fulfilled_order(
            bot,
            user or {"telegram_id": str(order.get("user_id") or ""), "id": order.get("user_id")},
            custom_order_id=order_id,
            target_config_id=target_config_id,
            service_id=cfg.get("service_id"),
            service_name=actual_name,
            package_text=alerts.get_config_package_name(cfg) or f"{volume_text} | {days_text}",
            expiry_text=expiry_date or "نامحدود",
        )
    except Exception:
        # خراب شدن کانال لاگ نباید باعث شود کاربر تصور کند تمدید پنل ناموفق بوده.
        logger.exception("ثبت لاگ کانال برای تمدید سفارش %s ناموفق بود", order_id)
    try:
        await send_rich(bot, ADMIN_ID, alerts.admin_delivery_summary(user, actual_name, alerts.get_config_package_name(cfg), order.get("price", 0)))
    except Exception:
        logger.exception("ارسال خلاصه تمدید برای ادمین ناموفق بود")

    return True, "سرویس همان سرویس قبلی به‌طور واقعی از داخل پنل تمدید شد (بدون ساخت سرویس جدید)."


async def _deliver_panel_link(bot, ctx: dict, link: str):
    """سرویس ساخته‌شده از هر یک از سه نوع پنل را در دیتابیس ذخیره و برای کاربر ارسال می‌کند
    (دقیقاً همان قالب/تجربه‌ی ارسال دستی، فقط بدون نیاز به آپلود دستی عکس/لینک)."""
    uid = ctx["uid"]
    plan_key = ctx.get("plan_key")
    order_id = ctx.get("order_id")
    order_kind = ctx.get("order_kind")
    panel_id = ctx.get("panel_id")
    panel_type = ctx.get("panel_type")
    service_id = ctx.get("service_id")
    snap = ctx.get("snapshot") or {}

    user = db.get_user(uid)
    if user is None:
        await bot.send_message(ADMIN_ID, "❌ کاربر یافت نشد؛ سرویس در پنل ساخته شد ولی ارسال نشد.")
        return

    # نام/حجم/انقضا باید از خود پنل خوانده شود؛ مخصوصاً برای شاهراه نام سرویس ممکن است
    # با نام پلن فروشگاه متفاوت باشد.
    if panel_id and service_id:
        panel_obj = db.get_vpn_panel(panel_id)
        if panel_obj:
            ok_live, live, _ = await panels.get_service_snapshot(panel_obj, service_id)
            if ok_live and isinstance(live, dict):
                snap = {**snap, **live}
    name = snap.get("username") or service_id or snap.get("name") or "کاربر"
    volume_gb = snap.get("volume_gb")
    if volume_gb is None and snap.get("total"):
        try: volume_gb = snap["total"] / (1024**3)
        except Exception: volume_gb = None
    days = snap.get("days")
    if days is None and snap.get("expire"):
        try: days = max(0, days_remaining(snap["expire"]))
        except Exception: days = None
    volume_text, days_text = format_service_package(volume_gb, days, plan_key)
    expiry_date = None
    if snap.get("expire"):
        try: expiry_date = datetime.fromtimestamp(int(snap["expire"]), tz=TEHRAN_TZ).replace(tzinfo=None).strftime("%Y-%m-%d")
        except Exception: expiry_date = None
    if not expiry_date and days: expiry_date = (now_tehran_naive() + timedelta(days=days)).strftime("%Y-%m-%d")

    # 🐛 فیکس: این مسیر (پنل یکپارچه‌ی مرزبان/پاسارگارد/۳ایکس‌یوآی) قبلاً کپشن
    # تحویل را با یک متن ثابت در کد می‌ساخت و اصلاً از کاتالوگ متن قابل‌ویرایش
    # (service_delivery_text / service_delivery_test_text) استفاده نمی‌کرد؛
    # به همین دلیل هر ویرایشی که ادمین از بخش «✏️ ویرایش متن → 📦 تحویل سرویس»
    # ذخیره می‌کرد، روی این مسیر هیچ اثری نداشت و پیام همیشه با فرمت پیش‌فرض
    # قدیمی ارسال می‌شد. حالا دقیقاً مثل بقیه‌ی مسیرهای تحویل، از همان قالب
    # قابل‌ویرایش استفاده می‌کند.
    is_test_delivery = plan_key == FREE_TEST_PLAN_KEY
    delivery_label = "تست رایگان" if is_test_delivery else f"{volume_text} | {days_text} | نامحدود کاربر"
    delivery_text_key = "service_delivery_test_text" if is_test_delivery else "service_delivery_text"
    caption = t(delivery_text_key, service_label=delivery_label, link=link)

    encrypted = crypto.encrypt_config(link)
    plan_name = f"{name} | {volume_text} | {days_text}"
    config_type = db.plan_type(plan_key) if plan_key else "vip"

    config_id = db.add_config(
        user["id"], plan_name, encrypted, expiry=expiry_date,
        config_type=config_type, service_id=service_id, source=panel_type, panel_id=panel_id,
        category_id=((db.get_vip_plan(plan_key) or {}).get("category_id") if plan_key else None),
        plan_key=plan_key,
    )

    # 🛠 فیکس ریشه‌ای: اگر سفارش با کیف‌پول کاملاً از referral_wallet تأمین شده،
    # حتی در تحویل دستی/لینک دستی هم سرویس باید referral_funded=1 شود.
    # قبلاً این فلگ فقط در مسیر auto_fulfill ست می‌شد و در مسیر دستی جا می‌افتاد.
    if order_kind == "plan" and order_id and db.order_is_referral_funded(int(order_id)):
        db.set_config_referral_funded(config_id, True)
    elif order_kind == "custom" and order_id and db.custom_order_is_referral_funded(int(order_id)):
        db.set_config_referral_funded(config_id, True)

    sent_photo_file_id = None
    try:
        if qrcode:
            photo = types.BufferedInputFile(_make_qr_bytes(link), filename="qr.png")
            sent = await send_photo_rich(
                bot, int(uid), photo, caption=caption,
                reply_markup=config_delivery_keyboard(bot_info.get("connection_guide_url"), is_test=is_test_delivery),
            )
            if sent and sent.photo:
                sent_photo_file_id = sent.photo[-1].file_id
        else:
            await send_rich(
                bot, int(uid), caption,
                reply_markup=config_delivery_keyboard(bot_info.get("connection_guide_url"), is_test=is_test_delivery),
            )
        order_obj = db.get_order(order_id) if order_id else None
        if plan_key and db.get_effective_plan(plan_key):
            admin_package_name = db.get_effective_plan(plan_key).get("name")
        else:
            admin_package_name = f"{volume_text} | {days_text}"
        admin_amount = order_obj.get("price", 0) if order_obj else 0
        admin_summary = alerts.admin_delivery_summary(user, name, admin_package_name, admin_amount)
        await send_rich(bot, ADMIN_ID, admin_summary)
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"⚠️ سرویس ساخته و ذخیره شد ولی ارسال پیام به کاربر ناموفق بود: {e}")

    if sent_photo_file_id:
        db.set_config_qr(config_id, sent_photo_file_id)

    if order_kind == "plan" and order_id:
        db.set_order_status(order_id, "fulfilled")
    elif order_kind == "custom" and order_id:
        db.set_custom_order_status(order_id, "fulfilled")

    await _log_fulfilled_order(
        bot, user,
        plan_order_id=order_id if order_kind == "plan" else None,
        custom_order_id=order_id if order_kind == "custom" else None,
        # 🐛 فیکس: قبلاً target_config_id پاس داده نمی‌شد، پس منطق «گرفتن نام
        # واقعی از خودِ پنل» در _log_fulfilled_order هیچ‌وقت اجرا نمی‌شد و
        # لاگ همیشه اسم پلن (name) را نشان می‌داد، نه یوزرنیم واقعی‌ای که
        # پنل ساخته (مثل businesssvpnbot_39xpsj6h596).
        target_config_id=config_id, service_id=service_id, service_name=name,
        package_text=alerts.get_config_package_name(db.get_config_by_id(config_id) or {}) or f"{volume_text} | {days_text}", expiry_text=expiry_date or "نامحدود",
    )


# ---------------------------------------------------------------------------
# 🔁 مدیریت سرویس‌های ساخته‌شده از هر یک از سه نوع پنل (از صفحه‌ی جزئیات سرویس)
# نمونه‌ی پنل هر سرویس از روی configs.panel_id دقیقاً همونی که سرویس روی اون ساخته شده پیدا می‌شود
# (حتی اگر بعداً نمونه‌های تکراری دیگه‌ای از همون نوع اضافه شوند).
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("panelrevoke_"))
async def panel_revoke_sub(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    cfg_id=int(callback.data.replace("panelrevoke_","")); cfg=db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await callback.answer("❌ سرویس پنلی پیدا نشد.",show_alert=True); return
    panel=db.get_vpn_panel(cfg["panel_id"])
    if not panel: await callback.answer("❌ پنل پیدا نشد.",show_alert=True); return
    if panel.get("panel_type")=="shahrah":
        await callback.answer("ℹ️ شاهراه تغییر لینک مستقل بدون تعویض بسته را از این مسیر ارائه نمی‌کند.",show_alert=True); return
    await callback.answer("⏳ در حال باطل‌کردن لینک قبلی و ساخت لینک جدید از خود پنل...")
    ok, link, new_id, data, msg = await panels.regenerate_sub_link(panel, cfg["service_id"])
    if not ok or not link:
        await callback.message.answer(
            f"❌ تغییر لینک انجام نشد. لینک قبلی همچنان در دیتابیس حفظ شده است.\n\n{msg}"
        )
        return

    # فقط وقتی پنل واقعاً لینک جدید داده، لینک ذخیره‌شده را جایگزین می‌کنیم.
    db.update_config(
        cfg_id, cfg["plan"], crypto.encrypt_config(link),
        expiry=cfg.get("expiry"),
        service_id=new_id or cfg["service_id"],
        panel_id=cfg["panel_id"],
    )
    db.set_config_link_disabled(cfg_id, False)
    await callback.message.answer(
        "✅ لینک سرویس با موفقیت تغییر کرد.\n\n"
        "🔒 لینک قبلی توسط خود پنل Revoke و غیرفعال شد.\n"
        "🔗 لینک جدید صادر و در سرویس ذخیره شد.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 مشاهده سرویس", callback_data=f"svcdetail_{cfg_id}")]]
        ),
    )

@router.callback_query(F.data.startswith("panelrenew_"))
async def panel_renew_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    cfg_id = int(callback.data.replace("panelrenew_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از هیچ پنلی ساخته نشده.", show_alert=True)
        return
    panel = db.get_vpn_panel(cfg["panel_id"])
    if not panel:
        await callback.answer("❌ نمونه پنل مربوط به این سرویس دیگر وجود ندارد.", show_alert=True)
        return

    # تمدید از «مدیریت سرویس‌های کاربر» باید دقیقاً همان منطق تمدید مشتری را
    # داشته باشد؛ دیگر از تمپلیت/کاتالوگ پنل برای بازنشانی سرویس استفاده نمی‌کنیم.
    # helper مشترک marzban_admin همان تنظیمات دسته‌بندی فروشگاه را می‌خواند و
    # برای هر سه نوع پنل قابل استفاده است.
    from handlers.marzban_admin import _admin_renewal_settings_for_config
    settings = _admin_renewal_settings_for_config(cfg)
    mode = settings.get("mode", "day")
    await state.update_data(
        marzban_renew_cfg_id=cfg_id,
        marzban_renew_mode=mode,
        marzban_renew_settings=settings,
    )
    await callback.answer(f"⏳ تمدید {panels.panel_label(panel)} بدون Revoke و بدون تغییر لینک...")

    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"marzbanrenewback_{cfg_id}", style="danger")]
    ])
    if mode == "day":
        await state.update_data(marzban_renew_volume_gb=0)
        await state.set_state(AdminStates.waiting_marzban_renew_days)
        prompt = "تعداد روز اضافه را وارد کن:"
    elif mode == "gb":
        await state.set_state(AdminStates.waiting_marzban_renew_volume)
        prompt = "حجم اضافه را به گیگابایت وارد کن:"
    else:
        # برای «روز + گیگ» همان ترتیب بخش مشتری: ابتدا روز، سپس حجم.
        await state.set_state(AdminStates.waiting_marzban_renew_days)
        prompt = "ابتدا تعداد روز اضافه را وارد کن:"
    await answer_rich(callback.message, prompt, reply_markup=back_kb)


@router.callback_query(F.data.startswith("paneldisable_"))
async def panel_disable(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    cfg_id = int(callback.data.replace("paneldisable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از هیچ پنلی ساخته نشده.", show_alert=True)
        return
    panel = db.get_vpn_panel(cfg["panel_id"])
    if not panel:
        await callback.answer("❌ نمونه پنل مربوط پیدا نشد.", show_alert=True)
        return
    ok, msg = await panels.disable_service(panel, cfg["service_id"])
    if ok:
        db.set_config_link_disabled(cfg_id, True)
    await callback.answer(f"✅ در {panels.panel_label(panel)} غیرفعال شد." if ok else f"❌ {msg}", show_alert=True)


@router.callback_query(F.data.startswith("panelenable_"))
async def panel_enable(callback: types.CallbackQuery):
    if not _is_admin(callback.from_user.id):
        return
    cfg_id = int(callback.data.replace("panelenable_", ""))
    cfg = db.get_config_by_id(cfg_id)
    if not cfg or not cfg.get("panel_id") or not cfg.get("service_id"):
        await callback.answer("❌ این سرویس از هیچ پنلی ساخته نشده.", show_alert=True)
        return
    panel = db.get_vpn_panel(cfg["panel_id"])
    if not panel:
        await callback.answer("❌ نمونه پنل مربوط پیدا نشد.", show_alert=True)
        return
    ok, msg = await panels.enable_service(panel, cfg["service_id"])
    if ok:
        db.set_config_link_disabled(cfg_id, False)
    await callback.answer(f"✅ در {panels.panel_label(panel)} فعال شد." if ok else f"❌ {msg}", show_alert=True)


# ---------------------------------------------------------------------------
# 🛠 ایجاد سرویس دستی توسط ادمین (برای خودش)
# مشابه «بساز سرویس خودت» کاربر عادی، ولی:
#   • هیچ قیمتی محاسبه/کم نمی‌شود (رایگان)
#   • هیچ محدودیت حداقل/حداکثر حجم یا روز اعمال نمی‌شود
#   • ادمین آزاد است از هر پنل فعال (نه فقط پنل مپ‌شده‌ی custom_build) و هر
#     تمپلیت/اینباند آن پنل انتخاب کند
#   • سرویس مستقیم به خود ادمین تحویل داده می‌شود (لینک/کانفیگ)
# ---------------------------------------------------------------------------


@router.message(F.text == "🛠 ایجاد سرویس (ادمین)")
async def admin_create_service_start_reply(message: types.Message, state: FSMContext):
    """ورودی Reply Keyboard برای همان wizard ایجاد سرویس دستی."""
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    panels_list = db.list_vpn_panels(enabled_only=True)
    if not panels_list:
        await message.answer("❌ هیچ پنل فعالی ثبت نشده. اول یک پنل اضافه کن.")
        return
    await message.answer(
        "🛠 ایجاد سرویس دستی (رایگان، مخصوص خودت)\n\nاز کدوم پنل بسازم؟ 👇",
        reply_markup=admin_create_service_panel_keyboard(panels_list),
    )

@router.callback_query(F.data == "admin_create_service")
async def admin_create_service_start(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    panels_list = db.list_vpn_panels(enabled_only=True)
    if not panels_list:
        await callback.answer("❌ هیچ پنل فعالی ثبت نشده. اول یک پنل اضافه کن.", show_alert=True)
        return
    await callback.message.edit_text(
        "🛠 ایجاد سرویس دستی (رایگان، مخصوص خودت)\n\nاز کدوم پنل بسازم؟ 👇",
        reply_markup=admin_create_service_panel_keyboard(panels_list),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adminsvcpanel_"))
async def admin_create_service_pick_panel(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    panel_id = int(callback.data.replace("adminsvcpanel_", ""))
    panel = db.get_vpn_panel(panel_id)
    if not panel:
        await callback.answer("❌ این نمونه پنل پیدا نشد.", show_alert=True)
        return
    await callback.answer("⏳ در حال دریافت لیست از پنل...")
    choices, msg = await panels.get_catalog(panel)
    if not choices:
        await callback.message.answer(f"❌ {msg}", reply_markup=vpn_panel_back_keyboard(panel_id))
        return
    await state.update_data(adminsvc_panel_id=panel_id, adminsvc_choices=choices)
    await callback.message.answer(
        f"از {panels.panel_label(panel)}، کدوم بسته/تمپلیت/اینباند؟ 👇",
        reply_markup=admin_create_service_catalog_keyboard(choices, panel_id),
    )


@router.callback_query(F.data.startswith("adminsvccatalog_"))
async def admin_create_service_pick_catalog(callback: types.CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        return
    _, panel_id_str, idx_str = callback.data.split("_")
    panel_id, idx = int(panel_id_str), int(idx_str)
    data = await state.get_data()
    choices = data.get("adminsvc_choices") or []
    if data.get("adminsvc_panel_id") != panel_id or not choices:
        await callback.answer("❌ این انتخاب منقضی شده؛ دوباره از منو وارد شو.", show_alert=True)
        return
    chosen = next((c for c in choices if c["idx"] == idx), None)
    if not chosen:
        await callback.answer("❌ گزینه نامعتبر است.", show_alert=True)
        return
    await state.update_data(adminsvc_remote_ref=chosen["ref"], adminsvc_choice_label=chosen["label"])
    await state.set_state(AdminStates.waiting_admin_service_volume)
    await callback.message.answer("📦 حجم سرویس رو به گیگابایت وارد کن (بدون محدودیت، هر عددی):")
    await callback.answer()


@router.message(AdminStates.waiting_admin_service_volume)
async def admin_create_service_volume(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    volume = parse_int_in_range(message.text, 1, 1_000_000)
    if volume is None:
        await message.answer("❌ فقط یک عدد صحیح مثبت بفرست:")
        return
    await state.update_data(adminsvc_volume=volume)
    await state.set_state(AdminStates.waiting_admin_service_days)
    await message.answer("⏳ مدت اعتبار رو به روز وارد کن (بدون محدودیت):")


@router.message(AdminStates.waiting_admin_service_days)
async def admin_create_service_days(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    days = parse_int_in_range(message.text, 1, 1_000_000)
    if days is None:
        await message.answer("❌ فقط یک عدد صحیح مثبت بفرست:")
        return
    await state.update_data(adminsvc_days=days)
    await state.set_state(AdminStates.waiting_admin_service_name)
    await message.answer("🔤 یک نام (لاتین، بدون فاصله) برای سرویس بفرست، یا فقط «-» بفرست تا خودکار ساخته بشه:")


@router.message(AdminStates.waiting_admin_service_name)
async def admin_create_service_name(message: types.Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    custom_name = None if raw == "-" else raw
    if custom_name and not _LATIN_NAME_RE.match(custom_name):
        await message.answer("❌ فقط حروف انگلیسی و عدد، بدون فاصله؛ یا «-» برای نام خودکار:")
        return

    data = await state.get_data()
    panel_id = data.get("adminsvc_panel_id")
    remote_ref = data.get("adminsvc_remote_ref")
    volume = data.get("adminsvc_volume")
    days = data.get("adminsvc_days")
    panel = db.get_vpn_panel(panel_id) if panel_id else None
    await state.clear()
    if not panel or remote_ref is None or volume is None or days is None:
        await message.answer("❌ این مسیر منقضی شده؛ دوباره از «🛠 ایجاد سرویس (ادمین)» شروع کن.")
        return

    admin_user = db.get_user(message.from_user.id)
    if admin_user is None:
        db.create_user(message.from_user.id, message.from_user.full_name)

    username = custom_name or _service_username(message.from_user.id)
    await message.answer("⏳ در حال ساخت سرویس روی پنل...")
    ok, link, remote_service_id, resp_data, msg = await panels.create_service(
        panel, username, remote_ref, volume_gb=volume, days=days,
    )
    if not ok:
        await message.answer(f"❌ ساخت سرویس ناموفق بود:\n{msg}")
        return

    snapshot = {"name": custom_name or "سرویس ادمین", "volume_gb": volume, "days": days}
    ctx = {
        "uid": message.from_user.id, "plan_key": None, "order_id": None, "order_kind": "admin_manual",
        "panel_id": panel["id"], "panel_type": panel["panel_type"], "service_id": remote_service_id,
        "snapshot": snapshot,
    }
    if not link:
        await message.answer(
            f"⚠️ سرویس در {panels.panel_label(panel)} ساخته شد ولی لینک ساب خودکار پیدا نشد.\n"
            f"پاسخ پنل:\n<pre>{_pretty(resp_data)}</pre>\nلطفاً لینک رو از بالا کپی و همینجا برام بفرست:",
            parse_mode="HTML",
        )
        await state.update_data(panel_pending_ctx=ctx)
        await state.set_state(AdminStates.waiting_panel_manual_link)
        return

    await _deliver_panel_link(message.bot, ctx, link)
