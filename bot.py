# bot.py
import io
import os
import re
import time
import base64
import requests
from bs4 import BeautifulSoup
from PIL import Image

TELEGRAM_TOKEN = '8426186448:AAFx1NO1zhNERio8x4fn6v94bBOLaWXh4eY'
VERCEL_URL = 'https://birthid-puce.vercel.app'
DEVELOPER = "@shakib2016"
API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
OCR_KEYS = ['K8824022881352388957']

_UNICODE_MATH = str.maketrans({'−': '-', '–': '-', '—': '-', '×': '*', '✕': '*', '·': '*', 'x': '*', 'X': '*', '÷': '/', '／': '/', '＋': '+', '－': '-', '＊': '*', '？': '?', '＝': '=', '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4', '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'})

def eval_math(raw_ocr):
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
    try: img = Image.open(io.BytesIO(img_bytes)).convert('L')
    except Exception as e: return '', f'ImgErr: {e}'
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
            form = {'apikey': key, 'language': 'eng', 'base64Image': f'data:image/png;base64,{b64}', 'OCREngine': '2', 'scale': 'true'}
            try:
                r = requests.post('https://api.ocr.space/parse/image', data=form, timeout=4)
                if r.status_code == 200:
                    results = r.json().get('ParsedResults') or []
                    if results:
                        text = results[0].get('ParsedText', '')
                        ans = eval_math(text)
                        if ans: return ans, 'Success'
                        else: debug_msgs.append(f'{text}')
                    else: debug_msgs.append('empty')
                else: debug_msgs.append(f'HTTP {r.status_code}')
            except: debug_msgs.append('Err')
    return '', f'OCR Failed: {", ".join(debug_msgs)}'

def fetch_bdris(record_type, number, dob):
    base_url = 'https://everify.bdris.gov.bd/'
    search_url = f'{base_url}UBRNVerification/Search'
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
        if not solved: return {'success': False, 'error': f'Captcha issue: {debug_info}'}
        form = {'__RequestVerificationToken': token, 'UBRN': number, 'BirthDate': dob, 'CaptchaDeText': captcha_de_text, 'CaptchaInputText': solved}
        res = session.post(search_url, data=form, timeout=6)
        soup = BeautifulSoup(res.text, 'html.parser')
        tables = soup.find_all('table')
        if not tables:
            if 'no record' in res.text.lower(): return {'success': False, 'error': 'No record found.'}
            elif 'captcha' in res.text.lower(): return {'success': False, 'error': 'Captcha wrong.'}
            return {'success': False, 'error': 'Invalid BDRIS response'}
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
    except requests.exceptions.Timeout: return {'success': False, 'error': 'Server timeout.'}
    except Exception as e: return {'success': False, 'error': f'Error: {e}'}

def get_updates(offset=None):
    try:
        resp = requests.get(f"{API_URL}/getUpdates", params={'timeout': 30, 'offset': offset}, timeout=35)
        return resp.json()
    except: return {}

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(f"{API_URL}/sendMessage", json=payload)

def main():
    print("🤖 Bot started...")
    offset = None
    while True:
        updates = get_updates(offset)
        if not updates.get('ok'): time.sleep(5); continue
        for update in updates.get('result', []):
            offset = update['update_id'] + 1
            if 'message' not in update: continue
            msg = update['message']
            chat_id = msg['chat']['id']
            text = msg.get('text', '').strip()
            if text == '/start':
                markup = {"inline_keyboard": [[{"text": "🚀 Open Ultra UI", "web_app": {"url": VERCEL_URL}}]]}
                send_message(chat_id, f"<b>👋 Welcome!</b>\n\nSend: <code>19901234567890123 2008-03-08</code>\n\n👨‍💻 <b>Dev:</b> {DEVELOPER}", markup)
                continue
            match = re.search(r'\b(\d{17})\s+(\d{4}-\d{2}-\d{2})\b', text)
            if match:
                ubrn, dob = match.groups()
                send_message(chat_id, f"⏳ <b>Verifying:</b> <code>{ubrn}</code>...")
                res = fetch_bdris('birth', ubrn, dob)
                if res.get('success'):
                    d = res['data']
                    send_message(chat_id, f"✅ <b>Record Found!</b>\n👤 {d.get('name_bn', '')}\n📅 {d.get('date_of_birth', '')}\n👨‍👩‍👧 {d.get('father_name_bn', '')}\n👩‍👧 {d.get('mother_name_bn', '')}\n📍 {d.get('birth_place_bn', '')}")
                else:
                    send_message(chat_id, f"❌ <b>Failed:</b> {res.get('error', 'Error')}")
            elif text and not text.startswith('/'):
                send_message(chat_id, "⚠️ Invalid format. Send: <code>19901234567890123 2008-03-08</code>")
        time.sleep(1)

if __name__ == '__main__':
    main()
