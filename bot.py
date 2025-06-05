import os
import re
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import RPCError
from typing import Tuple, List, Optional

# Bot configuration
API_ID = 24720817
API_HASH = "43669876f7dbd754e157c69c89ebf3eb"
BOT_TOKEN = "7534650093:AAHs6cD3AoPT5jkg2ugoP_XxcvPyPuuLBk4"

# Initialize the bot
app = Client(
    "combo_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Constants
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB in bytes
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# User states for conversation handling
user_states = {}

# Helper functions
def get_file_size(file_path: str) -> int:
    """Get file size in bytes"""
    return os.path.getsize(file_path)

def is_valid_domain(domain: str) -> bool:
    """Check if domain is valid"""
    pattern = r'^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$'
    return re.match(pattern, domain) is not None

def clean_domain(domain: str) -> str:
    """Clean domain input"""
    domain = domain.strip().lower()
    if domain.startswith(("http://", "https://", "www.")):
        domain = re.sub(r'^https?://(www\.)?', '', domain)
    domain = domain.split('/')[0]  # Remove paths
    return domain

async def process_file(
    file_path: str,
    target_domain: Optional[str] = None,
    progress_callback: Optional[callable] = None,
    cancel_flag: Optional[dict] = None
) -> Tuple[List[str], int]:
    """Process the file to extract email:pass combos"""
    email_pass_pattern = re.compile(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s]+)')
    combos = set()
    total_lines = 0
    processed_lines = 0
    
    # Count total lines first for progress
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for _ in f:
            total_lines += 1
    
    # Now process the file
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if cancel_flag and cancel_flag.get('cancel'):
                return [], 0
                
            processed_lines += 1
            if progress_callback and processed_lines % 1000 == 0:
                await progress_callback(processed_lines, total_lines)
                
            matches = email_pass_pattern.findall(line)
            for match in matches:
                if target_domain:
                    if f"@{target_domain}" in match.lower():
                        combos.add(match)
                else:
                    combos.add(match)
    
    return list(combos), len(combos)

async def send_progress(
    message: Message,
    chat_id: int,
    processed: int,
    total: int,
    last_update: float,
    cancel_flag: dict
) -> float:
    """Send progress update if enough time has passed"""
    current_time = time.time()
    if current_time - last_update >= 1:  # Update every 1 second
        progress = min(100, int((processed / total) * 100))
        try:
            await message.edit_text(
                f"Processing... {progress}%\n"
                f"Processed: {processed}/{total} lines\n\n"
                "Click below to cancel:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_processing")]
                ])
            )
        except RPCError:
            pass
        return current_time
    return last_update

# Bot handlers
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    """Send welcome message"""
    welcome_text = (
        "👋 Welcome to the Combo Generator Bot!\n\n"
        "📁 Send me a .txt file with the /combo command and I'll extract email:password combinations for you.\n\n"
        "⚠️ Note: Files larger than 200MB will be rejected for performance reasons."
    )
    await message.reply_text(welcome_text)

@app.on_message(filters.command("combo") & filters.document)
async def handle_combo_command(client: Client, message: Message):
    """Handle the combo command with a document"""
    # Check file size
    file_size = message.document.file_size
    if file_size > MAX_FILE_SIZE:
        await message.reply_text("⚠️ File size exceeds 200MB limit. Please send a smaller file.")
        return
    
    # Check file extension
    file_name = message.document.file_name.lower()
    if not file_name.endswith('.txt'):
        await message.reply_text("❌ Please send a .txt file.")
        return
    
    # Store file info for later processing
    user_id = message.from_user.id
    user_states[user_id] = {
        "file_id": message.document.file_id,
        "file_name": message.document.file_name,
        "chat_id": message.chat.id,
        "message_id": message.id
    }
    
    # Ask for processing type
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Targeted", callback_data="targeted")],
        [InlineKeyboardButton("🌀 Mixed", callback_data="mixed")]
    ])
    
    await message.reply_text(
        "🔍 How would you like to process the file?",
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("^targeted$|^mixed$"))
async def handle_processing_choice(client: Client, callback_query: CallbackQuery):
    """Handle user's choice of processing type"""
    user_id = callback_query.from_user.id
    processing_type = callback_query.data
    
    if user_id not in user_states:
        await callback_query.answer("Session expired. Please send the file again.", show_alert=True)
        return
    
    user_data = user_states[user_id]
    user_data["processing_type"] = processing_type
    
    if processing_type == "targeted":
        await callback_query.message.edit_text(
            "🔗 Please send the target domain (e.g., gmail.com, yahoo.com):\n\n"
            "⚠️ Do not include http:// or https://"
        )
    else:
        # For mixed processing, proceed directly to download
        await callback_query.message.edit_text("📥 Downloading your file...")
        await process_user_file(client, user_id)
    
    await callback_query.answer()

@app.on_message(filters.text & ~filters.command("start") & ~filters.command("combo"))
async def handle_domain_input(client: Client, message: Message):
    """Handle target domain input"""
    user_id = message.from_user.id
    
    if user_id not in user_states or "processing_type" not in user_states[user_id]:
        return
    
    if user_states[user_id]["processing_type"] != "targeted":
        return
    
    domain = clean_domain(message.text)
    if not is_valid_domain(domain):
        await message.reply_text("❌ Invalid domain format. Please send a valid domain (e.g., gmail.com):")
        return
    
    user_states[user_id]["target_domain"] = domain
    await message.reply_text("📥 Downloading your file...")
    await process_user_file(client, user_id)

async def process_user_file(client: Client, user_id: int):
    """Download and process the user's file"""
    if user_id not in user_states:
        return
    
    user_data = user_states[user_id]
    file_id = user_data["file_id"]
    chat_id = user_data["chat_id"]
    
    try:
        # Download the file
        download_msg = await client.send_message(chat_id, "⏳ Downloading file...")
        file_path = await client.download_media(
            message=file_id,
            file_name=os.path.join(TEMP_DIR, f"temp_{user_id}.txt")
        
        await download_msg.edit_text("📊 Processing file...")
        
        # Setup progress tracking
        cancel_flag = {'cancel': False}
        last_update = time.time()
        
        def progress_callback(processed, total):
            nonlocal last_update
            asyncio.create_task(
                send_progress(download_msg, chat_id, processed, total, last_update, cancel_flag)
            )
        
        # Process the file
        target_domain = user_data.get("target_domain")
        combos, count = await process_file(
            file_path,
            target_domain,
            progress_callback,
            cancel_flag
        )
        
        if cancel_flag['cancel']:
            await download_msg.edit_text("❌ Processing canceled.")
            return
        
        if not combos:
            await download_msg.edit_text("❌ No valid email:password combinations found.")
            return
        
        # Save combos to a new file
        output_filename = f"{target_domain or 'mixed'}_{user_id}.txt"
        output_path = os.path.join(TEMP_DIR, output_filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(combos))
        
        # Send the file
        await client.send_document(
            chat_id=chat_id,
            document=output_path,
            caption=f"✅ Found {count} unique {'targeted' if target_domain else 'mixed'} combos!"
        )
        
        # Clean up
        os.remove(file_path)
        os.remove(output_path)
        
    except Exception as e:
        await client.send_message(chat_id, f"❌ An error occurred: {str(e)}")
    finally:
        if user_id in user_states:
            del user_states[user_id]

@app.on_callback_query(filters.regex("^cancel_processing$"))
async def cancel_processing(client: Client, callback_query: CallbackQuery):
    """Handle processing cancellation"""
    user_id = callback_query.from_user.id
    if user_id in user_states:
        user_data = user_states[user_id]
        user_data["cancel"] = True
        await callback_query.answer("Processing will be canceled...")
    else:
        await callback_query.answer("Nothing to cancel.", show_alert=True)

# Start the bot
print("Bot is running...")
app.run()
