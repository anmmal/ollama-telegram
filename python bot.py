import requests
from telegram.ext import Updater, MessageHandler, Filters

TOKEN = "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE"

def reply(update, context):
    user_message = update.message.text
    print("📩 Message:", user_message)

    try:
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.1:8b",
                "prompt": user_message,
                "stream": False
            },
            timeout=120
        )

        answer = res.json().get("response", "ما قدرت أرد حالياً")
        update.message.reply_text(answer)

    except Exception as e:
        print("❌ Error:", e)
        update.message.reply_text("صار خطأ تقني")

def main():
    print("🤖 Bot started and listening...")
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, reply))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
