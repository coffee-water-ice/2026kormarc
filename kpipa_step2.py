"""
kpipa_step2.py
기존 Excel 파일에 신규 수집 데이터를 추가하고 중복을 처리한다.
my project 폴더에서 '출판사정리_리스트_*.xlsx' 중 가장 최신 파일을 자동으로 찾는다.

실행: python kpipa_step2.py [--pages N]
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from kpipa_scraper import fetch_all


def process_duplicates(combined: pd.DataFrame) -> pd.DataFrame:
    """
    combined: 기존 DataFrame + 신규 DataFrame을 concat한 결과.
    신규 행은 '비고' 컬럼이 NaN 또는 빈 문자열.

    중복 기준: 출판사명 + 지역 모두 일치.
    """
    combined = combined.copy()
    combined["비고"] = combined["비고"].fillna("").astype(str)

    # 신규/기존 구분 플래그 (_is_new: 신규 수집 행)
    combined["_is_new"] = combined["비고"].str.strip() == ""

    result_rows: list[dict] = []

    # 출판사명 + 지역 조합을 고유 식별자로 사용
    # 동명이지만 지역이 다른 출판사는 서로 다른 법인이므로 별개로 처리
    for (_, _), group in combined.groupby(["출판사명", "지역"], sort=False):
        old_rows = group[~group["_is_new"]]
        new_rows = group[group["_is_new"]]

        if old_rows.empty and not new_rows.empty:
            # 신규 등록
            for _, r in new_rows.iterrows():
                row = r.to_dict()
                row["비고"] = "신규 등록"
                result_rows.append(row)

        elif not old_rows.empty and new_rows.empty:
            # 최신 목록에서 사라짐
            for _, r in old_rows.iterrows():
                row = r.to_dict()
                row["비고"] = "확인필요"
                result_rows.append(row)

        else:
            # 출판사명 + 지역 모두 일치 → 동일 출판사, 중복 제거 후 유지
            row = new_rows.iloc[0].to_dict()
            row["비고"] = "유지"
            result_rows.append(row)

    result = pd.DataFrame(result_rows)
    if result.empty:
        return pd.DataFrame(columns=["순번", "출판사명", "지역", "전화번호", "비고"])

    result = result.drop(columns=["_is_new"], errors="ignore")
    result = result[["순번", "출판사명", "지역", "전화번호", "비고"]]
    result = result.sort_values("순번", ascending=False).reset_index(drop=True)
    return result


def main(max_pages: int | None = None) -> None:
    base_dir = Path(__file__).parent
    files = sorted(base_dir.glob("출판사정리_리스트_*.xlsx"))
    if not files:
        raise FileNotFoundError(
            "기존 Excel 파일을 찾을 수 없습니다. 먼저 kpipa_step1.py를 실행하세요."
        )
    latest = files[-1]
    print(f"기존 파일: {latest}")

    old_df = pd.read_excel(latest)
    # 비고 컬럼이 없으면 추가
    if "비고" not in old_df.columns:
        old_df["비고"] = ""

    print("신규 데이터 수집 중...")
    new_df = fetch_all(until_no=1, max_pages=max_pages)
    new_df["비고"] = ""

    combined = pd.concat([old_df, new_df], ignore_index=True)
    result_df = process_duplicates(combined)

    신규 = (result_df["비고"] == "신규 등록").sum()
    확인 = (result_df["비고"] == "확인필요").sum()
    print(f"처리 완료 — 신규 등록: {신규}건 / 확인필요: {확인}건 / 전체: {len(result_df)}건")

    filename = f"출판사정리_리스트_{date.today().strftime('%Y%m%d')}.xlsx"
    path = base_dir / filename
    result_df.to_excel(path, index=False)
    print(f"저장 완료: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KPIPA 출판사 DB 갱신 + 중복 처리")
    parser.add_argument("--pages", type=int, default=None, help="테스트용: N 페이지만 수집")
    args = parser.parse_args()
    main(max_pages=args.pages)
