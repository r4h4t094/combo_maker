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

# Bot configuration
API_ID = 23933044
API_HASH = "6df11147cbec7d62a323f0f498c8c03a"
BOT_TOKEN = "7989255010:AAH4Ap0mV3f1btlXLBIrMhwErpSbYlcH81E"
OWNER_ID = 7125341830  # Replace with your Telegram ID
OWNER_USERNAME = "@still_alivenow"  # Your Telegram username

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global variables to track processing
processing_users = {}
MAX_FILE_SIZE = 4000 * 1024 * 1024  # 500 MB in bytes
PROGRESS_UPDATE_INTERVAL = 5  # Update progress every 5%

# Helper function to clean up files
async def cleanup_files(*files):
    for file in files:
        try:
            if os.path.exists(file):
                os.remove(file)
        except Exception as e:
            print(f"Error deleting file {file}: {e}")

async def process_log_file(user_id, file_path, target_domains=None):
    total_lines = 0
    processed_lines = 0
    valid_combos = {}  # Dictionary to store combos per domain
    last_update = 0
    
    # Initialize combo storage for each domain
    if target_domains:
        for domain in target_domains:
            valid_combos[domain] = set()
    else:
        valid_combos['mixed'] = set()
    
    try:
        # Count total lines first
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in f:
                total_lines += 1
        
        if total_lines == 0:
            return {}
        
        # Process file
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                processed_lines += 1
                line = line.strip()
                if not line:
                    continue
                
                line_lower = line.lower()
                
                # Calculate current progress percentage
                current_progress = (processed_lines / total_lines) * 100
                
                # Update progress only when we've passed the next threshold
                if current_progress - last_update >= PROGRESS_UPDATE_INTERVAL or processed_lines == total_lines:
                    last_update = current_progress
                    
                    # Build progress bar
                    progress_bar_length = 20
                    filled_length = int(progress_bar_length * processed_lines // total_lines)
                    progress_bar = '█' * filled_length + '░' * (progress_bar_length - filled_length)
                    
                    # Calculate total found combos
                    total_found = sum(len(combos) for combos in valid_combos.values())
                    
                    # Prepare domain counts text
                    domain_counts = []
                    if target_domains:
                        for domain in target_domains:
                            count = len(valid_combos.get(domain, set()))
                            if count > 0:
                                domain_counts.append(f"{domain} → {count}")
                    
                    # Prepare progress message
                    progress_text = (
                        f"🔍 Processing... {current_progress:.1f}%\n"
                        f"[{progress_bar}]\n"
                        f"📊 Processed: {processed_lines}/{total_lines} lines\n"
                        f"✅ Found: {total_found} combos total\n"
                    )
                    
                    # Add domain counts if available
                    if domain_counts:
                        progress_text += "\n".join(domain_counts) + "\n\n"
                    else:
                        progress_text += "\n"
                    
                    progress_text += "⏳ Click /cancel to stop processing."
                    
                    # Try to update progress message with flood control
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
                
                # Check cancellation
                if user_id in processing_users and processing_users[user_id].get('cancelled', False):
                    return None
                
                # For targeted mode: check if any target domain exists ANYWHERE in the line
                if target_domains:
                    domain_found = None
                    for domain in target_domains:
                        if domain.lower() in line_lower:
                            domain_found = domain
                            break
                    
                    if not domain_found:
                        continue
                
                # Extract first email:pass combo from line (regardless of email domain)
                email_pass_match = re.search(
                    r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}):([^\s]+)', 
                    line
                )
                if email_pass_match:
                    email = email_pass_match.group(1)
                    password = email_pass_match.group(2)
                    
                    if target_domains:
                        # Add to the domain's combos
                        valid_combos[domain_found].add(f"{email}:{password}")
                    else:
                        # For mixed mode, add all valid combos
                        valid_combos['mixed'].add(f"{email}:{password}")
        
        # Convert sets to lists
        return {domain: list(combos) for domain, combos in valid_combos.items()}
    
    except Exception as e:
        print(f"Error processing file: {e}")
        return {}
# Start command handler
@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    welcome_msg = (
        "👋 **Welcome to the Email:Pass Combo Generator Bot!**\n\n"
        "📌 **How to use:**\n"
        "1. Send or reply to a .txt file with the command `/combo`\n"
        "2. Choose between targeted or mixed combos\n"
        "3. For targeted, enter the domain (e.g., gmail.com) or multiple domains separated by space\n"
        "4. Wait for processing to complete\n\n"
        "⚙️ **Commands:**\n"
        "/start - Show this help message\n"
        "/combo - Start processing a file\n"
        "/cancel - Cancel current processing\n"
        "/help - Show detailed help\n\n"
        f"👑 **Owner:** {OWNER_USERNAME}\n"
        "🔐 This bot securely processes your files and deletes them immediately after."
    )
    
    try:
        await message.reply_text(welcome_msg, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error in start command: {e}")

# Help command handler
@app.on_message(filters.command("help") & filters.private)
async def help_command(client: Client, message: Message):
    help_text = (
        "📖 **Combo Bot Help Guide**\n\n"
        "🔹 **What this bot does:**\n"
        "Extracts email:password combinations from text files, with options for:\n"
        "- 🎯 Targeted extraction (specific domains like gmail.com)\n"
        "- 🌀 Mixed extraction (all valid email:pass combos)\n\n"
        "🔹 **How to use:**\n"
        "1. Send a .txt file or reply to one with `/combo`\n"
        "2. Choose processing type (targeted/mixed)\n"
        "3. For targeted, enter one or multiple domains separated by space\n"
        "4. Wait for processing to complete\n\n"
        "⚙️ **Technical Details:**\n"
        "- Max file size: 200MB\n"
        "- Files are deleted after processing\n"
        "- Processing progress is shown in real-time\n\n"
        f"💡 **Need help? Contact owner:** {OWNER_USERNAME}"
    )
    
    try:
        await message.reply_text(help_text, disable_web_page_preview=True)
    except Exception as e:
        print(f"Error in help command: {e}")

# Combo command handler
@app.on_message(filters.command("combo") & filters.private)
async def combo_command(client: Client, message: Message):
    user_id = message.from_user.id
    # Check if the command is used without replying to a message
    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ **Please reply to a .txt file with the /combo command.**\n\n"
            "Example:\n"
            "1. First, send or forward the .txt file\n"
            "2. Then reply to that file with `/combo`\n\n"
            "Need help? Use /help for more instructions."
        )
        return

    try:
        # Check if the replied message has a document
        if not message.reply_to_message.document:
            await message.reply_text("❌ Please reply to a .txt file with the /combo command.")
            return
             
        # Check file extension
        file_name = message.reply_to_message.document.file_name or ""
        if not file_name.lower().endswith('.txt'):
            await message.reply_text("❌ Invalid file type. Please send a .txt file.")
            return
        
        # Check file size
        file_size = message.reply_to_message.document.file_size
        if file_size > MAX_FILE_SIZE:
            await message.reply_text(f"⚠️ File size exceeds {MAX_FILE_SIZE//(1024*1024)}MB. Please send a smaller file.")
            return
        
        # Store user data
        processing_users[user_id] = {
            'file_id': message.reply_to_message.document.file_id,
            'file_name': file_name,
            'file_size': file_size,
            'cancelled': False,
            'start_time': time.time()
        }
        
        # Ask for processing type
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Targeted (Specific Domain)", callback_data="targeted")],
            [InlineKeyboardButton("🌀 Mixed (All Domains)", callback_data="mixed")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ])
        
        await message.reply_text(
            "📝 **Please choose the type of combo you want to generate:**\n\n"
            "🎯 **Targeted** - Extract combos for specific domain(s) (e.g., gmail.com or multiple domains separated by space)\n"
            "🌀 **Mixed** - Extract all valid email:password combinations\n\n"
            f"👑 Bot Owner: {OWNER_USERNAME}",
            reply_markup=keyboard
        )
    
    except Exception as e:
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        if user_id in processing_users:
            del processing_users[user_id]

# Cancel command handler
@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    try:
        if user_id in processing_users:
            processing_users[user_id]['cancelled'] = True
            await message.reply_text("🛑 Processing cancelled. Any incomplete files will be deleted.")
        else:
            await message.reply_text("ℹ️ No active processing to cancel.")
    except Exception as e:
        await message.reply_text(f"❌ Error cancelling operation: {str(e)}")

# Callback query handler
@app.on_callback_query()
async def callback_query_handler(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if user_id not in processing_users:
            await callback_query.answer("❌ Session expired. Please start again.", show_alert=True)
            return
        
        if data == "cancel":
            processing_users[user_id]['cancelled'] = True
            await callback_query.message.edit_text("🛑 Processing cancelled.")
            await callback_query.answer("Cancelled")
            if user_id in processing_users:
                del processing_users[user_id]
            return
        
        if data in ["targeted", "mixed"]:
            processing_users[user_id]['type'] = data
            
            if data == "targeted":
                await callback_query.message.edit_text(
                    "🔎 **Please send the target domain(s)** (e.g., gmail.com or multiple domains separated by space)\n\n"
                    "ℹ️ Examples:\n"
                    "- For single domain: `gmail.com`\n"
                    "- For multiple domains: `gmail.com yahoo.com netflix.com`\n\n"
                    "🛑 Send /cancel to abort"
                )
                await callback_query.answer()
            else:
                # For mixed, proceed directly to download
                msg = await callback_query.message.edit_text("📥 Downloading your file... Please wait.")
                await callback_query.answer("Starting mixed processing...")
                
                # Download the file
                try:
                    file_path = await client.download_media(
                        message=processing_users[user_id]['file_id'],
                        file_name=f"temp_{user_id}_{int(time.time())}.txt"
                    )
                    
                    processing_users[user_id]['file_path'] = file_path
                    processing_users[user_id]['progress_msg'] = msg.id
                    
                    # Start processing
                    await process_and_send_combos(user_id)
                    
                except Exception as e:
                    await callback_query.message.edit_text(f"❌ Error: {str(e)}")
                    if user_id in processing_users:
                        del processing_users[user_id]
    
    except Exception as e:
        print(f"Error in callback handler: {e}")
        try:
            await callback_query.answer("❌ An error occurred", show_alert=True)
        except:
            pass

# Handler for target domain
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel", "combo"]))
async def handle_target_domain(client: Client, message: Message):
    user_id = message.from_user.id
    
    try:
        if user_id not in processing_users or 'type' not in processing_users[user_id]:
            return
        
        if processing_users[user_id]['type'] == "targeted":
            # Split input into multiple domains
            input_text = message.text.strip().lower()
            potential_domains = input_text.split()
            target_domains = []
            
            # Validate each domain
            '''for domain in potential_domains:
                if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', domain):
                    target_domains.append(domain)
                else:
                    await message.reply_text(f"❌ Invalid domain format: {domain}. Please send valid domains (e.g., gmail.com or multiple domains separated by space)")
                    return'''

            for domain in potential_domains:
                if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/[a-zA-Z0-9-]*)?$', domain):
                    target_domains.append(domain)
                else:
                    await message.reply_text(f"❌ Invalid domain format: {domain}. Please send valid domains (e.g., gmail.com or multiple domains separated by space)")
                    return
            
            if not target_domains:
                await message.reply_text("❌ No valid domains provided. Please try again.")
                return
            
            processing_users[user_id]['target_domains'] = target_domains
            msg = await message.reply_text("📥 Downloading your file... Please wait.")
            
            # Download the file
            try:
                file_path = await client.download_media(
                    message=processing_users[user_id]['file_id'],
                    file_name=f"temp_{user_id}_{int(time.time())}.txt"
                )
                
                processing_users[user_id]['file_path'] = file_path
                processing_users[user_id]['progress_msg'] = msg.id
                
                # Start processing
                await process_and_send_combos(user_id, target_domains)
                
            except Exception as e:
                await message.reply_text(f"❌ Error: {str(e)}")
                if user_id in processing_users:
                    del processing_users[user_id]
    
    except Exception as e:
        await message.reply_text(f"❌ An error occurred: {str(e)}")
        if user_id in processing_users:
            del processing_users[user_id]

# Function to process and send combos
async def process_and_send_combos(user_id, target_domains=None):
    try:
        if user_id not in processing_users:
            return
        
        file_path = processing_users[user_id]['file_path']
        start_time = processing_users[user_id].get('start_time', time.time())
        
        # Process the file
        combos_dict = await process_log_file(user_id, file_path, target_domains)
        
        if combos_dict is None:  # Processing was cancelled
            await app.send_message(user_id, "🛑 Processing was cancelled.")
            await cleanup_files(file_path)
            if user_id in processing_users:
                del processing_users[user_id]
            return
        
        if not combos_dict or all(not combos for combos in combos_dict.values()):
            await app.send_message(
                user_id,
                "❌ No valid email:pass combos found." + 
                (f"\nNo combos found for domains: {', '.join(target_domains)}" if target_domains else "")
            )
            await cleanup_files(file_path)
            if user_id in processing_users:
                del processing_users[user_id]
            return
        
        # Save and send combos for each domain
        processing_time = time.time() - start_time
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # For mixed mode
        if not target_domains and 'mixed' in combos_dict:
            output_filename = f"mixed_combos_{timestamp}.txt"
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(combos_dict['mixed']))
            
            await app.send_document(
                chat_id=user_id,
                document=output_filename,
                caption=(
                    f"✅ **Successfully processed!**\n\n"
                    f"🔹 **Type:** Mixed\n"
                    f"🔹 **Combos found:** {len(combos_dict['mixed'])}\n"
                    f"🔹 **Processing time:** {processing_time:.2f} seconds\n\n"
                    f"👑 **Bot Owner:** {OWNER_USERNAME}\n"
                    "⚠️ This file will be deleted from our server shortly."
                )
            )
            await cleanup_files(output_filename)
        
        # For targeted mode
        else:
            total_combos = 0
            sent_files = 0
            
            for domain, combos in combos_dict.items():
                if not combos:
                    continue
                
                total_combos += len(combos)
                domain_clean = domain.replace('.', '_')
                domain_clean = domain.replace('/', '_')
                output_filename = f"{domain_clean}_{timestamp}.txt"
                
                with open(output_filename, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(combos))
                
                await app.send_document(
                    chat_id=user_id,
                    document=output_filename,
                    caption=(
                        f"✅ **Domain:** {domain}\n"
                        f"🔹 **Combos found:** {len(combos)}\n\n"
                        f"👑 **Bot Owner:** {OWNER_USERNAME}\n"
                        "⚠️ This file will be deleted from our server shortly."
                    )
                )
                sent_files += 1
                await cleanup_files(output_filename)
                await asyncio.sleep(1)  # Small delay between files
            
            # Send summary if multiple files were sent
            if sent_files > 1:
                await app.send_message(
                    user_id,
                    f"📦 **Processing Complete!**\n\n"
                    f"🔹 **Domains processed:** {len(target_domains)}\n"
                    f"🔹 **Files sent:** {sent_files}\n"
                    f"🔹 **Total combos found:** {total_combos}\n"
                    f"🔹 **Processing time:** {processing_time:.2f} seconds\n\n"
                    f"👑 **Bot Owner:** {OWNER_USERNAME}"
                )
        
        # Cleanup
        await cleanup_files(file_path)
        
    except Exception as e:
        await app.send_message(
            user_id,
            f"❌ **An error occurred during processing:**\n{str(e)}\n\n"
            f"Please contact {OWNER_USERNAME} if this persists."
        )
    finally:
        if user_id in processing_users:
            del processing_users[user_id]

# Error handler
@app.on_error()
async def error_handler(client: Client, error: Exception):
    print(f"Error occurred: {error}")

# Start the bot
if __name__ == "__main__":
    print("Bot is running...")
    try:
        app.run()
    except Exception as e:
        print(f"Fatal error: {e}")
