import asyncio
import logging
import random
import time
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from app.config import * # Đảm bảo app/config.py đã có biến ADMIN_IDS = [...]
from app import database as db
from app.services import locket, nextdns

logger = logging.getLogger(__name__)

request_queue = asyncio.Queue()
pending_items = []
queue_lock = asyncio.Lock()

# Anti-Spam Dictionary: {user_id: last_click_timestamp}
user_clicks = {}

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
    
    if not db.get_user_usage(user_id):
        pass 

    banner = db.get_config("start_banner", None)
    
    if update.callback_query:
        # Avoid exception if we edit with identical message
        try:
            if banner:
                await update.callback_query.message.delete()
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=banner,
                    caption=T("welcome", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_main_menu_keyboard(lang)
                )
            else:
                await update.callback_query.edit_message_text(
                    T("welcome", lang),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_main_menu_keyboard(lang)
                )
        except: pass
    else:
        if banner:
            await update.message.reply_photo(
                photo=banner,
                caption=T("welcome", lang),
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu_keyboard(lang)
            )
        else:
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
            f"/noti [msg] - Broadcast msg, use {{name}} for dynamic names\n"
            f"/setbanner - Set /start welcome image\n"
            f"/setdonate - Set success photo\n"
            f"/rs [id] - Reset usage limit\n"
            f"/stats - View statistics"
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

# --- User Commands ---
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    if not context.args:
        await update.message.reply_text(T("feedback_empty", lang), parse_mode=ParseMode.HTML)
        return
        
    msg_text = update.message.text.split(maxsplit=1)[1]
    name = update.effective_user.full_name or "Không rõ"
    
    feedback_content = f"📩 <b>NEW FEEDBACK</b>\n👤 <b>Từ:</b> {name} [<code>{user_id}</code>]\n\n📝 <b>Nội dung:</b>\n{msg_text}"
    
    # Gửi tin nhắn này cho admin đầu tiên trong list (hoặc tất cả)
    admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
    if admin_id:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=feedback_content,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"Nhắn riêng cho User", url=f"tg://user?id={user_id}")]])
            )
            # Thêm Reply chức năng nếu admin reply tin nhắn
        except Exception as e:
            logger.error(f"Cannot send feedback to admin: {e}")
            pass
            
    await update.message.reply_text(T("feedback_sent", lang), parse_mode=ParseMode.HTML)

# --- Admin Commands ---
async def broadcast_worker(bot, users, text, chat_id, message_id):
    success = 0
    fail = 0
    total = len(users)
    
    for i, user_record in enumerate(users):
        uid = user_record['id']
        name = user_record['name'] or "bạn"
        
        # Format the dynamic message
        custom_text = text.replace("{name}", name).replace("{id}", str(uid))
        
        try:
            await bot.send_message(
                chat_id=uid, 
                text=f"📢 <b>ADMIN NOTIFICATION</b>\n\n{custom_text}", 
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👌 Đã hiểu (Dismiss)", callback_data="dismiss_msg")]])
            )
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

    users_list = []
    if db.db is not None:
        # Lấy full users list thay vì chỉ distinct
        all_logs = db.db.usage_logs.find({})
        all_settings = db.db.user_settings.find({})
        seen = set()
        
        for u in all_logs:
            if u["user_id"] not in seen:
                users_list.append({"id": u["user_id"], "name": u.get("name", "")})
                seen.add(u["user_id"])
        
        for u in all_settings:
            if u["user_id"] not in seen:
                users_list.append({"id": u["user_id"], "name": u.get("name", "")})
                seen.add(u["user_id"])
                
    if not users_list:
        await update.message.reply_text("No users found in database.")
        return

    status_msg = await update.message.reply_text(
        f"{E_LOADING} <b>Starting broadcast to {len(users_list)} users...</b>",
        parse_mode=ParseMode.HTML
    )
    
    asyncio.create_task(broadcast_worker(context.bot, users_list, msg, status_msg.chat_id, status_msg.message_id))

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

async def set_banner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
        
    photo = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo = update.message.reply_to_message.photo[-1]
    elif update.message.photo:
        photo = update.message.photo[-1]
        
    if photo:
        file_id = photo.file_id
        db.set_config("start_banner", file_id)
        await update.message.reply_text(f"✅ Updated Welcome Banner Photo ID:\n<code>{file_id}</code>", parse_mode=ParseMode.HTML)
    else:
        # If no photo, clear the banner
        db.set_config("start_banner", None)
        await update.message.reply_text("❌ Banner removed. Bot will use text only for /start.")

async def admin_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
        
    if update.message.reply_to_message and "NEW FEEDBACK" in (update.message.reply_to_message.text or ""):
        # Extract target user_id from the format [12345678]
        try:
            target_str = update.message.reply_to_message.text.split("Từ:")[1].split("[")[1].split("]")[0]
            target_uid = int(target_str)
            
            admin_answer = update.message.text
            await context.bot.send_message(
                chat_id=target_uid,
                text=f"👑 <b>Admin vừa trả lời bạn:</b>\n\n{admin_answer}",
                parse_mode=ParseMode.HTML
            )
            await update.message.reply_text("✅ Đã chuyển câu trả lời cho User.")
        except Exception as e:
            await update.message.reply_text(f"❌ Không lấy được ID User từ tin nhắn gốc để reply. {e}")

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
    
    # Sửa / Cập nhật Tên User
    db.set_user_name(user_id, update.effective_user.full_name)

    if "locket.cam/" in text:
        username = text.split("locket.cam/")[-1].split("?")[0]
    elif len(text) < 50 and " " not in text:
        username = text
    else:
        username = text

    await queue_request(update, context, username)


async def queue_request(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    msg = await update.message.reply_text(T("resolving", lang), parse_mode=ParseMode.HTML)
    
    uid = await locket.resolve_uid(username)
    if not uid:
        await msg.edit_text(T("not_found", lang), parse_mode=ParseMode.HTML)
        return
        
    # [V-EDIT] Admin bypass limit check (List support)
    if user_id not in ADMIN_IDS and not db.check_can_request(user_id):
        await msg.edit_text(T("limit_reached", lang), parse_mode=ParseMode.HTML)
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

async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý dữ liệu trả về từ Telegram Mini App"""
    if not update.message.web_app_data:
        return
        
    user_id = update.effective_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    db.set_user_name(user_id, update.effective_user.full_name)
    
    try:
        data = json.loads(update.message.web_app_data.data)
        if data.get("action") == "activate" and data.get("username"):
            username = data["username"]
            
            # Xử lý locket.cam link format y như handle_text
            if "locket.cam/" in username:
                username = username.split("locket.cam/")[-1].split("?")[0]
            elif "locket.com/" in username:
                username = username.split("locket.com/")[-1].split("?")[0]
            
            await queue_request(update, context, username)
    except Exception as e:
        logger.error(f"WebApp Data parse error: {e}")
        await update.message.reply_text(f"{E_ERROR} Lỗi xử lý dữ liệu WebApp!", parse_mode=ParseMode.HTML)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    lang = db.get_lang(user_id) or DEFAULT_LANG
    
    # 🛡️ ANTI-SPAM LOGIC
    current_time = time.time()
    last_click = user_clicks.get(user_id, 0)
    
    if current_time - last_click < 2:
        try:
            await query.answer(T("spam_warning", lang), show_alert=True)
        except: pass
        return
        
    user_clicks[user_id] = current_time

    if data == "dismiss_msg":
        try:
            await query.message.delete()
        except: pass
        return

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
        f"/noti [msg] - Broadcast msg, use {{name}} for dynamic names\n"
        f"/setbanner - Set /start welcome image\n"
        f"/setdonate - Set success photo\n"
        f"/rs [id] - Reset usage limit\n"
        f"/stats - View statistics"
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
                await query.answer(T("limit_reached", lang), show_alert=True)
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
    # Select token based on worker ID (round-robin)
    # worker_id is 1-based, so subtract 1
    token_idx = (worker_id - 1) % len(TOKEN_SETS)
    token_config = TOKEN_SETS[token_idx]
    token_name = f"Token-{token_idx+1}"
    
    print(f"Worker #{worker_id} started using {token_name}...")
    
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
                await edit(T("limit_reached", lang))
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
    # Sử dụng WebApp rực rỡ
    webapp_url = "https://locketgold-926y.onrender.com/webapp"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 MỞ TOOL HACK LOCKET", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton(T("btn_input", lang), callback_data="menu_input")],
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
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("noti", noti_command))
    app.add_handler(CommandHandler("rs", reset_command))
    app.add_handler(CommandHandler("setdonate", set_donate_command))
    app.add_handler(CommandHandler("setbanner", set_banner_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Tách luồng xử lý tin tức text: một cho Admin trả lời feedback, một cho logic nhập UID, một cho WebApp
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY, admin_reply_handler), group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text), group=0)
    
    async def post_init(application):
        # Dynamically create workers based on config
        for i in range(1, NUM_WORKERS + 1):
            asyncio.create_task(queue_worker(application, i))

    app.post_init = post_init
    print(f"Bot is running... ({NUM_WORKERS} workers)")
    app.run_polling()