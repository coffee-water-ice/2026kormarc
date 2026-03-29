# 🔧 중복 제거된 통합 코드 (겹치는 부분 주석 제거)

# ===== 표준 라이브러리
import os
import re
import io
import json
import time
import html
import datetime
import logging
import sqlite3
import threading
import math
from string import Template
from collections import defaultdict, Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus, urljoin, urlencode
import xml.etree.ElementTree as ET

# ===== 서드파티 라이브러리
import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from pymarc import Record, Field, MARCWriter, Subfield

# ===== 환경 설정
load_dotenv()
st.set_page_config(page_title="ISBN→MARC", layout="wide")

# ===== 전역 설정
OPENAI_CHAT_COMPLETIONS = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
LOGGER_NAME = "isbn2marc"

# ===== 로거 설정
logger = logging.getLogger(LOGGER_NAME)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _fmt = logging.Formatter("%(levelname)s:%(name)s: %(message)s")
    _handler.setFormatter(_fmt)
    logger.addHandler(_handler)
logger.setLevel(logging.WARNING)

if "debug_mode" not in st.session_state:
    st.session_state["debug_mode"] = False

CURRENT_DEBUG_LINES: list[str] = []

# ===== 디버그 함수
def dbg(*args):
    """디버그 라인 수집"""
    msg = " ".join(str(a) for a in args)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    CURRENT_DEBUG_LINES.append(line)
    logger.debug(msg)

def dbg_err(*args):
    """에러 로그"""
    msg = " ".join(str(a) for a in args)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] ERROR: {msg}"
    CURRENT_DEBUG_LINES.append(line)
    logger.error(msg)

# ===== HTTP 세션 설정
def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; isbn2marc/1.0; +https://local)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    retries = Retry(
        total=4, connect=2, read=3, backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = _get_session()
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

# ===== API 키 및 설정
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY", "")
ALADIN_TTB_KEY = os.getenv("ALADIN_TTB_KEY") or st.secrets.get("ALADIN_TTB_KEY", "")
NLK_CERT_KEY = os.getenv("NLK_CERT_KEY") or st.secrets.get("NLK_CERT_KEY", "")

# 호환용 별칭
aladin_key = ALADIN_TTB_KEY
ALADIN_KEY = ALADIN_TTB_KEY
openai_key = OPENAI_API_KEY
ttbkey = ALADIN_TTB_KEY
DEFAULT_MODEL = (st.secrets.get("openai", {}) or {}).get("model") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
model = DEFAULT_MODEL

# OpenAI 클라이언트
try:
    _client = OpenAI(api_key=OPENAI_API_KEY, timeout=10) if OPENAI_API_KEY else None
except Exception:
    _client = None

# ===== 상수 정의
ISDS_LANGUAGE_CODES = {
    'kor': '한국어', 'eng': '영어', 'jpn': '일본어', 'chi': '중국어',
    'rus': '러시아어', 'ara': '아랍어', 'fre': '프랑스어', 'ger': '독일어',
    'ita': '이탈리아어', 'spa': '스페인어', 'por': '포르투갈어', 'tur': '터키어',
    'und': '알 수 없음'
}
ALLOWED_CODES = set(ISDS_LANGUAGE_CODES.keys()) - {"und"}

# KDC 관련 상수
KR_REGION_TO_CODE = {
    "서울": "ulk", "서울특별시": "ulk",
    "경기": "ggk", "경기도": "ggk",
    "부산": "bnk", "부산광역시": "bnk",
    "대구": "tgk", "대구광역시": "tgk",
    "인천": "ick", "인천광역시": "ick",
    "광주": "kjk", "광주광역시": "kjk",
    "대전": "tjk", "대전광역시": "tjk",
    "울산": "usk", "울산광역시": "usk",
    "세종": "sjk", "세종특별자치시": "sjk",
    "강원": "gak", "강원특별자치도": "gak",
    "충북": "hbk", "충청북도": "hbk",
    "충남": "hck", "충청남도": "hck",
    "전북": "jbk", "전라북도": "jbk",
    "전남": "jnk", "전라남도": "jnk",
    "경북": "gbk", "경상북도": "gbk",
    "경남": "gnk", "경상남도": "gnk",
    "제주": "jjk", "제주특별자치도": "jjk",
}

COUNTRY_FIXED = "ulk"
LANG_FIXED = "kor"
DEFAULT_TIMEOUT = 10

ALADIN_ITEMLOOKUP_URL = "https://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
ALADIN_SEARCH_URL = "https://www.aladin.co.kr/search/wsearchresult.aspx?SearchTarget=Book&SearchWord={query}"

# ===== 데이터베이스 설정
_cache_lock = threading.Lock()
_conn = sqlite3.connect("author_cache.sqlite3", check_same_thread=False)
_conn.execute("""CREATE TABLE IF NOT EXISTS name_cache(
  key TEXT PRIMARY KEY,
  value TEXT
)""")
_conn.commit()

# ===== 캐시 함수
def cache_get(key: str):
    with _cache_lock:
        cur = _conn.execute("SELECT value FROM name_cache WHERE key=?", (key,))
        row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]

def cache_set(key: str, value: dict):
    with _cache_lock:
        _conn.execute(
            "INSERT OR REPLACE INTO name_cache(key,value) VALUES(?,?)",
            (key, json.dumps(_jsonify(value), ensure_ascii=False)),
        )
        _conn.commit()

def cache_set_many(items: list[tuple[str, dict]]):
    if not items:
        return
    with _cache_lock:
        _conn.executemany(
            "INSERT OR REPLACE INTO name_cache(key,value) VALUES(?,?)",
            [(k, json.dumps(_jsonify(v), ensure_ascii=False)) for k, v in items]
        )
        _conn.commit()

# ===== JSON 직렬화 헬퍼
def _jsonify(obj):
    """dict/list/set 안의 set을 JSON 직렬화 가능하게 변환"""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    return obj

def _ensure_name_bundle(d):
    if d is None:
        return {"native": set(), "roman": set(), "countries": set()}
    return {
        "native": set(d.get("native", [])),
        "roman": set(d.get("roman", [])),
        "countries": set(d.get("countries", [])),
    }

# ===== 정규식 정의
_HANGUL_RE = re.compile(r"[가-힣]")
_CJK_RX = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7A3]")
_CYR_RX = re.compile(r"[\u0400-\u04FF]")
_KOREAN_ONLY_RX = re.compile(r"^[가-힣\s·\u00B7]$")
_TRAIL_PAREN_PAT = re.compile(
    r"""\s*(?:[\(\[](
        개정|증보|개역|전정|합본|전면개정|개정판|증보판|신판|보급판|
        최신개정판|개정증보판|국역|번역|영문판|초판|제?\d+\s*판|
        \d+\s*주년\s*기념판|기념판|
        [^()\[\]]*총서[^()\[\]]*|[^()\[\]]*시리즈[^()\[\]]*
    )[\)\]])\s*$""",
    re.IGNORECASE | re.VERBOSE
)

# ===== 유틸 함수
def strip_ns(tag):
    return tag.split('}')[-1] if '}' in tag else tag

def _norm(text: str) -> str:
    import unicodedata
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s\uac00-\ud7a3]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def _compat_normalize(s: str) -> str:
    if not s:
        return ""
    s = s.replace("：", ":").replace("－", "-").replace("‧", "·").replace("／", "/")
    s = re.sub(r"[\u2000-\u200f\u202a-\u202e]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _clean_author_str(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[/;·,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# ===== MarcBuilder 클래스
class MarcBuilder:
    def __init__(self):
        self.rec = Record(to_unicode=True, force_utf8=True)
        self.lines: list[str] = []

    def add_ctl(self, tag: str, data: str):
        if not data:
            return
        self.rec.add_field(Field(tag=tag, data=str(data)))
        self.lines.append(f"={tag}  {data}")

    def add(self, tag: str, ind1: str, ind2: str, subfields: list[tuple[str, str]]):
        sf = [(c, v) for c, v in subfields if (v or "") != ""]
        if not sf:
            return
        ind1 = " " if not ind1 or ind1 == "\\" else ind1
        ind2 = " " if not ind2 or ind2 == "\\" else ind2
        self.rec.add_field(Field(
            tag=tag,
            indicators=[ind1, ind2],
            subfields=[Subfield(c, v) for c, v in sf]
        ))
        parts = "".join(f"${c}{v}" for c, v in sf)
        self.lines.append(f"={tag}  {ind1}{ind2}{parts}")

    def mrk_text(self) -> str:
        return "\n".join(self.lines)

# ===== 언어 감지 함수 (중복 제거)
def detect_language_by_unicode(text):
    text = re.sub(r'[\s\W_]+', '', text or "")
    if not text:
        return 'und'
    c = text[0]
    if '\uac00' <= c <= '\ud7a3': return 'kor'
    if '\u3040' <= c <= '\u30ff': return 'jpn'
    if '\u4e00' <= c <= '\u9fff': return 'chi'
    if '\u0600' <= c <= '\u06FF': return 'ara'
    if '\u0e00' <= c <= '\u0e7f': return 'tha'
    return 'und'

def override_language_by_keywords(text, initial_lang):
    text = (text or "").lower()
    if initial_lang == 'chi' and re.search(r'[\u3040-\u30ff]', text): return 'jpn'
    if initial_lang in ['und', 'eng']:
        if "spanish" in text or "español" in text: return "spa"
        if "italian" in text or "italiano" in text: return "ita"
        if "french" in text or "français" in text: return "fre"
        if "portuguese" in text or "português" in text: return "por"
        if "german" in text or "deutsch" in text: return "ger"
        if any(ch in text for ch in ['é','è','ê','à','ç','ù','ô','â','î','û']): return "fre"
        if any(ch in text for ch in ['ñ','á','í','ó','ú']): return "spa"
        if any(ch in text for ch in ['ã','õ']): return "por"
    return initial_lang

def detect_language(text):
    lang = detect_language_by_unicode(text)
    return override_language_by_keywords(text, lang)

def detect_language_from_category(text):
    words = re.split(r'[>/\s]+', text or "")
    for w in words:
        if "일본" in w: return "jpn"
        if "중국" in w: return "chi"
        if "영미" in w or "영어" in w or "아일랜드" in w: return "eng"
        if "프랑스" in w: return "fre"
        if "독일" in w or "오스트리아" in w: return "ger"
        if "러시아" in w: return "rus"
        if "이탈리아" in w: return "ita"
        if "스페인" in w: return "spa"
        if "포르투갈" in w: return "por"
        if "튀르키예" in w or "터키" in w: return "tur"
    return None

# ===== 카테고리 처리 함수
def tokenize_category(text: str):
    if not text:
        return []
    t = re.sub(r'[()]+', ' ', text)
    raw = re.split(r'[>/\s]+', t)
    tokens = []
    for w in raw:
        w = w.strip()
        if not w:
            continue
        if '/' in w and w.count('/') <= 3 and len(w) <= 20:
            tokens.extend([p for p in w.split('/') if p])
        else:
            tokens.append(w)
    lower_tokens = tokens + [w.lower() for w in tokens if any('A'<=ch<='Z' or 'a'<=ch<='z' for ch in w)]
    return lower_tokens

def has_kw_token(tokens, kws):
    s = set(tokens)
    return any(k in s for k in kws)

def trigger_kw_token(tokens, kws):
    s = set(tokens)
    for k in kws:
        if k in s:
            return k
    return None

def is_literature_top(category_text: str) -> bool:
    return "소설/시/희곡" in (category_text or "")

def is_literature_category(category_text: str) -> bool:
    tokens = tokenize_category(category_text or "")
    ko_hits = ["문학", "소설", "시", "희곡"]
    en_hits = ["literature", "fiction", "novel", "poetry", "poem", "drama", "play"]
    return has_kw_token(tokens, ko_hits) or has_kw_token(tokens, en_hits)

def is_nonfiction_override(category_text: str) -> bool:
    tokens = tokenize_category(category_text or "")
    lit_top = is_literature_top(category_text or "")
    ko_nf_strict = ["역사","근현대사","서양사","유럽사","전기","평전",
                    "사회","정치","철학","경제","경영","인문","에세이","수필"]
    en_nf_strict = ["history","biography","memoir","politics","philosophy",
                    "economics","science","technology","nonfiction","essay","essays"]
    sci_keys = ["과학","기술"]
    sci_keys_en = ["science","technology"]
    
    k = trigger_kw_token(tokens, ko_nf_strict) or trigger_kw_token(tokens, en_nf_strict)
    if k:
        dbg(f"🔎 [판정근거] 비문학 키워드 발견: '{k}'")
        return True
    
    if not lit_top:
        k2 = trigger_kw_token(tokens, sci_keys) or trigger_kw_token(tokens, sci_keys_en)
        if k2:
            dbg(f"🔎 [판정근거] 비문학 최상위 추정 & '{k2}' 발견 → 비문학 오버라이드")
            return True
    
    if lit_top:
        dbg("🔎 [판정근거] 문학 최상위 감지: '과학/기술'은 오버라이드에서 제외(SF 보호).")
    return False

def is_domestic_category(category_text: str) -> bool:
    return "국내도서" in (category_text or "")

# ===== NLK/Aladin API 함수 (중복 제거)
def build_nlk_url_json(isbn: str, page_no: int = 1, page_size: int = 1) -> str:
    base = "https://seoji.nl.go.kr/landingPage/SearchApi.do"
    qs = urlencode({
        "cert_key": NLK_CERT_KEY,
        "result_style": "json",
        "page_no": page_no,
        "page_size": page_size,
        "isbn": isbn
    })
    return f"{base}?{qs}"

def fetch_nlk_seoji_json(isbn: str):
    """NLK 서지 API 호출"""
    if not NLK_CERT_KEY:
        raise RuntimeError("NLK_CERT_KEY 미설정")
    
    attempts = [
        "https://seoji.nl.go.kr/landingPage/SearchApi.do",
        "https://www.nl.go.kr/seoji/SearchApi.do",
        "http://seoji.nl.go.kr/landingPage/SearchApi.do",
        "http://www.nl.go.kr/seoji/SearchApi.do",
    ]
    params = {
        "cert_key": NLK_CERT_KEY, "result_style": "json",
        "page_no": 1, "page_size": 1, "isbn": isbn
    }
    last_err = None
    for base in attempts:
        try:
            r = SESSION.get(base, params=params, timeout=(10, 30))
            r.raise_for_status()
            data = r.json()
            docs = data.get("docs") or data.get("DOCS") or []
            if docs:
                return docs[0], r.url
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"NLK JSON 실패: {last_err}")

def fetch_nlk_author_only(isbn: str):
    """NLK에서 저자만 추출"""
    try:
        rec, used_url = fetch_nlk_seoji_json(isbn)
        author = get_anycase(rec, "AUTHOR") or ""
        return author, used_url
    except Exception:
        return "", build_nlk_url_json(isbn)

def get_anycase(rec: dict, key: str):
    if not rec:
        return None
    key_norm = key.strip().upper()
    for k, v in rec.items():
        if (k or "").strip().upper() == key_norm:
            return v
    return None

def fetch_aladin_item(isbn13: str) -> dict:
    """Aladin API로 item 정보 조회"""
    if not ALADIN_TTB_KEY:
        raise RuntimeError("ALADIN_TTB_KEY 미설정")
    url = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    params = {
        "ttbkey": ALADIN_TTB_KEY, "itemIdType": "ISBN13",
        "ItemId": isbn13, "output": "js", "Version": "20131101",
    }
    r = SESSION.get(url, params=params, timeout=(5, 20))
    r.raise_for_status()
    data = r.json()
    return (data.get("item") or [{}])[0]

def load_uploaded_csv(uploaded):
    """CSV 파일 로드 (다양한 인코딩 지원)"""
    content = uploaded.getvalue()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            text = content.decode(enc)
            return pd.read_csv(io.StringIO(text), engine="python", sep=None, dtype=str)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"CSV 인코딩/파싱 실패: {last_err}")

# ===== 041/546 언어 관련 함수
def _extract_code_and_reason(content, code_key="$h"):
    code, reason, signals = "und", "", ""
    lines = [l.strip() for l in (content or "").splitlines() if l.strip()]
    for ln in lines:
        if ln.startswith(f"{code_key}="):
            code = ln.split("=", 1)[1].strip()
        elif ln.lower().startswith("#reason="):
            reason = ln.split("=", 1)[1].strip()
        elif ln.lower().startswith("#signals="):
            signals = ln.split("=", 1)[1].strip()
    return code, reason, signals

def gpt_guess_original_lang(title, category, publisher, author="", original_title=""):
    """원서 언어 추정 (GPT)"""
    prompt = f"""
    아래 도서의 원서 언어(041 $h)를 ISDS 코드로 추정해줘.
    가능한 코드: kor, eng, jpn, chi, rus, fre, ger, ita, spa, por, tur

    도서정보:
    - 제목: {title}
    - 원제: {original_title or "(없음)"}
    - 분류: {category}
    - 출판사: {publisher}
    - 저자: {author}

    지침:
    - 국가/지역을 언어로 곧바로 치환하지 말 것.
    - 저자 국적·주 집필 언어·최초 출간 언어를 우선 고려.
    - 불확실하면 임의 추정 대신 'und' 사용.

    출력형식(정확히 이 2~3줄):
    $h=[ISDS 코드]
    #reason=[짧게 근거 요약]
    #signals=[잡은 단서들, 콤마로](선택)
    """.strip()
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system","content":"사서용 언어 추정기"},
                      {"role":"user","content":prompt}],
            temperature=0
        )
        content = (resp.choices[0].message.content or "").strip()
        code, reason, signals = _extract_code_and_reason(content, "$h")
        if code not in ALLOWED_CODES:
            code = "und"
        dbg(f"🧭 [GPT 근거] $h={code}")
        if reason: dbg(f"🧭 [이유] {reason}")
        if signals: dbg(f"🧭 [단서] {signals}")
        return code
    except Exception as e:
        dbg_err(f"GPT 오류: {e}")
        return "und"

def gpt_guess_main_lang(title, category, publisher):
    """본문 언어 추정 (GPT)"""
    prompt = f"""
    아래 도서의 본문 언어(041 $a)를 ISDS 코드로 추정.
    가능한 코드: kor, eng, jpn, chi, rus, fre, ger, ita, spa, por, tur

    입력:
    - 제목: {title}
    - 분류: {category}
    - 출판사: {publisher}

    지침:
    - '본문 언어'는 이 자료의 **현시본(Manifestation)** 언어다.
    - 저자 국적, 원작 언어, 시리즈 원산지 등 **원작 관련 단서 사용 금지**.
    - 카테고리에 '국내도서'가 있거나, 제목에 **한글이 1자라도** 포함되면 반드시 kor.
    - 허용 코드 밖이거나 불확실하면 'und'.

    출력형식:
    $a=[ISDS 코드]
    #reason=[짧게 근거 요약]
    #signals=[잡은 단서들, 콤마로](선택)
    """.strip()
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system","content":"사서용 본문 언어 추정기"},
                      {"role":"user","content":prompt}],
            temperature=0
        )
        content = (resp.choices[0].message.content or "").strip()
        code, reason, signals = _extract_code_and_reason(content, "$a")
        if code not in ALLOWED_CODES:
            code = "und"
        st.write(f"🧭 [GPT 근거] $a={code}")
        if reason: st.write(f"🧭 [이유] {reason}")
        if signals: st.write(f"🧭 [단서] {signals}")
        return code
    except Exception as e:
        st.error(f"GPT 오류: {e}")
        return "und"

def gpt_guess_original_lang_by_author(author, title="", category="", publisher=""):
    """저자 기반 원서 언어 추정 (GPT)"""
    prompt = f"""
    저자 정보를 중심으로 원서 언어(041 $h)를 ISDS 코드로 추정.
    가능한 코드: kor, eng, jpn, chi, rus, fre, ger, ita, spa, por, tur

    입력:
    - 저자: {author}
    - (참고) 제목: {title}
    - (참고) 분류: {category}
    - (참고) 출판사: {publisher}

    지침:
    - 저자 국적·주 집필 언어·대표 작품 원어를 우선.
    - 국가=언어 단순 치환 금지.
    - 불확실하면 'und'.

    출력형식:
    $h=[ISDS 코드]
    #reason=[짧게 근거 요약]
    #signals=[잡은 단서들, 콤마로](선택)
    """.strip()
    try:
        resp = _client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role":"system","content":"저자 기반 원서 언어 추정기"},
                      {"role":"user","content":prompt}],
            temperature=0
        )
        content = (resp.choices[0].message.content or "").strip()
        code, reason, signals = _extract_code_and_reason(content, "$h")
        if code not in ALLOWED_CODES:
            code = "und"
        st.write(f"🧭 [저자기반 근거] $h={code}")
        if reason: st.write(f"🧭 [이유] {reason}")
        if signals: st.write(f"🧭 [단서] {signals}")
        return code
    except Exception as e:
        st.error(f"GPT(저자기반) 오류: {e}")
        return "und"

def reconcile_language(candidate, fallback_hint=None, author_hint=None):
    """언어 충돌 해소"""
    if author_hint and author_hint != "und" and author_hint != candidate:
        st.write(f"🔁 [조정] 저자기반({author_hint}) ≠ 1차({candidate}) → 저자기반 우선")
        return author_hint
    if fallback_hint and fallback_hint != "und" and fallback_hint != candidate:
        if candidate in {"ita","fre","spa","por"}:
            if fallback_hint == "eng":
                return candidate
            st.write(f"🔁 [조정] 규칙힌트({fallback_hint}) vs 1차({candidate}) → 규칙힌트 우선")
            return fallback_hint
    return candidate

def determine_h_language(title: str, original_title: str, category_text: str,
                        publisher: str, author: str, subject_lang: str) -> str:
    """$h 우선순위 결정"""
    lit_raw = is_literature_category(category_text)
    nf_override = is_nonfiction_override(category_text)
    is_lit_final = lit_raw and not nf_override

    if lit_raw and not nf_override:
        dbg("📘 [판정] 이 자료는 문학(소설/시/희곡 등) 성격이 뚜렷합니다.")
    elif lit_raw and nf_override:
        dbg("📘 [판정] 겉보기에는 문학이지만, '역사·에세이·사회과학' 등 비문학 요소가 함께 보여 최종적으로는 비문학으로 처리될 수 있습니다.")
    elif not lit_raw and nf_override:
        dbg("📘 [판정] 문학적 단서는 없고, 비문학(역사·사회·철학 등) 성격이 강합니다.")
    else:
        dbg("📘 [판정] 문학/비문학 판단 단서가 약해 추가 판단이 필요합니다.")

    rule_from_original = detect_language(original_title) if original_title else "und"
    lang_h = None
    author_hint = None

    if is_lit_final:
        lang_h = subject_lang or rule_from_original
        dbg(f"📘 [설명] (문학 흐름) 1차 후보: {lang_h or 'und'}")
        if not lang_h or lang_h == "und":
            dbg("📘 [설명] (문학 흐름) GPT 보완 시도…")
            lang_h = gpt_guess_original_lang(title, category_text, publisher, author, original_title)
            dbg(f"📘 [설명] (문학 흐름) GPT 결과: {lang_h}")
        if (not lang_h or lang_h == "und") and author:
            dbg("📘 [설명] (문학 흐름) 원제 없음/애매 → 저자 기반 보정 시도…")
            author_hint = gpt_guess_original_lang_by_author(author, title, category_text, publisher)
            dbg(f"📘 [설명] (문학 흐름) 저자 기반 결과: {author_hint}")
    else:
        dbg("📘 [설명] (비문학 흐름) GPT 선행 판단…")
        lang_h = gpt_guess_original_lang(title, category_text, publisher, author, original_title)
        dbg(f"📘 [설명] (비문학 흐름) GPT 결과: {lang_h or 'und'}")
        if not lang_h or lang_h == "und":
            lang_h = subject_lang or rule_from_original
            dbg(f"📘 [설명] (비문학 흐름) 보조 규칙 적용 → 후보: {lang_h or 'und'}")
        if author and (not lang_h or lang_h == "und"):
            dbg("📘 [설명] (비문학 흐름) 원제 없음/애매 → 저자 기반 보정 시도…")
            author_hint = gpt_guess_original_lang_by_author(author, title, category_text, publisher)
            dbg(f"📘 [설명] (비문학 흐름) 저자 기반 결과: {author_hint}")

    fallback_hint = subject_lang or rule_from_original
    lang_h = reconcile_language(candidate=lang_h, fallback_hint=fallback_hint, author_hint=author_hint)
    dbg("📘 [결과] 조정 후 원서 언어(h) =", lang_h)

    return (lang_h if lang_h in ALLOWED_CODES else "und") or "und"

def get_kormarc_tags(item, detail):
    """KORMARC 041/546 태그 생성"""
    item = item or {}
    detail = detail or {}

    title = item.get("title", "") or ""
    publisher = item.get("publisher", "") or ""
    author = item.get("author", "") or ""

    subinfo = (item.get("subInfo") or {}) or {}
    original_title = subinfo.get("originalTitle", "") or ""
    original_title = html.unescape(original_title)

    if not original_title:
        original_title = detail.get("original_title", "") or ""

    subject_lang = detail.get("subject_lang")
    category_text = item.get("categoryText", "") or detail.get("category_text", "") or ""

    try:
        # $a: 본문 언어
        lang_a = detect_language(title)
        dbg("📘 [DEBUG] 규칙 기반 1차 lang_a =", lang_a)

        if is_domestic_category(category_text):
            dbg("📘 [판정] 카테고리에 '국내도서' 감지 → $a=kor(강한 가드)")
            lang_a = "kor"

        if lang_a in ("und", "eng"):
            dbg("📘 [설명] und/eng → GPT 보조로 본문 언어 재판정…")
            gpt_a = gpt_guess_main_lang(title, category_text, publisher)
            dbg(f"📘 [설명] GPT 판단 lang_a = {gpt_a}")
            if gpt_a in ALLOWED_CODES:
                lang_a = gpt_a
            else:
                lang_a = "und"

        # $h: 원저 언어
        dbg("📘 [DEBUG] 원제 감지됨:", bool(original_title), "| 원제:", original_title or "(없음)")
        dbg("📘 [DEBUG] 카테고리/크롤링 기반 lang_h 후보 =", subject_lang or "(없음)")

        lang_h = determine_h_language(
            title=title,
            original_title=original_title,
            category_text=category_text,
            publisher=publisher,
            author=author,
            subject_lang=subject_lang,
        )
        dbg("📘 [결과] 최종 원서 언어(h) =", lang_h)

        # 태그 조합
        if lang_h and lang_h != lang_a and lang_h != "und":
            tag_041 = f"041 $a{lang_a} $h{lang_h}"
        else:
            tag_041 = f"041 $a{lang_a}"

        if "$h" not in tag_041:
            return None, None, original_title

        tag_546 = generate_546_from_041_kormarc(tag_041)
        return tag_041, tag_546, original_title

    except Exception as e:
        dbg(f"📕 [ERROR] get_kormarc_tags 예외 발생: {e}")
        return f"📕 예외 발생: {e}", "", original_title

def generate_546_from_041_kormarc(marc_041: str) -> str:
    """041에서 546 자동 생성"""
    a_codes, h_code = [], None
    for part in marc_041.split():
        if part.startswith("$a"):
            a_codes.append(part[2:])
        elif part.startswith("$h"):
            h_code = part[2:]
    
    if len(a_codes) == 1:
        a_lang = ISDS_LANGUAGE_CODES.get(a_codes[0], "알 수 없음")
        if h_code:
            h_lang = ISDS_LANGUAGE_CODES.get(h_code, "알 수 없음")
            return f"{h_lang} 원작을 {a_lang}로 번역"
        else:
            return f"{a_lang}로 씀"
    elif len(a_codes) > 1:
        langs = [ISDS_LANGUAGE_CODES.get(code, "알 수 없음") for code in a_codes]
        return f"{'、'.join(langs)} 병기"
    return ""

def _as_mrk_041(tag_041: str | None) -> str | None:
    """041을 MRK 형식으로"""
    if not tag_041:
        return None
    s = tag_041.strip()
    s = re.sub(r"^=?\s*041\s*", "", s)
    s = re.sub(r"\s+", "", s)
    if not s.startswith("$a"):
        return None
    return f"=041  1\\{s}"

def _as_mrk_546(tag_546_text: str | None) -> str | None:
    """546을 MRK 형식으로"""
    if not tag_546_text:
        return None
    t = tag_546_text.strip()
    if not t:
        return None
    if t.startswith("=546"):
        return t
    if t.startswith("$a"):
        return f"=546  \\\\{t}"
    return f"=546  \\\\$a{t}"

# ===== 웹 크롤링 함수 (Aladin)
def crawl_aladin_fallback(isbn13):
    """Aladin 웹 크롤링으로 추가 정보 수집"""
    url = f"https://www.aladin.co.kr/shop/wproduct.aspx?ISBN={isbn13}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        original = soup.select_one("div.info_original")
        lang_info = soup.select_one("div.conts_info_list1")

        category_text = ""
        categories = soup.select("div.conts_info_list2 li")
        for cat in categories:
            category_text += cat.get_text(separator=" ", strip=True) + " "

        detected_lang = ""
        if lang_info and "언어" in lang_info.text:
            if "Japanese" in lang_info.text:
                detected_lang = "jpn"
            elif "Chinese" in lang_info.text:
                detected_lang = "chi"
            elif "English" in lang_info.text:
                detected_lang = "eng"

        original_title = original.text.strip() if original else ""

        # 원어 저자명 추출
        original_author = ""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue

            if not (isinstance(data, dict) and data.get("@type") == "Book"):
                continue

            author = data.get("author")
            name_field = ""

            if isinstance(author, dict):
                name_field = author.get("name", "") or ""
            elif isinstance(author, list):
                names = []
                for a in author:
                    if isinstance(a, dict):
                        nm = a.get("name", "")
                        if nm:
                            names.append(nm)
                name_field = ", ".join(names)

            if not name_field:
                continue

            parts = [p.strip() for p in name_field.split(",") if p.strip()]
            original_author = ""

            if len(parts) >= 2:
                cand = parts[1]
                if not re.search(r"[가-힣]", cand):
                    original_author = cand

        return {
            "original_title": original_title,
            "original_author": original_author,
            "subject_lang": detect_language_from_category(category_text) or detected_lang,
            "category_text": category_text,
        }

    except Exception as e:
        dbg_err(f"❌ 크롤링 중 오류 발생: {e}")
        return {}

def fetch_aladin_data(isbn13: str):
    """Aladin API + 크롤링 통합"""
    isbn13 = isbn13.strip().replace("-", "")

    try:
        api_data = aladin_lookup_by_api(isbn13, ALADIN_TTB_KEY)
    except Exception as e:
        dbg(f"[ERROR] 알라딘 API 실패: {e}")
        api_data = None

    try:
        detail = crawl_aladin_fallback(isbn13)
    except Exception as e:
        dbg(f"[ERROR] 알라딘 상세 크롤링 실패: {e}")
        detail = {}
    
    return {"api": (api_data.extra if api_data else {}), "detail": detail}

def aladin_lookup_by_api(isbn13: str, ttbkey: str):
    """Aladin API 호출"""
    if not ttbkey:
        return None
    params = {
        "ttbkey": ttbkey,
        "itemIdType": "ISBN13",
        "ItemId": isbn13,
        "output": "js",
        "Version": "20131101",
        "OptResult": "authors,categoryName,fulldescription,toc,packaging,ratings"
    }
    try:
        r = requests.get("https://www.aladin.co.kr/ttb/api/ItemLookUp.aspx", 
                        params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("item", [])
        if not items:
            return None
        it = items[0]
        return BookInfo(
            title=clean_text(it.get("title")),
            author=clean_text(it.get("author")),
            pub_date=clean_text(it.get("pubDate")),
            publisher=clean_text(it.get("publisher")),
            isbn13=clean_text(it.get("isbn13")) or isbn13,
            category=clean_text(it.get("categoryName")),
            description=clean_text(it.get("fulldescription")) or clean_text(it.get("description")),
            toc=clean_text(it.get("toc")),
            extra=it,
        )
    except Exception as e:
        dbg(f"알라딘 API 호출 예외: {e}")
        return None

# ===== 저자 관련 함수
ROLE_ALIASES = {
    "지은이":"author","저자":"author","글":"author","글쓴이":"author","집필":"author","원작":"author",
    "지음":"author","글작가":"author","스토리":"author",
    "옮긴이":"translator","옮김":"translator","역자":"translator","역":"translator","번역":"translator","역주":"translator","공역":"translator",
    "그림":"illustrator","그린이":"illustrator","삽화":"illustrator","일러스트":"illustrator","만화":"illustrator","작화":"illustrator","채색":"illustrator",
    "엮음":"editor","엮은이":"editor","편집":"editor","편":"editor","편저":"editor","편집자":"editor",
    "author":"author","writer":"author","story":"author",
    "translator":"translator","trans":"translator","translated":"translator",
    "illustrator":"illustrator","illus.":"illustrator","artist":"illustrator",
    "editor":"editor","ed.":"editor",
}

def normalize_role(token: str) -> str:
    """역할명 정규화"""
    if not token:
        return "other"
    t = re.sub(r"[()\[\]\s{}]", "", token.strip().lower())
    parts = re.split(r"[·/・]", t)
    cats = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in ROLE_ALIASES:
            cats.add(ROLE_ALIASES[p])
        else:
            for key, val in ROLE_ALIASES.items():
                if key in p:
                    cats.add(val)
                    break
            else:
                cats.add("other")
    
    for pref in ("translator", "author", "illustrator", "editor"):
        if pref in cats:
            return pref
    return "other"

def strip_tail_role(name: str) -> tuple[str, str]:
    m = re.search(r"\(([^)]+)\)\s*$", name.strip())
    if not m:
        return name.strip(), "other"
    base = name[:m.start()].strip()
    return base, normalize_role(m.group(1))

def split_names(chunk: str) -> list[str]:
    if not chunk: return []
    chunk = re.sub(r"^\s*\([^)]*\)\s*", "", chunk.strip())
    parts = re.split(rf"\s*[,/&·]\s*", chunk)
    return [p.strip() for p in parts if p and p.strip()]

def extract_people_from_aladin(item: dict) -> dict:
    """Aladin에서 사람 정보 추출"""
    res = {"author": [], "translator": [], "illustrator": [], "editor": [], "other": []}
    if not item:
        return res

    sub = (item.get("subInfo") or {})
    arr = sub.get("authors")

    if isinstance(arr, list) and arr:
        for a in arr:
            name = (a.get("authorName") or a.get("name") or "").strip()
            typ  = (a.get("authorTypeName") or a.get("authorType") or "").strip()
            if not name:
                continue

            typ_compact = re.sub(r"\s+", "", typ or "")
            if any(kw in typ_compact for kw in ("기획", "기획·구성", "기획/구성")):
                continue

            m = re.search(r"\(([^)]*)\)", name)
            if m and ("기획" in m.group(1)):
                continue

            base, tail = strip_tail_role(name)

            cat = normalize_role(typ)
            if tail != "other":
                cat = tail

            if cat == "other":
                continue

            res.setdefault(cat, []).append(base)

        for k in list(res.keys()):
            res[k] = list(set(res[k]))
        return res

    # fallback: 문자열 파싱
    parsed = parse_people_flexible(item.get("author") or "")
    for k, lst in parsed.items():
        if k in res:
            res[k].extend(lst)

    for k in list(res.keys()):
        res[k] = list(set(res[k]))

    return res

def parse_people_flexible(author_str: str) -> dict:
    """사람 정보 유연한 파싱"""
    out = defaultdict(list)
    if not author_str:
        return out

    role_pattern = r"(\([^)]*\)|지은이|저자|글|글쓴이|집필|원작|엮음|엮은이|지음|글작가|스토리|옮긴이|옮김|역자|역|번역|역주|공역|그림|그린|삽화|일러스[...]"
    tokens = [t.strip() for t in re.split(role_pattern, author_str) if t and t.strip()]

    current = "other"
    pending = []
    last_names = []
    last_assigned_to = None
    seen_real_role = False

    def _assign(lst, cat):
        for x in lst:
            out[cat].append(x)

    for tok in tokens:
        role_cat = normalize_role(tok)

        if role_cat == "other":
            stripped = tok.strip()
            if stripped.startswith("(") and stripped.endswith(")"):
                inner = stripped[1:-1].strip()
                if any(kw in inner for kw in ("기획", "기획·구성", "기획/구성", "구성", "해설")):
                    continue

        if role_cat != "other":
            if role_cat in ("author", "translator", "illustrator", "editor"):
                seen_real_role = True

            if pending:
                _assign(pending, role_cat)
                pending.clear()
                last_names = []
                last_assigned_to = None
            else:
                if last_names and last_assigned_to:
                    for x in last_names:
                        try:
                            out[last_assigned_to].remove(x)
                        except ValueError:
                            pass
                    _assign(last_names, role_cat)
                    last_names = []
                    last_assigned_to = None

            current = role_cat
            continue

        names = split_names(tok)
        if not names:
            continue

        direct = []
        for raw in names:
            base, tail = strip_tail_role(raw)
            if tail != "other":
                out[tail].append(base)
                direct.append(base)

        remain = [n for n in names if n not in direct]
        if not remain:
            last_names = direct
            last_assigned_to = None
            continue

        if current != "other":
            _assign(remain, current)
            last_names = remain[:]
            last_assigned_to = current
        else:
            pending.extend(remain)
            last_names = remain[:]
            last_assigned_to = None

    if pending:
        if seen_real_role:
            _assign(pending, "author")
        # else: 역할 없는 이름은 버림

    for k, arr in out.items():
        seen = set(); uniq=[]
        for x in arr:
            if x not in seen:
                seen.add(x); uniq.append(x)
        out[k] = uniq

    return out

def build_700_from_people(people: dict, reorder_fn=None, aladin_item=None) -> list[str]:
    """700 필드 생성"""
    lines = []
    authors = people.get("author", [])
    edtrs   = people.get("editor", [])
    illus   = people.get("illustrator", [])
    trans   = people.get("translator", [])

    def reorder(name):
        return reorder_fn(name, aladin_item=aladin_item) if reorder_fn else name

    for a in authors:
        lines.append(f"=700  1\\$a{reorder(a)}")

    for e in edtrs:
        lines.append(f"=700  1\\$a{reorder(e)}")

    for i in illus:
        lines.append(f"=700  1\\$a{reorder(i)}")

    for t in trans:
        lines.append(f"=700  1\\$a{reorder(t)}")

    return lines

def _dedup(seq):
    seen=set(); out=[]
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def extract_primary_author_ko_from_aladin(item: dict) -> str:
    """Aladin에서 첫 저자 추출"""
    if not item:
        return ""

    sub = (item.get("subInfo") or {})
    authors_list = sub.get("authors")

    if isinstance(authors_list, list) and authors_list:
        for a in authors_list:
            atype = (a.get("authorTypeName") or a.get("authorType") or "").strip()
            nm = (a.get("authorName") or a.get("name") or "").strip()
            if not nm:
                continue
            if ("지은이" in atype) or ("저자" in atype):
                return _strip_role_suffix(nm)
        first = (authors_list[0].get("authorName") or authors_list[0].get("name") or "").strip()
        return _strip_role_suffix(first)

    author_str = (item.get("author") or "").strip()
    if author_str:
        first_seg = author_str.split(",")[0]
        first = re.sub(r"\s*\(.*?\)\s*$", "", first_seg).strip()
        first = _strip_role_suffix(first)
        return first

    return ""

_ROLE_SUFFIX_RX = re.compile(r"\s*(지음|지은이|엮음|옮김|역|편|글|그림)\s*$")

def _strip_role_suffix(s: str) -> str:
    return _ROLE_SUFFIX_RX.sub("", (s or "").strip())

# ===== 245/246/260/300 관련 함수
DELIMS = [": ", " : ", ":", " - ", " — ", "–", "—", "-", " · ", "·", "; ", ";", " | ", "|", "/"]

def _strip_trailing_paren_notes(s: str) -> str:
    return _TRAIL_PAREN_PAT.sub("", s).strip(" .,/;:-—·|")

def _clean_piece(s: str) -> str:
    if not s:
        return ""
    s = _compat_normalize(s)
    s = _strip_trailing_paren_notes(s)
    s = s.strip(" .,/;:-—·|")
    return s

def _find_top_level_split(text: str, delims=DELIMS):
    pairs = {"(": ")", "[": "]", "{": "}", "〈": "〉", "《": "》", "「": "」", "『": "』", """: """, "'": "'", "«": "»"}
    opens, closes = set(pairs), {v: k for k, v in pairs.items()}
    stack, i, L = [], 0, len(text)
    while i < L:
        ch = text[i]
        if ch in opens:
            stack.append(ch); i += 1; continue
        if ch in closes:
            if stack and pairs.get(stack[-1]) == ch: stack.pop()
            i += 1; continue
        if not stack:
            for d in delims:
                if text.startswith(d, i):
                    return i, d
        i += 1
    return None

def split_title_only_for_245(title: str):
    if not title:
        return "", None
    t = _compat_normalize(title)
    hit = _find_top_level_split(t, DELIMS)
    if not hit:
        return _clean_piece(t), None
    idx, delim = hit
    left, right = t[:idx], t[idx + len(delim):]
    return _clean_piece(left), (_clean_piece(right) or None)

def extract_245_from_aladin_item(item: dict, collapse_a_spaces: bool = True):
    """245 필드 추출"""
    raw_title = (item.get("title") or "")
    raw_sub   = (item.get("subInfo", {}) or {}).get("subTitle") or ""

    t = _compat_normalize(raw_title)
    s = _clean_piece(raw_sub)
    if s:
        tail = [f" : {s}", f": {s}", f":{s}", f" - {s}", f"- {s}", f"-{s}"]
        t_removed = t
        for pat in tail:
            if t_removed.endswith(pat):
                t_removed = t_removed[: -len(pat)]
                break
        a0, b = _clean_piece(t_removed) or _clean_piece(t), s
    else:
        a0, b = split_title_only_for_245(t)

    a_base = a0
    n = ""

    a_out = a_base.replace(" ", "") if collapse_a_spaces else a_base

    line = f"=245  00$a{a_out}"
    if n:
        line += (" " if a_out.endswith(".") else " .")
        line += f"$n{n}"
    if b:
        line += f" :$b{b}"

    return {"ind1":"0","ind2":"0","a":a_out,"b":b,"n":n,"mrk":line}

def build_246_from_aladin_item(item: dict) -> str | None:
    """246 필드 생성"""
    if not item:
        return None
    orig = ((item.get("subInfo") or {}).get("originalTitle") or "").strip()
    orig = _clean_piece(orig)

    _YEAR_OR_EDITION_PAREN_PAT = re.compile(
        r"""
        \s*\(\s*(?:
           \d{3,4}\s*년?
          |rev(?:ised)?\.?\s*ed\.?
          |(?:\d+(?:st|nd|rd|th)\s*ed\.?)
          |edition|ed\.?
          |제?\s*\d+\s*판
          |개정(?:증보)?판?
          |증보판|초판|신판|보급판
        )[^()\[\]]*\)\s*$
        """,
        re.IGNORECASE | re.VERBOSE
    )
    orig = _YEAR_OR_EDITION_PAREN_PAT.sub("", orig).strip()

    if orig:
        return f"=246  19$a{orig}"
    return None

def parse_245_a_n(marc245_line: str) -> tuple[str, str | None]:
    """245에서 $a와 $n 추출"""
    if not marc245_line:
        return "", None

    m_a = re.search(r"=245\s+\d{2}\$a(.*?)(?=\$[a-z]|$)", marc245_line)
    a_out = (m_a.group(1).strip() if m_a else "").strip()

    a_out = re.sub(r"\s+([:;,./])", r"\1", a_out)
    a_out = re.sub(r"[.:;,/]\s*$", "", a_out).strip()

    m_n = re.search(r"\$n(.*?)(?=\$[a-z]|$)", marc245_line)
    n_val = m_n.group(1).strip() if m_n else None

    return a_out, n_val if n_val else None

def build_245_with_people_from_sources(aladin_item: dict, nlk_author_raw: str, prefer="aladin") -> str:
    """245 필드 + 책임표시 생성"""
    tb = extract_245_from_aladin_item(aladin_item, collapse_a_spaces=False)
    a_out, b, n = tb["a"], tb.get("b"), tb.get("n")

    line = f"=245  00$a{a_out}"
    if n:
        line += (" " if a_out.endswith(".") else " .") + f"$n{n}"
    if b:
        line += f" :$b{b}"

    people = extract_people_from_aladin(aladin_item) if (prefer == "aladin" and aladin_item) else None
    authors = (people or {}).get("author", [])
    edtrs   = (people or {}).get("editor", [])
    illus   = (people or {}).get("illustrator", [])
    trans   = (people or {}).get("translator", [])

    if not (authors or trans or illus or edtrs):
        parsed = parse_people_flexible(nlk_author_raw or "")
        authors = parsed.get("author", [])
        edtrs   = parsed.get("editor", [])
        illus   = parsed.get("illustrator", [])
        trans   = parsed.get("translator", [])

    def clean_name_list(names, remove_words):
        result = []
        for name in names:
            clean = name
            for w in remove_words:
                clean = clean.replace(w, "")
            result.append(clean.strip())
        return result

    authors = clean_name_list(authors, ["(지은이)", "(저자)"])
    edtrs   = clean_name_list(edtrs, ["(엮은이)", "(편집)", "(편저)"])
    illus   = clean_name_list(illus, ["(그림)", "(그린이)", "(일러스트)", "(삽화)"])
    trans   = clean_name_list(trans, ["(옮긴이)", "(역자)", "(번역)"])

    parts = []

    if authors:
        seg = []
        head, tail = authors[0], authors[1:]
        seg.append(f"$d{head}")
        for t in tail:
            seg.append(f"$e{t}")
        parts.append(", ".join(seg) + " 지음")

    if edtrs:
        seg = []
        head, tail = edtrs[0], edtrs[1:]
        seg.append(f"$e{head}")
        for t in tail:
            seg.append(f"$e{t}")
        parts.append(", ".join(seg) + " 엮음")

    if illus:
        seg = []
        head, tail = illus[0], illus[1:]
        seg.append(f"$e{head}")
        for t in tail:
            seg.append(f"$e{t}")
        parts.append(", ".join(seg) + " 그림")

    if trans:
        seg = []
        head, tail = trans[0], trans[1:]
        seg.append(f"$e{head}")
        for t in tail:
            seg.append(f"$e{t}")
        parts.append(", ".join(seg) + " 옮김")

    if parts:
        line += " /" + " ; ".join(parts)

    return line

# ===== 008 필드 관련
def build_008_kormarc_bk(
    date_entered, date1, country3, lang3,
    date2="", illus4="", has_index="0", lit_form=" ", bio=" ",
    type_of_date="s", modified_record=" ", cataloging_src="a",
):
    """008 필드 생성"""
    def pad(s, n, fill=" "):
        s = "" if s is None else str(s)
        return (s[:n] + fill * n)[:n]

    if len(date_entered) != 6 or not date_entered.isdigit():
        raise ValueError("date_entered는 YYMMDD 6자리 숫자여야 합니다.")
    if len(date1) != 4:
        raise ValueError("date1은 4자리여야 합니다.")

    body = "".join([
        date_entered,
        pad(type_of_date,1),
        date1,
        pad(date2,4),
        pad(country3,3),
        pad(illus4,4),
        " " * 4,
        " " * 2,
        pad(modified_record,1),
        "0", "0",
        has_index if has_index in ("0","1") else "0",
        pad(cataloging_src,1),
        pad(lit_form,1),
        pad(bio,1),
        pad(lang3,3),
        " " * 2
    ])
    if len(body) != 40:
        raise AssertionError(f"008 length != 40: {len(body)}")
    return body

def extract_year_from_aladin_pubdate(pubdate_str: str) -> str:
    """발행연도 추출"""
    m = re.search(r"(19|20)\d{2}", pubdate_str or "")
    return m.group(0) if m else "19uu"

def guess_country3_from_place(place_str: str) -> str:
    """발행지 → country3 코드 추론"""
    if not place_str:
        return COUNTRY_FIXED
    for key, code in KR_REGION_TO_CODE.items():
        if key in place_str:
            return code
    return COUNTRY_FIXED

def detect_illus4(text: str) -> str:
    """삽화 여부 감지"""
    keys = []
    if re.search(r"삽화|삽도|도해|일러스트|일러스트레이션|그림|illustration", text, re.I): keys.append("a")
    if re.search(r"도표|표|차트|그래프|chart|graph", text, re.I): keys.append("d")
    if re.search(r"사진|포토|화보|photo|photograph|컬러사진|칼라사진", text, re.I): keys.append("o")
    out = []
    for k in keys:
        if k not in out:
            out.append(k)
    return "".join(out)[:4]

def detect_index(text: str) -> str:
    """색인 여부 감지"""
    return "1" if re.search(r"색인|찾아보기|인명색인|사항색인|index", text, re.I) else "0"

def detect_lit_form(title: str, category: str, extra_text: str = "") -> str:
    """문학형식 감지"""
    blob = f"{title} {category} {extra_text}"
    if re.search(r"서간집|편지|서간문|letters?", blob, re.I): return "i"
    if re.search(r"기행|여행기|여행 에세이|일기|수기|diary|travel", blob, re.I): return "m"
    if re.search(r"시집|산문시|poem|poetry", blob, re.I): return "p"
    if re.search(r"소설|장편|중단편|novel|fiction", blob, re.I): return "f"
    if re.search(r"에세이|수필|essay", blob, re.I): return "e"
    return " "

def detect_bio(text: str) -> str:
    """전기 형식 감지"""
    if re.search(r"자서전|회고록|autobiograph", text, re.I): return "a"
    if re.search(r"전기|평전|인물 평전|biograph", text, re.I): return "b"
    if re.search(r"전기적|자전적|회고|회상", text): return "d"
    return " "

def _is_unknown_place(s: str | None) -> bool:
    if not s:
        return False
    t = s.strip()
    t_no_sp = t.replace(" ", "")
    lower = t.lower()
    return (
        "미상" in t or
        "미상" in t_no_sp or
        "unknown" in lower or
        "place unknown" in lower
    )

def build_008_from_isbn(
    isbn: str,
    *,
    aladin_pubdate: str = "",
    aladin_title: str = "",
    aladin_category: str = "",
    aladin_desc: str = "",
    aladin_toc: str = "",
    source_300_place: str = "",
    override_country3: str = None,
    override_lang3: str = None,
    cataloging_src: str = "a",
):
    """008 필드 생성"""
    today  = datetime.datetime.now().strftime("%y%m%d")
    date1  = extract_year_from_aladin_pubdate(aladin_pubdate)

    if override_country3:
        country3 = override_country3
    elif source_300_place:
        if _is_unknown_place(source_300_place):
            CURRENT_DEBUG_LINES.append(f"[008] 발행지 미상 감지")
            country3 = "   "
        else:
            guessed = guess_country3_from_place(source_300_place)
            country3 = guessed if guessed else COUNTRY_FIXED
    else:
        country3 = COUNTRY_FIXED

    lang3 = override_lang3 or LANG_FIXED

    bigtext = " ".join([aladin_title or "", aladin_desc or "", aladin_toc or ""])
    illus4    = detect_illus4(bigtext)
    has_index = detect_index(bigtext)
    lit_form  = detect_lit_form(aladin_title or "", aladin_category or "", bigtext)
    bio       = detect_bio(bigtext)

    return build_008_kormarc_bk(
        date_entered=today,
        date1=date1,
        country3=country3,
        lang3=lang3,
        illus4=illus4,
        has_index=has_index,
        lit_form=lit_form,
        bio=bio,
        cataloging_src=cataloging_src,
    )

def _lang3_from_tag041(tag_041: str | None) -> str | None:
    """041에서 $a 추출"""
    if not tag_041: return None
    m = re.search(r"\$a([a-z]{3})", tag_041, flags=re.I)
    return m.group(1).lower() if m else None

# ===== 020/049 관련
def _build_020_from_item_and_nlk(isbn: str, item: dict) -> str:
    """020 필드 생성"""
    price = str((item or {}).get("priceStandard", "") or "").strip()

    try:
        nlk_extra = fetch_additional_code_from_nlk(isbn) or {}
        add_code = nlk_extra.get("add_code", "")
        price_from_nlk = nlk_extra.get("price", "")
    except Exception:
        add_code = ""
        price_from_nlk = ""

    final_price = price or price_from_nlk

    parts = [f"=020  \\\\$a{isbn}"]
    if add_code:
        parts.append(f"$g{add_code}")
    if final_price:
        parts.append(f":$c{final_price}")

    return "".join(parts)

def build_049(reg_mark: str, reg_no: str, copy_symbol: str) -> str:
    """049 필드 생성"""
    reg_mark = (reg_mark or "").strip()
    reg_no = (reg_no or "").strip()
    copy_symbol = (copy_symbol or "").strip()

    if not (reg_mark or reg_no):
        field = "=049  0\\$lEMQ999999"
        if copy_symbol:
            field += f"$f{copy_symbol}"
        return field

    field = f"=049  0\\$l{reg_mark}{reg_no}"
    if copy_symbol:
        field += f"$f{copy_symbol}"
    return field

@st.cache_data(ttl=24*3600)
def fetch_additional_code_from_nlk(isbn: str) -> dict:
    """NLK에서 부가기호 조회"""
    attempts = [
        "https://seoji.nl.go.kr/landingPage/SearchApi.do",
        "https://www.nl.go.kr/seoji/SearchApi.do",
        "http://seoji.nl.go.kr/landingPage/SearchApi.do",
        "http://www.nl.go.kr/seoji/SearchApi.do",
    ]
    params = {
        "cert_key": NLK_CERT_KEY,
        "result_style": "json",
        "page_no": 1,
        "page_size": 1,
        "isbn": isbn.strip().replace("-", ""),
    }

    for base in attempts:
        try:
            r = SESSION.get(base, params=params, timeout=(5, 10))
            r.raise_for_status()
            j = r.json()
            doc = None
            if isinstance(j, dict):
                if "docs" in j and isinstance(j["docs"], list) and j["docs"]:
                    doc = j["docs"][0]
                elif "doc" in j and isinstance(j["doc"], list) and j["doc"]:
                    doc = j["doc"][0]
            if not doc:
                continue

            add_code = (doc.get("EA_ADD_CODE") or "").strip()
            set_isbn = (doc.get("SET_ISBN") or "").strip()
            price = (doc.get("PRE_PRICE") or "").strip()

            return {
                "add_code": add_code,
                "set_isbn": set_isbn,
                "price": price,
            }

        except Exception:
            continue

    return {
        "add_code": "",
        "set_isbn": "",
        "set_title": "",
        "price": "",
    }

# ===== 653 필드 관련
def _build_forbidden_set(title: str, authors: str) -> set:
    """제외어 세트 생성"""
    t_norm = _norm(title)
    a_norm = _norm(authors)
    forb = set()
    if t_norm:
        forb.update(t_norm.split())
        forb.add(t_norm.replace(" ", ""))
    if a_norm:
        forb.update(a_norm.split())
        forb.add(a_norm.replace(" ", ""))
    return {f for f in forb if f and len(f) >= 2}

def _should_keep_keyword(kw: str, forbidden: set) -> bool:
    """키워드 필터"""
    n = _norm(kw)
    if not n or len(n.replace(" ", "")) < 2:
        return False
    for tok in forbidden:
        if tok in n or n in tok:
            return False
    return True

def generate_653_with_gpt(category, title, authors, description, toc, max_keywords=7):
    """653 필드 생성 (GPT 기반)"""
    import re

    parts = [p.strip() for p in (category or "").split(">") if p.strip()]
    cat_tail = " ".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "")

    forbidden = _build_forbidden_set(title, authors)
    forbidden_list = ", ".join(sorted(forbidden)) or "(없음)"

    system_msg = {
        "role": "system",
        "content": (
            "당신은 KORMARC 작성 경험이 풍부한 도서관 메타데이터 전문가입니다. "
            "주어진 분류 정보, 설명, 목차를 바탕으로 'MARC 653 자유주제어'를 도출합니다.\n\n"
            "원칙\n"
            "- 653은 '검색·발견' 효용을 높이는 명사 중심 주제어로 구성하되, "
            "**모든 주제어는 붙여쓰기 형태(공백 없음)**로 작성합니다.\n"
            "- 서명/저자에서 나온 단어와 불필요한 표현(연구, 개론, 방법 등)을 제외합니다.\n"
            "- **추상·평가·메타 표현은 절대 사용하지 마세요.**\n"
            f"출력 형식: `$a키워드1 $a키워드2 ...` (최대 {max_keywords}개)\n"
            "설명, 번호, 괄호, 줄바꿈 금지."
        )
    }

    user_msg = {
        "role": "user",
        "content": (
            f"아래 정보를 바탕으로 최대 {max_keywords}개의 MARC 653 주제어를 한 줄로 출력해 주세요.\n\n"
            f"- 분류: {category}\n"
            f"- 제목: {title}\n"
            f"- 저자: {authors}\n"
            f"- 설명: {description}\n"
            f"- 목차: {toc}\n"
            f"- 제외어: {forbidden_list}\n\n"
            "지시사항:\n"
            "1) 제목·저자 유래 단어는 포함 금지\n"
            "2) 분류·설명·목차에서 핵심 주제 명사(2~6글자) 중심 선택\n"
            "3) **붙여쓰기**로 작성 (공백 제거)\n"
            "4) 일반적 표현(연구, 방법, 사례) 제외\n"
            "5) 출력 형식만: `$a키워드1 $a키워드2 ...`"
        )
    }

    try:
        resp = _client.chat.completions.create(
            model="gpt-4o",
            messages=[system_msg, user_msg],
            temperature=0.2,
            max_tokens=180,
        )
        raw = (resp.choices[0].message.content or "").strip()

        pattern = re.compile(r"\$a(.*?)(?=(?:\$a|$))", re.DOTALL)
        kws = [m.group(1).strip() for m in pattern.finditer(raw)]
        if not kws:
            tmp = re.split(r"[,\n;|/·]", raw)
            kws = [t.strip().lstrip("$a") for t in tmp if t.strip()]

        kws = [kw.replace(" ", "") for kw in kws if kw]
        kws = [kw for kw in kws if _should_keep_keyword(kw, forbidden)]

        seen = set(); uniq = []
        for kw in kws:
            n = _norm(kw)
            if n not in seen:
                seen.add(n); uniq.append(kw)

        uniq = uniq[:max_keywords]
        return "".join(f"$a{kw}" for kw in uniq)

    except Exception as e:
        st.warning(f"⚠️ 653 주제어 생성 실패: {e}")
        return None

def _build_653_via_gpt(item: dict) -> str | None:
    """653 한 줄 생성"""
    title = (item or {}).get("title","") or ""
    category = (item or {}).get("categoryName","") or ""
    raw_author = (item or {}).get("author","") or ""
    desc = (item or {}).get("description","") or ""
    toc  = ((item or {}).get("subInfo",{}) or {}).get("toc","") or ""

    kwline = generate_653_with_gpt(
        category=category,
        title=title,
        authors=_clean_author_str(raw_author),
        description=desc,
        toc=toc,
        max_keywords=7
    )
    return f"=653  \\\\{kwline.replace(' ', '')}" if kwline else None

def _parse_653_keywords(tag_653: str | None) -> list[str]:
    """653 파싱"""
    if not tag_653:
        return []
    s = tag_653.strip()
    s = re.sub(r"^=653\s+\\\\", "", s)
    kws = []
    for m in re.finditer(r"\$a([^$]+)", s):
        w = (m.group(1) or "").strip()
        if w:
            kws.append(w)

    seen, out = set(), []
    for w in kws:
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= 7:
            break
    return out

# ===== KDC/056 관련
@dataclass
class BookInfo:
    title: str = ""
    author: str = ""
    pub_date: str = ""
    publisher: str = ""
    isbn13: str = ""
    category: str = ""
    description: str = ""
    toc: str = ""
    extra: Optional[Dict[str, Any]] = None

def first_match_number(text: str) -> Optional[str]:
    """KDC 숫자 추출"""
    if not text:
        return None
    m = re.search(r"\b([0-9]{1,3}(?:\.[0-9]+)?)\b", text)
    return m.group(1) if m else None

def normalize_kdc_3digit(code: Optional[str]) -> Optional[str]:
    """KDC 정규화 (3자리)"""
    if not code:
        return None
    m = re.search(r"(\d{1,3})", code)
    return m.group(1) if m else None

def ask_llm_for_kdc(book: BookInfo, api_key: str, model: str = DEFAULT_MODEL,
                    keywords_hint: list[str] | None = None) -> Optional[str]:
    """KDC 판단 (LLM)"""
    if model is None:
        try:
            model = (st.secrets.get("openai", {}) or {}).get("model", "")
        except Exception:
            model = ""
        if not model:
            model = "gpt-4o-mini"

    def clip(s: str, n: int) -> str:
        if not s:
            return ""
        s = str(s).strip()
        return s if len(s) <= n else s[:n] + "…"

    title = clip(book.title, 160)
    author = clip(book.author, 120)
    category = clip(book.category, 160)
    description = clip(book.description, 1200)
    toc = clip(book.toc, 1200)

    payload = {
        "title": title,
        "author": author,
        "publisher": book.publisher,
        "pub_date": book.pub_date,
        "isbn13": book.isbn13,
        "category": category,
        "description": description,
        "toc": toc,
    }

    sys_prompt = (
        "너는 한국십진분류법(KDC) 전문가이자 공공도서관 분류 사서이다.\n"
        "입력된 도서 정보를 바탕으로 이 책의 **주제 중심 분류기호(KDC 번호)**를 한 줄로 판단하라.\n\n"
        "규칙:\n"
        "1. 반드시 **소수점 없이 3자리 정수만** 출력한다.\n"
        "2. 설명, 이유, 접두어 등은 출력하지 않는다.\n"
        "3. 한 책이 여러 주제를 다루더라도 **가장 중심되는 주제**를 선택한다.\n"
        "4. 확신이 없으면 **정확히 '직접분류추천'**만 출력한다.\n\n"
        "출력 예시: 823 / 813 / 325 / 181 / 직접분류추천"
    )

    hint_str = ", ".join(keywords_hint or [])
    user_prompt = (
        "아래 도서 정보(JSON)를 참고하여 **KDC 분류기호를 소수점 없이 3자리 정수로 한 줄**만 출력하라. "
        "만약 확실히 판단하기 어렵다면 **정확히 '직접분류추천'**만 출력하라.\n\n"
        f"※ 참고용 키워드 힌트(653): {hint_str or '(없음)'}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "출력 예시: 823 / 813 / 325 / 181 / 직접분류추천"
    )

    def _parse_response(s: str) -> Optional[str]:
        if not s:
            return None
        s = s.strip()
        if "직접분류추천" in s:
            return "직접분류추천"
        m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", s)
        if not m:
            return None
        whole = m.group(1)
        num = whole.zfill(3)
        if not re.fullmatch(r"\d{3}", num):
            return None
        return num

    def _call_llm(sys_p: str, user_p: str, max_tokens: int) -> Optional[str]:
        resp = requests.post(
            OPENAI_CHAT_COMPLETIONS,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": user_p},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        return _parse_response(text)

    try:
        code = _call_llm(sys_prompt, user_prompt, max_tokens=18)
        if code:
            return code
    except Exception as e:
        st.warning(f"1차 LLM 호출 경고: {e}")

    fb_sys = (
        "너는 KDC 제6판 기준 분류 사서다. "
        "가장 관련성이 높은 **3자리 정수**만 출력하라(예: 823, 325, 370). "
        "정확히 판단하기 어렵다면 **정확히 '직접분류추천'** 글자만 출력하라. "
        "다른 문자는 금지."
    )
    fb_user = f"도서 정보:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    try:
        code = _call_llm(fb_sys, fb_user, max_tokens=8)
        if code:
            return code
    except Exception as e:
        st.error(f"2차 LLM 호출 오류: {e}")

    return "직접분류추천"

def get_kdc_from_isbn(isbn13: str, ttbkey: Optional[str], openai_key: str, model: str,
                      keywords_hint: list[str] | None = None) -> Optional[str]:
    """ISBN → KDC"""
    info = aladin_lookup_by_api(isbn13, ttbkey) if ttbkey else None
    if not info:
        info = aladin_lookup_by_web(isbn13)
    if not info:
        st.warning("알라딘에서 도서 정보를 찾지 못했습니다.")
        return None
    code = ask_llm_for_kdc(info, api_key=openai_key, model=model, keywords_hint=keywords_hint)

    llm_meta = {
        "title": info.title,
        "author": info.author,
        "publisher": info.publisher,
        "pub_date": info.pub_date,
        "isbn13": info.isbn13,
        "category": info.category,
        "description": (info.description[:600] + "…") if info.description and len(info.description) > 600 else info.description,
        "toc": info.toc,
    }

    return code

def aladin_lookup_by_web(isbn13: str) -> Optional[BookInfo]:
    """Aladin 웹 스크레이핑"""
    try:
        params = {"SearchTarget": "Book", "SearchWord": f"isbn:{isbn13}"}
        sr = requests.get(ALADIN_SEARCH_URL.format(query=""), params=params, headers=HEADERS, timeout=15)
        sr.raise_for_status()
        soup = BeautifulSoup(sr.text, "html.parser")

        link_tag = soup.select_one("a.bo3")
        item_url = None
        if link_tag and link_tag.get("href"):
            item_url = urljoin("https://www.aladin.co.kr", link_tag["href"])

        if not item_url:
            m = re.search(r'href=[\'"](/shop/wproduct\.aspx\?ItemId=\d+[^\'"]*)[\'"]', sr.text, re.I)
            if m:
                item_url = urljoin("https://www.aladin.co.kr", html.unescape(m.group(1)))

        if not item_url:
            first_card = soup.select_one(".ss_book_box, .ss_book_list")
            if first_card:
                a = first_card.find("a", href=True)
                if a:
                    item_url = urljoin("https://www.aladin.co.kr", a["href"])

        if not item_url:
            st.warning("알라딘 검색 페이지에서 상품 링크를 찾지 못했습니다.")
            return None

        pr = requests.get(item_url, headers=HEADERS, timeout=15)
        pr.raise_for_status()
        psoup = BeautifulSoup(pr.text, "html.parser")

        og_title = psoup.select_one('meta[property="og:title"]')
        og_desc  = psoup.select_one('meta[property="og:description"]')
        title = clean_text(og_title["content"]) if og_title and og_title.has_attr("content") else ""
        desc  = clean_text(og_desc["content"]) if og_desc and og_desc.has_attr("content") else ""

        body_text = clean_text(psoup.get_text(" "))[:4000]
        description = desc or body_text

        author = ""
        publisher = ""
        pub_date = ""
        cat_text = ""

        info_box = psoup.select_one("#Ere_prod_allwrap, #Ere_prod_mconts_wrap, #Ere_prod_titlewrap")
        if info_box:
            text = clean_text(info_box.get_text(" "))
            m_author = re.search(r"(저자|지은이)\s*:\s*([^\|·/]+)", text)
            m_publisher = re.search(r"(출판사)\s*:\s*([^\|·/]+)", text)
            m_pubdate = re.search(r"(출간일|출판일)\s*:\s*([0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2})", text)
            if m_author:   author   = clean_text(m_author.group(2))
            if m_publisher: publisher = clean_text(m_publisher.group(2))
            if m_pubdate:  pub_date = clean_text(m_pubdate.group(2))

        crumbs = psoup.select(".location, .path, .breadcrumb")
        if crumbs:
            cat_text = clean_text(" > ".join(c.get_text(" ") for c in crumbs))

        return BookInfo(
            title=title,
            description=description,
            isbn13=isbn13,
            author=author,
            publisher=publisher,
            pub_date=pub_date,
            category=cat_text
        )
    except Exception as e:
        st.error(f"웹 스크레이핑 예외: {e}")
        return None

# ===== 260/300 관련
def build_260(place_display: str, publisher_name: str, pubyear: str):
    """260 필드 생성"""
    place = (place_display or "발행지 미상")
    pub = (publisher_name or "발행처 미상")
    year = (pubyear or "발행년 미상")
    return f"=260  \\\\$a{place} :$b{pub},$c{year}"

def build_pub_location_bundle(isbn, publisher_name_raw):
    """출판지 정보 통합"""
    debug = []
    try:
        publisher_data, region_data, imprint_data = load_publisher_db()
        debug.append("✓ 구글시트 DB 적재 성공")

        kpipa_full, kpipa_norm, err = get_publisher_name_from_isbn_kpipa(isbn)
        if err: debug.append(f"KPIPA 검색: {err}")

        rep_name, aliases = split_publisher_aliases(kpipa_full or publisher_name_raw or "")
        resolved_pub_for_search = rep_name or (publisher_name_raw or "").strip()
        debug.append(f"대표 출판사명 추정: {resolved_pub_for_search}")

        place_raw, msgs = search_publisher_location_with_alias(resolved_pub_for_search, publisher_data)
        debug += msgs
        source = "KPIPA_DB"

        if place_raw in ("출판지 미상", "예외 발생", None):
            place_raw, msgs = find_main_publisher_from_imprints(resolved_pub_for_search, imprint_data, publisher_data)
            debug += msgs
            if place_raw: source = "IMPRINT→KPIPA"

        if not place_raw or place_raw in ("출판지 미상", "예외 발생"):
            mcst_addr, mcst_rows, mcst_dbg = get_mcst_address(resolved_pub_for_search)
            debug += mcst_dbg
            if mcst_addr not in ("미확인", "오류 발생", None):
                place_raw, source = mcst_addr, "MCST"

        if not place_raw or place_raw in ("출판지 미상", "예외 발생", "미확인", "오류 발생"):
            place_raw, source = "출판지 미상", "FALLBACK"
            debug.append("⚠️ 모든 경로 실패 → '출판지 미상'")

        place_display = normalize_publisher_location_for_display(place_raw)
        country_code = get_country_code_by_region(place_raw, region_data)

        return {
            "place_raw": place_raw,
            "place_display": place_display,
            "country_code": country_code,
            "resolved_publisher": resolved_pub_for_search,
            "source": source,
            "debug": debug,
        }
    except Exception as e:
        return {
            "place_raw": "발행지 미상",
            "place_display": "발행지 미상",
            "country_code": "   ",
            "resolved_publisher": publisher_name_raw or "",
            "source": "ERROR",
            "debug": [f"예외: {e}"],
        }

# ===== 출판지/출판사 DB 함수
def normalize_publisher_name(name):
    return re.sub(r"\s|\(.*?\)|주식회사|㈜|도서출판|출판사", "", name).lower()

def normalize_stage2(name):
    name = re.sub(r"(주니어|JUNIOR|어린이|키즈|북스|아이세움|프레스)", "", name, flags=re.IGNORECASE)
    eng_to_kor = {"springer": "스프링거", "cambridge": "케임브리지", "oxford": "옥스포드"}
    for eng, kor in eng_to_kor.items():
        name = re.sub(eng, kor, name, flags=re.IGNORECASE)
    return name.strip().lower()

def split_publisher_aliases(name):
    aliases = []
    bracket_contents = re.findall(r"\((.*?)\)", name)
    for content in bracket_contents:
        parts = re.split(r"[,/]", content)
        parts = [p.strip() for p in parts if p.strip()]
        aliases.extend(parts)
    name_no_brackets = re.sub(r"\(.*?\)", "", name).strip()
    if "/" in name_no_brackets:
        parts = [p.strip() for p in name_no_brackets.split("/") if p.strip()]
        rep_name = parts[0]
        aliases.extend(parts[1:])
    else:
        rep_name = name_no_brackets
    return rep_name, aliases

def normalize_publisher_location_for_display(location_name):
    if not location_name or location_name in ("출판지 미상", "예외 발생"):
        return location_name
    location_name = location_name.strip()
    major_cities = ["서울", "인천", "대전", "광주", "울산", "대구", "부산", "세종"]
    for city in major_cities:
        if city in location_name:
            return location_name[:2]
    parts = location_name.split()
    loc = parts[1] if len(parts) > 1 else parts[0]
    if loc.endswith("시"):
        loc = loc[:-1]
    return loc

def search_publisher_location_with_alias(name, publisher_data):
    debug_msgs = []
    if not name:
        return "출판지 미상", ["❌ 검색 실패: 입력된 출판사명이 없음"]
    norm_name = normalize_publisher_name(name)
    candidates = publisher_data[publisher_data["출판사명"].apply(lambda x: normalize_publisher_name(x)) == norm_name]
    if not candidates.empty:
        address = candidates.iloc[0]["주소"]
        debug_msgs.append(f"✅ KPIPA DB 매칭 성공: {name} → {address}")
        return address, debug_msgs
    else:
        debug_msgs.append(f"❌ KPIPA DB 매칭 실패: {name}")
        return "출판지 미상", debug_msgs

def find_main_publisher_from_imprints(rep_name, imprint_data, publisher_data):
    """IM_* 시트에서 임프린트 검색"""
    norm_rep = normalize_publisher_name(rep_name)
    for full_text in imprint_data["임프린트"]:
        if "/" in full_text:
            pub_part, imprint_part = [p.strip() for p in full_text.split("/", 1)]
        else:
            pub_part, imprint_part = full_text.strip(), None

        if imprint_part:
            norm_imprint = normalize_publisher_name(imprint_part)
            if norm_imprint == norm_rep:
                location, debug_msgs = search_publisher_location_with_alias(pub_part, publisher_data)
                return location, debug_msgs
    return None, [f"❌ IM DB 검색 실패: 매칭되는 임프린트 없음 ({rep_name})"]

@st.cache_data(ttl=3600)
def load_publisher_db():
    """구글시트에서 출판사 DB 로드"""
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gspread"], 
                                                                ["https://spreadsheets.google.com/feeds",
                                                                 "https://www.googleapis.com/auth/drive"])
        client = gspread.authorize(creds)
        sh = client.open("출판사 DB")
        
        pub_rows = sh.worksheet("발행처명–주소 연결표").get_all_values()[1:]
        pub_rows_filtered = [row[1:3] for row in pub_rows]
        publisher_data = pd.DataFrame(pub_rows_filtered, columns=["출판사명", "주소"])
        
        region_rows = sh.worksheet("발행국명–발행국부호 연결표").get_all_values()[1:]
        region_rows_filtered = [row[:2] for row in region_rows]
        region_data = pd.DataFrame(region_rows_filtered, columns=["발행국", "발행국 부호"])
        
        imprint_frames = []
        for ws in sh.worksheets():
            if ws.title.startswith("발행처-임프린트 연결표"):
                data = ws.get_all_values()[1:]
                imprint_frames.extend([row[0] for row in data if row])
        imprint_data = pd.DataFrame(imprint_frames, columns=["임프린트"])
        
        return publisher_data, region_data, imprint_data
    except Exception as e:
        st.error(f"구글시트 로드 오류: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

def get_publisher_name_from_isbn_kpipa(isbn):
    """KPIPA 페이지에서 출판사명 검색"""
    search_url = "https://bnk.kpipa.or.kr/home/v3/addition/search"
    params = {"ST": isbn, "PG": 1, "PG2": 1, "DSF": "Y", "SO": "weight", "DT": "A"}
    headers = {"User-Agent": "Mozilla/5.0"}
    def normalize(name):
        return re.sub(r"\s|\(.*?\)|주식회사|㈜|도서출판|출판사|프레스", "", name).lower()
    try:
        res = requests.get(search_url, params=params, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        first_result_link = soup.select_one("a.book-grid-item")
        if not first_result_link:
            return None, None, "❌ 검색 결과 없음 (KPIPA)"
        detail_href = first_result_link.get("href")
        detail_url = f"https://bnk.kpipa.or.kr{detail_href}"
        detail_res = requests.get(detail_url, headers=headers, timeout=15)
        detail_res.raise_for_status()
        detail_soup = BeautifulSoup(detail_res.text, "html.parser")
        pub_info_tag = detail_soup.find("dt", string="출판사 / 임프린트")
        if not pub_info_tag:
            return None, None, "❌ '출판사 / 임프린트' 항목을 찾을 수 없습니다. (KPIPA)"
        dd_tag = pub_info_tag.find_next_sibling("dd")
        if dd_tag:
            full_text = dd_tag.get_text(strip=True)
            publisher_name_full = full_text
            publisher_name_part = publisher_name_full.split("/")[0].strip()
            publisher_name_norm = normalize(publisher_name_part)
            return publisher_name_full, publisher_name_norm, None
        return None, None, "❌ 'dd' 태그에서 텍스트를 추출할 수 없습니다. (KPIPA)"
    except Exception as e:
        return None, None, f"KPIPA 예외: {e}"

def get_mcst_address(publisher_name):
    """문체부에서 발행지 검색"""
    url = "https://book.mcst.go.kr/html/searchList.php"
    params = {"search_area": "전체", "search_state": "1", "search_kind": "1", 
              "search_type": "1", "search_word": publisher_name}
    debug_msgs = []
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        for row in soup.select("table.board tbody tr"):
            cols = row.find_all("td")
            if len(cols) >= 4:
                reg_type = cols[0].get_text(strip=True)
                name = cols[1].get_text(strip=True)
                address = cols[2].get_text(strip=True)
                status = cols[3].get_text(strip=True)
                if status == "영업":
                    results.append((reg_type, name, address, status))
        if results:
            debug_msgs.append(f"[문체부] 검색 성공: {len(results)}건")
            return results[0][2], results, debug_msgs
        else:
            debug_msgs.append("[문체부] 검색 결과 없음")
            return "미확인", [], debug_msgs
    except Exception as e:
        debug_msgs.append(f"[문체부] 예외 발생: {e}")
        return "오류 발생", [], debug_msgs

def get_country_code_by_region(region_name, region_data):
    """지역명 → 008 발행국 부호"""
    try:
        def normalize_region_for_code(region):
            region = (region or "").strip()
            if region.startswith(("전라", "충청", "경상")):
                return region[0] + (region[2] if len(region) > 2 else "")
            return region[:2]
        normalized_input = normalize_region_for_code(region_name)
        for idx, row in region_data.iterrows():
            sheet_region, country_code = row["발행국"], row["발행국 부호"]
            if normalize_region_for_code(sheet_region) == normalized_input:
                return country_code.strip() or "   "
        return "   "
    except Exception as e:
        st.write(f"⚠️ get_country_code_by_region 예외: {e}")
        return "   "

# ===== 940 필드 관련
DECIMAL_MAP = {
    "2.0": "이점영",
    "3.0": "삼점영",
    "4.0": "사점영",
}

EN_KO_MAP = {
    "chatgpt": "챗지피티",
    "gpt": "지피티",
    "ai": "에이아이",
    "api": "에이피아이",
    "ml": "엠엘",
    "nlp": "엔엘피",
    "llm": "엘엘엠",
    "excel": "엑셀",
    "youtube": "유튜브",
}

SINO = {"0":"영","1":"일","2":"이","3":"삼","4":"사","5":"오","6":"육","7":"칠","8":"팔","9":"구"}
ZERO_ALT = ["영", "공"]

def replace_decimals(text: str) -> str:
    """소수점 치환"""
    for k, v in DECIMAL_MAP.items():
        text = text.replace(k, v)
    return text

def replace_english_simple(text: str) -> str:
    """영문 간이 치환"""
    if not EN_KO_MAP: 
        return text
    def _sub(m):
        return EN_KO_MAP.get(m.group(0).lower(), m.group(0))
    pattern = r"\b(" + "|".join(map(re.escape, EN_KO_MAP.keys())) + r")\b"
    return re.sub(pattern, _sub, text, flags=re.IGNORECASE)

def _read_year_yyyy(num: str) -> str:
    """4자리 숫자 읽기"""
    n = int(num)
    th = n // 1000; hu = (n // 100) % 10; te = (n // 10) % 10; on = n % 10
    out = []
    if th: out.append(SINO[str(th)] + "천")
    if hu: out.append(SINO[str(hu)] + "백")
    if te: out.append("십" if te==1 else SINO[str(te)] + "십")
    if on: out.append(SINO[str(on)])
    return "".join(out) if out else "영"

def _read_cardinal(num: str) -> str:
    """기수 읽기"""
    return _read_year_yyyy(num)

def _read_digits(num: str, zero="영") -> str:
    """자릿수 읽기"""
    return "".join(SINO[ch] if ch in SINO and ch != "0" else (zero if ch=="0" else ch) for ch in num)

def generate_korean_title_variants(title: str, max_variants: int = 5) -> List[str]:
    """한국어 제목 변형 생성"""
    base0 = (title or "").strip()
    base = replace_decimals(base0)
    base = replace_english_simple(base)

    variants = {base0, base}

    nums = re.findall(r"\d{2,}", base0)
    if nums:
        per_num_choices = []
        for n in nums:
            local = {_read_cardinal(n)}
            if len(n) == 4 and 1000 <= int(n) <= 2999:
                local.add(_read_year_yyyy(n))
            for z in ZERO_ALT:
                local.add(_read_digits(n, zero=z))
            per_num_choices.append(sorted(local, key=len))

        work = {base}
        for i, choices in enumerate(per_num_choices):
            new_work = set()
            for w in work:
                cnt = 0
                for c in choices:
                    def _repl(m, idx=i, repl=c):
                        nonlocal cnt
                        if cnt==0 and m.group(0)==nums[idx]:
                            cnt = 1
                            return repl
                        return m.group(0)
                    new_work.add(re.sub(r"\d{2,}", _repl, w))
            work = new_work
        variants |= work

    outs = []
    for v in variants:
        if not v: continue
        v = re.sub(r"\s+([:;,./])", r"\1", v).strip()
        outs.append(v)
    outs = sorted(set(outs), key=lambda s: (len(s), s))
    return outs[:max_variants]

def ai_korean_readings(title: str, n: int = 4) -> List[str]:
    """AI 기반 한국어 발음 생성"""
    title = (title or "").strip()
    if not title or _client is None:
        return []

    key = f"ai940|{title}"
    cached = _ai940_get(key)
    if cached is not None:
        return cached[:n]

    try:
        sys = (
            "역할: 한국어 도서 서명 '발음 표기 생성기'. "
            "입력 서명의 영어/숫자를 자연스러운 한국어 발음으로 바꾸어라. "
            "각 줄에 하나의 변형만 출력. 설명/번호/기호 금지. 최대 6줄."
        )
        prompt = (
            f"서명: {title}\n"
            "지침: 표기는 한국어로만, 맞춤법 준수. "
            "예: 2025→이천이십오, 2.0→이점영, ChatGPT→챗지피티"
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":sys},
                      {"role":"user","content":prompt}],
            temperature=0.3,
        )
        text = (resp.choices[0].message.content or "").strip()
        lines = [re.sub(r"^\d+[\).\s-]*", "", x).strip() for x in text.splitlines() if x.strip()]
        lines = [l for l in lines if l and l != title and re.search(r"[가-힣]", l)]
        _ai940_set(key, lines)
        return lines[:n]
    except Exception:
        return []

_ai940_lock = threading.Lock()
_ai940_conn = sqlite3.connect("author_cache.sqlite3", check_same_thread=False)
_ai940_conn.execute("""CREATE TABLE IF NOT EXISTS name_cache(
  key TEXT PRIMARY KEY,
  value TEXT
)""")

def _ai940_get(key: str):
    with _ai940_lock:
        cur = _ai940_conn.execute("SELECT value FROM name_cache WHERE key=?", (key,))
        row = cur.fetchone()
    return json.loads(row[0]) if row else None

def _ai940_set(key: str, value: list[str]):
    with _ai940_lock:
        _ai940_conn.execute("INSERT OR REPLACE INTO name_cache(key,value) VALUES(?,?)",
                            (key, json.dumps(value, ensure_ascii=False)))
        _ai940_conn.commit()

def ai_korean_readings_strict(title_a: str, n: int = 4) -> list[str]:
    """엄격한 AI 한국어 발음 생성"""
    if not title_a or _client is None:
        return []

    key = f"ai940|strict|{title_a}"
    cached = _ai940_get(key)
    if cached is not None:
        return cached[:n]

    try:
        sys = (
            "역할: 한국어 도서 서명 '발음 표기 생성기'. "
            "주어진 본표제(245 $a)에서 숫자/영문만 한국어 발음으로 치환하라. "
            "입력에 없는 단어/부제($b) 추가 금지. 콜론(:), 대시(-) 등 새 구두점 추가 금지. "
            "각 줄에 1개 변형만, 순수 텍스트만 출력."
        )
        prompt = (
            f"본표제(245 $a): {title_a}\n"
            "예: 2025→이천이십오, 2.0→이점영, ChatGPT→챗지피티"
        )
        resp = _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":sys},
                      {"role":"user","content":prompt}],
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        lines = [re.sub(r"^\d+[\).\s-]*", "", x).strip() for x in text.splitlines() if x.strip()]
        safe = []
        for l in lines:
            if not re.search(r"[가-힣]", l):
                continue
            if (":" in l and ":" not in title_a) or (" - " in l and " - " not in title_a and "-" not in title_a):
                continue
            safe.append(l)
        _ai940_set(key, safe)
        return safe[:n]
    except Exception:
        return []

def build_940_from_title_a(title_a: str, use_ai: bool = True, *, disable_number_reading: bool = False) -> list[str]:
    """940 필드 생성"""
    base = (title_a or "").strip()
    if not base:
        return []

    if not re.search(r"[0-9A-Za-z]", base):
        return []

    if disable_number_reading:
        v0 = replace_english_simple(base) if 'replace_english_simple
