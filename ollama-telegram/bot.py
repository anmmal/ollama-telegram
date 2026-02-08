import requests
from telegram.ext import Updater, MessageHandler, Filters

SYSTEM_PROMPT = """
You are “A R K Customer Support Assistant” for A R K (Kuwait).

Language:
- Arabic (Kuwaiti/Gulf). English if user writes English.

Rules:
- Do not invent info.
- Be concise and professional.
"""

def reply(update, context):
    user_message = update.message.text

    prompt = f"""{SYSTEM_PROMPT}

Customer: {user_message}
Assistant:"""

    res = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "llama3.1:8b",
            "prompt": prompt,
            "stream": False
        },
        timeout=60
    )

    answer = res.json().get("response", "صار خطأ، حاول مرة ثانية.")
    update.message.reply_text(answer)

updater = Updater("8329761412:AAEnjD9P_JqNRNwyJ8esY1ETWSp5dpGxga4", use_context=True)
dp = updater.dispatcher

dp.add_handler(MessageHandler(Filters.text & ~Filters.command, reply))

updater.start_polling()
updater.idle()

