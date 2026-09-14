"""
keyboards.py
تمام کیبوردهای Inline و Reply ربات. هیچ handlerای نباید خودش InlineKeyboardMarkup
بسازد؛ همه از این فایل صدا زده می‌شوند تا تغییر ظاهر منو در یک‌جا متمرکز باشد.
"""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton as _RealInlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton as _RealKeyboardButton,
    CopyTextButton,
)

import database as db
from text_catalog import text as t, TEXTS
import bot_info
import vpn_panel
import panels
_PANEL_MODULE = panels
from config import UNIQUEPAY_ENABLED, MARZBAN_ENABLED, PASARGAD_ENABLED, ONLINE_PAYMENT_MIN_AMOUNT


def _button_premium_emoji_kwargs(kwargs):
    """در صورت وجود Premium Emoji ذخیره‌شده برای متن دکمه، آن را به icon رسمی تلگرام وصل می‌کند.

    🐛 تاریخچه‌ی این تابع (برای این‌که دوباره از اول باگ نسازیم):
    ۱) اول متنِ دکمه هنگام ساخت icon حذف می‌شد → با فشردن دکمه متنِ ناقص
       برمی‌گشت و هیچ فیلتری match نمی‌شد.
    ۲) بعد از این‌که متن را دست‌نخورده نگه داشتیم، مشخص شد خودِ تلگرام وقتی
       هم `text` (که با همان ایموجی شروع می‌شود) و هم `icon_custom_emoji_id`
       روی یک دکمه ست باشند، همان کاراکتر ابتدای متن را خودش حذف می‌کند تا
       ایموجی دوبار نمایش داده نشود؛ و باز هم متنِ برگشتی با متنِ ذخیره‌شده
       مطابقت نداشت.
    برای همین icon_custom_emoji_id کلاً حذف شده بود — ولی نتیجه‌اش این شد که
    ایموجی‌های پرمیوم/انیمیشنی روی هیچ دکمه‌ای نمایش داده نمی‌شدند (حتی روی
    دکمه‌های Inline که اصلاً این مشکل را نداشتند، چون فشردن دکمه‌ی Inline
    همیشه با callback_data تشخیص داده می‌شود، نه با متن).
    راه‌حل نهایی: icon_custom_emoji_id دوباره فعال است (برای هم Inline و هم
    Reply Keyboard)، اما حالا `_MenuButtonText` در handlers/menu.py یک لایه‌ی
    محافظتی دارد که پیشوند ایموجیِ حذف‌شده توسط تلگرام را نادیده می‌گیرد؛
    یعنی حتی اگر تلگرام دوباره همان کاراکتر را از متنِ دکمه‌های Reply Keyboard
    حذف کند، دکمه باز هم درست تشخیص داده می‌شود. دکمه‌های Inline از اول هم
    ریسکی نداشتند چون تشخیصشان از طریق callback_data است.
    """
    # حتی اگر caller خودش icon_custom_emoji_id را داده باشد (مثل راهنماها)،
    # fallback emoji ابتدای text نباید کنار آیکن Premium باقی بماند. نسخه‌ی قبلی
    # در این حالت زود return می‌کرد و دقیقاً باعث نمایش هم‌زمان دو ایموجی می‌شد.
    explicit_icon = kwargs.get("icon_custom_emoji_id")
    text = kwargs.get("text")
    if explicit_icon:
        if text:
            kwargs["text"] = _strip_leading_emoji_cluster(str(text))
        return kwargs

    if text:
        try:
            display_text, emoji_id = db.get_button_premium_emoji_for_text(str(text))
            if emoji_id:
                kwargs["text"] = _strip_leading_emoji_cluster(display_text)
                kwargs["icon_custom_emoji_id"] = emoji_id
        except Exception:
            # Premium Emoji نباید باعث خراب‌شدن هیچ دکمه‌ای شود.
            pass
    return kwargs


def _strip_leading_emoji_cluster(value: str) -> str:
    """حذف فقط ایموجی/نماد ابتدای متن برای جایگزینی با Premium Emoji.

    این تابع عمداً فقط ابتدای متن را لمس می‌کند تا ایموجی‌های داخل عنوان،
    اعداد، علائم فارسی و متن دکمه تغییر نکنند. ZWJ/VS16 و modifierهای بعد از
    ایموجی هم همراه همان خوشه حذف می‌شوند.
    """
    import unicodedata

    if not value:
        return value

    def is_emojiish(ch: str) -> bool:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        # همه‌ی بلوک‌های رایج Emoji/Symbol + کاراکترهایی که تلگرام
        # معمولاً به‌عنوان fallback یک Custom/Premium Emoji استفاده می‌کند.
        # هدف این تابع فقط حذف fallback ابتدای متن است؛ متن فارسی/لاتین
        # و اعداد دست‌نخورده می‌مانند.
        return (
            0x1F000 <= cp <= 0x1FAFF
            or 0x1FC00 <= cp <= 0x1FFFF
            or 0x2300 <= cp <= 0x23FF
            or 0x2600 <= cp <= 0x27BF
            or 0x2B00 <= cp <= 0x2BFF
            or 0x2E80 <= cp <= 0x2EFF
            or 0x3000 <= cp <= 0x303F
            or 0xFE0E <= cp <= 0xFE0F
            or cat in {"So", "Sk"}
        )

    i = 0
    n = len(value)
    started = False

    # Keycap emoji مثل 1️⃣ / #️⃣ / *️⃣ با کاراکتر ASCII شروع می‌شوند؛
    # اگر Premium Emoji روی چنین دکمه‌ای فعال باشد، خود کاراکتر آزاد نباید
    # کنار آیکن Premium باقی بماند.
    if n >= 3 and value[0] in "0123456789#*" and value[1] in ("\ufe0e", "\ufe0f") and value[2] == "\u20e3":
        i = 3
        started = True

    while i < n:
        ch = value[i]
        cp = ord(ch)
        if is_emojiish(ch):
            started = True
            i += 1
            continue
        if started and cp in (0xFE0E, 0xFE0F, 0x200D, 0x20E3):
            i += 1
            continue
        if started and 0x1F3FB <= cp <= 0x1F3FF:
            i += 1
            continue
        break
    if not started:
        return value
    # اگر بعد از ایموجی یک فاصله آمده، همان فاصله هم حذف شود.
    return value[i:].lstrip()


def InlineKeyboardButton(*args, **kwargs):
    """
    Wrapper مرکزی دکمه‌های Inline.
    Premium/Custom Emoji فقط ظاهر دکمه است و نباید هیچ‌وقت callback_data
    یا ساخت خود دکمه را خراب کند. اگر Telegram/aiogram آیکن سفارشی را
    نپذیرفت، همان دکمه بدون آیکن سفارشی ساخته می‌شود.
    """
    if kwargs.get("callback_data") is not None:
        kwargs["callback_data"] = _safe_callback_data(kwargs["callback_data"])

    kwargs = _button_premium_emoji_kwargs(kwargs)

    try:
        return _RealInlineKeyboardButton(*args, **kwargs)
    except Exception:
        # ظاهر دکمه نباید باعث از کار افتادن callback شود.
        kwargs.pop("icon_custom_emoji_id", None)
        return _RealInlineKeyboardButton(*args, **kwargs)


def KeyboardButton(*args, **kwargs):
    """Wrapper مرکزی Reply Keyboard؛ Premium Emoji نباید رفتار دکمه را خراب کند."""
    kwargs = _button_premium_emoji_kwargs(kwargs)
    try:
        return _RealKeyboardButton(*args, **kwargs)
    except Exception:
        kwargs.pop("icon_custom_emoji_id", None)
        return _RealKeyboardButton(*args, **kwargs)



# fix: callback_data محدودیت 64 بایت دارد (محدودیت Telegram Bot API).
# نام دسته/پلن توسط ادمین قابل‌ساخت است و ممکن است طولانی باشد،
# به همین دلیل هر callback_data قبل استفاده از این تابع رد می‌شود.
def _safe_callback_data(data: str) -> str:
    """حفظ دقیق callback_data.

    callback_data نباید truncate شود؛ چون کوتاه‌کردن رشته‌های ساختاریافته
    (مثل plan/order/user IDs) آن را با الگوی handlerها ناسازگار می‌کند و
    نتیجه‌اش دکمه‌ای است که ظاهراً ارسال شده ولی هیچ handlerای آن را match نمی‌کند.
    طول callback_data باید در محل طراحی callback کنترل شود، نه با بریدن
    کورکورانه‌ی مقدار.
    """
    return str(data)


# fix: به‌جای ویرایش تک‌تک ۱۵۰+ محلی که InlineKeyboardButton ساخته می‌شود،
# یک Wrapper مرکزی می‌سازیم تا callback_data همه‌ی دکمه‌ها همیشه از این تابع
# رد شود و هیچ دکمه‌ای هرگز به‌خاطر طول callback_data توسط تلگرام رد نشود.



# ---------------------------------------------------------------------------
# عضویت اجباری
# ---------------------------------------------------------------------------
def join_channels_keyboard(channels):
    rows = [
        [InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch["url"], style="primary")]
        for ch in channels
    ]
    rows.append([InlineKeyboardButton(text=t("join_confirm"), callback_data="check_join", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# منوی پایین صفحه (Reply Keyboard) — همیشه در دسترس کاربر
# ---------------------------------------------------------------------------
def main_reply_keyboard(user_id=None):
    return ReplyKeyboardMarkup(
        keyboard=[
            # ردیف ۱: تست / خرید اشتراک — سبز
            [
                KeyboardButton(text=db.get_text_override("main_free_test", "تست"), style="success"),
                KeyboardButton(text=db.get_text_override("main_buy", "خرید اشتراک"), style="success"),
            ],
            # ردیف ۲: کیف پول / تمدید — سبز
            [
                KeyboardButton(text=db.get_text_override("main_wallet", "کیف پول"), style="success"),
                KeyboardButton(text=db.get_text_override("main_renew", "تمدید"), style="success"),
            ],
            # ردیف ۳: پروفایل / سرویس‌های من — آبی
            [
                KeyboardButton(text=db.get_text_override("main_profile", "پروفایل"), style="primary"),
                KeyboardButton(text=db.get_text_override("main_configs", "سرویس‌های من"), style="primary"),
            ],
            # ردیف ۴: راهنما / پشتیبانی — آبی
            [
                KeyboardButton(text=db.get_text_override("main_guides", "راهنما"), style="primary"),
                KeyboardButton(text=db.get_text_override("main_support", "پشتیبانی"), style="primary"),
            ],
            # ردیف ۵: نمایندگی / دعوت دوستان — قرمز
            [
                KeyboardButton(text=db.get_text_override("main_agency", "نمایندگی"), style="danger"),
                KeyboardButton(text=db.get_text_override("main_referral", "دعوت دوستان"), style="danger"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=False,
    )


def admin_reply_keyboard(orders_enabled: bool | None = None, permissions: set[str] | None = None, is_main_admin: bool = True):
    """Reply Keyboard ادمین؛ همان ورودی واحد مدیریت پنل‌های VPN را نشان می‌دهد."""
    def allowed(perm: str) -> bool:
        return is_main_admin or permissions is None or perm in permissions
    if orders_enabled is None:
        try: orders_enabled = db.is_orders_enabled()
        except Exception: orders_enabled = True
    rows=[]
    def pair(a_perm,a_text,b_perm=None,b_text=None):
        row=[]
        if allowed(a_perm): row.append(KeyboardButton(text=a_text, style="primary"))
        if b_text is not None and allowed(b_perm): row.append(KeyboardButton(text=b_text, style="primary"))
        if row: rows.append(row)
    pair("stats","📊 آمار","requests","📥 صف درخواست‌ها")
    pair("tickets", t("admin_tickets"))
    pair("users","👥 لیست کاربران","users","🔍 جستجوی کاربر")
    pair("users","🔎 جستجوی کانفیگ")
    pair("broadcast","📢 پیام همگانی","discounts","🎟 مدیریت تخفیف")
    pair("agency","🤝 نمایندگی (تخفیف VIP)","plans","🗂 دسته‌بندی‌های VIP")
    pair("plans","🛒 خرید اشتراک برای خودم","vpn_panel","🖥 مدیریت پنل‌های VPN")
    pair("referrals","🤝 مدیریت دعوت‌ها","guides","📚 مدیریت راهنما")
    pair("logs","🦖 لاگ خطاها","botinfo","ℹ️ اطلاعات ربات")
    pair("stickers","🎬 استیکرهای منو","backup","💾 بکاپ")
    pair("texts","📝 مدیریت متن‌های کاربر","settings","🎁 تنظیم تست رایگان")
    if allowed("settings"):
        rows.append([KeyboardButton(text="🔁 تنظیمات تمدید", style="success")])
    if allowed("orders_toggle"):
        rows.append([KeyboardButton(text=("🔴 خاموش کردن سفارشات" if orders_enabled else "🟢 روشن کردن سفارشات"), style=("danger" if orders_enabled else "success"))])
    if is_main_admin: rows.append([KeyboardButton(text="👮 مدیریت ادمین‌ها", style="danger")])
    if not rows: rows=[[KeyboardButton(text="⛔ بدون دسترسی", style="danger")]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=False, one_time_keyboard=False)


# ---------------------------------------------------------------------------
# 🆕 فیکس گیرکردن FSM: تمام متن‌های ممکنِ دکمه‌های ثابت منوی پایین صفحه (کاربر
# عادی + ادمین اصلی/فرعی، در همه‌ی حالت‌های ممکنِ سوییچ سفارشات/سطح دسترسی) را
# برمی‌گرداند. bot.py از این مجموعه استفاده می‌کند تا تشخیص دهد یک پیام متنی
# واقعاً فشردن یکی از دکمه‌های ثابت منو بوده؛ در آن صورت هر state ناتمام
# (مثلاً «منتظر عکس کیوآرکد» یا «منتظر رسید شارژ کیف پول») پاک می‌شود تا آن
# دکمه بلافاصله توسط handler خودش پردازش شود، نه با تکرار سوال قبلی FSM.
# ---------------------------------------------------------------------------
def all_reply_menu_texts() -> set[str]:
    texts: set[str] = set()

    def collect(markup: ReplyKeyboardMarkup) -> None:
        for row in markup.keyboard:
            for btn in row:
                if getattr(btn, "text", None):
                    texts.add(btn.text)

    collect(main_reply_keyboard())
    collect(admin_reply_keyboard(orders_enabled=True, permissions=None, is_main_admin=True))
    collect(admin_reply_keyboard(orders_enabled=False, permissions=None, is_main_admin=True))
    collect(admin_reply_keyboard(orders_enabled=True, permissions=set(), is_main_admin=False))
    return texts



# ---------------------------------------------------------------------------
# منوی اصلی (Inline) — کاربر عادی
# ---------------------------------------------------------------------------
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("main_buy"), callback_data="plans", style="success")],
        [InlineKeyboardButton(text=t("main_free_test"), callback_data="buy_plan_test", style="success")],
        [InlineKeyboardButton(text=t("main_configs"), callback_data="my_configs", style="primary")],
        [InlineKeyboardButton(text=t("main_wallet"), callback_data="wallet", style="primary")],
        [InlineKeyboardButton(text=t("main_referral"), callback_data="referral", style="primary")],
        [InlineKeyboardButton(text=t("main_profile"), callback_data="profile", style="primary")],
        [InlineKeyboardButton(text=t("main_support"), callback_data="support", style="primary")],
        [InlineKeyboardButton(text=t("main_guides"), callback_data="user_guides", style="primary")],
       
    ])


def back_button(callback_data: str = "back", text: str | None = None):
    if text is None:
        text = t("main_back")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data, style="danger")]])


def profile_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("profile_free_wallet"), callback_data="wallet_free", style="primary")],
        [InlineKeyboardButton(text=t("profile_locked_wallet"), callback_data="wallet_locked", style="danger")],
        [InlineKeyboardButton(text=t("profile_history"), callback_data="purchase_history", style="primary")],
        [InlineKeyboardButton(text=t("profile_transactions"), callback_data="transactions", style="primary")],
        [InlineKeyboardButton(text=t("profile_referral"), callback_data="referral", style="success")],
        [InlineKeyboardButton(text=t("profile_back"), callback_data="back", style="danger")],
    ])


def wallet_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("wallet_charge"), callback_data="charge", style="success")],
        [InlineKeyboardButton(text=t("wallet_discount"), callback_data="use_discount", style="success")],
        [InlineKeyboardButton(text=t("wallet_transactions"), callback_data="transactions", style="primary")],
        [InlineKeyboardButton(text=t("wallet_back"), callback_data="back", style="danger")],
    ])


def charge_amount_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("charge_50000"), callback_data="charge_50000", style="primary")],
        [InlineKeyboardButton(text=t("charge_100000"), callback_data="charge_100000", style="primary")],
        [InlineKeyboardButton(text=t("charge_200000"), callback_data="charge_200000", style="primary")],
        [InlineKeyboardButton(text=t("charge_custom"), callback_data="charge_custom", style="primary")],
        [InlineKeyboardButton(text=t("back"), callback_data="wallet", style="danger")],
    ])


def charge_payment_method_keyboard(amount: int):
    """انتخاب روش پرداخت برای شارژ کیف پول. دکمه‌ی «پرداخت آنلاین» فقط وقتی
    نمایش داده می‌شود که درگاه فعال باشد و مبلغ بیشتر از
    ONLINE_PAYMENT_MIN_AMOUNT باشد (برای مبالغ مساوی یا کمتر، درگاه آنلاین
    اصلاً پیشنهاد نمی‌شود و فقط کارت‌به‌کارت در دسترس است)."""
    buttons = []
    if UNIQUEPAY_ENABLED and amount > ONLINE_PAYMENT_MIN_AMOUNT:
        buttons.append(
            [InlineKeyboardButton(text=t("wallet_pay_online"), callback_data=f"chargepay_online_{amount}", style="success")]
        )
    buttons.append([InlineKeyboardButton(text=t("wallet_pay_card"), callback_data=f"chargepay_card_{amount}", style="success")])
    buttons.append([InlineKeyboardButton(text=t("back"), callback_data="charge", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def online_payment_wallet_keyboard(payment_link: str, online_payment_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("wallet_online_pay"), url=payment_link, style="success")],
        [InlineKeyboardButton(text=t("wallet_check_pay"), callback_data=f"checkpay_{online_payment_id}", style="success")],
        [InlineKeyboardButton(text=t("wallet_cancel"), callback_data="wallet", style="danger")],
    ])


def referral_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("referral_back"), callback_data="back", style="danger")],
    ])


def support_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("support_ticket"), callback_data="ticket", style="primary")],
        [InlineKeyboardButton(text=t("support_channels"), url=bot_info.get_support_url(), style="primary")],
        [InlineKeyboardButton(text=t("support_back"), callback_data="back", style="danger")],
    ])


# ---------------------------------------------------------------------------
# سرویس‌ها / خرید اشتراک
# ---------------------------------------------------------------------------
def plans_menu():
    """🧹 دیگر مستقیماً استفاده نمی‌شود: دکمهٔ «🛒 خرید اشتراک» مستقیماً دسته‌بندی‌های VIP را باز می‌کند (vip_categories_keyboard)."""
    return vip_categories_keyboard()






def _plans_keyboard(plans_dict: dict, icon: str, discount_percent: int = 0):
    buttons = []
    for key, plan in plans_dict.items():
        price = plan["price"]
        if discount_percent:
            price = int(price * (1 - discount_percent / 100))
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {plan['name']} — {price:,} تومان",
            callback_data=f"buy_{key}"
        , style="success")])
    buttons.append([InlineKeyboardButton(text=t("plans_back"), callback_data="plans", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vip_categories_keyboard():
    """مرحله‌ی اول خرید VIP: لیست دسته‌بندی‌ها (بعداً از پنل ادمین می‌توان دسته‌ی
    جدید اضافه کرد؛ همه‌شان اینجا خودکار ظاهر می‌شوند)."""
    buttons = []
    for cat in db.get_vip_categories():
        buttons.append([InlineKeyboardButton(text=f"{cat['name']}", callback_data=f"vipcat_{cat['key']}", style="primary")])
    if not buttons:
        buttons.append([InlineKeyboardButton(text=t("vip_category_empty"), callback_data="noop", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("plans_back"), callback_data="back", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vip_category_plans_keyboard(category_key: str, discount_percent: int = 0):
    """مرحله‌ی دوم: پلن‌های داخل یک دسته‌ی VIP خاص."""
    cat = db.get_vip_category(category_key)
    plans = db.get_vip_plans(cat["id"]) if cat else []
    buttons = []
    for plan in plans:
        price = plan["price"]
        if discount_percent:
            price = int(price * (1 - discount_percent / 100))
        buttons.append([InlineKeyboardButton(
            text=f"{plan['name']} — {price:,} تومان", callback_data=f"buy_{plan['plan_key']}"
        , style="primary")])
    if not buttons:
        buttons.append([InlineKeyboardButton(text=t("vip_plans_empty"), callback_data="noop", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("vip_plans_back"), callback_data="plans_vip", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)






def all_plans_discount_keyboard(discount_percent: int):
    return _plans_keyboard(db.get_all_plans(), "📅", discount_percent)


def purchase_payment_keyboard(plan_key: str, show_discount: bool = True):
    buttons = [
        [InlineKeyboardButton(text=t("pay_wallet"), callback_data=f"pay_wallet_{plan_key}", style="success")],
    ]
    if UNIQUEPAY_ENABLED:
        buttons.append(
            [InlineKeyboardButton(text=t("pay_online"), callback_data=f"pay_online_{plan_key}", style="success")]
        )
    buttons.append([InlineKeyboardButton(text=t("pay_card"), callback_data=f"pay_card_{plan_key}", style="success")])
    buttons.append([InlineKeyboardButton(text=t("pay_crypto"), callback_data=f"pay_crypto_{plan_key}", style="success")])
    if show_discount:
        buttons.append([InlineKeyboardButton(text=t("pay_discount"), callback_data=f"discount_plan_{plan_key}", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("pay_back"), callback_data="back", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def online_payment_keyboard(payment_link: str, online_payment_id: int, cancel_callback: str = "plans"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("online_pay"), url=payment_link, style="primary")],
        [InlineKeyboardButton(text=t("online_check"), callback_data=f"checkpay_{online_payment_id}", style="success")],
        [InlineKeyboardButton(text=t("online_cancel"), callback_data=cancel_callback, style="danger")],
    ])


def insufficient_balance_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("insufficient_charge"), callback_data="wallet", style="primary")],
        [InlineKeyboardButton(text=t("insufficient_back"), callback_data="plans", style="danger")],
    ])


# ---------------------------------------------------------------------------
# سرویس‌های من
# ---------------------------------------------------------------------------
def my_configs_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("configs_vip"), callback_data="my_configs_vip", style="primary")],
        [InlineKeyboardButton(text=t("configs_back"), callback_data="back", style="danger")],
    ])


def my_configs_list_keyboard(configs, icon: str, back_callback: str):
    buttons = []
    for cfg in configs:
        # در لیست «سرویس‌های من» فقط username واقعی سرویس نمایش داده شود؛
        # وضعیت/ایموجی عمداً از متن دکمه حذف شده است.
        display_name = str(cfg.get("service_id") or cfg.get("_display_name") or cfg.get("plan") or "سرویس").strip()
        buttons.append([InlineKeyboardButton(
            text=display_name, callback_data=f"viewconfig_{cfg['id']}", style="primary"
        )])
    buttons.append([InlineKeyboardButton(text=t("service_search"), callback_data="search_my_configs", style="success")])
    buttons.append([InlineKeyboardButton(text=t("back"), callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def renew_services_keyboard(configs):
    buttons=[]
    for cfg in configs:
        name=str(cfg.get("service_id") or cfg.get("_display_name") or cfg.get("plan") or "سرویس").strip()
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"renewcfg_{cfg['id']}", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("renew_cancel"), callback_data="renew_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _renew_choice_values(minimum: int, maximum: int, defaults: list[int]) -> list[int]:
    """فقط همان گزینه‌هایی را که ادمین برای دسته ذخیره کرده نمایش بده.
    حداقل/حداکثر فقط اعتبارسنجی هستند و نباید یک دکمه‌ی ناخواسته مثل «۱ گیگ»
    یا «۱ روز» به لیست اضافه کنند.
    """
    minimum = max(1, int(minimum or 1))
    maximum = int(maximum or 0)
    values = []
    for raw in defaults:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v < minimum:
            continue
        if maximum and v > maximum:
            continue
        if v not in values:
            values.append(v)
    return values

def renew_volume_keyboard(settings: dict | None = None):
    st = settings or {}
    raw_options = st.get("gb_options") or "10,20,50"
    try: requested = [int(float(x.strip())) for x in str(raw_options).replace("،", ",").split(",") if x.strip()]
    except Exception: requested = [10, 20, 50]
    values = _renew_choice_values(st.get("min_gb", 1), st.get("max_gb", 0), requested)
    buttons = []
    for i in range(0, len(values), 2):
        row = []
        for v in values[i:i+2]:
            key = f"renew_volume_{int(v)}"
            label = f"{int(v)} گیگ"
            row.append(InlineKeyboardButton(text=label, callback_data=f"renewvol_{v}", style="primary"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text=t("renew_volume_custom"), callback_data="renewvol_custom", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("renew_cancel"), callback_data="renew_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def renew_days_keyboard(settings: dict | None = None):
    st = settings or {}
    raw_options = st.get("day_options") or "30,60,90"
    try: requested = [int(float(x.strip())) for x in str(raw_options).replace("،", ",").split(",") if x.strip()]
    except Exception: requested = [30, 60, 90]
    values = _renew_choice_values(st.get("min_day", 1), st.get("max_day", 0), requested)
    buttons = []
    for i in range(0, len(values), 2):
        row = []
        for v in values[i:i+2]:
            key = f"renew_days_{int(v)}"
            label = f"{int(v)} روز"
            row.append(InlineKeyboardButton(text=label, callback_data=f"renewdays_{v}", style="primary"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text=t("renew_days_custom"), callback_data="renewdays_custom", style="primary")])
    buttons.append([InlineKeyboardButton(text=t("renew_cancel"), callback_data="renew_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def renew_payment_keyboard():
    buttons = [
        [InlineKeyboardButton(text=t("pay_wallet"), callback_data="renewpay_wallet", style="success")],
    ]
    if UNIQUEPAY_ENABLED:
        buttons.append([InlineKeyboardButton(text=t("pay_online"), callback_data="renewpay_online", style="success")])
    buttons.append([InlineKeyboardButton(text=t("pay_card"), callback_data="renewpay_card", style="success")])
    buttons.append([InlineKeyboardButton(text=t("pay_crypto"), callback_data="renewpay_crypto", style="success")])
    buttons.append([InlineKeyboardButton(text=t("pay_back"), callback_data="renew_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def service_alert_80_90_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("notif_renew_service_button"), callback_data=f"renewcfg_{cfg_id}", style="success")],
    ])

def service_expired_alert_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("notif_renew_service_button"), callback_data=f"renewcfg_{cfg_id}", style="success")],
        [InlineKeyboardButton(text=t("notif_buy_new_service_button"), callback_data="plans", style="primary")],
    ])

def fair_use_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("fair_use_continue"), callback_data=f"fairuse_yes_{cfg_id}"), InlineKeyboardButton(text=t("fair_use_buy_new"), callback_data="plans", style="success")],
    ])

def crypto_payment_keyboard(asset: str, wallet: str, amount: str):
    # خود آدرس کیف پول مثل شماره کارت قابل لمس/کپی است؛ هیچ دکمه‌ی دیگری
    # برای «ارسال رسید» یا «بررسی پرداخت» زیر فاکتور قرار نمی‌گیرد.
    wallet_text = str(wallet or "").strip()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=wallet_text, copy_text=CopyTextButton(text=wallet_text), style="primary")],
    ])

def config_detail_keyboard(cfg_id, sub_link_url: str | None = None, has_qr: bool = False, back_callback: str = "my_configs_vip", service_id: str | None = None, disabled: bool = False):
    """کیبورد جزئیات سرویس VIP: کیوآرکد + لینک ساب + مدیریت سرویس + حذف سرویس."""
    buttons = [
    ]
    row = []
    if has_qr:
        row.append(InlineKeyboardButton(text=t("config_qr"), callback_data=f"viewqr_{cfg_id}", style="primary"))
    if sub_link_url:
        row.append(InlineKeyboardButton(text=t("config_sub"), url=sub_link_url, style="primary"))
    if row:
        buttons.append(row)
    if sub_link_url:
        buttons.append([InlineKeyboardButton(text=t("config_mirror"), callback_data=f"mirrorconfigs_{cfg_id}", style="success")])
    if service_id:
        if disabled:
            buttons.append([InlineKeyboardButton(text=t("config_enable"), callback_data=f"cfgenable_{cfg_id}", style="success")])
        else:
            buttons.append([InlineKeyboardButton(text=t("config_disable"), callback_data=f"cfgdisable_{cfg_id}", style="danger")])
        buttons.append([InlineKeyboardButton(text=t("config_revoke"), callback_data=f"cfgrevokesub_{cfg_id}", style="danger")])
    buttons.append([InlineKeyboardButton(text=t("config_delete"), callback_data=f"delconfig_{cfg_id}", style="danger")])
    buttons.append([InlineKeyboardButton(text=t("config_back"), callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)




def confirm_delete_config_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("confirm_delete_yes"), callback_data=f"delconfirm_{cfg_id}", style="danger")],
        [InlineKeyboardButton(text=t("confirm_delete_no"), callback_data=f"viewconfig_{cfg_id}", style="danger")],
    ])


def confirm_disable_service_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("confirm_disable_yes"), callback_data=f"cfgdisabledo_{cfg_id}", style="danger")],
        [InlineKeyboardButton(text=t("confirm_disable_no"), callback_data=f"viewconfig_{cfg_id}", style="danger")],
    ])


def confirm_revoke_sub_keyboard(cfg_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("confirm_revoke_yes"), callback_data=f"cfgrevokesubdo_{cfg_id}", style="danger")],
        [InlineKeyboardButton(text=t("confirm_revoke_no"), callback_data=f"viewconfig_{cfg_id}", style="danger")],
    ])




# ---------------------------------------------------------------------------
# پنل ادمین
# ---------------------------------------------------------------------------
def admin_panel_menu(orders_enabled: bool = True, permissions: set[str] | None = None, is_main_admin: bool = True):
    """منوی Inline بالایی ادمین؛ مدیریت تمام پنل‌های VPN فقط از یک ورودی واحد."""
    def allowed(perm: str) -> bool:
        return is_main_admin or permissions is None or perm in permissions

    items = [
        ("stats", "📊 آمار", "admin_stats", "primary"),
        ("requests", "📥 صف درخواست‌ها", "admin_request_queue", "success"),
        ("tickets", t("admin_tickets"), "admin_tickets", "primary"),
        ("users", "👥 کاربران بدون خرید", "admin_userlist", "primary"),
        ("users", "🔍 جستجوی حرفه‌ای", "admin_search", "primary"),
        ("users", "🔎 جستجوی کانفیگ", "admin_config_search", "primary"),
        ("broadcast", "📢 پیام همگانی", "admin_broadcast", "primary"),
        ("discounts", "🎟 مدیریت تخفیف", "admin_discount", "primary"),
        ("agency", "🤝 نمایندگی (تخفیف VIP)", "admin_agency", "primary"),
        ("plans", "🗂 دسته‌بندی‌های VIP", "admin_vip_categories", "primary"),
        ("plans", "🛒 خرید اشتراک برای خودم", "admin_buy_subscription", "success"),
        ("vpn_panel", "🖥 مدیریت پنل‌های VPN", "admin_vpn_panels", "primary"),
        ("referrals", "🤝 مدیریت دعوت‌ها", "admin_referrals", "primary"),
        ("guides", "📚 مدیریت راهنما", "admin_guides", "primary"),
        ("texts", "📝 مدیریت متن‌های کاربر", "admin_texts", "primary"),
        ("logs", "🦖 لاگ خطاها", "errlog", "primary"),
        ("botinfo", "ℹ️ اطلاعات ربات", "admin_botinfo", "primary"),
        ("stickers", "🎬 استیکرهای منو", "admin_stickers", "primary"),
        ("backup", "💾 بکاپ", "admin_backup", "primary"),
        ("settings", "🎁 تنظیم تست رایگان", "admin_free_test_settings", "primary"),
        ("settings", "🔁 تنظیمات تمدید", "admin_renewal_settings", "primary"),
    ]
    buttons=[]
    for perm,text,cb,style in items:
        if allowed(perm): buttons.append(InlineKeyboardButton(text=text, callback_data=cb, style=style))
    if allowed("orders_toggle"):
        buttons.append(InlineKeyboardButton(text=("🔴 خاموش کردن سفارشات" if orders_enabled else "🟢 روشن کردن سفارشات"), callback_data=("admin_orders_off" if orders_enabled else "admin_orders_on"), style=("danger" if orders_enabled else "success")))
    if is_main_admin:
        buttons.append(InlineKeyboardButton(text="👮 مدیریت ادمین‌ها", callback_data="admin_manage_admins", style="danger"))
    if not buttons:
        buttons=[InlineKeyboardButton(text="⛔ هیچ دسترسی فعالی ندارید", callback_data="noop", style="danger")]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i+2] for i in range(0,len(buttons),2)])

def admin_back_button():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")]])


def admin_userlist_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 کاربران فعال (خریدکرده)", callback_data="admin_userlist_active", style="success")],
        [InlineKeyboardButton(text="👤 کاربران بدون خرید", callback_data="admin_userlist_all", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")],
    ])


def admin_discount_menu(discounts: list | None = None):
    buttons = []
    for d in (discounts or []):
        value_text = f"{d['amount']:,}ت" if d.get("discount_type") == "amount" else f"{d['percent']}٪"
        buttons.append([InlineKeyboardButton(
            text=f"🎟 {d['code']} | {value_text} | 🔁 {d['uses']}",
            callback_data=f"discdetail_{d['id']}", style="primary",
        )])
    buttons.append([InlineKeyboardButton(text="➕ ساخت کد تخفیف جدید", callback_data="new_discount", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def discount_detail_keyboard(discount_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💯 ویرایش مقدار تخفیف", callback_data=f"discedit_value_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="👤 ویرایش کاربران مجاز", callback_data=f"discedit_users_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="🎯 ویرایش پلن‌های مجاز", callback_data=f"discedit_plans_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="🔁 ویرایش تعداد استفاده", callback_data=f"discedit_uses_{discount_id}", style="success")],
        [InlineKeyboardButton(text="💰 ویرایش حداقل مبلغ سفارش", callback_data=f"discedit_minorder_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="🔂 ویرایش سقف استفاده هر کاربر", callback_data=f"discedit_maxuser_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="⏰ ویرایش تاریخ انقضا", callback_data=f"discedit_expiry_{discount_id}", style="primary")],
        [InlineKeyboardButton(text="🗑 حذف کد تخفیف", callback_data=f"discdelete_{discount_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست", callback_data="admin_discount", style="primary")],
    ])


def discount_delete_confirm_keyboard(discount_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"discdeleteconfirm_{discount_id}", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"discdetail_{discount_id}", style="danger")],
    ])


def admin_user_actions_keyboard(uid: str, is_blocked: bool = False, show_pm_link: bool = True):
    block_btn = (
        InlineKeyboardButton(text="✅ رفع مسدودیت کاربر", callback_data=f"toggleblock_{uid}", style="success")
        if is_blocked else
        InlineKeyboardButton(text="🚫 مسدود کردن کاربر", callback_data=f"toggleblock_{uid}", style="danger")
    )
    pm_row = [InlineKeyboardButton(text="✉️ پیام خصوصی به کاربر", callback_data=f"pm_{uid}", style="primary")]
    # دکمه‌ی "رفتن به پیوی کاربر" (لینک tg://user) برای برخی کاربران با تنظیمات حریم‌خصوصی محدودتر
    # توسط تلگرام رد می‌شود، پس handlers/admin.py در صورت خطای BUTTON_USER_PRIVACY_RESTRICTED همین کیبورد را با
    # show_pm_link=False دوباره می‌سازد تا فقط همین دکمه حذف شود.
    if show_pm_link:
        pm_row.append(InlineKeyboardButton(text="💬 رفتن به پیوی کاربر", url=f"tg://user?id={uid}", style="primary"))
    return InlineKeyboardMarkup(inline_keyboard=[
        pm_row,
        [InlineKeyboardButton(text="💰 شارژ دستی", callback_data=f"custom_{uid}", style="primary")],
        [InlineKeyboardButton(text="📒 حسابداری کاربر (تراکنش‌ها/منشأ پول)", callback_data=f"accounting_{uid}_0", style="primary")],
        [InlineKeyboardButton(text="🚀 ارسال کانفیگ VIP (QR)", callback_data=f"sendvip_{uid}", style="primary")],
        [InlineKeyboardButton(text="📦 مشاهده و مدیریت سرویس‌های کاربر", callback_data=f"svcs_{uid}", style="primary")],
        [block_btn, InlineKeyboardButton(text="🗑 حذف کامل کاربر", callback_data=f"deleteuser_{uid}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")],
    ])


def admin_delete_user_confirm_keyboard(uid: str):
    """تأیید حذف کامل کاربر فقط از دیتابیس ربات؛ هیچ پنل VPNای فراخوانی نمی‌شود."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚠️ بله، حذف کامل کاربر",
            callback_data=f"deleteuserconfirm_{uid}",
            style="danger",
        )],
        [InlineKeyboardButton(
            text="❌ انصراف",
            callback_data=f"useropen_{uid}",
            style="primary",
        )],
    ])


def admin_pm_cancel_keyboard(uid: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف از پیام خصوصی", callback_data=f"useropen_{uid}", style="danger")],
    ])


def admin_charge_approval_keyboard(uid: str, amount: int, receipt_id: int):
    # 🐛 فیکس: قبلاً callback_data فقط uid+amount بود که برای دو رسید متفاوت با همان مبلغ
    # یکسان می‌شد و قفل دائمی ضدتکرار (claim_admin_action) بعد از اولین بار همیشه برای
    # همان کاربر+مبلغ پیام «قبلاً پردازش شده» می‌داد. اضافه‌کردن receipt_id هر دکمه
    # را منحصربه‌فرد می‌کند.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ تأیید {amount:,}", callback_data=f"approve_{uid}_{amount}_{receipt_id}", style="success")],
        [InlineKeyboardButton(text="💵 مبلغ دلخواه", callback_data=f"custom_{uid}", style="primary")],
        [InlineKeyboardButton(text="❌ رد", callback_data=f"reject_{uid}_{receipt_id}", style="danger")],
    ])


def admin_purchase_card_approval_keyboard(uid: str, plan_key: str, price: int, receipt_id: int):
    # 🐛 فیکس: همان دلیل بالا — receipt_id را اضافه می‌کنیم تا دو رسید برای همان کاربر/پلن/قیمت با هم تداخل نکنند.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ تأیید پرداخت ({price:,} ت)", callback_data=f"approvepay|{uid}|{plan_key}|{price}|{receipt_id}", style="success")],
        [InlineKeyboardButton(text="❌ رد رسید", callback_data=f"rejectpay|{uid}|{receipt_id}", style="danger")],
    ])


def admin_purchase_notify_keyboard(uid: str, plan_key: str | None = None, order_id: int | None = None):
    suffix = f"|{order_id}" if order_id else ""
    oid = order_id or 0

    # نگاشت جدید چندپنلی: اگر برای همین پلن/دسته یک نمونه پنل فعال و یک
    # remote_ref معتبر نگاشت شده باشد، دکمه ارسال خودکار نمایش داده می‌شود.
    # panel_plan_map ستون enabled ندارد؛ فعال/غیرفعال بودن از vpn_panels خوانده می‌شود.
    auto_row = []
    if plan_key:
        mapping = db.get_panel_map_for_plan_key(plan_key)
        if mapping and mapping.get("panel_id") is not None and mapping.get("remote_ref") is not None:
            mapped_panel = db.get_vpn_panel(mapping["panel_id"])
            if mapped_panel and mapped_panel.get("enabled"):
                auto_row = [[InlineKeyboardButton(
                    text="📤 ارسال خودکار از پنل", callback_data=f"panelsend|{uid}|{plan_key}|{oid}",
                    style="danger"
                )]]

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 ارسال کانفیگ VIP (QR) — دستی", callback_data=f"sendvip_{uid}{suffix}", style="primary")],
        *auto_row,
    ])








# شناسه ثابت بخش‌های راهنمایی که برای دکمه‌های تحویل سرویس انتخاب شده‌اند.
# این اعداد همان id ستون جدول guides هستند و با تغییر عنوان راهنماها، مقصد دکمه‌ها تغییر نمی‌کند.
DELIVERY_APPS_GUIDE_ID = 8
DELIVERY_CONNECTION_GUIDE_ID = 4


def config_delivery_keyboard(guide_url: str | None = None, is_test: bool = False):
    """دکمه‌های شیشه‌ای تحویل سرویس. برای تست و بسته‌های عادی متن دکمه‌ها مستقل است."""
    if is_test:
        apps_text = t("service_delivery_test_apps_button")
        connection_text = t("service_delivery_test_connection_button")
    else:
        apps_text = t("service_delivery_apps_button")
        connection_text = t("service_delivery_connection_button")

    # برای سازگاری با متن‌های قدیمی ذخیره‌شده، برچسب «(تست)» را هم از عنوان دکمه حذف می‌کنیم.
    apps_text = apps_text.replace(" (تست)", "").replace("(تست)", "").strip()
    connection_text = connection_text.replace(" (تست)", "").replace("(تست)", "").strip()

    # هر دو دکمه در یک ردیف قرار می‌گیرند؛ مقصد Guideها ثابت می‌ماند.
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=apps_text,
                callback_data=f"guideopen_{DELIVERY_APPS_GUIDE_ID}",
                style="primary",
            ),
            InlineKeyboardButton(
                text=connection_text,
                callback_data=f"guideopen_{DELIVERY_CONNECTION_GUIDE_ID}",
                style="primary",
            ),
        ],
    ])


def admin_tickets_menu(counts: dict | None = None):
    counts=counts or {}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("admin_tickets_open", count=counts.get("open",0)),callback_data="admintickets_open",style="primary")],
        [InlineKeyboardButton(text=t("admin_tickets_unanswered", count=counts.get("unanswered",0)),callback_data="admintickets_unanswered",style="success")],
        [InlineKeyboardButton(text=t("admin_tickets_closed", count=counts.get("closed",0)),callback_data="admintickets_closed",style="primary")],
        [InlineKeyboardButton(text=t('admin_ticket_back'),callback_data="admin_back",style="danger")],
    ])

def admin_ticket_list_keyboard(tickets, filter_key: str):
    buttons=[]
    for item in tickets:
        tid=item.get('id'); marker='● ' if item.get('last_sender')=='user' and item.get('status')=='open' else ''
        buttons.append([InlineKeyboardButton(text=f"{marker}#{tid} | {item.get('telegram_id','-')}",callback_data=f"adminticket_{tid}",style="primary")])
    buttons.append([InlineKeyboardButton(text=t('admin_ticket_back'),callback_data="admin_tickets",style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_ticket_detail_keyboard(ticket: dict):
    tid=int(ticket['id'])
    buttons=[]
    if ticket.get('status')=='open':
        buttons.append([InlineKeyboardButton(text=t('admin_ticket_reply'),callback_data=f"ticketreply_{tid}",style="success")])
        buttons.append([InlineKeyboardButton(text=t('admin_ticket_close'),callback_data=f"ticketclose_{tid}",style="danger")])
    else:
        buttons.append([InlineKeyboardButton(text=t('admin_ticket_reopen'),callback_data=f"ticketreopen_{tid}",style="success")])
    buttons.append([InlineKeyboardButton(text=t('admin_ticket_back'),callback_data="admin_tickets",style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def ticket_reply_keyboard(uid: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ پاسخ", callback_data=f"replyticket_{uid}", style="primary")],
    ])


# ---------------------------------------------------------------------------
# 📦 مدیریت سرویس‌های کاربران توسط ادمین
# ---------------------------------------------------------------------------
def admin_services_list_keyboard(configs, uid: str):
    buttons = []
    for cfg in configs:
        icon = "🚀" if cfg.get("type", "vip") == "vip" else "🎮"
        mark = "❌ " if cfg.get("deleted") else ""
        display_name = cfg.get("_display_name") or cfg.get("plan") or "سرویس"
        live = cfg.get("_live_status")
        status = "🟢" if live == "active" else ("🔴" if live == "expired" else "⚪")
        buttons.append([InlineKeyboardButton(
            text=f"{mark}{status} {icon} {display_name}", callback_data=f"svcdetail_{cfg['id']}", style="primary"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"useractions_{uid}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_service_detail_keyboard(cfg: dict, uid: str):
    cfg_id = cfg["id"]
    is_deleted = bool(cfg.get("deleted"))
    is_vip = cfg.get("type", "vip") == "vip"
    buttons = []

    if is_deleted:
        buttons.append([InlineKeyboardButton(text="♻️ بازگردانی سرویس", callback_data=f"svcrestore_{cfg_id}", style="primary")])
        buttons.append([InlineKeyboardButton(text="🗑 حذف همیشگی (غیرقابل بازگشت)", callback_data=f"svcpurge_{cfg_id}", style="danger")])
    else:
        buttons.append([InlineKeyboardButton(text="✏️ تغییر لینک ساب", callback_data=f"svcedit_link_{cfg_id}", style="primary")])
        if is_vip:
            buttons.append([InlineKeyboardButton(text="🖼 تغییر عکس کیوآرکد", callback_data=f"svcedit_qr_{cfg_id}", style="primary")])
        else:
            buttons.append([InlineKeyboardButton(text="📁 مدیریت فایل‌های کانفیگ", callback_data=f"svcfiles_{cfg_id}", style="primary")])

        if cfg.get("source") in ("marzban", "pasargad", "threexui") and cfg.get("service_id"):
            buttons.append([InlineKeyboardButton(text="🔁 تمدید از پنل", callback_data=f"marzbanrenew_{cfg_id}", style="success")])
            buttons.append([InlineKeyboardButton(text="⏸ غیرفعال‌کردن در پنل", callback_data=f"marzbandisable_{cfg_id}", style="danger")])
            buttons.append([InlineKeyboardButton(text="▶️ فعال‌کردن در پنل", callback_data=f"marzbanenable_{cfg_id}", style="primary")])
            buttons.append([InlineKeyboardButton(text="🔄 ساخت لینک ساب جدید (خودکار از پنل)", callback_data=f"svcrevokesub_{cfg_id}", style="danger")])

        buttons.append([InlineKeyboardButton(text="🗑 حذف سرویس (مخفی از کاربر)", callback_data=f"svcdelete_{cfg_id}", style="danger")])

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به لیست سرویس‌ها", callback_data=f"svcs_{uid}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)




def admin_purge_confirm_keyboard(cfg_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، برای همیشه حذف کن", callback_data=f"svcpurgeconfirm_{cfg_id}", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"svcdetail_{cfg_id}", style="danger")],
    ])


def admin_request_queue_menu(order_count: int = 0, receipt_count: int = 0):
    order_label = f"📦 سفارش‌های در انتظار ({order_count})" if order_count else "📦 سفارش‌های در انتظار"
    receipt_label = f"🧾 رسیدهای در انتظار تایید ({receipt_count})" if receipt_count else "🧾 رسیدهای در انتظار تایید"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=order_label, callback_data="admin_order_queue", style="primary")],
        [InlineKeyboardButton(text=receipt_label, callback_data="admin_pending_receipts", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")],
    ])


def admin_pending_receipts_keyboard(receipts):
    """receipts: ردیف‌های جدول pending_receipts (kind='charge' یا 'plan_card')."""
    buttons = []
    for r in receipts:
        # 🐛 فیکس: r["id"] (شناسه‌ی خود ردیف pending_receipts) را هم در callback_data می‌فرستیم تا در
        # صف رسیدهای در انتظار هم که کاربر/مبلغشان یکسان است، قفل ضدتکرار با هم تداخل نکند.
        user = db.get_user(r.get("telegram_id"))
        profile_name = (user or {}).get("name") or "کاربر"
        if r["kind"] == "charge":
            label = f"💰 شارژ {r['amount']:,} ت — {profile_name}"
            buttons.append([
                InlineKeyboardButton(text=f"✅ {label}", callback_data=f"approve_{r['telegram_id']}_{r['amount']}_{r['id']}", style="success"),
                InlineKeyboardButton(text="❌", callback_data=f"reject_{r['telegram_id']}_{r['id']}", style="danger"),
            ])
        else:  # plan_card
            label = f"💳 {r['label']} — {r['amount']:,} ت — {profile_name}"
            buttons.append([
                InlineKeyboardButton(text=f"✅ {label}", callback_data=f"approvepay|{r['telegram_id']}|{r['extra']}|{r['amount']}|{r['id']}", style="success"),
                InlineKeyboardButton(text="❌", callback_data=f"rejectpay|{r['telegram_id']}|{r['id']}", style="danger"),
            ])
    if receipts:
        buttons.append([InlineKeyboardButton(text="🧹 علامت‌گذاری همه به‌عنوان بررسی‌شده", callback_data="clearreceipts_confirm", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_request_queue", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_clear_receipts_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، همه رو علامت بزن", callback_data="clearreceipts_do", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_pending_receipts", style="danger")],
    ])


def admin_order_queue_keyboard(orders):
    """صف سفارش‌ها: ارسال دستی + ارسال خودکار از پنل فعال برای پلن‌های نگاشت‌شده."""
    buttons = []
    for o in orders:
        uid = o.get("telegram_id") or ""
        plan_key = o.get("plan_key") or ""
        order_id = o["id"]
        buttons.append([
            InlineKeyboardButton(
                text=f"🚀 {o['plan_name']} — {o['price']:,} ت",
                callback_data=f"sendvip_{uid}|{order_id}", style="primary",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"dismissorder_{order_id}", style="danger"),
        ])
        if plan_key:
            buttons.append([InlineKeyboardButton(
                text="⚡ ارسال خودکار از پنل فعال",
                callback_data=f"marzbansend|{uid}|{plan_key}|{order_id}",
                style="success",
            )])
    if orders:
        buttons.append([InlineKeyboardButton(text="🧹 پاک کردن همه‌ی سفارش‌های این صف", callback_data="clearorders_confirm", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_request_queue", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_clear_orders_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، همه رو پاک کن", callback_data="clearorders_do", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_order_queue", style="danger")],
    ])


# ---------------------------------------------------------------------------
# 👥 لیست کاربران با صفحه‌بندی ۱۰تا۱۰تا (مرتب‌شده بر اساس بیشترین خرید)
# ---------------------------------------------------------------------------
def admin_userlist_page_keyboard(users: list, page: int, has_next: bool, list_kind: str = "active"):
    buttons = []
    for u in users:
        buttons.append([InlineKeyboardButton(
            text=f"👤 {u['name']} | 🛒 {u['total_purchase']:,} ت",
            callback_data=f"useropen_{u['telegram_id']}", style="primary",
        )])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ صفحه قبل", callback_data=f"userpage_{list_kind}_{page - 1}", style="primary"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️ صفحه بعد", callback_data=f"userpage_{list_kind}_{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_userlist", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 📚 راهنما و اموزش — فهرست قابل‌رشد از پنل ادمین (متن/عکس/فیلم)
# ---------------------------------------------------------------------------
def _guide_button_title_and_icon(guide: dict) -> tuple[str, str | None]:
    """متن دکمه راهنما و آیکن Premium آن را بدون نمایش fallback تکراری می‌سازد.

    فقط Custom Emojiای که واقعاً ابتدای عنوان است می‌تواند icon دکمه باشد.
    سایر Emojiهای داخل عنوان جزو خود عنوان‌اند و نباید مبنای icon شوند.
    """
    title = str(guide.get("title") or "")
    for raw in guide.get("title_entities") or []:
        try:
            typ = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            if hasattr(typ, "value"):
                typ = typ.value
            off = int(raw.get("offset", -1) if isinstance(raw, dict) else getattr(raw, "offset", -1))
            length = int(raw.get("length", 0) if isinstance(raw, dict) else getattr(raw, "length", 0))
            emoji_id = raw.get("custom_emoji_id") if isinstance(raw, dict) else getattr(raw, "custom_emoji_id", None)
            if typ == "custom_emoji" and emoji_id and off == 0 and length > 0:
                # Entity offsets بر اساس UTF-16 هستند؛ fallback ابتدای متن را دقیقاً
                # به همان اندازه حذف می‌کنیم تا emoji عادی کنار Premium نماند.
                used = 0; cut = 0
                for i, ch in enumerate(title):
                    if used >= length:
                        cut = i; break
                    used += 2 if ord(ch) > 0xFFFF else 1
                    cut = i + 1
                return title[cut:].lstrip(), str(emoji_id)
        except Exception:
            continue
    return title, None

def user_guides_menu(guides: list):
    if not guides:
        buttons = []
    else:
        buttons = []
        for g in guides:
            button_text, emoji_id = _guide_button_title_and_icon(g)
            kwargs = {
                "text": button_text,
                "callback_data": f"guideopen_{g['id']}",
                "style": "primary",
            }
            if emoji_id:
                kwargs["icon_custom_emoji_id"] = emoji_id
            buttons.append([InlineKeyboardButton(**kwargs)])
    buttons.append([InlineKeyboardButton(text=t("guides_back"), callback_data="back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def user_guide_detail_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("guide_detail_back"), callback_data="user_guides", style="primary")],
    ])


def admin_guides_menu(guides: list):
    buttons = []
    for i, g in enumerate(guides):
        buttons.append([InlineKeyboardButton(
            text=f"{g['title']}",
            callback_data=f"guideadminopen_{g['id']}",
            style="primary",
        )])
        move_row = []
        if i > 0:
            move_row.append(InlineKeyboardButton(
                text="⬆️",
                callback_data=f"guidemove_{g['id']}_up",
                style="primary",
            ))
        if i < len(guides) - 1:
            move_row.append(InlineKeyboardButton(
                text="⬇️",
                callback_data=f"guidemove_{g['id']}_down",
                style="primary",
            ))
        if move_row:
            buttons.append(move_row)

    buttons.append([InlineKeyboardButton(text="➕ افزودن راهنما/اموزش جدید", callback_data="guidenew", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_guide_detail_keyboard(guide_id: int, index: int, total: int):
    move_row = []
    if index > 0:
        move_row.append(InlineKeyboardButton(text="⬆️ بالاتر", callback_data=f"guidemove_{guide_id}_up", style="primary"))
    if index < total - 1:
        move_row.append(InlineKeyboardButton(text="⬇️ پایین‌تر", callback_data=f"guidemove_{guide_id}_down", style="primary"))
    buttons = [move_row] if move_row else []
    buttons += [
        [InlineKeyboardButton(text="✏️ ویرایش عنوان", callback_data=f"guideeditname_{guide_id}", style="primary")],
        [InlineKeyboardButton(text="📝 ویرایش محتوا (متن/عکس/فیلم)", callback_data=f"guideeditcontent_{guide_id}", style="primary")],
        [InlineKeyboardButton(text="🗑 حذف این راهنما", callback_data=f"guidedelete_{guide_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست راهنما", callback_data="admin_guides", style="primary")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_guide_delete_confirm_keyboard(guide_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"guidedeleteconfirm_{guide_id}", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"guideadminopen_{guide_id}", style="danger")],
    ])


def admin_guide_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="admin_guides", style="danger")],
    ])


def admin_stickers_menu(sections: list[dict]):
    """sections: [{"key": ..., "label": ..., "status_emoji": ...}, ...]"""
    buttons = [
        [InlineKeyboardButton(
            text=f"{s['status_emoji']} {s['label']}",
            callback_data=f"stickeropen_{s['key']}",
            style="primary",
        )]
        for s in sections
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_sticker_detail_keyboard(section_key: str, has_custom: bool, is_enabled: bool):
    buttons = [
        [InlineKeyboardButton(text="📤 آپلود/تغییر استیکر", callback_data=f"stickerset_{section_key}", style="success")],
    ]
    if is_enabled:
        buttons.append([InlineKeyboardButton(text="🛑 غیرفعال کردن (بدون استیکر)", callback_data=f"stickeroff_{section_key}", style="danger")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ فعال‌سازی دوباره", callback_data=f"stickeron_{section_key}", style="success")])
    if has_custom:
        buttons.append([InlineKeyboardButton(text="♻️ بازگرداندن به پیش‌فرض", callback_data=f"stickerreset_{section_key}", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به لیست بخش‌ها", callback_data="admin_stickers", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_sticker_cancel_keyboard(section_key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"stickeropen_{section_key}", style="danger")],
    ])


def admin_error_logs_keyboard(logs: list):
    buttons = []
    for log in logs:
        ts = str(log.get("occurred_at") or "")[:16]
        buttons.append([InlineKeyboardButton(
            text=f"⚠️ {ts} | {log['error_type']}",
            callback_data=f"errlogdetail_{log['id']}", style="danger",
        )])
    if logs:
        buttons.append([InlineKeyboardButton(text="🗑 این لاگ پاک‌سازیشون", callback_data="errlogclear", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="errlogrefresh", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_error_log_detail_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به لیست لاگ‌ها", callback_data="errlogrefresh", style="primary")],
    ])


def admin_error_logs_clear_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، پاکشون", callback_data="errlogclearconfirm", style="danger")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data="errlogrefresh", style="danger")],
    ])


def admin_referrers_page_keyboard(users: list, page: int, has_next: bool):
    buttons = []
    for u in users:
        buttons.append([InlineKeyboardButton(
            text=f"🤝 {u['name']} | 👥 دعوت: {u['invited_count']} | ✅ موفق: {u['successful_invites']}",
            callback_data=f"refdetail_{u['telegram_id']}_{page}", style="primary",
        )])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ صفحه قبل", callback_data=f"refpage_{page - 1}", style="primary"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️ صفحه بعد", callback_data=f"refpage_{page + 1}", style="primary"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_referred_detail_keyboard(referrer_uid: str, back_page: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 مشاهدهی کامل کاربر دعوت‌کننده", callback_data=f"useropen_{referrer_uid}", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست دعوت‌کنندگان", callback_data=f"refpage_{back_page}", style="primary")],
    ])


def admin_accounting_keyboard(uid: str, page: int, has_next: bool):
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ قبل", callback_data=f"accounting_{uid}_{page - 1}", style="primary"))
    if has_next:
        nav_row.append(InlineKeyboardButton(text="➡️ بعد", callback_data=f"accounting_{uid}_{page + 1}", style="primary"))
    buttons = [nav_row] if nav_row else []
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به کاربر", callback_data=f"useropen_{uid}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🎟 ساخت کد تخفیف — نوع تخفیف و پلن‌های قابل‌اعمال
# ---------------------------------------------------------------------------
def discount_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💯 درصدی", callback_data="disctype_percent", style="primary")],
        [InlineKeyboardButton(text="💵 مبلغ ثابت (تومان)", callback_data="disctype_amount", style="primary")],
    ])


def discount_plans_select_keyboard(selected: list):
    """با هر بار زدن روی یک پلن، انتخاب/عدم‌انتخابش toggle می‌شود؛ ✅ همه یعنی روی همه‌ی پلن‌ها اعمال شود."""
    buttons = [[InlineKeyboardButton(
        text="✅ همه‌ی پلن‌ها (بدون محدودیت)" if not selected else "☑️ همه‌ی پلن‌ها (بدون محدودیت)",
        callback_data="discplan_all", style="success",
    )]]
    for key, plan in db.get_all_plans().items():
        mark = "☑️" if key in selected else "⬜️"
        buttons.append([InlineKeyboardButton(text=f"{mark} {plan['name']}", callback_data=f"discplan_{key}", style="primary")])
    buttons.append([InlineKeyboardButton(text="✅ تأیید و ادامه", callback_data="discplan_done", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def discount_plans_edit_keyboard(discount_id: int, selected: list):
    """نسخه‌ی ویرایشِ کد تخفیف موجود؛ همان discount_plans_select_keyboard است اما با
    callback_data متفاوت (discplaned_) تا با مسیر ساخت کد جدید تداخل نکند."""
    buttons = [[InlineKeyboardButton(
        text="✅ همه‌ی پلن‌ها (بدون محدودیت)" if not selected else "☑️ همه‌ی پلن‌ها (بدون محدودیت)",
        callback_data=f"discplaned_{discount_id}_all", style="success",
    )]]
    for key, plan in db.get_all_plans().items():
        mark = "☑️" if key in selected else "⬜️"
        buttons.append([InlineKeyboardButton(text=f"{mark} {plan['name']}", callback_data=f"discplaned_{discount_id}_{key}", style="primary")])
    buttons.append([InlineKeyboardButton(text="✅ ذخیره", callback_data=f"discplaned_{discount_id}_done", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"discdetail_{discount_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🤝 نمایندگی — تخفیف خودکار روی VIP برای آیدی عددی‌های خاص
# ---------------------------------------------------------------------------
def admin_agency_menu(agents: list | None = None):
    """لیست نمایندگان به‌صورت دکمه؛ با زدن روی هرکدام دقیقاً همان صفحه‌ی
    مدیریت کاربر (مثل بخش «کاربران») باز می‌شود، به‌علاوه‌ی گزینه‌ی تغییر درصد تخفیف."""
    buttons = []
    for a in (agents or []):
        profile = db.get_user(a["telegram_id"])
        profile_name = (profile or {}).get("name") or "کاربر"
        buttons.append([InlineKeyboardButton(
            text=f"👤 {profile_name} | 💯 {a['vip_discount_percent']}٪",
            callback_data=f"agentopen_{a['telegram_id']}", style="primary",
        )])
    buttons.append([InlineKeyboardButton(text="➕ افزودن نماینده", callback_data="new_agent", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_agent_row_keyboard(telegram_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 حذف این نماینده", callback_data=f"deleteagent_{telegram_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_agency", style="primary")],
    ])


def admin_agent_actions_keyboard(uid: str):
    """دقیقاً همان کیبورد مدیریت کاربر (admin_user_actions_keyboard)، به‌علاوه‌ی
    یک دکمه‌ی اضافه برای تغییر درصد تخفیف نمایندگی؛ دکمه‌ی بازگشت هم به لیست
    نمایندگان برمی‌گردد (نه لیست کلی کاربران)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💯 تغییر درصد تخفیف نمایندگی", callback_data=f"editagentpercent_{uid}", style="primary")],
        [InlineKeyboardButton(text="💰 شارژ دستی", callback_data=f"custom_{uid}", style="primary")],
        [InlineKeyboardButton(text="📒 حسابداری کاربر (تراکنش‌ها/منشأ پول)", callback_data=f"accounting_{uid}_0", style="primary")],
        [InlineKeyboardButton(text="🚀 ارسال کانفیگ VIP (QR)", callback_data=f"sendvip_{uid}", style="primary")],
        [InlineKeyboardButton(text="📦 مشاهده و مدیریت سرویس‌های کاربر", callback_data=f"svcs_{uid}", style="primary")],
        [InlineKeyboardButton(text="🗑 حذف این نماینده", callback_data=f"deleteagent_{uid}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست نمایندگان", callback_data="admin_agency", style="primary")],
    ])


# ---------------------------------------------------------------------------
# 🗂 دسته‌بندی‌های VIP (پنل ادمین) — افزودن دسته‌ی جدید، ورود به هر دسته برای
# افزودن/ویرایش/حذف پلن‌های داخلش + تغییر ترتیب نمایش (⬆️/⬇️) دسته‌ها و پلن‌ها.
# ---------------------------------------------------------------------------
def admin_vip_categories_keyboard():
    buttons = []
    cats = db.get_vip_categories()
    for i, cat in enumerate(cats):
        n = len(db.get_vip_plans(cat["id"]))
        buttons.append([InlineKeyboardButton(
            text=f"{cat['name']} ({n} پلن)", callback_data=f"admincat_{cat['key']}"
        , style="primary")])
        move_row = []
        if i > 0:
            move_row.append(InlineKeyboardButton(text="⬆️", callback_data=f"movevipcat_{cat['key']}_up", style="primary"))
        if i < len(cats) - 1:
            move_row.append(InlineKeyboardButton(text="⬇️", callback_data=f"movevipcat_{cat['key']}_down", style="primary"))
        if move_row:
            buttons.append(move_row)
    buttons.append([InlineKeyboardButton(text="➕ دسته‌بندی جدید", callback_data="newvipcat", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vip_category_detail_keyboard(category_key: str):
    cat = db.get_vip_category(category_key)
    buttons = []
    if cat:
        plans = db.get_vip_plans(cat["id"])
        for i, plan in enumerate(plans):
            buttons.append([InlineKeyboardButton(
                text=f"{plan['name']} — {plan['price']:,} ت", callback_data=f"vipplan_{plan['plan_key']}"
            , style="primary")])
            move_row = []
            if i > 0:
                move_row.append(InlineKeyboardButton(text="⬆️", callback_data=f"movevipplan_{plan['plan_key']}_up", style="primary"))
            if i < len(plans) - 1:
                move_row.append(InlineKeyboardButton(text="⬇️", callback_data=f"movevipplan_{plan['plan_key']}_down", style="primary"))
            if move_row:
                buttons.append(move_row)
    buttons.append([InlineKeyboardButton(text="➕ افزودن پلن به این دسته", callback_data=f"newvipplan_{category_key}", style="success")])
    buttons.append([InlineKeyboardButton(text="🗑 حذف این دسته (فقط اگر خالی باشد)", callback_data=f"delvipcat_{category_key}", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_vip_categories", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vip_plan_detail_keyboard(plan_key: str, category_key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش نام", callback_data=f"vipplanname_{plan_key}", style="primary")],
        [InlineKeyboardButton(text="💰 ویرایش قیمت", callback_data=f"vipplanprice_{plan_key}", style="primary")],
        [InlineKeyboardButton(text="📦 ویرایش حجم (گیگ)", callback_data=f"vipplangb_{plan_key}", style="primary")],
        [InlineKeyboardButton(text="⏳ ویرایش مدت (روز، ۰=نامحدود)", callback_data=f"vipplandays_{plan_key}", style="primary")],
        [InlineKeyboardButton(text="👥 ویرایش سقف کاربر (۰ تا ۱۰، 0=نامحدود)", callback_data=f"vipplanuserlimit_{plan_key}", style="primary")],
        [InlineKeyboardButton(text="🗑 حذف این پلن", callback_data=f"delvipplan_{plan_key}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت به دسته", callback_data=f"admincat_{category_key}", style="primary")],
    ])


# ---------------------------------------------------------------------------
# 🔗 اتصال پنل مرزبان
# ---------------------------------------------------------------------------
def admin_marzban_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 تست اتصال (/me)", callback_data="marzban_test", style="primary")],
        [InlineKeyboardButton(text="🚦 ترافیک/مصرف برند (/traffic)", callback_data="marzban_traffic", style="primary")],
        [InlineKeyboardButton(text="📦 مشاهده‌ی بسته‌های پنل فعال (/plans)", callback_data="marzban_plans", style="primary")],
        [InlineKeyboardButton(text="🗂 نگاشت دسته‌بندی‌های VIP", callback_data="marzban_map_vip", style="primary")],
        [InlineKeyboardButton(text="🧪 نگاشت پیش‌فرض «تست رایگان»", callback_data="marzban_map_free_test", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")],
    ])


def marzban_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_marzban", style="primary")],
    ])


def marzban_map_category_pick_keyboard(categories: list[dict], scope: str):
    """لیست دسته‌بندی‌های VIP برای انتخاب اینکه کدام‌یک نگاشت شود."""
    buttons = []
    for cat in categories:
        mapping = db.get_marzban_plan_map(scope, cat["id"])
        mark = f" ✅ ({mapping['plan_slug']})" if mapping else ""
        buttons.append([InlineKeyboardButton(
            text=f"{cat['name']}{mark}", callback_data=f"marzbanmapcat_{scope}_{cat['id']}"
        , style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_marzban", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def marzban_map_vip_category_pick_keyboard(categories: list[dict]):
    """قدم اول نگاشت اختصاصی VIP: انتخاب دسته‌بندی (فقط برای رفتن به لیست
    پلن‌های داخل آن دسته، نه ذخیره‌ی مستقیم نگاشت)."""
    buttons = [
        [InlineKeyboardButton(text=cat["name"], callback_data=f"marzbanmapvipcat_{cat['id']}", style="primary")]
        for cat in categories
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_marzban", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def marzban_map_vip_plans_keyboard(category_id: int, plans: list[dict]):
    """قدم دوم نگاشت اختصاصی VIP: لیست تک‌تک پلن‌های داخل یک دسته، هرکدام با
    نگاشت اختصاصی خودشان (اگر قبلاً ست شده باشد). همچنین یک گزینه‌ی اختیاری
    برای «نگاشت پیش‌فرض کل دسته» (رفتار قدیمی، برای وقتی همه‌ی پلن‌های آن
    دسته واقعاً باید یک بسته‌ی مرزبان یکسان بگیرند)."""
    buttons = []
    for p in plans:
        mapping = db.get_marzban_plan_map("vip_plan", p["id"])
        mark = f" ✅ ({mapping['plan_slug']})" if mapping else " ⚪️ نگاشت‌نشده"
        label = f"{p['name']} — {p['volume_gb']}GB/{p['days']}روز{mark}"
        if len(label) > 64:
            label = label[:61] + "..."
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"marzbanmapvipplan_{category_id}_{p['id']}"
        , style="primary")])
    buttons.append([InlineKeyboardButton(
        text="🗂 نگاشت پیش‌فرض کل این دسته (اختیاری)",
        callback_data=f"marzbanmapcat_vip_category_{category_id}", style="primary",
    )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="marzban_map_vip", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def marzban_plan_pick_keyboard(plans: list[dict], callback_prefix: str):
    """لیست بسته‌های واقعی مرزبان (از /plans) برای انتخاب — plans باید هرکدام
    حداقل کلید 'idx' (اندیس محلی در state) و متن نمایشی 'label' داشته باشند."""
    buttons = [
        [InlineKeyboardButton(text=p["label"], callback_data=f"{callback_prefix}_{p['idx']}", style="primary")]
        for p in plans
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_marzban", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# ℹ️ اطلاعات ربات (قالب فروشی)
# ---------------------------------------------------------------------------
def admin_botinfo_menu():
    labels = bot_info.labels()
    buttons = []
    for key, label in labels.items():
        buttons.append([InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"botinfoedit_{key}", style="primary")])
    buttons.append([InlineKeyboardButton(text="📢 مدیریت کانال‌های اجباری", callback_data="botinfochannels", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_botinfo_renewal_categories_menu():
    buttons = []
    for cat in db.get_vip_categories():
        buttons.append([InlineKeyboardButton(
            text=f"💰 {cat['name']}",
            callback_data=f"botinforenewcat_{cat['id']}", style="primary",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_botinfo", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_botinfo_renewal_category_menu(category_id: int):
    cat = next((c for c in db.get_vip_categories() if int(c['id']) == int(category_id)), None)
    name = cat['name'] if cat else f"دسته {category_id}"
    gb = bot_info.get_renewal_price(category_id, "gb")
    day = bot_info.get_renewal_price(category_id, "day")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💰 قیمت هر گیگ: {gb:,} تومان", callback_data=f"botinforenewgb_{category_id}", style="success")],
        [InlineKeyboardButton(text=f"⏱ قیمت هر روز: {day:,} تومان", callback_data=f"botinforenewday_{category_id}", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data="botinforenewal", style="primary")],
        [InlineKeyboardButton(text="🔙 اطلاعات ربات", callback_data="admin_botinfo", style="primary")],
    ])


def _renewal_settings_ui(category_id):
    try: cid=int(category_id)
    except Exception: cid=0
    st={"mode":"day","price_day":0,"price_gb":5500,"min_day":1,"max_day":0,"min_gb":1,"max_gb":0,"day_options":"30,60,90","gb_options":"10,20,50"}
    for field in st:
        raw=db.get_setting(f"renewal_category_{cid}_{field}") if cid else None
        if raw not in (None, ""):
            try: st[field]=raw if field in ("mode", "day_options", "gb_options") else int(float(raw))
            except Exception: pass
    try:
        legacy=bot_info.get_renewal_settings(cid)
        if isinstance(legacy,dict):
            for field in st:
                if db.get_setting(f"renewal_category_{cid}_{field}") in (None, "") and field in legacy: st[field]=legacy[field]
    except Exception: pass
    return st


def admin_renewal_categories_menu():
    buttons = []
    for cat in db.get_vip_categories():
        settings = _renewal_settings_ui(cat["id"])
        mode_label = {"day": "روز", "gb": "گیگ", "both": "روز+گیگ"}.get(settings["mode"], "روز")
        buttons.append([InlineKeyboardButton(
            text=f"🔁 {cat['name']} — {mode_label}",
            callback_data=f"renewsetcat_{cat['id']}",
            style="primary",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def _renewal_edit_menu(category_id: int, plan_key: str | None = None):
    cat=db.get_vip_category(category_id); st=_renewal_settings_ui(category_id)
    if plan_key:
        try: st=bot_info.get_renewal_plan_settings(plan_key,category_id)
        except Exception: pass
    mode={"day":"فقط روز","gb":"فقط گیگ","both":"روز + گیگ"}.get(st["mode"],"فقط روز")
    def lim(v,suffix): return "نامحدود" if not v else f"{v} {suffix}"
    target=f"plan_{plan_key}" if plan_key else f"cat_{category_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"بخش تمدید: {mode}",callback_data=f"renewsetmode_{target}",style="primary")],
        [InlineKeyboardButton(text=f"قیمت روز: {st['price_day']:,}",callback_data=f"renewset_price_day_{target}",style="primary")],
        [InlineKeyboardButton(text=f"قیمت گیگ: {st['price_gb']:,}",callback_data=f"renewset_price_gb_{target}",style="primary")],
        [InlineKeyboardButton(text=f"حداقل روز: {st['min_day']}",callback_data=f"renewset_min_day_{target}",style="primary")],
        [InlineKeyboardButton(text=f"حداکثر روز: {lim(st['max_day'],'روز')}",callback_data=f"renewset_max_day_{target}",style="primary")],
        [InlineKeyboardButton(text=f"حداقل گیگ: {st['min_gb']}",callback_data=f"renewset_min_gb_{target}",style="primary")],
        [InlineKeyboardButton(text=f"حداکثر گیگ: {lim(st['max_gb'],'گیگ')}",callback_data=f"renewset_max_gb_{target}",style="primary")],
        [InlineKeyboardButton(text=f"دکمه‌های روز: {st.get('day_options') or '30,60,90'}",callback_data=f"renewset_options_day_{target}",style="primary")],
        [InlineKeyboardButton(text=f"دکمه‌های گیگ: {st.get('gb_options') or '10,20,50'}",callback_data=f"renewset_options_gb_{target}",style="primary")],
        [InlineKeyboardButton(text="بازگشت به انتخاب محدوده",callback_data=f"renewsetscope_{category_id}",style="primary")],
    ])

def admin_renewal_category_menu(category_id: int): return _renewal_edit_menu(category_id)

def admin_renewal_plan_menu(category_id: int, plan_key: str): return _renewal_edit_menu(category_id,plan_key)

def admin_renewal_mode_menu(category_id: int, plan_key: str | None = None):
    target=f"plan_{plan_key}" if plan_key else f"cat_{category_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="فقط روز",callback_data=f"renewsetmodeval_day_{target}",style="primary")],
        [InlineKeyboardButton(text="فقط گیگ",callback_data=f"renewsetmodeval_gb_{target}",style="primary")],
        [InlineKeyboardButton(text="هم روز + هم گیگ",callback_data=f"renewsetmodeval_both_{target}",style="primary")],
        [InlineKeyboardButton(text="بازگشت",callback_data=f"renewsetplan_{plan_key}" if plan_key else f"renewsetscope_{category_id}",style="danger")],
    ])

def admin_renewal_scope_menu(category_id: int):
    buttons=[[InlineKeyboardButton(text="همه پلن‌های این دسته",callback_data=f"renewsetscope_all_{category_id}",style="success")]]
    for plan in db.get_vip_plans(category_id):
        buttons.append([InlineKeyboardButton(text=str(plan.get("name") or plan.get("plan_key")),callback_data=f"renewsetscope_plan_{category_id}_{plan['plan_key']}",style="primary")])
    buttons.append([InlineKeyboardButton(text="بازگشت به دسته‌ها",callback_data="admin_renewal_settings",style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_botinfo_field_keyboard(key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_botinfo", style="primary")],
    ])


def admin_botinfo_channels_menu(channels: list[dict]):
    buttons = []
    for ch in channels:
        buttons.append([InlineKeyboardButton(
            text=f"❌ {ch.get('name') or ch.get('id')}",
            callback_data=f"botinfochdel_{ch.get('id')}", style="danger",
        )])
    buttons.append([InlineKeyboardButton(text="➕ افزودن کانال جدید", callback_data="botinfochadd", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_botinfo", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🛡️ اتصال پنل پاسارگارد (پنل VPN)
# ---------------------------------------------------------------------------
def admin_pasargad_menu(status_text: str):
    buttons=[[InlineKeyboardButton(text="🔌 تست اتصال", callback_data="pasargadtest", style="primary")],
             [InlineKeyboardButton(text="➕ افزودن پنل سرور", callback_data="pasargad_add", style="success")]]
    for row in db.get_vpn_panels("pasargad"):
        buttons.append([InlineKeyboardButton(text=f"🗑 حذف «{row.get('name') or row.get('id')}»", callback_data=f"pasargaddel_{row['id']}", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🛡️ کیبوردهای مدیریت چندنمونه‌ای پنل پاسارگارد
# ---------------------------------------------------------------------------
def admin_pasargad_panels_keyboard(panels=None):
    """لیست پنل‌های پاسارگارد برای handler جدید panel_admin."""
    buttons = []
    for panel in (panels or []):
        pid = panel.get("id")
        name = panel.get("name") or f"پنل {pid}"
        status = "🟢" if panel.get("enabled") else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {name}",
            callback_data=f"pp_detail|{pid}",
            style="success" if panel.get("enabled") else "danger",
        )])
    buttons.append([InlineKeyboardButton(text="➕ افزودن پنل", callback_data="pp_add", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_pasargad_panel_detail_keyboard(panel):
    """جزئیات یک پنل پاسارگارد."""
    pid = panel.get("id")
    enabled = bool(panel.get("enabled"))
    buttons = [
        [InlineKeyboardButton(text="🔌 تست اتصال", callback_data=f"pp_test|{pid}", style="primary")],
        [InlineKeyboardButton(
            text="⛔ غیرفعال کردن" if enabled else "✅ فعال کردن",
            callback_data=f"pp_toggle|{pid}",
            style="danger" if enabled else "success",
        )],
        [InlineKeyboardButton(text="🔀 نگاشت پلن‌ها به این پنل", callback_data=f"pp_mapping|{pid}|0", style="success")],
        [InlineKeyboardButton(text="✏️ ویرایش اطلاعات", callback_data=f"pp_edit|{pid}", style="primary")],
        [InlineKeyboardButton(text="🗑 حذف پنل", callback_data=f"pp_delete|{pid}", style="danger")],
        [InlineKeyboardButton(text="🔙 لیست پنل‌ها", callback_data="admin_pasargad_panels", style="primary")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_pasargad_panel_edit_keyboard(panel):
    """انتخاب فیلد قابل ویرایش پنل پاسارگارد."""
    pid = panel.get("id")
    buttons = [
        [InlineKeyboardButton(text="📝 نام پنل", callback_data=f"pp_editfield|{pid}|name", style="primary")],
        [InlineKeyboardButton(text="🌐 آدرس پنل", callback_data=f"pp_editfield|{pid}|base_url", style="primary")],
        [InlineKeyboardButton(text="👤 نام کاربری", callback_data=f"pp_editfield|{pid}|username", style="primary")],
        [InlineKeyboardButton(text="🔑 رمز عبور", callback_data=f"pp_editfield|{pid}|password", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"pp_detail|{pid}", style="primary")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)




def admin_pasargad_panel_mapping_keyboard(panel_id: int, plans, mappings=None, page: int = 0, per_page: int = 15):
    """لیست پلن‌های VIP برای نگاشت به یک پنل پاسارگارد. هر پلن می‌تواند
    به یک Template مشخص از همان پنل متصل شود."""
    mappings = mappings or {}
    plans = list(plans or [])
    total_pages = max(1, (len(plans) + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    chunk = plans[page * per_page:(page + 1) * per_page]
    buttons = []
    for plan in chunk:
        pid = int(plan.get("id"))
        name = plan.get("name") or plan.get("plan_key") or f"پلن {pid}"
        mapping = mappings.get(pid)
        if mapping and str(mapping.get("panel_id")) == str(panel_id):
            remote_name = mapping.get("remote_name") or mapping.get("remote_ref") or "تمپلیت"
            label = f"🟢 {name} ← {remote_name}"
        else:
            label = f"⚪ {name} — بدون نگاشت"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"pp_mapplan|{panel_id}|{pid}|{page}", style="success" if mapping else "primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ صفحه قبل", callback_data=f"pp_mapping|{panel_id}|{page-1}", style="primary"))
    nav.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="noop", style="primary"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="صفحه بعد ➡️", callback_data=f"pp_mapping|{panel_id}|{page+1}", style="primary"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data=f"pp_detail|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_pasargad_panel_template_map_keyboard(panel_id: int, plan_id: int, templates=None, current_ref=None):
    """انتخاب Template پاسارگارد برای یک پلن."""
    buttons = []
    for template in (templates or []):
        if not isinstance(template, dict) or template.get("id") is None:
            continue
        tid = template.get("id")
        name = template.get("name") or template.get("remark") or str(tid)
        selected = str(tid) == str(current_ref)
        buttons.append([InlineKeyboardButton(
            text=("✅ " if selected else "📦 ") + name,
            callback_data=f"pp_maptemplate|{panel_id}|{plan_id}|{tid}",
            style="success" if selected else "primary",
        )])
    if current_ref is not None:
        buttons.append([InlineKeyboardButton(text="🚫 حذف نگاشت این پلن", callback_data=f"pp_mapclear|{panel_id}|{plan_id}", style="danger")])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="⚠️ تمپلیتی در پنل پیدا نشد", callback_data=f"pp_mapping|{panel_id}|0", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به نگاشت پلن‌ها", callback_data=f"pp_mapping|{panel_id}|0", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_vip_plan_panel_keyboard(plan_key: str, panels=None, selected_panel_id=None):
    """انتخاب پنل پاسارگارد برای یک پلن VIP."""
    buttons = []
    for panel in (panels or []):
        pid = panel.get("id")
        if not pid:
            continue
        selected = str(pid) == str(selected_panel_id)
        name = panel.get("name") or f"پنل {pid}"
        buttons.append([InlineKeyboardButton(
            text=("✅ " if selected else "🖥️ ") + name,
            callback_data=f"vipplanpanelset|{plan_key}|{pid}",
            style="success" if selected else "primary",
        )])
    if selected_panel_id:
        buttons.append([InlineKeyboardButton(
            text="🚫 حذف اتصال پنل",
            callback_data=f"vipplanpanelclear|{plan_key}",
            style="danger",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vipplan_{plan_key}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vip_plan_panel_template_keyboard(plan_key: str, panel_id: int, templates=None):
    """انتخاب User Template پاسارگارد برای پلن VIP."""
    buttons = []
    for template in (templates or []):
        if not isinstance(template, dict) or template.get("id") is None:
            continue
        tid = template.get("id")
        name = template.get("name") or template.get("remark") or str(tid)
        buttons.append([InlineKeyboardButton(
            text=f"📦 {name}",
            callback_data=f"vipplanpaneltemplate|{plan_key}|{panel_id}|{tid}",
            style="primary",
        )])
    if not buttons:
        buttons.append([InlineKeyboardButton(
            text="⚠️ تمپلیتی پیدا نشد",
            callback_data=f"vipplanpanel|{plan_key}",
            style="danger",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vipplanpanel|{plan_key}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🔀 انتخاب پنل VPN فعال (وقتی هردو پنل مرزبان و پاسارگارد متصل باشند)
# ---------------------------------------------------------------------------
def admin_panel_choose_menu(available: list, active: str | None):
    buttons=[]
    for key in available:
        label = vpn_panel.panel_label(key)
        mark=" ✅" if key==active else ""
        buttons.append([InlineKeyboardButton(text=f"{label}{mark}",callback_data=f"panelchoose_{key}",style="success" if key==active else "primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت",callback_data="admin_back",style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_manage_admins_keyboard(admins=None):
    buttons = []
    for a in (admins or []):
        buttons.append([InlineKeyboardButton(text=f"👤 {a.get('name') or 'ادمین'}", callback_data=f"subadm_{a['telegram_id']}", style="primary")])
    buttons.append([InlineKeyboardButton(text="➕ افزودن ادمین فرعی", callback_data="subadm_add", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_permissions_keyboard(admin_id: str, selected=None):
    selected = set(selected or [])
    buttons = []
    for key, label in db.ADMIN_PERMISSIONS.items():
        mark = "✅" if key in selected else "☑️"
        # رفع باگ: از ':' به‌جای '_' برای جدا کردن آیدی از نام قابلیت استفاده می‌شود، چون خود
        # کلیدهای قابلیت مثل "vpn_panel" و "orders_toggle" داخلشان زیرخط دارند و با split قبلی قاطی می‌شدند.
        buttons.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"subadmperm_{admin_id}:{key}", style="success" if key in selected else "primary")])
    buttons.append([InlineKeyboardButton(text="🗑 حذف این ادمین", callback_data=f"subadmdel_{admin_id}", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔙 لیست ادمین‌ها", callback_data="admin_manage_admins", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# 🖥 مدیریت پنل‌های VPN — هر سه نوع (شاهراه/مرزبان/پاسارگارد) هم‌زمان
# فعال هستند و هر کدام می‌تواند چند نمونه (Instance) هم‌زمان داشته باشد.
# ---------------------------------------------------------------------------
def admin_vpn_panel_types_keyboard():
    """قدم اول: انتخاب نوع پنل برای مدیریت. هر سه نوع مستقل هم‌زمان قابل فعال‌شدن هستند."""
    buttons = [
        [InlineKeyboardButton(text=_PANEL_MODULE.PANEL_TYPE_LABELS[t], callback_data=f"vpntype|{t}", style="primary")]
        for t in panels.PANEL_TYPES
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vpn_panel_list_keyboard(panel_type: str, panel_list: list[dict]):
    """لیست نمونه‌های ساخته‌شده از یک نوع پنل (می‌توانند چندتایی باشند
    و همه هم‌زمان فعال بمانند) + دکمه‌ی افزودن نمونه‌ی جدید."""
    buttons = []
    for p in panel_list:
        mark = "🟢" if p.get("enabled") else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {p['name']}", callback_data=f"vpndetail|{p['id']}", style="primary"
        )])
    buttons.append([InlineKeyboardButton(
        text=f"➕ افزودن پنل {_PANEL_MODULE.PANEL_TYPE_LABELS.get(panel_type, panel_type)} جدید",
        callback_data=f"vpnadd|{panel_type}", style="success",
    )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به انتخاب نوع پنل", callback_data="admin_vpn_panels", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vpn_panel_detail_keyboard(panel: dict):
    """منوی مدیریت یک نمونه‌ی مشخص از پنل."""
    pid = panel["id"]
    if panel.get("enabled"):
        toggle_text = "🔴 غیرفعال کردن این پنل"
    else:
        toggle_text = "🟢 فعال‌کردن این پنل"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📡 تست اتصال", callback_data=f"vpntest|{pid}", style="primary")],
        [InlineKeyboardButton(text="✏️ ویرایش اطلاعات پنل", callback_data=f"vpnedit|{pid}", style="primary")],
        [InlineKeyboardButton(text="🗂 نگاشت پلن‌ها/بسته‌ها به این پنل", callback_data=f"vpnmap|{pid}", style="primary")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"vpntoggle|{pid}", style="danger" if panel.get("enabled") else "success")],
        [InlineKeyboardButton(text="🗑 حذف این پنل", callback_data=f"vpndelete|{pid}", style="danger")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست", callback_data=f"vpntype|{panel['panel_type']}", style="primary")],
    ])


def admin_vpn_panel_delete_confirm_keyboard(panel_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"vpndeleteconfirm|{panel_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data=f"vpndetail|{panel_id}", style="primary")],
    ])


def admin_vpn_panel_edit_menu_keyboard(panel: dict):
    """فیلدهای قابل‌ویرایش به نوع پنل و روش اتصال انتخاب‌شده بستگی دارد:
    شاهراه همیشه با API Key کار می‌کند. مرزبان/پاسارگارد بسته به auth_method
    یا با یوزرنیم+پسورد یا با یک API Key ثابت کار می‌کنند و ادمین می‌تواند
    با دکمه‌ی «تغییر روش اتصال» بین این دو جابه‌جا شود."""
    pid = panel["id"]
    buttons = [
        [InlineKeyboardButton(text="✏️ نام", callback_data=f"vpneditfield|{pid}|name", style="primary")],
        [InlineKeyboardButton(text="✏️ آدرس پایه (base URL)", callback_data=f"vpneditfield|{pid}|base_url", style="primary")],
    ]
    if panel["panel_type"] == "shahrah":
        buttons.append([InlineKeyboardButton(text="✏️ API Key", callback_data=f"vpneditfield|{pid}|api_key", style="primary")])
    else:
        auth_method = panel.get("auth_method") or "userpass"
        if auth_method == "api_key":
            buttons.append([InlineKeyboardButton(text="✏️ API Key", callback_data=f"vpneditfield|{pid}|api_key", style="primary")])
        else:
            buttons.append([InlineKeyboardButton(text="✏️ نام کاربری", callback_data=f"vpneditfield|{pid}|username", style="primary")])
            buttons.append([InlineKeyboardButton(text="✏️ رمز عبور", callback_data=f"vpneditfield|{pid}|password", style="primary")])
        other = "👤 یوزرنیم/پسورد" if auth_method == "api_key" else "🔑 API Key"
        buttons.append([InlineKeyboardButton(text=f"🔀 تغییر روش اتصال به {other}", callback_data=f"vpnauthswitch|{pid}", style="secondary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpndetail|{pid}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🛠 ایجاد سرویس دستی توسط ادمین (برای خودش) — مشابه «بساز سرویس خودت» ولی
# بدون قیمت/محدودیت حجم-روز و بدون نیاز به پرداخت؛ ادمین آزاد است از هر
# پنل فعال و هر تمپلیت/اینباند آن، هر حجم/روزی که بخواهد بسازد.
# ---------------------------------------------------------------------------
def admin_create_service_panel_keyboard(panels_list: list[dict]):
    import panels as _panels
    buttons = [
        [InlineKeyboardButton(text=_panels.panel_label(p), callback_data=f"adminsvcpanel_{p['id']}", style="primary")]
        for p in panels_list
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_create_service_catalog_keyboard(items: list[dict], panel_id: int):
    buttons = [
        [InlineKeyboardButton(text=p["label"], callback_data=f"adminsvccatalog_{panel_id}_{p['idx']}", style="primary")]
        for p in items
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_create_service", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_vpn_panel_auth_choice_keyboard(panel_type: str):
    """قدم اضافه‌ی «افزودن پنل جدید» برای انواعی که هم یوزر/پسورد و هم API Key
    را پشتیبانی می‌کنند: ادمین انتخاب می‌کند کدام روش برای این نمونه استفاده شود."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 یوزرنیم و پسورد", callback_data=f"vpnauthadd|{panel_type}|userpass", style="primary")],
        [InlineKeyboardButton(text="🔑 API Key", callback_data=f"vpnauthadd|{panel_type}|api_key", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_vpn_panels", style="danger")],
    ])


def vpn_panel_back_keyboard(panel_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpndetail|{panel_id}", style="primary")],
    ])


def admin_vpn_panel_types_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_vpn_panels", style="primary")],
    ])


def admin_vpn_panel_map_menu_keyboard(panel_id: int):
    """قدم اول نگاشت: برای این نمونه‌ی پنل، کدام بخش نگاشت شود؟"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 دسته‌بندی‌های VIP", callback_data=f"vpnmapvip|{panel_id}", style="primary")],
        [InlineKeyboardButton(text="🧪 «تست رایگان»", callback_data=f"vpnmapfreetest|{panel_id}", style="primary")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpndetail|{panel_id}", style="primary")],
    ])


def vpn_map_category_pick_keyboard(categories: list[dict], scope: str, panel_id: int):
    """لیست دسته‌بندی‌های VIP برای نگاشت پیش‌فرض کل دسته به این نمونه‌ی پنل."""
    buttons = []
    for cat in categories:
        mapping = db.get_panel_plan_map(scope, cat["id"])
        mark = f" ✅ ({mapping['remote_name'] or mapping['remote_ref']})" if mapping else ""
        buttons.append([InlineKeyboardButton(
            text=f"{cat['name']}{mark}", callback_data=f"vpnmapcat|{panel_id}|{scope}|{cat['id']}"
        , style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpnmap|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vpn_map_vip_category_pick_keyboard(categories: list[dict], panel_id: int):
    """قدم اول نگاشت اختصاصی VIP برای این نمونه‌ی پنل: انتخاب دسته‌بندی."""
    buttons = [
        [InlineKeyboardButton(text=cat["name"], callback_data=f"vpnmapvipcat|{panel_id}|{cat['id']}", style="primary")]
        for cat in categories
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpnmap|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vpn_map_vip_plans_keyboard(category_id: int, plans: list[dict], panel_id: int):
    """قدم دوم نگاشت اختصاصی VIP: هر پلن با نگاشت اختصاصی خودش به این نمونه‌ی پنل،
    یا نگاشت پیش‌فرض کل دسته."""
    buttons = []
    for p in plans:
        mapping = db.get_panel_plan_map("vip_plan", p["id"])
        mark = f" ✅ ({mapping['remote_name'] or mapping['remote_ref']})" if mapping else " ⚪️ نگاشت‌نشده"
        label = f"{p['name']} — {p['volume_gb']}GB/{p['days']}روز{mark}"
        if len(label) > 64:
            label = label[:61] + "..."
        buttons.append([InlineKeyboardButton(
            text=label, callback_data=f"vpnmapvipplan|{panel_id}|{category_id}|{p['id']}"
        , style="primary")])
        if mapping:
            buttons.append([InlineKeyboardButton(text="🗑 حذف نگاشت این پلن", callback_data=f"vpnmapdelplan|{panel_id}|{p['id']}", style="danger")])
    buttons.append([InlineKeyboardButton(
        text="🗂 نگاشت پیش‌فرض کل این دسته (اختیاری)",
        callback_data=f"vpnmapcat|{panel_id}|vip_category|{category_id}", style="primary",
    )])
    if db.get_panel_plan_map("vip_category", category_id):
        buttons.append([InlineKeyboardButton(text="🗑 حذف نگاشت پیش‌فرض این دسته", callback_data=f"vpnmapdelcat|{panel_id}|vip_category|{category_id}", style="danger")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpnmapvip|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vpn_catalog_pick_keyboard(items: list[dict], panel_id: int):
    """لیست بسته‌ها/تمپلیت‌های واقعی گرفته‌شده از خودِ پنل برای انتخاب نهایی — items هرکدام
    حداقل 'idx' (اندیس محلی در state) و متن نمایشی 'label' داشته باشند."""
    buttons = [
        [InlineKeyboardButton(text=p["label"], callback_data=f"vpncatalogpick|{panel_id}|{p['idx']}", style="primary")]
        for p in items
    ]
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpnmap|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# 🧩 نگاشت «بدون تمپلیت» — ادمین بین ساختن سرویس از روی یک تمپلیت آماده‌ی پنل
# یا مستقیم از روی اینباند/گروه‌های زنده‌ی پنل (بدون نیاز به هیچ تمپلیتی)
# انتخاب می‌کند.
# ---------------------------------------------------------------------------
def vpn_map_mode_keyboard(panel_id: int, supports_direct: bool):
    buttons = [[InlineKeyboardButton(text="📦 از تمپلیت پنل استفاده کن", callback_data=f"vpnmapmode|{panel_id}|template", style="primary")]]
    if supports_direct:
        buttons.append([InlineKeyboardButton(text="🧩 بدون تمپلیت (مستقیم از اینباند/گروه)", callback_data=f"vpnmapmode|{panel_id}|direct", style="success")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"vpnmap|{panel_id}", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def vpn_direct_multiselect_keyboard(items: list[dict], panel_id: int, selected: set):
    buttons = []
    for it in items:
        mark = "✅" if it["idx"] in selected else "⬜️"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {it['label']}", callback_data=f"vpndirecttoggle|{panel_id}|{it['idx']}", style="secondary",
        )])
    buttons.append([InlineKeyboardButton(
        text=f"✅ ذخیره ({len(selected)} انتخاب‌شده)", callback_data=f"vpndirectconfirm|{panel_id}", style="success",
    )])
    buttons.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"vpnmap|{panel_id}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
