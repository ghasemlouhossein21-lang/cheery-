from utils import send_photo_rich, edit_caption_rich
from utils import send_rich
"""
handlers/plans.py
نمایش دسته‌بندی سرویس‌های VIP، اعمال کد تخفیف، خرید سرویس
(با دو روش پرداخت: کیف پول و کارت‌به‌کارت)، و نمایش سرویس‌های خریداری‌شده کاربر.

نکته مهم: بعد از یک خرید موفق (چه با کیف پول چه با کارت‌به‌کارت)، اگر حجم آن
بعد از خرید واقعی و پولی، db.complete_referral فراخوانی می‌شود
می‌شود تا اگر معرفی داشته، مبلغ قفل‌شده‌ی معرفش آزاد شود (تست رایگان و
پلن‌های زیر این حجم پاداش را آزاد نمی‌کنند).
"""

import html
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext

import database as db
from utils import answer_rich, edit_rich
from text_catalog import text as t, RichText
import crypto
import vpn_panel
import panels
import uniquepay
import payments
import alerts
from subscription import fetch_subscription_info, extract_configs, format_bytes, format_expire, usage_bar, days_remaining, is_config_expired, enrich_configs_with_subscription_names, get_live_service_status
from utils import send_admin_task_message, forward_admin_task_message, parse_int_in_range, is_duplicate_action, format_deadline_time, progress_bar, now_tehran_naive, show_menu_with_sticker, get_main_keyboard
from states import UserStates
from handlers.panel_admin import auto_fulfill_vip_via_panel
# سازگاری نام قدیمی؛ مسیر جدید بر اساس نگاشت همان پلن کار می‌کند.
auto_fulfill_vip_via_marzban = auto_fulfill_vip_via_panel

import bot_info
from keyboards import InlineKeyboardButton


def _render_card_invoice_text(key: str, default: str, values: dict, deadline_str: str) -> RichText:
    """Render the editable card invoice as RichText so stored Premium/Custom Emoji entities survive."""
    rich_values = dict(values)
    # The invoice is sent with real Telegram entities, not HTML parse mode.
    # Remove any legacy HTML wrappers that older versions injected into values.
    for field in ("card_number", "card_holder", "plan_name"):
        value = rich_values.get(field)
        if isinstance(value, str):
            rich_values[field] = value.replace("<code>", "").replace("</code>", "")

    body = t(key, default=default, **rich_values)
    if not isinstance(body, RichText):
        body = RichText(str(body), getattr(body, "entities", []))

    # Keep the card number copyable without HTML/Markdown.
    card_number = str(rich_values.get("card_number") or "")
    if card_number:
        idx = str(body).find(card_number)
        if idx >= 0:
            offset = len(str(body)[:idx].encode("utf-16-le")) // 2
            length = len(card_number.encode("utf-16-le")) // 2
            body = RichText(str(body), list(body.entities) + [{
                "type": "code", "offset": offset, "length": length,
            }])

    expiry = RichText(
        f"⏱ این شماره کارت و قیمت تا ساعت {deadline_str} (۳۰ دقیقه) معتبر است. "
        "لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."
    )
    return body + "\n\n" + expiry


PLAN_CARD_INVOICE_DEFAULT = (
    "🟩🟩⬜️ مرحله 2 از 3\n\n"
    "💳 پرداخت کارت به کارت\n\n"
    "🛒 {plan_name}\n"
    "💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n"
    "💳 شماره کارت:\n{card_number}\n\n"
    "👤 به نام: {card_holder}\n\n"
    "📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید."
)

RENEW_CARD_INVOICE_DEFAULT = (
    "🟩🟩⬜️ مرحله 2 از 3\n\n"
    "💳 پرداخت کارت به کارت\n\n"
    "🔁 تمدید سرویس: {service_name}\n"
    "📦 حجم اضافه: {volume}\n"
    "⏳ زمان اضافه: {days}\n"
    "💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n"
    "💳 شماره کارت:\n{card_number}\n\n"
    "👤 به نام: {card_holder}\n\n"
    "📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید."
)


from config import (
    ADMIN_ID,
    UNIQUEPAY_ENABLED,
    FREE_TEST_PLAN_KEY,
    ONLINE_PAYMENT_MIN_AMOUNT,
)
from keyboards import (
    main_reply_keyboard,
    plans_menu,
    vip_categories_keyboard,
    vip_category_plans_keyboard,
    purchase_payment_keyboard,
    insufficient_balance_keyboard,
    back_button,
    admin_purchase_notify_keyboard,
    admin_purchase_card_approval_keyboard,
    my_configs_menu,
    my_configs_list_keyboard,
    config_detail_keyboard,
    renew_services_keyboard, renew_volume_keyboard, renew_days_keyboard, renew_payment_keyboard, crypto_payment_keyboard,
    confirm_delete_config_keyboard,
    confirm_disable_service_keyboard,
    confirm_revoke_sub_keyboard,
    online_payment_keyboard,
)

logger = logging.getLogger(__name__)

ORDERS_CLOSED_TEXT = (
    "🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می‌باشد.\n\nروشن شدن دوباره‌ی آن اطلاع‌رسانی خواهد شد."
)

plan_type = db.plan_type  # نسخه‌ی DB-aware (دسته‌بندی‌های VIP را هم می‌شناسد)


def _renewal_settings(category_id, plan_key=None):
    """تنظیمات تمدید واقعی دسته را از کلیدهای canonical دیتابیس می‌خواند.

    این مسیر عمداً قبل از fallbackهای قدیمی bot_info بررسی می‌شود تا مقادیر
    پیش‌فرض عمومی نتوانند تنظیمات ذخیره‌شده‌ی یک دسته را override کنند.
    """
    try:
        cid = int(category_id or 0)
    except Exception:
        cid = 0
    defaults = {
        "mode": "day", "price_day": 0, "price_gb": 5500,
        "min_day": 1, "max_day": 0, "min_gb": 1, "max_gb": 0,
        "day_options": "30,60,90", "gb_options": "10,20,50",
    }
    if cid <= 0:
        try:
            legacy = bot_info.get_renewal_settings(category_id)
            if isinstance(legacy, dict):
                defaults.update(legacy)
        except Exception:
            pass
        return defaults
    keymap = {
        "mode": f"renewal_category_{cid}_mode",
        "price_day": f"renewal_category_{cid}_price_day",
        "price_gb": f"renewal_category_{cid}_price_gb",
        "min_day": f"renewal_category_{cid}_min_day",
        "max_day": f"renewal_category_{cid}_max_day",
        "min_gb": f"renewal_category_{cid}_min_gb",
        "max_gb": f"renewal_category_{cid}_max_gb",
        "day_options": f"renewal_category_{cid}_day_options",
        "gb_options": f"renewal_category_{cid}_gb_options",
    }
    for field, key in keymap.items():
        raw = db.get_setting(key)
        if raw not in (None, ""):
            try:
                if field in ("mode", "day_options", "gb_options"):
                    defaults[field] = str(raw)
                else:
                    defaults[field] = int(float(raw))
            except Exception:
                pass
    # مقادیر قدیمی bot_info فقط برای فیلدهایی استفاده شوند که هنوز canonical
    # نیستند؛ هر مقدار ذخیره‌شده‌ی دسته‌ای در DB اولویت قطعی دارد.
    try:
        legacy = bot_info.get_renewal_settings(cid)
        if isinstance(legacy, dict):
            for field in defaults:
                if db.get_setting(keymap[field]) in (None, "") and field in legacy:
                    defaults[field] = legacy[field]
    except Exception:
        pass
    if defaults["mode"] not in ("day", "gb", "both"):
        defaults["mode"] = "day"
    # تنظیم اختصاصی یک پلن، در صورت وجود، روی تنظیمات دسته اولویت دارد.
    if plan_key:
        try:
            defaults = bot_info.get_renewal_plan_settings(str(plan_key), cid)
        except Exception:
            pass
    return defaults


def _renewal_category_id(cfg):
    cid = cfg.get("category_id")
    if cid:
        try:
            return int(cid)
        except Exception:
            pass
    try:
        plan = db.get_vip_plan(cfg.get("plan"))
        if plan and plan.get("category_id"):
            return int(plan["category_id"])
    except Exception:
        pass
    return None


def _save_renewal_snapshot(cfg: dict, snapshot: dict | None) -> None:
    """بعد از تمدید فقط وضعیت واقعی پنل را در DB همگام می‌کند؛ لینک/شناسه سرویس دست‌نخورده می‌ماند."""
    if not cfg or not snapshot:
        return
    exp = snapshot.get("expire")
    if exp:
        try:
            from subscription import TEHRAN_TZ
            expiry = datetime.fromtimestamp(int(exp), tz=TEHRAN_TZ).replace(tzinfo=None).strftime("%Y-%m-%d")
        except Exception:
            expiry = cfg.get("expiry")
    elif "expire" in snapshot:
        expiry = "نامحدود"
    else:
        expiry = cfg.get("expiry")
    db.update_config_expiry(int(cfg["id"]), expiry)


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


router = Router(name="plans")


def _clean_subscription_url(url):
    """پاک‌سازی فقط نویسه‌های wrapper انتهایی که گاهی در لینک ساب ذخیره می‌شوند."""
    if not isinstance(url, str):
        return url
    cleaned = url.strip()
    # Telegram/Markdown backtick may arrive URL-encoded as %60 at the very end.
    while cleaned.endswith("%60") or cleaned.endswith("`"):
        cleaned = cleaned[:-3] if cleaned.endswith("%60") else cleaned[:-1]
    return cleaned.strip()




@router.callback_query(F.data == "noop")
async def noop(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "plans")
async def show_services(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "buy_plans", t("plans_intro"), reply_markup=plans_menu(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "plans_vip")
async def show_vip_plans(callback: types.CallbackQuery, state: FSMContext):
    # 🧪 تست: استیکر plan.webm درست بالای منوی دسته‌های VIP
    await show_menu_with_sticker(
        callback.bot, callback.message.chat.id, "plan_select",
        t("vip_intro"), reply_markup=vip_categories_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vipcat_"))
async def show_vip_category_plans(callback: types.CallbackQuery, state: FSMContext):
    category_key = callback.data.replace("vipcat_", "")
    cat = db.get_vip_category(category_key)
    if cat is None:
        await answer_rich(callback, t("category_not_found"), show_alert=True)
        return
    data = await state.get_data()
    discount = data.get("discount_percent", 0)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "vip_category_list", 
        t("vip_category_title", category_name=cat["name"]), reply_markup=vip_category_plans_keyboard(category_key, discount)
    )
    await callback.answer()






def _compute_final_price(plan_key: str, plan: dict, telegram_id, data: dict) -> tuple[int, str, str | None]:
    """قیمت نهایی یک پلن را با درنظرگرفتن کد تخفیف کاربر (اگر برای این پلن معتبر باشد)
    و تخفیف خودکار نمایندگی (فقط روی VIP) محاسبه می‌کند و بهترین (کمترین) قیمت را برمی‌گرداند.
    خروجی سوم، کد تخفیفی است که واقعاً «برنده» شده (باید مصرفش ثبت شود) یا None اگر
    تخفیف نمایندگی برنده شده باشد یا هیچ تخفیفی اعمال نشده باشد."""
    price = plan["price"]

    code_price = price
    code = data.get("discount_code")
    valid_code = False
    if code:
        discount = db.get_discount(code)
        if (
            discount
            and discount["uses"] > 0
            and not db.discount_is_expired(discount)
            and db.discount_applies_to_plan(discount, plan_key)
            and db.discount_allowed_for_user(discount, telegram_id)
        ):
            user = db.get_user(telegram_id)
            over_cap = (
                discount.get("max_uses_per_user")
                and user is not None
                and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]
            )
            under_min = discount.get("min_order_amount") and price < discount["min_order_amount"]
            if not over_cap and not under_min:
                code_price = db.compute_discount(discount, price)
                valid_code = True

    agent_price = price
    if plan_type(plan_key) == "vip":
        agent = db.get_agent(telegram_id)
        if agent:
            agent_price = int(round(price * (1 - agent["vip_discount_percent"] / 100)))

    final_price = min(code_price, agent_price)
    note = ""
    winning_code = None
    if final_price < price:
        if valid_code and code_price <= agent_price:
            note = " (کد تخفیف اعمال شد)"
            winning_code = code
        else:
            note = " (تخفیف نمایندگی اعمال شد)"
    return final_price, note, winning_code


# ---------------------------------------------------------------------------
# کد تخفیف عمومی (از طریق کیف پول وارد می‌شود و روی خرید بعدی اعمال می‌شود)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "use_discount")
async def use_discount(callback: types.CallbackQuery, state: FSMContext):
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "discount_code_entry", t("discount_prompt"), reply_markup=back_button("wallet", t("discount_cancel")))
    await state.set_state(UserStates.waiting_discount_code)
    await callback.answer()


@router.message(UserStates.waiting_discount_code)
async def check_discount(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    discount = db.get_discount(code)

    if discount is None or discount["uses"] <= 0 or db.discount_is_expired(discount):
        await answer_rich(message, 
            t("discount_invalid"),
            reply_markup=back_button("plans"),
        )
        await state.clear()
        return

    if not db.discount_allowed_for_user(discount, message.from_user.id):
        await answer_rich(message, 
            t("discount_forbidden"),
            reply_markup=back_button("plans"),
        )
        await state.clear()
        return

    if discount.get("max_uses_per_user"):
        user = db.get_user(message.from_user.id)
        if user and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]:
            await answer_rich(message, 
                t("discount_limit"),
                reply_markup=back_button("plans"),
            )
            await state.clear()
            return

    if discount.get("discount_type") == "amount":
        await answer_rich(message, 
            t("discount_fixed_note"),
            reply_markup=plans_menu(),
        )
        await state.clear()
        return

    await state.update_data(discount_code=code, discount_percent=discount["percent"])
    plans_note = "" if not db.discount_is_expired(discount) and not db.discount_plans(discount) else \
        " (فقط روی پلن‌های خاص قابل استفاده است)"
    await answer_rich(message, 
        f"✅ کد تخفیف {discount['percent']}٪ با موفقیت ثبت شد و در خرید بعدی شما (در صورت تطابق پلن) اعمال می‌شود.{plans_note}",
        reply_markup=plans_menu(),
    )
    await state.set_state(None)


# ---------------------------------------------------------------------------
# کد تخفیف اختصاصیِ یک پلن (در مرحله‌ی پرداخت)
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("discount_plan_"))
async def discount_for_plan(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("discount_plan_", "")
    if db.get_effective_plan(plan_key) is None:
        await answer_rich(callback, t("plan_not_found"), show_alert=True)
        return
    await state.update_data(discount_target_plan=plan_key)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "discount_code_entry", t("discount_prompt"))
    await state.set_state(UserStates.waiting_discount_plan)
    await callback.answer()


@router.message(UserStates.waiting_discount_plan)
async def check_discount_for_plan(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    discount = db.get_discount(code)
    data = await state.get_data()
    plan_key = data.get("discount_target_plan")
    plan = db.get_effective_plan(plan_key)

    if plan is None:
        await answer_rich(message, t("plan_action_error"), reply_markup=back_button("plans"))
        await state.clear()
        return

    if discount is None or discount["uses"] <= 0 or db.discount_is_expired(discount):
        await answer_rich(message, 
            t("discount_invalid"),
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if not db.discount_applies_to_plan(discount, plan_key):
        await answer_rich(message, 
            "❌ این کد تخفیف روی این پلن قابل استفاده نیست.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if not db.discount_allowed_for_user(discount, message.from_user.id):
        await answer_rich(message, 
            t("discount_forbidden"),
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    if discount.get("max_uses_per_user"):
        user = db.get_user(message.from_user.id)
        if user and db.user_discount_uses(discount["id"], user["id"]) >= discount["max_uses_per_user"]:
            await answer_rich(message, 
                t("discount_limit"),
                reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
            )
            await state.set_state(None)
            return

    if discount.get("min_order_amount") and plan["price"] < discount["min_order_amount"]:
        await answer_rich(message, 
            f"❌ این کد فقط برای خریدهای بالای {discount['min_order_amount']:,} تومان قابل استفاده است.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=True),
        )
        await state.set_state(None)
        return

    await state.update_data(discount_code=code, discount_percent=discount["percent"])
    final_price = db.compute_discount(discount, plan["price"])
    value_text = f"{discount['percent']}٪" if discount.get("discount_type") != "amount" else f"{discount['amount']:,} تومانی"
    text = (
        f"✅ کد تخفیف {value_text} اعمال شد!\n\n"
        f"🛒 {plan['name']}\n💰 قیمت نهایی: {final_price:,} تومان\n\n"
        f"روش پرداخت را انتخاب کنید:"
    )
    await answer_rich(message, text, reply_markup=purchase_payment_keyboard(plan_key, show_discount=False))
    await state.set_state(None)


# ---------------------------------------------------------------------------
# انتخاب پلن → نمایش روش‌های پرداخت
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("buy_"))
async def buy_plan(callback: types.CallbackQuery, state: FSMContext):
    if not db.is_orders_enabled():
        await callback.answer(ORDERS_CLOSED_TEXT, show_alert=True)
        return

    plan_key = callback.data.replace("buy_", "")
    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await answer_rich(callback, t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, note, _winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    text = progress_bar(1, 3) + t("plan_payment_page", plan_name=plan["name"], price=final_price, note=note, wallet=user["wallet"])

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_payment_method", text, reply_markup=purchase_payment_keyboard(plan_key, show_discount=not note))
    await callback.answer()


# ---------------------------------------------------------------------------
# پرداخت از کیف پول
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pay_wallet_"))
async def pay_with_wallet(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("pay_wallet_", "")
    if is_duplicate_action(f"walletbuy_{callback.from_user.id}_{plan_key}"):
        await answer_rich(callback, t("processing_request"), show_alert=True)
        return

    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await answer_rich(callback, t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    if user["wallet"] < final_price:
        needed = final_price - user["wallet"]
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
            t("wallet_insufficient", price=final_price, wallet=user["wallet"], needed=needed),
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    success = db.deduct_from_wallet(user["id"], final_price, f"خرید {plan['name']}")
    if not success:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
            t("wallet_not_enough"),
            reply_markup=insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    if winning_code:
        db.use_discount(winning_code, user["id"])

    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), final_price)

    # فقط بعد از ثبت موفق خرید پولی، پاداش دعوت آزاد می‌شود.
    # ساخت سفارش pending به‌تنهایی برای مسیرهای دیگر پاداش را آزاد نمی‌کند.
    if final_price > 0 and plan_key != FREE_TEST_PLAN_KEY:
        try:
            db.complete_referral(user["id"], qualifying_order_id=order_id)
        except ValueError:
            pass

    # 🐛 فیکس: پیام «پرداخت شما ثبت شد» را زودتر از ارسال خودکار سرویس می‌فرستیم تا کاربر قبل از دریافت سرویس، این پیام را ببیند.
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_wallet", 
        t("wallet_purchase_success"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
    )
    await state.update_data(discount_percent=0, discount_code="")

    # VIP و «تست رایگان»: اگر مرزبان فعال و برای این پلن (یا برای تست رایگان،
    # نگاشت سراسری‌اش) بسته‌ای نگاشت شده باشد، همین‌جا و بدون نیاز به هیچ
    # انتخابی از ادمین، سرویس ساخته و مستقیم ارسال می‌شود. گیمینگ هرگز وارد
    # این مسیر نمی‌شود (auto_fulfill_vip_via_marzban فقط روی
    # get_marzban_plan_map_for_plan_key کار می‌کند که خودش گیمینگ را نادیده
    # می‌گیرد)، و اگر نگاشتی نباشد هم دقیقاً رفتار قبلی حفظ می‌شود.
    handled = False
    if plan_type(plan_key) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(callback.bot, str(callback.from_user.id), plan_key, order_id)

    if handled:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (کیف پول) — به‌صورت خودکار از پنل مرزبان ساخته و ارسال شد ✅\n\n"
            f"👤 {callback.from_user.full_name}\n"
            f"🆔 {callback.from_user.id}\n"
            f"📦 {plan['name']}\n"
            f"💰 {final_price:,} تومان",
        )
    else:
        await send_admin_task_message(
            callback.bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (کیف پول)!\n\n"
            f"👤 {callback.from_user.full_name}\n"
            f"🆔 {callback.from_user.id}\n"
            f"📦 {plan['name']}\n"
            f"💰 {final_price:,} تومان",
            reply_markup=admin_purchase_notify_keyboard(str(callback.from_user.id), plan_key, order_id),
        )
    await callback.answer()


# ---------------------------------------------------------------------------
# 🎁 تست رایگان با قیمت صفر (رایگان) — بدون هیچ مرحله‌ای انتخاب روش پرداخت
# ---------------------------------------------------------------------------
async def fulfill_free_test_directly(bot, message: types.Message, user: dict, plan: dict, plan_key: str) -> None:
    """وقتی قیمت پلن «تست رایگان» (از پنل ادمین) صفر باشد، هیچ صفحه‌ی
    انتخاب روش پرداخت (کیف پول/کارت‌به‌کارت/آنلاین) نشان داده نمی‌شود و
    همینجا (دقیقاً معادل مسیر موفق پرداخت کیف پول ولی بدون هیچ کسری از موجودی) سرویس
    ساخته و ارسال می‌شود."""
    if is_duplicate_action(f"freetestdirect_{message.from_user.id}"):
        await answer_rich(message, "\u26a0\ufe0f \u0627\u06cc\u0646 \u062f\u0631\u062e\u0648\u0627\u0633\u062a \u062f\u0631 \u062d\u0627\u0644 \u067e\u0631\u062f\u0627\u0632\u0634/\u062b\u0628\u062a\u200c\u0634\u062f\u0647 \u0627\u0633\u062a.")
        return

    if db.has_used_free_test(user["id"]):
        await answer_rich(message, 
            t("free_test_used"),
        )
        return


    order_id = db.create_order(user["id"], plan_key, plan["name"], plan_type(plan_key), 0)

    # 🐛 فیکس: قبلاً این پیام «ثبت درخواست» بعد از ارسال خودکار سرویس (auto_fulfill_vip_via_marzban)
    # فرستاده می‌شد و کاربر بعد از دریافت سرویس می‌دیدش که «درخواستت ثبت شد» که گیج‌کننده بود. الان این پیام زودتر از ارسال سرویس فرستاده می‌شود.
    await show_menu_with_sticker(
        bot, message.chat.id, "free_test",
        t("free_test_registered"),
    )

    handled = False
    if plan_type(plan_key) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(bot, str(message.from_user.id), plan_key, order_id)

    if handled:
        return
    else:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"\U0001F381 \u062a\u0633\u062a \u0631\u0627\u06cc\u06af\u0627\u0646 \u062c\u062f\u06cc\u062f (\u0642\u06cc\u0645\u062a: \u0631\u0627\u06cc\u06af\u0627\u0646)!\n\n"
            f"\U0001F464 {message.from_user.full_name}\n"
            f"\U0001F194 {message.from_user.id}\n"
            f"\U0001F4E6 {plan['name']}",
            reply_markup=admin_purchase_notify_keyboard(str(message.from_user.id), plan_key, order_id),
        )


# ---------------------------------------------------------------------------
# پرداخت آنلاین (درگاه یونیک‌پی — کارت‌به‌کارت با تایید خودکار)
# ---------------------------------------------------------------------------
async def finalize_online_payment(bot, payment: dict) -> int | None:
    """اینوویس پرداخت‌شده‌ی یونیک‌پی را به یک سفارش واقعی تبدیل می‌کند و به
    ادمین اطلاع می‌دهد تا کانفیگ را ارسال کند.

    🐛 فیکس ریس‌کاندیشن: قبلاً idempotency فقط با یک if ساده روی payment
    ورودی چک می‌شد که چون هم پولر پس‌زمینه‌ی ربات و هم دکمه‌ی «بررسی پرداخت»
    (و در دیپلوی مینی‌اپ، endpoint جدای سرویس جداگانه) می‌توانند هم‌زمان این
    را صدا بزنند، امکان ساخت سفارش/سرویس تکراری برای یک پرداخت وجود داشت.
    حالا با db.claim_online_payment_for_finalize یک قفل اتمیک روی ردیف
    گرفته می‌شود؛ اگر فراخوانی دیگری برنده شده باشد، اینجا فقط None برمی‌گردد
    (کاری تکراری انجام نمی‌شود). اگر وسط کار خطا بیفتد، وضعیت به pending
    برمی‌گردد تا پولر بعدی دوباره تلاش کند."""
    if payment["status"] == "paid" and payment.get("order_id"):
        return payment["order_id"]

    if not db.claim_online_payment_for_finalize(payment["id"]):
        # یعنی یک فراخوانی هم‌زمان دیگر (یا پولر، یا دکمه‌ی کاربر، یا مینی‌اپ)
        # همین الان دارد/داشت همین پرداخت را پردازش می‌کند؛ برای جلوگیری از
        # سفارش تکراری اینجا هیچ کاری نمی‌کنیم.
        fresh = db.get_online_payment(payment["id"])
        if fresh and fresh["status"] == "paid" and fresh.get("order_id"):
            return fresh["order_id"]
        return None

    try:
        order_id = db.create_order(
            payment["user_id"], payment["plan_key"], payment["plan_name"],
            payment["order_type"], payment["price"],
        )
        db.mark_online_payment_paid(payment["id"], order_id)

        if payment.get("discount_code"):
            try:
                db.use_discount(payment["discount_code"], payment["user_id"])
            except Exception:
                logger.exception("خطا در مصرف کد تخفیف پس از پرداخت آنلاین")

        plan = db.get_effective_plan(payment["plan_key"]) if payment["plan_key"] else None
        if payment["price"] > 0 and payment.get("plan_key") != FREE_TEST_PLAN_KEY:
            try:
                db.complete_referral(payment["user_id"])
            except ValueError:
                pass
    except Exception:
        # اگر وسط ساخت سفارش خطا بیفتد، claim را آزاد می‌کنیم تا دفعه‌ی بعد
        # (پولر یا کلیک مجدد کاربر) بتواند دوباره تلاش کند، نه اینکه پرداخت
        # برای همیشه در حالت processing گیر کند.
        db.set_online_payment_status(payment["id"], "pending")
        raise

    handled = False
    if payment["plan_key"] and plan_type(payment["plan_key"]) in ("vip", "test"):
        handled = await auto_fulfill_vip_via_marzban(bot, payment["telegram_id"], payment["plan_key"], order_id)

    if handled:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (پرداخت آنلاین - یونیک‌پی) — به‌صورت خودکار از پنل مرزبان ساخته و ارسال شد ✅\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 {payment['plan_name']}\n"
            f"💰 {payment['price']:,} تومان",
        )
    else:
        await send_admin_task_message(
            bot, ADMIN_ID, "requests",
            f"🛒 خرید جدید (پرداخت آنلاین - یونیک‌پی)!\n\n"
            f"🆔 {payment['telegram_id']}\n"
            f"📦 {payment['plan_name']}\n"
            f"💰 {payment['price']:,} تومان",
            reply_markup=admin_purchase_notify_keyboard(payment["telegram_id"], payment["plan_key"], order_id),
        )
    return order_id




@router.callback_query(F.data.startswith("pay_online_"))
async def pay_with_online(callback: types.CallbackQuery, state: FSMContext):
    if not UNIQUEPAY_ENABLED:
        await answer_rich(callback, t("payment_not_active"), show_alert=True)
        return

    plan_key = callback.data.replace("pay_online_", "")
    if is_duplicate_action(f"onlinebuy_{callback.from_user.id}_{plan_key}"):
        await answer_rich(callback, t("processing_request"), show_alert=True)
        return

    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await answer_rich(callback, t("plan_not_found"), show_alert=True)
        return

    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY and db.has_used_free_test(user["id"]):
        await callback.answer(
            t("free_test_used"),
            show_alert=True,
        )
        return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    await answer_rich(callback, t("building_payment"))

    hash_id = uniquepay.new_hash_id("plan")
    invoice = await payments.create_invoice(hash_id, final_price)
    if invoice is None:
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
            "❌ برای مبالغ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت‌به‌کارت یا کیف پول استفاده کنید.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=False),
        )
        return

    payment_link = invoice.get("paymentLink")
    if not payment_link:
        await alerts.report_uniquepay_create_failure(callback.bot, ADMIN_ID)
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
            "❌ برای مبالِ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت‌به‌کارت یا کیف پول استفاده کنید.",
            reply_markup=purchase_payment_keyboard(plan_key, show_discount=False),
        )
        return

    alerts.report_uniquepay_create_success()

    payment_id = db.create_online_payment(
        user_id=user["id"],
        telegram_id=str(callback.from_user.id),
        hash_id=hash_id,
        plan_name=plan["name"],
        price=final_price,
        order_type=plan_type(plan_key),
        plan_key=plan_key,
        discount_code=winning_code,
        payment_link=payment_link,
        ref_id=str(invoice.get("refId")),
        provider=invoice.get("provider", "uniquepay"),
    )

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", 
        progress_bar(2, 3) + t("online_plan_invoice", plan_name=plan["name"], amount=final_price),
        reply_markup=online_payment_keyboard(payment_link, payment_id),
    )


@router.callback_query(F.data.startswith("checkpay_"))
async def check_online_payment(callback: types.CallbackQuery):
    try:
        payment_id = int(callback.data.replace("checkpay_", ""))
    except ValueError:
        await answer_rich(callback, t("invalid_request"), show_alert=True)
        return

    payment = db.get_online_payment(payment_id)
    if payment is None:
        await callback.answer(
            t("invoice_expired_wait"),
            show_alert=True,
        )
        return

    if str(callback.from_user.id) != payment["telegram_id"]:
        await answer_rich(callback, t("payment_owned"), show_alert=True)
        return

    if payment["status"] == "paid":
        await answer_rich(callback, t("payment_already_confirmed"), show_alert=True)
        return

    await answer_rich(callback, t("checking_payment"))

    invoice = await payments.check_invoice(payment)
    if not invoice or not invoice.get("isPaid"):
        await callback.answer(
            "⏳ هنوز پرداختی برای این اینوویس ثبت نشده. اگر همین الان پرداخت کردید،"
            " چند لحظه صبر کنید و دوباره بزنید.",
            show_alert=True,
        )
        return

    payment_kind = payment.get("kind")

    # تمدید آنلاین باید قبل از اعلام موفقیت واقعاً روی پنل اعمال و تأیید شود.
    # خریدهای معمولی همچنان همان جریان قبلی خودشان را دارند.
    if payment_kind == "renew":
        if not db.claim_online_payment_for_finalize(payment["id"]):
            fresh = db.get_online_payment(payment["id"])
            if fresh and fresh.get("status") == "paid":
                await callback.answer("✅ این پرداخت قبلاً پردازش شده است.", show_alert=True)
            else:
                await callback.answer("⏳ این پرداخت در حال پردازش است.", show_alert=True)
            return
        try:
            payload = json.loads(payment.get("extra") or "{}")
        except Exception:
            payload = {}
        cfg = db.get_config_by_id(int(payload.get("cfg_id"))) if payload.get("cfg_id") else None
        if not cfg or not cfg.get("service_id"):
            db.set_online_payment_status(payment["id"], "pending")
            await callback.answer("❌ سرویس تمدیدی پیدا نشد.", show_alert=True); return
        panel = db.get_vpn_panel(cfg.get("panel_id")) if cfg.get("panel_id") else None
        if not panel:
            db.set_online_payment_status(payment["id"], "pending")
            await callback.answer("❌ پنل این سرویس پیدا نشد؛ تمدید انجام نشد.", show_alert=True)
            return
        ok, panel_data, msg = await panels.renew_existing_service(
            panel, cfg["service_id"], float(payload.get("volume_gb") or 0), int(payload.get("days") or 0)
        )
        if not ok:
            db.set_online_payment_status(payment["id"], "pending")
            await answer_rich(callback.message, f"❌ تمدید روی پنل انجام نشد. پرداخت شما هنوز در وضعیت قابل بررسی است.\n{msg}", reply_markup=renew_payment_keyboard())
            return
        _save_renewal_snapshot(cfg, panel_data)
        db.mark_online_payment_paid(payment["id"], None)
        added_volume = float(payload.get("volume_gb") or 0)
        added_days = int(payload.get("days") or 0)
        service_name = alerts.get_config_service_username(cfg, panel_data)
        await answer_rich(callback.message, t("renew_done", service_name=service_name, details=_renewal_confirmation_details(panel_data, added_volume, added_days, fallback_expiry=cfg.get("expiry") if cfg else None)))
        try:
            await alerts.log_renewal_to_channel(callback.bot, db.get_user_by_id(cfg["user_id"]) or {}, cfg, panel_data, int(payment.get("price") or 0), added_volume, added_days)
        except Exception:
            logger.exception("ثبت لاگ تمدید آنلاین در کانال ناموفق بود")
        try:
            user_for_summary = db.get_user_by_id(cfg["user_id"]) or {}
            await alerts.send_rich(callback.bot, ADMIN_ID, alerts.admin_delivery_summary(user_for_summary, service_name, alerts.get_config_package_name(cfg), int(payment.get("price") or 0)))
        except Exception:
            logger.exception("ارسال خلاصه تمدید آنلاین برای ادمین ناموفق بود")
        return

    success_text = (
        "✅ کیف پول شما شارژ شد."
        if payment_kind == "wallet_charge"
        else "✅ پرداخت شما تأیید شد و سفارش شما در صف ارسال سرویس قرار گرفت."
    )
    _sticker_key = "walletcharge_pay_online" if payment_kind == "wallet_charge" else "plan_pay_online"
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, _sticker_key, success_text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]))

    if payment_kind == "wallet_charge":
        from handlers.wallet import finalize_wallet_charge_online_payment
        await finalize_wallet_charge_online_payment(callback.bot, payment)
    else:
        await finalize_online_payment(callback.bot, payment)


# ---------------------------------------------------------------------------
# پرداخت کارت به کارت
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("pay_crypto_"))
async def pay_with_crypto(callback: types.CallbackQuery, state: FSMContext):
    plan_key=callback.data.replace("pay_crypto_",""); plan=db.get_effective_plan(plan_key)
    if not plan: await callback.answer(t("plan_not_found"),show_alert=True); return
    data=await state.get_data(); final_price,_,winning_code=_compute_final_price(plan_key,plan,callback.from_user.id,data)
    await state.update_data(crypto_price=final_price,crypto_plan_key=plan_key,crypto_discount_code=winning_code)
    await _start_crypto_invoice(callback,state,renewal=False,plan_key=plan_key); await callback.answer()

@router.callback_query(F.data.startswith("pay_card_"))
async def pay_with_card(callback: types.CallbackQuery, state: FSMContext):
    plan_key = callback.data.replace("pay_card_", "")
    plan = db.get_effective_plan(plan_key)
    if plan is None:
        await answer_rich(callback, t("plan_not_found"), show_alert=True)
        return

    if plan_key == FREE_TEST_PLAN_KEY:
        user = db.get_user(callback.from_user.id)
        if user and db.has_used_free_test(user["id"]):
            await callback.answer(
                t("free_test_used"),
                show_alert=True,
            )
            return

    data = await state.get_data()
    final_price, _note, winning_code = _compute_final_price(plan_key, plan, callback.from_user.id, data)

    invoicing_user = db.get_user(callback.from_user.id)
    invoice = db.create_invoice(
        user_id=invoicing_user["id"] if invoicing_user else None,
        telegram_id=str(callback.from_user.id),
        kind="plan_card",
        label=plan["name"],
        price=final_price,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])

    await state.update_data(
        card_purchase_plan=plan_key, card_purchase_price=final_price, card_purchase_discount_code=winning_code,
        card_invoice_id=invoice["id"],
    )
    await state.set_state(UserStates.waiting_card_purchase_receipt)

    invoice_text = _render_card_invoice_text(
        "invoice_plan_card",
        PLAN_CARD_INVOICE_DEFAULT,
        {
            "plan_name": plan["name"],
            "amount": final_price,
            "card_number": bot_info.get("card_number") or "",
            "card_holder": bot_info.get("card_holder") or "",
        },
        deadline_str,
    )
    await show_menu_with_sticker(
        callback.bot,
        callback.message.chat.id,
        "plan_pay_card",
        invoice_text,
        parse_mode=None,
        entities=getattr(invoice_text, "entities", None),
    )
    await callback.answer()


@router.message(UserStates.waiting_card_purchase_receipt)
async def receive_purchase_receipt(message: types.Message, state: FSMContext):
    uid = str(message.from_user.id)
    data = await state.get_data()
    if data.get("renew_invoice_id"):
        invoice=db.consume_invoice(data.get("renew_invoice_id"))
        if not invoice:
            await answer_rich(message,t("invoice_expired_wait")); await state.clear(); return
        user=db.get_user(uid); payload=json.loads(invoice.get("payload") or "{}")
        receipt_id=db.create_pending_receipt("renew_card",uid,user["id"] if user else None,data.get("renew_service_name") or "تمدید سرویس",int(invoice["price"]),extra=json.dumps(payload,ensure_ascii=False))
        db.delete_invoice(invoice["id"])
        await forward_admin_task_message(message.bot,ADMIN_ID,"receipts",message.chat.id,message.message_id)
        renew_cfg=db.get_config_by_id(payload.get("cfg_id")) if payload.get("cfg_id") else None
        renew_cat=db.get_vip_category((renew_cfg or {}).get("category_id")) if renew_cfg else None
        renew_user = db.get_user(uid) or {}
        renew_service_name = alerts.get_config_service_username(renew_cfg or {})
        renew_package_name = alerts.get_config_package_name(renew_cfg or {})
        renew_details = f"+{float(payload.get('volume_gb') or 0):g} گیگ | +{int(payload.get('days') or 0)} روز"
        admin_renew_text = t(
            "admin_renew_card_receipt",
            customer=message.from_user.full_name or renew_user.get("name") or "-",
            telegram_id=message.from_user.id,
            service_username=renew_service_name,
            package_name=renew_package_name,
            category_name=(renew_cat or {}).get("name") or "-",
            renew_details=renew_details,
            amount=int(invoice["price"]),
        )
        await send_admin_task_message(message.bot,ADMIN_ID,"receipts",admin_renew_text,reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("admin_renew_approve"),callback_data=f"approverenew|{receipt_id}",style="success"),InlineKeyboardButton(text=t("admin_renew_reject"),callback_data=f"rejectrenew|{receipt_id}",style="danger")]]))
        await answer_rich(message,t("renew_card_registered"),reply_markup=get_main_keyboard(message.from_user.id)); await state.clear(); return
    plan_key = data.get("card_purchase_plan")
    final_price = data.get("card_purchase_price")
    winning_code = data.get("card_purchase_discount_code")
    invoice_id = data.get("card_invoice_id")
    plan = db.get_effective_plan(plan_key)

    if plan is None or final_price is None:
        await answer_rich(message, t("purchase_receipt_error"), reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    if not invoice_id or db.consume_invoice(invoice_id) is None:
        await answer_rich(message, 
            t("invoice_expired_wait"),
            reply_markup=get_main_keyboard(message.from_user.id),
        )
        await state.clear()
        return

    user = db.get_user(uid)
    # 🐛 فیکس: کد تخفیف قبلاً همین‌جا (قبل از تأیید ادمین) مصرف می‌شد؛ یعنی اگر ادمین رسید
    # را رد می‌کرد، سهم کد تخفیف به کاربر برنمی‌گردد. حالا مانند مینی‌اپ، کد تخفیف
    # فقط همراه رسید ذخیره می‌شود و در approve_purchase (handlers/admin.py) مصرف خواهد شد.

    # 🐛 فیکس: receipt_id را حتماً نگه میداریم تا دکمه‌های تأیید/رد زیر همین رسید را در callback_data حمل کنند
    # (وگرنه دو رسید با همان پلن/قیمت با هم تداخل می‌کنند و پیام «قبلاً پردازش شده» اشتباه می‌دهد).
    receipt_id = None
    try:
        receipt_id = db.create_pending_receipt(
            "plan_card", uid, user["id"] if user else None, plan["name"], final_price,
            extra=plan_key, plan_key=plan_key, discount_code=winning_code,
        )
    except Exception:
        receipt_id = None

    if invoice_id:
        db.delete_invoice(invoice_id)

    await forward_admin_task_message(message.bot, ADMIN_ID, "receipts", message.chat.id, message.message_id)
    await send_admin_task_message(
        message.bot, ADMIN_ID, "receipts",
        f"💳 رسید خرید کارت‌به‌کارت\n\n"
        f"👤 {message.from_user.full_name}\n"
        f"🆔 {uid}\n"
        f"📦 {plan['name']}\n"
        f"💰 {final_price:,} تومان",
        reply_markup=admin_purchase_card_approval_keyboard(uid, plan_key, final_price, receipt_id or 0),
    )
    # 🐛 فیکس: منوی دائمی پایین صفحه‌ی کاربر را صریحاً روی همین پیام تازه می‌کنیم؛ قبلاً این
    # پیام بدون reply_markup فرستاده می‌شد و برای کاربری که منوی پایین صفحه‌اشرا جمعشده
    # بود (مثلاً پس از یک پیام دارای دکمه‌ی inline)، منو تا زدن /start دوباره باز
    # نمی‌شد و کاربر/مشتری فکر می‌کرد منو کاملاً گم شده.
    await answer_rich(message, 
        progress_bar(3, 3) + t("card_receipt_registered"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    await state.update_data(
        discount_percent=0, discount_code="", card_purchase_plan=None,
        card_purchase_price=None, card_purchase_discount_code=None, card_invoice_id=None,
    )
    await state.set_state(None)


@router.message(UserStates.waiting_card_purchase_receipt)
async def purchase_receipt_wrong_format(message: types.Message):
    await answer_rich(message, "❌ رسید باید به‌صورت عکس یا متن ارسال شود.")


# ---------------------------------------------------------------------------
# 🔁 تمدید سرویس کاربر
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("renewcfg_"))
async def renew_choose_service(callback: types.CallbackQuery, state: FSMContext):
    user = db.get_user(callback.from_user.id)
    try:
        cfg_id = int(callback.data.replace("renewcfg_", ""))
    except Exception:
        cfg_id = 0
    cfg = db.get_config_by_id(cfg_id) if user else None
    if not user or not cfg or cfg.get("deleted"):
        await callback.answer(t("service_not_owned"), show_alert=True)
        return

    # مالکیت را از لیست سرویس‌های خود کاربر هم تأیید می‌کنیم. در بعضی دیتابیس‌های
    # قدیمی ممکن است user_id سرویس با شناسه داخلی کاربر ناسازگار/قدیمی باشد؛
    # در این حالت نباید کاربر صاحب سرویس، بی‌دلیل «این سرویس برای شما نیست» بگیرد.
    owned_cfg = next((c for c in db.get_configs(user["id"], include_deleted=True)
                      if int(c.get("id", 0)) == cfg_id), None)
    if not owned_cfg:
        await callback.answer(t("service_not_owned"), show_alert=True)
        return
    cfg = owned_cfg

    await enrich_configs_with_subscription_names([cfg])
    name = cfg.get("_display_name") or cfg.get("plan") or "سرویس"
    category_id = cfg.get("category_id")
    if not category_id:
        try:
            plan = db.get_vip_plan(cfg.get("plan"))
            category_id = plan.get("category_id") if plan else None
        except Exception:
            category_id = None
    settings = _renewal_settings(category_id, cfg.get("plan_key"))
    mode = settings["mode"]
    await state.update_data(
        renew_cfg_id=cfg_id, renew_service_name=name, renew_mode=mode,
        renew_category_id=category_id, renew_plan_key=cfg.get("plan_key"), renew_telegram_id=callback.from_user.id,
    )

    if mode == "gb":
        prompt, kb = "حجم موردنظر برای تمدید را انتخاب کنید:", renew_volume_keyboard(settings)
    elif mode == "both":
        prompt, kb = "ابتدا تعداد روز تمدید را انتخاب کنید:", renew_days_keyboard(settings)
    else:
        prompt, kb = "تعداد روز موردنظر برای تمدید را انتخاب کنید:", renew_days_keyboard(settings)
    await edit_rich(
        callback.message,
        t("renew_service_title", service_name=name) + "\n\n" + prompt,
        reply_markup=kb,
    )
    await callback.answer()

def _renew_validate(settings: dict, volume_gb: float, days: int) -> tuple[bool, str]:
    mode = settings.get("mode", "day")
    if mode in ("day", "both"):
        if days < int(settings.get("min_day", 1)):
            return False, f"حداقل زمان تمدید {settings.get('min_day', 1)} روز است."
        if settings.get("max_day") and days > int(settings["max_day"]):
            return False, f"حداکثر زمان تمدید {settings['max_day']} روز است."
    if mode in ("gb", "both"):
        if volume_gb < float(settings.get("min_gb", 1)):
            return False, f"حداقل حجم تمدید {settings.get('min_gb', 1)} گیگ است."
        if settings.get("max_gb") and volume_gb > float(settings["max_gb"]):
            return False, f"حداکثر حجم تمدید {settings['max_gb']} گیگ است."
    return True, ""


def _renewal_confirmation_details(panel_data: dict | None, added_volume: float, added_days: int, fallback_expiry=None) -> str:
    """نمایش تمیز نتیجه تمدید؛ فقط بخش‌هایی را نشان می‌دهد که واقعاً تمدید شده‌اند."""
    values = _renewal_confirmation_values(panel_data, added_volume, added_days, fallback_expiry=fallback_expiry)
    if added_volume and added_days:
        detail = t(
            "renew_volume_days_detail",
            previous_volume=values["previous_volume"],
            added_volume=values["added_volume"],
            new_volume=values["new_volume"],
            previous_days=values["previous_days"],
            added_days=values["added_days"],
            new_days=values["new_days"],
        )
    elif added_volume:
        detail = t(
            "renew_volume_only_detail",
            previous_volume=values["previous_volume"],
            added_volume=values["added_volume"],
            new_volume=values["new_volume"],
        )
    elif added_days:
        detail = t(
            "renew_days_only_detail",
            previous_days=values["previous_days"],
            added_days=values["added_days"],
            new_days=values["new_days"],
        )
    else:
        return t("renew_details_empty")
    return t("renew_details_title") + "\n\n" + detail

async def _renew_prepare_payment(target, state, volume_gb=0, days=0):
    data = await state.get_data()
    cfg = db.get_config_by_id(data.get("renew_cfg_id")) if data.get("renew_cfg_id") else None
    # در CallbackQuery، target همان callback.message است و from_user آن، خودِ
    # بات است؛ بنابراین مالکیت نباید از target.from_user خوانده شود. شناسه‌ی
    # واقعی کاربر را از state نگه می‌داریم و در صورت نبودن، از target فقط برای
    # پیام‌های معمولی استفاده می‌کنیم.
    telegram_id = data.get("renew_telegram_id")
    if telegram_id is None:
        telegram_id = getattr(getattr(target, "from_user", None), "id", None)
    user = db.get_user(telegram_id) if telegram_id is not None else None
    if not cfg or not user or cfg.get("deleted"):
        await answer_rich(target, t("service_not_owned"))
        await state.clear()
        return
    owned_cfg = next((c for c in db.get_configs(user["id"], include_deleted=True)
                      if int(c.get("id", 0)) == int(cfg.get("id", 0))), None)
    if not owned_cfg:
        await answer_rich(target, t("service_not_owned"))
        await state.clear()
        return
    cfg = owned_cfg

    category_id = cfg.get("category_id") or data.get("renew_category_id")
    if not category_id:
        try:
            plan = db.get_vip_plan(cfg.get("plan"))
            category_id = plan.get("category_id") if plan else None
        except Exception:
            category_id = None
    settings = _renewal_settings(category_id, cfg.get("plan_key"))
    mode = settings.get("mode", "day")
    if mode == "day":
        volume_gb = 0
    elif mode == "gb":
        days = 0
    ok, error = _renew_validate(settings, float(volume_gb or 0), int(days or 0))
    if not ok:
        await answer_rich(target, "❌ " + error)
        return

    price = (
        int(round(float(volume_gb or 0) * int(settings.get("price_gb", 0))))
        + int(days or 0) * int(settings.get("price_day", 0))
    )
    await state.update_data(
        renew_volume_gb=float(volume_gb or 0),
        renew_days=int(days or 0),
        renew_price=price,
    )
    await answer_rich(
        target,
        t(
            "renew_summary",
            service_name=data.get("renew_service_name") or cfg.get("plan"),
            volume=(f"{volume_gb:g} گیگ" if volume_gb else "بدون تغییر"),
            days=(f"{days} روز" if days else "بدون تغییر"),
            price=price,
        ),
        reply_markup=renew_payment_keyboard(),
    )

@router.callback_query(F.data.startswith("renewvol_"))
async def renew_volume_choice(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.replace("renewvol_", "")
    data = await state.get_data()
    if value == "custom":
        await state.set_state(UserStates.waiting_renew_custom_volume)
        await answer_rich(
            callback.message,
            "حجم تمدید را به گیگ وارد کنید:",
            reply_markup=back_button("renew_cancel", t("renew_cancel")),
        )
        await callback.answer()
        return
    try:
        volume = float(value)
    except Exception:
        await callback.answer("❌ مقدار نامعتبر.", show_alert=True)
        return
    if data.get("renew_mode") == "both":
        await _renew_prepare_payment(callback.message, state, volume, int(data.get("renew_days") or 0))
    else:
        await _renew_prepare_payment(callback.message, state, volume, 0)
    await callback.answer()

@router.message(UserStates.waiting_renew_custom_volume)
async def renew_custom_volume(message: types.Message, state: FSMContext):
    try:
        value = float((message.text or "").strip().replace(",", "."))
    except Exception:
        value = 0
    if value <= 0:
        await answer_rich(message, "❌ حجم نامعتبر است؛ یک عدد مثبت وارد کنید.")
        return
    data = await state.get_data()
    days = int(data.get("renew_days") or 0) if data.get("renew_mode") == "both" else 0
    await _renew_prepare_payment(message, state, value, days)
    if (await state.get_data()).get("renew_price") is not None:
        await state.set_state(None)

@router.callback_query(F.data.startswith("renewdays_"))
async def renew_days_choice(callback: types.CallbackQuery, state: FSMContext):
    value = callback.data.replace("renewdays_", "")
    if value == "custom":
        await state.set_state(UserStates.waiting_renew_days)
        await answer_rich(
            callback.message,
            "تعداد روز تمدید را وارد کنید:",
            reply_markup=back_button("renew_cancel", t("renew_cancel")),
        )
        await callback.answer()
        return
    try:
        days = int(value)
    except Exception:
        await callback.answer("❌ مقدار نامعتبر.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("renew_mode") == "both":
        await state.update_data(renew_days=days)
        settings = _renewal_settings(data.get("renew_category_id"), data.get("renew_plan_key"))
        await answer_rich(callback.message, "حالا حجم تمدید را انتخاب کنید:", reply_markup=renew_volume_keyboard(settings))
        await callback.answer()
        return
    await _renew_prepare_payment(callback.message, state, 0, days)
    await callback.answer()

@router.message(UserStates.waiting_renew_days)
async def renew_custom_days(message: types.Message, state: FSMContext):
    try:
        value = int((message.text or "").strip())
    except Exception:
        value = 0
    if value <= 0:
        await answer_rich(message, "❌ تعداد روز نامعتبر است؛ یک عدد مثبت وارد کنید.")
        return
    data = await state.get_data()
    if data.get("renew_mode") == "both":
        await state.set_state(None)
        await state.update_data(renew_days=value)
        settings = _renewal_settings(data.get("renew_category_id"), data.get("renew_plan_key"))
        await answer_rich(message, "حالا حجم تمدید را انتخاب کنید:", reply_markup=renew_volume_keyboard(settings))
        return
    await _renew_prepare_payment(message, state, 0, value)
    if (await state.get_data()).get("renew_price") is not None:
        await state.set_state(None)

@router.callback_query(F.data == "renew_cancel")
async def renew_cancel(callback:types.CallbackQuery,state:FSMContext):
    await state.clear(); await answer_rich(callback.message,t("renew_cancelled"),reply_markup=main_reply_keyboard()); await callback.answer()

@router.callback_query(F.data == "renewpay_wallet")
async def renew_pay_wallet(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cfg = db.get_config_by_id(data.get("renew_cfg_id")) if data.get("renew_cfg_id") else None
    user = db.get_user(callback.from_user.id)
    price = int(data.get("renew_price") or 0)
    if not user or not cfg or cfg.get("user_id") != user.get("id"):
        await callback.answer(t("service_not_owned"), show_alert=True); return
    if int(user.get("wallet") or 0) < price:
        await answer_rich(callback.message, t("wallet_insufficient", price=price, wallet=user.get("wallet", 0), needed=price-int(user.get("wallet") or 0)), reply_markup=insufficient_balance_keyboard())
        await callback.answer(); return
    if not db.deduct_from_wallet(user["id"], price, f"تمدید {data.get('renew_service_name') or cfg.get('plan')}"):
        await callback.answer(t("wallet_not_enough"), show_alert=True); return
    panel = db.get_vpn_panel(cfg.get("panel_id")) if cfg.get("panel_id") else None
    if not panel:
        try: db.add_to_wallet(user["id"], price, f"بازگشت وجه تمدید ناموفق {data.get('renew_service_name') or cfg.get('plan')}")
        except Exception: pass
        await callback.answer("❌ پنل این سرویس پیدا نشد؛ مبلغ به کیف پول شما برگشت.", show_alert=True)
        return
    ok, panel_data, msg = await panels.renew_existing_service(
        panel, cfg["service_id"], float(data.get("renew_volume_gb") or 0), int(data.get("renew_days") or 0)
    )
    if not ok:
        # بازگرداندن موجودی در صورت شکست واقعی پنل
        try: db.add_to_wallet(user["id"], price, f"بازگشت وجه تمدید ناموفق {data.get('renew_service_name') or cfg.get('plan')}")
        except Exception: pass
        await answer_rich(callback.message, f"❌ تمدید روی پنل انجام نشد. مبلغ به کیف پول شما برگشت.\n{msg}", reply_markup=renew_payment_keyboard())
        await callback.answer(); return
    _save_renewal_snapshot(cfg, panel_data)
    await state.clear()
    added_volume = float(data.get("renew_volume_gb") or 0)
    added_days = int(data.get("renew_days") or 0)
    service_name = alerts.get_config_service_username(cfg, panel_data)
    await answer_rich(callback.message, t("renew_done", service_name=service_name, details=_renewal_confirmation_details(panel_data, added_volume, added_days, fallback_expiry=cfg.get("expiry") if cfg else None)))
    try:
        await alerts.log_renewal_to_channel(callback.bot, user, cfg, panel_data, price, added_volume, added_days)
    except Exception:
        logger.exception("ثبت لاگ تمدید کیف پول در کانال ناموفق بود")
    try:
        await alerts.send_rich(callback.bot, ADMIN_ID, alerts.admin_delivery_summary(user, service_name, alerts.get_config_package_name(cfg), price))
    except Exception:
        logger.exception("ارسال خلاصه تمدید کیف پول برای ادمین ناموفق بود")
    await callback.answer("تمدید شد")


@router.callback_query(F.data == "renewpay_online")
async def renew_pay_online(callback: types.CallbackQuery, state: FSMContext):
    if not UNIQUEPAY_ENABLED:
        await callback.answer(t("payment_not_active"), show_alert=True); return
    data = await state.get_data(); cfg = db.get_config_by_id(data.get("renew_cfg_id")) if data.get("renew_cfg_id") else None
    user = db.get_user(callback.from_user.id); price = int(data.get("renew_price") or 0)
    if not user or not cfg:
        await callback.answer(t("service_not_owned"), show_alert=True); return
    if price < ONLINE_PAYMENT_MIN_AMOUNT:
        await answer_rich(callback.message, "❌ حداقل مبلغ پرداخت آنلاین رعایت نشده است. لطفاً از کیف پول، کارت‌به‌کارت یا ارز دیجیتال استفاده کنید.", reply_markup=renew_payment_keyboard()); await callback.answer(); return
    await answer_rich(callback, t("building_payment"))
    hash_id = uniquepay.new_hash_id("renew")
    invoice = await payments.create_invoice(hash_id, price)
    if not invoice or not invoice.get("paymentLink"):
        await answer_rich(callback.message, "❌ ساخت فاکتور آنلاین ناموفق بود. لطفاً روش دیگری را انتخاب کنید.", reply_markup=renew_payment_keyboard()); return
    payload = json.dumps({"cfg_id": cfg["id"], "volume_gb": data.get("renew_volume_gb", 0), "days": data.get("renew_days", 0)}, ensure_ascii=False)
    payment_id = db.create_online_payment(user_id=user["id"], telegram_id=str(callback.from_user.id), hash_id=hash_id, plan_name=data.get("renew_service_name") or cfg.get("plan") or "تمدید سرویس", price=price, order_type="renew", plan_key=cfg.get("plan_key"), payment_link=invoice.get("paymentLink"), ref_id=str(invoice.get("refId")), kind="renew", extra=payload, provider=invoice.get("provider", "uniquepay"))
    await state.update_data(renew_online_payment_id=payment_id)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_online", progress_bar(2, 3) + f"\n\n🔁 تمدید سرویس\n📦 {data.get('renew_service_name') or cfg.get('plan')}\n💰 مبلغ قابل پرداخت: {price:,} تومان", reply_markup=online_payment_keyboard(invoice["paymentLink"], payment_id, cancel_callback="renew_cancel"))
    await callback.answer()


@router.callback_query(F.data == "renewpay_card")
async def renew_pay_card(callback:types.CallbackQuery,state:FSMContext):
    data=await state.get_data(); cfg=db.get_config_by_id(data.get("renew_cfg_id")) if data.get("renew_cfg_id") else None; user=db.get_user(callback.from_user.id)
    if not cfg or not user: await callback.answer(t("service_not_found"),show_alert=True); return
    invoice=db.create_invoice(user["id"],str(callback.from_user.id),"renew_card",data.get("renew_service_name") or cfg.get("plan") or "تمدید سرویس",int(data.get("renew_price") or 0),payload={"cfg_id":cfg["id"],"volume_gb":data.get("renew_volume_gb",0),"days":data.get("renew_days",0)})
    await state.update_data(renew_invoice_id=invoice["id"]); await state.set_state(UserStates.waiting_card_purchase_receipt)
    deadline_str = format_deadline_time(invoice["expires_at"])
    text = _render_card_invoice_text(
        "renew_card_invoice",
        RENEW_CARD_INVOICE_DEFAULT,
        {
            "service_name": data.get("renew_service_name") or cfg.get("plan") or "تمدید سرویس",
            "volume": f"{data.get('renew_volume_gb', 0):g} گیگ" if data.get("renew_volume_gb") else "بدون تغییر",
            "days": f"{int(data.get('renew_days', 0))} روز" if data.get("renew_days") else "بدون تغییر",
            "amount": invoice["price"],
            "card_number": bot_info.get("card_number") or "",
            "card_holder": bot_info.get("card_holder") or "",
        },
        deadline_str,
    )
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "plan_pay_card", text, parse_mode=None, entities=getattr(text, "entities", None)); await callback.answer()

@router.callback_query(F.data == "renewpay_crypto")
async def renew_pay_crypto(callback:types.CallbackQuery,state:FSMContext):
    await _start_crypto_invoice(callback,state,renewal=True); await callback.answer()

async def _start_crypto_invoice(callback,state,renewal=False,plan_key=None):
    data=await state.get_data(); user=db.get_user(callback.from_user.id)
    if not user:return
    if renewal:
        cfg=db.get_config_by_id(data.get("renew_cfg_id")); price=int(data.get("renew_price") or 0); label=data.get("renew_service_name") or (cfg or {}).get("plan") or "تمدید سرویس"; payload={"cfg_id":(cfg or {}).get("id"),"volume_gb":data.get("renew_volume_gb",0),"days":data.get("renew_days",0)}; kind="renew_crypto"
    else:
        plan=db.get_effective_plan(plan_key); price=int(data.get("crypto_price") or 0); label=plan["name"]; payload={"plan_key":plan_key,"discount_code":data.get("crypto_discount_code")}; kind="plan_crypto"
    assets=[]
    for asset,rk,wk,network in (("USDT","crypto_usdt_rate","crypto_usdt_wallet","TRC20"),("TON","crypto_ton_rate","crypto_ton_wallet","TON"),("TRX","crypto_trx_rate","crypto_trx_wallet","TRON")):
        try: rate=float(bot_info.get(rk) or 0)
        except Exception: rate=0
        wallet=bot_info.get(wk) or ""
        if rate>0 and wallet: assets.append((asset,rate,wallet,network))
    if not assets:
        await answer_rich(callback.message,t("crypto_not_configured"),reply_markup=renew_payment_keyboard() if renewal else purchase_payment_keyboard(plan_key)); return
    rows="\n".join(f"• {a}: {price/r:.2f} {a}" for a,r,_,_ in assets)
    await state.update_data(crypto_renewal=renewal,crypto_plan_key=plan_key,crypto_price=price,crypto_assets=assets,crypto_payload=payload)
    await answer_rich(
        callback.message,
        t("crypto_choose_asset_intro",plan_name=label,price=price,rows=rows),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="USDT (TRC20)",
                callback_data="cryptoasset_USDT",
                style="success"
            )],
            [InlineKeyboardButton(
                text="TON",
                callback_data="cryptoasset_TON",
                style="primary"
            ),
             InlineKeyboardButton(
                 text="TRX",
                 callback_data="cryptoasset_TRX",
                 style="danger"
             )],
            [InlineKeyboardButton(
                text="بازگشت",
                callback_data="renew_cancel",
                style="danger"
            )],
        ]),
    )

@router.callback_query(F.data.startswith("cryptoreceipt_"))
async def crypto_receipt_hint(callback:types.CallbackQuery): await callback.answer(t("crypto_receipt_hint_alert"),show_alert=True)

@router.callback_query(F.data.startswith("cryptoasset_"))
async def crypto_asset_choice(callback:types.CallbackQuery,state:FSMContext):
    asset=callback.data.replace("cryptoasset_",""); data=await state.get_data(); chosen=next((x for x in (data.get("crypto_assets") or []) if x[0]==asset),None)
    if not chosen: await callback.answer(t("crypto_asset_unavailable"),show_alert=True); return
    asset,rate,wallet,network=chosen; price=float(data.get("crypto_price") or 0); amount=price/rate
    invoice=db.create_invoice(
        db.get_user(callback.from_user.id)["id"],
        str(callback.from_user.id),
        "renew_crypto" if data.get("crypto_renewal") else "plan_crypto",
        data.get("renew_service_name") or "پرداخت ارزی",
        int(price),
        payload=data.get("crypto_payload"),
        minutes=30,
    )
    deadline_str = format_deadline_time(invoice["expires_at"])
    await state.update_data(crypto_invoice_id=invoice["id"],crypto_asset=asset,crypto_amount=amount,crypto_wallet=wallet,crypto_deadline=deadline_str)
    await state.set_state(UserStates.waiting_crypto_receipt)
    await answer_rich(
        callback.message,
        t(
            "crypto_payment_detail",
            asset=asset,
            plan_name=data.get("renew_service_name") or data.get("crypto_plan_key") or "سرویس",
            price=int(price),
            amount=f"{amount:.2f}",
            network=network,
            wallet=wallet,
            deadline=deadline_str,
        ),
        reply_markup=crypto_payment_keyboard(asset,wallet,f"{amount:.2f}"),
    )
    await callback.answer()

@router.message(UserStates.waiting_crypto_receipt)
async def receive_crypto_receipt(message:types.Message,state:FSMContext):
    data=await state.get_data()
    invoice_id=data.get("crypto_invoice_id")
    invoice=db.get_invoice_by_id(invoice_id) if invoice_id else None
    if not invoice:
        await answer_rich(message,t("invoice_expired_wait")); await state.clear(); return

    # متن/Hash را واقعاً در رسید ذخیره می‌کنیم؛ قبلاً فقط پیام را forward می‌کردیم
    # و مقدار Hash داخل pending_receipt باقی نمی‌ماند.
    submitted_text=(message.text or message.caption or "").strip()
    photo_file_id=None
    if message.photo:
        photo_file_id=message.photo[-1].file_id
    if not submitted_text and not photo_file_id:
        await answer_rich(message,t("crypto_receipt_invalid"))
        return

    invoice=db.consume_invoice(invoice_id) if invoice_id else None
    if not invoice:
        await answer_rich(message,t("invoice_expired_wait")); await state.clear(); return

    user=db.get_user(message.from_user.id)
    kind="crypto_renew" if data.get("crypto_renewal") else "crypto_plan"
    payload=dict(data.get("crypto_payload") or {})
    payload["receipt_text"]=submitted_text
    if photo_file_id:
        payload["receipt_file_id"]=photo_file_id
    extra=json.dumps(payload,ensure_ascii=False)
    receipt_id=db.create_pending_receipt(
        kind,str(message.from_user.id),user["id"],
        data.get("renew_service_name") or data.get("crypto_plan_key") or "پرداخت ارزی",
        int(data.get("crypto_price") or 0),
        extra=extra,plan_key=data.get("crypto_plan_key")
    )
    db.delete_invoice(invoice["id"])

    is_crypto_renewal = bool(data.get("crypto_renewal"))
    if is_crypto_renewal:
        renew_cfg = db.get_config_by_id((data.get("crypto_payload") or {}).get("cfg_id"))
        category = db.get_vip_category((renew_cfg or {}).get("category_id")) if renew_cfg else None
        package_name = alerts.get_config_package_name(renew_cfg or {})
        service_name = alerts.get_config_service_username(renew_cfg or {})
        renew_details = f"+{float((data.get('crypto_payload') or {}).get('volume_gb') or 0):g} گیگ | +{int((data.get('crypto_payload') or {}).get('days') or 0)} روز"
        template_key = "admin_crypto_renew_receipt_photo" if photo_file_id else "admin_crypto_renew_receipt"
        admin_values = dict(
            name=html.escape(message.from_user.full_name or "-"),
            telegram_id=message.from_user.id,
            service_name=html.escape(service_name),
            plan_name=html.escape(str(package_name)),
            category_name=html.escape((category or {}).get("name") or "-"),
            renew_details=html.escape(renew_details),
            price=int(data.get("crypto_price") or 0),
            amount=f"{float(data.get('crypto_amount') or 0):.2f}",
            asset=data.get("crypto_asset","-"),
        )
    else:
        plan_obj = db.get_effective_plan(data.get("crypto_plan_key"))
        package_name = (plan_obj or {}).get("name") or data.get("crypto_plan_key") or "سرویس"
        template_key = "admin_crypto_receipt_photo" if photo_file_id else "admin_crypto_receipt"
        admin_values = dict(
            name=html.escape(message.from_user.full_name or "-"),
            telegram_id=message.from_user.id,
            plan_name=html.escape(str(package_name)),
            price=int(data.get("crypto_price") or 0),
            amount=f"{float(data.get('crypto_amount') or 0):.2f}",
            asset=data.get("crypto_asset","-"),
            hash_text=html.escape(submitted_text),
        )
    admin_text=t(template_key, **admin_values)

    crypto_admin_markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("admin_crypto_approve"),callback_data=f"approvecrypto|{receipt_id}"),
         InlineKeyboardButton(text=t("admin_crypto_reject"),callback_data=f"rejectcrypto|{receipt_id}")]
    ])

    # اگر رسید تصویری است، خود عکس باید به همان ادمین/ادمین‌های مسئول برسد؛
    # صرفاً ذخیره کردن file_id داخل pending_receipts کافی نیست و باعث می‌شد
    # پنل ادمین فقط متن/Hash را ببیند. مسیر دریافت ادمین‌های عملیاتی را همان
    # مسیر سایر رسیدها نگه می‌داریم تا دسترسی ادمین فرعی هم حفظ شود.
    if photo_file_id:
        try:
            targets = db.get_admin_notification_targets("receipts")
        except Exception:
            targets = []
        if not targets:
            targets = [str(ADMIN_ID)]
        for tid in targets:
            try:
                await send_photo_rich(
                    message.bot,
                    int(tid),
                    photo_file_id,
                    caption=admin_text,
                    reply_markup=crypto_admin_markup,
                )
            except Exception:
                logger.exception("ارسال عکس رسید ارزی به ادمین %s ناموفق بود", tid)
    else:
        await send_admin_task_message(
            message.bot,ADMIN_ID,"receipts",admin_text,
            reply_markup=crypto_admin_markup,
        )
    await answer_rich(message,t("renew_card_registered") if data.get("crypto_renewal") else t("receipt_registered"),reply_markup=main_reply_keyboard())
    await state.clear()

# ---------------------------------------------------------------------------
# سرویس‌های من
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "my_configs")
async def my_configs(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    configs = [c for c in db.get_configs(user["id"]) if not c.get("deleted")]
    if not configs:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_empty", 
            t("configs_empty"),
            reply_markup=back_button("back", "🏠 بازگشت به منوی اصلی"),
        )
    else:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_has", 
            t("configs_has"),
            reply_markup=my_configs_menu(),
        )
    await callback.answer()


@router.callback_query(F.data == "my_configs_vip")
async def my_configs_vip(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    configs = [c for c in db.get_configs_by_type(user["id"], "vip", include_deleted=True)]
    if not configs:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_list_empty", 
            t("vip_configs_empty"),
            reply_markup=back_button("my_configs", "🔙 بازگشت"),
        )
    else:
        # نام نمایشی سرویس باید قبل از ساخت کیبورد از پنل/Subscription mirror شود؛
        # در غیر این صورت سرویس‌های قدیمی فقط service_id خراب یا نام پلن DB را نشان می‌دهند.
        await enrich_configs_with_subscription_names(configs)
        for cfg in configs:
            try: cfg["_live_status"] = await get_live_service_status(cfg)
            except Exception: cfg["_live_status"] = None
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "my_configs_list_has", 
            t("vip_configs_has"),
            reply_markup=my_configs_list_keyboard(configs, "🚀", "my_configs"),
        )
    await callback.answer()




@router.callback_query(F.data.startswith("viewconfig_"))
async def view_config(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("viewconfig_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"]:
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return

    # همان موتور mirror نام برای جزئیات هم اجرا شود تا لیست و صفحه‌ی جزئیات یک نام داشته باشند.
    await enrich_configs_with_subscription_names([cfg])

    try:
        decrypted = crypto.decrypt_config(cfg["config"])
    except Exception:
        decrypted = t("config_decode_error")

    sub_url = _clean_subscription_url(decrypted) if decrypted.lower().startswith(("http://", "https://")) else None
    if sub_url and sub_url != decrypted:
        try:
            db.update_config_link(cfg_id, crypto.encrypt_config(sub_url))
            decrypted = sub_url
        except Exception:
            logger.exception("پاک‌سازی لینک Subscription برای cfg_id=%s ناموفق بود", cfg_id)

    # نشون بده داریم اطلاعات مصرف رو زنده می‌خونیم (ممکنه چند ثانیه طول بکشه)
    await answer_rich(callback, t("service_loading"))

    # -----------------------------------------------------------------
    # نکته‌ی مهم: اگر هر خطای غیرمنتظره‌ای (نه فقط در دریافت اطلاعات مصرف،
    # بلکه حتی در ساخت متن یا کیبورد جزئیات) رخ می‌داد، چون callback.answer بالا از
    # قبل صدا زده شده بود، هندلر سراسری خطا نمی‌توانست دوباره پیامی
    # بدهد و کاربر هیچ نتیجه‌ای نمی‌دید: پیام لیست قبلی همینطور روی صفحه می‌ماند و
    # تپ‌کردن روی سرویس بی‌اثر به نظر می‌رسید (برخلاف مینی‌اپ که این بخش را
    # در یک درخواست جدا و بدون این ریسک نشان می‌داد). به همین دلیل
    # کل این بخش الان با همون الگوی محافظتی mirror_configs احاطه شده تا کاربر همیشه
    # یک نتیجه (جزئیات سرویس یا پیام خطای واضح) ببیند.
    # -----------------------------------------------------------------
    try:

        try:
            usage = await fetch_subscription_info(decrypted)
        except Exception:
            logger.exception("خطای غیرمنتظره در fetch_subscription_info برای cfg_id=%s", cfg_id)
            usage = None

        total = used = remaining_bytes = None
        percent = 0
        bar = usage_bar(0)
        expiry = cfg.get("expiry") or "-"
        expiry_status = ""

        if usage:
            total = usage.get("total")
            used = (usage.get("upload") or 0) + (usage.get("download") or 0)
            remaining_bytes = (total - used) if total else None
            expiry = format_expire(usage.get("expire"))
            if total:
                percent = min(100, int(used / total * 100))
                bar = usage_bar(percent)
            remaining_days = days_remaining(usage.get("expire"))
            if remaining_days is not None:
                expiry_status = t("config_expired") if remaining_days <= 0 else t("config_days_left", days=remaining_days)
        elif cfg.get("expiry"):
            expiry = cfg["expiry"]
            try:
                _exp_dt = datetime.strptime(str(cfg["expiry"])[:10], "%Y-%m-%d")
                _remaining_days = (_exp_dt - now_tehran_naive()).days
                expiry_status = t("config_expired") if _remaining_days <= 0 else t("config_days_left", days=_remaining_days)
            except Exception:
                expiry_status = ""

        if cfg.get("deleted") or cfg.get("disabled") or (total is not None and used is not None and total > 0 and used >= total):
            live_status = "expired"
        else:
            live_status = await get_live_service_status(cfg)
        status_line = t("service_live_expired") if live_status == "expired" else (t("service_live_active") if live_status == "active" else "⚪ وضعیت نامشخص")
        text = t(
            "service_detail_text",
            plan=cfg.get("_display_name") or cfg.get("plan", ""),
            total=format_bytes(total) if total else "-",
            used=format_bytes(used or 0),
            remaining=format_bytes(remaining_bytes) if remaining_bytes is not None else "-",
            bar=bar,
            percent=percent,
            expiry=expiry,
            expiry_status=expiry_status,
            link=decrypted,
            purchase_date=cfg.get("created_at", "-"),
            live_status=status_line,
        )

        kb = (back_button("my_configs_vip", t("back")) if cfg.get("deleted") else config_detail_keyboard(cfg_id, sub_link_url=sub_url, has_qr=bool(cfg.get("qr_file_id")), service_id=cfg.get("service_id"), disabled=bool(cfg.get("disabled"))))
        try:
            # لینک Subscription باید دقیقاً byte-for-byte همان مقدار پنل باشد؛
            # Markdown می‌تواند underscore داخل URL را به‌عنوان entity تفسیر کند.
            await show_menu_with_sticker(
                callback.bot,
                callback.message.chat.id,
                "config_detail",
                text,
                parse_mode=None,
                entities=getattr(text, "entities", None),
                reply_markup=kb,
            )
        except Exception as e:
            # اگر مارک‌داون به هر دلیلی (مثلاً کاراکتر خاص داخل لینک ساب) شکست
            # بخورد، کاربر نباید بدون هیچ نتیجه‌ای رها شود؛ متن رو بدون فرمت دوباره
            # امتحان می‌کنیم. "message is not modified" را هم بی‌خطر نادید�� می‌گیریم
            # (یعنی محتوای جدید دقیقاً همون محتوای قبلی بود؛ کاربر همون اطلاعات رو
            # روی صفحه می‌بیند، پس نیازی به هشدار نیست).
            if "message is not modified" in str(e).lower():
                pass
            else:
                logger.exception("خطا در ویرایش پیام جزئیات سرویس برای cfg_id=%s", cfg_id)
                try:
                    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "config_detail", text, parse_mode=None, reply_markup=kb)
                except Exception:
                    logger.exception("خطا در ارسال fallback بدون فرمت برای cfg_id=%s", cfg_id)
                    await answer_rich(callback.message, 
                        t("config_detail_error")
                    )
    except Exception:
        # هر خطای غیرمنتظره‌ی دیگری (خارج از مسیرهای بالا، مثلاً در ساخت متن یا کیبورد) هم اینجا گرفته می‌شود
        # تا کاربر هرگز روی همون پیام لیست قبلی «گیر» نکند و همیشه پیام یا خطای واضحی ببیند.
        logger.exception("خطای کلی غیرمنتظره در نمایش جزئیات سرویس برای cfg_id=%s", cfg_id)
        try:
            await answer_rich(callback.message, 
                t("config_detail_error"),
                reply_markup=back_button("my_configs_vip"),
            )
        except Exception:
            logger.exception("خطا در ارسال پیام خطای fallback برای cfg_id=%s", cfg_id)


# حداکثر واقعی تلگرام برای متن یک پیام ۴۰۹۶ کاراکتر است؛ برای امنیت بیشتر
# (کدهای یونیکد چندبایتی و فاصله‌ی احتیاطی) عدد کمتری در نظر گرفته می‌شود.
_TELEGRAM_MSG_SAFE_LIMIT = 3500


async def _send_configs_safely(callback: types.CallbackQuery, configs: list[str], plan_name: str):
    """کانفیگ‌های تکی استخراج‌شده را در چند پیام (هرکدام زیر سقف امن تلگرام)
    برای کاربر ارسال می‌کند. برای هر پیام:
    ۱) هر کانفیگ تکی که به‌تنهایی طولانی‌تر از سقف امن باشد را در پیام
       جداگانه‌ی خودش می‌فرستد تا هرگز یک پیام بیش از حد مجاز تلگرام نشود.
    ۲) اگر ارسال با فرمت مارک‌داون (برای امکان تپ-کپی راحت‌تر) به هر دلیلی
       (مثلاً کاراکتر خاص داخل یک کانفیگ) با خطا مواجه شد، بدون فرمت و به‌صورت
       متن ساده دوباره ارسال می‌کند تا کاربر حتماً کانفیگ را دریافت کند.
    """

    async def _send(text: str, parse_mode: str | None):
        try:
            await answer_rich(callback.message, text, parse_mode=parse_mode)
            return True
        except Exception:
            logger.exception("خطا در ارسال پیام کانفیگ (parse_mode=%s)", parse_mode)
            return False

    async def _send_with_fallback(text_md: str, text_plain: str):
        if await _send(text_md, "Markdown"):
            return
        # اگر مارک‌داون شکست خورد، همون متن رو بدون فرمت دوباره امتحان کن
        if not await _send(text_plain, None):
            await answer_rich(callback.message, 
                t("config_delivery_error")
            )

    header = f"📥 {len(configs)} کانفیگ از سرویس {plan_name} پیدا شد:\n\n"
    chunk_md = header
    chunk_plain = header

    for conf in configs:
        # اگر یک کانفیگ به‌تنهایی از سقف امن بزرگ‌تر باشد (مثلاً کانفیگ‌های
        # reality/hysteria2 با پارامترهای زیاد)، نمی‌توان آن را با بقیه در یک
        # پیام جا داد؛ باید تنها و مستقیماً ارسال شود.
        conf_md_line = f"`{conf}`\n\n"
        if len(conf_md_line) > _TELEGRAM_MSG_SAFE_LIMIT:
            if chunk_md != header:
                await _send_with_fallback(chunk_md, chunk_plain)
                chunk_md, chunk_plain = header, header
            await _send_with_fallback(f"`{conf}`", conf)
            continue

        if len(chunk_md) + len(conf_md_line) > _TELEGRAM_MSG_SAFE_LIMIT:
            await _send_with_fallback(chunk_md, chunk_plain)
            chunk_md, chunk_plain = header, header

        chunk_md += conf_md_line
        chunk_plain += f"{conf}\n\n"

    if chunk_md != header:
        await _send_with_fallback(chunk_md, chunk_plain)


@router.callback_query(F.data.startswith("mirrorconfigs_"))
async def mirror_configs(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("mirrorconfigs_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return

    try:
        decrypted = crypto.decrypt_config(cfg["config"])
    except Exception:
        await answer_rich(callback, t("config_decode_error"), show_alert=True)
        return

    if not decrypted or not decrypted.lower().startswith(("http://", "https://")):
        await answer_rich(callback, t("subscription_missing"), show_alert=True)
        return

    await answer_rich(callback, t("subscription_fetching"))

    # -----------------------------------------------------------------
    # نکته‌ی مهم: قبلاً اگر یک خطای غیرمنتظره (مثلاً پیام خیلی طولانی برای
    # تلگرام یا کاراکتر خاصی که پارس مارک‌داون را خراب می‌کرد) در این بخش رخ
    # می‌داد، چون callback.answer بالا از قبل صدا زده شده بود، هندلر سراسری
    # خطا (bot.py) نمی‌توانست دوباره callback را answer کند و کاربر هیچ
    # پیام/خطایی نمی‌دید؛ دکمه فقط "در حال دریافت..." نشان می‌داد و بعد هیچ
    # اتفاقی نمی‌افتاد. حالا کل این بخش try/except دارد تا در هر حالتی
    # کاربر حتماً یک نتیجه (موفق یا پیام خطای واضح) ببیند.
    # -----------------------------------------------------------------
    try:
        configs = await extract_configs(decrypted)
    except Exception:
        logger.exception("خطای غیرمنتظره در extract_configs برای cfg_id=%s", cfg_id)
        configs = None

    if configs is None:
        await answer_rich(callback.message, t("subscription_unavailable"))
        return
    if not configs:
        await answer_rich(callback.message, 
            "⚠️ لینک ساب باز شد ولی هیچ کانفیگ تکی‌ای داخلش پیدا نشد.\n"
            "برای استفاده، همون لینک ساب رو مستقیم داخل اپ V2Ray/Clash وارد کنید."
        )
        return

    await _send_configs_safely(callback, configs, cfg["plan"])


@router.callback_query(F.data.startswith("viewqr_"))
async def view_config_qr(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("viewqr_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return
    if not cfg.get("qr_file_id"):
        await answer_rich(callback, t("qr_missing"), show_alert=True)
        return

    await callback.answer()
    try:
        await send_photo_rich(callback.bot, callback.from_user.id, cfg["qr_file_id"], caption=f"🖼 کیوآرکد {cfg['plan']}")
    except Exception:
        await answer_rich(callback, t("qr_failed"), show_alert=True)


@router.callback_query(F.data.startswith("delconfig_"))
async def delete_config_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("delconfig_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return

    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "config_delete_confirm", 
        t("service_delete_confirm", plan=cfg["plan"]),
        reply_markup=confirm_delete_config_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delconfirm_"))
async def delete_config_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return

    try:
        cfg_id = int(callback.data.replace("delconfirm_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return

    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"]:
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return

    db.set_config_deleted(cfg_id, True)
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, None, 
        "✅ سرویس حذف شد.",
        reply_markup=back_button("my_configs_vip"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgdisable_"))
async def user_service_disable_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgdisable_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await answer_rich(callback, t("service_disable_not_available"), show_alert=True)
        return
    await answer_rich(callback.message, 
        t("service_disable_confirm"),
        reply_markup=confirm_disable_service_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgdisabledo_"))
async def user_service_disable_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgdisabledo_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return
    await answer_rich(callback, t("config_disabling"))
    ok, data, msg = await vpn_panel.disable_user(cfg["service_id"])
    if not ok:
        await answer_rich(callback.message, t("service_disable_failed", msg=msg))
        return
    db.set_config_disabled(cfg_id, True)
    await answer_rich(callback.message, 
        t("service_disabled"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )


@router.callback_query(F.data.startswith("cfgenable_"))
async def user_service_enable_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgenable_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return
    await answer_rich(callback, t("config_enabling"))
    ok, data, msg = await vpn_panel.enable_user(cfg["service_id"])
    if not ok:
        await answer_rich(callback.message, t("service_enable_failed", msg=msg))
        return
    db.set_config_disabled(cfg_id, False)
    await answer_rich(callback.message, 
        t("service_enabled"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )


@router.callback_query(F.data.startswith("cfgrevokesub_"))
async def user_service_revoke_sub_confirm(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgrevokesub_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await answer_rich(callback, t("service_revoke_not_available"), show_alert=True)
        return
    await answer_rich(callback.message, 
        t("service_revoke_confirm"),
        reply_markup=confirm_revoke_sub_keyboard(cfg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfgrevokesubdo_"))
async def user_service_revoke_sub_apply(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    if user is None:
        await answer_rich(callback, t("common_start_required"), show_alert=True)
        return
    try:
        cfg_id = int(callback.data.replace("cfgrevokesubdo_", ""))
    except ValueError:
        await answer_rich(callback, t("service_not_found"), show_alert=True)
        return
    cfg = db.get_config_by_id(cfg_id)
    if cfg is None or cfg["user_id"] != user["id"] or cfg.get("deleted") or not cfg.get("service_id"):
        await answer_rich(callback, t("service_not_owned"), show_alert=True)
        return
    await answer_rich(callback, t("config_revoking"))
    ok, data, msg = await vpn_panel.revoke_sub(cfg["service_id"])
    if not ok:
        await answer_rich(callback.message, t("service_revoke_failed", msg=msg))
        return
    link, _slug = vpn_panel.extract_link_and_username(data)
    if not link:
        await answer_rich(callback.message, t("service_revoke_missing"))
        return
    db.update_config_link(cfg_id, crypto.encrypt_config(link))
    await answer_rich(callback.message, 
        t("service_revoke_done"),
        reply_markup=back_button(f"viewconfig_{cfg_id}", t("config_back_service")),
    )




# ---------------------------------------------------------------------------
# 🛠 سرویس خودت رو بساز (و همچنین 🔁 تمدید سرویس از همین مسیر مشترک رد می‌شود)
# ---------------------------------------------------------------------------
_LATIN_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")






















