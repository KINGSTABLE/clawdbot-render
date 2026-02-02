import os
import threading
import time
from flask import Flask
import telebot
from telebot import apihelper
from gradio_client import Client, handle_file

# ==========================================
# CONFIGURATION
# ==========================================

# 1. Get the Telegram Token
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# 2. Get the Hugging Face Token (REQUIRED for private spaces)
HF_TOKEN = os.environ.get("HF_TOKEN") 

# 3. Your Gradio API URL
GRADIO_API_URL = "https://executor-tyrant-framework-clawdbot-dev.hf.space/"

# Initialize Flask
app = Flask(__name__)

# Initialize Bot
if not BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
    bot = None
else:
    bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# GLOBAL CLIENT INITIALIZATION
# ==========================================
gradio_client = None

def connect_to_gradio():
    """Attempts to connect/reconnect to the Gradio backend."""
    global gradio_client
    try:
        print(f"🔄 Attempting to connect to Gradio API at {GRADIO_API_URL}...")
        
        if HF_TOKEN:
            print("🔑 Using HF_TOKEN for authentication.")
            gradio_client = Client(GRADIO_API_URL, hf_token=HF_TOKEN)
        else:
            print("⚠️ No HF_TOKEN found. Trying public access.")
            gradio_client = Client(GRADIO_API_URL)
            
        print("✅ Gradio Client Connected successfully.")
        return True
    except Exception as e:
        print(f"❌ Error connecting to Gradio API: {e}")
        print("👉 HINT: If the error is 'Not Found', your HF Space might be SLEEPING. Go to the URL and restart it.")
        gradio_client = None
        return False

# Initial connection attempt
connect_to_gradio()

# In-memory storage for user history
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
    if not history:
        return "No response received."
    try:
        last_turn = history[-1]
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

if bot:
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        user_id = message.from_user.id
        user_sessions[user_id] = {"history": [], "pending_proposals": []}
        
        status = "✅ Online" if gradio_client else "❌ Offline (Backend unreachable)"
        
        bot.reply_to(message, 
            f"🤖 **Executor Tyrant Bot**\nStatus: {status}\n\n"
            "/clear - Reset memory\n"
            "/approve - Execute proposals",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['clear'])
    def clear_history(message):
        user_id = message.from_user.id
        user_sessions[user_id] = {"history": [], "pending_proposals": []}
        
        global gradio_client
        if gradio_client is None:
            if not connect_to_gradio():
                 bot.reply_to(message, "⚠️ Backend unavailable. Local memory cleared.")
                 return

        try:
            gradio_client.predict(api_name="/clear_all_proposals")
            bot.reply_to(message, "✅ History cleared.")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Local clear only. Server error: {str(e)}")

    @bot.message_handler(commands=['approve'])
    def approve_proposals(message):
        user_id = message.from_user.id
        session = get_session(user_id)
        
        if not session['pending_proposals']:
            bot.reply_to(message, "No pending proposals.")
            return

        global gradio_client
        if gradio_client is None:
            if not connect_to_gradio():
                bot.reply_to(message, "❌ Backend offline. Wake up the HF Space.")
                return

        bot.reply_to(message, "⏳ Executing...")
        try:
            result = gradio_client.predict(
                selected_ids=session['pending_proposals'],
                history=session['history'],
                api_name="/execute_approved_proposals"
            )
            session['history'] = result[2]
            session['pending_proposals'] = result[1]
            response_text = format_gradio_response(result[2])
            bot.reply_to(message, f"✅ **Done**:\n\n{response_text}", parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {str(e)}")

    @bot.message_handler(func=lambda message: True)
    def handle_message(message):
        user_id = message.from_user.id
        session = get_session(user_id)

        global gradio_client
        if gradio_client is None:
            status_msg = bot.reply_to(message, "⚠️ Connecting to backend...")
            if not connect_to_gradio():
                bot.edit_message_text(
                    "❌ Error: Backend is unreachable (404/Offline).\n"
                    "Please check if the Hugging Face Space is 'Sleeping' and restart it.", 
                    message.chat.id, status_msg.message_id
                )
                return
            try: bot.delete_message(message.chat.id, status_msg.message_id)
            except: pass

        status_msg = bot.reply_to(message, "Thinking...")

        try:
            result = gradio_client.predict(
                message=message.text,
                history=session['history'],
                uploaded_file=[], 
                api_name="/agent_loop"
            )
            
            session['history'] = result[0]
            session['pending_proposals'] = result[2]
            
            response_text = format_gradio_response(result[0])
            
            try: bot.delete_message(message.chat.id, status_msg.message_id)
            except: pass
            
            bot.reply_to(message, response_text)

            if session['pending_proposals']:
                p_text = "\n".join([str(p) for p in session['pending_proposals']])
                bot.send_message(message.chat.id, f"⚠️ **Approve?**\n`{p_text}`\n\n/approve", parse_mode="Markdown")

        except Exception as e:
            err_str = str(e)
            if "Not Found" in err_str or "Connection" in err_str:
                gradio_client = None # Force reconnect next time
                bot.edit_message_text("❌ Backend connection failed (Space might be sleeping).", message.chat.id, status_msg.message_id)
            else:
                bot.edit_message_text(f"❌ Error: {err_str}", message.chat.id, status_msg.message_id)

# ==========================================
# FLASK & BOT EXECUTION
# ==========================================

@app.route('/')
def index():
    status = "Connected" if gradio_client else "Disconnected"
    return f"Bot Running. Backend: {status}", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def run_bot_safely():
    """Runs the bot with auto-restart on conflict."""
    if not bot: return

    # 1. Clean up any previous webhooks or stuck updates
    try:
        print("🧹 Clearing previous webhooks...")
        bot.delete_webhook(drop_pending_updates=True)
        time.sleep(1)
    except Exception as e:
        print(f"Warning clearing webhook: {e}")

    print("🚀 Bot polling started...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"⚠️ Polling Error: {e}")
            time.sleep(5)  # Wait before retrying to let conflicts resolve

if __name__ == "__main__":
    # Start Flask
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Start Bot
    run_bot_safely()
