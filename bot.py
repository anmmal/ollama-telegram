import os
import requests
from telegram.ext import Updater, MessageHandler, Filters

import os
import json
from datetime import datetime

UNANSWERED_LOG = os.path.join(os.path.dirname(__file__), "logs", "unanswered.txt")

def log_unanswered(update, user_message: str):
    os.makedirs(os.path.dirname(UNANSWERED_LOG), exist_ok=True)

    user = update.effective_user
    chat = update.effective_chat

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_id = user.id if user else ""
    username = f"@{user.username}" if user and user.username else ""
    full_name = (user.full_name if user else "").strip()
    chat_id = chat.id if chat else ""

    line = f"[{ts}] chat_id={chat_id} user_id={user_id} {username} name='{full_name}' msg='{user_message}'\n"
    with open(UNANSWERED_LOG, "a", encoding="utf-8") as f:
        f.write(line)
FAQ_PATH = os.path.join(os.path.dirname(__file__), "faq.json")

def load_faq():
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["items"]

FAQ_ITEMS = load_faq()

def faq_lookup(user_message: str):
    msg = (user_message or "").lower().strip()
    for item in FAQ_ITEMS:
        for key in item["q"]:
            if key.lower() in msg:
                return item["a"]
    return None

# ======================
# SYSTEM PROMPT
# ======================

SYSTEM_PROMPT = """
You are “A R K Customer Support Assistant” for A R K (Kuwait).

أنت مساعد خدمة عملاء لشركة A R K في الكويت.

أسلوبك:
- كويتي واضح
- مختصر ومهني
- بدون سوالف أو كلام عام

قواعد صارمة:
- لا تفترض أي معلومة
- لا تجاوب إلا عن A R K فقط
- إذا المعلومة غير مؤكدة قل:
  "حالياً ما عندي معلومة مؤكدة، وأقدر أحوّل استفسارك للفريق المختص."

مسموح تجاوب فقط عن:
- القهوة والشاي
- موقع A R K
- طبيعة النشاط

ممنوع:
- ساعات العمل
- الأسعار
- التوفر
- التوصيل
إلا إذا كانت معلومة مؤكدة ومذكورة صراحة.

نهاية كل رد:
اسأل سؤال واحد بسيط للمساعدة.
مثال:
"تحب أساعدك بشي ثاني؟"

def reply(update, context):
    user_message = update.message.text.strip()

    prompt = f"""{SYSTEM_PROMPT}

سؤال العميل:
{user_message}

الرد بأسلوب كويتي مختصر:
"""

PRIMARY GOAL:
Help customers accurately with information related to A R K only.

LANGUAGE & TONE:
- Default language: Arabic (Kuwaiti/Gulf).
- If the customer writes in English, reply in English.
- Friendly, professional, short, and clear.
- Do NOT be conversational or generic.

STRICT BUSINESS RULES (VERY IMPORTANT):
1) NEVER invent or assume any information.
2) If you do not have confirmed information, say clearly:
   "حالياً ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحوّل استفسارك للفريق المختص."
3) DO NOT guess:
   - Opening hours
   - Prices
   - Availability
   - Branch timings
   - Delivery coverage
4) Only answer using information explicitly provided.
5) If asked about something unknown, ASK ONE short clarifying question OR offer escalation.

KNOWN FACTS (ONLY THESE ARE CONFIRMED):
- Website: www.ark.com.kw
- Address: 33 Street, Building 367, Block 1, 70070 Rai, Kuwait
- Business: Specialty Coffee & Tea
- Divisions: Café, Roasters, Tea

COMPLAINT HANDLING:
- Apologize briefly.
- Confirm understanding.
- Ask for: name, phone number, order number (if any).
- Offer escalation to human support.

ENDING RULE:
Always end with ONE short helpful question.
Example:
"تحب أساعدك بشي ثاني؟"
"""

# ======================
# TELEGRAM REPLY HANDLER
# ======================

def reply(update, context):
    user_message = update.message.text

    prompt = f"""{SYSTEM_PROMPT}

Customer message:
{user_message}

Assistant reply:
"""

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3.1:8b",
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )

        answer = response.json().get("response", "صار خطأ، حاول مرة ثانية.")
        update.message.reply_text(answer)

    except Exception:
        update.message.reply_text(
            "صار خلل تقني بسيط، تقدر تعطيني لحظة أو أحوّل طلبك للفريق المختص؟"
        )

# ======================
# BOT STARTUP
# ======================

def main():
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN غير موجود")
        return

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, reply))

    print("✅ A R K Telegram Bot is running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
