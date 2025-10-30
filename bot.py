import os
import re
import time
import asyncio
import pymongo
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import RPCError, FloodWait, BadRequest
from collections import deque
import psutil
import requests

# Bot configuration
API_ID = 23933044
API_HASH = "6df11147cbec7d62a323f0f498c8c03a"
BOT_TOKEN = "7989255010:AAGI73-gpORxqqnsNrRRCLWNCyyACA0ia-w"
OWNER_ID = 7125341830
OWNER_USERNAME = "@still_alivenow"
LOG_CHANNEL = -1003277595247
DB_URL = "mongodb+srv://animepahe:animepahe@animepahe.o8zgy.mongodb.net/?retryWrites=true&w=majority&appName=animepahe"

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=200, max_concurrent_transmissions=1000, sleep_threshold=15)

# Database setup
client = pymongo.MongoClient(DB_URL)
db = client["combo_bot"]
users_collection = db["users"]
settings_collection = db["settings"]
payments_collection = db["payments"]

# Default settings
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
    "premium_combo_types": ["email_pass", "user_pass", "number_pass", "ulp"]
}

# Plans configuration
PLANS = {
    "1": {"days": 1, "price": 2},
    "3": {"days": 3, "price": 5},
    "5": {"days": 5, "price": 9},
    "7": {"days": 7, "price": 12},
    "15": {"days": 15, "price": 20},
    "30": {"days": 30, "price": 25}
}

# Payment methods
PAYMENT_METHODS = {
    "binance_pay": "Binance Pay: 907900897",
    "btc": "BTC (Bitcoin): 1JbetrmgdjNGp2jq9jvg33tWkgEuiwVpGt",
    "usdt": "USDT (BEP-20): 0x5896aea48d1205057ec415a248e75fa0f3e4c4e9",
    "tron": "TRON (TRC-20): TLUbSv8KrAxpSccMbBNsjm4o6FmHtXt1pa",
    "bnb": "BNB (BEP-20): 0x5896aea48d1205057ec415a248e75fa0f3e4c4e9",
    "litecoin": "Litecoin: LXhcDTUVyRkf7oYjBHHvyZ9ZVA3UYGDbME"
}

# Global variables
processing_users = {}
processing_queue = deque()
queue_processor_running = False

# Initialize database
async def initialize_database():
    if settings_collection.count_documents({}) == 0:
        settings_collection.insert_one(DEFAULT_SETTINGS)

# Helper functions
async def get_settings():
    return settings_collection.find_one({})

async def update_settings(new_settings):
    settings_collection.update_one({}, {"$set": new_settings})

async def get_user(user_id):
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        # Create new user
        user_data = {
            "user_id": user_id,
            "registered_at": datetime.now(),
            "user_type": "free",
            "daily_checks_used": 0,
            "last_check_time": None,
            "premium_expiry": None,
            "banned": False,
            "total_files_processed": 0
        }
        users_collection.insert_one(user_data)
        return user_data
    return user

async def update_user(user_id, update_data):
    users_collection.update_one({"user_id": user_id}, {"$set": update_data})

async def is_admin(user_id):
    return user_id == OWNER_ID

async def is_banned(user_id):
    user = await get_user(user_id)
    return user.get("banned", False)

async def is_registered(user_id):
    user = await get_user(user_id)
    return user is not None

async def can_process_file(user_id):
    user = await get_user(user_id)
    settings = await get_settings()
    
    if user.get("banned", False):
        return False, "You are banned from using this bot."
    
    # Check daily limit
    if user["daily_checks_used"] >= (settings["premium_daily_checks"] if user["user_type"] == "premium" else settings["free_daily_checks"]):
        return False, "Daily file check limit reached. Try again tomorrow."
    
    # Check time break
    last_check = user.get("last_check_time")
    if last_check:
        time_break = settings["premium_time_break"] if user["user_type"] == "premium" else settings["free_time_break"]
        time_since_last = (datetime.now() - last_check).total_seconds() / 60
        if time_since_last < time_break:
            wait_time = time_break - time_since_last
            return False, f"Please wait {wait_time:.1f} minutes before processing another file."
    
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
    for user_id, task_data in list(processing_queue):
        queue_info.append({
            "user_id": user_id,
            "file_name": task_data.get('file_name', 'Unknown'),
            "added_time": task_data.get('added_time', datetime.now())
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
    m = re.search(r':([a-zA-Z0-9._-]{3,50}):([^\s:\r\n]{1,100})$', line)
    if m:
        username, password = m.group(1), m.group(2)
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
    
    if target_domains:
        for domain in target_domains:
            valid_combos[domain] = set()
    elif target_keywords:
        for keyword in target_keywords:
            valid_combos[keyword] = set()
    else:
        valid_combos['mixed'] = set()
    
    try:
        print(f"Counting lines for user {user_id}...")
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in f:
                total_lines += 1
        
        if total_lines == 0:
            return {}
        
        print(f"Total lines: {total_lines}. Starting processing...")
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                processed_lines += 1
                line = line.strip()
                if not line:
                    continue
                
                if user_id in processing_users and processing_users[user_id].get('cancelled', False):
                    return None
                
                current_progress = (processed_lines / total_lines) * 100
                
                if current_progress - last_update >= 5 or processed_lines == total_lines:
                    last_update = current_progress
                    progress_bar_length = 20
                    filled_length = int(progress_bar_length * processed_lines // total_lines)
                    progress_bar = '◉' * filled_length + '◯' * (progress_bar_length - filled_length)
                    
                    total_found = sum(len(combos) for combos in valid_combos.values())
                    
                    progress_text = (
                        f"🔍 **Processing... {current_progress:.1f}%**\n"
                        f"`[{progress_bar}]`\n"
                        f"📊 **Lines:** {processed_lines}/{total_lines}\n"
                        f"✅ **Found:** {total_found} combos\n"
                    )
                    
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
                    if queue_pos == 0:
                        progress_text += f"\n\n⚡ **Currently Processing**"
                    else:
                        progress_text += f"\n\n📋 **Queue Position:** {queue_pos}"
                    
                    progress_text += f"\n⏳ **Click /cancel to stop**"
                    
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
                            pass
                
                line_lower = line.lower()
                
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

# Command handlers
@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not await is_registered(user_id):
        await message.reply_text(
            "👋 **Welcome to Advanced Combo Generator Bot!**\n\n"
            "📝 **You need to register first to use this bot.**\n"
            "Use /register to create your account.\n\n"
            f"👑 **Owner:** {OWNER_USERNAME}"
        )
        return
    
    if await is_banned(user_id):
        await message.reply_text("🚫 **You are banned from using this bot.**")
        return
    
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

@app.on_message(filters.command("register") & filters.private)
async def register_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if await is_registered(user_id):
        await message.reply_text("✅ **You are already registered!**")
        return
    
    user_data = {
        "user_id": user_id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "registered_at": datetime.now(),
        "user_type": "free",
        "daily_checks_used": 0,
        "last_check_time": None,
        "premium_expiry": None,
        "banned": False,
        "total_files_processed": 0
    }
    
    users_collection.insert_one(user_data)
    
    # Send to log channel
    log_text = (
        "🆕 **New User Registered**\n\n"
        f"👤 **User:** {message.from_user.mention}\n"
        f"🆔 **ID:** `{user_id}`\n"
        f"📅 **Registered:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    try:
        await app.send_message(LOG_CHANNEL, log_text)
    except:
        pass
    
    await message.reply_text(
        "🎉 **Registration Successful!**\n\n"
        "You can now use the bot features.\n"
        "Use /start to see available commands.\n\n"
        "💡 **Free Plan Limits:**\n"
        "• File Size: 500MB\n"
        "• 5 files per day\n"
        "• 10 min cooldown\n\n"
        "Use /plans to upgrade to premium!"
    )

@app.on_message(filters.command("help") & filters.private)
async def help_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
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
        "🔹 **Commands:**\n"
        "/start - Start the bot\n"
        "/combo - Process a file\n"
        "/cancel - Cancel processing\n"
        "/queue - Check queue\n"
        "/myplan - Check your plan\n"
        "/plans - Premium plans\n"
        "/id - Get your user info\n\n"
        f"💡 **Contact:** {OWNER_USERNAME}"
    )
    
    await message.reply_text(help_text, disable_web_page_preview=True)

@app.on_message(filters.command("queue") & filters.private)
async def queue_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
    queue_size = get_queue_size()
    user_position = get_queue_position(user_id)
    
    if user_position > 0:
        queue_info = get_queue_info()
        queue_text = f"📋 **Queue Information**\n\n"
        queue_text += f"• **Your Position:** {user_position}\n"
        queue_text += f"• **Total in Queue:** {queue_size}\n"
        queue_text += f"• **Estimated Wait:** ~{user_position * 2} minutes\n\n"
        
        if queue_info:
            queue_text += "👥 **Current Queue:**\n"
            for i, item in enumerate(queue_info[:5], 1):
                queue_text += f"{i}. User {item['user_id']} - {item['file_name']}\n"
            if len(queue_info) > 5:
                queue_text += f"... and {len(queue_info) - 5} more\n"
    elif user_id in processing_users:
        queue_text = "⚡ **Your file is currently being processed!**"
    else:
        queue_text = "ℹ️ **You are not in the queue.**\nUse `/combo` to start processing."
    
    await message.reply_text(queue_text)

@app.on_message(filters.command("combo") & filters.private)
async def combo_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
    if await is_banned(user_id):
        await message.reply_text("🚫 **You are banned from using this bot.**")
        return
    
    # Check if user can process file
    can_process, reason = await can_process_file(user_id)
    if not can_process:
        await message.reply_text(f"❌ **{reason}**")
        return
    
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
        
        user = await get_user(user_id)
        settings = await get_settings()
        
        max_file_size = settings["premium_file_size"] if user["user_type"] == "premium" else settings["free_file_size"]
        file_size = message.reply_to_message.document.file_size
        
        if file_size > max_file_size * 1024 * 1024:
            await message.reply_text(f"⚠️ File too large. Max size: {max_file_size}MB")
            return
        
        # Forward file to log channel
        log_caption = (
            f"📁 **New File Received**\n\n"
            f"👤 **User:** {message.from_user.mention}\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"📄 **File:** {file_name}\n"
            f"📊 **Size:** {file_size / (1024*1024):.2f}MB\n"
            f"🕒 **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"👑 **Plan:** {user['user_type'].title()}"
        )
        
        try:
            await message.reply_to_message.forward(LOG_CHANNEL)
            await app.send_message(LOG_CHANNEL, log_caption)
        except:
            pass
        
        # Store user data
        processing_users[user_id] = {
            'file_id': message.reply_to_message.document.file_id,
            'file_name': file_name,
            'file_size': file_size,
            'cancelled': False,
            'start_time': time.time(),
            'status': 'waiting_for_mode',
            'added_time': datetime.now()
        }
        
        # Ask for processing mode
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

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in processing_users:
        processing_users[user_id]['cancelled'] = True
        remove_from_queue(user_id)
        
        # Cleanup files
        if 'file_path' in processing_users[user_id]:
            await cleanup_files(processing_users[user_id]['file_path'])
        
        await message.reply_text("🛑 **Processing cancelled.**")
        
        # Update user stats
        user = await get_user(user_id)
        await update_user(user_id, {
            "last_check_time": datetime.now(),
            "daily_checks_used": user["daily_checks_used"] + 1
        })
        
        await asyncio.sleep(2)
        if user_id in processing_users:
            del processing_users[user_id]
    else:
        await message.reply_text("ℹ️ **No active processing to cancel.**")

@app.on_message(filters.command("myplan") & filters.private)
async def myplan_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
    user = await get_user(user_id)
    settings = await get_settings()
    
    plan_text = f"📊 **Your Current Plan: {user['user_type'].upper()}**\n\n"
    
    if user["user_type"] == "premium":
        expiry = user.get("premium_expiry")
        if expiry:
            days_left = (expiry - datetime.now()).days
            plan_text += f"⭐ **Premium Expiry:** {expiry.strftime('%Y-%m-%d')}\n"
            plan_text += f"📅 **Days Left:** {days_left}\n\n"
    
    # Show limits
    if user["user_type"] == "premium":
        limits = settings["premium"]
    else:
        limits = settings["free"]
    
    plan_text += f"📁 **File Size:** {limits['file_size']}MB\n"
    plan_text += f"⏰ **Cooldown:** {limits['time_break']} minutes\n"
    plan_text += f"📊 **Daily Files:** {user['daily_checks_used']}/{limits['daily_checks']}\n"
    plan_text += f"🔢 **Multi-domain:** {'Yes' if limits['multi_domain'] else 'No'}\n"
    plan_text += f"🔄 **Combo Types:** All\n\n"
    
    if user["user_type"] == "free":
        plan_text += "💎 **Upgrade to premium for better limits!**\nUse /plans to view available plans."
    
    await message.reply_text(plan_text)

@app.on_message(filters.command("plans") & filters.private)
async def plans_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
    plans_text = "💎 **Premium Plans Available**\n\n"
    
    for days, info in PLANS.items():
        plans_text += f"**{days} Day{'s' if int(days) > 1 else ''}** - ${info['price']}\n"
    
    plans_text += "\n💰 **Payment Methods:**\n"
    for method, address in PAYMENT_METHODS.items():
        plans_text += f"• {method.replace('_', ' ').title()}\n"
    
    plans_text += "\n📝 **How to purchase:**\n"
    plans_text += "1. Choose your plan\n"
    plans_text += "2. Send payment to any address\n"
    plans_text += "3. Forward payment proof to admin\n"
    plans_text += "4. We'll activate your premium\n\n"
    plans_text += f"👑 **Contact:** {OWNER_USERNAME}"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Day - $2", callback_data="plan_1")],
        [InlineKeyboardButton("3 Days - $5", callback_data="plan_3")],
        [InlineKeyboardButton("5 Days - $9", callback_data="plan_5")],
        [InlineKeyboardButton("7 Days - $12", callback_data="plan_7")],
        [InlineKeyboardButton("15 Days - $20", callback_data="plan_15")],
        [InlineKeyboardButton("30 Days - $25", callback_data="plan_30")],
    ])
    
    await message.reply_text(plans_text, reply_markup=keyboard, disable_web_page_preview=True)

@app.on_message(filters.command("id") & filters.private)
async def id_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_registered(user_id):
        await message.reply_text("❌ **Please register first using /register**")
        return
    
    user = await get_user(user_id)
    
    id_text = (
        f"👤 **User Information**\n\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"👤 **Username:** @{message.from_user.username or 'N/A'}\n"
        f"📛 **Name:** {message.from_user.first_name or ''} {message.from_user.last_name or ''}\n"
        f"📅 **Registered:** {user['registered_at'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"💎 **Plan:** {user['user_type'].title()}\n"
        f"📊 **Files Processed:** {user['total_files_processed']}\n"
        f"📅 **Daily Used:** {user['daily_checks_used']}"
    )
    
    if user["user_type"] == "premium" and user.get("premium_expiry"):
        id_text += f"\n⭐ **Premium Until:** {user['premium_expiry'].strftime('%Y-%m-%d %H:%M:%S')}"
    
    await message.reply_text(id_text)

# Admin commands
@app.on_message(filters.command("addpremium") & filters.private)
async def addpremium_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.reply_text("❌ **Usage:** /addpremium <user_id> <days>")
            return
        
        target_user_id = int(args[1])
        days = int(args[2])
        
        target_user = await get_user(target_user_id)
        if not target_user:
            await message.reply_text("❌ User not found.")
            return
        
        expiry_date = datetime.now() + timedelta(days=days)
        
        await update_user(target_user_id, {
            "user_type": "premium",
            "premium_expiry": expiry_date
        })
        
        # Notify user
        try:
            await app.send_message(
                target_user_id,
                f"🎉 **Premium Activated!**\n\n"
                f"Your premium plan has been activated for {days} days.\n"
                f"Expiry: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Thank you for choosing us! 👑"
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
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("rmvpremium") & filters.private)
async def rmvpremium_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply_text("❌ **Usage:** /rmvpremium <user_id>")
            return
        
        target_user_id = int(args[1])
        target_user = await get_user(target_user_id)
        
        if not target_user:
            await message.reply_text("❌ User not found.")
            return
        
        await update_user(target_user_id, {
            "user_type": "free",
            "premium_expiry": None
        })
        
        # Notify user
        try:
            await app.send_message(
                target_user_id,
                "ℹ️ **Premium Plan Ended**\n\n"
                "Your premium plan has expired. You can still use the free features.\n"
                "Use /plans to upgrade again!"
            )
        except:
            pass
        
        await message.reply_text(f"✅ **Premium removed from user {target_user_id}**")
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("ban") & filters.private)
async def ban_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply_text("❌ **Usage:** /ban <user_id>")
            return
        
        target_user_id = int(args[1])
        target_user = await get_user(target_user_id)
        
        if not target_user:
            await message.reply_text("❌ User not found.")
            return
        
        await update_user(target_user_id, {"banned": True})
        
        await message.reply_text(f"✅ **User {target_user_id} has been banned.**")
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("unban") & filters.private)
async def unban_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply_text("❌ **Usage:** /unban <user_id>")
            return
        
        target_user_id = int(args[1])
        target_user = await get_user(target_user_id)
        
        if not target_user:
            await message.reply_text("❌ User not found.")
            return
        
        await update_user(target_user_id, {"banned": False})
        
        await message.reply_text(f"✅ **User {target_user_id} has been unbanned.**")
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("userinfo") & filters.private)
async def userinfo_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply_text("❌ **Usage:** /userinfo <user_id>")
            return
        
        target_user_id = int(args[1])
        target_user = await get_user(target_user_id)
        
        if not target_user:
            await message.reply_text("❌ User not found.")
            return
        
        # Try to get user info from Telegram
        try:
            tg_user = await app.get_users(target_user_id)
            username = f"@{tg_user.username}" if tg_user.username else "N/A"
            name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
        except:
            username = "N/A"
            name = "N/A"
        
        info_text = (
            f"👤 **User Information**\n\n"
            f"🆔 **User ID:** `{target_user_id}`\n"
            f"👤 **Username:** {username}\n"
            f"📛 **Name:** {name}\n"
            f"📅 **Registered:** {target_user['registered_at'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💎 **Plan:** {target_user['user_type'].title()}\n"
            f"🚫 **Banned:** {'Yes' if target_user.get('banned') else 'No'}\n"
            f"📊 **Total Files:** {target_user['total_files_processed']}\n"
            f"📅 **Daily Used:** {target_user['daily_checks_used']}"
        )
        
        if target_user["user_type"] == "premium" and target_user.get("premium_expiry"):
            expiry = target_user['premium_expiry']
            days_left = (expiry - datetime.now()).days
            info_text += f"\n⭐ **Premium Expiry:** {expiry.strftime('%Y-%m-%d %H:%M:%S')}\n"
            info_text += f"📅 **Days Left:** {days_left}"
        
        await message.reply_text(info_text)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        total_users = users_collection.count_documents({})
        premium_users = users_collection.count_documents({"user_type": "premium"})
        free_users = users_collection.count_documents({"user_type": "free"})
        banned_users = users_collection.count_documents({"banned": True})
        
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_registrations = users_collection.count_documents({
            "registered_at": {"$gte": today}
        })
        
        total_files_processed = users_collection.aggregate([
            {"$group": {"_id": None, "total": {"$sum": "$total_files_processed"}}}
        ])
        total_files = 0
        for doc in total_files_processed:
            total_files = doc["total"]
        
        stats_text = (
            "📊 **Bot Statistics**\n\n"
            f"👥 **Total Users:** {total_users}\n"
            f"💎 **Premium Users:** {premium_users}\n"
            f"🆓 **Free Users:** {free_users}\n"
            f"🚫 **Banned Users:** {banned_users}\n"
            f"📈 **Today's Registrations:** {today_registrations}\n"
            f"📁 **Total Files Processed:** {total_files}\n"
            f"⏰ **Current Queue:** {get_queue_size()}\n"
            f"🔄 **Active Processes:** {len(processing_users)}"
        )
        
        await message.reply_text(stats_text)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    if not message.reply_to_message:
        await message.reply_text("❌ **Please reply to a message to broadcast.**")
        return
    
    broadcast_msg = message.reply_to_message
    users = users_collection.find({})
    
    success = 0
    failed = 0
    
    progress_msg = await message.reply_text("📢 **Starting broadcast...**\n\nSent: 0\nFailed: 0")
    
    for user in users:
        try:
            await broadcast_msg.copy(user["user_id"])
            success += 1
        except:
            failed += 1
        
        if (success + failed) % 10 == 0:
            await progress_msg.edit_text(
                f"📢 **Broadcasting...**\n\n"
                f"✅ **Sent:** {success}\n"
                f"❌ **Failed:** {failed}\n"
                f"📊 **Progress:** {success + failed}/{total_users}"
            )
    
    await progress_msg.edit_text(
        f"📢 **Broadcast Complete!**\n\n"
        f"✅ **Sent:** {success}\n"
        f"❌ **Failed:** {failed}\n"
        f"📊 **Total:** {success + failed}"
    )

@app.on_message(filters.command("serverstats") & filters.private)
async def serverstats_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    try:
        # CPU usage
        cpu_usage = psutil.cpu_percent(interval=1)
        
        # Memory usage
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        memory_total = memory.total / (1024 ** 3)
        memory_used = memory.used / (1024 ** 3)
        
        # Disk usage
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent
        disk_total = disk.total / (1024 ** 3)
        disk_used = disk.used / (1024 ** 3)
        
        # Bot stats
        total_users = users_collection.count_documents({})
        queue_size = get_queue_size()
        active_processes = len(processing_users)
        
        stats_text = (
            "🖥️ **Server Statistics**\n\n"
            f"⚡ **CPU Usage:** {cpu_usage}%\n"
            f"💾 **Memory Usage:** {memory_usage}% ({memory_used:.1f}GB / {memory_total:.1f}GB)\n"
            f"💿 **Disk Usage:** {disk_usage}% ({disk_used:.1f}GB / {disk_total:.1f}GB)\n\n"
            f"🤖 **Bot Stats:**\n"
            f"👥 **Total Users:** {total_users}\n"
            f"📋 **Queue Size:** {queue_size}\n"
            f"🔄 **Active Processes:** {active_processes}"
        )
        
        await message.reply_text(stats_text)
        
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")

# Callback query handlers
@app.on_callback_query(filters.regex(r'^(domain_mode|keyword_mode|mixed_mode|cancel)$'))
async def processing_mode_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    try:
        if user_id not in processing_users:
            await callback_query.answer("❌ Session expired. Please start again.", show_alert=True)
            return
        
        data = callback_query.data
        
        if data == "cancel":
            processing_users[user_id]['cancelled'] = True
            await callback_query.message.edit_text("🛑 **Cancelled.**")
            if user_id in processing_users:
                del processing_users[user_id]
            return
        
        processing_users[user_id]['processing_mode'] = data
        processing_users[user_id]['status'] = 'waiting_for_format'
        
        # Check user permissions for combo types
        user = await get_user(user_id)
        settings = await get_settings()
        
        available_formats = settings["premium_combo_types"] if user["user_type"] == "premium" else settings["free_combo_types"]
        
        keyboard_buttons = []
        row = []
        
        if "email_pass" in available_formats:
            row.append(InlineKeyboardButton("📧 Email:Pass", callback_data="format_email_pass"))
        if "user_pass" in available_formats:
            row.append(InlineKeyboardButton("👤 User:Pass", callback_data="format_user_pass"))
        if row:
            keyboard_buttons.append(row)
        
        row = []
        if "number_pass" in available_formats:
            row.append(InlineKeyboardButton("🔢 Number:Pass", callback_data="format_number_pass"))
        if "ulp" in available_formats:
            row.append(InlineKeyboardButton("📄 ULP (Full Line)", callback_data="format_ulp"))
        if row:
            keyboard_buttons.append(row)
        
        if len(available_formats) > 1:
            keyboard_buttons.append([InlineKeyboardButton("🔄 All Formats", callback_data="format_all")])
        
        keyboard_buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
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

@app.on_callback_query(filters.regex(r'^format_'))
async def combo_format_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    try:
        if user_id not in processing_users:
            await callback_query.answer("❌ Session expired. Please start again.", show_alert=True)
            return
        
        data = callback_query.data
        
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
        
        user = await get_user(user_id)
        settings = await get_settings()
        
        multi_domain = settings["premium_multi_domain"] if user["user_type"] == "premium" else settings["free_multi_domain"]
        
        if processing_mode == "domain_mode":
            if multi_domain:
                await callback_query.message.edit_text(
                    "🔎 **Enter target domain(s)**\n\n"
                    "**Examples:**\n"
                    "• Single domain: `netflix.com`\n" 
                    "• Multiple domains: `netflix.com gmail.com youtube.com`\n"
                    "• With paths: `netflix.com/account/mfa`\n\n"
                    "🛑 **Send /cancel to abort**"
                )
            else:
                await callback_query.message.edit_text(
                    "🔎 **Enter target domain**\n\n"
                    "**Example:** `netflix.com`\n\n"
                    "🛑 **Send /cancel to abort**"
                )
        elif processing_mode == "keyword_mode":
            if multi_domain:
                await callback_query.message.edit_text(
                    "🔎 **Enter target keyword(s)**\n\n"
                    "**Examples:**\n"
                    "• Single keyword: `password`\n" 
                    "• Multiple keywords: `login user pass`\n"
                    "• Phrases: `reset password`\n\n"
                    "🛑 **Send /cancel to abort**"
                )
            else:
                await callback_query.message.edit_text(
                    "🔎 **Enter target keyword**\n\n"
                    "**Example:** `password`\n\n"
                    "🛑 **Send /cancel to abort**"
                )
        else:  # mixed_mode
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
            
            asyncio.create_task(start_queue_processor())
        
        await callback_query.answer()
    
    except Exception as e:
        print(f"Error in format handler: {e}")
        await callback_query.answer("❌ Error occurred", show_alert=True)

@app.on_callback_query(filters.regex(r'^plan_'))
async def plan_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    plan_days = callback_query.data.split('_')[1]
    
    plan_info = PLANS[plan_days]
    
    payment_text = (
        f"💎 **Plan: {plan_days} Day{'s' if int(plan_days) > 1 else ''} - ${plan_info['price']}**\n\n"
        "💰 **Payment Methods:**\n\n"
    )
    
    for method, address in PAYMENT_METHODS.items():
        payment_text += f"**{method.replace('_', ' ').title()}:**\n`{address}`\n\n"
    
    payment_text += (
        "📝 **Instructions:**\n"
        "1. Send payment to any address above\n"
        "2. Take screenshot or note transaction ID\n"
        "3. Forward proof to admin\n"
        "4. We'll activate your premium ASAP\n\n"
        f"👑 **Contact:** {OWNER_USERNAME}"
    )
    
    await callback_query.message.edit_text(payment_text)
    await callback_query.answer()

# Handler for target domain/keyword input
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel", "combo", "queue", "myplan", "plans", "id", "register"]))
async def handle_target_input(client: Client, message: Message):
    user_id = message.from_user.id
    
    try:
        if user_id not in processing_users or processing_users[user_id].get('status') != 'ready_for_input':
            return
        
        processing_mode = processing_users[user_id].get('processing_mode')
        input_text = message.text.strip()
        
        user = await get_user(user_id)
        settings = await get_settings()
        multi_domain = settings["premium_multi_domain"] if user["user_type"] == "premium" else settings["free_multi_domain"]
        
        if processing_mode == "domain_mode":
            potential_domains = input_text.split()
            target_domains = []
            
            for domain in potential_domains:
                if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[a-zA-Z0-9-/_]*)?$', domain):
                    target_domains.append(domain)
                else:
                    await message.reply_text(f"❌ **Invalid domain format:** `{domain}`\n\nPlease send valid domains like: `netflix.com` or `netflix.com/account`")
                    return
            
            if not target_domains:
                await message.reply_text("❌ **No valid domains provided.**")
                return
            
            if not multi_domain and len(target_domains) > 1:
                await message.reply_text("❌ **Multiple domains not allowed in your plan.**\nUpgrade to premium for multi-domain support.")
                return
            
            processing_users[user_id]['target_domains'] = target_domains
            
        elif processing_mode == "keyword_mode":
            target_keywords = input_text.split()
            
            if not target_keywords:
                await message.reply_text("❌ **No keywords provided.**")
                return
            
            if not multi_domain and len(target_keywords) > 1:
                await message.reply_text("❌ **Multiple keywords not allowed in your plan.**\nUpgrade to premium for multi-keyword support.")
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
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"Queue processor error: {e}")
    finally:
        queue_processor_running = False
        print("Queue processor stopped")

# Process individual user task
async def process_user_task(user_id, task_data):
    """Process a single user task"""
    try:
        # Send initial processing message
        processing_msg = await app.send_message(
            user_id, 
            "⚡ **Starting processing...**\n\n📥 Downloading your file..."
        )
        
        # Download file
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
            await process_all_formats(user_id, file_path, target_domains, target_keywords, task_data)
        else:
            await process_single_format(user_id, file_path, target_domains, target_keywords, task_data, combo_format)
        
    except Exception as e:
        print(f"Error processing task for user {user_id}: {e}")
        try:
            await app.send_message(user_id, f"❌ **Processing error:** {str(e)}")
        except:
            pass
    finally:
        # Update user stats and cleanup
        if user_id in processing_users:
            user = await get_user(user_id)
            await update_user(user_id, {
                "last_check_time": datetime.now(),
                "daily_checks_used": user["daily_checks_used"] + 1,
                "total_files_processed": user["total_files_processed"] + 1
            })
            
            if 'file_path' in processing_users[user_id]:
                await cleanup_files(processing_users[user_id]['file_path'])
            del processing_users[user_id]

async def download_file_with_progress(user_id, file_id, message_id):
    """Download file"""
    try:
        file_path = f"temp_{user_id}_{int(time.time())}.txt"
        
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
    
    if result is None:
        await app.send_message(user_id, "🛑 **Processing cancelled.**")
        return
    
    if not result or all(not combos for combos in result.values()):
        await app.send_message(user_id, "❌ **No valid combos found.**")
        return
    
    processing_time = time.time() - task_data['start_time']
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if combo_format == "ulp":
        format_name = "ULP (Full Line)"
    else:
        format_name = combo_format.replace('_', ':').title()
    
    processing_mode = task_data.get('processing_mode', 'mixed_mode')
    
    if processing_mode == "mixed_mode" and 'mixed' in result:
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
            await asyncio.sleep(0.5)
        
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
    user_data = await get_user(user_id)
    settings = await get_settings()
    available_formats = settings["premium_combo_types"] if user_data["user_type"] == "premium" else settings["free_combo_types"]
    
    format_names = {
        "email_pass": "Email:Pass", 
        "user_pass": "User:Pass", 
        "number_pass": "Number:Pass",
        "ulp": "ULP (Full Line)"
    }
    
    results = {}
    total_combos = 0
    
    for fmt in available_formats:
        if user_id in processing_users and processing_users[user_id].get('cancelled', False):
            await app.send_message(user_id, "🛑 **Processing cancelled.**")
            return
        
        await app.edit_message_text(
            user_id,
            processing_users[user_id]['progress_msg'],
            f"🔄 **Processing {format_names[fmt]}...**\n\nPlease wait..."
        )
        
        result = await process_log_file(user_id, file_path, target_domains, target_keywords, fmt)
        if result:
            results[fmt] = result
    
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

# Background task to reset daily limits
async def reset_daily_limits():
    while True:
        now = datetime.now()
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        
        await asyncio.sleep(wait_seconds)
        
        users_collection.update_many({}, {"$set": {"daily_checks_used": 0}})
        print("Daily limits reset at midnight")

# Background task to check premium expiry
async def check_premium_expiry():
    while True:
        await asyncio.sleep(3600)  Check every hour
        
        expired_users = users_collection.find({
            "user_type": "premium",
            "premium_expiry": {"$lt": datetime.now()}
        })
        
        for user in expired_users:
            await update_user(user["user_id"], {
                "user_type": "free",
                "premium_expiry": None
            })
            
            try:
                await app.send_message(
                    user["user_id"],
                    "ℹ️ **Your Premium Plan Has Expired**\n\n"
                    "Your premium subscription has ended. You can still use free features.\n"
                    "Use /plans to upgrade again!"
                )
            except:
                pass

# Error handler
@app.on_error()
async def error_handler(client: Client, error: Exception):
    print(f"Bot error: {error}")

# Start the bot
if __name__ == "__main__":
    print("🤖 Advanced Combo Bot Starting...")
    
    # Initialize database
    asyncio.run(initialize_database())
    
    # Start background tasks
    asyncio.create_task(reset_daily_limits())
    asyncio.create_task(check_premium_expiry())
    
    print("✅ Bot is responsive and ready!")
    try:
        app.run()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
