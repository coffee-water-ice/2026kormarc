from __future__ import annotations

import streamlit as st

from api_client import convert_isbn


st.set_page_config(page_title="ISBN → MARC", page_icon="📚", layout="wide")
st.title("ISBN → 300 MARC 변환기")
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
            st.subheader("메타 정보")
            st.json(result.get("meta", {}))
