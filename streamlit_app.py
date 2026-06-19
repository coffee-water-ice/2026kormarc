from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

from api_client import convert_isbn, convert_batch, query_kpipa


st.set_page_config(page_title="ISBN → MARC", page_icon="📚", layout="wide")
st.title("ISBN → 300 MARC 변환기(KPIPA API 포함)")
st.caption("FastAPI 백엔드(`/api/convert`)를 호출해 MARC 결과를 보여줍니다.")

_SOURCE_LABEL = {
    "ISBN_PREFIX_DB":    "📖 ISBN발행자번호-발행지 연결표",
    "KPIPA_API→DB":      "🔗 KPIPA API → 발행처명-주소 연결표",
    "KPIPA_API→MCST":    "🔗 KPIPA API → 문체부",
    "ALADIN→DB":         "📚 알라딘 → 발행처명-주소 연결표",
    "ALADIN→IMPRINT→DB": "📚 알라딘 → 임프린트 → 발행처명-주소 연결표",
    "ALADIN→MCST":       "📚 알라딘 → 문체부",
    "FALLBACK":          "⚠️ 모든 경로 실패 (출판지 미상)",
}


def _extract_field(mrk_text: str, tag: str) -> str:
    """mrk_text에서 특정 태그 행만 추출."""
    for line in (mrk_text or "").splitlines():
        if line.startswith(f"={tag}"):
            return line
    return ""


def _results_to_dataframe(results: list[dict]) -> pd.DataFrame:
    rows = []
    for r in results:
        meta = r.get("meta") or {}
        mrk = r.get("mrk_text", "")
        rows.append({
            "ISBN":       r.get("isbn", ""),
            "제목":       meta.get("title", ""),
            "발행처":     meta.get("publisher", ""),
            "발행지":     meta.get("pub_location", ""),
            "발행년도":   meta.get("pub_year", ""),
            "260필드":    _extract_field(mrk, "260"),
            "300필드":    _extract_field(mrk, "300"),
            "발행지 출처": _SOURCE_LABEL.get(meta.get("bundle_source", ""), meta.get("bundle_source", "")),
            "오류":       r.get("error", "") or "",
        })
    return pd.DataFrame(rows)


def _to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="MARC변환결과")
        ws = writer.sheets["MARC변환결과"]
        # 열 너비 자동 조정
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)
    return buf.getvalue()


# ── 탭 구성 ────────────────────────────────────────────────────
tab_single, tab_batch = st.tabs(["단건 변환", "일괄 변환 (엑셀 다운로드)"])


# ══════════════════════════════════════════════════════════════
# 탭 1: 단건 변환
# ══════════════════════════════════════════════════════════════
with tab_single:
    isbn = st.text_input("ISBN-13", placeholder="예: 9788937462849").strip()

    if st.button("변환 실행", type="primary", key="btn_single"):
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
                label = _SOURCE_LABEL.get(source, source or "알 수 없음")
                st.caption(f"발행지 출처: **{label}**")

                # ── 300 $b 삽화 감지 상세 ──────────────────────
                illus_diag = meta.get("illus_diagnosis", {})
                with st.expander("300 $b 삽화 감지 상세", expanded=True):
                    sources = illus_diag.get("sources", {})
                    naver_desc = sources.get("네이버 책소개", "")
                    st.markdown("**네이버 책소개**")
                    st.text_area(
                        "네이버 책소개",
                        value=naver_desc or "(없음)",
                        height=200,
                        disabled=True,
                        key="illus_src_naver",
                        label_visibility="collapsed",
                    )

                    st.markdown("**키워드 감지 결과**")
                    detected = illus_diag.get("detected", [])
                    if detected:
                        df_illus = pd.DataFrame(detected)[["label", "keyword", "source"]]
                        df_illus.columns = ["KORMARC 레이블", "감지 키워드", "출처"]
                        st.dataframe(df_illus, hide_index=True, use_container_width=True)
                    else:
                        st.info("감지된 삽화 키워드 없음")

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


# ══════════════════════════════════════════════════════════════
# 탭 2: 일괄 변환
# ══════════════════════════════════════════════════════════════
with tab_batch:
    st.markdown("ISBN을 한 줄에 하나씩 입력하거나, ISBN 열이 있는 엑셀/CSV 파일을 업로드하세요.")

    col_text, col_file = st.columns([1, 1], gap="large")

    with col_text:
        st.markdown("**직접 입력**")
        raw_text = st.text_area(
            "ISBN 목록 (한 줄에 하나)",
            placeholder="9788937462849\n9791162540091\n9788936434120",
            height=200,
            key="batch_text",
        )

    with col_file:
        st.markdown("**파일 업로드** (Excel / CSV)")
        uploaded = st.file_uploader(
            "ISBN 열 이름: `isbn` 또는 `ISBN`",
            type=["xlsx", "xls", "csv"],
            key="batch_file",
        )

    # ISBN 목록 수집
    isbn_list: list[str] = []

    if raw_text.strip():
        for line in raw_text.splitlines():
            cleaned = re.sub(r"[^0-9X]", "", line.strip().upper())
            if len(cleaned) in (10, 13):
                isbn_list.append(cleaned)

    if uploaded is not None:
        try:
            if uploaded.name.endswith(".csv"):
                file_df = pd.read_csv(uploaded, dtype=str)
            else:
                file_df = pd.read_excel(uploaded, dtype=str)

            col_name = next(
                (c for c in file_df.columns if c.strip().lower() == "isbn"), None
            )
            if col_name:
                for val in file_df[col_name].dropna():
                    cleaned = re.sub(r"[^0-9X]", "", str(val).strip().upper())
                    if len(cleaned) in (10, 13):
                        isbn_list.append(cleaned)
                st.success(f"파일에서 ISBN {len(isbn_list)}건 인식")
            else:
                st.warning("파일에 `isbn` 또는 `ISBN` 열이 없습니다.")
        except Exception as e:
            st.error(f"파일 읽기 실패: {e}")

    # 중복 제거, 순서 유지
    seen: set[str] = set()
    unique_isbns = [x for x in isbn_list if not (x in seen or seen.add(x))]

    if unique_isbns:
        st.info(f"변환 대상: **{len(unique_isbns)}건** (중복 제거 후)")

    if st.button("일괄 변환 실행", type="primary", key="btn_batch", disabled=not unique_isbns):
        progress = st.progress(0, text="변환 준비 중...")
        status_text = st.empty()

        # 10건씩 나눠서 진행률 표시
        chunk_size = 10
        all_results: list[dict] = []
        total = len(unique_isbns)

        for i in range(0, total, chunk_size):
            chunk = unique_isbns[i : i + chunk_size]
            status_text.text(f"{i + 1} ~ {min(i + chunk_size, total)} / {total} 변환 중...")
            jobs = [[isbn] for isbn in chunk]
            chunk_results = convert_batch(jobs)
            all_results.extend(chunk_results)
            progress.progress(min(i + chunk_size, total) / total, text=f"{min(i + chunk_size, total)}/{total} 완료")

        progress.empty()
        status_text.empty()

        df = _results_to_dataframe(all_results)
        success_count = df["오류"].eq("").sum()
        fail_count = total - success_count

        st.success(f"변환 완료 — 성공 {success_count}건 / 실패 {fail_count}건")
        st.dataframe(df, use_container_width=True, hide_index=True)

        excel_bytes = _to_excel_bytes(df)
        st.download_button(
            label="엑셀 파일 다운로드 (.xlsx)",
            data=excel_bytes,
            file_name="marc_변환결과.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
