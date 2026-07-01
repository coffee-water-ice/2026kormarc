"""
core/field_rules.py
규칙 기반 MARC 필드 생성 로직.
현재 담당 범위: 260 (발행사항), 300 (형태사항)

의존: core/marc_builder.py (mrk_str_to_field)
      api/external_apis.py (search_aladin_detail_page, KPIPA/MCST 검색)
      database/publisher_db.py (load_publisher_db)
"""

from __future__ import annotations

import math
import re

import requests
from bs4 import BeautifulSoup
from pymarc import Field, Subfield

from core.marc_builder import mrk_str_to_field


# ============================================================
# 내부 헬퍼: 디버그 로거 (외부에서 주입하거나 기본 print 사용)
# ============================================================
_debug_lines: list[str] = []

def _dbg(*args):
    msg = " ".join(str(a) for a in args)
    _debug_lines.append(msg)

def _dbg_err(*args):
    msg = " ".join(str(a) for a in args)
    _debug_lines.append(f"ERROR: {msg}")

def get_debug_lines() -> list[str]:
    return list(_debug_lines)

def clear_debug_lines():
    _debug_lines.clear()


# ============================================================
# 260 — 발행사항
# ============================================================

# 260 $b 표시용 법인격 제거 패턴
# normalize_publisher_name()은 비교 전용(소문자·공백 제거) — 표시용은 별도 처리
_PUB_LEGAL_RE = re.compile(
    r"㈜|㈔"
    r"|\(주\)|\(재\)|\(주식회사\)|\(유한회사\)|\(사단법인\)|\(재단법인\)"
    r"|주식회사\s*|유한회사\s*"
    r"|Co\.,?\s*Ltd\.?|Inc\.?"
    r"|\([A-Za-z][^)]*\)",   # 괄호 영문명 (MinumSa) 등
    flags=re.IGNORECASE,
)

def _clean_pub_name(name: str) -> str:
    """260 $b 표시용: 법인격 표기(㈜·(주식회사)·주식회사·Co.,Ltd. 등) 제거."""
    return _PUB_LEGAL_RE.sub("", name or "").strip(" ,.")


def build_260(
    place_display: str, publisher_name: str, pubyear: str, publisher_name2: str = ""
) -> str:
    """
    260 MRK 문자열을 생성한다.

    publisher_name2: 임프린트·KPIPA 등에서 알라딘 출판사명과 다른 발행처가
                     확인된 경우 두 번째 $b로 추가된다.
                     예) "=260  \\\\$a파주 :$b요요 :$b다산북스,$c2022"
    """
    place = place_display or "발행지 미상"
    pub   = _clean_pub_name(publisher_name) or "발행처 미상"
    year  = pubyear or "발행년 미상"
    if publisher_name2:
        pub2 = _clean_pub_name(publisher_name2)
        if pub2:
            return f"=260  \\\\$a{place} :$b{pub} :$b{pub2},$c{year}"
    return f"=260  \\\\$a{place} :$b{pub},$c{year}"


def build_260_field(
    place_display: str, publisher_name: str, pubyear: str, publisher_name2: str = ""
) -> tuple[str, Field | None]:
    """
    260 MRK 문자열과 pymarc.Field 객체를 함께 반환한다.

    Returns:
        (mrk_str, Field 객체)  — Field 변환 실패 시 (mrk_str, None)
    """
    tag_260 = build_260(place_display, publisher_name, pubyear, publisher_name2)
    f_260 = mrk_str_to_field(tag_260)
    return tag_260, f_260


# ============================================================
# 300 — 형태사항 (알라딘 상세 페이지 크롤링 기반)
# ============================================================

# 삽화 키워드 매핑 (KORMARC 용어 → 감지 키워드)
_ILLUS_KEYWORD_GROUPS: dict[str, list[str]] = {
    "천연색삽화": ["삽화", "일러스트", "일러스트레이션", "illustration", "그림"],
    "삽화":       ["흑백 삽화", "흑백 일러스트", "흑백 일러스트레이션", "흑백 그림"],
    "사진":       ["사진", "포토", "photo", "화보"],
    "도표":       ["도표", "차트", "그래프"],
    "지도":       ["지도", "지도책"],
}


def detect_illustrations(text: str) -> tuple[bool, str | None]:
    """
    텍스트에서 삽화 관련 키워드를 감지하여 KORMARC $b 값을 반환한다.

    Returns:
        (감지 여부, 삽화 레이블 문자열 또는 None)
        예: (True, "도표, 사진") / (False, None)
    """
    if not text:
        return False, None
    found = set()
    for label, keywords in _ILLUS_KEYWORD_GROUPS.items():
        if any(kw in text for kw in keywords):
            found.add(label)
    if found:
        return True, ", ".join(sorted(found))
    return False, None


def _norm_label(text: str) -> str:
    """레이블 텍스트에서 NBSP·줄바꿈 등 모든 공백을 일반 공백으로 정규화."""
    return re.sub(r"[\s 　]+", " ", text).strip()


def _find_section_text(soup: BeautifulSoup, label: str) -> str:
    """
    알라딘 상세 페이지에서 레이블(Ere_prod_mconts_LL/LS)이 일치하는
    Ere_prod_mconts_box 내의 Ere_prod_mconts_R 텍스트를 반환한다.
    공백 정규화(NBSP 포함)를 적용해 비교한다.
    """
    for box in soup.select("div.Ere_prod_mconts_box"):
        for lbl_el in box.select(".Ere_prod_mconts_LL, .Ere_prod_mconts_LS"):
            if _norm_label(lbl_el.get_text()) == label:
                content = box.select_one(".Ere_prod_mconts_R")
                if content:
                    return content.get_text(" ", strip=True)
    return ""


def _diagnose_boxes(soup: BeautifulSoup) -> list[dict]:
    """디버그: 모든 Ere_prod_mconts_box의 레이블을 수집해 반환."""
    result = []
    for box in soup.select("div.Ere_prod_mconts_box"):
        labels = [
            _norm_label(el.get_text())
            for el in box.select(".Ere_prod_mconts_LL, .Ere_prod_mconts_LS")
        ]
        result.append({"labels": list(dict.fromkeys(labels))})
    return result


def detect_illustrations_with_sources(
    title_text: str, subtitle_text: str, desc_text: str,
    toc_text: str, pub_desc_text: str = ""
) -> tuple[bool, str | None, list[dict]]:
    """
    소스별로 삽화 키워드를 검사해 KORMARC 레이블과 출처를 함께 반환한다.

    Returns:
        (감지 여부, 레이블 문자열, 상세 리스트)
        상세 리스트 예: [{"label": "사진", "keyword": "사진", "source": "책소개"}]
    """
    source_map = [
        ("제목",           title_text),
        ("부제",           subtitle_text),
        ("책소개",         desc_text),
        ("목차",           toc_text),
        ("출판사 제공 소개", pub_desc_text),
    ]
    found: dict[str, dict] = {}
    for label, keywords in _ILLUS_KEYWORD_GROUPS.items():
        for kw in keywords:
            for src_name, src_text in source_map:
                if src_text and kw in src_text:
                    found[label] = {"keyword": kw, "source": src_name}
                    break
            if label in found:
                break
    if found:
        label_str = ", ".join(sorted(found.keys()))
        detail = [{"label": k, **v} for k, v in found.items()]
        return True, label_str, detail
    return False, None, []


def _fetch_naver_description(isbn: str, client_id: str, client_secret: str) -> str:
    """네이버 책 검색 API(book_adv)로 책소개를 가져온다."""
    if not client_id or not client_secret or not isbn:
        return ""
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/book_adv.json",
            params={"d_isbn": isbn, "display": 1},
            headers={
                "X-Naver-Client-Id":     client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            timeout=8,
        )
        if not r.ok:
            return ""
        items = r.json().get("items", [])
        if not items:
            return ""
        raw = items[0].get("description", "")
        return re.sub(r"<[^>]+>", "", raw).strip()
    except Exception:
        return ""


def _parse_aladin_categories(soup: BeautifulSoup) -> list[str]:
    """알라딘 상세 페이지의 conts_info_list2 블록에서 분류 경로 목록을 추출한다."""
    cat_div = soup.select_one("div.conts_info_list2")
    if not cat_div:
        return []
    results = []
    for li in cat_div.select("li"):
        text = li.get_text(" ", strip=True)
        # "보기" / "접기" 버튼 텍스트 제거
        text = re.sub(r"\s*(보기|접기)\s*$", "", text).strip()
        # 연속 공백·중복 꺾쇠 정리
        text = re.sub(r"\s{2,}", " ", text)
        if text:
            results.append(text)
    return results


def _parse_aladin_physical_info(html: str, api_description: str = "", naver_description: str = "") -> dict:
    """
    알라딘 상세 페이지 HTML에서 형태사항(300 필드용) 데이터를 파싱한다.

    api_description: 알라딘 TTB API item["description"] — 책소개 섹션이 JS 렌더링으로만
                     존재할 때(정적 HTML에 없을 때) 대체 소스로 사용.

    Returns:
        {
            "300": MRK 문자열,
            "300_subfields": [Subfield, ...],
            "page_value": int | None,
            "size_value": str | None,
            "illustration_possibility": str,
        }
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── 5개 텍스트 소스 추출 ────────────────────────────────────
    title_el      = soup.select_one("span.Ere_bo_title")
    subtitle_el   = soup.select_one("span.Ere_sub1_title")
    title_text    = title_el.get_text(strip=True)    if title_el    else ""
    subtitle_text = subtitle_el.get_text(strip=True) if subtitle_el else ""

    # 책소개: 네이버 API → 알라딘 HTML → TTB API 순서로 fallback
    desc_text = naver_description or _find_section_text(soup, "책소개") or api_description

    # 출판사 제공 소개: 레이블이 책마다 다름 — 순서대로 시도
    pub_desc_text = ""
    for _pub_label in ("출판사 제공 책소개", "출판사 소개"):
        pub_desc_text = _find_section_text(soup, _pub_label)
        if pub_desc_text:
            break

    # 형태사항 블록 파싱
    a_part: str = ""
    b_part: str = ""
    c_part: str = ""
    page_value:  int | None = None
    size_value:  str | None = None

    form_wrap = soup.select_one("div.conts_info_list1")
    if form_wrap:
        for item in [s.strip() for s in form_wrap.stripped_strings if s.strip()]:
            # $a — 쪽수
            if re.search(r"(쪽|p)\s*$", item):
                m = re.search(r"\d+", item)
                if m:
                    page_value = int(m.group())
                    a_part = f"{m.group()} p."

            # $c — 크기 (mm 단위 → cm 변환)
            elif "mm" in item:
                m = re.search(r"(\d+)\s*[*x×X]\s*(\d+)", item)
                if m:
                    width  = int(m.group(1))
                    height = int(m.group(2))
                    size_value = f"{width}x{height}mm"
                    if width == height or width > height or width < height / 2:
                        c_part = f"{math.ceil(width/10)}x{math.ceil(height/10)} cm"
                    else:
                        c_part = f"{math.ceil(height/10)} cm"

    # 목차(TOC) 파싱: 레이블 "목차" 섹션 전체 텍스트 (Short+All 포함)
    toc_text = _find_section_text(soup, "목차")

    # 알라딘 카테고리 경로 추출
    aladin_categories = _parse_aladin_categories(soup)

    # $b — 삽화 감지 (소스별, 5개)
    has_illus, illus_label, illus_detail = detect_illustrations_with_sources(
        title_text, subtitle_text, desc_text, toc_text, pub_desc_text
    )
    if has_illus:
        b_part = illus_label  # type: ignore[assignment]

    # ---- pymarc Subfield 리스트 구성 ----
    subfields_300: list[Subfield] = []
    if a_part:
        subfields_300.append(Subfield("a", a_part))
    if b_part:
        subfields_300.append(Subfield("b", b_part))
    if c_part:
        subfields_300.append(Subfield("c", c_part))

    # ---- MRK 텍스트 구성 (KORMARC 구두점 규칙 준수) ----
    mrk_parts: list[str] = []

    if a_part:
        chunk = f"$a{a_part}"
        if b_part:
            chunk += f" :$b{b_part}"
        mrk_parts.append(chunk)
    elif b_part:
        mrk_parts.append(f"$b{b_part}")

    if c_part:
        if mrk_parts:
            mrk_parts.append(f"; $c{c_part}")
        else:
            mrk_parts.append(f"$c{c_part}")

    # 아무 정보도 없으면 fallback
    if not mrk_parts:
        mrk_parts = ["$a1책."]
        subfields_300 = [Subfield("a", "1책.")]

    field_300 = "=300  \\\\" + " ".join(mrk_parts)

    return {
        "300": field_300,
        "300_subfields": subfields_300,
        "page_value": page_value,
        "size_value": size_value,
        "illustration_possibility": illus_label if illus_label else "없음",
        "toc_text": toc_text,
        "illus_diagnosis": {
            "sources": {
                "네이버 책소개":   naver_description,
                "제목":           title_text,
                "부제":           subtitle_text,
                "책소개":         desc_text,
                "목차":           toc_text,
                "출판사 제공 소개": pub_desc_text,
            },
            "알라딘 카테고리": aladin_categories,
            "detected": illus_detail,
            "_boxes": _diagnose_boxes(soup),
        },
    }


def _fetch_aladin_detail_page(link: str, api_description: str = "", naver_description: str = "") -> tuple[dict, str | None]:
    """
    알라딘 상세 페이지를 HTTP로 가져와 형태사항 dict를 반환한다.

    api_description: TTB API로부터 미리 받은 책소개 — JS 렌더링 섹션 대체용.

    Returns:
        (결과 dict, 에러 메시지 또는 None)
    """
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    try:
        res = requests.get(link, headers=_HEADERS, timeout=15)
        res.raise_for_status()
        res.encoding = "utf-8"
        return _parse_aladin_physical_info(res.text, api_description, naver_description), None
    except Exception as e:
        return {
            "300": "=300  \\\\$a1책. [상세 페이지 파싱 오류]",
            "300_subfields": [Subfield("a", "1책 [파싱 실패]")],
            "page_value": None,
            "size_value": None,
            "illustration_possibility": "정보 없음",
        }, f"Aladin 상세 페이지 크롤링 예외: {e}"


_EMPTY_DIAG = {"toc_text": "", "illus_diagnosis": {"sources": {}, "detected": []}}


def build_300_field(item: dict, isbn: str = "", secrets: dict | None = None) -> tuple[str, Field, dict]:
    """
    알라딘 item dict에서 알라딘 상세 페이지 링크를 꺼내 300 필드를 생성한다.

    Args:
        item:    알라딘 API item dict
        isbn:    ISBN-13 (네이버 API 호출용)
        secrets: 런타임 시크릿 dict (NAVER_SEARCH_KEY_ID/SECRET 포함)

    Returns:
        (mrk 문자열, pymarc.Field 객체, 진단 dict)
        진단 dict: {"toc_text": str, "illus_diagnosis": {"sources": {}, "detected": []}}
    """
    _FALLBACK_MRK    = "=300  \\\\$a1책."
    _FALLBACK_SF     = [Subfield("a", "1책.")]

    try:
        aladin_link     = (item or {}).get("link", "")
        api_description = (item or {}).get("description", "") or ""

        # 네이버 책소개 수집
        naver_description = ""
        if isbn and secrets:
            naver_description = _fetch_naver_description(
                isbn,
                (secrets or {}).get("NAVER_SEARCH_KEY_ID", ""),
                (secrets or {}).get("NAVER_SEARCH_KEY_SECRET", ""),
            )
            if naver_description:
                _dbg(f"[300] 네이버 책소개 수집됨 ({len(naver_description)}자)")
            else:
                _dbg("[300] 네이버 책소개 없음 (미수록 또는 키 미설정)")

        if not aladin_link:
            _dbg_err("[300] 알라딘 링크 없음 → 기본값 사용")
            return _FALLBACK_MRK, Field(
                tag="300", indicators=["\\", "\\"], subfields=_FALLBACK_SF
            ), _EMPTY_DIAG

        detail_result, err = _fetch_aladin_detail_page(
            aladin_link, api_description=api_description, naver_description=naver_description
        )

        tag_300       = detail_result.get("300")           or _FALLBACK_MRK
        subfields_300 = detail_result.get("300_subfields") or _FALLBACK_SF
        toc_text      = detail_result.get("toc_text", "")
        illus_diag    = detail_result.get("illus_diagnosis", {"sources": {}, "detected": []})

        f_300 = Field(tag="300", indicators=[" ", " "], subfields=subfields_300)

        if err:
            _dbg_err(f"[300] {err}")
        _dbg(f"[300] {tag_300}")

        illus = detail_result.get("illustration_possibility")
        if illus and illus != "없음":
            _dbg(f"[300] 삽화 감지됨 → {illus}")
        if toc_text:
            _dbg(f"[300] 목차 추출됨 ({len(toc_text)}자)")

        return tag_300, f_300, {"toc_text": toc_text, "illus_diagnosis": illus_diag}

    except Exception as e:
        _dbg_err(f"[300] 생성 중 예외: {e}")
        return (
            "=300  \\\\$a1책. [예외]",
            Field(tag="300", indicators=["\\", "\\"],
                  subfields=[Subfield("a", "1책. [예외]")]),
            _EMPTY_DIAG,
        )


def build_300_mrk(item: dict) -> str:
    """300 MRK 문자열만 필요한 경우의 편의 래퍼."""
    tag_300, _, _diag = build_300_field(item)
    return tag_300 or "=300  \\$a1책."
