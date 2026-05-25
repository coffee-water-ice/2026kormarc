"""
api/external_apis.py
외부 API·크롤링 래퍼 — 260/300 필드 생성에 필요한 발행지 조회 로직.

포함 범위:
  - KPIPA(출판사 정보 진흥원) 페이지 크롤링
  - 문체부(MCST) 출판사 검색
  - Google Sheets 기반 출판사 DB 로드
  - 발행지·국가코드 조회 통합 번들 (build_pub_location_bundle)

의존:
  - gspread, oauth2client (Google Sheets 인증)
  - requests, beautifulsoup4 (HTTP 크롤링)
  - pandas (데이터프레임)
"""

from __future__ import annotations

import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

# 전역 캐시 변수 (매 요청마다 구글 시트를 다시 읽지 않도록 방지)
# isbn_prefix_dict: {isbn_prefix(str) → 발행지(str)} — iterrows 대신 O(1) dict 조회용
_PUBLISHER_DB_CACHE: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, str]] | None = None


# ============================================================
# 정규화 유틸
# ============================================================

def get_aladin_item_by_isbn(isbn: str, secrets: dict) -> tuple[dict, str | None]:
    """
    알라딘 OpenAPI에서 ISBN으로 도서 item 1건을 조회한다.

    Returns:
        (item dict, error msg or None)
    """
    key = (
        (secrets or {}).get("ALADIN_TTB_KEY")
        or (secrets or {}).get("aladin_ttb_key")
        or ""
    )
    if not key:
        return {}, "ALADIN_TTB_KEY가 설정되지 않았습니다."

    url = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    params = {
        "ttbkey": key,
        "itemIdType": "ISBN13",
        "ItemId": isbn,
        "output": "js",
        "Version": "20131101",
        "OptResult": "ebookList,usedList,reviewList,fileFormatList,packing,subbarcode",
        "Cover": "Big",
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        items = data.get("item", []) if isinstance(data, dict) else []
        if not items:
            return {}, f"알라딘 검색 결과 없음: {isbn}"
        return items[0], None
    except Exception as e:
        return {}, f"알라딘 API 조회 실패: {e}"

def normalize_publisher_name(name: str) -> str:
    """출판사명 표준화 (공백·법인격·괄호 제거, 소문자 변환)."""
    return re.sub(r"\s|\(.*?\)|주식회사|㈜|도서출판|주\)도서출판|출판사", "", name or "").lower()


def normalize_stage2(name: str) -> str:
    """2단계 정규화 — 시리즈성 접미어, 영문→한글 치환."""
    name = re.sub(
        r"(주니어|JUNIOR|어린이|키즈|북스|아이세움|프레스)", "", name, flags=re.IGNORECASE
    )
    eng_to_kor = {
        "springer": "스프링거",
        "cambridge": "케임브리지",
        "oxford": "옥스포드",
    }
    for eng, kor in eng_to_kor.items():
        name = re.sub(eng, kor, name, flags=re.IGNORECASE)
    return name.strip().lower()


def split_publisher_aliases(name: str) -> tuple[str, list[str]]:
    """
    "출판사명(별칭1/별칭2)" 형태에서 대표명·별칭 목록을 분리한다.

    Returns:
        (대표명, [별칭1, 별칭2, ...])
    """
    aliases: list[str] = []
    for content in re.findall(r"\((.*?)\)", name):
        aliases.extend(p.strip() for p in re.split(r"[,/]", content) if p.strip())
    name_no_brackets = re.sub(r"\(.*?\)", "", name).strip()
    if "/" in name_no_brackets:
        parts = [p.strip() for p in name_no_brackets.split("/") if p.strip()]
        return parts[0], aliases + parts[1:]
    return name_no_brackets, aliases


def normalize_publisher_location_for_display(location_name: str) -> str:
    """
    주소 문자열을 KORMARC 260 $a 표시용 지역명으로 변환한다.
    예: "서울특별시 마포구 …" → "서울"
    """
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


# ============================================================
# Google Sheets 기반 출판사 DB
# ============================================================

def load_publisher_db(secrets: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Google Sheets '출판사 DB' 스프레드시트에서 네 가지 데이터프레임을 로드한다.

    Args:
        secrets: Streamlit secrets dict (또는 동등한 dict).
                 secrets["gspread"] 에 서비스 계정 JSON 키가 있어야 한다.

    Returns:
        (publisher_data, region_data, imprint_data, isbn_prefix_dict)
        - publisher_data:    columns=["출판사명", "주소"]
        - region_data:       columns=["발행국", "발행국 부호"]
        - imprint_data:      columns=["임프린트"]
        - isbn_prefix_dict:  {isbn_prefix → 발행지} — O(1) 조회용 dict
    """
    global _PUBLISHER_DB_CACHE
    if _PUBLISHER_DB_CACHE is not None:
        return _PUBLISHER_DB_CACHE

    import json
    import os

    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    env_creds = os.environ.get("GSPREAD_CREDENTIALS", "").strip()
    keyfile_dict = None

    if env_creds:
        # 1. 만약 전체가 따옴표로 감싸져 있다면 제거 (Render 등 배포 환경에서 흔히 발생)
        if (env_creds.startswith('"') and env_creds.endswith('"')) or \
           (env_creds.startswith("'") and env_creds.endswith("'")):
            env_creds = env_creds[1:-1]

        try:
            # 2. 표준 JSON 파싱 시도
            keyfile_dict = json.loads(env_creds)
        except json.JSONDecodeError:
            try:
                # 3. Invalid \escape 에러 대응: 실제 줄바꿈(\n)이 포함된 경우 \n 문자열로 치환하여 재시도
                fixed_creds = env_creds.replace('\n', '\\n')
                keyfile_dict = json.loads(fixed_creds)
            except Exception as e:
                raise ValueError(f"GSPREAD_CREDENTIALS JSON 형식이 올바르지 않습니다: {e}")
    else:
        keyfile_dict = secrets.get("gspread")

    if not keyfile_dict:
        raise ValueError("구글 시트 인증 정보(GSPREAD_CREDENTIALS)를 찾을 수 없습니다.")

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        keyfile_dict,
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open("출판사 DB")

    pub_rows = sh.worksheet("발행처명–주소 연결표").get_all_values()[1:]
    publisher_data = pd.DataFrame(
        [row[1:3] for row in pub_rows], columns=["출판사명", "주소"]
    )

    region_rows = sh.worksheet("발행국명–발행국부호 연결표").get_all_values()[1:]
    region_data = pd.DataFrame(
        [row[:2] for row in region_rows], columns=["발행국", "발행국 부호"]
    )

    imprint_frames: list[str] = []
    for ws in sh.worksheets():
        if ws.title.startswith("발행처-임프린트 연결표"):
            imprint_frames.extend(row[0] for row in ws.get_all_values()[1:] if row)
    imprint_data = pd.DataFrame(imprint_frames, columns=["임프린트"])

    # "ISBN발행자번호-발행지 연결표" 시트 로드
    # 행 구조: [No, 발행자명, isbn_prefix_1, ..., isbn_prefix_9, 발행지]  (12열)
    # → col[0]=No, col[1]=발행자명, col[2..10]=ISBN 접두부, col[11]=발행지
    # dict {prefix: 발행지} 로 빌드 — 검색 시 O(1) 조회를 위해 DataFrame 대신 사용
    # 동일 접두부가 여러 발행지를 가질 경우 첫 번째 값 유지 (선입 우선)
    isbn_loc_rows = sh.worksheet("ISBN발행자번호-발행지 연결표").get_all_values()[1:]
    isbn_prefix_dict: dict[str, str] = {}
    for row in isbn_loc_rows:
        if not row:
            continue
        location = row[-1].strip() if row else ""
        if not location:
            continue
        for cell in row[2:-1]:  # No(0)·발행자명(1) 제외, 발행지(-1) 제외
            prefix = re.sub(r"\D", "", str(cell))
            if 5 <= len(prefix) <= 13 and prefix not in isbn_prefix_dict:
                isbn_prefix_dict[prefix] = location

    result = (publisher_data, region_data, imprint_data, isbn_prefix_dict)
    _PUBLISHER_DB_CACHE = result
    return _PUBLISHER_DB_CACHE


# ============================================================
# 출판사 위치 검색
# ============================================================

def search_location_by_isbn_prefix(
    isbn: str, isbn_prefix_dict: dict[str, str]
) -> tuple[str, list[str]]:
    """
    'ISBN발행자번호-발행지 연결표' dict에서 ISBN 접두부 매칭으로 발행지를 찾는다.

    긴 접두부(더 구체적)부터 짧은 접두부 순으로 dict 키를 조회하므로
    최장 매칭이 우선된다. 검색 비용은 O(접두부 길이 범위) = O(9).

    Args:
        isbn:             13자리 ISBN (하이픈 포함/미포함 모두 처리)
        isbn_prefix_dict: {isbn_prefix → 발행지}

    Returns:
        (발행지 또는 "출판지 미상", 디버그 메시지 목록)
    """
    debug: list[str] = []
    isbn_clean = re.sub(r"\D", "", isbn or "")

    if len(isbn_clean) != 13:
        return "출판지 미상", [f"❌ ISBN 접두부 검색 불가: 13자리 아님 ({isbn_clean})"]

    if not isbn_prefix_dict:
        return "출판지 미상", ["❌ ISBN발행자번호-발행지 연결표가 비어 있음"]

    # 길이 13→5 순으로 줄여가며 dict 조회 — 첫 번째 히트가 최장 매칭
    for length in range(13, 4, -1):
        prefix = isbn_clean[:length]
        if prefix in isbn_prefix_dict:
            location = isbn_prefix_dict[prefix]
            debug.append(f"✅ ISBN 접두부 매칭 성공: {prefix}… → {location}")
            return location, debug

    debug.append(f"❌ ISBN 접두부 매칭 실패: {isbn_clean}")
    return "출판지 미상", debug


def search_publisher_location_with_alias(
    name: str, publisher_data: pd.DataFrame
) -> tuple[str, list[str]]:
    """
    KPIPA DB(Google Sheets)에서 출판사명으로 주소를 찾는다.

    Returns:
        (주소 또는 "출판지 미상", 디버그 메시지 목록)
    """
    debug: list[str] = []
    if not name:
        return "출판지 미상", ["❌ 검색 실패: 입력된 출판사명이 없음"]
    norm = normalize_publisher_name(name)
    candidates = publisher_data[
        publisher_data["출판사명"].apply(normalize_publisher_name) == norm
    ]
    if not candidates.empty:
        addr = candidates.iloc[0]["주소"]
        debug.append(f"✅ KPIPA DB 매칭 성공: {name} → {addr}")
        return addr, debug
    debug.append(f"❌ KPIPA DB 매칭 실패: {name} (정규화 결과: {norm})")
    return "출판지 미상", debug


def find_main_publisher_from_imprints(
    rep_name: str,
    imprint_data: pd.DataFrame,
    publisher_data: pd.DataFrame,
) -> tuple[str | None, list[str]]:
    """
    임프린트 DB에서 rep_name을 임프린트로 가진 출판사를 찾아 주소를 반환한다.
    """
    norm_rep = normalize_publisher_name(rep_name)
    for full_text in imprint_data["임프린트"]:
        if "/" in full_text:
            pub_part, imprint_part = [p.strip() for p in full_text.split("/", 1)]
        else:
            pub_part, imprint_part = full_text.strip(), None
        if imprint_part and normalize_publisher_name(imprint_part) == norm_rep:
            location, msgs = search_publisher_location_with_alias(pub_part, publisher_data)
            return location, msgs
    return None, [f"❌ IM DB 검색 실패: 매칭되는 임프린트 없음 ({rep_name})"]


def get_country_code_by_region(region_name: str, region_data: pd.DataFrame) -> str:
    """
    지역명(발행지)으로 008 발행국 3자리 부호를 찾는다.
    매칭 실패 시 공백 3칸("   ") 반환.
    """
    def _norm(r: str) -> str:
        r = (r or "").strip()
        if r.startswith(("전라", "충청", "경상")):
            return r[0] + (r[2] if len(r) > 2 else "")
        return r[:2]

    try:
        norm_input = _norm(region_name)
        for _, row in region_data.iterrows():
            if _norm(row["발행국"]) == norm_input:
                return row["발행국 부호"].strip() or "   "
        return "   "
    except Exception:
        return "   "


# ============================================================
# KPIPA 공식 OpenAPI (ISBN → 도서 상세)
# ============================================================

def get_kpipa_book_detail(isbn: str, api_key: str) -> tuple[dict, str | None]:
    """
    KPIPA 공식 OpenAPI로 ISBN 도서 상세 정보를 조회한다.

    Args:
        isbn:    ISBN-13 문자열
        api_key: KPIPA 서비스키 (KPIPA_API_KEY 환경변수)

    Returns:
        (response_dict, error_msg or None)
        오류 시 response_dict = {}, error_msg = 설명 문자열
    """
    if not api_key:
        return {}, "KPIPA_API_KEY가 설정되지 않았습니다."

    url = "https://bnk.kpipa.or.kr/api/openApi/metaInfoSvc/getBookDetail"
    params = {"apiKey": api_key, "isbn": isbn}
    headers = {"Accept": "application/json"}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=8)
        res.raise_for_status()
        data = res.json()
        return data, None
    except requests.exceptions.Timeout:
        return {}, "KPIPA API 요청 시간 초과 (8s)"
    except requests.exceptions.HTTPError as e:
        return {}, f"KPIPA API HTTP 오류: {e}"
    except Exception as e:
        return {}, f"KPIPA API 예외: {e}"


# ============================================================
# 행정안전부 출판사 조회 API (MOIS)
# ============================================================

def get_mois_publisher_address(publisher_name: str, api_key: str) -> tuple[str | None, list[str]]:
    """
    행정안전부 출판사 조회 API로 출판사 주소를 검색한다.
    (문체부 웹크롤링 대체)

    endpoint: https://apis.data.go.kr/1741000/publishers/info
    cond[SALS_STTS_CD::EQ]=01 로 영업/정상 업체만 필터링한다.

    Args:
        publisher_name: 검색할 출판사명
        api_key:        DATA_GO_KR 환경변수 값 (공공데이터포털 인증키)

    Returns:
        (도로명주소 또는 None, 디버그 메시지 목록)
    """
    debug: list[str] = []
    if not api_key:
        debug.append("[행안부] API 키(DATA_GO_KR) 없음")
        return None, debug
    if not publisher_name:
        debug.append("[행안부] 검색어 없음")
        return None, debug

    url = "https://apis.data.go.kr/1741000/publishers/info"
    params = {
        "serviceKey": api_key,
        "pageNo": "1",
        "numOfRows": "10",
        "returnType": "json",
        "cond[SALS_STTS_CD::EQ]": "01",
        "cond[BPLC_NM::LIKE]": publisher_name,
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        body = (data.get("response") or {}).get("body") or {}
        raw_items = (body.get("items") or {}).get("item") or []
        # 단일 결과인 경우 dict로 반환될 수 있음
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not raw_items:
            debug.append(f"[행안부] 검색 결과 없음: {publisher_name}")
            return None, debug
        addr = raw_items[0].get("ROAD_NM_ADDR") or raw_items[0].get("LOTNO_ADDR") or ""
        if addr:
            debug.append(f"✅ 행안부 API 매칭 성공: {publisher_name} → {addr}")
            return addr, debug
        debug.append(f"[행안부] 주소 필드 없음: {publisher_name}")
        return None, debug
    except requests.exceptions.Timeout:
        debug.append("[행안부] 요청 시간 초과 (10s)")
        return None, debug
    except Exception as e:
        debug.append(f"[행안부] 예외: {e}")
        return None, debug


# ============================================================
# KPIPA 페이지 크롤링 (ISBN → 출판사명) 사용 X
# ============================================================

def get_publisher_name_from_isbn_kpipa(isbn: str) -> tuple[str | None, str | None, str | None]:
    """
    KPIPA 사이트에서 ISBN으로 출판사명을 크롤링한다.

    Returns:
        (full_name, normalized_name, error_msg)
        오류 시 full_name/normalized_name = None, error_msg = 설명 문자열
    """
    search_url = "https://bnk.kpipa.or.kr/home/v3/addition/search"
    params = {"ST": isbn, "PG": 1, "PG2": 1, "DSF": "Y", "SO": "weight", "DT": "A"}
    headers = {"User-Agent": "Mozilla/5.0"}

    def _normalize(name: str) -> str:
        return re.sub(r"\s|\(.*?\)|주식회사|㈜|도서출판|출판사|프레스", "", name).lower()

    try:
        res = requests.get(search_url, params=params, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        link = soup.select_one("a.book-grid-item")
        if not link:
            return None, None, "❌ 검색 결과 없음 (KPIPA)"

        detail_url = "https://bnk.kpipa.or.kr" + link.get("href", "")
        detail_res = requests.get(detail_url, headers=headers, timeout=15)
        detail_res.raise_for_status()
        detail_soup = BeautifulSoup(detail_res.text, "html.parser")

        dt_tag = detail_soup.find("dt", string="출판사 / 임프린트")
        if not dt_tag:
            return None, None, "❌ '출판사 / 임프린트' 항목을 찾을 수 없습니다. (KPIPA)"
        dd_tag = dt_tag.find_next_sibling("dd")
        if not dd_tag:
            return None, None, "❌ 'dd' 태그에서 텍스트를 추출할 수 없습니다. (KPIPA)"

        full_text = dd_tag.get_text(strip=True)
        part = full_text.split("/")[0].strip()
        return full_text, _normalize(part), None

    except Exception as e:
        return None, None, f"KPIPA 예외: {e}"


# ============================================================
# [보존용 — 행안부 API(get_mois_publisher_address)로 대체됨]
# 문체부(MCST) 출판사 주소 검색
# build_pub_location_bundle()에서는 더 이상 호출하지 않음.
# ============================================================

def get_mcst_address(publisher_name: str) -> tuple[str, list, list[str]]:
    """
    문체부 출판물 검색에서 출판사 주소를 가져온다.

    Returns:
        (주소 또는 "미확인"/"오류 발생", 결과 행 목록, 디버그 메시지 목록)
    """
    url = "https://book.mcst.go.kr/html/searchList.php"
    params = {
        "search_area": "전체", "search_state": "1",
        "search_kind": "1", "search_type": "1",
        "search_word": publisher_name,
    }
    debug: list[str] = []
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        for row in soup.select("table.board tbody tr"):
            cols = row.find_all("td")
            if len(cols) >= 4 and cols[3].get_text(strip=True) == "영업":
                results.append(tuple(c.get_text(strip=True) for c in cols[:4]))
        if results:
            debug.append(f"[문체부] 검색 성공: {len(results)}건")
            return results[0][2], results, debug
        debug.append("[문체부] 검색 결과 없음")
        return "미확인", [], debug
    except Exception as e:
        debug.append(f"[문체부] 예외 발생: {e}")
        return "오류 발생", [], debug


# ============================================================
# 발행지 통합 번들 (260 생성에 직접 사용)
# ============================================================

def _extract_kpipa_publisher_name(data: dict) -> str | None:
    """
    KPIPA API 응답 dict에서 PublisherName 값을 추출한다.

    실제 응답 경로:
      response → body → items → Product → PublishingDetail → Publisher → PublisherName
    ImprintName은 PublisherName 부재 시 보조로 사용한다.

    NOTE: `or {}` 패턴을 사용해 값이 null인 키도 안전하게 처리한다.
    (.get("key", {}) 는 키가 없을 때만 {} 반환; 키가 있고 값이 None이면 None 반환)
    """
    if not data:
        return None

    try:
        response = data.get("response") or {}
        body     = response.get("body") or {}
        items    = body.get("items") or {}

        # 실제 응답에서 items가 리스트로 오는 경우 대응
        if isinstance(items, list):
            items = items[0] if items else {}

        product = items.get("Product") or {}

        # Product도 리스트로 오는 경우 대응
        if isinstance(product, list):
            product = product[0] if product else {}

        publishing_detail = product.get("PublishingDetail") or {}

        # Publisher와 Imprint도 실제 응답에서 리스트로 옴: [{...}]
        publisher = publishing_detail.get("Publisher") or {}
        if isinstance(publisher, list):
            publisher = publisher[0] if publisher else {}
        publisher_name = publisher.get("PublisherName") if isinstance(publisher, dict) else None
        if publisher_name:
            return str(publisher_name)

        imprint = publishing_detail.get("Imprint") or {}
        if isinstance(imprint, list):
            imprint = imprint[0] if imprint else {}
        imprint_name = imprint.get("ImprintName") if isinstance(imprint, dict) else None
        if imprint_name:
            return str(imprint_name)

    except (AttributeError, TypeError, IndexError, KeyError):
        pass

    return None


def build_pub_location_bundle(isbn: str, publisher_name_raw: str, secrets: dict) -> dict:
    """
    5단계 체인으로 발행지를 조회하고 260/008 필드 생성에 필요한 정보를 dict로 반환한다.

    탐색 순서 (실패 시 다음 단계로):
      [1] ISBN 접두부 → ISBN발행자번호-발행지 연결표
      [2] KPIPA API 출판사명 → KPIPA DB
      [3] 알라딘 출판사명 → KPIPA DB
      [4] 알라딘 출판사명 → IMPRINT DB → KPIPA DB
      [5] 알라딘 출판사명 → IMPRINT DB → 행안부 API

    Args:
        isbn:               ISBN-13
        publisher_name_raw: 알라딘 API에서 받은 출판사명 원본 (steps 3-5 사용)
        secrets:            Google Sheets 인증용 secrets dict

    Returns:
        {
            "place_raw":          원본 주소 문자열,
            "place_display":      정규화된 표시용 지역명 (260 $a),
            "country_code":       008용 3자리 국가코드,
            "resolved_publisher": 검색에 실제 사용한 출판사명,
            "source":             데이터 출처 레이블,
            "debug":              디버그 메시지 목록,
        }
    """
    debug: list[str] = []
    _UNKNOWN = ("출판지 미상", "예외 발생", "미확인", "오류 발생", None)

    try:
        publisher_data, region_data, imprint_data, isbn_prefix_dict = load_publisher_db(secrets)
        debug.append("✓ 구글시트 DB 적재 성공")

        place_raw: str | None = None
        source = "FALLBACK"
        resolved = (publisher_name_raw or "").strip()

        # [1] ISBN 접두부 → ISBN발행자번호-발행지 연결표
        place_raw, isbn_msgs = search_location_by_isbn_prefix(isbn, isbn_prefix_dict)
        debug += isbn_msgs
        if place_raw not in _UNKNOWN:
            source = "ISBN_PREFIX_DB"

        # [2] KPIPA API 출판사명 → KPIPA DB
        if place_raw in _UNKNOWN:
            kpipa_api_key = (secrets or {}).get("KPIPA_API_KEY", "")
            kpipa_data, kpipa_err = get_kpipa_book_detail(isbn, kpipa_api_key)
            if kpipa_err:
                debug.append(f"KPIPA API 오류: {kpipa_err}")
            else:
                result_code = (
                    (kpipa_data.get("response") or {})
                    .get("result", {})
                    .get("resultCode", "?")
                )
                kpipa_pub = _extract_kpipa_publisher_name(kpipa_data)
                if kpipa_pub:
                    debug.append(
                        f"✓ KPIPA API 성공 (resultCode={result_code}, 출판사: {kpipa_pub})"
                    )
                    place_raw, msgs = search_publisher_location_with_alias(kpipa_pub, publisher_data)
                    debug += msgs
                    if place_raw not in _UNKNOWN:
                        resolved = kpipa_pub
                        source = "KPIPA_API→DB"
                else:
                    debug.append(
                        f"KPIPA API 응답 있음 (resultCode={result_code}) → PublisherName 없음"
                    )

        # 알라딘 출판사 대표명 분리 (steps 3-5 공통)
        aladin_rep, _ = split_publisher_aliases(publisher_name_raw or "")
        aladin_rep = aladin_rep or (publisher_name_raw or "").strip()

        # [3] 알라딘 출판사명 → KPIPA DB
        if place_raw in _UNKNOWN:
            debug.append(f"[알라딘경로] 대표명: {aladin_rep}")
            place_raw, msgs = search_publisher_location_with_alias(aladin_rep, publisher_data)
            debug += msgs
            if place_raw not in _UNKNOWN:
                resolved = aladin_rep
                source = "ALADIN→DB"

        # [4] + [5]: IMPRINT DB 조회 (한 번만 수행 후 두 단계에서 재사용)
        if place_raw in _UNKNOWN:
            imprint_main: str | None = None
            norm_aladin = normalize_publisher_name(aladin_rep)
            for full_text in imprint_data["임프린트"]:
                if "/" in full_text:
                    pub_p, imp_p = [p.strip() for p in full_text.split("/", 1)]
                    if normalize_publisher_name(imp_p) == norm_aladin:
                        imprint_main = pub_p
                        debug.append(f"✅ IMPRINT 매칭: {aladin_rep} → {imprint_main}")
                        break
            if not imprint_main:
                debug.append(f"❌ IM DB 검색 실패: 매칭되는 임프린트 없음 ({aladin_rep})")

            # [4] IMPRINT → KPIPA DB
            if imprint_main:
                place_raw, msgs = search_publisher_location_with_alias(imprint_main, publisher_data)
                debug += msgs
                if place_raw not in _UNKNOWN:
                    resolved = aladin_rep
                    source = "ALADIN→IMPRINT→DB"

            # [5] IMPRINT → 행안부 API
            # imprint 매칭 성공 시 메인 출판사명으로, 실패 시 알라딘 대표명으로 검색
            if place_raw in _UNKNOWN:
                mois_target = imprint_main or aladin_rep
                mois_key = (secrets or {}).get("DATA_GO_KR", "")
                mois_addr, mois_debug = get_mois_publisher_address(mois_target, mois_key)
                debug += mois_debug
                if mois_addr:
                    place_raw = mois_addr
                    resolved = aladin_rep
                    source = "ALADIN→IMPRINT→MOIS"

        # 최종 fallback
        if not place_raw or place_raw in _UNKNOWN:
            place_raw, source = "출판지 미상", "FALLBACK"
            debug.append("⚠️ 모든 경로 실패 → '출판지 미상'")

        place_display = normalize_publisher_location_for_display(place_raw)
        country_code  = get_country_code_by_region(place_raw, region_data)

        return {
            "place_raw":          place_raw,
            "place_display":      place_display,
            "country_code":       country_code,
            "resolved_publisher": resolved,
            "source":             source,
            "debug":              debug,
        }

    except Exception as e:
        return {
            "place_raw":          "발행지 미상",
            "place_display":      "발행지 미상",
            "country_code":       "   ",
            "resolved_publisher": publisher_name_raw or "",
            "source":             "ERROR",
            "debug":              [f"예외: {e}"],
        }
