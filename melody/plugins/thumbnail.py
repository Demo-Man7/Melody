# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of Melody


import re

from pyrogram import filters, types

from melody import app, db, lang
from melody.helpers import admin_check, buttons
# Fonts သို့မဟုတ် font utility ကို မလိုအပ်ပါက ပုံမှန်စာသားဖြင့် အစားထိုးနိုင်သည်
try:
    from melody.helpers._fonts_data import Fonts
except ImportError:
    Fonts = None


def font(text: str) -> str:
    if not text or not Fonts:
        return text

    segments = re.split(r'(<[^>]+>)', text)
    styled_segments = []
    for segment in segments:
        if segment.startswith('<') and segment.endswith('>'):
            styled_segments.append(segment)
        else:
            styled_text = ""
            for char in segment:
                try:
                    if char.isupper() and hasattr(Fonts, "serief"):
                        styled_text += Fonts.serief(char)
                    elif char.islower() and hasattr(Fonts, "smallcap"):
                        styled_text += Fonts.smallcap(char)
                    else:
                        styled_text += char
                except Exception:
                    styled_text += char
            styled_segments.append(styled_text)

    return "".join(styled_segments)


@app.on_message(filters.command(["thumb", "thumbnail"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def _thumb(_, m: types.Message):
    # db.get_thumb ကို melody database ပုံစံအတိုင်း အသုံးပြုခြင်း
    enabled = await db.get_thumb(m.chat.id)
    status = "🟢 Enabled" if enabled else "🔴 Disabled"
    await m.reply_text(
        text=m.lang["thumb_status"].format(status),
        reply_markup=buttons.thumb_markup(m.chat.id, enabled),
    )


@app.on_callback_query(filters.regex("^thumbtoggle") & ~app.bl_users)
@lang.language()
@admin_check
async def _thumb_toggle(_, query: types.CallbackQuery):
    data = query.data.split()
    chat_id, want = int(data[1]), data[2] == "on"

    current = await db.get_thumb(chat_id)
    status = "🟢 Enabled" if want else "🔴 Disabled"
    if want == current:
        return await query.answer(f"Thumbnail is already {status}", show_alert=False)

    await db.set_thumb(chat_id, want)
    await query.answer(query.lang["thumb"] + ": " + status, show_alert=False)
    await query.edit_message_text(
        text=query.lang["thumb_status"].format(status),
        reply_markup=buttons.thumb_markup(chat_id, want),
    )


@app.on_callback_query(filters.regex("^thumbclose$") & ~app.bl_users)
async def _thumb_close(_, query: types.CallbackQuery):
    try:
        await query.message.delete()
    except Exception:
        pass
