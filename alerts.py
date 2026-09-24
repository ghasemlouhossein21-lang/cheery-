from utils import send_photo_rich, edit_caption_rich
from utils import send_rich
"""
alerts.py
بررسی دوره‌ای مصرف و تاریخ انقضای سرویس‌های VIP و اطلاع‌رسانی خودکار به کاربر
وقتی ۸۰٪/۹۰٪ حجم مصرف شده یا ۲ روز به پایان سرویس مانده است.
این هشدارها فقط مخصوص سرویس‌های VIP هستند (طبق درخواست کاربر).
"""
import logging

import crypto
import database as db
from text_catalog import text as t
from subscription import fetch_subscription_info, usage_bar, days_remaining, get_live_service_status
from keyboards import back_button, fair_use_keyboard, service_alert_80_90_keyboard, service_expired_alert_keyboard
import bot_info
from utils import send_notification_sticker

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 1800  # هر ۳۰ دقیقه یک بار


async def check_usage_alerts(bot):
    """روی همه‌ی سرویس‌های VIP فعال حلقه می‌زند و در صورت لزوم هشدار می‌فرستد."""
    configs = db.get_active_vip_configs()
    for cfg in configs:
        try:
            await _check_single_config(bot, cfg)
        except Exception:
            logger.exception("خطا در بررسی هشدار مصرف برای سرویس %s", cfg.get("id"))


async def _check_single_config(bot, cfg):
    try:
        sub_link = crypto.decrypt_config(cfg["config"])
    except Exception:
        return
    if not sub_link.lower().startswith(("http://", "https://")):
        return

    usage = await fetch_subscription_info(sub_link)
    if not usage:
        return

    user = db.get_user_by_id(cfg["user_id"])
    if not user:
        return

    total = usage.get("total")
    used = (usage.get("upload") or 0) + (usage.get("download") or 0)
    expire_ts = usage.get("expire")

    # ابتدا پایان واقعی سرویس را بررسی می‌کنیم تا سرویس ۱۰۰٪ تمام‌شده در همان
    # چرخه همزمان هشدار ۹۰٪ و پایان نگیرد. غیرفعال‌شدن از خود پنل هم بررسی می‌شود.
    ended = bool(cfg.get("disabled"))
    if total:
        ended = ended or used >= total
    if expire_ts:
        try: ended = ended or int(expire_ts) <= int(__import__("time").time())
        except (TypeError,ValueError): pass
    if not ended:
        try: ended = (await get_live_service_status(cfg)) == "expired"
        except Exception: pass
    if ended:
        if not cfg.get("alert_expiry_sent") and await _send_expired_alert(bot,user,cfg):
            db.set_config_alert_sent(cfg["id"],"alert_expiry_sent")
            db.set_config_alert_sent(cfg["id"],"alert_80_sent")
            db.set_config_alert_sent(cfg["id"],"alert_90_sent")
        return

    if total:
        percent=min(100,int(used/total*100))
        if percent>=90 and not cfg.get("alert_90_sent"):
            if await _send_usage_alert(bot,user,cfg,percent):
                db.set_config_alert_sent(cfg["id"],"alert_90_sent")
                db.set_config_alert_sent(cfg["id"],"alert_80_sent")
        elif percent>=80 and not cfg.get("alert_80_sent"):
            if await _send_usage_alert(bot,user,cfg,percent):
                db.set_config_alert_sent(cfg["id"],"alert_80_sent")
    else:
        try: fair_gb=float(bot_info.get("fair_use_gb") or 0)
        except Exception: fair_gb=0
        if fair_gb>0 and used>=fair_gb*(1024**3) and not cfg.get("fair_use_alert_sent"):
            if await _send_fair_use_alert(bot,user,cfg,fair_gb):
                db.set_fair_use_alert_sent(cfg["id"],True)


def _config_package_name(cfg: dict) -> str:
    plan_key=str(cfg.get("plan_key") or "").strip()
    if plan_key:
        try:
            plan=db.get_effective_plan(plan_key)
            if plan and plan.get("name"): return str(plan["name"])
        except Exception: pass
    try:
        cid=int(cfg.get("category_id") or 0)
        plans=db.get_vip_plans(cid) if cid else []
        raw_candidate=str(cfg.get("plan") or "").strip()
        for plan in plans:
            if raw_candidate == str(plan.get("name") or "").strip(): return raw_candidate
        if len(plans)==1 and plans[0].get("name"): return str(plans[0]["name"])
    except Exception: pass
    raw=str(cfg.get("plan") or "سرویس")
    # سرویس‌های قدیمی نام کاربری را قبل از اولین | نگه می‌داشتند؛ برای آن‌ها
    # بخش بسته از قسمت‌های بعدی قابل تشخیص است.
    parts=[x.strip() for x in raw.split("|")]
    return " | ".join(parts[1:]) if len(parts)>1 else raw



def get_config_service_username(cfg: dict, panel_data: dict | None = None) -> str:
    """نام واقعی سرویس در پنل؛ برای لاگ/گزارش همیشه username اولویت دارد."""
    data = panel_data if isinstance(panel_data, dict) else {}
    return str(
        data.get("username")
        or cfg.get("service_id")
        or data.get("name")
        or cfg.get("_display_name")
        or "-"
    ).strip() or "-"


def get_config_package_name(cfg: dict) -> str:
    """نام دقیق پلن فروشگاه، بدون چسباندن username یا جزئیات تمدید."""
    return _config_package_name(cfg)


def expiry_text_from_panel_data(panel_data: dict | None, fallback: str | None = None) -> str:
    """انقضای واقعی ثبت‌شده در پنل را به تاریخ تهران تبدیل می‌کند."""
    data = panel_data if isinstance(panel_data, dict) else {}
    if "expire" in data:
        expire = data.get("expire")
        if not expire:
            return "نامحدود"
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(int(expire), tz=ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d")
        except Exception:
            pass
    return str(fallback or "نامحدود")


async def log_renewal_to_channel(bot, user: dict, cfg: dict, panel_data: dict | None, amount: int | float, added_volume: float = 0, added_days: int = 0, payment_method: str = ""):
    """لاگ تمدید با قالب قابل ویرایش ادمین و روش پرداخت."""
    await log_order_to_channel(
        bot,
        order_label="🔁 تمدید سرویس",
        user=user,
        username=None,
        service_id=cfg.get("service_id"),
        service_name=get_config_service_username(cfg, panel_data),
        package_text=get_config_package_name(cfg),
        amount_text=f"{int(amount or 0):,} تومان" if amount else "رایگان",
        expiry_text=expiry_text_from_panel_data(panel_data, cfg.get("expiry")),
        renewal_details=_renewal_log_details(added_volume, added_days),
        payment_method=payment_method or "-",
    )


async def _send_usage_alert(bot, user, cfg, percent):
    bar = usage_bar(percent)
    key = "notif_usage_90" if percent >= 90 else "notif_usage_80"
    text = t(key, plan=_config_package_name(cfg), percent=percent, bar=bar)
    return await _safe_send(bot, user, cfg, text, sticker_key=key, reply_markup=service_alert_80_90_keyboard(cfg["id"]))


async def _send_fair_use_alert(bot, user, cfg, fair_gb):
    text=t("notif_fair_use",plan=_config_package_name(cfg),fair_use_gb=f"{fair_gb:g}")
    try:
        await send_notification_sticker(bot,int(user["telegram_id"]),"notif_usage_90")
        await send_rich(bot,int(user["telegram_id"]),text,reply_markup=fair_use_keyboard(cfg["id"]))
        return True
    except Exception:
        logger.exception("ارسال هشدار مصرف منصفانه ناموفق بود برای %s",user.get("telegram_id"))
        return False


async def _send_expired_alert(bot, user, cfg):
    text=t("notif_expiry", plan=_config_package_name(cfg), days_text="به پایان رسید")
    return await _safe_send(bot,user,cfg,text,sticker_key="notif_expiry",reply_markup=service_expired_alert_keyboard(cfg["id"]))


async def _safe_send(bot, user, cfg, text, sticker_key: str | None = None, reply_markup=None):
    try:
        if sticker_key:
            await send_notification_sticker(bot, int(user["telegram_id"]), sticker_key)
        await send_rich(bot, 
            int(user["telegram_id"]), text,
            reply_markup=reply_markup or back_button(f"viewconfig_{cfg['id']}", t("notif_view_service")),
        )
        return True
    except Exception:
        logger.exception("ارسال هشدار مصرف به کاربر %s ناموفق بود", user.get("telegram_id"))
        return False


# ---------------------------------------------------------------------------
# 🛎 لاگ همه‌ی سفارش‌های نهایی‌شده (خرید/تمدید/تست رایگان/سرویس سفارشی) در
# کانال «اعتماد»، با قالب ثابت.
# ---------------------------------------------------------------------------
def _mask_telegram_id(telegram_id) -> str:
    """آیدی عددی را برای حفظ حریم خصوصی، در پیام کانال اعتماد به‌شکل ماسک‌شده
    نمایش می‌دهد؛ مثلاً 6512345515 → 65*****515 (۲ رقم اول + ۳ رقم آخر باقی می‌مانند)."""
    s = str(telegram_id or "-")
    if len(s) <= 5:
        return s
    return s[:2] + "*" * (len(s) - 5) + s[-3:]


def _fix_unlimited_typo(value: str) -> str:
    """رفع تایپوی احتمالی «نامدود» (بجای «نامحدود») در متن‌های لاگ سفارش."""
    s = str(value or "")
    return s.replace("نامدود", "نامحدود") if "نامدود" in s else s


def _renewal_log_details(added_volume: float, added_days: int) -> str:
    parts = []
    if added_volume:
        parts.append(f"+{float(added_volume):g} گیگ")
    if added_days:
        parts.append(f"+{int(added_days)} روز")
    return " | ".join(parts) if parts else "بدون تغییر"


async def log_order_to_channel(
    bot,
    *,
    order_label: str,
    user: dict,
    username: str | None,
    service_id: str | None,
    service_name: str | None,
    package_text: str,
    amount_text: str,
    expiry_text: str,
    renewal_details: str | None = None,
    payment_method: str | None = None,
):
    """ارسال لاگ سفارش با قالب‌های قابل ویرایش و پشتیبانی از Premium Emoji."""
    from utils import now_tehran

    package_text = _fix_unlimited_typo(package_text)
    expiry_text = _fix_unlimited_typo(expiry_text)
    if "تست" in order_label:
        template_key = "order_log_test"
    elif "تمدید" in order_label:
        template_key = "order_log_renewal"
    else:
        template_key = "order_log_purchase"

    values = {
        "order_label": order_label,
        "customer_name": user.get("name", "-"),
        "telegram_id": _mask_telegram_id(user.get("telegram_id")),
        "service_id": service_id or "-",
        "service_name": service_name or "-",
        "package_name": package_text or "-",
        "amount": amount_text or "-",
        "expiry": expiry_text or "-",
        "time": now_tehran().strftime("%Y-%m-%d %H:%M"),
        "payment_method": payment_method or "-",
        "renewal_details": renewal_details or "",
        "username": username or "-",
    }
    text = t(template_key, **values)
    try:
        order_log_channel_id = bot_info.get("order_log_channel_id")
        if order_log_channel_id and str(order_log_channel_id) != "0":
            # متن لاگ یک RichText است و entityهای Premium Emoji آن باید دقیقاً
            # از همان مسیر عمومی ارسال RichText عبور کنند. این مسیر هم entityها را
            # اعتبارسنجی می‌کند و هم در صورت خطای Telegram رفتار fallback خودش را دارد.
            await send_rich(bot, order_log_channel_id, text)
    except Exception:
        logger.exception("ارسال لاگ سفارش به کانال اعتماد ناموفق بود")


def admin_delivery_summary(
    user: dict,
    service_username: str,
    package_name: str,
    amount: int | None = None,
    when=None,
    payment_method: str = "-",
) -> str:
    """گزارش تحویل/خرید برای پنل ادمین با روش پرداخت قابل ویرایش."""
    from utils import now_tehran
    return t(
        "admin_delivery_summary",
        customer=user.get("name") or "-",
        telegram_id=str(user.get("telegram_id") or "-"),
        service_username=service_username or "-",
        package_name=package_name or "-",
        amount=int(amount or 0),
        payment_method=payment_method or "-",
        time=(when or now_tehran()).strftime("%Y-%m-%d %H:%M"),
    )

def report_uniquepay_create_success():
    _uniquepay_state["create_fail_streak"] = 0
