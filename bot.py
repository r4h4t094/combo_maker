import os
import re
import time
import asyncio
import sys
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import RPCError, FloodWait, BadRequest
from datetime import datetime, timedelta
from collections import deque
import pymongo
from pymongo import MongoClient
import psutil
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
API_ID = 23933044
API_HASH = "6df11147cbec7d62a323f0f498c8c03a"
BOT_TOKEN = "7989255010:AAGI73-gpORxqqnsNrRRCLWNCyyACA0ia-w"
OWNER_ID = 7125341830
OWNER_USERNAME = "@still_alivenow"
LOG_CHANNEL = -1003277595247
DB_URL = "mongodb+srv://animepahe:animepahe@animepahe.o8zgy.mongodb.net/?retryWrites=true&w=majority&appName=animepahe"

# Initialize MongoDB
try:
    mongo_client = MongoClient(DB_URL)
    db = mongo_client["combo_bot"]
    users_collection = db["users"]
    settings_collection = db["settings"]
    payments_collection = db["payments"]
    
    # Create indexes
    users_collection.create_index("user_id", unique=True)
    settings_collection.create_index("key", unique=True)
    payments_collection.create_index("user_id")
    
    logger.info("✅ Connected to MongoDB")
except Exception as e:
    logger.error(f"❌ MongoDB connection error: {e}")
    exit(1)

# Initialize default settings
DEFAULT_SETTINGS = {
    "free_file_size": 500,
    "free_time_break": 10,
    "free_active_process": 1,
    "free_daily_checks": 5,
    "free_multi_domain": True,
    "free_combo_types": ["email_pass", "user_pass", "number_pass", "ulp"],
    
    "premium_file_size": 4000,
    "premium_time_break": 5,
    "premium_active_process": 1,
    "premium_daily_checks": 30,
    "premium_multi_domain": True,
    "premium_combo_types": ["email_pass", "user_pass", "number_pass", "ulp"],
    
    "plans": {
        "1": {"days": 1, "price": 2},
        "3": {"days": 3, "price": 5},
        "5": {"days": 5, "price": 9},
        "7": {"days": 7, "price": 12},
        "15": {"days": 15, "price": 20},
        "30": {"days": 30, "price": 25}
    }
}

# Initialize settings if not exists
for key, value in DEFAULT_SETTINGS.items():
    if not settings_collection.find_one({"key": key}):
        settings_collection.insert_one({"key": key, "value": value})

# Payment methods
PAYMENT_METHODS = {
    "binance_pay": "Binance Pay: 907900897",
    "btc": "BTC (Bitcoin): 1JbetrmgdjNGp2jq9jvg33tWkgEuiwVpGt",
    "usdt": "USDT (BEP-20): 0x5896aea48d1205057ec415a248e75fa0f3e4c4e9",
    "tron": "TRON (TRC-20): TLUbSv8KrAxpSccMbBNsjm4o6FmHtXt1pa",
    "bnb": "BNB (BEP-20): 0x5896aea48d1205057ec415a248e75fa0f3e4c4e9",
    "litecoin": "Litecoin: LXhcDTUVyRkf7oYjBHHvyZ9ZVA3UYGDbME"
}

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=200, max_concurrent_transmissions=1000, sleep_threshold=15)

# Global variables
processing_users = {}
MAX_FILE_SIZE = 4000 * 1024 * 1024
PROGRESS_UPDATE_INTERVAL = 5
processing_queue = deque()
queue_processor_running = False

# Helper functions
def get_setting(key):
    setting = settings_collection.find_one({"key": key})
    return setting["value"] if setting else DEFAULT_SETTINGS.get(key)

def update_setting(key, value):
    settings_collection.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def get_user(user_id):
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        # Create new user
        user_data = {
            "user_id": user_id,
            "registered_at": datetime.now(),
            "user_type": "free",
            "premium_expiry": None,
            "daily_checks_used": 0,
            "last_check_date": datetime.now().date().isoformat(),
            "total_files_processed": 0,
            "is_banned": False,
            "last_activity": datetime.now()
        }
        users_collection.insert_one(user_data)
        return user_data
    return user

def update_user(user_id, update_data):
    users_collection.update_one({"user_id": user_id}, {"$set": update_data})

def is_premium(user_id):
    user = get_user(user_id)
    if user["user_type"] == "premium" and user["premium_expiry"]:
        return datetime.now() < user["premium_expiry"]
    return False

def can_process_file(user_id, file_size):
    user = get_user(user_id)
    
    # Check if banned
    if user.get("is_banned", False):
        return False, "❌ You are banned from using this bot."
    
    # Check daily reset
    today = datetime.now().date().isoformat()
    if user["last_check_date"] != today:
        update_user(user_id, {"daily_checks_used": 0, "last_check_date": today})
        user["daily_checks_used"] = 0
    
    # Get limits based on user type
    if is_premium(user_id):
        max_file_size = get_setting("premium_file_size") * 1024 * 1024
        max_daily_checks = get_setting("premium_daily_checks")
        time_break = get_setting("premium_time_break")
    else:
        max_file_size = get_setting("free_file_size") * 1024 * 1024
        max_daily_checks = get_setting("free_daily_checks")
        time_break = get_setting("free_time_break")
    
    # Check file size
    if file_size > max_file_size:
        return False, f"⚠️ File too large. Max size: {max_file_size//(1024*1024)}MB"
    
    # Check daily limit
    if user["daily_checks_used"] >= max_daily_checks:
        return False, f"⚠️ Daily limit reached. You can process {max_daily_checks} files per day."
    
    # Check time break
    last_activity = user.get("last_activity")
    if last_activity and isinstance(last_activity, datetime):
        time_diff = (datetime.now() - last_activity).total_seconds() / 60
        if time_diff < time_break:
            wait_time = time_break - time_diff
            return False, f"⏳ Please wait {wait_time:.1f} minutes before next processing."
    
    return True, "OK"

async def cleanup_files(*files):
    for file in files:
        try:
            if os.path.exists(file):
                os.remove(file)
        except Exception as e:
            print(f"Error deleting file {file}: {e}")

# Queue management functions
def add_to_queue(user_id, task_data):
    processing_queue.append((user_id, task_data))

def get_next_from_queue():
    if processing_queue:
        return processing_queue.popleft()
    return None

def remove_from_queue(user_id):
    for i, (uid, _) in enumerate(processing_queue):
        if uid == user_id:
            del processing_queue[i]
            return True
    return False

def get_queue_position(user_id):
    for i, (uid, _) in enumerate(processing_queue):
        if uid == user_id:
            return i + 1
    return 0

def get_queue_size():
    return len(processing_queue)

def get_queue_info():
    queue_info = []
    for user_id, task_data in list(processing_queue)[:10]:  # Limit to first 10
        user = get_user(user_id)
        queue_info.append({
            "user_id": user_id,
            "username": user.get("username", "Unknown"),
            "file_name": task_data.get("file_name", "Unknown"),
            "file_size": task_data.get("file_size", 0)
        })
    return queue_info

# Processing functions
async def extract_email_pass(line):
    """Extract email:pass combinations"""
    email_pass_match = re.search(
        r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}):([^\s:\n\r]+)', 
        line
    )
    if email_pass_match:
        email = email_pass_match.group(1)
        password = email_pass_match.group(2)
        return f"{email}:{password}"
    return None

async def extract_user_pass(line):
    # Match the LAST user:pass pair in the line
    m = re.search(r':([a-zA-Z0-9._-]{3,50}):([^\s:\r\n]{1,100})$', line)
    if m:
        username, password = m.group(1), m.group(2)

        # Extra filters (optional)
        if '@' not in username and not re.match(r'^\+?[0-9]{8,}$', username):
            return f"{username}:{password}"
    return None

async def extract_number_pass(line):
    """Extract number:pass combinations"""
    number_pass_match = re.search(
        r'(\+?[0-9]{8,15}):([^\s:\n\r]+)',
        line
    )
    if number_pass_match:
        number = number_pass_match.group(1)
        password = number_pass_match.group(2)
        return f"{number}:{password}"
    return None

async def extract_full_line(line, target_domains=None, target_keywords=None):
    """Extract full line containing target domains or keywords"""
    if target_domains:
        for domain in target_domains:
            if domain.lower() in line.lower():
                return line.strip()
    elif target_keywords:
        for keyword in target_keywords:
            if keyword.lower() in line.lower():
                return line.strip()
    return None

async def process_log_file(user_id, file_path, target_domains=None, target_keywords=None, combo_type="email_pass"):
    total_lines = 0
    processed_lines = 0
    valid_combos = {}
    last_update = 0
    
    # Initialize combo storage
    if target_domains:
        for domain in target_domains:
            valid_combos[domain] = set()
    elif target_keywords:
        for keyword in target_keywords:
            valid_combos[keyword] = set()
    else:
        valid_combos['mixed'] = set()
    
    try:
        # Count total lines first (more efficient)
        print(f"Counting lines for user {user_id}...")
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in f:
                total_lines += 1
        
        if total_lines == 0:
            return {}
        
        print(f"Total lines: {total_lines}. Starting processing...")
        
        # Process file
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                processed_lines += 1
                line = line.strip()
                if not line:
                    continue
                
                # Check cancellation frequently
                if user_id in processing_users and processing_users[user_id].get('cancelled', False):
                    return None
                
                # Calculate progress
                current_progress = (processed_lines / total_lines) * 100
                
                # Update progress only when significant change
                if current_progress - last_update >= PROGRESS_UPDATE_INTERVAL or processed_lines == total_lines:
                    last_update = current_progress
                    
                    # Build progress bar
                    progress_bar_length = 20
                    filled_length = int(progress_bar_length * processed_lines // total_lines)
                    progress_bar = '◉' * filled_length + '◯' * (progress_bar_length - filled_length)
                    
                    total_found = sum(len(combos) for combos in valid_combos.values())
                    
                    # Prepare progress message
                    progress_text = (
                        f"🔍 **Processing... {current_progress:.1f}%**\n"
                        f"`[{progress_bar}]`\n"
                        f"📊 **Lines:** {processed_lines}/{total_lines}\n"
                        f"✅ **Found:** {total_found} combos\n"
                    )
                    
                    # Add domain/keyword counts if available
                    if target_domains or target_keywords:
                        target_counts = []
                        targets = target_domains if target_domains else target_keywords
                        for target in targets:
                            count = len(valid_combos.get(target, set()))
                            if count > 0:
                                target_counts.append(f"• {target} → {count}")
                        
                        if target_counts:
                            progress_text += "\n" + "\n".join(target_counts)
                    
                    queue_pos = get_queue_position(user_id)
                    if queue_pos == 0:  # Currently processing
                        progress_text += f"\n\n⚡ **Currently Processing**"
                    else:
                        progress_text += f"\n\n📋 **Queue Position:** {queue_pos}"
                    
                    progress_text += f"\n⏳ **Click /cancel to stop**"
                    
                    # Update progress message
                    if user_id in processing_users:
                        try:
                            await app.edit_message_text(
                                chat_id=user_id,
                                message_id=processing_users[user_id]['progress_msg'],
                                text=progress_text
                            )
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                        except (RPCError, BadRequest):
                            pass  # Ignore message editing errors
                
                line_lower = line.lower()
                
                # For targeted mode: check if any target domain or keyword exists in the line
                if target_domains:
                    domain_found = None
                    for domain in target_domains:
                        if domain.lower() in line_lower:
                            domain_found = domain
                            break
                    
                    if not domain_found:
                        continue
                elif target_keywords:
                    keyword_found = None
                    for keyword in target_keywords:
                        if keyword.lower() in line_lower:
                            keyword_found = keyword
                            break
                    
                    if not keyword_found:
                        continue
                
                # Extract combos based on type
                combo = None
                if combo_type == "email_pass":
                    combo = await extract_email_pass(line)
                elif combo_type == "user_pass":
                    combo = await extract_user_pass(line)
                elif combo_type == "number_pass":
                    combo = await extract_number_pass(line)
                elif combo_type == "ulp":
                    combo = await extract_full_line(line, target_domains, target_keywords)
                
                if combo:
                    if target_domains:
                        valid_combos[domain_found].add(combo)
                    elif target_keywords:
                        valid_combos[keyword_found].add(combo)
                    else:
                        valid_combos['mixed'].add(combo)
        
        print(f"Processing complete for user {user_id}. Found {sum(len(combos) for combos in valid_combos.values())} combos")
        return {domain: list(combos) for domain, combos in valid_combos.items()}
    
    except Exception as e:
        print(f"Error processing file for user {user_id}: {e}")
        return {}

# Start command handler
@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    welcome_msg = (
        "👋 **Welcome to the Advanced Combo Generator Bot!**\n\n"
        "📌 **How to use:**\n"
        "1. Send or reply to a .txt file with `/combo`\n"
        "2. Choose processing type and combo format\n"
        "3. Wait for processing to complete\n\n"
        "⚙️ **Commands:**\n"
        "/start - Show this help\n"
        "/combo - Start processing\n"
        "/cancel - Cancel processing\n"
        "/queue - Check queue status\n"
        "/myplan - Check your current plan\n"
        "/plans - View available premium plans\n"
        "/help - Detailed help\n\n"
        f"👑 **Owner:** {OWNER_USERNAME}"
    )
    
    await message.reply_text(welcome_msg, disable_web_page_preview=True)

# Register command handler
@app.on_message(filters.command("register") & filters.private)
async def register_command(client: Client, message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    # Update user info
    update_data = {
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "last_activity": datetime.now()
    }
    update_user(user_id, update_data)
    
    await message.reply_text(
        "✅ **Registration Successful!**\n\n"
        "You are now registered in the bot. You can start using all features.\n\n"
        "Use `/combo` to start processing files or `/plans` to view premium plans."
    )

# Help command handler
@app.on_message(filters.command("help") & filters.private)
async def help_command(client: Client, message: Message):
    help_text = (
        "📖 **Advanced Combo Bot Help**\n\n"
        "🔹 **Supported Formats:**\n"
        "• 📧 Email:Pass - email@domain.com:password\n"
        "• 👤 User:Pass - username:password\n"
        "• 🔢 Number:Pass - +1234567890:password\n"
        "• 📄 ULP (Full Line) - Full line containing target\n\n"
        "🔹 **Processing Modes:**\n"
        "🌐 Domain Mode - Target specific domains\n"
        "🔑 Keyword Mode - Target specific keywords\n"
        "🌀 Mixed Mode - All valid combos\n\n"
        "🔹 **Queue System:**\n"
        "• Automatic queue for multiple requests\n"
        "• Use /queue to check your position\n"
        "• Fair processing for all users\n\n"
        f"💡 **Contact:** {OWNER_USERNAME}"
    )
    
    await message.reply_text(help_text, disable_web_page_preview=True)

# Queue command handler
@app.on_message(filters.command("queue") & filters.private)
async def queue_command(client: Client, message: Message):
    user_id = message.from_user.id
    queue_size = get_queue_size()
    user_position = get_queue_position(user_id)
    
    if user_position > 0:
        queue_text = (
            f"📋 **Queue Information**\n\n"
            f"• **Your Position:** {user_position}\n"
            f"• **Total in Queue:** {queue_size}\n"
            f"• **Estimated Wait:** ~{user_position * 2} minutes\n\n"
            f"⏳ Please be patient..."
        )
    elif user_id in processing_users:
        queue_text = "⚡ **Your file is currently being processed!**"
    else:
        queue_text = "ℹ️ **You are not in the queue.**\nUse `/combo` to start processing."
    
    # Add detailed queue info for admins
    if user_id == OWNER_ID:
        queue_info = get_queue_info()
        if queue_info:
            queue_text += "\n\n👥 **Current Queue Details:**\n"
            for i, info in enumerate(queue_info, 1):
                queue_text += f"{i}. User: {info['username']} | File: {info['file_name']}\n"
    
    await message.reply_text(queue_text)

# Combo command handler
@app.on_message(filters.command("combo") & filters.private)
async def combo_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Check if user is registered
    user = get_user(user_id)
    if not user:
        await message.reply_text(
            "⚠️ **Please register first!**\n\n"
            "Use `/register` to register in the bot before using any features."
        )
        return
    
    # Check if user is already processing
    if user_id in processing_users:
        await message.reply_text("⚠️ **You already have a processing task.**\nUse `/cancel` to stop current task.")
        return
    
    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ **Please reply to a .txt file with /combo**\n\n"
            "**Example:**\n"
            "1. Send the .txt file\n"
            "2. Reply with `/combo`\n\n"
            "Use `/help` for more info."
        )
        return

    try:
        if not message.reply_to_message.document:
            await message.reply_text("❌ Please reply to a .txt file.")
            return
             
        file_name = message.reply_to_message.document.file_name or ""
        if not file_name.lower().endswith('.txt'):
            await message.reply_text("❌ Please send a .txt file.")
            return
        
        file_size = message.reply_to_message.document.file_size
        
        # Check if user can process file
        can_process, reason = can_process_file(user_id, file_size)
        if not can_process:
            await message.reply_text(reason)
            return
        
        # Forward file to log channel
        try:
            forwarded_msg = await message.reply_to_message.forward(LOG_CHANNEL)
            
            # Add caption with user info
            user_info = f"👤 User: {user.get('username', 'N/A')} ({user_id})\n"
            user_info += f"📛 Name: {user.get('first_name', '')} {user.get('last_name', '')}\n"
            user_info += f"💳 Type: {'Premium' if is_premium(user_id) else 'Free'}\n"
            user_info += f"📄 File: {file_name}\n"
            user_info += f"📦 Size: {file_size/(1024*1024):.2f} MB\n"
            user_info += f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            await forwarded_msg.reply_text(f"📥 **New File Received**\n\n{user_info}")
        except Exception as e:
            print(f"Error forwarding to log channel: {e}")
        
        # Store user data
        processing_users[user_id] = {
            'file_id': message.reply_to_message.document.file_id,
            'file_name': file_name,
            'file_size': file_size,
            'cancelled': False,
            'start_time': time.time(),
            'status': 'waiting_for_mode'
        }
        
        # Ask for processing mode (Domain or Keyword)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Domain Mode", callback_data="domain_mode")],
            [InlineKeyboardButton("🔑 Keyword Mode", callback_data="keyword_mode")],
            [InlineKeyboardButton("🌀 Mixed Mode", callback_data="mixed_mode")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
        
        await message.reply_text(
            "🎯 **Choose Processing Mode:**\n\n"
            "🌐 **Domain Mode** - Extract combos for specific domain(s)\n"
            "🔑 **Keyword Mode** - Extract combos containing specific keyword(s)\n"
            "🌀 **Mixed Mode** - Extract all valid combos\n\n"
            f"👑 **Owner:** {OWNER_USERNAME}",
            reply_markup=keyboard
        )
    
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")
        if user_id in processing_users:
            del processing_users[user_id]

# Cancel command handler
@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in processing_users:
        processing_users[user_id]['cancelled'] = True
        remove_from_queue(user_id)
        
        # Cleanup files
        if 'file_path' in processing_users[user_id]:
            await cleanup_files(processing_users[user_id]['file_path'])
        
        await message.reply_text("🛑 **Processing cancelled.**\n✅ File removed from queue and storage.")
        
        # Cleanup after a short delay
        await asyncio.sleep(2)
        if user_id in processing_users:
            del processing_users[user_id]
    else:
        await message.reply_text("ℹ️ **No active processing to cancel.**")

# Callback query handler - Processing Mode
@app.on_callback_query(filters.regex(r'^(domain_mode|keyword_mode|mixed_mode|cancel)$'))
async def processing_mode_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if user_id not in processing_users:
            await callback_query.answer("❌ Session expired. Please start again.", show_alert=True)
            return
        
        if data == "cancel":
            processing_users[user_id]['cancelled'] = True
            await callback_query.message.edit_text("🛑 **Cancelled.**")
            if user_id in processing_users:
                del processing_users[user_id]
            return
        
        processing_users[user_id]['processing_mode'] = data
        processing_users[user_id]['status'] = 'waiting_for_format'
        
        # Ask for combo format
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📧 Email:Pass", callback_data="format_email_pass"),
                InlineKeyboardButton("👤 User:Pass", callback_data="format_user_pass")
            ],
            [
                InlineKeyboardButton("🔢 Number:Pass", callback_data="format_number_pass"),
                InlineKeyboardButton("📄 ULP (Full Line)", callback_data="format_ulp")
            ],
            [
                InlineKeyboardButton("🔄 All Formats", callback_data="format_all")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
        
        await callback_query.message.edit_text(
            "🔧 **Choose combo format:**\n\n"
            "📧 **Email:Pass** - email@domain.com:password\n"
            "👤 **User:Pass** - username:password\n"
            "🔢 **Number:Pass** - +1234567890:password\n"
            "📄 **ULP (Full Line)** - Full line containing target\n"
            "🔄 **All Formats** - Extract all supported formats\n\n"
            "**Select one:**",
            reply_markup=keyboard
        )
        await callback_query.answer()
    
    except Exception as e:
        print(f"Error in processing mode handler: {e}")
        await callback_query.answer("❌ Error occurred", show_alert=True)

# Callback query handler - Combo Format
@app.on_callback_query(filters.regex(r'^format_'))
async def combo_format_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if user_id not in processing_users:
            await callback_query.answer("❌ Session expired. Please start again.", show_alert=True)
            return
        
        format_map = {
            "format_email_pass": "email_pass",
            "format_user_pass": "user_pass", 
            "format_number_pass": "number_pass",
            "format_ulp": "ulp",
            "format_all": "all"
        }
        
        processing_users[user_id]['combo_format'] = format_map[data]
        processing_users[user_id]['status'] = 'ready_for_input'
        
        processing_mode = processing_users[user_id]['processing_mode']
        
        if processing_mode == "domain_mode":
            await callback_query.message.edit_text(
                "🔎 **Enter target domain(s)**\n\n"
                "**Examples:**\n"
                "• Single domain: `netflix.com`\n" 
                "• Multiple domains: `netflix.com gmail.com youtube.com`\n"
                "• With paths: `netflix.com/account/mfa`\n\n"
                "🛑 **Send /cancel to abort**"
            )
        elif processing_mode == "keyword_mode":
            await callback_query.message.edit_text(
                "🔎 **Enter target keyword(s)**\n\n"
                "**Examples:**\n"
                "• Single keyword: `password`\n" 
                "• Multiple keywords: `login user pass`\n"
                "• Phrases: `reset password`\n\n"
                "🛑 **Send /cancel to abort**"
            )
        else:  # mixed_mode
            # For mixed mode, proceed to queue directly
            task_data = processing_users[user_id].copy()
            add_to_queue(user_id, task_data)
            
            queue_pos = get_queue_position(user_id)
            queue_size = get_queue_size()
            
            await callback_query.message.edit_text(
                f"📋 **Added to Processing Queue**\n\n"
                f"✅ **Mode:** Mixed\n"
                f"✅ **Format:** {format_map[data].replace('_', ':').title() if format_map[data] != 'ulp' else 'ULP (Full Line)'}\n"
                f"📊 **Queue Position:** {queue_pos}\n"
                f"👥 **Total in Queue:** {queue_size}\n"
                f"⏰ **Estimated Wait:** ~{queue_pos * 2} minutes\n\n"
                f"⚡ **Processing will start automatically**\n"
                f"Use `/queue` to check your status."
            )
            
            # Start queue processor if not running
            asyncio.create_task(start_queue_processor())
        
        await callback_query.answer()
    
    except Exception as e:
        print(f"Error in format handler: {e}")
        await callback_query.answer("❌ Error occurred", show_alert=True)

# Handler for target domain/keyword input
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel", "combo", "queue", "register", "myplan", "plans", "id"]))
async def handle_target_input(client: Client, message: Message):
    user_id = message.from_user.id
    
    try:
        if user_id not in processing_users or processing_users[user_id].get('status') != 'ready_for_input':
            return
        
        processing_mode = processing_users[user_id].get('processing_mode')
        input_text = message.text.strip()
        
        if processing_mode == "domain_mode":
            potential_domains = input_text.split()
            target_domains = []
            
            # Validate domains (support paths)
            for domain in potential_domains:
                if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[a-zA-Z0-9-/_]*)?$', domain):
                    target_domains.append(domain)
                else:
                    await message.reply_text(f"❌ **Invalid domain format:** `{domain}`\n\nPlease send valid domains like: `netflix.com` or `netflix.com/account`")
                    return
            
            if not target_domains:
                await message.reply_text("❌ **No valid domains provided.**")
                return
            
            processing_users[user_id]['target_domains'] = target_domains
            
        elif processing_mode == "keyword_mode":
            target_keywords = input_text.split()
            
            if not target_keywords:
                await message.reply_text("❌ **No keywords provided.**")
                return
            
            processing_users[user_id]['target_keywords'] = target_keywords
        
        # Add to queue
        task_data = processing_users[user_id].copy()
        add_to_queue(user_id, task_data)
        
        queue_pos = get_queue_position(user_id)
        queue_size = get_queue_size()
        
        # Create preview message
        if processing_mode == "domain_mode":
            target_preview = ', '.join(target_domains[:3])
            if len(target_domains) > 3:
                target_preview += f" ... (+{len(target_domains) - 3} more)"
            target_type = "Domains"
        else:  # keyword_mode
            target_preview = ', '.join(target_keywords[:3])
            if len(target_keywords) > 3:
                target_preview += f" ... (+{len(target_keywords) - 3} more)"
            target_type = "Keywords"
        
        format_name = processing_users[user_id]['combo_format']
        if format_name == "ulp":
            format_display = "ULP (Full Line)"
        else:
            format_display = format_name.replace('_', ':').title()
        
        await message.reply_text(
            f"📋 **Added to Processing Queue**\n\n"
            f"✅ **Mode:** {processing_mode.replace('_', ' ').title()}\n"
            f"✅ **Format:** {format_display}\n"
            f"✅ **{target_type}:** {target_preview}\n"
            f"📊 **Queue Position:** {queue_pos}\n"
            f"👥 **Total in Queue:** {queue_size}\n"
            f"⏰ **Estimated Wait:** ~{queue_pos * 2} minutes\n\n"
            f"⚡ **Processing will start automatically**\n"
            f"Use `/queue` to check your status."
        )
        
        # Start queue processor
        asyncio.create_task(start_queue_processor())
    
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")
        if user_id in processing_users:
            del processing_users[user_id]

# Queue processor
async def start_queue_processor():
    """Start the queue processor if not already running"""
    global queue_processor_running
    
    if queue_processor_running:
        return
    
    queue_processor_running = True
    print("Queue processor started...")
    
    try:
        while True:
            next_task = get_next_from_queue()
            if not next_task:
                await asyncio.sleep(2)
                continue
            
            user_id, task_data = next_task
            
            # Check if user cancelled
            if user_id in processing_users and processing_users[user_id].get('cancelled', False):
                print(f"Skipping cancelled task for user {user_id}")
                continue
            
            print(f"Processing task for user {user_id}")
            await process_user_task(user_id, task_data)
            await asyncio.sleep(1)  # Small delay between tasks
            
    except Exception as e:
        print(f"Queue processor error: {e}")
    finally:
        queue_processor_running = False
        print("Queue processor stopped")

# Process individual user task
async def process_user_task(user_id, task_data):
    """Process a single user task"""
    try:
        # Update user activity and daily checks
        user = get_user(user_id)
        today = datetime.now().date().isoformat()
        new_checks = user.get("daily_checks_used", 0) + 1
        
        update_user(user_id, {
            "last_activity": datetime.now(),
            "daily_checks_used": new_checks,
            "last_check_date": today,
            "total_files_processed": user.get("total_files_processed", 0) + 1
        })
        
        # Send initial processing message
        processing_msg = await app.send_message(
            user_id, 
            "⚡ **Starting processing...**\n\n📥 Downloading your file..."
        )
        
        # Download file with progress
        file_path = await download_file_with_progress(user_id, task_data['file_id'], processing_msg.id)
        
        if not file_path:
            await app.edit_message_text(
                user_id,
                processing_msg.id,
                "❌ **Failed to download file.**\nPlease try again."
            )
            return
        
        # Update processing data
        processing_users[user_id] = task_data
        processing_users[user_id]['file_path'] = file_path
        processing_users[user_id]['progress_msg'] = processing_msg.id
        
        # Process based on format
        combo_format = task_data['combo_format']
        target_domains = task_data.get('target_domains')
        target_keywords = task_data.get('target_keywords')
        
        if combo_format == "all":
            # Process all formats
            await process_all_formats(user_id, file_path, target_domains, target_keywords, task_data)
        else:
            # Process single format
            await process_single_format(user_id, file_path, target_domains, target_keywords, task_data, combo_format)
        
    except Exception as e:
        print(f"Error processing task for user {user_id}: {e}")
        try:
            await app.send_message(user_id, f"❌ **Processing error:** {str(e)}")
        except:
            pass
    finally:
        # Cleanup
        if user_id in processing_users:
            if 'file_path' in processing_users[user_id]:
                await cleanup_files(processing_users[user_id]['file_path'])
            del processing_users[user_id]

async def download_file_with_progress(user_id, file_id, message_id):
    """Download file with progress updates"""
    try:
        file_path = f"temp_{user_id}_{int(time.time())}.txt"
        
        # Simple download without progress for now (to avoid blocking)
        file = await app.download_media(
            message=file_id,
            file_name=file_path
        )
        
        await app.edit_message_text(
            user_id,
            message_id,
            "✅ **File downloaded!**\n\n🔍 **Starting to process...**"
        )
        
        return file
    except Exception as e:
        print(f"Download error for user {user_id}: {e}")
        return None

async def process_single_format(user_id, file_path, target_domains, target_keywords, task_data, combo_format):
    """Process a single combo format"""
    result = await process_log_file(user_id, file_path, target_domains, target_keywords, combo_format)
    
    if result is None:  # Cancelled
        await app.send_message(user_id, "🛑 **Processing cancelled.**")
        return
    
    if not result or all(not combos for combos in result.values()):
        await app.send_message(user_id, "❌ **No valid combos found.**")
        return
    
    # Send results
    processing_time = time.time() - task_data['start_time']
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if combo_format == "ulp":
        format_name = "ULP (Full Line)"
    else:
        format_name = combo_format.replace('_', ':').title()
    
    processing_mode = task_data.get('processing_mode', 'mixed_mode')
    
    if processing_mode == "mixed_mode" and 'mixed' in result:
        # Mixed mode
        output_filename = f"{combo_format}_mixed_{timestamp}.txt"
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(result['mixed']))
        
        await app.send_document(
            chat_id=user_id,
            document=output_filename,
            caption=(
                f"✅ **{format_name} - Mixed Results**\n\n"
                f"🔹 **Combos found:** {len(result['mixed'])}\n"
                f"🔹 **Processing time:** {processing_time:.2f}s\n\n"
                f"👑 {OWNER_USERNAME}"
            )
        )
        await cleanup_files(output_filename)
    else:
        # Targeted mode (domain or keyword)
        total_combos = 0
        sent_files = 0
        
        for target, combos in result.items():
            if not combos:
                continue
            
            total_combos += len(combos)
            target_clean = target.replace('.', '_').replace('/', '_').replace(' ', '_')
            output_filename = f"{combo_format}_{target_clean}_{timestamp}.txt"
            
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(combos))
            
            if processing_mode == "domain_mode":
                target_type = "Domain"
            else:
                target_type = "Keyword"
            
            await app.send_document(
                chat_id=user_id,
                document=output_filename,
                caption=(
                    f"✅ **{format_name} - {target_type}: {target}**\n"
                    f"🔹 **Combos found:** {len(combos)}\n\n"
                    f"👑 {OWNER_USERNAME}"
                )
            )
            sent_files += 1
            await cleanup_files(output_filename)
            await asyncio.sleep(0.5)  # Small delay between files
        
        if sent_files > 1:
            await app.send_message(
                user_id,
                f"📦 **Processing Complete!**\n\n"
                f"🔹 **Files sent:** {sent_files}\n"
                f"🔹 **Total combos:** {total_combos}\n"
                f"🔹 **Time:** {processing_time:.2f}s\n\n"
                f"👑 {OWNER_USERNAME}"
            )

async def process_all_formats(user_id, file_path, target_domains, target_keywords, task_data):
    """Process all combo formats"""
    formats = ["email_pass", "user_pass", "number_pass", "ulp"]
    format_names = {
        "email_pass": "Email:Pass", 
        "user_pass": "User:Pass", 
        "number_pass": "Number:Pass",
        "ulp": "ULP (Full Line)"
    }
    
    results = {}
    total_combos = 0
    
    for fmt in formats:
        if user_id in processing_users and processing_users[user_id].get('cancelled', False):
            await app.send_message(user_id, "🛑 **Processing cancelled.**")
            return
        
        # Update progress
        await app.edit_message_text(
            user_id,
            processing_users[user_id]['progress_msg'],
            f"🔄 **Processing {format_names[fmt]}...**\n\nPlease wait..."
        )
        
        result = await process_log_file(user_id, file_path, target_domains, target_keywords, fmt)
        if result:
            results[fmt] = result
    
    # Send results
    processing_time = time.time() - task_data['start_time']
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    processing_mode = task_data.get('processing_mode', 'mixed_mode')
    
    for fmt, result in results.items():
        if not result:
            continue
            
        if processing_mode == "mixed_mode" and 'mixed' in result:
            combos = result['mixed']
            if combos:
                total_combos += len(combos)
                output_filename = f"{fmt}_{timestamp}.txt"
                
                with open(output_filename, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(combos))
                
                await app.send_document(
                    chat_id=user_id,
                    document=output_filename,
                    caption=f"✅ {format_names[fmt]} - {len(combos)} combos"
                )
                await cleanup_files(output_filename)
        else:
            for target, combos in result.items():
                if combos:
                    total_combos += len(combos)
                    target_clean = target.replace('.', '_').replace('/', '_').replace(' ', '_')
                    output_filename = f"{fmt}_{target_clean}_{timestamp}.txt"
                    
                    with open(output_filename, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(combos))
                    
                    await app.send_document(
                        chat_id=user_id,
                        document=output_filename,
                        caption=f"✅ {format_names[fmt]} - {target} - {len(combos)} combos"
                    )
                    await cleanup_files(output_filename)
                    await asyncio.sleep(0.5)
    
    await app.send_message(
        user_id,
        f"🎉 **All Formats Processing Complete!**\n\n"
        f"🔹 **Total combos found:** {total_combos}\n"
        f"🔹 **Processing time:** {processing_time:.2f}s\n\n"
        f"👑 {OWNER_USERNAME}"
    )

# ===========================
# ADMIN COMMANDS
# ===========================

def is_admin(user_id):
    return user_id == OWNER_ID

@app.on_message(filters.command("addpremium") & filters.private)
async def add_premium_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.reply_text("❌ **Usage:** `/addpremium <user_id> <days>`")
            return
        
        target_user_id = int(args[1])
        days = int(args[2])
        
        user = get_user(target_user_id)
        if not user:
            await message.reply_text("❌ **User not found.**")
            return
        
        expiry_date = datetime.now() + timedelta(days=days)
        
        update_user(target_user_id, {
            "user_type": "premium",
            "premium_expiry": expiry_date
        })
        
        # Notify user
        try:
            await app.send_message(
                target_user_id,
                f"🎉 **Premium Activated!**\n\n"
                f"Your premium subscription has been activated for {days} days.\n"
                f"Expiry: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Thank you for choosing our service! 👑"
            )
        except:
            pass
        
        await message.reply_text(
            f"✅ **Premium added successfully!**\n\n"
            f"👤 User: {target_user_id}\n"
            f"📅 Days: {days}\n"
            f"⏰ Expiry: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("rmvpremium") & filters.private)
async def remove_premium_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/rmvpremium <user_id>`")
            return
        
        target_user_id = int(args[1])
        
        user = get_user(target_user_id)
        if not user:
            await message.reply_text("❌ **User not found.**")
            return
        
        update_user(target_user_id, {
            "user_type": "free",
            "premium_expiry": None
        })
        
        # Notify user
        try:
            await app.send_message(
                target_user_id,
                "ℹ️ **Premium Subscription Ended**\n\n"
                "Your premium subscription has been removed.\n"
                "You can still use the free features."
            )
        except:
            pass
        
        await message.reply_text(f"✅ **Premium removed for user:** {target_user_id}")
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("ban") & filters.private)
async def ban_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/ban <user_id>`")
            return
        
        target_user_id = int(args[1])
        
        user = get_user(target_user_id)
        if not user:
            await message.reply_text("❌ **User not found.**")
            return
        
        update_user(target_user_id, {"is_banned": True})
        
        await message.reply_text(f"✅ **User banned:** {target_user_id}")
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("unban") & filters.private)
async def unban_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/unban <user_id>`")
            return
        
        target_user_id = int(args[1])
        
        user = get_user(target_user_id)
        if not user:
            await message.reply_text("❌ **User not found.**")
            return
        
        update_user(target_user_id, {"is_banned": False})
        
        await message.reply_text(f"✅ **User unbanned:** {target_user_id}")
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("id") & filters.private)
async def id_command(client: Client, message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    user_info = (
        f"👤 **Your Information**\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"📛 **Name:** {user.get('first_name', '')} {user.get('last_name', '')}\n"
        f"🔗 **Username:** @{user.get('username', 'N/A')}\n"
        f"💳 **Account Type:** {'Premium' if is_premium(user_id) else 'Free'}\n"
    )
    
    if is_premium(user_id):
        expiry = user.get('premium_expiry')
        if expiry:
            user_info += f"⏰ **Premium Expiry:** {expiry.strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    user_info += f"📅 **Registered:** {user.get('registered_at').strftime('%Y-%m-%d')}\n"
    user_info += f"📊 **Files Processed:** {user.get('total_files_processed', 0)}\n"
    user_info += f"🔢 **Daily Checks Used:** {user.get('daily_checks_used', 0)}/{get_setting('premium_daily_checks' if is_premium(user_id) else 'free_daily_checks')}"
    
    await message.reply_text(user_info)

@app.on_message(filters.command("userinfo") & filters.private)
async def userinfo_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("❌ **Usage:** `/userinfo <user_id>`")
            return
        
        target_user_id = int(args[1])
        user = get_user(target_user_id)
        
        if not user:
            await message.reply_text("❌ **User not found.**")
            return
        
        user_info = (
            f"👤 **User Information**\n\n"
            f"🆔 **User ID:** `{target_user_id}`\n"
            f"📛 **Name:** {user.get('first_name', '')} {user.get('last_name', '')}\n"
            f"🔗 **Username:** @{user.get('username', 'N/A')}\n"
            f"💳 **Account Type:** {user.get('user_type', 'free').title()}\n"
            f"🚫 **Banned:** {'Yes' if user.get('is_banned') else 'No'}\n"
        )
        
        if user.get('premium_expiry'):
            user_info += f"⏰ **Premium Expiry:** {user['premium_expiry'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        user_info += f"📅 **Registered:** {user.get('registered_at').strftime('%Y-%m-%d %H:%M:%S')}\n"
        user_info += f"📊 **Files Processed:** {user.get('total_files_processed', 0)}\n"
        user_info += f"🔢 **Daily Checks Today:** {user.get('daily_checks_used', 0)}\n"
        user_info += f"🕒 **Last Activity:** {user.get('last_activity', 'Never')}"
        
        await message.reply_text(user_info)
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        # User statistics
        total_users = users_collection.count_documents({})
        free_users = users_collection.count_documents({"user_type": "free"})
        premium_users = users_collection.count_documents({"user_type": "premium"})
        banned_users = users_collection.count_documents({"is_banned": True})
        
        # Today's activity
        today = datetime.now().date().isoformat()
        today_users = users_collection.count_documents({"last_check_date": today})
        
        # Queue info
        queue_size = get_queue_size()
        
        # Server stats
        cpu_usage = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        stats_text = (
            "📊 **Bot Statistics**\n\n"
            f"👥 **Total Users:** {total_users}\n"
            f"🆓 **Free Users:** {free_users}\n"
            f"💎 **Premium Users:** {premium_users}\n"
            f"🚫 **Banned Users:** {banned_users}\n"
            f"📅 **Active Today:** {today_users}\n"
            f"📋 **Queue Size:** {queue_size}\n\n"
            "🖥️ **Server Stats**\n"
            f"⚡ **CPU Usage:** {cpu_usage}%\n"
            f"💾 **Memory:** {memory.percent}%\n"
            f"💿 **Disk:** {disk.percent}%"
        )
        
        await message.reply_text(stats_text)
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("restart") & filters.private)
async def restart_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    await message.reply_text("🔄 **Restarting bot...**")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        if not message.reply_to_message:
            await message.reply_text("❌ **Please reply to a message to broadcast.**")
            return
        
        broadcast_msg = message.reply_to_message
        users = users_collection.find({})
        total = users_collection.count_documents({})
        success = 0
        failed = 0
        
        status_msg = await message.reply_text(f"📢 **Broadcasting...**\n\nProgress: 0/{total}")
        
        for user in users:
            try:
                await broadcast_msg.copy(user["user_id"])
                success += 1
            except:
                failed += 1
            
            if (success + failed) % 10 == 0:
                await status_msg.edit_text(
                    f"📢 **Broadcasting...**\n\n"
                    f"✅ Success: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"📊 Progress: {success + failed}/{total}"
                )
        
        await status_msg.edit_text(
            f"✅ **Broadcast Complete!**\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Total: {total}"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("pin") & filters.private)
async def pin_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        if not message.reply_to_message:
            await message.reply_text("❌ **Please reply to a message to pin.**")
            return
        
        pin_msg = message.reply_to_message
        users = users_collection.find({})
        total = users_collection.count_documents({})
        success = 0
        failed = 0
        
        status_msg = await message.reply_text(f"📌 **Pinning message...**\n\nProgress: 0/{total}")
        
        for user in users:
            try:
                sent_msg = await pin_msg.copy(user["user_id"])
                await sent_msg.pin()
                success += 1
            except:
                failed += 1
            
            if (success + failed) % 10 == 0:
                await status_msg.edit_text(
                    f"📌 **Pinning message...**\n\n"
                    f"✅ Success: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"📊 Progress: {success + failed}/{total}"
                )
        
        await status_msg.edit_text(
            f"✅ **Pin Complete!**\n\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Total: {total}"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

@app.on_message(filters.command("serverstats") & filters.private)
async def server_stats_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        # System information
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Bot information
        total_users = users_collection.count_documents({})
        queue_size = get_queue_size()
        active_processes = len(processing_users)
        
        stats_text = (
            "🖥️ **Server Statistics**\n\n"
            "⚙️ **System Info**\n"
            f"⚡ **CPU Usage:** {cpu_usage}%\n"
            f"💾 **Memory:** {memory.used//(1024**3)}GB/{memory.total//(1024**3)}GB ({memory.percent}%)\n"
            f"💿 **Disk:** {disk.used//(1024**3)}GB/{disk.total//(1024**3)}GB ({disk.percent}%)\n\n"
            "🤖 **Bot Info**\n"
            f"👥 **Total Users:** {total_users}\n"
            f"📋 **Queue Size:** {queue_size}\n"
            f"⚡ **Active Processes:** {active_processes}\n"
            f"🔄 **Queue Processor:** {'Running' if queue_processor_running else 'Stopped'}"
        )
        
        await message.reply_text(stats_text)
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

# ===========================
# USER PLAN COMMANDS
# ===========================

@app.on_message(filters.command("myplan") & filters.private)
async def myplan_command(client: Client, message: Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if is_premium(user_id):
        plan_text = (
            "💎 **Premium Plan Active**\n\n"
            f"⏰ **Expiry:** {user.get('premium_expiry').strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📦 **File Size Limit:** {get_setting('premium_file_size')} MB\n"
            f"🔄 **Time Break:** {get_setting('premium_time_break')} minutes\n"
            f"⚡ **Active Processes:** {get_setting('premium_active_process')}\n"
            f"📊 **Daily Checks:** {user.get('daily_checks_used', 0)}/{get_setting('premium_daily_checks')}\n"
            f"🌐 **Multi-Domain:** {'Yes' if get_setting('premium_multi_domain') else 'No'}\n"
            f"🔧 **All Combo Types:** Available"
        )
    else:
        plan_text = (
            "🆓 **Free Plan**\n\n"
            f"📦 **File Size Limit:** {get_setting('free_file_size')} MB\n"
            f"🔄 **Time Break:** {get_setting('free_time_break')} minutes\n"
            f"⚡ **Active Processes:** {get_setting('free_active_process')}\n"
            f"📊 **Daily Checks:** {user.get('daily_checks_used', 0)}/{get_setting('free_daily_checks')}\n"
            f"🌐 **Multi-Domain:** {'Yes' if get_setting('free_multi_domain') else 'No'}\n"
            f"🔧 **All Combo Types:** Available\n\n"
            "💡 **Upgrade to premium for better limits!**\n"
            "Use `/plans` to view available plans."
        )
    
    await message.reply_text(plan_text)

@app.on_message(filters.command("plans") & filters.private)
async def plans_command(client: Client, message: Message):
    plans = get_setting("plans")
    
    plans_text = "💎 **Premium Plans**\n\n"
    
    for days, plan in plans.items():
        plans_text += f"📅 **{days} Day** - ${plan['price']}\n"
    
    plans_text += "\n🔧 **Premium Features:**\n"
    plans_text += f"• 📦 File Size: {get_setting('premium_file_size')} MB\n"
    plans_text += f"• 🔄 Time Break: {get_setting('premium_time_break')} minutes\n"
    plans_text += f"• 📊 Daily Checks: {get_setting('premium_daily_checks')}\n"
    plans_text += "• 🌐 Multi-Domain Support\n"
    plans_text += "• 🔧 All Combo Types\n\n"
    plans_text += "💳 **To purchase:**\n"
    plans_text += "1. Choose a plan\n"
    plans_text += "2. Send payment to any method below\n"
    plans_text += "3. Forward payment proof to admin\n"
    plans_text += "4. Get activated within minutes!\n\n"
    plans_text += "👑 **Contact:** @still_alivenow"
    
    # Create payment methods keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 View Payment Methods", callback_data="payment_methods")],
        [InlineKeyboardButton("👑 Contact Admin", url=f"https://t.me/{OWNER_USERNAME[1:]}")]
    ])
    
    await message.reply_text(plans_text, reply_markup=keyboard, disable_web_page_preview=True)

@app.on_callback_query(filters.regex("^payment_methods$"))
async def payment_methods_handler(client: Client, callback_query: CallbackQuery):
    payment_text = "💳 **Payment Methods**\n\n"
    
    for method, address in PAYMENT_METHODS.items():
        payment_text += f"**{method.replace('_', ' ').title()}:**\n`{address}`\n\n"
    
    payment_text += "📝 **Instructions:**\n"
    payment_text += "1. Send payment to any address above\n"
    payment_text += "2. Take screenshot/note transaction ID\n"
    payment_text += "3. Forward to admin for activation\n\n"
    payment_text += "👑 **Admin:** @still_alivenow"
    
    # Create copy buttons for each payment method
    buttons = []
    for method, address in PAYMENT_METHODS.items():
        buttons.append([InlineKeyboardButton(f"📋 Copy {method.replace('_', ' ').title()}", callback_data=f"copy_{method}")])
    
    buttons.append([InlineKeyboardButton("👑 Contact Admin", url=f"https://t.me/{OWNER_USERNAME[1:]}")])
    buttons.append([InlineKeyboardButton("🔙 Back to Plans", callback_data="back_to_plans")])
    
    keyboard = InlineKeyboardMarkup(buttons)
    
    await callback_query.message.edit_text(payment_text, reply_markup=keyboard)
    await callback_query.answer()

@app.on_callback_query(filters.regex("^copy_"))
async def copy_payment_handler(client: Client, callback_query: CallbackQuery):
    method = callback_query.data.replace("copy_", "")
    address = PAYMENT_METHODS.get(method)
    
    if address:
        # We can't directly copy to clipboard in Telegram, but we can show it
        await callback_query.answer(f"📋 {method.replace('_', ' ').title()} address ready to copy", show_alert=True)
        
        # Edit message to show address prominently
        address_text = f"**{method.replace('_', ' ').title()} Address:**\n\n`{address}`\n\n📋 **Select and copy the above address**"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Payment Methods", callback_data="payment_methods")],
            [InlineKeyboardButton("👑 Contact Admin", url=f"https://t.me/{OWNER_USERNAME[1:]}")]
        ])
        
        await callback_query.message.edit_text(address_text, reply_markup=keyboard)
    else:
        await callback_query.answer("❌ Payment method not found", show_alert=True)

@app.on_callback_query(filters.regex("^back_to_plans$"))
async def back_to_plans_handler(client: Client, callback_query: CallbackQuery):
    await plans_command(client, callback_query.message)
    await callback_query.answer()

# ===========================
# SETTINGS MANAGEMENT
# ===========================

@app.on_message(filters.command("settings") & filters.private)
async def settings_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    settings_text = "⚙️ **Bot Settings**\n\n"
    
    # Free user settings
    settings_text += "🆓 **Free User Settings:**\n"
    settings_text += f"• File Size: {get_setting('free_file_size')} MB\n"
    settings_text += f"• Time Break: {get_setting('free_time_break')} minutes\n"
    settings_text += f"• Active Process: {get_setting('free_active_process')}\n"
    settings_text += f"• Daily Checks: {get_setting('free_daily_checks')}\n"
    settings_text += f"• Multi-Domain: {get_setting('free_multi_domain')}\n\n"
    
    # Premium user settings  
    settings_text += "💎 **Premium User Settings:**\n"
    settings_text += f"• File Size: {get_setting('premium_file_size')} MB\n"
    settings_text += f"• Time Break: {get_setting('premium_time_break')} minutes\n"
    settings_text += f"• Active Process: {get_setting('premium_active_process')}\n"
    settings_text += f"• Daily Checks: {get_setting('premium_daily_checks')}\n"
    settings_text += f"• Multi-Domain: {get_setting('premium_multi_domain')}\n\n"
    
    settings_text += "🔧 **Use /set <key> <value> to change settings**"
    
    await message.reply_text(settings_text)

@app.on_message(filters.command("set") & filters.private)
async def set_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.reply_text("❌ **Admin only command.**")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.reply_text(
                "❌ **Usage:** `/set <key> <value>`\n\n"
                "**Available keys:**\n"
                "• free_file_size, free_time_break, free_active_process, free_daily_checks, free_multi_domain\n"
                "• premium_file_size, premium_time_break, premium_active_process, premium_daily_checks, premium_multi_domain"
            )
            return
        
        key = args[1]
        value = args[2]
        
        # Convert value to appropriate type
        if value.lower() in ['true', 'yes', '1']:
            value = True
        elif value.lower() in ['false', 'no', '0']:
            value = False
        elif value.isdigit():
            value = int(value)
        elif value.replace('.', '').isdigit():
            value = float(value)
        
        if key not in DEFAULT_SETTINGS:
            await message.reply_text("❌ **Invalid setting key.**")
            return
        
        update_setting(key, value)
        await message.reply_text(f"✅ **Setting updated:** `{key} = {value}`")
        
    except Exception as e:
        await message.reply_text(f"❌ **Error:** {str(e)}")

# ===========================
# PREMIUM EXPIRY CHECKER
# ===========================

async def check_premium_expiry():
    """Check and notify users about premium expiry"""
    while True:
        try:
            now = datetime.now()
            expiring_soon = users_collection.find({
                "user_type": "premium",
                "premium_expiry": {
                    "$gte": now,
                    "$lte": now + timedelta(hours=24)
                }
            })
            
            for user in expiring_soon:
                try:
                    expiry_time = user["premium_expiry"]
                    hours_left = (expiry_time - now).total_seconds() / 3600
                    
                    if hours_left <= 24:
                        await app.send_message(
                            user["user_id"],
                            f"⚠️ **Premium Expiring Soon**\n\n"
                            f"Your premium subscription expires in {hours_left:.1f} hours.\n"
                            f"Renew now to continue enjoying premium features!\n\n"
                            f"Use `/plans` to view available plans."
                        )
                except:
                    pass
            
            # Check expired premiums
            expired = users_collection.find({
                "user_type": "premium", 
                "premium_expiry": {"$lt": now}
            })
            
            for user in expired:
                update_user(user["user_id"], {
                    "user_type": "free",
                    "premium_expiry": None
                })
                
                try:
                    await app.send_message(
                        user["user_id"],
                        "ℹ️ **Premium Subscription Ended**\n\n"
                        "Your premium subscription has expired.\n"
                        "You've been downgraded to free plan.\n\n"
                        "Use `/plans` to upgrade again!"
                    )
                except:
                    pass
            
            await asyncio.sleep(3600)  # Check every hour
            
        except Exception as e:
            print(f"Premium expiry checker error: {e}")
            await asyncio.sleep(300)

# ===========================
# BOT STARTUP
# ===========================

@app.on_message(filters.command("ping") & filters.private)
async def ping_command(client: Client, message: Message):
    start_time = time.time()
    msg = await message.reply_text("🏓 **Pong!**")
    end_time = time.time()
    await msg.edit_text(f"🏓 **Pong!**\n⏱️ Response time: {(end_time - start_time) * 1000:.2f} ms")

# Error handler
@app.on_error()
async def error_handler(client: Client, error: Exception):
    print(f"Bot error: {error}")

# Start the bot
async def main():
    await app.start()
    print("🤖 Advanced Combo Bot Started...")
    print("✅ Bot is responsive and ready!")
    
    # Start background tasks
    asyncio.create_task(start_queue_processor())
    asyncio.create_task(check_premium_expiry())
    
    # Keep the bot running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
    finally:
        print("Bot stopped")
