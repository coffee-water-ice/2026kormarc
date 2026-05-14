"""
kpipa_step1.py
KPIPA 출판사 목록 전체를 수집하여 Excel 파일로 저장한다.
최초 1회 실행용이지만 언제든 재실행 가능.

실행: python kpipa_step1.py [--pages N]
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from kpipa_scraper import fetch_all


def main(max_pages: int | None = None) -> None:
    df = fetch_all(until_no=1, max_pages=max_pages)
    df["비고"] = ""

    filename = f"출판사정리_리스트_{date.today().strftime('%Y%m%d')}.xlsx"
    path = Path(__file__).parent / filename
    df.to_excel(path, index=False)
    print(f"저장 완료: {path} ({len(df)}개)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KPIPA 출판사 전체 수집 → Excel")
    parser.add_argument("--pages", type=int, default=None, help="테스트용: N 페이지만 수집")
    args = parser.parse_args()
    main(max_pages=args.pages)
