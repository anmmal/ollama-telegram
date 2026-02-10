import os
import json
import requests
from datetime import datetime
from telegram.ext import Updater, MessageHandler, Filters

# ========= Paths =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

UNANSWERED_LOG = os.path.join(LOG_DIR, "unanswered.txt")
FAQ_PATH = os.path.join(BASE_DIR, "faq.json")  # optional

# ========= Config =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

SYSTEM_PROMPT = """
You are "A R K Customer Support Assistant" for A R K (Kuwait).

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
5) If asked about something unknown, ask ONE short clarifying question or offer escalation.

KNOWN FACTS (ONLY THESE ARE CONFIRMED):
- Website: www.ark.com.kw
- Address: 33 Street, Building 367, Block 1, 70070 Rai, Kuwait
- Business: Specialty Coffee & Tea
- Divisions: Cafe, Roasters, Tea

COMPLAINT HANDLING:
- Apologize briefly.
- Confirm understanding.
- Ask for: name, phone number, order number (if any).
- Offer escalation to human support.

ENDING RULE:
Always end with ONE short helpful question.
Example:
"تحب أساعدك بشي ثاني؟"
""".strip()


import os
import re
import requests
from telegram.ext import Updater, MessageHandler, Filters

BASE_DIR = os.path.dirname(__file__)
FAQ_FILE = os.path.join(BASE_DIR, "faq.txt")
KB_FILE = os.path.join(BASE_DIR, "knowledge_base.txt")
PROMPT_FILE = os.path.join(BASE_DIR, "system_prompt.txt")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Accept either name (some scripts use TELEGRAM_TOKEN, some TELEGRAM_BOT_TOKEN)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default

SYSTEM_PROMPT = read_text(PROMPT_FILE, "")

def parse_faq(text: str):
    """
    faq.txt format:
    Q: ...
    A: ...
    (blank line)
    """
    items = []
    q = None
    a_lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("q:"):
            if q and a_lines:
                items.append((q, "\n".join(a_lines).strip()))
            q = line[2:].strip()
            a_lines = []
        elif line.lower().startswith("a:"):
            a_lines.append(line[2:].strip())
        else:
            if q is not None:
                # allow multi-line answers
                if line == "" and a_lines:
                    a_lines.append("")
                elif line != "":
                    a_lines.append(line)
    if q and a_lines:
        items.append((q, "\n".join(a_lines).strip()))
    return items

def score_match(query: str, candidate_q: str):
    # simple token overlap score
    q_tokens = set(re.findall(r"[\w\u0600-\u06FF]+", query.lower()))
    c_tokens = set(re.findall(r"[\w\u0600-\u06FF]+", candidate_q.lower()))
    if not q_tokens or not c_tokens:
        return 0
    return len(q_tokens & c_tokens) / max(1, len(q_tokens))

def find_faq_answer(user_message: str):
    faq_text = read_text(FAQ_FILE, "")
    items = parse_faq(faq_text)
    if not items:
        return None

    best = (0, None)
    for q, a in items:
        s = score_match(user_message, q)
        if s > best[0]:
            best = (s, a)

    # threshold (tune later)
    if best[0] >= 0.35:
        return best[1]
    return None

def find_kb_snippets(user_message: str, max_snips=4):
    kb = read_text(KB_FILE, "")
    if not kb:
        return []

    words = set(re.findall(r"[\w\u0600-\u06FF]+", user_message.lower()))
    paras = [p.strip() for p in kb.split("\n\n") if p.strip()]
    scored = []
    for p in paras:
        p_words = set(re.findall(r"[\w\u0600-\u06FF]+", p.lower()))
        overlap = len(words & p_words)
        if overlap > 0:
            scored.append((overlap, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_snips]]

def ollama_rewrite_with_sources(user_message: str, sources: list[str]):
    sources_block = "\n\n---\n".join(sources) if sources else ""
    prompt = f"""{SYSTEM_PROMPT}

SOURCES (only these are allowed):
{sources_block}

Customer message:
{user_message}

Write the best answer using ONLY SOURCES. If SOURCES do not contain the answer, say you do not have confirmed info and offer escalation.
"""

    res = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=90
    )
    data = res.json()
    return (data.get("response") or "").strip()

def reply(update, context):
    user_message = (update.message.text or "").strip()
    if not user_message:
        update.message.reply_text("هلا فيك! شنو حاب تستفسر عنه؟")
        return

    # 1) FAQ is truth source
    faq_answer = find_faq_answer(user_message)
    if faq_answer:
        update.message.reply_text(faq_answer + "\n\nتحب أساعدك بشي ثاني؟")
        return

    # 2) Otherwise, use KB snippets for rewrite only
    snippets = find_kb_snippets(user_message)
    if snippets:
        try:
            answer = ollama_rewrite_with_sources(user_message, snippets)
            if not answer:
                answer = "حاليا ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحول استفسارك للفريق المختص. تحب؟"
            update.message.reply_text(answer)
        except Exception:
            update.message.reply_text("صار عندي تعليق بسيط بالنظام. تقدر تعيد رسالتك؟")
        return

    # 3) No sources -> escalate
    update.message.reply_text(
        "حاليا ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحول استفسارك للفريق المختص.\n"
        "تقدر تعطيني اسمك ورقمك؟"
    )

def main():
    if not TELEGRAM_TOKEN:
        print("❌ Missing TELEGRAM_TOKEN. Put it in .env as TELEGRAM_TOKEN=...")
        return

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, reply))

    print("✅ A R K Telegram Bot is running...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
