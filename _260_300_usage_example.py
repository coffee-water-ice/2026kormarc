"""
260/300 필드 생성 — 새 구조에서의 사용 예시
(기존 generate_all_oneclick 내부 로직 중 260·300 담당 부분)

실제 app.py / Flask 라우터에서는 아래 패턴으로 호출한다.
"""

# ── 새 구조 import ──────────────────────────────────────────
from core.field_rules import build_260_field, build_300_field
from api.external_apis import build_pub_location_bundle


def build_260_and_300(isbn: str, item: dict, secrets: dict):
    """
    ISBN + 알라딘 item dict로 260/300 필드를 생성하는 통합 함수.

    Args:
        isbn:    ISBN-13 문자열
        item:    알라딘 API item.extra dict
        secrets: st.secrets (또는 동등한 dict)

    Returns:
        {
            "tag_260": MRK 문자열,
            "f_260":   pymarc.Field | None,
            "tag_300": MRK 문자열,
            "f_300":   pymarc.Field,
            "bundle":  발행지 조회 번들 (디버그·008 country_code 포함),
            "pubyear": 발행연도 4자리 문자열,
        }
    """
    # ── 260 발행사항 ─────────────────────────────────────────
    publisher_raw = (item or {}).get("publisher", "") or ""
    pubdate       = (item or {}).get("pubDate", "")  or ""
    pubyear       = pubdate[:4] if len(pubdate) >= 4 else ""

    # 발행지 3단계 조회 (KPIPA DB → 임프린트 → 문체부)
    bundle = build_pub_location_bundle(isbn, publisher_raw, secrets)

    tag_260, f_260 = build_260_field(
        place_display  = bundle["place_display"],
        publisher_name = publisher_raw,
        pubyear        = pubyear,
    )

    # ── 300 형태사항 ─────────────────────────────────────────
    tag_300, f_300 = build_300_field(item)

    return {
        "tag_260": tag_260,
        "f_260":   f_260,
        "tag_300": tag_300,
        "f_300":   f_300,
        "bundle":  bundle,
        "pubyear": pubyear,
    }


# ── 기존 코드(generate_all_oneclick)와의 대응표 ─────────────
#
#  [기존]                              [새 구조]
#  ─────────────────────────────────────────────────────────
#  build_pub_location_bundle(isbn, p)  api/external_apis.py
#  build_260(place, pub, year)         core/field_rules.py → build_260_field()
#  mrk_str_to_field(tag_260)           core/marc_builder.py (내부에서 호출)
#  search_aladin_detail_page(link)     core/field_rules.py (내부 헬퍼)
#  parse_aladin_physical_book_info()   core/field_rules.py (내부 헬퍼)
#  detect_illustrations(text)          core/field_rules.py (공개 함수)
#  build_300_from_aladin_detail(item)  core/field_rules.py → build_300_field()
#  build_300_mrk(item)                 core/field_rules.py → build_300_mrk()
