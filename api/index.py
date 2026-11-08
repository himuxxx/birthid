# api/index.py
import os
import re
import requests
from flask import Flask, request, jsonify
from core import fetch_record, get_ocr_keys, add_ocr_key

app = Flask(__name__)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
VERCEL_URL = os.environ.get('VERCEL_URL', 'https://your-vercel-app.vercel.app')
ADMIN_ID = str(os.environ.get('ADMIN_ID', '0')).strip()
DEVELOPER = "@shakib2016"

def tg_api(endpoint, payload):
    if not TELEGRAM_TOKEN: return None
    return requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}", json=payload, timeout=5)

def tg_send(chat_id, text, markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup: payload["reply_markup"] = markup
    tg_api("sendMessage", payload)

@app.route('/api/birth', methods=['POST'])
def api_birth():
    body = request.get_json(silent=True) or {}
    num = body.get('num', '').strip()
    dob = body.get('dob', '').strip()
    if not num or not dob: return jsonify({'success': False, 'error': 'Missing UBRN or DOB'}), 400
    return jsonify(fetch_record('birth', num, dob))

@app.route('/api/death', methods=['POST'])
def api_death():
    body = request.get_json(silent=True) or {}
    num = body.get('num', '').strip()
    dob = body.get('dob', '').strip()
    if not num or not dob: return jsonify({'success': False, 'error': 'Missing UDRN or DOB'}), 400
    return jsonify(fetch_record('death', num, dob))

@app.route('/api/bot', methods=['POST'])
def telegram_webhook():
    if not TELEGRAM_TOKEN: return 'No token configured', 200
    data = request.json
    
    if 'callback_query' in data:
        cb = data['callback_query']
        chat_id = cb['message']['chat']['id']
        user_id = str(cb['from']['id'])
        cb_id = cb['id']
        cb_data = cb.get('data')
        
        if cb_data == 'help':
            tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Help opened"})
            tg_send(chat_id, f"<b>📖 How to use:</b>\n\n1. Tap <b>Open Ultra UI</b> for the best experience.\n2. Or send your details in this format:\n<code>UBRN DOB</code>\nExample: <code>19901234567890123 2008-03-08</code>\n\n👨‍💻 <b>Developer:</b> {DEVELOPER}")
        elif cb_data == 'admin_keys' and user_id == ADMIN_ID:
            keys = get_ocr_keys()
            masked = [k[:4] + "..." + k[-4:] for k in keys]
            tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Keys loaded"})
            tg_send(chat_id, f"<b>🔑 Active OCR Keys ({len(keys)}):</b>\n" + "\n".join([f"<code>{m}</code>" for m in masked]) + "\n\nTo add a new key, send:\n<code>/addkey YOUR_NEW_KEY</code>")
        return 'ok', 200

    if 'message' not in data: return 'ok', 200
    msg = data['message']
    chat_id = msg['chat']['id']
    user_id = str(msg['from']['id'])
    text = msg.get('text', '').strip()

    if text == '/start':
        markup = {"inline_keyboard": []}
        markup["inline_keyboard"].append([{"text": "🚀 Open Ultra UI", "web_app": {"url": VERCEL_URL}}])
        markup["inline_keyboard"].append([{"text": "📖 How to use", "callback_data": "help"}])
        if user_id == ADMIN_ID:
            markup["inline_keyboard"].append([{"text": "⚙️ Admin Panel", "callback_data": "admin_keys"}])

        welcome = (
            "<b>👋 Welcome to BDRIS Verify Bot!</b>\n\n"
            "You can check Birth & Death registrations instantly.\n\n"
            "👉 Tap <b>Open Ultra UI</b> to use our interactive app, "
            "or just send the details in this chat like this:\n\n"
            "<code>19901234567890123 2008-03-08</code>\n"
            f"<i>(17-digit UBRN and Date of Birth separated by a space)</i>\n\n"
            f"👨‍💻 <b>Developer:</b> {DEVELOPER}"
        )
        tg_send(chat_id, welcome, markup)
        return 'ok', 200

    if user_id == ADMIN_ID and text.startswith('/addkey '):
        new_key = text.split(' ', 1)[1].strip()
        if len(new_key) > 5:
            keys = add_ocr_key(new_key)
            tg_send(chat_id, f"✅ <b>Key Added Successfully!</b>\nTotal active keys: {len(keys)}")
        else:
            tg_send(chat_id, "❌ Invalid key format.")
        return 'ok', 200

    match = re.search(r'\b(\d{17})\s+(\d{4}-\d{2}-\d{2})\b', text)
    if match:
        ubrn, dob = match.groups()
        tg_send(chat_id, f"⏳ <b>Verifying UBRN:</b> <code>{ubrn}</code>...\n<i>Bypassing captcha, please wait 5-10s.</i>")
        
        result = fetch_record('birth', ubrn, dob)
        if result.get('success'):
            d = result['data']
            reply = (
                "✅ <b>Record Found!</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Name:</b> {d.get('name_bn', '')} ({d.get('name_en', '')})\n"
                f"🆔 <b>UBRN:</b> <code>{d.get('birth_registration_number', '')}</code>\n"
                f"📅 <b>DOB:</b> {d.get('date_of_birth', '')}\n"
                f"👨‍👩‍👧 <b>Father:</b> {d.get('father_name_bn', '')}\n"
                f"👩‍👧 <b>Mother:</b> {d.get('mother_name_bn', '')}\n"
                f"📍 <b>Place:</b> {d.get('birth_place_bn', '')}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<i>Verified via BDRIS</i>"
            )
            tg_send(chat_id, reply)
        else:
            tg_send(chat_id, f"❌ <b>Failed:</b> {result.get('error', 'Unknown error')}\n<i>Try again later or use the UI.</i>")
    elif text and not text.startswith('/'):
        tg_send(chat_id, "⚠️ <b>Invalid format.</b> Please send it like this:\n<code>19901234567890123 2008-03-08</code>")

    return 'ok', 200
