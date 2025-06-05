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

# Bot credentials
API_ID = 24720817
API_HASH = "43669876f7dbd754e157c69c89ebf3eb"
BOT_TOKEN = "7534650093:AAHs6cD3AoPT5jkg2ugoP_XxcvPyPuuLBk4"

# Initialize the bot
app = Client("combo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Constants
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB in bytes
CHUNK_SIZE = 4096  # Read file in chunks to handle large files

# Global dictionary to track ongoing processes and allow cancellation
active_processes = {}

# Helper function to validate email format
def is_valid_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None

# Helper function to validate domain format
def is_valid_domain(domain: str) -> bool:
    domain = domain.strip().lower()
    if not domain.startswith(('http://', 'https://')):
        domain = 'http://' + domain
    try:
        from urllib.parse import urlparse
        parsed = urlparse(domain)
        if not parsed.netloc:
            return False
        return True
    except:
        return False

# Extract domain from URL
def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url if '://' in url else 'http://' + url)
    return parsed.netloc.lower()

# Process file to extract combos
async def process_file(
    file_path: str, 
    target_domain: Optional[str] = None, 
    message: Message = None,
    process_id: str = None
) -> Tuple[int, str]:
    total_lines = 0
    extracted = 0
    duplicates = 0
    output_lines = set()
    last_update = time.time()
    
    # Count total lines first (for progress)
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            total_lines += 1
    
    if total_lines == 0:
        return 0, "File is empty or could not be read properly."
    
    # Read file and process lines
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        line_number = 0
        for line in f:
            line_number += 1
            
            # Check if process was cancelled
            if process_id in active_processes and active_processes[process_id].get('cancelled'):
                return 0, "Process cancelled by user."
            
            # Update progress every 0.5 seconds or every 1000 lines
            current_time = time.time()
            if current_time - last_update > 0.5 or line_number % 1000 == 0:
                last_update = current_time
                if message and process_id:
                    try:
                        progress = (line_number / total_lines) * 100
                        await message.edit_text(
                            f"Processing... {progress:.1f}%\n"
                            f"Lines processed: {line_number}/{total_lines}\n"
                            f"Extracted: {extracted}\n"
                            f"Duplicates: {duplicates}",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("Cancel", callback_data=f"cancel_{process_id}")]
                            ])
                        )
                    except RPCError:
                        pass
            
            # Skip empty lines
            line = line.strip()
            if not line:
                continue
            
            # Try to find email:password pattern
            parts = line.split(':', 1)
            if len(parts) != 2:
                continue
            
            email, password = parts[0].strip(), parts[1].strip()
            
            # Validate email format
            if not is_valid_email(email):
                continue
            
            # Check for target domain if specified
            if target_domain:
                _, email_domain = email.rsplit('@', 1)
                if email_domain.lower() != target_domain.lower():
                    continue
            
            # Add to output if not duplicate
            combo = f"{email}:{password}"
            if combo not in output_lines:
                output_lines.add(combo)
                extracted += 1
            else:
                duplicates += 1
    
    return extracted, output_lines

# Save combos to file
def save_combos(combos: set, filename: str) -> str:
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(combos))
    return filename

# Command handlers
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    welcome_msg = (
        "👋 Welcome to the Combo Generator Bot!\n\n"
        "📁 Send me a .txt file containing email:password combinations "
        "and reply to it with /combo command.\n\n"
        "I can extract targeted combos (for a specific domain) "
        "or mixed combos (all valid email:password pairs).\n\n"
        "⚠️ Note: Files larger than 200MB will be rejected."
    )
    await message.reply_text(welcome_msg)

@app.on_message(filters.command("combo") & filters.reply)
async def combo_command(client: Client, message: Message):
    replied = message.reply_to_message
    
    # Check if the replied message has a document
    if not replied.document or not replied.document.file_name.endswith('.txt'):
        await message.reply_text("Please reply to a .txt file with the /combo command.")
        return
    
    # Check file size
    file_size = replied.document.file_size
    if file_size > MAX_FILE_SIZE:
        await message.reply_text(f"File is too large! Maximum allowed size is 200MB. Your file: {file_size/1024/1024:.1f}MB")
        return
    
    # Create buttons for targeted or mixed
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Targeted", callback_data="targeted")],
        [InlineKeyboardButton("Mixed", callback_data="mixed")]
    ])
    
    await message.reply_text(
        "Choose the type of combo you want to generate:",
        reply_markup=keyboard
    )

# Callback query handler
@app.on_callback_query()
async def callback_handler(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    message = callback_query.message
    chat_id = callback_query.message.chat.id
    
    if data in ["targeted", "mixed"]:
        # Store the choice and ask for domain if targeted
        if data == "targeted":
            await callback_query.edit_message_text(
                "Please send the target domain (e.g., example.com):"
            )
            # Store that we're waiting for domain input
            active_processes[f"waiting_domain_{chat_id}"] = {
                "message_id": message.id,
                "file_message_id": message.reply_to_message.id,
                "type": "targeted"
            }
        else:
            # For mixed, proceed directly
            file_message_id = message.reply_to_message.id
            file_message = await client.get_messages(chat_id, file_message_id)
            
            # Generate a unique process ID
            process_id = f"process_{chat_id}_{int(time.time())}"
            active_processes[process_id] = {
                "cancelled": False,
                "chat_id": chat_id,
                "message_id": message.id
            }
            
            # Download the file
            try:
                await callback_query.edit_message_text("Downloading file...")
                file_path = await file_message.download()
                
                # Process the file
                status_msg = await callback_query.edit_message_text(
                    "Processing file... 0.0%\nLines processed: 0\nExtracted: 0\nDuplicates: 0",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("Cancel", callback_data=f"cancel_{process_id}")]
                    ])
                )
                
                extracted, output_lines = await process_file(
                    file_path, 
                    target_domain=None,  # Mixed mode
                    message=status_msg,
                    process_id=process_id
                )
                
                if isinstance(output_lines, str):  # Error message
                    await callback_query.edit_message_text(output_lines)
                else:
                    if not output_lines:
                        await callback_query.edit_message_text("No valid email:password combinations found.")
                    else:
                        # Save combos to file
                        output_filename = f"mixed_combos_{int(time.time())}.txt"
                        save_combos(output_lines, output_filename)
                        
                        # Send the file
                        await client.send_document(
                            chat_id,
                            output_filename,
                            caption=f"✅ Done! Found {extracted} unique combos."
                        )
                        
                        # Clean up
                        os.remove(output_filename)
                
                # Clean up
                os.remove(file_path)
                
            except Exception as e:
                await callback_query.edit_message_text(f"An error occurred: {str(e)}")
            finally:
                # Remove process from tracking
                active_processes.pop(process_id, None)
    
    elif data.startswith("cancel_"):
        process_id = data.split("_", 1)[1]
        if process_id in active_processes:
            active_processes[process_id]['cancelled'] = True
            await callback_query.answer("Cancellation requested...")
        else:
            await callback_query.answer("Process not found or already completed.")
    
    elif data == "targeted":
        await callback_query.edit_message_text(
            "Please send the target domain (e.g., example.com):"
        )
        # Store that we're waiting for domain input
        active_processes[f"waiting_domain_{chat_id}"] = {
            "message_id": message.id,
            "file_message_id": message.reply_to_message.id,
            "type": "targeted"
        }

# Handle domain input for targeted mode
@app.on_message(filters.text & filters.command)
async def handle_domain_input(client: Client, message: Message):
    chat_id = message.chat.id
    waiting_key = f"waiting_domain_{chat_id}"
    
    if waiting_key in active_processes:
        domain_input = message.text.strip()
        
        if not is_valid_domain(domain_input):
            await message.reply_text("Invalid domain format. Please send a valid domain (e.g., example.com)")
            return
        
        # Extract just the domain part
        target_domain = extract_domain(domain_input)
        
        # Get the original file message
        process_info = active_processes.pop(waiting_key)
        file_message_id = process_info["file_message_id"]
        original_message_id = process_info["message_id"]
        
        try:
            file_message = await client.get_messages(chat_id, file_message_id)
            
            # Generate a unique process ID
            process_id = f"process_{chat_id}_{int(time.time())}"
            active_processes[process_id] = {
                "cancelled": False,
                "chat_id": chat_id,
                "message_id": original_message_id,
                "target_domain": target_domain
            }
            
            # Download the file
            status_msg = await client.edit_message_text(
                chat_id,
                original_message_id,
                "Downloading file..."
            )
            
            file_path = await file_message.download()
            
            # Process the file
            status_msg = await client.edit_message_text(
                chat_id,
                original_message_id,
                f"Processing file for {target_domain}... 0.0%\nLines processed: 0\nExtracted: 0\nDuplicates: 0",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancel", callback_data=f"cancel_{process_id}")]
                ])
            )
            
            extracted, output_lines = await process_file(
                file_path, 
                target_domain=target_domain,
                message=status_msg,
                process_id=process_id
            )
            
            if isinstance(output_lines, str):  # Error message
                await client.edit_message_text(
                    chat_id,
                    original_message_id,
                    output_lines
                )
            else:
                if not output_lines:
                    await client.edit_message_text(
                        chat_id,
                        original_message_id,
                        f"No valid email:password combinations found for {target_domain}."
                    )
                else:
                    # Save combos to file
                    output_filename = f"{target_domain.replace('.', '_')}_{int(time.time())}.txt"
                    save_combos(output_lines, output_filename)
                    
                    # Send the file
                    await client.send_document(
                        chat_id,
                        output_filename,
                        caption=f"✅ Done! Found {extracted} unique combos for {target_domain}."
                    )
                    
                    # Clean up
                    os.remove(output_filename)
            
            # Clean up
            os.remove(file_path)
            
        except Exception as e:
            await client.edit_message_text(
                chat_id,
                original_message_id,
                f"An error occurred: {str(e)}"
            )
        finally:
            # Remove process from tracking
            active_processes.pop(process_id, None)

# Start the bot
print("Bot is running...")
app.run()
