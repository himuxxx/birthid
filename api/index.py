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
DEVELOPER = "@shakib2016"
OCR_KEYS = os.environ.get('OCR_API_KEYS', 'K8513647421792688957,K8824022881352388957').split(',')

_UNICODE_MATH = str.maketrans({'−': '-', '–': '-', '—': '-', '×': '*', '✕': '*', '·': '*', 'x': '*', 'X': '*', '÷': '/', '／': '/', '＋': '+', '－': '-', '＊': '*', '？': '?', '＝': '=', '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4', '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'})

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
                except: pass
    return '', 'Failed'

def fetch_bdris(record_type, number, dob):
    base_url = 'https://everify.bdris.gov.bd/'
    search_url = f'{base_url}{"UBRN" if record_type == "birth" else "UDRN"}Verification/Search'
    start_time = time.time()
    
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
            if not solved: continue
            form = {'__RequestVerificationToken': token, 'UBRN': number, 'BirthDate': dob, 'CaptchaDeText': captcha_de_text, 'CaptchaInputText': solved}
            res = session.post(search_url, data=form, timeout=4)
            soup = BeautifulSoup(res.text, 'html.parser')
            tables = soup.find_all('table')
            if not tables:
                if 'no record' in res.text.lower(): return {'success': False, 'error': 'No record found.'}
                elif 'captcha' in res.text.lower(): continue
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
        except: continue
    return {'success': False, 'error': 'Timeout or Captcha failed'}


# ---------- BOT & API HANDLER ----------
@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def catch_all(path):
    if request.method == 'GET':
        if 'set_webhook' in path:
            # Pointing directly to /api/index.py
            url = "https://birthid-puce.vercel.app/api/index.py"
            res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", json={"url": url, "drop_pending_updates": True}).text
            return f"<h3>Webhook Result:</h3><pre>{res}</pre>"
        return jsonify({"status": "ok"}), 200

    try:
        body = request.get_json(force=True, silent=True) or {}
    except: 
        body = {}

    # --- TELEGRAM BOT MESSAGE ---
    if 'message' in body:
        msg = body['message']
        chat_id = msg.get('chat', {}).get('id')
        text = msg.get('text', '').strip()

        if chat_id:
            if text.startswith('/start'):
                welcome_text = (
                    "<b>👋 Welcome to BD Birth Number Ultra Verify!</b>\n\n"
                    "📖 <b>How to use:</b>\n"
                    "Send: <code>19901234567890123 2008-03-08</code>\n"
                    "(17-Digit Birth number and Year-Month-Day)\n\n"
                    "👨‍💻 <b>Developer Info:</b>\n"
                    "Name: Sakib Hasan\n"
                    "Telegram: @shakib2016\n"
                    "Project: BD Birth Number Ultra Verify"
                )
                # No reply_markup (buttons removed)
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
                    "chat_id": chat_id, 
                    "text": welcome_text, 
                    "parse_mode": "HTML"
                })
                return 'ok', 200
                
            match = re.search(r'\b(\d{17})\s+(\d{4}-\d{2}-\d{2})\b', text)
            if match:
                ubrn, dob = match.groups()
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": f"⏳ Verifying: {ubrn}..."})
                res = fetch_bdris('birth', ubrn, dob)
                if res.get('success'):
                    d = res['data']
                    reply = f"✅ <b>Record Found!</b>\n👤 {d.get('name_bn', '')} ({d.get('name_en', '')})\n📅 {d.get('date_of_birth', '')}\n👨‍👩‍👧 {d.get('father_name_bn', '')}\n👩‍👧 {d.get('mother_name_bn', '')}\n📍 {d.get('birth_place_bn', '')}"
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": reply, "parse_mode": "HTML"})
                else:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": f"❌ Failed: {res.get('error', 'Error')}"})
            elif text and not text.startswith('/'):
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": "⚠️ Invalid format. Send: <code>19901234567890123 2008-03-08</code>", "parse_mode": "HTML"})
            return 'ok', 200

    # --- WEB UI FETCH ---
    if 'num' in body and 'dob' in body:
        return jsonify(fetch_bdris('birth', body.get('num', '').strip(), body.get('dob', '').strip()))

    return 'ok', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
