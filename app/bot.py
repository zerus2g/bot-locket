import asyncio
import logging
import random
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from app.config import * # Đảm bảo app/config.py đã có biến ADMIN_IDS = [...]
from app import database as db
from app.services import locket, nextdns

logger = logging.getLogger(__name__)

request_queue = asyncio.Queue()
pending_items = []
queue_lock = asyncio.Lock()

class Clr:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

async def update_pending_positions(app):
    for i, item in enumerate(pending_items):
        position = i + 1
        ahead = i
        try:
            # Update position text
            await app.bot.edit_message_text(
                chat_id=item['chat_id'],
                message_id=item['message_id'],
                text=T("queued", item['lang']).format(item['username'], position, ahead),
                parse_mode=ParseMode.HTML
            )
            
            # Notify if almost turn (ahead == 2)
            if ahead == 2:
                try:
                    await app.bot.send_message(
                        chat_id=item['chat_id'],
                        text=T("queue_almost", item['lang']),
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    # Handle referral deep link: /start REF-XXXXXX
    if context.args and len(context.args) > 0:
        ref_code = context.args[0].strip()
        if ref_code.startswith("REF-"):
            referrer_id = db.find_user_by_referral_code(ref_code)
            if referrer_id and referrer_id != user_id:
                success = db.process_referral(referrer_id, user_id)
                if success:
                    # Notify new user
                    await update.message.reply_text(T("ref_welcome", lang), parse_mode=ParseMode.HTML)
                    # Notify referrer
                    try:
                        ref_lang = db.get_lang(referrer_id) or DEFAULT_LANG
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=T("ref_notify_referrer", ref_lang),
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
    
    await update.message.reply_text(
        T("welcome", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_keyboard(lang)
    )

async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_language_select(update)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    help_text = T("help_msg", lang)
    
    # [V-EDIT] Check if user is in ADMIN_IDS list
    if user_id in ADMIN_IDS:
        help_text += (
            f"\n\n<b>👑 Admin Control:</b>\n"
            f"/noti [msg] - Broadcast message\n"
            f"/rs [id] - Reset usage limit\n"
            f"/setdonate - Set success photo\n"
            f"/settoken - Update fetch_token\n"
            f"/viewtoken - View current tokens\n"
            f"/setlimit - Set daily limit\n"
            f"/genkey - Generate VIP keys\n"
            f"/listkeys - View unused keys\n"
            f"/stats - View detailed statistics"
        )
        
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # [V-EDIT] Check list permissions
    if user_id not in ADMIN_IDS: return

    stats = db.get_stats()
    msg = (
        f"{E_STAT} <b>SYSTEM STATISTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{E_USER} <b>Active Users</b>: {stats['unique_users']}\n"
        f"{E_GLOBE} <b>Total Requests</b>: {stats['total']}\n"
        f"{E_SUCCESS} <b>Success</b>: {stats['success']}\n"
        f"{E_ERROR} <b>Failed</b>: {stats['fail']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{E_ANDROID} <b>Active Workers</b>: {NUM_WORKERS}\n"
        f"🔑 <b>Token Sets</b>: {len(TOKEN_SETS)}\n"
        f"⏳ <b>Queue Size</b>: {request_queue.qsize()}\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# --- Admin Commands ---
async def broadcast_worker(bot, users, text, chat_id, message_id):
    success = 0
    fail = 0
    total = len(users)
    
    for i, uid in enumerate(users):
        try:
            await bot.send_message(chat_id=uid, text=f"📢 <b>ADMIN NOTIFICATION</b>\n\n{text}", parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            fail += 1
            
        # Update progress every 5 users or at the end
        if (i + 1) % 5 == 0 or (i + 1) == total:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"{E_LOADING} <b>Broadcasting...</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 <b>Progress</b>: {i+1}/{total}\n"
                        f"{E_SUCCESS} <b>Success</b>: {success}\n"
                        f"{E_ERROR} <b>Failed</b>: {fail}"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await asyncio.sleep(0.05) # Prevent flood limits

    # Final completion message
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"{E_SUCCESS} <b>Broadcast Complete!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👥 <b>Total</b>: {total}\n"
                f"{E_SUCCESS} <b>Success</b>: {success}\n"
                f"{E_ERROR} <b>Failed</b>: {fail}"
            ),
            parse_mode=ParseMode.HTML
        )
    except:
        pass

async def noti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    # [V-EDIT] Check list permissions
    if user_id not in ADMIN_IDS:
        return
        
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /noti {message}")
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("No users found.")
        return

    status_msg = await update.message.reply_text(
        f"{E_LOADING} <b>Starting broadcast to {len(users)} users...</b>",
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(broadcast_worker(context.bot, users, msg, status_msg.chat_id, status_msg.message_id))

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    # [V-EDIT] Check list permissions
    if user_id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("Usage: /rs {user_id}")
        return
        
    try:
        target_id = int(context.args[0])
        db.reset_usage(target_id)
        await update.message.reply_text(T("admin_reset", lang).format(target_id))
    except ValueError:
        await update.message.reply_text("Invalid User ID")

async def set_donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # [V-EDIT] Check list permissions
    if user_id not in ADMIN_IDS:
        return

    photo = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]
    elif update.message.photo:
        photo = update.message.photo[-1]
        
    if photo:
        file_id = photo.file_id
        db.set_config("donate_photo", file_id)
        await update.message.reply_text(f"✅ Updated Donate Photo ID:\n<code>{file_id}</code>", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Please reply to a photo with /setdonate to set it.")

async def settoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        f"{E_TIP} <b>Cập nhật fetch_token</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Gửi file <b>token.json</b> với nội dung:\n\n"
        f'<pre>{{\n'
        f'  "fetch_token": "eyJ..."\n'
        f'}}</pre>',
        parse_mode=ParseMode.HTML
    )

async def handle_token_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded token.json file from admin."""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    doc = update.message.document
    if not doc:
        return
    
    fname = (doc.file_name or "").lower()
    if not fname.endswith(".json"):
        return
    
    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        data = json.loads(file_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        await update.message.reply_text(f"{E_ERROR} File JSON không hợp lệ.", parse_mode=ParseMode.HTML)
        return
    except Exception as e:
        await update.message.reply_text(f"{E_ERROR} Lỗi đọc file: <code>{e}</code>", parse_mode=ParseMode.HTML)
        return
    
    new_fetch_token = data.get("fetch_token", "").strip()
    
    if not new_fetch_token or len(new_fetch_token) < 50:
        await update.message.reply_text(f"{E_ERROR} <code>fetch_token</code> không hợp lệ hoặc thiếu.", parse_mode=ParseMode.HTML)
        return
    
    # Update fetch_token only
    TOKEN_SETS[0]['fetch_token'] = new_fetch_token
    
    # Persist to DB
    db.save_token_set(0, TOKEN_SETS[0])
    
    preview = new_fetch_token[:30] + "..."
    await update.message.reply_text(
        f"{E_SUCCESS} <b>fetch_token Updated!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <code>{preview}</code>\n"
        f"💾 Đã lưu vào DB (persist qua restart)",
        parse_mode=ParseMode.HTML
    )

async def viewtoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return

    msg = f"🔑 <b>TOKEN SETS ({len(TOKEN_SETS)} sets)</b>\n━━━━━━━━━━━━━━━━━━━\n"
    
    saved = db.load_token_sets()
    for i, ts in enumerate(TOKEN_SETS):
        name = ts.get('name', f'Token_{i}')
        ft_preview = ts.get('fetch_token', 'N/A')[:30] + "..."
        at_preview = ts.get('app_transaction', 'N/A')[:30] + "..."
        is_sandbox = ts.get('is_sandbox', False)
        env = "🟡 Sandbox" if is_sandbox else "🟢 Production"
        source = "💾 DB" if i in saved else "📄 Config"
        
        msg += (
            f"\n<b>#{i+1} {name}</b>\n"
            f"  {env} | {source}\n"
            f"  🔑 <code>{ft_preview}</code>\n"
            f"  📄 <code>{at_preview}</code>\n"
        )
    
    msg += f"\n━━━━━━━━━━━━━━━━━━━\n{E_TIP} Dùng <code>/settoken</code> để đổi token"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def setlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return

    current_limit = db.get_daily_limit()
    
    if not context.args:
        await update.message.reply_text(
            f"{E_STAT} <b>Daily Limit</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 Limit hiện tại: <b>{current_limit} lượt/ngày</b>\n\n"
            f"{E_TIP} Dùng: <code>/setlimit [số]</code> để thay đổi",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        new_limit = int(context.args[0])
        if new_limit < 1 or new_limit > 100:
            await update.message.reply_text(f"{E_ERROR} Limit phải từ 1 đến 100.", parse_mode=ParseMode.HTML)
            return
    except ValueError:
        await update.message.reply_text(f"{E_ERROR} Vui lòng nhập số hợp lệ.", parse_mode=ParseMode.HTML)
        return

    db.set_config("daily_limit", str(new_limit))
    
    await update.message.reply_text(
        f"{E_SUCCESS} <b>Limit Updated!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {current_limit} lượt ➜ <b>{new_limit} lượt/ngày</b>\n"
        f"💾 Đã lưu vào DB",
        parse_mode=ParseMode.HTML
    )

# ===== KEY SYSTEM (Admin) =====

async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            f"{E_TIP} <b>Tạo Key VIP</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Cách dùng: <code>/genkey [số lượng] [loại]</code>\n\n"
            f"<b>Loại key:</b>\n"
            f"  • <code>1d</code> — VIP 1 ngày\n"
            f"  • <code>7d</code> — VIP 7 ngày\n"
            f"  • <code>30d</code> — VIP 30 ngày\n\n"
            f"<b>Ví dụ:</b> <code>/genkey 5 7d</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        count = int(context.args[0])
        if count < 1 or count > 50:
            await update.message.reply_text(f"{E_ERROR} Số lượng từ 1-50.", parse_mode=ParseMode.HTML)
            return
    except ValueError:
        await update.message.reply_text(f"{E_ERROR} Số lượng không hợp lệ.", parse_mode=ParseMode.HTML)
        return
    
    key_type = context.args[1].lower()
    if key_type not in ("1d", "7d", "30d"):
        await update.message.reply_text(f"{E_ERROR} Loại key phải là: <code>1d</code>, <code>7d</code>, <code>30d</code>", parse_mode=ParseMode.HTML)
        return
    
    keys = db.generate_keys(count, key_type, user_id)
    
    keys_text = "\n".join([f"<code>{k}</code>" for k in keys])
    await update.message.reply_text(
        f"{E_SUCCESS} <b>Đã tạo {len(keys)} key VIP ({key_type})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{keys_text}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{E_TIP} User dùng <code>/redeem [key]</code> để kích hoạt",
        parse_mode=ParseMode.HTML
    )

async def listkeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    keys = db.list_unused_keys()
    if not keys:
        await update.message.reply_text(f"{E_TIP} Không có key nào chưa sử dụng.", parse_mode=ParseMode.HTML)
        return
    
    msg = f"🔑 <b>UNUSED KEYS ({len(keys)})</b>\n━━━━━━━━━━━━━━━━━━━\n"
    for k in keys[:30]:  # Show max 30
        msg += f"  <code>{k['key']}</code> — {k['type']}\n"
    
    if len(keys) > 30:
        msg += f"\n... và {len(keys) - 30} key khác"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

# ===== KEY SYSTEM (User) =====

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    if not context.args:
        await update.message.reply_text(
            f"{E_TIP} <b>Redeem VIP Key</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{'Cách dùng' if lang == 'VI' else 'Usage'}: <code>/redeem [key]</code>\n"
            f"{'Ví dụ' if lang == 'VI' else 'Example'}: <code>/redeem LG-ABCD-1234</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    key_str = context.args[0].strip().upper()
    success, msg_key, days = db.redeem_key(key_str, user_id)
    
    if success:
        expiry = db.get_vip_expiry(user_id) or "N/A"
        await update.message.reply_text(
            T("redeem_success", lang).format(days, expiry),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(T(msg_key, lang), parse_mode=ParseMode.HTML)

# ===== REFERRAL SYSTEM =====

async def myref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    ref_code = db.get_or_create_referral_code(user_id)
    stats = db.get_referral_stats(user_id)
    
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    ref_link = f"https://t.me/{bot_username}?start={ref_code}"
    
    total_limit = db.get_user_total_limit(user_id)
    base_limit = db.get_daily_limit()
    vip_expiry = db.get_vip_expiry(user_id)
    is_vip = db.is_vip(user_id)
    
    if lang == "VI":
        msg = (
            f"🤝 <b>Hệ Thống Giới Thiệu</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>Link mời:</b>\n<code>{ref_link}</code>\n\n"
            f"📊 <b>Thống kê:</b>\n"
            f"  👥 Đã mời: <b>{stats['total_refs']}</b> người\n"
            f"  🎁 Bonus: <b>+{stats['bonus']}</b> lượt/ngày\n\n"
            f"📋 <b>Limit hiện tại:</b>\n"
        )
        if is_vip:
            msg += f"  💎 <b>VIP UNLIMITED</b> (đến {vip_expiry})\n"
        else:
            msg += f"  🔢 {base_limit} (gốc) + {stats['bonus']} (ref) = <b>{total_limit} lượt/ngày</b>\n"
        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━\n"
            f"{E_TIP} Mời 1 bạn = <b>+2 lượt/ngày</b> cho bạn\n"
            f"🎁 Người được mời = <b>+1 lượt/ngày</b> bonus"
        )
    else:
        msg = (
            f"🤝 <b>Referral System</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>Invite link:</b>\n<code>{ref_link}</code>\n\n"
            f"📊 <b>Stats:</b>\n"
            f"  👥 Invited: <b>{stats['total_refs']}</b> users\n"
            f"  🎁 Bonus: <b>+{stats['bonus']}</b> requests/day\n\n"
            f"📋 <b>Current limit:</b>\n"
        )
        if is_vip:
            msg += f"  💎 <b>VIP UNLIMITED</b> (until {vip_expiry})\n"
        else:
            msg += f"  🔢 {base_limit} (base) + {stats['bonus']} (ref) = <b>{total_limit}/day</b>\n"
        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━\n"
            f"{E_TIP} Invite 1 friend = <b>+2 requests/day</b> for you\n"
            f"🎁 Invited user gets <b>+1 request/day</b> bonus"
        )
    
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def show_language_select(update: Update):
    keyboard = [
        [InlineKeyboardButton("Tiếng Việt 🇻🇳", callback_data="setlang_VI")],
        [InlineKeyboardButton("English 🇺🇸", callback_data="setlang_EN")]
    ]
    text = T("lang_select", "EN")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only process if it's a reply to the bot's prompt (ForceReply)
    if not update.message.reply_to_message or not update.message.reply_to_message.from_user.is_bot:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    lang = db.get_lang(user_id) or DEFAULT_LANG

    if "locket.cam/" in text:
        username = text.split("locket.cam/")[-1].split("?")[0]
    elif len(text) < 50 and " " not in text:
        username = text
    else:
        username = text

    msg = await update.message.reply_text(T("resolving", lang), parse_mode=ParseMode.HTML)
    
    uid = await locket.resolve_uid(username)
    if not uid:
        await msg.edit_text(T("not_found", lang), parse_mode=ParseMode.HTML)
        return
        
    # [V-EDIT] Admin bypass limit check (List support)
    if user_id not in ADMIN_IDS and not db.check_can_request(user_id):
        total = db.get_user_total_limit(user_id)
        limit_msg = f"{E_LIMIT} Đã đạt giới hạn request ({total}/{total})." if lang == "VI" else f"{E_LIMIT} Daily limit reached ({total}/{total})."
        await msg.edit_text(limit_msg, parse_mode=ParseMode.HTML)
        return
        
    await msg.edit_text(T("checking_status", lang), parse_mode=ParseMode.HTML)
    status = await locket.check_status(uid)
    
    status_text = T("free_status", lang)
    if status and status.get("active"):
        status_text = T("gold_active", lang).format(status['expires'])
    
    safe_username = username[:30]
    keyboard = [[InlineKeyboardButton(T("btn_upgrade", lang), callback_data=f"upg|{uid}|{safe_username}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.edit_text(
        f"{T('user_info_title', lang)}\n"
        f"{E_ID}: <code>{uid}</code>\n"
        f"{E_TAG}: <code>{username}</code>\n"
        f"{E_STAT} <b>Status</b>: {status_text}\n\n"
        f"👇",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG

    if data.startswith("setlang_"):
        new_lang = data.split("_")[1]
        db.set_lang(user_id, new_lang)
        lang = new_lang
        await query.answer(f"Language: {new_lang}")
        await query.message.edit_text(
            T("menu_msg", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    if data == "menu_lang":
        await show_language_select(update)
        return
        
    if data == "menu_help":
        help_text = T("help_msg", lang)
        # [V-EDIT] Check list permissions
        if user_id in ADMIN_IDS:
            help_text += (
                f"\n\n<b>👑 Admin Control:</b>\n"
                f"/noti [msg] - Broadcast message\n"
                f"/rs [id] - Reset usage limit\n"
                f"/setdonate - Set success photo\n"
                f"/settoken - Update fetch_token\n"
                f"/viewtoken - View current tokens\n"
                f"/setlimit - Set daily limit\n"
                f"/genkey - Generate VIP keys\n"
                f"/listkeys - View unused keys\n"
                f"/stats - View detailed statistics"
            )
            
        await query.edit_message_text(
            help_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]])
        )
        return

    if data == "menu_back":
        await query.message.edit_text(
            T("menu_msg", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard(lang)
        )
        return

    if data == "menu_ref":
        try:
            await query.answer()
        except:
            pass
        ref_code = db.get_or_create_referral_code(user_id)
        stats = db.get_referral_stats(user_id)
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={ref_code}"
        total_limit = db.get_user_total_limit(user_id)
        base_limit = db.get_daily_limit()
        is_vip_user = db.is_vip(user_id)
        vip_expiry = db.get_vip_expiry(user_id)
        
        if lang == "VI":
            msg = (
                f"🤝 <b>Hệ Thống Giới Thiệu</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <b>Link mời:</b>\n<code>{ref_link}</code>\n\n"
                f"📊 👥 Đã mời: <b>{stats['total_refs']}</b> | 🎁 Bonus: <b>+{stats['bonus']}</b> lượt/ngày\n"
            )
            if is_vip_user:
                msg += f"📋 💎 <b>VIP UNLIMITED</b> (đến {vip_expiry})\n"
            else:
                msg += f"📋 🔢 {base_limit} + {stats['bonus']} = <b>{total_limit} lượt/ngày</b>\n"
            msg += f"\n{E_TIP} Mời 1 bạn = <b>+2</b> cho bạn, bạn bè = <b>+1</b>"
        else:
            msg = (
                f"🤝 <b>Referral System</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <b>Invite link:</b>\n<code>{ref_link}</code>\n\n"
                f"📊 👥 Invited: <b>{stats['total_refs']}</b> | 🎁 Bonus: <b>+{stats['bonus']}</b>/day\n"
            )
            if is_vip_user:
                msg += f"📋 💎 <b>VIP UNLIMITED</b> (until {vip_expiry})\n"
            else:
                msg += f"📋 🔢 {base_limit} + {stats['bonus']} = <b>{total_limit}/day</b>\n"
            msg += f"\n{E_TIP} Invite 1 friend = <b>+2</b> for you, friend gets <b>+1</b>"
        
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]])
        )
        return

    if data == "menu_redeem":
        try:
            await query.answer()
        except:
            pass
        if lang == "VI":
            msg = (
                f"💎 <b>Kích Hoạt VIP Key</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Gửi lệnh: <code>/redeem [key]</code>\n"
                f"Ví dụ: <code>/redeem LG-ABCD-1234</code>\n\n"
                f"{E_TIP} VIP = Unlimited request!"
            )
        else:
            msg = (
                f"💎 <b>Activate VIP Key</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Send: <code>/redeem [key]</code>\n"
                f"Example: <code>/redeem LG-ABCD-1234</code>\n\n"
                f"{E_TIP} VIP = Unlimited requests!"
            )
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_back")]])
        )
        return

    if data == "menu_input":
        try:
            await query.answer()
        except:
            pass
        await query.message.reply_text(
            T("prompt_input", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True, input_field_placeholder="Username...")
        )
        return

    if data.startswith("upg|"):
        parts = data.split("|")
        uid = parts[1]
        username = parts[2] if len(parts) > 2 else uid
        
        # [V-EDIT] Admin bypass limit check (List support)
        if user_id not in ADMIN_IDS and not db.check_can_request(user_id):
            try:
                total = db.get_user_total_limit(user_id)
                limit_msg = f"Đã đạt giới hạn ({total}/{total})" if lang == "VI" else f"Daily limit reached ({total}/{total})"
                await query.answer(limit_msg, show_alert=True)
            except:
                pass
            return
            
        try:
            await query.answer("🚀 Queue...")
        except:
            pass
        
        item = {
            'user_id': user_id,
            'uid': uid,
            'username': username,
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id,
            'lang': lang
        }
        
        async with queue_lock:
            pending_items.append(item)
            position = len(pending_items)
            ahead = position - 1
        
        await query.edit_message_text(
            T("queued", lang).format(username, position, ahead),
            parse_mode=ParseMode.HTML
        )
        
        await request_queue.put(item)
        return

async def queue_worker(app, worker_id):
    # Select token index based on worker ID (round-robin)
    # worker_id is 1-based, so subtract 1
    token_idx = (worker_id - 1) % len(TOKEN_SETS)
    
    print(f"Worker #{worker_id} started using Token-{token_idx+1}...")
    
    while True:
        try:
            item = await request_queue.get()
            
            user_id = item['user_id']
            uid = item['uid']
            username = item['username']
            chat_id = item['chat_id']
            message_id = item['message_id']
            lang = item['lang']
            
            async with queue_lock:
                if item in pending_items:
                    pending_items.remove(item)
                await update_pending_positions(app) # Enabled queue updates
            
            # Read token dynamically so /settoken updates take effect immediately
            token_config = TOKEN_SETS[token_idx]
            token_name = f"Token-{token_idx+1}"
            
            print(f"{Clr.BLUE}[Worker #{worker_id}][{token_name}] Processing:{Clr.ENDC} UID={uid} | UserID={user_id}")
            
            async def edit(text):
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    if "Message is not modified" in str(e):
                        pass
                    elif "Message to edit not found" in str(e):
                        pass
                    else:
                        logger.error(f"Edit msg error: {e}")

            # [V-EDIT] Check limit before processing (unless admin in list)
            if user_id not in ADMIN_IDS and not db.check_can_request(user_id):
                total = db.get_user_total_limit(user_id)
                limit_msg = f"{E_LIMIT} Đã đạt giới hạn request ({total}/{total})." if lang == "VI" else f"{E_LIMIT} Daily limit reached ({total}/{total})."
                await edit(limit_msg)
                request_queue.task_done()
                continue
            
            logs = [f"[Worker #{worker_id}] Processing Request..."]
            loop = asyncio.get_running_loop()
            
            def safe_log_callback(msg):
                clean_msg = msg.replace(Clr.BLUE, "").replace(Clr.GREEN, "").replace(Clr.WARNING, "").replace(Clr.FAIL, "").replace(Clr.ENDC, "").replace(Clr.BOLD, "")
                logs.append(clean_msg)
                asyncio.run_coroutine_threadsafe(update_log_ui(), loop)

            async def update_log_ui():
                display_logs = "\n".join(logs[-10:])
                text = (
                    f"{E_LOADING} <b>⚡ SYSTEM EXPLOIT RUNNING...</b>\n"
                    f"<pre>{display_logs}</pre>"
                )
                try:
                    await app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except:
                    pass

            await update_log_ui()
            
            # Use dynamic token config
            success, msg_result = await locket.inject_gold(uid, token_config, safe_log_callback)
            
            # Log request to DB
            db.log_request(user_id, uid, "SUCCESS" if success else "FAIL")
            
            if success:
                # [V-EDIT] Don't count usage for any Admin in list
                if user_id not in ADMIN_IDS:
                    db.increment_usage(user_id)
                    
                pid, link = await nextdns.create_profile(NEXTDNS_KEY, safe_log_callback)
                
                dns_text = ""
                if link:
                   dns_text = T('dns_msg', lang).format(link, pid)
                else:
                   dns_text = f"{E_ERROR} NextDNS Error: Check API Key"
                
                final_msg = (
                    f"{T('success_title', lang)}\n\n"
                    f"{E_TAG}: <code>{username}</code>\n"
                    f"{E_ID}: <code>{uid}</code>\n"
                    f"{E_CALENDAR} <b>Plan</b>: Gold (30d)\n"
                    f"{dns_text}"
                )
                
                await asyncio.sleep(2.0)
                
                # Delete progress message and send photo with caption
                try:
                    await app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
                
                current_photo = db.get_config("donate_photo", "")
                if current_photo:
                    try:
                        await app.bot.send_photo(
                            chat_id=chat_id,
                            photo=current_photo,
                            caption=final_msg,
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Send photo error: {e}")
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=final_msg,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                else:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=final_msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )

                # Wait 1s for THIS token/worker
                await asyncio.sleep(1)
            else:
                final_msg = f"{T('fail_title', lang)}\nInfo:\n<code>{msg_result}</code>"
                await edit(final_msg)
                
            request_queue.task_done()
            
        except Exception as e:
            logger.error(f"Worker #{worker_id} Exception: {e}")
            request_queue.task_done()

def get_main_menu_keyboard(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T("btn_input", lang), callback_data="menu_input")],
        [InlineKeyboardButton("🤝 Referral" if lang == "EN" else "🤝 Giới Thiệu", callback_data="menu_ref"),
         InlineKeyboardButton("💎 VIP Key", callback_data="menu_redeem")],
        [InlineKeyboardButton(T("btn_lang", lang), callback_data="menu_lang"),
         InlineKeyboardButton(T("btn_help", lang), callback_data="menu_help")]
    ])

def run_bot():
    logging.basicConfig(
        format='%(message)s',
        level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("telegram").setLevel(logging.ERROR)
    logging.getLogger("aiohttp").setLevel(logging.ERROR)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Load saved tokens from DB (overwrite config defaults if saved)
    saved_tokens = db.load_token_sets()
    for idx, token_data in saved_tokens.items():
        if idx < len(TOKEN_SETS):
            TOKEN_SETS[idx] = token_data
            print(f"  ✅ Loaded Token #{idx+1} from DB: {token_data.get('name', 'Unknown')}")
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("noti", noti_command))
    app.add_handler(CommandHandler("rs", reset_command))
    app.add_handler(CommandHandler("setdonate", set_donate_command))
    app.add_handler(CommandHandler("settoken", settoken_command))
    app.add_handler(CommandHandler("viewtoken", viewtoken_command))
    app.add_handler(CommandHandler("setlimit", setlimit_command))
    app.add_handler(CommandHandler("genkey", genkey_command))
    app.add_handler(CommandHandler("listkeys", listkeys_command))
    app.add_handler(CommandHandler("redeem", redeem_command))
    app.add_handler(CommandHandler("myref", myref_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_token_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    async def post_init(application):
        # Dynamically create workers based on config
        for i in range(1, NUM_WORKERS + 1):
            asyncio.create_task(queue_worker(application, i))

    app.post_init = post_init
    print(f"Bot is running... ({NUM_WORKERS} workers, {len(TOKEN_SETS)} token sets)")
    app.run_polling()