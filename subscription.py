"""
subscription.py
دریافت زنده‌ی اطلاعات مصرف (حجم، تاریخ انقضا، نام سرویس) از روی لینک ساب کاربر،
بدون نیاز به هیچ دسترسی به دیتابیس یا API پنل.

توضیح فنی:
اکثر پنل‌های V2Ray/X-UI/Marzban/Hiddify و مشابه، وقتی یک درخواست GET به لینک ساب
زده شود (دقیقاً همان کاری که اپ‌های کلاینت مثل v2rayNG برای نمایش حجم باقی‌مانده
انجام می‌دهند)، یک هدر استاندارد به نام Subscription-Userinfo برمی‌گردانند؛
چیزی شبیه:
    upload=1073741824; download=2147483648; total=53687091200; expire=1751328000
همچنین بسیاری از پنل‌ها هدر Profile-Title را هم برمی‌گردانند که نام سرویس را
به‌صورت base64 دارد.

بعضی پنل‌ها (مثل Hiddify) به‌جای لینک خام ساب، یک لینک «نمایش در مرورگر»
(چیزی شبیه down.hplo.ir/view?...) می‌دهند که یک صفحه‌ی HTML برمی‌گرداند، نه
هدرهای بالا. در این حالت باید لینک ساب واقعی را از داخل همان صفحه پیدا کرد.
این ماژول این حالت را هم به‌صورت best-effort پوشش می‌دهد.

⚠️ توجه: چون این محیط به اینترنت دسترسی ندارد، این بخش قابل تست مستقیم روی
لینک‌های واقعی نبوده؛ اگر باز هم لینک‌های down.hplo.ir جواب ندادند، لطفاً یک
نمونه لینک واقعی (یا خروجی که مرورگر/curl از آن می‌گیرد) بفرست تا دقیق‌تر اصلاح شود.
"""

import base64
import asyncio
import re
import json
from datetime import datetime
from urllib.parse import unquote, parse_qs, urlparse

from utils import TEHRAN_TZ, now_tehran_naive

import aiohttp

# هدرهایی که شبیه یک کلاینت واقعی V2Ray/Clash هستند؛ خیلی از پنل‌ها بدون
# User-Agent مناسب، درخواست را رد می‌کنند یا صفحه‌ی HTML عادی برمی‌گردانند.
_CLIENT_HEADERS = {
    "User-Agent": "v2rayNG/1.8.29 (Linux; Android)",
    "Accept": "*/*",
}

_SUB_TIMEOUT = aiohttp.ClientTimeout(total=14, connect=5, sock_read=9)
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    """Session مشترک برای لینک‌های ساب؛ باعث reuse اتصال و سرعت بیشتر نمایش/ارسال کانفیگ می‌شود."""
    global _session
    if _session is not None and not _session.closed:
        return _session
    async with _session_lock:
        if _session is None or _session.closed:
            connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300, keepalive_timeout=60)
            _session = aiohttp.ClientSession(timeout=_SUB_TIMEOUT, headers=_CLIENT_HEADERS, connector=connector)
        return _session


_SUB_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# اسکیم‌های پروتکل‌های کانفیگ تکی که ممکن است داخل بدنه‌ی یک لینک ساب باشند.
_CONFIG_SCHEMES = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hysteria://", "hysteria2://", "hy2://", "tuic://")


async def _get(session: aiohttp.ClientSession, url: str):
    # اعتبارسنجی TLS برای حفاظت از لینک محرمانه اشتراک فعال است.
    async with session.get(url, allow_redirects=True) as resp:
        headers = dict(resp.headers)
        try:
            body = await resp.text(errors="ignore")
        except Exception:
            body = ""
        return resp.status, headers, body, str(resp.url)


def _looks_like_html(body: str) -> bool:
    head = (body or "").strip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<head" in head


def _decode_profile_title(headers: dict) -> str | None:
    raw = headers.get("Profile-Title") or headers.get("profile-title")
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower().startswith("base64:"):
        raw = raw[7:]
    try:
        return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="ignore").strip()
    except Exception:
        return raw or None


def _parse_userinfo(headers: dict) -> dict | None:
    header = headers.get("Subscription-Userinfo") or headers.get("subscription-userinfo")
    if not header:
        return None
    info = {}
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            try:
                info[key.strip()] = int(value.strip())
            except ValueError:
                pass
    return info if info else None


def _extract_name_from_body(body: str) -> str | None:
    """نام سرویس را از محتوای واقعی لینک ساب استخراج می‌کند.

    بعضی پنل‌ها هدر Profile-Title را نمی‌فرستند یا مقدار عمومی مثل
    ``Subscription`` می‌فرستند. در این حالت نام واقعی معمولاً داخل remark
    کانفیگ‌هاست؛ بنابراین دیگر فقط خط اول را بررسی نمی‌کنیم و همه‌ی خطوط را
    بررسی می‌کنیم. vmess های base64/JSON و پارامترهای name/remark هم پوشش داده
    می‌شوند.
    """
    if not body:
        return None

    candidate = body.strip()
    decoded = None
    try:
        decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4)).decode("utf-8", errors="ignore")
    except Exception:
        pass

    text = decoded if decoded and any(s in decoded for s in _CONFIG_SCHEMES) else candidate
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for line in lines:
        # remark استاندارد بعد از # در vless/trojan/ss/hysteria و ...
        if "#" in line and "://" in line:
            remark = unquote(line.rsplit("#", 1)[1]).strip()
            if remark:
                return remark

        # بعضی لینک‌ها نام را به صورت query parameter می‌دهند.
        if "://" in line:
            try:
                qs = parse_qs(urlparse(line).query)
                for key in ("name", "remark", "remarks", "profile-title", "profile_title"):
                    vals = qs.get(key)
                    if vals and vals[0].strip():
                        return unquote(vals[0]).strip()
            except Exception:
                pass

        # vmess://<base64-json> معمولاً نام را در ps دارد.
        if line.lower().startswith("vmess://"):
            try:
                payload = line.split("://", 1)[1]
                raw = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", errors="ignore")
                obj = json.loads(raw)
                for key in ("ps", "name", "remark"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        return unquote(value).strip()
            except Exception:
                pass

    return None


def _find_embedded_sub_link(html: str) -> str | None:
    """در صفحات «نمایش در مرورگر» (مثل Hiddify) دنبال لینک ساب واقعی درون HTML/JS می‌گردد."""
    if not html:
        return None
    candidates = _SUB_URL_PATTERN.findall(html)
    # اولویت با لینک‌هایی که به نظر لینک ساب واقعی می‌رسند (نه فایل‌های استاتیک/آیکون)
    for url in candidates:
        low = url.lower()
        if any(bad in low for bad in [".png", ".jpg", ".css", ".js", ".ico", ".svg", ".woff"]):
            continue
        if any(good in low for good in ["/sub", "/api/", "sub/", "subscribe"]):
            return url.rstrip("\"'<>),.;")
    return None



def _is_generic_subscription_name(value: str | None) -> bool:
    """مقادیر عمومی/خراب مثل Subscription یا base64 آن را نام سرویس حساب نکن."""
    if not isinstance(value, str):
        return True
    raw = value.strip()
    if not raw:
        return True
    candidates = {raw.casefold()}
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", errors="ignore").strip()
        if decoded:
            candidates.add(decoded.casefold())
    except Exception:
        pass
    return any(x in {"subscription", "profile", "sub", "default", "unknown"} for x in candidates)


def _extract_username_from_payload(payload) -> str | None:
    """username را از پاسخ‌های رایج endpointهای usage/info استخراج می‌کند."""
    if not isinstance(payload, dict):
        return None
    for key in ("username", "user_name", "name", "remark", "display_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and not _is_generic_subscription_name(value):
            return value.strip()
    for key in ("user", "data", "subscription", "account"):
        value = payload.get(key)
        found = _extract_username_from_payload(value)
        if found:
            return found
    return None


async def _get_subscription_usage_username(sub_url: str) -> str | None:
    """برای سرویس‌های قدیمی username را از endpoint اطلاعات Subscription/usage می‌خواند.

    بعضی پنل‌ها روی خود لینک ساب فقط Profile-Title عمومی می‌دهند اما endpoint
    usage/info همان username واقعی (مثل Config_430485) را برمی‌گرداند.
    چند شکل متداول URL امتحان می‌شود و شکست هرکدام روی بقیه اثری ندارد.
    """
    if not sub_url or not sub_url.lower().startswith(("http://", "https://")):
        return None
    try:
        session = await _get_session()
        base = sub_url.rstrip("/")
        candidates = [base + "/usage", base + "/info"]
        # اگر لینک با slash تمام شده بود، نسخه‌ی query نیز برای برخی proxyها مفید است.
        candidates.extend([base + "?usage=1", base + "?info=1"])
        seen = set()
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        continue
                    try:
                        payload = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text(errors="ignore")
                        try:
                            payload = json.loads(text)
                        except Exception:
                            continue
                    username = _extract_username_from_payload(payload)
                    if username:
                        return username
            except Exception:
                continue
    except Exception:
        pass
    return None

def normalize_service_name(name: str) -> str:
    """نام سرویس را برای نمایش کاربر تمیز و قابل‌خواندن می‌کند.

    مثال:
        👤-Config_520120-100.0 MB-1  ->  Config_520120

    فقط پیشوند ایموجی/خط تیره و پسوندهای ماشینیِ حجم/شماره را حذف می‌کنیم؛
    بخش اصلی نام (از جمله underscore) دست‌نخورده می‌ماند.
    """
    if not isinstance(name, str):
        return ""

    value = unquote(name).strip()

    # حذف ایموجی/نمادهای ابتدای نام و جداکننده‌ی بعد از آن.
    while value:
        cp = ord(value[0])
        if (0x1F000 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or cp in (0xFE0F, 0xFE0E):
            value = value[1:]
            continue
        break
    value = re.sub(r"^\s*[-–—_:|]+\s*", "", value)

    # پسوندهای تولیدشده توسط پنل، مثل: -100.0 MB-1 / -50 GB-2 / _100MB-1
    value = re.sub(
        r"(?:\s*[-_|]\s*\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*[-_|]\s*\d+)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip(" -–—_|:")


async def get_subscription_display_name(sub_url: str) -> str | None:
    """نام واقعی سرویس را مستقیماً از لینک Subscription استخراج می‌کند.

    Profile-Title اولویت دارد، اما مقادیر عمومی پنل مثل ``Subscription``
    یا ``profile`` نام سرویس محسوب نمی‌شوند و در این حالت از remark/name
    داخل خود محتوای لینک ساب استفاده می‌کنیم.
    """
    try:
        meta = await extract_meta(sub_url)
        if not meta:
            return None
        name = meta.get("name")
        if not isinstance(name, str):
            return None
        # نامی که از Subscription/پنل برمی‌گردد باید دقیقاً همان‌طور که هست
        # نمایش داده شود؛ هیچ normalize/حذف پیشوند یا پسوندی روی آن انجام نمی‌دهیم.
        name = name.strip()
        if _is_generic_subscription_name(name):
            return None
        return name
    except Exception:
        return None


async def _get_panel_display_name(cfg: dict) -> str | None:
    """نام واقعی سرویس را مستقیماً از همان پنلی که سرویس در آن ساخته شده می‌خواند.

    بعضی پنل‌ها در لینک Subscription هدر Profile-Title را به‌صورت مقدار عمومی
    مثل ``U3Vic2NyaXB0aW9u`` (= Subscription) می‌فرستند. در این حالت لینک ساب
    به‌تنهایی منبع قابل اعتمادی برای نام سرویس نیست؛ اما رکورد پنل با service_id
    همان شناسه/username واقعی سرویس را دارد.
    """
    service_id = str(cfg.get("service_id") or "").strip()
    if not service_id:
        return None
    source = str(cfg.get("source") or "").strip().lower()
    try:
        if source == "marzban":
            import marzban
            ok, data, _ = await marzban.get_user(service_id)
        elif source.startswith("pasargad:"):
            import pasargad, database as db
            row=db.get_vpn_panel(int(source.split(":",1)[1]))
            token=pasargad.set_runtime_panel(row)
            try: ok, data, _ = await pasargad.get_user(service_id)
            finally: pasargad.reset_runtime_panel(token)
        else:
            # برای سرویس‌های قدیمی که source ندارند، پنل فعال را امتحان کن.
            import vpn_panel
            ok, data, _ = await vpn_panel.get_user(service_id)
        if not ok or not isinstance(data, dict):
            return None

        # username اولویت دارد چون در Marzban/PasarGuard همان نام سرویس ساخته‌شده
        # است. کلیدهای دیگر فقط برای سازگاری با نسخه‌های مختلف پنل هستند.
        for key in ("username", "name", "remark", "remarks", "title", "display_name"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception:
        return None
    return None


async def enrich_configs_with_subscription_names(configs: list[dict]) -> list[dict]:
    """نام نمایشی سرویس را بدون تغییر DB mirror می‌کند.

    ترتیب قطعی: username پنل → username endpoint usage/info ساب → Profile-Title/remark
    → نام قبلی ذخیره‌شده. این ترتیب مخصوصاً سرویس‌های قدیمی با service_id خراب را
    پوشش می‌دهد؛ آن‌ها دیگر روی مقدارهایی مثل U3Vic2NyaXB0aW9u گیر نمی‌کنند.
    """
    async def _one(cfg: dict):
        try:
            panel_name = await asyncio.wait_for(_get_panel_display_name(cfg), timeout=6)
            if panel_name and not _is_generic_subscription_name(panel_name):
                cfg["_display_name"] = panel_name
                return cfg

            from crypto import decrypt_config
            sub_url = decrypt_config(cfg.get("config", ""))
            if sub_url and sub_url.lower().startswith(("http://", "https://")):
                usage_name = await asyncio.wait_for(_get_subscription_usage_username(sub_url), timeout=6)
                if usage_name and not _is_generic_subscription_name(usage_name):
                    cfg["_display_name"] = usage_name
                    return cfg

                name = await asyncio.wait_for(get_subscription_display_name(sub_url), timeout=6)
                if name and not _is_generic_subscription_name(name):
                    cfg["_display_name"] = name
        except Exception:
            pass
        return cfg

    if not configs:
        return configs
    await asyncio.gather(*(_one(cfg) for cfg in configs))
    return configs


async def fetch_subscription_info(sub_url: str) -> dict | None:
    """نسخه‌ی سازگار قبلی: فقط upload/download/total/expire را برمی‌گرداند."""
    meta = await extract_meta(sub_url)
    return meta.get("userinfo") if meta else None


async def extract_meta(sub_url: str, _depth: int = 0, _retry: int = 0) -> dict | None:
    """
    اطلاعات کامل یک لینک ساب را برمی‌گرداند. برای سرعت بیشتر از session مشترک
    و timeout کوتاه‌تر استفاده می‌شود؛ در صورت خطای موقت، فقط یک retry انجام می‌شود.
    """
    if not sub_url or not sub_url.strip().lower().startswith(("http://", "https://")):
        return None

    try:
        session = await _get_session()
        status, headers, body, final_url = await _get(session, sub_url.strip())

        userinfo = _parse_userinfo(headers)
        name = _decode_profile_title(headers)

        # بعضی پنل‌ها Profile-Title را با مقدار عمومی «Subscription»
        # برمی‌گردانند. این مقدار اسم سرویس نیست؛ در این حالت حتماً
        # محتوای خود ساب را برای remark/name واقعی بررسی می‌کنیم.
        if isinstance(name, str) and name.strip().casefold() in {"subscription", "profile", "sub", "default"}:
            name = None

        if userinfo or name:
            if not name:
                name = _extract_name_from_body(body)
            return {"userinfo": userinfo, "name": name, "final_url": final_url}

        if _looks_like_html(body) and _depth == 0:
            embedded = _find_embedded_sub_link(body)
            if embedded and embedded != sub_url:
                return await extract_meta(embedded, _depth=1)

        name = _extract_name_from_body(body)
        if name:
            return {"userinfo": None, "name": name, "final_url": final_url}

        return None
    except Exception:
        if _retry == 0:
            return await extract_meta(sub_url, _depth=_depth, _retry=1)
        return None


def _parse_configs(body: str) -> list[str]:
    """بدنه‌ی خام لینک ساب (معمولاً base64) را به لیست کانفیگ‌های تکی تبدیل می‌کند."""
    if not body:
        return []
    text = body.strip()
    try:
        decoded = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", errors="ignore")
    except Exception:
        decoded = None

    candidate = decoded if decoded and any(s in decoded for s in _CONFIG_SCHEMES) else text
    lines = [ln.strip() for ln in candidate.splitlines() if ln.strip()]
    return [ln for ln in lines if ln.startswith(_CONFIG_SCHEMES)]


async def extract_configs(sub_url: str, _depth: int = 0, _retry: int = 0) -> list[str] | None:
    """کانفیگ‌های تکی را از لینک ساب استخراج می‌کند؛ با session مشترک و timeout بهینه."""
    if not sub_url or not sub_url.strip().lower().startswith(("http://", "https://")):
        return None

    try:
        session = await _get_session()
        status, headers, body, final_url = await _get(session, sub_url.strip())

        configs = _parse_configs(body)
        if configs:
            return configs

        if _looks_like_html(body) and _depth == 0:
            embedded = _find_embedded_sub_link(body)
            if embedded and embedded != sub_url:
                return await extract_configs(embedded, _depth=1)

        return []
    except Exception:
        if _retry == 0:
            return await extract_configs(sub_url, _depth=_depth, _retry=1)
        return None


def format_bytes(num_bytes) -> str:
    """بایت را به شکل خوانا مثل «۱۲.۴ گیگابایت» تبدیل می‌کند."""
    if num_bytes is None:
        return "نامشخص"
    try:
        num_bytes = int(num_bytes)
    except (TypeError, ValueError):
        return "نامشخص"

    gb = num_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} گیگابایت"
    mb = num_bytes / (1024 ** 2)
    return f"{mb:.0f} مگابایت"


def format_expire(expire_ts) -> str:
    """تایم‌استمپ انقضا را به تاریخ خوانا تبدیل می‌کند."""
    if not expire_ts:
        return "نامحدود"
    try:
        dt = datetime.fromtimestamp(int(expire_ts), tz=TEHRAN_TZ).replace(tzinfo=None)
        return dt.strftime("%Y/%m/%d")
    except Exception:
        return "نامشخص"


def usage_bar(percent, length: int = 10) -> str:
    """نوار پیشرفت مصرف با ایموجی؛ مثل 🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜ ۶۰٪"""
    try:
        percent = max(0, min(100, float(percent)))
    except (TypeError, ValueError):
        percent = 0
    filled = round(length * percent / 100)
    color = "🟥" if percent >= 90 else ("🟨" if percent >= 80 else "🟩")
    return color * filled + "⬜" * (length - filled)


def days_remaining(expire_ts) -> int | None:
    if not expire_ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(expire_ts), tz=TEHRAN_TZ).replace(tzinfo=None)
        delta = dt - now_tehran_naive()
        return delta.days
    except Exception:
        return None


def is_config_expired(cfg: dict) -> bool:
    """بررسی اینکه آیا یک کانفیگ منقضی شده است یا خیر.
    اگر expiry تنظیم نشده باشد (None) False برمی‌گرداند (نامحدود فرض می‌شود).
    """
    expiry = cfg.get("expiry")
    if not expiry:
        return False
    try:
        exp_dt = datetime.strptime(str(expiry)[:10], "%Y-%m-%d")
        return exp_dt < now_tehran_naive()
    except Exception:
        return False




async def get_live_service_status(cfg: dict) -> str | None:
    """وضعیت لحظه‌ای سرویس را از پنل/Subscription می‌خواند؛ فقط برای نمایش."""
    sid = str(cfg.get("service_id") or "").strip()
    source = str(cfg.get("source") or "").lower()
    if sid:
        try:
            if source == "marzban":
                import marzban
                ok, data, _ = await marzban.get_user(sid)
            elif source.startswith("pasargad:"):
                import pasargad, database as db
                row=db.get_vpn_panel(int(source.split(":",1)[1])); token=pasargad.set_runtime_panel(row)
                try: ok, data, _ = await pasargad.get_user(sid)
                finally: pasargad.reset_runtime_panel(token)
            else:
                ok, data, _ = False, None, None
            if ok and isinstance(data, dict):
                status = str(data.get("status") or "").lower()
                if status in {"disabled", "inactive", "expired"}:
                    return "expired"
                expire = data.get("expire")
                if expire:
                    try:
                        if int(expire) <= int(datetime.now().timestamp()):
                            return "expired"
                    except Exception:
                        pass
                return "active"
        except Exception:
            pass
    try:
        from crypto import decrypt_config
        sub = decrypt_config(cfg.get("config", ""))
        info = await fetch_subscription_info(sub)
        if info and info.get("expire"):
            return "expired" if int(info["expire"]) <= int(datetime.now().timestamp()) else "active"
    except Exception:
        pass
    return "expired" if is_config_expired(cfg) else "active"
