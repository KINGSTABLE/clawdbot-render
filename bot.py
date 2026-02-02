import os
import threading
import time
from flask import Flask
import telebot
from gradio_client import Client, handle_file

# ==========================================
# CONFIGURATION
# ==========================================

# 1. Get the Telegram Token from Environment Variables (set this in Render)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# 2. Your Gradio API URL
GRADIO_API_URL = "https://executor-tyrant-framework-clawdbot-dev.hf.space/"

# Initialize Flask (Required for Render to keep the app alive)
app = Flask(__name__)

# Initialize Bot
if not BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
    # We allow the script to run so Render doesn't crash immediately, 
    # but the bot won't work without the token.
else:
    bot = telebot.TeleBot(BOT_TOKEN)

# Initialize Gradio Client
try:
    print("Initializing Gradio Client...")
    gradio_client = Client(GRADIO_API_URL)
    print("Gradio Client Connected.")
except Exception as e:
    print(f"Error connecting to Gradio API: {e}")

# In-memory storage for user history (Note: This resets if the Render dyno restarts)
user_sessions = {}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "history": [],
            "pending_proposals": []
        }
    return user_sessions[user_id]

def format_gradio_response(history):
    """
    Extracts the latest text response from the complex Gradio history structure.
    """
    if not history:
        return "No response received."
    
    try:
        # Get the last message
        last_turn = history[-1]
        
        # Check if it is from the assistant (bot)
        if last_turn.get('role') == 'assistant':
            content_list = last_turn.get('content', [])
            message_text = ""
            for item in content_list:
                if isinstance(item, dict) and item.get('type') == 'text':
                    message_text += item.get('text', '') + "\n"
            return message_text.strip()
        
        return "Thinking..."
    except Exception as e:
        return f"Error parsing response: {str(e)}"

# ==========================================
# TELEGRAM BOT HANDLERS
# ==========================================

if BOT_TOKEN:
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        user_id = message.from_user.id
        # Reset session on start
        user_sessions[user_id] = {"history": [], "pending_proposals": []}
        
        welcome_text = (
            "🤖 **Executor Tyrant Bot Ready**\n\n"
            "I am connected to the Clawdbot framework.\n"
            "Just type a message to interact.\n\n"
            "**Commands:**\n"
            "/clear - Clear conversation history\n"
            "/approve - Execute pending proposals (if any)"
        )
        bot.reply_to(message, welcome_text, parse_mode="Markdown")

    @bot.message_handler(commands=['clear'])
    def clear_history(message):
        user_id = message.from_user.id
        
        # clear local session
        user_sessions[user_id] = {"history": [], "pending_proposals": []}
        
        # Call API to clear proposals
        try:
            gradio_client.predict(api_name="/clear_all_proposals")
        except:
            pass
            
        bot.reply_to(message, "Conversation history and pending proposals cleared.")

    @bot.message_handler(commands=['approve'])
    def approve_proposals(message):
        user_id = message.from_user.id
        session = get_session(user_id)
        
        if not session['pending_proposals']:
            bot.reply_to(message, "No pending proposals to approve.")
            return

        bot.reply_to(message, "⏳ Executing approved proposals...")

        try:
            # Call /execute_approved_proposals
            # The API expects selected_ids (list) and history
            result = gradio_client.predict(
                selected_ids=session['pending_proposals'], # We approve all pending
                history=session['history'],
                api_name="/execute_approved_proposals"
            )
            
            # Update history with the result
            # Result tuple: [0] str (val_27), [1] list (pending), [2] history
            new_history = result[2]
            session['history'] = new_history
            session['pending_proposals'] = result[1] # Should be empty now

            # Send the update from the agent
            response_text = format_gradio_response(new_history)
            bot.reply_to(message, f"✅ **Execution Complete**:\n\n{response_text}", parse_mode="Markdown")
            
        except Exception as e:
            bot.reply_to(message, f"❌ Error executing proposals: {str(e)}")

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        user_id = message.from_user.id
        user_input = message.text
        session = get_session(user_id)

        # Notify user we are processing
        status_msg = bot.reply_to(message, "Thinking...")

        try:
            # 1. Call /agent_loop
            # Parameters: message (str), history (list), uploaded_file (list)
            result = gradio_client.predict(
                message=user_input,
                history=session['history'],
                uploaded_file=[], # Required parameter, passing empty list
                api_name="/agent_loop"
            )

            # 2. Parse Results
            # [0] history (list of dicts)
            # [1] value_18 (textbox - usually empty after send)
            # [2] Pending Proposals (list)
            
            new_history = result[0]
            pending_proposals = result[2]

            # Update session
            session['history'] = new_history
            session['pending_proposals'] = pending_proposals

            # 3. Send Reply to Telegram
            response_text = format_gradio_response(new_history)
            
            # Delete the "Thinking..." message and send the actual response
            try:
                bot.delete_message(message.chat.id, status_msg.message_id)
            except:
                pass # If delete fails, just send the new one
            
            bot.reply_to(message, response_text)

            # 4. Check for Proposals
            if pending_proposals:
                proposal_text = "\n".join([str(p) for p in pending_proposals])
                bot.send_message(
                    message.chat.id, 
                    f"⚠️ **Pending Proposals Detected:**\n`{proposal_text}`\n\nType /approve to execute them.",
                    parse_mode="Markdown"
                )

        except Exception as e:
            bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, status_msg.message_id)

# ==========================================
# FLASK SERVER (FOR RENDER KEEPALIVE)
# ==========================================

@app.route('/')
def index():
    return "Bot is running!", 200

def run_flask():
    # Render assigns the PORT environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    if BOT_TOKEN:
        print("Bot polling started...")
        bot.infinity_polling()
    else:
        print("Bot token missing, skipping polling.")

if __name__ == "__main__":
    # Start Flask in a separate thread to satisfy Render's port requirement
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Start the Bot in the main thread (or separate, but here we separate Flask)
    run_bot()