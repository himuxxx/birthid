# api/core.py
import io
import os
import re
import base64
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter, ImageOps

try:
    from upstash_redis import Redis
    REDIS = Redis(url=os.environ.get("UPSTASH_REDIS_REST_URL"), token=os.environ.get("UPSTASH_REDIS_REST_TOKEN")) if os.environ.get("UPSTASH_REDIS_REST_URL") else None
except ImportError:
    REDIS = None

OCR_URL = 'https://api.ocr.space/parse/image'
DEFAULT_KEYS = os.environ.get('OCR_API_KEYS', 'K8824022881352388957').split(',')

def get_ocr_keys():
    if REDIS:
        try:
            keys = REDIS.get("ocr_keys")
            if keys: return [k.strip() for k in keys.split(',') if k.strip()]
        except: pass
    return [k.strip() for k in DEFAULT_KEYS if k.strip()]

def add_ocr_key(new_key):
    keys = get_ocr_keys()
    if new_key not in keys:
        keys.append(new_key)
    if REDIS:
        try: REDIS.set("ocr_keys", ",".join(keys))
        except: pass
    return keys

_UNICODE_MATH = str.maketrans({
    '−': '-', '–': '-', '—': '-', '×': '*', '✕': '*', '·': '*', 'x': '*', 'X': '*',
    '÷': '/', '／': '/', '＋': '+', '－': '-', '＊': '*', '？': '?', '＝': '=',
    '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4', '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
})

def eval_math_expression(raw_ocr: str) -> str:
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
    except: return [('raw', img_bytes)]
    out = []
    def emit(label, im):
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        out.append((label, buf.getvalue()))
    up = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    emit('up', up)
    for t in (110, 140, 180): emit(f'th{t}', up.point(lambda p, tt=t: 255 if p > tt else 0))
    inv = ImageOps.invert(up)
    for t in (110, 140): emit(f'inv_th{t}', inv.point(lambda p, tt=t: 255 if p > tt else 0))
    med = up.filter(ImageFilter.MedianFilter(3))
    emit('med_th140', med.point(lambda p: 255 if p > 140 else 0))
    return out

def _ocr_overspace(img_bytes, engine, key):
    b64 = base64.b64encode(img_bytes).decode('ascii')
    form = {'apikey': key, 'language': 'eng', 'base64Image': f'data:image/png;base64,{b64}', 'OCREngine': str(engine), 'scale': 'true'}
    try:
        r = requests.post(OCR_URL, data=form, timeout=15)
        if r.status_code != 200: return None
        data = r.json()
        if data.get('IsErroredOnProcessing'): return None
        results = data.get('ParsedResults') or []
        return (results[0].get('ParsedText') or '').strip() if results else ''
    except: return None

def solve_captcha_image(img_bytes: bytes) -> str:
    if not img_bytes: return ''
    variants = _variants(img_bytes)
    keys = get_ocr_keys()
    for label, vb in variants:
        for engine in (2, 3):
            for key in keys:
                text = _ocr_overspace(vb, engine, key)
                if text:
                    ans = eval_math_expression(text)
                    if ans: return ans
    return ''

class VerificationSession:
    def __init__(self, base_url, search_url):
        self.base_url = base_url
        self.search_url = search_url
        self.session = requests.Session()
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        })
        self.token = None
        self.captcha_de_text = None
        self.captcha_img_url = None

    def init_dynamic_session(self):
        resp = self.session.get(self.base_url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        token_elem = soup.find('input', {'name': '__RequestVerificationToken'})
        self.token = token_elem['value'] if token_elem else ''
        captcha_img = soup.find('img', {'id': 'CaptchaImage'})
        if not captcha_img: raise ValueError('Captcha not found')
        img_src = captcha_img['src']
        self.captcha_de_text = img_src.split('t=')[-1]
        self.captcha_img_url = f"https://everify.bdris.gov.bd{img_src}" if img_src.startswith('/') else img_src

    def run_full_flow(self, number, date_str):
        self.init_dynamic_session()
        img_resp = self.session.get(self.captcha_img_url, timeout=10)
        solved = solve_captcha_image(img_resp.content)
        if not solved: return None, 'Captcha solve failed'
        
        form = {'__RequestVerificationToken': self.token, 'UBRN': number, 'BirthDate': date_str, 'CaptchaDeText': self.captcha_de_text, 'CaptchaInputText': solved}
        res = self.session.post(self.search_url, data=form, timeout=15)
        parsed = parse_bdris_table(res.text)
        return res, parsed

def parse_bdris_table(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    tables = soup.find_all('table')
    if not tables: return None
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
            elif 'মাতার জাতীয়তা' in lbl_bn: data.update({'mother_nationality_bn': val_bn, 'mother_nationality_en': val_en})
            elif 'পিতার নাম' in lbl_bn: data.update({'father_name_bn': val_bn, 'father_name_en': val_en})
            elif 'পিতার জাতীয়তা' in lbl_bn: data.update({'father_nationality_bn': val_bn, 'father_nationality_en': val_en})
    return data if data else None

def fetch_record(record_type, number, dob):
    if record_type == 'birth':
        s = VerificationSession('https://everify.bdris.gov.bd/', 'https://everify.bdris.gov.bd/UBRNVerification/Search')
    else:
        s = VerificationSession('https://everify.bdris.gov.bd/UDRNVerification', 'https://everify.bdris.gov.bd/UDRNVerification/Search')
    for _ in range(3):
        try:
            res, data = s.run_full_flow(number, dob)
            if data: return {'success': True, 'data': data}
            if res and ('no record' in res.text.lower() or 'not found' in res.text.lower()):
                return {'success': False, 'error': 'Record not found'}
        except Exception: pass
    return {'success': False, 'error': 'Captcha solve failed or server timeout'}
