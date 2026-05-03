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

def build_260(place_display: str, publisher_name: str, pubyear: str) -> str:
    """
    260 MRK 문자열을 생성한다.

    Args:
        place_display:   정규화된 발행지 표시명 (예: "서울", "발행지 미상")
        publisher_name:  출판사명 원본 (예: "민음사")
        pubyear:         발행연도 4자리 문자열 (예: "2023")

    Returns:
        MRK 한 줄 문자열 (예: "=260  \\\\$a서울 :$b민음사,$c2023")
    """
    place = place_display or "발행지 미상"
    pub   = publisher_name or "발행처 미상"
    year  = pubyear or "발행년 미상"
    return f"=260  \\\\$a{place} :$b{pub},$c{year}"


def build_260_field(place_display: str, publisher_name: str, pubyear: str) -> tuple[str, Field | None]:
    """
    260 MRK 문자열과 pymarc.Field 객체를 함께 반환한다.

    Returns:
        (mrk_str, Field 객체)  — Field 변환 실패 시 (mrk_str, None)
    """
    tag_260 = build_260(place_display, publisher_name, pubyear)
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


def _parse_aladin_physical_info(html: str) -> dict:
    """
    알라딘 상세 페이지 HTML에서 형태사항(300 필드용) 데이터를 파싱한다.

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

    # 제목·부제·책소개 (삽화 감지용)
    title_text    = (soup.select_one("span.Ere_bo_title") or object).__class__.__name__  # 더미
    title_el      = soup.select_one("span.Ere_bo_title")
    subtitle_el   = soup.select_one("span.Ere_sub1_title")
    desc_el       = soup.select_one("div.Ere_prod_mconts_R")
    title_text    = title_el.get_text(strip=True)    if title_el    else ""
    subtitle_text = subtitle_el.get_text(strip=True) if subtitle_el else ""
    desc_text     = desc_el.get_text(" ", strip=True) if desc_el    else ""

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

    # $b — 삽화 감지
    combined = " ".join(filter(None, [title_text, subtitle_text, desc_text]))
    has_illus, illus_label = detect_illustrations(combined)
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
    }


def _fetch_aladin_detail_page(link: str) -> tuple[dict, str | None]:
    """
    알라딘 상세 페이지를 HTTP로 가져와 형태사항 dict를 반환한다.

    Returns:
        (결과 dict, 에러 메시지 또는 None)
    """
    try:
        res = requests.get(link, timeout=15)
        res.raise_for_status()
        return _parse_aladin_physical_info(res.text), None
    except Exception as e:
        return {
            "300": "=300  \\\\$a1책. [상세 페이지 파싱 오류]",
            "300_subfields": [Subfield("a", "1책 [파싱 실패]")],
            "page_value": None,
            "size_value": None,
            "illustration_possibility": "정보 없음",
        }, f"Aladin 상세 페이지 크롤링 예외: {e}"


def build_300_field(item: dict) -> tuple[str, Field]:
    """
    알라딘 item dict에서 알라딘 상세 페이지 링크를 꺼내 300 필드를 생성한다.

    Args:
        item: 알라딘 API item dict (item.extra)

    Returns:
        (mrk 문자열, pymarc.Field 객체)
    """
    _FALLBACK_MRK    = "=300  \\\\$a1책."
    _FALLBACK_SF     = [Subfield("a", "1책.")]
    _FALLBACK_FIELD  = Field(tag="300", indicators=[" ", " "], subfields=_FALLBACK_SF)

    try:
        aladin_link = (item or {}).get("link", "")
        if not aladin_link:
            _dbg_err("[300] 알라딘 링크 없음 → 기본값 사용")
            return _FALLBACK_MRK, Field(
                tag="300", indicators=["\\", "\\"], subfields=_FALLBACK_SF
            )

        detail_result, err = _fetch_aladin_detail_page(aladin_link)

        tag_300       = detail_result.get("300")       or _FALLBACK_MRK
        subfields_300 = detail_result.get("300_subfields") or _FALLBACK_SF

        f_300 = Field(tag="300", indicators=[" ", " "], subfields=subfields_300)

        if err:
            _dbg_err(f"[300] {err}")
        _dbg(f"[300] {tag_300}")

        illus = detail_result.get("illustration_possibility")
        if illus and illus != "없음":
            _dbg(f"[300] 삽화 감지됨 → {illus}")

        return tag_300, f_300

    except Exception as e:
        _dbg_err(f"[300] 생성 중 예외: {e}")
        return (
            "=300  \\\\$a1책. [예외]",
            Field(tag="300", indicators=["\\", "\\"],
                  subfields=[Subfield("a", "1책. [예외]")])
        )


def build_300_mrk(item: dict) -> str:
    """300 MRK 문자열만 필요한 경우의 편의 래퍼."""
    tag_300, _ = build_300_field(item)
    return tag_300 or "=300  \\$a1책."
