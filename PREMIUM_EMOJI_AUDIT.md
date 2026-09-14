# Premium / Custom Emoji architecture audit

Implemented in this build:
- Editable text overrides persist Telegram `custom_emoji` entities and IDs.
- Central InlineKeyboardButton and KeyboardButton wrappers apply `icon_custom_emoji_id`.
- Reply-keyboard matching tolerates Telegram stripping the fallback emoji.
- Dynamic VIP category and plan names persist Premium Emoji IDs under `text:<name>`.
- VIP category/plan buttons resolve dynamic Premium Emoji IDs.
- Rich editable messages preserve custom-emoji entities through send/edit paths.
- Dynamic plan/category names inside editable templates can restore their custom-emoji entity.
- The user text manager catalog was expanded to include the complete editable-text catalog from the reference build.
- Existing database migrations add `entities_json` without destroying old text overrides.
- Backup export now includes `settings`, which contains button Premium Emoji mappings.


Additional fixes in this build:
- Text editor preserves exact message text/UTF-16 entity offsets instead of stripping it before persistence.
- Persisted custom_emoji entities are validated and retained in `text_overrides.entities_json`.
- Rich editable notification sends use the stored Telegram entities.
- Removed the obsolete `min_gb` variable from the editable text catalog; referral messaging now refers to the first real paid purchase.
