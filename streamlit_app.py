from __future__ import annotations

import streamlit as st

from api_client import convert_isbn, query_kpipa


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
