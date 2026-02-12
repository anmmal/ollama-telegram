#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
A R K Telegram Bot — Enterprise Support Stable V1
- FAQ truth source
- KB snippets + LLM rewrite (ONLY from sources)
- Escalation + logging + analytics
- Health endpoint (local) + Ollama resilience
- Single instance lock (prevents multiple polling instances / 409 conflict)
"""

import os
import sys
import re
import json
import time
import uuid
import fcntl
import queue
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram.ext import Updater, MessageHandler, Filters


# =========================
# Paths / Directories
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

EVENTS_LOG = os.path.join(LOG_DIR, "events.jsonl")
MESSAGES_LOG = os.path.join(LOG_DIR, "messages.jsonl")
UNANSWERED_LOG = os.path.join(LOG_DIR, "unanswered.txt")

FAQ_FILE = os.path.join(BASE_DIR, "faq.txt")                 # Q:/A: format
KB_FILE = os.path.join(BASE_DIR, "knowledge_base.txt")       # paragraphs separated by blank line
PROMPT_FILE = os.path.join(BASE_DIR, "system_prompt.txt")    # optional override


# =========================
# Config from env
# =========================
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

# Health endpoint
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "18080"))  # local only
HEALTH_TOKEN = os.getenv("HEALTH_TOKEN", "").strip()  # required for /health?token=...
HEALTH_BIND = os.getenv("HEALTH_BIND", "127.0.0.1").strip()

# Lock
LOCK_PATH = os.getenv("BOT_LOCK_PATH", "/tmp/com.ark.ollama-telegram.lock").strip()

# Tuning
FAQ_THRESHOLD = float(os.getenv("FAQ_THRESHOLD", "0.35"))
KB_MAX_SNIPS = int(os.getenv("KB_MAX_SNIPS", "4"))

# Ollama resilience
OLLAMA_CONNECT_TIMEOUT = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "5"))
OLLAMA_READ_TIMEOUT = float(os.getenv("OLLAMA_READ_TIMEOUT", "60"))
OLLAMA_FAILS_BEFORE_COOLDOWN = int(os.getenv("OLLAMA_FAILS_BEFORE_COOLDOWN", "3"))
OLLAMA_COOLDOWN_SECONDS = int(os.getenv("OLLAMA_COOLDOWN_SECONDS", "120"))


# =========================
# System Prompt
# =========================
DEFAULT_SYSTEM_PROMPT = """
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
4) Only answer using information explicitly provided (FAQ / KB sources).
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


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


SYSTEM_PROMPT = read_text(PROMPT_FILE, DEFAULT_SYSTEM_PROMPT)


# =========================
# Logging (JSONL)
# =========================
_log_q: "queue.Queue[dict]" = queue.Queue(maxsize=5000)
_stop_logging = threading.Event()


def _jsonl_write(path: str, obj: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _logger_worker():
    while not _stop_logging.is_set():
        try:
            item = _log_q.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            # route
            if item.get("kind") == "message":
                _jsonl_write(MESSAGES_LOG, item)
            else:
                _jsonl_write(EVENTS_LOG, item)
        except Exception:
            # last resort: ignore logging failures
            pass
        finally:
            _log_q.task_done()


def log_event(data: dict):
    data = dict(data)
    data.setdefault("ts", datetime.utcnow().isoformat() + "Z")
    data.setdefault("kind", "event")
    try:
        _log_q.put_nowait(data)
    except queue.Full:
        pass


def log_message(data: dict):
    data = dict(data)
    data.setdefault("ts", datetime.utcnow().isoformat() + "Z")
    data.setdefault("kind", "message")
    try:
        _log_q.put_nowait(data)
    except queue.Full:
        pass


# =========================
# Single-instance lock
# =========================
_lock_fd = None


def acquire_lock_or_exit():
    global _lock_fd
    _lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("⚠️ Bot already running (lock exists). Exiting.", flush=True)
        sys.exit(0)

    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
    return _lock_fd


# =========================
# FAQ / KB
# =========================
def parse_faq(text: str):
    """
    faq.txt format:
      Q: question...
      A: answer line 1...
         answer line 2...
      (blank line)
    """
    items = []
    q = None
    a_lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower().startswith("q:"):
            if q and a_lines:
                items.append((q, "\n".join(a_lines).strip()))
            q = line[2:].strip()
            a_lines = []
        elif line.lower().startswith("a:"):
            a_lines.append(line[2:].strip())
        else:
            if q is not None:
                if line == "" and a_lines:
                    a_lines.append("")
                elif line != "":
                    a_lines.append(line)

    if q and a_lines:
        items.append((q, "\n".join(a_lines).strip()))
    return items


def _tokens(s: str):
    return set(re.findall(r"[\w\u0600-\u06FF]+", (s or "").lower()))


def score_match(query: str, candidate_q: str):
    q_tokens = _tokens(query)
    c_tokens = _tokens(candidate_q)
    if not q_tokens or not c_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / max(1, len(q_tokens))


def find_faq_answer(user_message: str):
    faq_text = read_text(FAQ_FILE, "")
    items = parse_faq(faq_text)
    if not items:
        return None

    best_score = 0.0
    best_ans = None
    for q, a in items:
        s = score_match(user_message, q)
        if s > best_score:
            best_score = s
            best_ans = a

    if best_score >= FAQ_THRESHOLD:
        return best_ans
    return None


def find_kb_snippets(user_message: str, max_snips=KB_MAX_SNIPS):
    kb = read_text(KB_FILE, "")
    if not kb:
        return []
    words = _tokens(user_message)
    paras = [p.strip() for p in kb.split("\n\n") if p.strip()]
    scored = []
    for p in paras:
        p_words = _tokens(p)
        overlap = len(words & p_words)
        if overlap > 0:
            scored.append((overlap, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_snips]]


# =========================
# Ollama resilience
# =========================
_ollama_fail_count = 0
_ollama_disabled_until = 0.0
_last_ollama_ok = True


def ollama_rewrite_with_sources(user_message: str, sources: list[str], trace_id: str):
    global _ollama_fail_count, _ollama_disabled_until, _last_ollama_ok

    now = time.time()
    if now < _ollama_disabled_until:
        _last_ollama_ok = False
        return ""

    sources_block = "\n\n---\n".join(sources) if sources else ""
    prompt = f"""{SYSTEM_PROMPT}

SOURCES (only these are allowed):
{sources_block}

Customer message:
{user_message}

Write the best answer using ONLY SOURCES.
If SOURCES do not contain the answer, say:
"حالياً ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحوّل استفسارك للفريق المختص."
""".strip()

    try:
        res = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_READ_TIMEOUT),
        )
        res.raise_for_status()
        data = res.json()
        out = (data.get("response") or "").strip()

        _ollama_fail_count = 0
        _last_ollama_ok = True

        log_event({
            "type": "ollama_ok",
            "trace_id": trace_id,
            "model": OLLAMA_MODEL,
            "len_out": len(out),
        })

        return out

    except Exception as e:
        _ollama_fail_count += 1
        _last_ollama_ok = False

        if _ollama_fail_count >= OLLAMA_FAILS_BEFORE_COOLDOWN:
            _ollama_disabled_until = time.time() + OLLAMA_COOLDOWN_SECONDS

        log_event({
            "type": "ollama_error",
            "trace_id": trace_id,
            "error": str(e),
            "fail_count": _ollama_fail_count,
            "disabled_until": _ollama_disabled_until,
        })
        return ""


# =========================
# Escalation / Unanswered
# =========================
def log_unanswered(user_message: str, meta: dict):
    try:
        with open(UNANSWERED_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.utcnow().isoformat()}Z] {json.dumps(meta, ensure_ascii=False)} :: {user_message}\n")
    except Exception:
        pass


ESCALATION_MSG_AR = (
    "حالياً ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحوّل استفسارك للفريق المختص.\n"
    "تقدر تعطيني اسمك ورقمك؟"
)

EMPTY_MSG_AR = "هلا فيك! شنو حاب تستفسر عنه؟"


# =========================
# Health Endpoint (local)
# =========================
class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Basic token auth
        # /health?token=XXXX
        try:
            path = self.path or ""
            token = ""
            if "?" in path:
                qs = path.split("?", 1)[1]
                for part in qs.split("&"):
                    if part.startswith("token="):
                        token = part.split("=", 1)[1]
                        break

            if not HEALTH_TOKEN or token != HEALTH_TOKEN:
                self._send(401, {"ok": False, "error": "unauthorized"})
                return

            if path.startswith("/health"):
                self._send(200, {
                    "ok": True,
                    "pid": os.getpid(),
                    "uptime_sec": int(time.time() - START_TIME),
                    "ollama": {
                        "url": OLLAMA_URL,
                        "model": OLLAMA_MODEL,
                        "last_ok": _last_ollama_ok,
                        "fail_count": _ollama_fail_count,
                        "disabled_until": _ollama_disabled_until,
                    }
                })
                return

            self._send(404, {"ok": False, "error": "not_found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, format, *args):
        # silence default http server logs
        return


def start_health_server():
    # Only start if token provided (so it's not open by accident)
    if not HEALTH_TOKEN:
        log_event({"type": "health_disabled", "reason": "HEALTH_TOKEN missing"})
        return None

    def _run():
        httpd = HTTPServer((HEALTH_BIND, HEALTH_PORT), HealthHandler)
        log_event({"type": "health_started", "bind": HEALTH_BIND, "port": HEALTH_PORT})
        httpd.serve_forever()

    t = threading.Thread(target=_run, name="health-server", daemon=True)
    t.start()
    return t


# =========================
# Reply Logic
# =========================
def reply(update, context):
    trace_id = str(uuid.uuid4())
    msg = update.message
    if not msg:
        return

    user_message = (msg.text or "").strip()
    user_id = getattr(msg.from_user, "id", None)
    username = getattr(msg.from_user, "username", None)
    chat_id = getattr(msg.chat, "id", None)
    msg_id = getattr(msg, "message_id", None)

    log_message({
        "trace_id": trace_id,
        "type": "incoming",
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": user_message,
    })

    if not user_message:
        msg.reply_text(EMPTY_MSG_AR)
        return

    # 1) FAQ is truth source
    faq_answer = find_faq_answer(user_message)
    if faq_answer:
        out = faq_answer.strip() + "\n\nتحب أساعدك بشي ثاني؟"
        msg.reply_text(out)
        log_message({
            "trace_id": trace_id,
            "type": "outgoing",
            "mode": "faq",
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": out,
        })
        return

    # 2) KB snippets -> LLM rewrite only from sources
    snippets = find_kb_snippets(user_message)
    if snippets:
        answer = ollama_rewrite_with_sources(user_message, snippets, trace_id=trace_id)
        if not answer:
            answer = "حاليا ما عندي معلومة مؤكدة بهالموضوع، وأقدر أحول استفسارك للفريق المختص. تحب؟"
        msg.reply_text(answer)

        log_message({
            "trace_id": trace_id,
            "type": "outgoing",
            "mode": "kb_llm",
            "snips": len(snippets),
            "ollama_ok": bool(answer and "ما عندي معلومة مؤكدة" not in answer),
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": answer,
        })
        return

    # 3) No sources -> escalate
    msg.reply_text(ESCALATION_MSG_AR)
    log_unanswered(user_message, {
        "trace_id": trace_id,
        "user_id": user_id,
        "username": username,
        "chat_id": chat_id,
        "message_id": msg_id,
        "reason": "no_sources",
    })

    log_message({
        "trace_id": trace_id,
        "type": "outgoing",
        "mode": "escalate",
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": ESCALATION_MSG_AR,
    })


# =========================
# Main
# =========================
START_TIME = time.time()


def main():
    # logging thread
    tlog = threading.Thread(target=_logger_worker, name="jsonl-logger", daemon=True)
    tlog.start()

    # lock
    acquire_lock_or_exit()

    print(f"✅ PID={os.getpid()} TELEGRAM_TOKEN_len={len(TELEGRAM_TOKEN)}", flush=True)
    log_event({"type": "boot", "pid": os.getpid(), "token_len": len(TELEGRAM_TOKEN)})

    if not TELEGRAM_TOKEN:
        print("❌ Missing TELEGRAM_TOKEN. Put it in .env as TELEGRAM_TOKEN=...", flush=True)
        log_event({"type": "fatal", "reason": "missing_telegram_token"})
        sys.exit(1)

    # health endpoint
    start_health_server()

    # telegram
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, reply))

    print("✅ A R K Telegram Bot is running...", flush=True)
    log_event({"type": "telegram_start_polling"})

    try:
        updater.start_polling()
        updater.idle()
    except KeyboardInterrupt:
        log_event({"type": "shutdown", "reason": "keyboard_interrupt"})
    except Exception as e:
        log_event({"type": "fatal", "reason": str(e)})
        raise
    finally:
        _stop_logging.set()
        # drain queue quickly
        try:
            _log_q.join()
        except Exception:
            pass


if __name__ == "__main__":
    main()
   