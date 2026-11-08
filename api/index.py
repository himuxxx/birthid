# api/index.py
import io
import os
import re
import base64
import requests
from bs4 import BeautifulSoup
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='../public', static_url_path='/')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8426186448:AAFx1NO1zhNERio8x4fn6v94bBOLaWXh4eY')
VERCEL_URL = os.environ.get('VERCEL_URL', 'https://birthid-puce.vercel.app')
DEVELOPER = "@shakib2016"

# You can add more keys here later separated by comma if this one fails
OCR_KEYS = os.environ.get('OCR_API_KEYS', 'K8824022881352388957').split(',')

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

def solve_captcha(img_bytes):
    if not img_bytes: return '', 'No image'
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert('L')
    except Exception as e:
        return '', f'ImgErr: {e}'
    
    # Fast processing, 2 variants
    variants = []
    up = img.resize((img.width * 3, img.height * 3), Image.Resampling.LANCZOS)
    
    buf = io.BytesIO(); up.save(buf, format='PNG')
    variants.append(('normal', base64.b64encode(buf.getvalue()).decode('ascii')))
    
    th = up.point(lambda p: 255 if p > 140 else 0)
    buf = io.BytesIO(); th.save(buf, format='PNG')
    variants.append(('thresh', base64.b64encode(buf.getvalue()).decode('ascii')))

    debug_msgs = []
    for label, b64 in variants:
        for key in OCR_KEYS:
            key = key.strip()
            if not key: continue
            form = {'apikey': key, 'language': 'eng', 'base64Image': f'data:image/png;base64,{b64}', 'OCREngine': '2', 'scale': 'true'}
            try:
                r = requests.post('https://api.ocr.space/parse/image', data=form, timeout=4)
                if r.status_code == 200:
                    results = r.json().get('ParsedResults') or []
                    if results:
                        text = results[0].get('ParsedText', '')
                        ans = eval_math(text)
                        if ans: return ans, f'Success text:{text}'
                        else: debug_msgs.append(f'{text}')
                    else:
                        debug_msgs.append('empty')
                else:
                    debug_msgs.append(f'HTTP {r.status_code}')
            except Exception as e:
                debug_msgs.append(f'Err')
                
    return '', f'OCR Failed: {", ".join(debug_msgs)}'

def fetch_bdris(record_type, number, dob):
    base_url = 'https://everify.bdris.gov.bd/'
    search_url = f'{base_url}{"UBRN" if record_type == "birth" else "UDRN"}Verification/Search'
    if record_type == 'death': base_url = 'https://everify.bdris.gov.bd/UDRNVerification'
    
    session = requests.Session()
    session.headers.update({'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    
    try:
        resp = session.get(base_url, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        token = soup.find('input', {'name': '__RequestVerificationToken'})['value']
        captcha_img = soup.find('img', {'id': 'CaptchaImage'})['src']
        captcha_de_text = captcha_img.split('t=')[-1]
        img_url = f"https://everify.bdris.gov.bd{captcha_img}" if captcha_img.startswith('/') else captcha_img
        
        img_data = session.get(img_url, timeout=5).content
        solved, debug_info = solve_captcha(img_data)
        
        # If captcha fails, return exact reason instead of timing out
        if not solved: 
            return {'success': False, 'error': f'Captcha issue: {debug_info}'}
        
        form = {'__RequestVerificationToken': token, 'UBRN': number, 'BirthDate': dob, 'CaptchaDeText': captcha_de_text, 'CaptchaInputText': solved}
        res = session.post(search_url, data=form, timeout=6)
        
        soup = BeautifulSoup(res.text, 'html.parser')
        tables = soup.find_all('table')
        
        if not tables:
            if 'no record' in res.text.lower() or 'not found' in res.text.lower(): 
                return {'success': False, 'error': 'No record found for this UBRN and DOB.'}
            elif 'captcha' in res.text.lower():
                return {'success': False, 'error': 'Captcha solved wrong. Please try again.'}
            return {'success': False, 'error': 'Invalid response from BDRIS server.'}
            
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
        return {'success': False, 'error': 'Server timeout. Please try again.'}
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

def tg_api(endpoint, payload):
    if not TELEGRAM_TOKEN: return
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}", json=payload, timeout=5)

def tg_send(chat_id, text, markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup: payload["reply_markup"] = markup
    tg_api("sendMessage", payload)

def telegram_webhook_logic(data):
    if not TELEGRAM_TOKEN: return 'No token', 200
    
    if 'callback_query' in data:
        cb = data['callback_query']
        tg_api("answerCallbackQuery", {"callback_query_id": cb['id'], "text": "Processing..."})
        if cb.get('data') == 'help':
            tg_send(cb['message']['chat']['id'], f"<b>📖 How to use:</b>\n\nSend: <code>UBRN DOB</code>\nExample: <code>19901234567890123 2008-03-08</code>\n\n👨‍💻 <b>Dev:</b> {DEVELOPER}")
        return 'ok', 200

    if 'message' not in data: return 'ok', 200
    msg = data['message']
    chat_id = msg['chat']['id']
    text = msg.get('text', '').strip()

    if text == '/start':
        markup = {"inline_keyboard": [[{"text": "🚀 Open Ultra UI", "web_app": {"url": VERCEL_URL}}], [{"text": "📖 How to use", "callback_data": "help"}]]}
        tg_send(chat_id, f"<b>👋 Welcome to BDRIS Verify!</b>\n\nSend: <code>19901234567890123 2008-03-08</code>\n<i>(17-digit UBRN and Date of Birth)</i>\n\n👨‍💻 <b>Dev:</b> {DEVELOPER}", markup)
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

@app.route('/api/index.py', methods=['POST', 'GET'])
@app.route('/api/index', methods=['POST', 'GET'])
@app.route('/api/birth', methods=['POST'])
@app.route('/api/death', methods=['POST'])
@app.route('/api/bot', methods=['POST'])
def unified_handler():
    if request.method == 'GET':
        return jsonify({"status": "ok", "message": "Backend is running perfectly!"})

    body = request.get_json(silent=True) or {}
    
    if 'message' in body or 'callback_query' in body:
        return telegram_webhook_logic(body)
        
    if 'num' in body and 'dob' in body:
        record_type = body.get('type', 'birth')
        return jsonify(fetch_bdris(record_type, body.get('num', '').strip(), body.get('dob', '').strip()))

    return jsonify({"error": "Invalid request payload"}), 400

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
