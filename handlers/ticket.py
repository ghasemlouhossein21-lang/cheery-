from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext

import database as db
from utils import answer_rich, edit_rich, send_admin_task_message, forward_admin_task_message, send_rich
from text_catalog import text as t
from utils import show_menu_with_sticker
from states import UserStates, AdminStates
from config import ADMIN_ID
from keyboards import back_button, support_menu, admin_tickets_menu, admin_ticket_list_keyboard, admin_ticket_detail_keyboard

router = Router(name="ticket")


def _ticket_text(ticket_id: int) -> str:
    ticket=db.get_ticket(ticket_id)
    if not ticket: return t("admin_ticket_not_found")
    user=db.get_user(ticket.get("telegram_id")) or {}
    lines=[f"#{ticket_id}",f"کاربر: {user.get('name') or '-'}",f"Telegram ID: {ticket.get('telegram_id')}",f"وضعیت: {'باز' if ticket.get('status')=='open' else 'بسته'}",f"آخرین ارسال: {'کاربر' if ticket.get('last_sender')=='user' else 'پشتیبانی'}","", "تاریخچه:"]
    for m in db.get_ticket_messages(ticket_id, limit=20):
        who="کاربر" if m.get("sender_type")=="user" else "پشتیبانی"
        body=str(m.get("message") or "")
        lines.append(f"{who} ({m.get('created_at','-')}):\n{body[:1200]}")
    return "\n".join(lines)[:3900]


@router.callback_query(F.data == "support")
async def support_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await show_menu_with_sticker(callback.bot, callback.message.chat.id, "support", t("support_intro"), reply_markup=support_menu())
    except Exception:
        await callback.message.answer(t("support_error"))
    await callback.answer()


@router.callback_query(F.data == "ticket")
async def ticket_start(callback: types.CallbackQuery, state: FSMContext):
    await show_menu_with_sticker(callback.bot, callback.message.chat.id, "ticket_write", t("ticket_write"), reply_markup=back_button("back", t("back")))
    await state.set_state(UserStates.waiting_ticket_message)
    await callback.answer()


@router.message(AdminStates.waiting_ticket_reply)
async def admin_reply_send(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(message.from_user.id), "tickets"):
        return
    data=await state.get_data(); ticket_id=data.get("ticket_id")
    if not ticket_id and data.get("reply_target"):
        body=(message.text or message.caption or "").strip()
        if not body: return
        try:
            await send_rich(message.bot,int(data["reply_target"]),t("ticket_user_reply_prefix") + "\n\n" + body)
            await answer_rich(message,t("ticket_reply_sent"))
        except Exception:
            await answer_rich(message,t("ticket_reply_failed"))
        await state.clear(); return
    ticket=db.get_ticket(ticket_id) if ticket_id else None
    if not ticket or ticket.get("status")!="open":
        await answer_rich(message,t("admin_ticket_not_found")); await state.clear(); return
    body=(message.text or message.caption or "").strip()
    if not body: return
    if not db.add_ticket_message(int(ticket_id),"admin",str(message.from_user.id),body):
        await answer_rich(message,t("admin_ticket_not_found")); await state.clear(); return
    try:
        await send_rich(message.bot,int(ticket["telegram_id"]),t("ticket_user_reply_prefix") + "\n\n" + body)
        await answer_rich(message,t("ticket_reply_sent"))
    except Exception:
        await answer_rich(message,t("ticket_reply_failed"))
    await state.clear()


@router.callback_query(F.data.startswith("replyticket_"))
async def legacy_admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ دسترسی ندارید.", show_alert=True); return
    target_uid=callback.data.replace("replyticket_", "")
    await state.update_data(reply_target=target_uid)
    await state.set_state(AdminStates.waiting_ticket_reply)
    await callback.message.answer(f"پاسخ خود را برای کاربر {target_uid} بنویسید:")
    await callback.answer()


@router.callback_query(F.data.startswith("ticketreply_"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    ticket_id=callback.data.replace("ticketreply_","")
    try: ticket_id=int(ticket_id)
    except ValueError: await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    ticket=db.get_ticket(ticket_id)
    if not ticket or ticket.get("status")!="open": await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(AdminStates.waiting_ticket_reply)
    await callback.message.answer(t("admin_ticket_reply_prompt"))
    await callback.answer()


@router.callback_query(F.data.startswith("ticketclose_"))
async def admin_ticket_close(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    try: tid=int(callback.data.replace("ticketclose_",""))
    except ValueError: await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    if not db.set_ticket_status(tid,"closed"): await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    ticket=db.get_ticket(tid)
    await edit_rich(callback.message,_ticket_text(tid),reply_markup=admin_ticket_detail_keyboard(ticket))
    await callback.answer(t("admin_ticket_closed_notice"))


@router.callback_query(F.data.startswith("ticketreopen_"))
async def admin_ticket_reopen(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    try: tid=int(callback.data.replace("ticketreopen_",""))
    except ValueError: await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    if not db.set_ticket_status(tid,"open"): await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    ticket=db.get_ticket(tid)
    await edit_rich(callback.message,_ticket_text(tid),reply_markup=admin_ticket_detail_keyboard(ticket))
    await callback.answer(t("admin_ticket_reopened_notice"))


@router.callback_query(F.data == "admin_tickets")
async def admin_tickets_open(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    await edit_rich(callback.message,"مدیریت تیکت",reply_markup=admin_tickets_menu(db.get_ticket_counts())); await callback.answer()

@router.message(F.text == t("admin_tickets"))
async def admin_tickets_reply_menu(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(message.from_user.id), "tickets"): return
    await state.clear(); await answer_rich(message,t("admin_tickets"),reply_markup=admin_tickets_menu(db.get_ticket_counts()))

@router.callback_query(F.data.in_({"admintickets_open","admintickets_unanswered","admintickets_closed"}))
async def admin_ticket_list(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    key=callback.data
    if key.endswith("open"): items=db.list_tickets(status="open"); title=t("admin_tickets_open",count=len(items))
    elif key.endswith("unanswered"): items=db.list_tickets(unanswered=True); title=t("admin_tickets_unanswered",count=len(items))
    else: items=db.list_tickets(status="closed"); title=t("admin_tickets_closed",count=len(items))
    if not items: await edit_rich(callback.message,f"{title}\n\nموردی وجود ندارد.",reply_markup=admin_tickets_menu(db.get_ticket_counts())); await callback.answer(); return
    await edit_rich(callback.message,f"{title}\n\nیک تیکت را انتخاب کنید:",reply_markup=admin_ticket_list_keyboard(items,key)); await callback.answer()

@router.callback_query(F.data.startswith("adminticket_"))
async def admin_ticket_detail(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID and not db.sub_admin_has_permission(str(callback.from_user.id), "tickets"):
        await callback.answer("⛔ دسترسی ندارید.",show_alert=True); return
    try: tid=int(callback.data.replace("adminticket_",""))
    except ValueError: await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    ticket=db.get_ticket(tid)
    if not ticket: await callback.answer(t("admin_ticket_not_found"),show_alert=True); return
    await edit_rich(callback.message,_ticket_text(tid),reply_markup=admin_ticket_detail_keyboard(ticket)); await callback.answer()


@router.message(UserStates.waiting_ticket_message)
async def ticket_message(message: types.Message, state: FSMContext):
    body=(message.text or message.caption or "").strip()
    if not body and (message.photo or message.document or message.video or message.voice):
        body="[فایل/رسانه ارسال شد]"
    if not body:
        return
    user=db.get_user(message.from_user.id)
    ticket_id=db.create_ticket(str(message.from_user.id),user["id"] if user else None,body)
    admin_text=f"تیکت جدید #{ticket_id}\n\nکاربر: {message.from_user.full_name}\nTelegram ID: {message.from_user.id}\n\n{body}"
    from keyboards import admin_ticket_detail_keyboard
    await send_admin_task_message(message.bot,ADMIN_ID,"tickets",admin_text,reply_markup=admin_ticket_detail_keyboard(db.get_ticket(ticket_id)))
    if message.photo or message.document or message.video or message.voice:
        try: await forward_admin_task_message(message.bot,ADMIN_ID,"tickets",message.chat.id,message.message_id)
        except Exception: pass
    await answer_rich(message,t("ticket_sent"))
    await state.clear()
