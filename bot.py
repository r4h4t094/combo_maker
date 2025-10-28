import os
import re
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import RPCError, FloodWait, BadRequest
from datetime import datetime
from collections import deque
import asyncio

# Bot configuration
API_ID = 23933044
API_HASH = "6df11147cbec7d62a323f0f498c8c03a"
BOT_TOKEN = "7989255010:AAGI73-gpORxqqnsNrRRCLWNCyyACA0ia-w"
OWNER_ID = 7125341830
OWNER_USERNAME = "@still_alivenow"

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=200, max_concurrent_transmissions = 1000, sleep_threshold=15)

# Global variables
processing_users = {}
MAX_FILE_SIZE = 4000 * 1024 * 1024
PROGRESS_UPDATE_INTERVAL = 5
processing_queue = deque()
queue_processor_running = False

# Helper function to clean up files
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
        "/help - Detailed help\n\n"
        f"👑 **Owner:** {OWNER_USERNAME}"
    )
    
    await message.reply_text(welcome_msg, disable_web_page_preview=True)

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
    
    await message.reply_text(queue_text)

# Combo command handler
@app.on_message(filters.command("combo") & filters.private)
async def combo_command(client: Client, message: Message):
    user_id = message.from_user.id
    
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
        if file_size > MAX_FILE_SIZE:
            await message.reply_text(f"⚠️ File too large. Max size: {MAX_FILE_SIZE//(1024*1024)}MB")
            return
        
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
        await message.reply_text("🛑 **Processing cancelled.**")
        
        # Cleanup after a short delay
        await asyncio.sleep(2)
        if user_id in processing_users:
            # Cleanup any downloaded files
            if 'file_path' in processing_users[user_id]:
                await cleanup_files(processing_users[user_id]['file_path'])
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
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel", "combo", "queue"]))
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

# Error handler
@app.on_error()
async def error_handler(client: Client, error: Exception):
    print(f"Bot error: {error}")

# Start the bot
if __name__ == "__main__":
    print("🤖 Advanced Combo Bot Started...")
    print("✅ Bot is responsive and ready!")
    try:
        app.run()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
