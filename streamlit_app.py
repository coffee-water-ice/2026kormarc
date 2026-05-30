from __future__ import annotations

import streamlit as st

from api_client import convert_isbn, query_kpipa, query_nlk_isbn


st.set_page_config(page_title="ISBN → MARC", page_icon="📚", layout="wide")
st.title("ISBN → 300 MARC 변환기(KPIPA API 포함)")
st.caption("FastAPI 백엔드(`/api/convert`)를 호출해 MARC 결과를 보여줍니다.")

isbn = st.text_input("ISBN-13", placeholder="예: 9788937462849").strip()

if st.button("변환 실행", type="primary"):
    if not isbn:
        st.warning("ISBN을 입력해 주세요.")
    else:
        with st.spinner("변환 중..."):
            result = convert_isbn(isbn)

        if result.get("error"):
            st.error(result["error"])
        else:
            st.success("변환 완료")
            st.subheader("MRK 텍스트")
            st.code(result.get("mrk_text", ""), language="text")

            meta = result.get("meta", {})
            source = meta.get("bundle_source", "")
            _SOURCE_LABEL = {
                "ISBN_PREFIX_DB":    "📖 ISBN발행자번호-발행지 연결표",
                "KPIPA_API→DB":      "🔗 KPIPA API → 발행처명-주소 연결표",
                "KPIPA_API→MCST":    "🔗 KPIPA API → 문체부",
                "ALADIN→DB":         "📚 알라딘 → 발행처명-주소 연결표",
                "ALADIN→IMPRINT→DB": "📚 알라딘 → 임프린트 → 발행처명-주소 연결표",
                "ALADIN→MCST":       "📚 알라딘 → 문체부",
                "FALLBACK":          "⚠️ 모든 경로 실패 (출판지 미상)",
            }
            label = _SOURCE_LABEL.get(source, source or "알 수 없음")
            st.caption(f"발행지 출처: **{label}**")

            st.subheader("메타 정보")
            st.json(meta)

        st.divider()
        st.subheader("KPIPA API 조회 결과")
        with st.spinner("KPIPA 조회 중..."):
            kpipa = query_kpipa(isbn)

        if kpipa.get("error"):
            st.error(kpipa["error"])
        else:
            st.success("KPIPA 조회 완료")
            st.json(kpipa.get("data", {}))

        st.divider()
        st.subheader("국립중앙도서관 ISBN 서지정보 API")
        with st.spinner("NLK API 조회 중..."):
            nlk = query_nlk_isbn(isbn)

        if nlk.get("error"):
            st.error(nlk["error"])
        else:
            raw = nlk.get("data", {})
            docs = raw.get("docs", [])
            total = raw.get("TOTAL_COUNT", "0")

            if not docs:
                st.info(f"검색 결과 없음 (TOTAL_COUNT={total})")
                st.json(raw)
            else:
                st.success(f"NLK 조회 완료 — {total}건 중 첫 번째 레코드")
                doc = docs[0]

                _NLK_FIELDS = [
                    ("표제", "TITLE"),
                    ("저자", "AUTHOR"),
                    ("발행처", "PUBLISHER"),
                    ("ISBN", "EA_ISBN"),
                    ("ISBN 부가기호", "EA_ADD_CODE"),
                    ("세트 ISBN", "SET_ISBN"),
                    ("판사항", "EDITION_STMT"),
                    ("페이지 → MARC 300 $a", "PAGE"),
                    ("책크기 → MARC 300 $c", "BOOK_SIZE"),
                    ("총서명", "SERIES_TITLE"),
                    ("발행예정일", "PUBLISH_PREDATE"),
                    ("KDC", "KDC"),
                    ("DDC", "DDC"),
                    ("형태사항", "FORM"),
                    ("전자책 여부", "EBOOK_YN"),
                    ("CIP 신청 여부", "CIP_YN"),
                    ("CIP 제어번호", "CONTROL_NO"),
                ]
                rows = [(label, doc.get(key, "")) for label, key in _NLK_FIELDS if doc.get(key)]
                mid = (len(rows) + 1) // 2
                col1, col2 = st.columns(2)
                with col1:
                    for label, val in rows[:mid]:
                        st.markdown(f"**{label}**: {val}")
                with col2:
                    for label, val in rows[mid:]:
                        st.markdown(f"**{label}**: {val}")

                _URL_FIELDS = [
                    ("목차", "BOOK_TB_CNT_URL"),
                    ("책소개", "BOOK_INTRODUCTION_URL"),
                    ("책요약", "BOOK_SUMMARY_URL"),
                    ("출판사 홈페이지", "PUBLISHER_URL"),
                ]
                links = [f"[{label}]({doc[key]})" for label, key in _URL_FIELDS if doc.get(key)]
                if links:
                    st.markdown("**링크**: " + " | ".join(links))

                with st.expander("전체 응답 JSON"):
                    st.json(raw)
