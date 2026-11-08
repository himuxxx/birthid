# api/index.py
import io
import os
import re
import time
import base64
import requests
from bs4 import BeautifulSoup
from PIL import Image
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = '8426545218:AAFKYlHjZwUlLrgXVPyDhRr0cMreRtPWwqw'
VERCEL_URL = 'https://birthid-puce.vercel.app'
DEVELOPER = "@shakib2016"
OCR_KEYS = os.environ.get('OCR_API_KEYS', 'K8513647421792688957,K8824022881352388957').split(',')

_UNICODE_MATH = str.maketrans({
    '−': '-', '–': '-', '—': '-', '×': '*', '✕': '*', '·': '*', 'x': '*', 'X': '*',
    '÷': '/', '／': '/', '＋': '+', '－': '-', '＊': '*', '？': '?', '＝': '=',
    '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4', '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
})

def eval_math(raw_ocr: str) -> str:
    if not raw_ocr: return ''
    norm = raw_ocr.translate(_UNICODE_MATH)
    cleaned = re.sub(r'\s+', '', norm).replace('=', '').replace('?', '')
    m = re.search(r'(\d+)([\+\-\*\/])(\d+)', cleaned)
    if m:
        n1, op, n2 = int(m.group(1)), m.group(2), int(m.group(3))
        try:
            if op == '+': return str(n1 + n2)
            if op == '-': return str(n1 - n2)
            if op == '*': return str(n1 * n2)
            if op == '/': return str(n1 // n2) if n2 else ''
        except: pass
    m_eq = re.search(r'=\s*(-?\d+)', norm)
    if m_eq: return m_eq.group(1).strip()
    nums = re.findall(r'\d+', norm)
    return nums[-1] if nums else ''

def _variants(img_bytes):
    try: img = Image.open(io.BytesIO(img_bytes)).convert('L')
    except: return []
    out = []
    def emit(label, im):
        buf = io.BytesIO(); im.save(buf, format='PNG')
        out.append((label, base64.b64encode(buf.getvalue()).decode('ascii')))
    up = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    emit('normal', up)
    th = up.point(lambda p: 255 if p > 140 else 0)
    emit('thresh', th)
    return out

def solve_captcha(img_bytes):
    if not img_bytes: return '', 'No image'
    variants = _variants(img_bytes)
    debug_msgs = []
    for label, b64 in variants:
        for engine in ('3', '2'):
            for key in OCR_KEYS:
                key = key.strip()
                if not key: continue
                form = {'apikey': key, 'language': 'eng', 'base64Image': f'data:image/png;base64,{b64}', 'OCREngine': engine, 'scale': 'true'}
                try:
                    r = requests.post('https://api.ocr.space/parse/image', data=form, timeout=4)
                    data = r.json()
                    if data.get('IsErroredOnProcessing'): continue
                    results = data.get('ParsedResults') or []
                    if results:
                        text = results[0].get('ParsedText', '').strip()
                        ans = eval_math(text)
                        if ans: return ans, f'Success: {text}'
                        else: debug_msgs.append(f"{label}-{engine}: '{text}'")
                except: pass
    return '', f'Failed: {", ".join(debug_msgs)}'

def fetch_bdris(record_type, number, dob):
    base_url = 'https://everify.bdris.gov.bd/'
    search_url = f'{base_url}{"UBRN" if record_type == "birth" else "UDRN"}Verification/Search'
    start_time = time.time()
    last_debug = 'Initializing'
    
    for attempt in range(4):
        if time.time() - start_time > 8.5: break
        session = requests.Session()
        session.headers.update({'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        
        try:
            resp = session.get(base_url, timeout=3)
            soup = BeautifulSoup(resp.text, 'html.parser')
            token = soup.find('input', {'name': '__RequestVerificationToken'})['value']
            captcha_img = soup.find('img', {'id': 'CaptchaImage'})['src']
            captcha_de_text = captcha_img.split('t=')[-1]
            img_url = f"https://everify.bdris.gov.bd{captcha_img}" if captcha_img.startswith('/') else captcha_img
            
            img_headers = {'referer': base_url, 'user-agent': session.headers['user-agent']}
            img_data = session.get(img_url, headers=img_headers, timeout=3).content
            
            solved, debug_info = solve_captcha(img_data)
            if not solved: 
                last_debug = debug_info
                continue
                
            form = {'__RequestVerificationToken': token, 'UBRN': number, 'BirthDate': dob, 'CaptchaDeText': captcha_de_text, 'CaptchaInputText': solved}
            res = session.post(search_url, data=form, timeout=4)
            
            soup = BeautifulSoup(res.text, 'html.parser')
            tables = soup.find_all('table')
            
            if not tables:
                if 'no record' in res.text.lower() or 'not found' in res.text.lower(): 
                    return {'success': False, 'error': 'No record found for this UBRN and DOB.'}
                elif 'captcha' in res.text.lower():
                    last_debug = f'Captcha wrong (Solved: {solved})'
                    continue
                else:
                    last_debug = 'Invalid response from BDRIS.'
                    continue
                    
            data = {}
            t0_rows = tables[0].find_all('tr')
            if len(t0_rows) >= 5:
                r2 = [c.get_text(strip=True) for c in t0_rows[2].find_all(['td', 'th'])]
                if len(r2) >= 3: data.update({'registration_date': r2[0], 'registration_office': r2[1], 'issuance_date': r2[2]})
                r4 = [c.get_text(strip=True) for c in t0_rows[4].find_all(['td', 'th'])]
                if len(r4) >= 3: data.update({'date_of_birth': r4[0], 'birth_registration_number': r4[1], 'sex': r4[2]})
            if len(tables) > 1:
                for tr in tables[1].find_all('tr'):
                    cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th']) if td.get_text(strip=True)]
                    if len(cells) < 4: continue
                    lbl_bn, val_bn, _lbl_en, val_en = cells[0], cells[1], cells[2], cells[3]
                    if 'নাম' in lbl_bn and 'মাতার' not in lbl_bn and 'পিতার' not in lbl_bn: data.update({'name_bn': val_bn, 'name_en': val_en})
                    elif 'জন্মস্থান' in lbl_bn: data.update({'birth_place_bn': val_bn, 'birth_place_en': val_en})
                    elif 'মাতার নাম' in lbl_bn: data.update({'mother_name_bn': val_bn, 'mother_name_en': val_en})
                    elif 'পিতার নাম' in lbl_bn: data.update({'father_name_bn': val_bn, 'father_name_en': val_en})
                    
            return {'success': True, 'data': data}
        except requests.exceptions.Timeout:
            last_debug = 'Network timeout'
            continue
        except Exception as e:
            last_debug = str(e)
            continue
    return {'success': False, 'error': f'Tried multiple times. Last issue: {last_debug}'}

def tg_api(endpoint, payload):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}", json=payload, timeout=8)
        return r.text
    except Exception as e:
        return f"Error: {e}"

def tg_send(chat_id, text, markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup: payload["reply_markup"] = markup
    return tg_api("sendMessage", payload)

def telegram_webhook_logic(data):
    # --- CALLBACK QUERY HANDLER (BUTTON CLICKS) ---
    if 'callback_query' in data:
        cb = data['callback_query']
        cb_id = cb['id']
        chat_id = cb['message']['chat']['id']
        cb_data = cb.get('data', '')
        
        # Answer the callback to stop the loading spinner on the button
        tg_api("answerCallbackQuery", {"callback_query_id": cb_id})
        
        if cb_data == 'help':
            help_text = (
                "<b>📖 How to use this bot:</b>\n\n"
                "1️⃣ Get the 17-digit Birth Registration Number (UBRN).\n"
                "2️⃣ Get the Date of Birth (Year-Month-Day).\n"
                "3️⃣ Send them in this chat separated by a space.\n\n"
                "<b>Example:</b>\n"
                "<code>19901234567890123 2008-03-08</code>\n\n"
                "The bot will automatically solve the captcha and fetch the information for you!"
            )
            tg_send(chat_id, help_text)
            
        elif cb_data == 'dev':
            tg_send(chat_id, f"👨‍💻 <b>Developer:</b> {DEVELOPER}")
            
        return 'ok', 200

    # --- MESSAGE HANDLER ---
    if 'message' not in data: return 'ok', 200
    msg = data['message']
    chat_id = msg['chat']['id']
    text = msg.get('text', '').strip()

    if text.startswith('/start'):
        # Beautiful UI layout
        markup = {
            "inline_keyboard": [
                [{"text": "📖 How to use", "callback_data": "help"}],
                [{"text": "👨‍💻 Developer", "callback_data": "dev"}]
            ]
        }
        
        # Exact format requested by user
        welcome_text = (
            "<b>👋 Welcome to BDRIS Verify!</b>\n\n"
            "Send: <code>19901234567890123 2008-03-08</code>\n"
            "<i>(17-Digit Birth number and Year-Month-Day)</i>"
        )
        
        tg_send(chat_id, welcome_text, markup)
        return 'ok', 200

    match = re.search(r'\b(\d{17})\s+(\d{4}-\d{2}-\d{2})\b', text)
    if match:
        ubrn, dob = match.groups()
        tg_send(chat_id, f"⏳ <b>Verifying:</b> <code>{ubrn}</code>...")
        res = fetch_bdris('birth', ubrn, dob)
        if res.get('success'):
            d = res['data']
            reply = f"✅ <b>Record Found!</b>\n👤 {d.get('name_bn', '')} ({d.get('name_en', '')})\n📅 {d.get('date_of_birth', '')}\n👨‍👩‍👧 {d.get('father_name_bn', '')}\n👩‍👧 {d.get('mother_name_bn', '')}\n📍 {d.get('birth_place_bn', '')}"
            tg_send(chat_id, reply)
        else:
            tg_send(chat_id, f"❌ <b>Failed:</b> {res.get('error', 'Error')}")
    elif text and not text.startswith('/'):
        tg_send(chat_id, "⚠️ Invalid format. Send: <code>19901234567890123 2008-03-08</code>")

    return 'ok', 200

@app.route('/api/birth', methods=['POST'])
@app.route('/api/index.py', methods=['POST'])
@app.route('/api/bot', methods=['POST'])
@app.route('/api/index', methods=['POST'])
def unified_handler():
    body = request.get_json(silent=True) or {}
    
    if 'message' in body or 'callback_query' in body:
        return telegram_webhook_logic(body)
        
    if 'num' in body and 'dob' in body:
        return jsonify(fetch_bdris('birth', body.get('num', '').strip(), body.get('dob', '').strip()))

    return jsonify({"error": "Invalid request payload"}), 400

@app.route('/api/set_webhook', methods=['GET'])
def set_webhook():
    res = tg_api("setWebhook", {"url": f"{VERCEL_URL}/api/bot", "drop_pending_updates": True})
    return f"<h3>Webhook Setup Result:</h3><pre>{res}</pre>"

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "ok", "message": "Backend running."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
