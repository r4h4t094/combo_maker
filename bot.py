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
from tqdm import tqdm

# Bot configuration
API_ID = 24720817
API_HASH = "43669876f7dbd754e157c69c89ebf3eb"
BOT_TOKEN = "7534650093:AAHs6cD3AoPT5jkg2ugoP_XxcvPyPuuLBk4"

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global variables to track processing
processing = False
cancel_processing = False

# Welcome message handler
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    welcome_msg = """
    👋 **Welcome to Email:Pass Combo Generator Bot!**

    **How to use:**
    1. Send me a `.txt` file
    2. Reply to that file with `/combo` command
    3. Choose between Targeted or Mixed combo
    4. For Targeted, provide the domain (e.g., gmail.com)
    5. Wait for processing to complete

    The bot will send you the processed combo file when done.
    """
    await message.reply_text(welcome_msg)

# Combo command handler
@app.on_message(filters.command("combo") & filters.reply)
async def combo_command(client: Client, message: Message):
    global processing
    
    if processing:
        await message.reply_text("⚠️ Another file is currently being processed. Please wait.")
        return
    
    replied_msg = message.reply_to_message
    
    if not replied_msg.document or not replied_msg.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Please reply to a .txt file with the /combo command.")
        return
    
    file_size = replied_msg.document.file_size
    
    if file_size > 200 * 1024 * 1024:  # 200 MB limit
        await message.reply_text("⚠️ File size exceeds 200 MB limit. Please provide a smaller file.")
        return
    
    # Create buttons for Targeted or Mixed
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Targeted", callback_data="targeted")],
        [InlineKeyboardButton("🌀 Mixed", callback_data="mixed")]
    ])
    
    await message.reply_text(
        "🔹 Please choose the combo type:",
        reply_markup=keyboard
    )

# Callback query handler
@app.on_callback_query()
async def callback_handler(client: Client, callback_query: CallbackQuery):
    global processing, cancel_processing
    
    data = callback_query.data
    chat_id = callback_query.message.chat.id
    message_id = callback_query.message.id
    
    if data in ["targeted", "mixed"]:
        if data == "targeted":
            await app.edit_message_text(
                chat_id,
                message_id,
                "🔹 Please send the target domain (e.g., gmail.com, yahoo.com)"
            )
        else:
            # For mixed, we can proceed directly
            replied_msg = callback_query.message.reply_to_message.reply_to_message
            await process_combo_file(replied_msg, chat_id, target_domain=None)
            await app.delete_messages(chat_id, message_id)
    
    elif data == "cancel_processing":
        cancel_processing = True
        await app.edit_message_text(
            chat_id,
            message_id,
            "⏹ Processing canceled. The partial file will be sent if any combos were found."
        )

# Process the combo file
async def process_combo_file(message: Message, chat_id: int, target_domain: str = None):
    global processing, cancel_processing
    
    processing = True
    cancel_processing = False
    
    try:
        # Download the file
        download_msg = await app.send_message(chat_id, "⬇️ Downloading the file...")
        file_path = await message.download()
        
        # Prepare for processing
        total_lines = sum(1 for _ in open(file_path, 'r', errors='ignore'))
        found_combos = set()
        
        await app.edit_message_text(
            chat_id,
            download_msg.id,
            f"🔍 Processing {total_lines:,} lines...\n\n"
            f"Found: 0 combos\n"
            f"Progress: 0%"
        )
        
        # Create progress bar
        progress_bar_length = 20
        progress_msg = download_msg
        
        # Process the file
        with open(file_path, 'r', errors='ignore') as file:
            for i, line in enumerate(file):
                if cancel_processing:
                    break
                
                # Extract email:pass combos
                matches = re.findall(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}):([^\s]+)', line)
                
                for email, password in matches:
                    if target_domain:
                        if email.lower().endswith(f"@{target_domain.lower()}"):
                            found_combos.add(f"{email}:{password}")
                    else:
                        found_combos.add(f"{email}:{password}")
                
                # Update progress every 1000 lines or 5% progress
                if i % max(1000, total_lines // 20) == 0:
                    progress = (i / total_lines) * 100
                    filled_length = int(progress_bar_length * progress // 100)
                    bar = '█' * filled_length + '░' * (progress_bar_length - filled_length)
                    
                    try:
                        await app.edit_message_text(
                            chat_id,
                            progress_msg.id,
                            f"🔍 Processing {total_lines:,} lines...\n\n"
                            f"Found: {len(found_combos):,} combos\n"
                            f"Progress: {progress:.1f}%\n"
                            f"[{bar}]"
                        )
                    except RPCError:
                        pass
        
        # Save the combos to a new file
        if found_combos:
            if target_domain:
                output_filename = f"{target_domain}_{int(time.time())}.txt"
            else:
                output_filename = f"mixed_combos_{int(time.time())}.txt"
            
            with open(output_filename, 'w') as out_file:
                out_file.write("\n".join(found_combos))
            
            # Send the file
            await app.send_document(
                chat_id,
                output_filename,
                caption=f"✅ Found {len(found_combos):,} combos!" + 
                       ("\n⚠️ Processing was canceled." if cancel_processing else "")
            )
            
            # Clean up
            os.remove(output_filename)
        else:
            await app.edit_message_text(
                chat_id,
                progress_msg.id,
                "❌ No valid combos found in the file." + 
                ("\n⚠️ Processing was canceled." if cancel_processing else "")
            )
        
        # Clean up the original file
        os.remove(file_path)
        
    except Exception as e:
        await app.send_message(chat_id, f"❌ An error occurred: {str(e)}")
    finally:
        processing = False

# Handle target domain input
@app.on_message(filters.text)
async def handle_target_domain(client: Client, message: Message):
    global processing
    
    if processing:
        # Check if this is a reply to our "send target domain" message
        replied_msg = message.reply_to_message
        if replied_msg and "send the target domain" in replied_msg.text:
            target_domain = message.text.strip()
            if not re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', target_domain):
                await message.reply_text("❌ Invalid domain format. Please send a valid domain (e.g., gmail.com)")
                return
            
            # Get the original file message (2 levels up in reply chain)
            original_file_msg = replied_msg.reply_to_message.reply_to_message
            
            # Process the file with target domain
            await process_combo_file(original_file_msg, message.chat.id, target_domain)
            
            # Delete the intermediate messages
            await client.delete_messages(message.chat.id, [replied_msg.id, message.id])

# Start the bot
print("Bot is running...")
app.run()
