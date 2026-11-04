"""Finds your Telegram chat id from the bot's recent messages and saves it to .env.
Send your bot any message first. The token is never printed."""
import re
from pathlib import Path

import requests
from dotenv import dotenv_values

env_path = Path(__file__).with_name(".env")
token = (dotenv_values(env_path).get("TELEGRAM_BOT_TOKEN") or "").strip()
if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", token):
    raise SystemExit("TELEGRAM_BOT_TOKEN in .env is missing or not in the right format.")

api = f"https://api.telegram.org/bot{token}"
me = requests.get(f"{api}/getMe", timeout=15).json()
if not me.get("ok"):
    raise SystemExit(f"Token rejected by Telegram: {me.get('description')}")
print(f"Bot OK: @{me['result']['username']}")

upd = requests.get(f"{api}/getUpdates", timeout=15).json()
chats = {}
for u in upd.get("result", []):
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") == "private":
        chats[chat["id"]] = chat.get("first_name") or chat.get("username") or ""
if not chats:
    raise SystemExit("No messages found. Open your bot in Telegram, press Start, send 'hi', then run again.")

chat_id, name = list(chats.items())[-1]
text = env_path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text)
env_path.write_text(text, encoding="utf-8")
print(f"Chat found: {name} -> saved TELEGRAM_CHAT_ID to .env")
