from collections import OrderedDict
import database as db

# All user-facing editable texts. Values containing {placeholders} are templates.
TEXT_CATEGORIES = OrderedDict({
    '🏠 منوی اصلی': [
        ('main_buy', '🛒 خرید اشتراک'),
        ('main_free_test', '🎁 تست رایگان'),
        ('main_configs', '📱 سرویس\u200cهای من'),
        ('main_wallet', '💰 کیف پول'),
        ('main_referral', '👥 دعوت دوستان و کسب درآمد'),
        ('main_profile', '👤 پروفایل من'),
        ('main_support', '👨\u200d💻 پشتیبانی'),
        ('main_guides', '📚 راهنما'),
        ('main_renew', '🔁 تمدید سرویس'),
        ('main_agency', '🤝 درخواست نمایندگی'),
    ],
    '👤 پروفایل من': [
        ('profile_overview', '🧑‍💻 پروفایل حرفه\u200cای شما\n\n📛 نام: {name}\n🪪 آیدی: {telegram_id}\n\n💰 موجودی قابل استفاده: {wallet:,} تومان\n🔒 موجودی در انتظار: {locked:,} تومان\n\n📦 تعداد سرویس: {configs_count}\n🛒 کل خرید: {total_purchase:,} تومان\n🗓 تاریخ عضویت: {joined}\n\n👥 تعداد دعوت: {invited_count} | دعوت موفق: {successful_invites}'),
        ('profile_free_wallet', '💰 کیف پول آزاد'),
        ('profile_locked_wallet', '🔒 کیف پول مسدود'),
        ('profile_history', '🛒 تاریخچه خرید'),
        ('profile_transactions', '📋 تاریخچه تراکنش'),
        ('profile_referral', '🔗 لینک دعوت اختصاصی'),
        ('profile_back', '🏠 بازگشت به منوی اصلی'),
        ('purchase_history_empty', '🛒 شما هنوز خریدی انجام نداده\u200cاید.'),
        ('purchase_history_title', '🛒 تاریخچه خرید شما:\n\n'),
    ],
    '💰 کیف پول': [
        ('wallet_overview', '💰 کیف پول شما\n\n👛 موجودی قابل استفاده: {wallet:,} تومان\n🔒 موجودی در انتظار: {locked:,} تومان'),
        ('wallet_free_overview', '💰 موجودی قابل استفاده شما\n\n{wallet:,} تومان\n\nاین مبلغ را می\u200cتوانید برای خرید سرویس استفاده کنید.'),
        ('wallet_locked_overview', '🔒 موجودی در انتظار شما\n\n{locked:,} تومان'),
        ('wallet_charge', '💳 شارژ کیف پول'),
        ('wallet_discount', '🎟 ثبت کد تخفیف'),
        ('wallet_transactions', '📋 تراکنش\u200cهای من'),
        ('wallet_back', '🏠 بازگشت به منوی اصلی'),
        ('wallet_charge_range', '❌ مبلغ شارژ باید بین {min_amount:,} تا {max_amount:,} تومان باشد.'),
        ('transactions_empty', '📋 هنوز تراکنشی ندارید.'),
        ('transactions_title', '📋 تراکنش\u200cهای اخیر:\n\n'),
        ('back', '🔙 بازگشت'),
        ('main_back', '🏠 بازگشت به منوی اصلی'),
    ],
    '💳 شارژ کیف پول': [
        ('charge_choose_amount', '💳 مبلغ شارژ را انتخاب کنید:'),
        ('charge_50000', '💰 ۵۰,۰۰۰ تومان'),
        ('charge_100000', '💰 ۱۰۰,۰۰۰ تومان'),
        ('charge_200000', '💰 ۲۰۰,۰۰۰ تومان'),
        ('charge_custom', '💵 مبلغ دلخواه'),
        ('charge_custom_prompt', '💵 مبلغ دلخواه را به تومان ارسال کنید:'),
        ('only_number', '❌ فقط عدد ارسال کنید.'),
        ('online_min_amount', '❌ برای مبالغ ۵۰ هزار تومان و کمتر امکان استفاده از درگاه پرداخت آنلاین نیست. لطفاً از کارت\u200cبه\u200cکارت یا کیف پول استفاده کنید.'),
        ('wallet_pay_online', '🌐 پرداخت آنلاین (تایید خودکار)'),
        ('wallet_pay_card', '💳 پرداخت کارت به کارت'),
        ('wallet_online_pay', '💳 پرداخت (کارت به کارت خودکار)'),
        ('wallet_check_pay', '✅ پرداخت را انجام دادم / بررسی کن'),
        ('wallet_cancel', '🔙 انصراف'),
        ('charge_problem', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی شارژ شروع کنید.'),
        ('charge_receipt_expired', '⏰ مهلت ۳۰ دقیقه\u200cای پرداخت این فاکتور به پایان رسیده و به\u200cطور خودکار منقضی شد. لطفاً دوباره از منوی شارژ شروع کنید.'),
        ('charge_receipt_registered', '✅ رسید ثبت شد. پس از تأیید ادمین، کیف پول شما شارژ می\u200cشود.'),
        ('invoice_wallet_card', '🟩⬜️ مرحله 2 از 2\n\n💳 شارژ کیف پول\n\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید.'),
    ],
    '🛒 خرید اشتراک': [
        ('plans_intro', '🛒 **خرید اشتراک**\n\n🚀 **سرور VIP (V2Ray)**\nفیلترشکن پرسرعت و پایدار؛ مناسب وب‌گردی با IP ثابت.\n✅ حتی در «اینترنت ملی» بدون قطعی\n\nلطفاً سرویس مورد نظر خود را از منوی زیر انتخاب کنید 👇'),
        ('vip_intro', 'سرویس‌های VIP (V2Ray) 🌐\n\nیکی از دسته‌ها را انتخاب کنید 👇'),
        ('free_test_page', '🎁 {plan_name}\n💰 قیمت: {price:,} تومان\n👛 موجودی کیف پول شما: {wallet:,} تومان\n\nروش پرداخت را انتخاب کنید:'),
        ('plan_payment_page', '🛒 {plan_name}\n💰 قیمت: {price:,} تومان{note}\n👛 موجودی کیف پول شما: {wallet:,} تومان\n\nروش پرداخت را انتخاب کنید:'),
        ('plan_payment_service_name', '🔤 نام سرویس: {service_name}'),
        ('wallet_purchase_success', '✅ پرداخت شما ثبت شد. سفارش شما در صف ارسال سرویس قرار گرفت.'),
        ('online_plan_invoice', '🌐 پرداخت آنلاین (کارت\u200cبه\u200cکارت خودکار)\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\nروی دکمه\u200cی «پرداخت» بزنید، مبلغ را واریز کنید، سپس همینجا روی «بررسی کن» بزنید.\n⏱ به\u200cمحض تأیید بانک، سفارش شما به\u200cطور خودکار ثبت می\u200cشود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است. اگر تا این مهلت پرداخت تایید نشود، به\u200cطور خودکار منقضی و حذف خواهد شد.'),
        ('card_receipt_registered', '✅ رسید شما ثبت شد. پس از تأیید، سفارش شما در صف ارسال سرویس قرار می\u200cگیرد.'),
        ('plans_back', '🔙 بازگشت'),
        ('vip_category_empty', '😔 فعلاً هیچ دسته\u200cای موجود نیست'),
        ('vip_category_back', '🔙 بازگشت'),
        ('vip_plans_empty', '😔 فعلاً هیچ پلنی در این دسته نیست'),
        ('vip_plans_back', '🔙 بازگشت به دسته\u200cبندی\u200cها'),
        ('pay_wallet', '👛 پرداخت از کیف پول'),
        ('pay_online', '🌐 پرداخت آنلاین (تایید خودکار)'),
        ('pay_card', '💳 پرداخت کارت به کارت'),
        ('pay_crypto', '💱 پرداخت ارزی'),
        ('pay_discount', '🎟 ثبت کد تخفیف'),
        ('pay_back', '🔙 بازگشت'),
        ('online_pay', '💳 پرداخت (کارت به کارت خودکار)'),
        ('online_check', '✅ پرداخت را انجام دادم / بررسی کن'),
        ('online_cancel', '🔙 انصراف'),
        ('insufficient_charge', '💵 شارژ کیف پول'),
        ('insufficient_back', '🔙 بازگشت'),
        ('common_start_required', 'ابتدا دستور /start را بزنید.'),
        ('orders_closed', '🔴 ربات به دلیل حجم سفارشات بالا موقتاً بسته می\u200cباشد.'),
        ('plan_not_found', '❌ این پلن یافت نشد.'),
        ('category_not_found', '❌ این دسته یافت نشد.'),
        ('processing_request', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
        ('invalid_request', '❌ درخواست نامعتبر است.'),
        ('payment_not_active', 'این روش پرداخت در حال حاضر فعال نیست.'),
        ('building_payment', '⏳ در حال ساخت لینک پرداخت...'),
        ('checking_payment', '⏳ در حال بررسی وضعیت پرداخت...'),
        ('payment_owned', '⛔️ این پرداخت متعلق به شما نیست.'),
        ('payment_already_confirmed', '✅ این پرداخت قبلاً تأیید شده است.'),
        ('payment_problem', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('invoice_expired_wait', '⏰ مهلت ۳۰ دقیقه\u200cای پرداخت این فاکتور به پایان رسیده و به\u200cطور خودکار منقضی شد. لطفاً دوباره از منوی سرویس\u200cها سفارش تان را ثبت کنید.'),
        ('receipt_photo_only', '📸 لطفاً عکس رسید پرداخت را ارسال کنید (نه متن).'),
        ('receipt_registered', '✅ رسید ثبت شد. پس از تأیید ادمین، نتیجه به شما اطلاع داده می\u200cشود.'),
        ('invoice_plan_card', '🟩🟩⬜️ مرحله 2 از 3\n\n💳 پرداخت کارت به کارت\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید.'),
        ('free_test_processing', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
        ('free_test_payment', 'روش پرداخت را انتخاب کنید:'),
        ('free_test_soon', '🎁 تست رایگان به\u200cزودی فعال می\u200cشود! منتظر باشید.'),
    ],
    '🎟 کد تخفیف': [
        ('discount_enter', '🎟 کد تخفیف خود را وارد کنید:'),
        ('discount_cancel', '🔙 انصراف'),
        ('discount_prompt', '🎟 کد تخفیف خود را وارد کنید:'),
        ('discount_invalid', '❌ کد تخفیف نامعتبر یا تمام شده.'),
        ('discount_forbidden', '❌ شما مجاز به استفاده از این کد تخفیف نیستید.'),
        ('discount_limit', '❌ سهمیه\u200cی استفاده\u200cی شما از این کد تمام شده.'),
        ('discount_fixed_note', '💡 این کد یک کد تخفیف با مبلغ ثابت است؛ لطفاً از منوی «🛒 خرید اشتراک» پلن مورد نظرتان را انتخاب کنید و در صفحه\u200cی پرداخت همان پلن، کد را وارد کنید.'),
        ('discount_success', '✅ کد تخفیف {percent}٪ با موفقیت ثبت شد و در خرید بعدی شما (در صورت تطابق پلن) اعمال می\u200cشود.{plans_note}'),
        ('invalid_discount', '❌ کد تخفیف نامعتبر است.'),
        ('free_test_used', '⚠️ شما قبلاً از «تست رایگان» استفاده کرده\u200cاید. هر کاربر فقط یک\u200cبار می\u200cتواند این پلن را دریافت کند.'),
        ('wallet_insufficient', '❌ موجودی کیف پول کافی نیست!\n\n💰 قیمت: {price:,} تومان\n👛 موجودی: {wallet:,} تومان\n⚠️ کمبود: {needed:,} تومان'),
        ('wallet_not_enough', '❌ موجودی کافی نیست. ممکن است موجودی شما تغییر کرده باشد.'),
        ('purchase_receipt_error', '❌ مشکلی پیش آمد، لطفاً دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('plan_action_error', '❌ مشکلی پیش آمد، دوباره از منوی سرویس\u200cها شروع کنید.'),
        ('request_processing_short', '⚠️ این درخواست در حال پردازش/ثبت\u200cشده است.'),
    ],
    '📦 سرویس\u200cهای من': [
        ('configs_empty', '📱 شما هنوز هیچ سرویسی خریداری نکرده\u200cاید.\n\nبرای خرید، از «🛒 خرید اشتراک» اقدام کنید.'),
        ('configs_has', '📱 سرویس‌های شما\n\nکدوم دسته رو می‌خوای ببینی؟ 👇'),
        ('my_configs_empty', '📱 شما هنوز هیچ سرویسی خریداری نکرده\u200cاید.\n\nبرای خرید، از «🛒 خرید اشتراک» اقدام کنید.'),
        ('my_configs_has', '📱 سرویس‌های شما\n\nکدوم دسته رو می‌خوای ببینی؟ 👇'),
        ('vip_configs_empty', 'شما هنوز هیچ سرویس VIPی خریداری نکرده\u200cاید.'),
        ('vip_configs_has', 'سرویس‌های VIP شما\n\nبرای مشاهده‌ی لینک سابسکریپشن و مدیریت هرکدام، روی نام آن بزنید 👇'),
        ('service_detail_text', '📦 {plan}\n{live_status}\n\n📊وضعیت مصرف (لحظه‌ای):\n💿 حجم کل: {total}\n📲 مصرف‌شده: {used}\n📱 باقی‌مانده: {remaining}\n\n{bar} {percent}٪ مصرف شده\n\n⏰ تاریخ انقضا: {expiry}\n{expiry_status}\n\n🔗 این لینک ساب (Subscription) شماست؛ می‌توانید کانفیگ‌های خودتان را از داخل آن بردارید و حجم مصرفی‌تان را مدیریت کنید:\n\n{link}\n\n📆 تاریخ خرید: {purchase_date}'),
        ('config_detail_error', '❌ خطا در نمایش جزئیات سرویس. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'),
        ('config_status_title', '📊 وضعیت مصرف (لحظه\u200cای):'),
        ('config_total', '   • حجم کل: {value}'),
        ('config_used', '   • مصرف\u200cشده: {value}'),
        ('config_remaining', '   • باقی\u200cمانده: {value}'),
        ('config_percent', '{bar} {percent}٪ مصرف شده'),
        ('config_expiry', '⏰ تاریخ انقضا: {value}'),
        ('config_expired', '⛔️ منقضی شده'),
        ('config_days_left', '⌛️ زمان باقی\u200cمانده: {days} روز'),
        ('config_subscription_help', '🔗 این لینک ساب (Subscription) شماست؛ می\u200cتوانید کانفیگ\u200cهای خودتان را از داخل آن بردارید و حجم مصرفی\u200cتان را مدیریت کنید:'),
        ('config_purchase_date', '📆 تاریخ خرید: {date}'),
        ('config_delivery_error', '❌ ارسال یکی از کانفیگ\u200cها با خطا مواجه شد. لطفاً با پشتیبانی تماس بگیرید.'),
        ('configs_vip', 'سرویس\u200cهای VIP من'),
        ('configs_back', '🏠 بازگشت به منوی اصلی'),
        ('config_qr', '🖼 مشاهده کیوآرکد'),
        ('config_sub', '🔗 لینک اشتراک'),
        ('config_refresh', '🔄 بروزرسانی اطلاعات'),
        ('config_name_auto', '🤖 انتخاب خودکار نام'),
        ('config_name_back', '🔙 بازگشت به مرحله قبل'),
        ('service_search', '🔎 جستجوی سرویس'),
        ('service_search_cancel', '🔙 بازگشت'),
        ('config_name_prompt', '🔤 یک نام برای سرویس انتخاب کنید.\n\nفقط حروف انگلیسی، عدد و _ مجاز است.'),
        ('config_name_invalid', '❌ نام باید ۳ تا ۳۲ کاراکتر و فقط شامل حروف انگلیسی، عدد و _ باشد.\n\nیک اسم دیگر بفرست یا «انتخاب خودکار نام» را بزن:'),
        ('config_name_duplicate', '❌ نام تکراری است. لطفاً یک اسم دیگر انتخاب کنید.'),
        ('config_name_panel_duplicate', '❌ این نام قبلاً در پنل وجود دارد. لطفاً یک اسم دیگر انتخاب کنید.'),
        ('config_name_back_message', '🔙 به انتخاب پلن برگشتید.'),
        ('service_search_prompt', '🔎 بخشی از اسم سرویس را وارد کنید تا بین سرویس‌های شما جستجو کنم.\n\nمثلاً: `520120` یا `Config`'),
        ('service_search_empty_input', '❌ لطفاً بخشی از نام سرویس را وارد کنید.'),
        ('service_search_empty', '🔎 برای «{query}» هیچ سرویسی پیدا نشد.'),
        ('service_search_found', '🔎 {count} سرویس برای «{query}» پیدا شد:\n\n👇 برای مشاهده جزئیات انتخاب کنید.'),
        ('duration_30', 'یک ماهه'),
        ('duration_60', 'دو ماهه'),
        ('duration_90', 'سه ماهه'),
        ('buy_service_button', '🛒 خرید سرویس'),
        ('config_mirror', '🔗 دریافت کانفیگ\u200cهای تکی'),
        ('config_mirror_disabled', '🔗 برای اتصال فقط لینک Subscription را مستقیماً داخل برنامه وارد کنید.'),
        ('config_back', '🔙 بازگشت به سرویس\u200cهای VIP من'),
        ('service_not_found', '❌ سرویس یافت نشد.'),
        ('service_not_owned', '❌ این سرویس متعلق به شما نیست یا یافت نشد.'),
        ('config_decode_error', '❌ خطا در رمزگشایی کانفیگ.'),
        ('subscription_missing', '❌ لینک ساب معتبری برای این سرویس ثبت نشده.'),
        ('service_loading', '⏳ در حال دریافت اطلاعات مصرف...'),
        ('subscription_fetching', '⏳ در حال دریافت کانفیگ\u200cها از روی لینک ساب...'),
        ('subscription_unavailable', '❌ لینک ساب در حال حاضر در دسترس نیست. کمی بعد دوباره امتحان کنید.'),
        ('qr_missing', '❌ کیوآرکدی برای این سرویس ثبت نشده.'),
        ('qr_failed', '❌ ارسال کیوآرکد ناموفق بود.'),
    ('renew_menu_title', '🔁 تمدید سرویس'),
    ('renew_choose_service', '🔁 سرویس موردنظر برای تمدید را انتخاب کنید 👇'),
    ('renew_service_title', '🔁 تمدید سرویس «{service_name}»'),
    ('renew_service_button', '🔁 {service_name}'),
    ('renew_volume_prompt', '📦 مقدار حجمی که می‌خواهید به سرویس اضافه شود را انتخاب کنید:'),
    ('renew_volume_10', '۱۰ گیگ'),
    ('renew_volume_20', '۲۰ گیگ'),
    ('renew_volume_30', '۳۰ گیگ'),
    ('renew_volume_40', '۴۰ گیگ'),
    ('renew_volume_50', '۵۰ گیگ'),
    ('renew_days_30', '۳۰ روز'),
    ('renew_days_60', '۶۰ روز'),
    ('renew_days_90', '۹۰ روز'),
    ('renew_volume_custom', '➕ مقدار دلخواه'),
    ('renew_custom_volume_prompt', '📦 مقدار حجم اضافه را به گیگ وارد کنید (بیشتر از ۵۰):'),
    ('renew_days_prompt', '⏳ مقدار زمان اضافه را انتخاب کنید:'),
    ('renew_days_custom', '➕ زمان دلخواه'),
    ('renew_custom_days_prompt', '⏳ تعداد روز اضافه را وارد کنید:'),
    ('renew_summary', '🧾 تمدید سرویس «{service_name}»\n\n📦 حجم اضافه: {volume}\n⏳ زمان اضافه: {days}\n💰 مبلغ: {price:,} تومان\n\nروش پرداخت را انتخاب کنید:'),
    ('renew_card_invoice', '🟩🟩⬜️ مرحله 2 از 3\n\n💳 پرداخت کارت به کارت\n\n🔁 تمدید سرویس: {service_name}\n📦 حجم اضافه: {volume}\n⏳ زمان اضافه: {days}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید.'),
    ('renew_card_registered', '✅ رسید تمدید ثبت شد. پس از تأیید ادمین، حجم/زمان جدید به سرویس شما اضافه می‌شود.'),
    ('renew_approved', '✅ تمدید سرویس شما تأیید شد و تغییرات روی سرویس اعمال می‌شود.'),
    ('renew_details_title', 'جزئیات تمدید'),
    ('renew_volume_only_detail', 'حجم\n{previous_volume}  ⬅️  {added_volume}  ⬅️  {new_volume}'),
    ('renew_days_only_detail', 'مدت زمان\n{previous_days}  ⬅️  {added_days}  ⬅️  {new_days}'),
    ('renew_volume_days_detail', 'حجم\n{previous_volume}  ⬅️  {added_volume}  ⬅️  {new_volume}\n\nمدت زمان\n{previous_days}  ⬅️  {added_days}  ⬅️  {new_days}'),
    ('renew_details_empty', 'تغییری برای تمدید ثبت نشده است.'),
    ('renew_done', 'تمدید سرویس «{service_name}» با موفقیت انجام شد.\n\n{details}\n\n✨ تمدید با موفقیت روی سرویس شما اعمال شد.'),
    ('renew_cancelled', '🔙 عملیات تمدید لغو شد و هیچ تغییری در تنظیمات سرویس ایجاد نشد.'),
    ('service_live_active', '🟢 فعال'),
    ('service_live_expired', '🔴 منقضی'),
    ('notif_fair_use', '⚖️ هشدار مصرف منصفانه\n\n📦 {plan}\n\nشما به سقف مصرف منصفانه {fair_use_gb} گیگابایت رسیدید.\nبرای ادامه، یکی از گزینه‌های زیر را انتخاب کنید:'),
    ('fair_use_continue', '⚖️ استفاده از حجم منصفانه'),
    ('fair_use_buy_new', '🛒 خرید سرویس جدید'),
    ('fair_use_selected', '✅ درخواست استفاده از حجم منصفانه برای سرویس شما ثبت شد و به ادمین اطلاع داده شد.'),
    ('notif_left_required', '⚠️ عضویت شما در کانال اجباری لغو شده است.\n\nبرای ادامه استفاده از ربات، دوباره در کانال‌های زیر عضو شوید و سپس «عضویت را بررسی کن» را بزنید 👇'),
    ('crypto_not_configured', '❌ پرداخت ارزی هنوز توسط ادمین تنظیم نشده است.'),
    ('crypto_asset_unavailable', '❌ این ارز در حال حاضر در دسترس نیست.'),
    ],
    '⚙️ عملیات سرویس': [
        ('config_enable', '▶️ فعال\u200cسازی سرویس'),
        ('config_disable', '⏸ غیرفعال\u200cسازی سرویس'),
        ('config_revoke', '🔄 ساخت لینک ساب جدید'),
        ('config_delete', '🗑 حذف سرویس'),
        ('confirm_delete_yes', '✅ بله، حذف کن'),
        ('confirm_delete_no', '❌ انصراف'),
        ('confirm_disable_yes', '✅ بله، غیرفعال کن'),
        ('confirm_disable_no', '❌ انصراف'),
        ('confirm_revoke_yes', '✅ بله، لینک جدید بساز'),
        ('confirm_revoke_no', '❌ انصراف'),
        ('config_disabled', '❌ این سرویس متعلق به شما نیست یا امکان غیرفعال\u200cسازی خودکار ندارد.'),
        ('config_disabling', '⏳ در حال غیرفعال\u200cسازی...'),
        ('config_enabling', '⏳ در حال فعال\u200cسازی...'),
        ('config_revoking', '⏳ در حال ساخت لینک ساب جدید...'),
        ('service_delete_confirm', '⚠️ مطمئنی می\u200cخوای «{plan}» رو حذف کنی؟\n\nاین سرویس از لیست «سرویس\u200cهای من» شما پاک می\u200cشه (ولی اطلاعاتش نزد پشتیبانی می\u200cمونه).'),
        ('service_deleted', '✅ سرویس حذف شد.'),
        ('service_disable_not_available', '❌ این سرویس متعلق به شما نیست یا امکان غیرفعال\u200cسازی خودکار ندارد.'),
        ('service_disable_confirm', '⚠️ مطمئنی می\u200cخوای سرویس رو غیرفعال کنی؟\n\nبعد از غیرفعال\u200cسازی، این سرویس دیگه وصل نمی\u200cشه تا دوباره فعالش کنی.'),
        ('service_disable_failed', '❌ غیرفعال\u200cسازی ناموفق بود: {msg}'),
        ('service_disabled', '🚫 سرویس غیرفعال شد.'),
        ('service_enable_failed', '❌ فعال\u200cسازی ناموفق بود: {msg}'),
        ('service_enabled', '✅ سرویس دوباره فعال شد.'),
        ('service_revoke_not_available', '❌ این سرویس متعلق به شما نیست یا امکان تعویض خودکار لینک ندارد.'),
        ('service_revoke_confirm', '⚠️ مطمئنی می\u200cخوای لینک ساب عوض شه؟\n\nبعد از تعویض، لینک قبلی دیگه کار نمی\u200cکنه و باید لینک جدید رو دوباره داخل اپ خودت وارد کنی.'),
        ('service_revoke_failed', '❌ ساخت لینک ساب جدید ناموفق بود: {msg}'),
        ('service_revoke_missing', '⚠️ لینک ساب جدید در پاسخ پنل پیدا نشد. با پشتیبانی تماس بگیر.'),
        ('service_revoke_done', '✅ لینک ساب جدید ساخته شد؛ برای دیدنش وارد جزئیات سرویس شو.'),
        ('config_back_service', '🔙 بازگشت به سرویس'),
    ],
    '👥 دعوت دوستان': [
        ('referral_overview', '👥 دعوت دوستان و کسب درآمد 💸\n\nدوستانتو دعوت کن و به\u200cازای هر دعوت موفق، {reward:,} تومان پاداش نقدی بگیر! 🎁\nکافیه لینک اختصاصی\u200cت رو برای دوستات، گروه\u200cها یا کانال\u200cهایی که توشون عضوی بفرستی.\n\n🔗 لینک اختصاصی شما:\n{invite_link}\n\n🔑 کد اختصاصی: {invite_code}\n\n👤 تعداد دعوت: {invited_count}\n✅ دعوت\u200cهای موفق: {successful_invites}\n🔓 مبلغ آزاد شده: {released:,} تومان\n🔒 مبلغ در انتظار: {locked:,} تومان\n\nℹ️ به\u200cازای هر دوستی که با لینک شما عضو شود و یک خرید حجم {min_gb} گیگ یا بیشتر انجام دهد، {reward:,} تومان به\u200cصورت خودکار و بدون نیاز به هیچ اقدام دیگری به کیف پول شما آزاد می\u200cشود. (تست رایگان و خریدهای کمتر از {min_gb} گیگ پاداش را آزاد نمی\u200cکنند)\n\n⚠️ لطفاً فقط لینک را برای افراد واقعی ارسال کنید؛ استفاده از اکانت\u200cهای فیک تقلب محسوب شده و جایزه شما لغو می\u200cشود.'),
        ('referral_back', '🏠 بازگشت به منوی اصلی'),
    ],
    '👨\u200d💻 پشتیبانی و نمایندگی': [
        ('support_intro', '👨‍💻 پشتیبانی\n\nاگر به هر چالشی برخورد کردید که از طریق راهنما ربات هم براتون حل نشد،\nمی‌تونید مستقیم تیکت بزنید یا از ارتباط با پشتیبان استفاده کنید 👇'),
        ('support_ticket', '🎫 ارسال تیکت'),
        ('support_channels', '📢 کانال اصلی و پشتیبان'),
        ('support_back', '🏠 بازگشت به منوی اصلی'),
        ('support_error', '❌ خطایی در نمایش منوی پشتیبانی پیش آمد (احتمالاً لینک پشتیبانی در تنظیمات نامعتبر است). لطفاً دوباره تلاش کنید یا به ادمین اطلاع بدهید.'),
        ('ticket_write', '✍️ پیام خود را برای پشتیبانی بنویسید:'),
        ('ticket_sent', '✅ پیام شما برای پشتیبانی ارسال شد. به\u200cزودی پاسخ داده می\u200cشود.'),
        ('ticket_reply_sent', '✅ پاسخ ارسال شد.'),
        ('ticket_reply_failed', '❌ ارسال پاسخ ناموفق بود (شاید کاربر ربات را بلاک کرده).'),
        ('agency_intro', '🤝 درخواست نمایندگی\n\nدرخواست و مشخصات خودتون (اسم، شماره تماس، میزان فعالیت/تعداد مشتری تقریبی و توضیحات) رو در یک پیام بنویسید و ارسال کنید؛ مستقیم برای پشتیبانی فرستاده می\u200cشه و به\u200cزودی بررسی و پاسخ داده می\u200cشه 👇'),
        ('agency_cancel', '🔙 انصراف'),
        ('agency_invalid', '❌ لطفاً درخواستتون رو به\u200cصورت متن ارسال کنید:'),
        ('agency_sent', '✅ درخواست شما برای پشتیبانی ارسال شد. به\u200cزودی بررسی و باهاتون تماس گرفته می\u200cشه.'),
    ],
    '📚 راهنما': [
        ('guides_back', '🏠 بازگشت به منوی اصلی'),
        ('guide_detail_back', '🔙 بازگشت به لیست راهنما'),
        ('guide_missing', '❌ این راهنما دیگر موجود نیست.'),
        ('guides_empty', '📚 راهنما و اموزش\u200cها\n\nهنوز هیچ راهنمایی ثبت نشده. به\u200cزودی محتوای آموزشی اینجا قرار می\u200cگیرد.'),
        ('guides_intro', '📚 راهنما و اموزش\u200cها\n\nیکی از موارد زیر را برای مشاهده انتخاب کنید 👇'),
    ],
    '🚀 شروع و عضویت اجباری': [
        ('join_confirm', '✅ عضو شدم'),
        ('start_join_required', '⚠️ برای استفاده از ربات ابتدا در کانال\u200cهای زیر عضو شوید:'),
        ('start_blocked', '🚫 دسترسی شما به ربات مسدود شده است. در صورت وجود ابهام با پشتیبانی در ارتباط باشید.'),
        ('start_admin_welcome', '👨\u200d💻 به پنل مدیریت خوش آمدید!\n\nهمه\u200cی امکانات مدیریتی از منوی پایین صفحه قابل دسترسی است ✅'),
        ('start_join_not_done', '❌ هنوز در همه کانال\u200cها عضو نشدید!'),
        ('start_blocked_short', '🚫 دسترسی شما به ربات مسدود شده است.'),
        ('start_join_confirmed', 'منوی اصلی در پایین صفحه فعال شد ✅'),
        ('start_back_admin', '👨\u200d💻 بازگشت به منوی اصلی — از منوی پایین صفحه ادامه دهید ✅'),
        ('start_back_user', '👋 بازگشت به منوی اصلی — از منوی پایین صفحه ادامه دهید ✅'),
    ],
    '🔔 اعلان\u200cها': [
        ('notif_wallet_charge_approved', '✅ شارژ {amount:,} تومانی شما تأیید شد.'),
        ('notif_wallet_charged', '✅ کیف پول شما {amount:,} تومان شارژ شد.'),
        ('notif_purchase_approved', '✅ پرداخت شما تأیید شد!\n\n📦 {plan_name}\nسرویس شما به\u200cزودی ارسال می\u200cشود.{discount_note}'),
        ('notif_receipt_rejected_short', '❌ متأسفانه رسید شما تأیید نشد. با پشتیبانی تماس بگیرید.'),
        ('notif_free_test_used_admin', '⚠️ این کاربر قبلاً از «تست رایگان» استفاده کرده؛ هر کاربر فقط یک\u200cبار می\u200cتواند این پلن را بگیرد.'),
        ('notif_service_delivery', '📦 سرویس شما آماده شد ⬇️'),
        ('notif_receipt_approved', '✅ رسید پرداخت شما تأیید شد.'),
        ('notif_receipt_rejected', '❌ متأسفانه رسید پرداخت شما تأیید نشد. با پشتیبانی تماس بگیرید.'),
        ('notif_usage_80', '🔔 هشدار حجم مصرفی سرویس\n\n📦 {plan}\n\n{bar}\n✅ شما تا الان {percent}٪ از حجم سرویستون رو مصرف کردید.\n\nبرای جلوگیری از قطعی سرویس، پیشنهاد می\u200cکنیم همین الان تمدید کنید 🔁'),
        ('notif_usage_90', '🔔 هشدار حجم مصرفی سرویس\n\n📦 {plan}\n\n{bar}\n⚠️ شما تا الان {percent}٪ از حجم سرویستون رو مصرف کردید.\n\nبرای جلوگیری از قطعی سرویس، پیشنهاد می\u200cکنیم همین الان تمدید کنید 🔁'),
        ('notif_renew_service_button', 'تمدید همین سرویس'),
        ('notif_buy_new_service_button', 'خرید سرویس جدید'),
        ('notif_expiry', '❌ سرویس شما به پایان رسید\n\n📦 {plan}\n\n🔴 حجم یا زمان سرویس شما تمام شده و این سرویس منقضی شده است.\n\nبرای ادامه استفاده از این سرویس، می‌توانید همین سرویس را تمدید کنید یا سرویس جدید بخرید.'),
        ('orders_opened', '🟢 ربات مجدداً فعال شد!'),
        ('notif_orders_closed_suffix', 'روشن شدن دوباره\u200cی آن اطلاع\u200cرسانی خواهد شد.'),
        ('notif_orders_opened_suffix', 'با زدن /start می\u200cتوانید دوباره سفارش ثبت کنید.'),
        ('notif_view_service', '📦 مشاهده سرویس'),
    ],
})


# 🛠 متن‌های بخش «بساز سرویس خودت» — قابل ویرایش از همان پنل مدیریت متن
TEXT_CATEGORIES.setdefault("custom_build", []).extend([
    ("plans_vip_button", "سرور VIP (V2Ray)"),
    ("plans_custom_button", "🛠 سرویس خودت رو بساز"),
    ("custom_pay_wallet", "👛 پرداخت از کیف پول"),
    ("custom_pay_online", "🌐 پرداخت آنلاین (تایید خودکار)"),
    ("custom_pay_card", "💳 پرداخت کارت به کارت"),
    ("custom_cancel", "🔙 انصراف"),
    ("admin_custom_approve", "✅ تأیید پرداخت"),
    ("admin_custom_reject", "❌ رد رسید"),
    ("admin_custom_send_manual", "📤 شروع ارسال کانفیگ — دستی"),
    ("custom_build_title", "🛠 سرویس خودت رو بساز"),
    ("custom_build_volume_prompt", "📦 حجم موردنظر را به گیگابایت ارسال کنید:"),
    ("custom_build_days_prompt", "⏳ مدت سرویس را به روز ارسال کنید:"),
    ("custom_build_name_prompt", "🔤 یک نام انگلیسی برای سرویس ارسال کنید:"),
    ("custom_build_summary", "🧾 خلاصه سفارش"),
    ("custom_payment_approved", "✅ پرداخت شما تأیید شد!\nسرویس شما به‌زودی ساخته و ارسال می‌شود."),
])


# 🎛 دکمه‌های ثابت بخش کاربر که متن آن‌ها قبلاً مستقیماً در keyboards.py نوشته
# می‌شد. حالا همه‌ی این دکمه‌ها هم از پنل «مدیریت متن‌های کاربر» قابل ویرایش‌اند
# و اگر ادمین داخل متن جدید Premium/Custom Emoji بگذارد، همان Emoji به‌عنوان
# آیکن دکمه استفاده می‌شود.
TEXT_CATEGORIES.setdefault("🎛 دکمه‌های کاربر", []).extend([
    ("crypto_choose_asset", "🔙 انتخاب ارز"),
    ("crypto_asset_ton", "🟣 TON"),
    ("crypto_asset_trx", "🔴 TRX"),
    ("crypto_asset_usdt", "🟢 USDT (TRC20)"),
    ("crypto_receipt_hint", "📨 ارسال رسید / Hash"),
    ("renew_pay_card", "💳 پرداخت کارت به کارت"),
    ("renew_pay_crypto", "💱 پرداخت ارزی"),
    ("renew_pay_back", "🔙 بازگشت"),
    ("renew_cancel", "❌ لغو تمدید"),
    ("renew_volume_10", "۱۰ گیگ"),
    ("renew_volume_20", "۲۰ گیگ"),
    ("renew_volume_30", "۳۰ گیگ"),
    ("renew_volume_40", "۴۰ گیگ"),
    ("renew_volume_50", "۵۰ گیگ"),
    ("renew_volume_choice", "💾 {value} گیگ"),
    ("renew_days_30", "۳۰ روز"),
    ("renew_days_60", "۶۰ روز"),
    ("renew_days_90", "۹۰ روز"),
    ("renew_days_choice", "⏱ {value} روز"),
    ("renew_day_options", "گزینه‌های روز"),
    ("renew_gb_options", "گزینه‌های گیگ"),
    ("renew_card_invoice", "🟩🟩⬜️ مرحله 2 از 3\n\n💳 پرداخت کارت به کارت\n\n🔁 تمدید سرویس: {service_name}\n📦 حجم اضافه: {volume}\n⏳ زمان اضافه: {days}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\n💳 شماره کارت:\n{card_number}\n\n👤 به نام: {card_holder}\n\n📸 پس از واریز، عکس رسید پرداخت یا 📝 متن رسید را همینجا ارسال کنید."),
])

# 💳 متن‌های پرداخت و فاکتورها — همه از پنل «مدیریت متن‌های کاربر» قابل ویرایش‌اند.
TEXT_CATEGORIES.setdefault("💳 پرداخت و فاکتورها", []).extend([
    ("invoice_card_expiry", "⏱ این شماره کارت و قیمت تا ساعت {deadline} (۳۰ دقیقه) معتبر است. لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."),
    ("invoice_wallet_expiry", "⏱ این شماره کارت و مبلغ تا ساعت {deadline} (۳۰ دقیقه) معتبر است. لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."),
    ("invoice_custom_expiry", "⏱ این شماره کارت و قیمت تا ساعت {deadline} (۳۰ دقیقه) معتبر است. لطفاً تا این ساعت رسید پرداخت را ارسال کنید، وگرنه این فاکتور به‌طور خودکار منقضی و حذف می‌شود."),
    ("online_wallet_invoice", "🌐 پرداخت آنلاین (کارت‌به‌کارت خودکار)\n\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\nروی دکمه‌ی «پرداخت» بزنید، مبلغ را واریز کنید، سپس همینجا روی «بررسی کن» بزنید.\n⏱ به‌محض تأیید بانک، کیف پول شما به‌طور خودکار شارژ می‌شود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است — تا ساعت {deadline}. اگر تا این مهلت پرداخت تایید نشود، به‌طور خودکار منقضی و حذف خواهد شد."),
    ("online_custom_invoice", "🌐 پرداخت آنلاین ({gateway})\n\n🧩 سرویس سفارشی — {volume} گیگ / {days} روز\n💰 مبلغ قابل پرداخت: {price:,} تومان\n\nروی دکمه‌ی «پرداخت» بزنید و پرداخت را تکمیل کنید. سپس روی «بررسی پرداخت» بزنید.\n⏱ پس از تأیید پرداخت، سفارش شما خودکار ثبت می‌شود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است."),
    ("online_plan_invoice_generic", "🌐 پرداخت آنلاین ({gateway})\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\nروی دکمه‌ی «پرداخت» بزنید و پرداخت را تکمیل کنید. سپس روی «بررسی کن» بزنید.\n⏱ پس از تأیید پرداخت، سفارش شما خودکار ثبت می‌شود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است — تا ساعت {deadline}."),
    ("online_wallet_invoice_generic", "🌐 پرداخت آنلاین ({gateway})\n\n💰 مبلغ قابل پرداخت: {amount:,} تومان\n\nروی دکمه‌ی «پرداخت» بزنید و پرداخت را تکمیل کنید. سپس روی «بررسی کن» بزنید.\n⏱ پس از تأیید پرداخت، کیف پول شما خودکار شارژ می‌شود.\n\n⚠️ این فاکتور تا ۳۰ دقیقه دیگر معتبر است — تا ساعت {deadline}."),
    ("online_payment_create_failed", "❌ ساخت لینک پرداخت آنلاین ناموفق بود. لطفاً روش پرداخت دیگری را انتخاب کنید."),
    ("online_payment_not_paid", "⏳ هنوز پرداختی برای این فاکتور ثبت نشده. اگر همین الان پرداخت کردید، چند لحظه صبر کنید و دوباره بزنید."),
    ("online_payment_success_order", "✅ پرداخت آنلاین شما تأیید شد و سفارش شما ثبت گردید. سرویس شما به‌زودی ارسال می‌شود."),
    ("online_payment_success_wallet", "✅ پرداخت آنلاین شما تأیید شد و کیف پول شما شارژ شد."),
    ("online_payment_button", "💳 پرداخت آنلاین"),
    ("online_payment_check", "✅ بررسی پرداخت"),
    ("online_payment_cancel", "🔙 انصراف"),
    ("crypto_payment_intro", "💱 پرداخت ارزی\n\n🛒 {plan_name}\n💰 قیمت سرویس: {price:,} تومان\n⏱ اعتبار این فاکتور: ۳۰ دقیقه — تا ساعت {deadline}\n\nتعرفه لحظه‌ای پرداخت:\n{available_rows}{stale_note}\n\nارز موردنظر را انتخاب کن تا آدرس کیف پول و جزئیات واریز نمایش داده شود."),
    ("crypto_payment_detail", "🟩🟩⬜️ مرحله 2 از 3\n\n💱 پرداخت ارزی\n\n🛒 {plan_name}\n💰 مبلغ قابل پرداخت: {price:,} تومان\n💵 مبلغ پرداختی: {amount} {asset}\n\n🌐 شبکه: {network}\n👛 ولت: برای دریافت آدرس و کپی، دکمه «📋 کپی ولت» را بزنید.\n\n📸 پس از واریز، عکس رسید پرداخت یا 📝 متن/Hash تراکنش را همینجا ارسال کنید.\n⏱ این فاکتور تا ساعت {deadline} (۳۰ دقیقه) معتبر است. پس از پایان مهلت، فاکتور به‌طور خودکار منقضی و حذف می‌شود."),
    ("crypto_choose_asset_intro", "💱 انتخاب ارز برای «{plan_name}»\n\n💰 {price:,} تومان\n\n{rows}"),
    ("crypto_receipt_hint_alert", "📸 عکس رسید یا 📝 متن/Hash تراکنش را همینجا ارسال کن."),
    ("invoice_copy_amount", "📋 کپی مبلغ به ریال"),
    ("invoice_copy_card", "💳 کپی شماره کارت"),
    ("crypto_copy_amount", "📋 کپی مبلغ به ریال"),
    ("crypto_copy_wallet", "👛 کپی ولت"),
])

# 📦 تحویل سرویس — متن و دکمه‌های تحویل بسته/تست هم از همین پنل قابل ویرایش‌اند.
TEXT_CATEGORIES.setdefault("📦 تحویل سرویس", []).extend([
    # قالب تحویل واقعی سرویس؛ متن و دو دکمه از پنل قابل ویرایش‌اند.
    # {service_label} برای خریدهای VIP دقیقاً نام همان پلن خریداری‌شده است.
    ("service_delivery_text",
     "✅ سرویس شما با موفقیت تحویل داده شد\n\n"
     "👤 نام کاربری : {service_label}\n\n"
     "🔗 لینک کانفینگ شما:\n{link}\n\n"
     "📋 لینک را کپی کنید و داخل برنامه‌تان جایگذاری کنید.\n\n"
     "💝 پیام ادمین : ممنون از اعتماد شما\n\n"
     "برای دریافت اپلیکیشن یا آشنایی با نحوه متصل کردن کانفینگ، از دو گزینه زیر استفاده کنید 👇"),
    ("service_delivery_test_text",
     "🎁 تست رایگان شما با موفقیت تحویل داده شد\n\n"
     "👤 نام کاربری : {service_label}\n\n"
     "🔗 لینک کانفینگ شما:\n{link}\n\n"
     "📋 لینک را کپی کنید و داخل برنامه‌تان جایگذاری کنید.\n\n"
     "💝 پیام ادمین : مرسی از انتخاب شما\n\n"
     "برای دریافت اپلیکیشن یا آشنایی با نحوه متصل کردن کانفینگ، از دو گزینه زیر استفاده کنید 👇"),
    ("service_delivery_apps_button", "📱 دریافت اپلیکیشن"),
    ("service_delivery_connection_button", "🔧 نحوه اتصال کانفینگ"),
    ("service_delivery_test_apps_button", "📱 دریافت اپلیکیشن"),
    ("service_delivery_test_connection_button", "🔧 نحوه اتصال کانفینگ"),
     ("admin_delivery_summary", "👤 مشتری: {customer}\n🆔 Telegram ID: {telegram_id}\n👤 نام سرویس: {service_username}\nنام بسته: {package_name}\n💰 مبلغ: {amount:,} تومان\n⏰ زمان: {time} (به وقت تهران)"),
     ("admin_renew_card_receipt", "رسید تمدید سرویس\n\nمشتری: {customer}\nTelegram ID: {telegram_id}\nنام سرویس: {service_username}\nنام بسته: {package_name}\nدسته: {category_name}\nجزئیات تمدید: {renew_details}\nمبلغ: {amount:,} تومان"),

])


# 🎫 مدیریت تیکت — متن‌ها و دکمه‌ها از ویرایشگر و Premium/Custom Emoji پشتیبانی می‌کنند.
TEXT_CATEGORIES.setdefault("🎫 مدیریت تیکت", []).extend([
    ("admin_tickets", "مدیریت تیکت"),
    ("admin_tickets_open", "تیکت‌های باز ({count})"),
    ("admin_tickets_unanswered", "تیکت‌های پاسخ‌داده‌نشده ({count})"),
    ("admin_tickets_closed", "تیکت‌های بسته ({count})"),
    ("admin_ticket_reply", "پاسخ به تیکت"),
    ("admin_ticket_close", "بستن تیکت"),
    ("admin_ticket_reopen", "باز کردن تیکت"),
    ("admin_ticket_back", "بازگشت"),
    ("admin_ticket_reply_prompt", "پاسخ خود را برای این تیکت ارسال کنید:"),
    ("admin_ticket_not_found", "تیکت پیدا نشد یا دیگر قابل مدیریت نیست."),
    ("admin_ticket_closed_notice", "این تیکت بسته شد."),
    ("admin_ticket_reopened_notice", "این تیکت دوباره باز شد."),
    ("ticket_user_reply_prefix", "پاسخ پشتیبانی:"),
])


# 🗂 دکمه‌های مدیریت دسته‌بندی VIP — این متن‌ها نیز از سیستم ویرایش متن و Premium/Custom Emoji استفاده می‌کنند.
TEXT_CATEGORIES.setdefault("🗂 مدیریت دسته‌بندی VIP", []).extend([
    ("admin_vip_new_subcategory", "➕ زیر‌دسته جدید"),
    ("admin_vip_back_level", "🔙 بازگشت به سطح قبل"),
    ("admin_vip_new_root_category", "➕ دسته‌بندی اصلی جدید"),
    ("admin_vip_back", "🔙 بازگشت"),
    ("admin_vip_create_subcategory", "➕ ساخت زیر‌دسته (اختیاری)"),
    ("admin_vip_add_direct_plan", "➕ افزودن پلن مستقیم به این دسته"),
    ("admin_vip_disable_category", "🔴 غیرفعال کردن این دسته"),
    ("admin_vip_enable_category", "🟢 فعال کردن این دسته"),
    ("admin_vip_rename_category", "✏️ تغییر نام این دسته"),
    ("admin_vip_delete_category", "🗑 حذف این دسته"),
])

TEXT_CATEGORIES.setdefault('🧩 سایر متن‌ها', []).append(('free_test_registered', '✅ درخواست تست رایگان شما ثبت شد!\n\nسرویس شما به زودی ارسال میشود.\n\n❗️ این کانفینگ فقط جهت تست سرعت برای شما کاربر عزیز فراهم شده و کاربرد دیگه ای ندارد.'))

TEXT_CATEGORIES.setdefault("🛒 خرید اشتراک", []).extend([
    ("vip_category_title", "{category_name}:\n\nسرویس‌های VIP (V2Ray) 🌐\n\nیکی از دسته‌ها را انتخاب کنید 👇"),
    ("vip_plan_admin_detail", "📦 {plan_name}\n\n💰 قیمت: {price:,} تومان\n🗜 حجم: {volume} گیگ\n⏳ مدت: {days} روز\n👥 سقف کاربر: {user_limit}\n🗂 دسته: {category}"),
])

# 🛡️ مدیریت پنل پاسارگارد — همه متن‌های این بخش از ویرایشگر پشتیبانی می‌کنند.
TEXT_CATEGORIES.setdefault("🛡️ مدیریت پنل پاسارگارد", []).extend([
    ("admin_manage_pasargad", "🛡️ مدیریت پنل‌های پاسارگارد"),
    ("admin_panels_intro", "🛡️ مدیریت پنل‌های پاسارگارد\n\nهر پنل یک نمونه مستقل است."),
    ("admin_panel_add", "➕ افزودن پنل"),
    ("admin_panel_test", "🔌 تست اتصال"),
    ("admin_panel_enable", "✅ فعال کردن"),
    ("admin_panel_disable", "⛔ غیرفعال کردن"),
    ("admin_panel_edit", "✏️ ویرایش اطلاعات"),
    ("admin_panel_delete", "🗑 حذف پنل"),
    ("admin_panel_back_list", "🔙 لیست پنل‌ها"),
    ("admin_panel_name", "📝 نام پنل"),
    ("admin_panel_url", "🌐 آدرس پنل"),
    ("admin_panel_username", "👤 نام کاربری"),
    ("admin_panel_password", "🔑 رمز عبور"),
    ("admin_panel_name_prompt", "➕ نام پنل را بفرستید:"),
    ("admin_panel_url_prompt", "🌐 آدرس پایه پنل را بفرستید:"),
    ("admin_panel_username_prompt", "👤 نام کاربری پنل را بفرستید:"),
    ("admin_panel_password_prompt", "🔑 رمز عبور پنل را بفرستید:"),
    ("admin_panel_value_required", "❌ مقدار نمی‌تواند خالی باشد."),
    ("admin_panel_invalid_url", "❌ آدرس نامعتبر است."),
    ("admin_panel_edit_choose", "✏️ مشخصه موردنظر را انتخاب کنید:"),
    ("admin_panel_edit_value", "✏️ مقدار جدید را ارسال کنید:"),
    ("admin_panel_testing", "⏳ در حال تست اتصال..."),
    ("admin_panel_connection_ok", "✅ اتصال موفق بود."),
    ("admin_panel_saved_test_failed", "⚠️ پنل ذخیره شد ولی تست اتصال ناموفق بود:\n{msg}"),
    ("admin_panel_detail", "🛡️ {name}\n\nوضعیت: {status}\nآدرس: {url}\nروش اتصال: Username / Password"),
])

# 🛡️ پیام‌های مدیریتی رسید ارزی
TEXT_CATEGORIES.setdefault("💳 پرداخت و فاکتورها", []).extend([
    ("admin_crypto_receipt", "💱 رسید پرداخت ارزی\n\n👤 {name}\n🆔 {telegram_id}\n\n📦 بسته: {plan_name}\n💰 {price:,} تومان\n💵 {amount} {asset}\n📝 Hash/متن تراکنش: {hash_text}"),
    ("admin_crypto_receipt_photo", "💱 رسید پرداخت ارزی\n\n👤 {name}\n🆔 {telegram_id}\n\n📦 بسته: {plan_name}\n💰 {price:,} تومان\n💵 {amount} {asset}"),
     ("admin_crypto_renew_receipt", "رسید تمدید ارزی\n\nمشتری: {name}\nTelegram ID: {telegram_id}\nنام سرویس: {service_name}\nنام بسته: {plan_name}\nدسته: {category_name}\nجزئیات تمدید: {renew_details}\nمبلغ: {price:,} تومان\nمبلغ ارزی: {amount} {asset}"),
     ("admin_crypto_renew_receipt_photo", "رسید تمدید ارزی\n\nمشتری: {name}\nTelegram ID: {telegram_id}\nنام سرویس: {service_name}\nنام بسته: {plan_name}\nدسته: {category_name}\nجزئیات تمدید: {renew_details}\nمبلغ: {price:,} تومان\nمبلغ ارزی: {amount} {asset}"),
    ("admin_crypto_approve", "✅ تأیید پرداخت ارزی"),
    ("admin_crypto_reject", "❌ رد پرداخت ارزی"),
    ("admin_renew_approve", "تأیید تمدید"),
    ("admin_renew_reject", "رد تمدید"),
    ("crypto_receipt_invalid", "❌ لطفاً عکس رسید یا متن/Hash تراکنش را ارسال کنید."),
    ("admin_crypto_photo_only", "📸 رسید تصویری ارسال شده؛ Hash متنی ثبت نشده است."),
])

TEXTS = {key: default for items in TEXT_CATEGORIES.values() for key, default in items}
CATEGORY_BY_KEY = {key: category for category, items in TEXT_CATEGORIES.items() for key, _ in items}
_CACHE = {}


class RichText(str):
    """رشته‌ای که entityهای واقعی تلگرام را همراه خودش حمل می‌کند.

    برای متن‌های قابل شخصی‌سازی، این امکان باعث می‌شود Custom/Premium Emoji،
    Bold، Italic، لینک و سایر entityها بعد از ذخیره در پنل ادمین هنگام ارسال
    دوباره به Telegram تحویل داده شوند؛ بدون اینکه parse_mode به متن تحمیل شود.
    """
    def __new__(cls, value: str, entities: list[dict] | None = None):
        obj = super().__new__(cls, value)
        obj.entities = [dict(e) for e in (entities or [])]
        return obj

    @staticmethod
    def _units(value: str) -> int:
        return len(str(value).encode("utf-16-le")) // 2

    def __add__(self, other):
        if isinstance(other, RichText):
            return RichText(str(self) + str(other), self.entities + other.entities_shifted(self._units(str(self))))
        return RichText(str(self) + str(other), self.entities)

    def __radd__(self, other):
        shift = self._units(str(other))
        return RichText(str(other) + str(self), self.entities_shifted(shift))

    def entities_shifted(self, shift: int) -> list[dict]:
        result = []
        for e in self.entities:
            x = dict(e)
            x["offset"] = int(x.get("offset", 0)) + shift
            result.append(x)
        return result


def _render_with_entities(template: str, entities: list[dict], values: dict, preserve_dynamic_entities: bool = False) -> RichText:
    if not values:
        return RichText(template, entities)

    # قالب را قطعه‌قطعه می‌سازیم تا offsetهای UTF-16 entityها بعد از جایگزینی
    # placeholderها دقیقاً به محل جدید منتقل شوند. Entityهایی که داخل یک
    # placeholder متغیر باشند عمداً حذف می‌شوند؛ چنین entityای متعلق به متن
    # ادمین نیست و نمی‌تواند به‌صورت امن روی مقدار داینامیک اعمال شود.
    import string
    formatter = string.Formatter()
    parts = []
    pending_dynamic_entities = []
    src_pos = 0
    out_pos = 0
    mappings = []  # (source_start, source_end, output_start, output_end)
    for literal, field, spec, conv in formatter.parse(template):
        if literal:
            parts.append(literal)
            n = len(literal.encode("utf-16-le")) // 2
            mappings.append((src_pos, src_pos+n, out_pos, out_pos+n))
            src_pos += n; out_pos += n
        if field is not None:
            # طول خود placeholder در سورس
            token = "{" + field
            if conv:
                token += "!" + conv
            if spec:
                token += ":" + spec
            token += "}"
            src_n = len(token.encode("utf-16-le")) // 2
            try:
                value = formatter.get_field(field, (), values)[0]
                nested_entities = []
                if preserve_dynamic_entities and isinstance(value, RichText):
                    nested_entities = value.entities_shifted(out_pos)
                if conv:
                    value = formatter.convert_field(value, conv)
                value = formatter.format_field(value, spec)
            except Exception:
                value = "{" + field + (":" + spec if spec else "") + "}"
                nested_entities = []
            value = str(value)
            parts.append(value)
            if nested_entities:
                pending_dynamic_entities.extend(nested_entities)
            out_n = len(value.encode("utf-16-le")) // 2
            mappings.append((src_pos, src_pos+src_n, out_pos, out_pos+out_n))
            # Dynamic VIP category/plan names can carry a Premium Emoji too.
            try:
                emoji_id = db.get_button_custom_emoji_id("text:" + value)
                if emoji_id:
                    import unicodedata
                    span = 0; j = 0
                    while j < len(value):
                        ch = value[j]; cp = ord(ch)
                        if j == 0 or cp in (0xFE0E, 0xFE0F, 0x200D, 0x20E3) or unicodedata.category(ch) in {"So", "Sk", "Sc"}:
                            span += len(ch.encode("utf-16-le")) // 2
                            j += 1
                            continue
                        break
                    if span:
                        # Entity offset is already in output UTF-16 units because
                        # this is the exact position where the placeholder value lands.
                        pending_dynamic_entities.append({
                            "type": "custom_emoji",
                            "offset": out_pos,
                            "length": span,
                            "custom_emoji_id": str(emoji_id),
                        })
            except Exception:
                pass
            # 🆕 فیکس: قبل از این خط src_pos/out_pos بعد از هر placeholder هرگز جلو
            # نمی‌رفت، بنابراین تمام entityهای (از جمله Premium Emojiها) بعد از اولین
            # placeholder با موقعیت اشتباه محاسبه می‌شدند. همین باعث می‌شد در متن‌هایی
            # با چند placeholder (مثل پروفایل) فقط ایموجی‌های قبل از اولین placeholder
            # درست premium نمایش داده شوند و بقیه یا جابه‌جا یا کلاً حذف شوند.
            src_pos += src_n; out_pos += out_n

    rendered = "".join(parts)
    rendered_units = len(rendered.encode("utf-16-le")) // 2

    def map_boundary(pos: int):
        for a,b,c,d in mappings:
            if a <= pos <= b:
                if b == a:
                    return c
                # فقط entityهای واقعاً داخل literalها را جابه‌جا کن.
                if pos == b:
                    return d
                ratio = (pos-a)/(b-a)
                return int(round(c + ratio*(d-c)))
        return None

    out_entities = list(pending_dynamic_entities)
    for ent in entities or []:
        try:
            off = int(ent.get("offset", 0)); length = int(ent.get("length", 0))
            start = map_boundary(off); end = map_boundary(off+length)
            if start is None or end is None or end <= start or end > rendered_units:
                continue
            e = dict(ent); e["offset"] = start; e["length"] = end-start
            out_entities.append(e)
        except Exception:
            continue
    return RichText(rendered, out_entities)



def _sanitize_service_detail_template(key: str, template):
    """قالب قدیمی جزئیات سرویس را کنار می‌گذارد تا Override قبلی DB فرم قدیمی را برنگرداند."""
    if key != "service_detail_text" or not isinstance(template, str):
        return template
    if any(token in template for token in ("{status}", "{service_name}", "{location}", "{requested_at}", "{delivery_duration}", "{last_connection}", "{last_update}", "{client}")):
        return TEXTS.get(key, template)
    # Markdown code fences around the subscription URL are unsafe here: the URL
    # may contain underscores and Telegram Markdown can consume them. The URL
    # must be emitted byte-for-byte as received from the panel.
    template = template.replace("`{link}`", "{link}")
    return template


def text(key: str, default: str | None = None, **values) -> str:
    if key not in _CACHE:
        raw_template = db.get_text_override(key, TEXTS.get(key, default or ""))
        template = _sanitize_service_detail_template(key, raw_template)
        entities = db.get_text_override_entities(key)
        # اگر فقط Markdown قدیمی دور {link} پاک شده، Entityهای Premium Emoji را
        # نگه می‌داریم؛ فقط Overrideهای واقعاً قدیمی با ساختار قبلی باید پاک شوند.
        legacy_template = isinstance(raw_template, str) and any(
            token in raw_template
            for token in (
                "{status}", "{service_name}", "{location}", "{requested_at}",
                "{delivery_duration}", "{last_connection}", "{last_update}", "{client}",
            )
        )
        if template != raw_template and legacy_template:
            entities = []
        _CACHE[key] = (template, entities)
    template, entities = _CACHE[key]
    if values:
        try:
            return _render_with_entities(template, entities, values, preserve_dynamic_entities=(key == "renew_done"))
        except Exception:
            fallback = TEXTS.get(key, default or template)
            try:
                return RichText(fallback.format_map(values), [])
            except Exception:
                return RichText(fallback, [])
    return RichText(template, entities) if entities else template


def refresh(key: str):
    _CACHE.pop(key, None)


def all_items():
    return TEXT_CATEGORIES
