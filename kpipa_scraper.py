"""
kpipa_scraper.py
KPIPA 출판사 목록 크롤링 공통 모듈 (Playwright 기반).
step1~step3이 import해서 사용한다. 단독 실행 시 --pages N 으로 테스트 수집.

설치: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import time

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

_BASE_URL = "https://bnk.kpipa.or.kr/home/v3/addition/adiPblshrInfoList"


def _extract_rows(page: Page) -> list[dict]:
    """page.content() HTML을 BeautifulSoup으로 파싱해 출판사 데이터를 추출한다."""
    soup = BeautifulSoup(page.content(), "html.parser")
    result = []
    for tr in soup.select("table.srch tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        seq_text = tds[0].get_text(strip=True)
        if not seq_text.isdigit():
            continue
        result.append({
            "순번": int(seq_text),
            "출판사명": tds[1].get_text(strip=True),
            "지역": tds[2].get_text(strip=True),
            "전화번호": tds[3].get_text(strip=True),
        })
    return result


def _get_total_pages(page: Page) -> int:
    """li.fraction 텍스트 "1 / 208" 에서 전체 페이지 수를 반환한다."""
    soup = BeautifulSoup(page.content(), "html.parser")
    fraction = soup.select_one("li.fraction")
    if not fraction:
        raise RuntimeError("li.fraction 없음 — 페이지 구조가 바뀌었을 수 있습니다.")
    return int(fraction.get_text(strip=True).split("/")[1].strip())


def _go_to_page(page: Page, page_no: int) -> None:
    """fnPblshrInfoList() JS 함수 직접 호출로 AJAX 페이지 이동."""
    page.evaluate(f"fnPblshrInfoList({page_no})")
    # 네트워크 요청이 끝나고 테이블이 갱신될 때까지 대기
    page.wait_for_load_state("networkidle", timeout=15_000)
    page.wait_for_selector("table.srch tbody tr", timeout=10_000)


def fetch_all(until_no: int = 1, max_pages: int | None = None) -> pd.DataFrame:
    """
    KPIPA 출판사 목록 전체를 수집하여 DataFrame으로 반환한다.

    Args:
        until_no:  이 순번 이하가 나오면 수집 종료 (기본 1 = 전체 수집).
        max_pages: 최대 페이지 수 제한 (테스트용).

    Returns:
        pandas DataFrame (컬럼: 순번, 출판사명, 지역, 전화번호)
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print(f"페이지 로드: {_BASE_URL}")
        page.goto(_BASE_URL, wait_until="networkidle", timeout=30_000)

        total = _get_total_pages(page)
        if max_pages is not None:
            total = min(total, max_pages)
        print(f"총 {total} 페이지 수집 시작 (until_no={until_no})")

        all_rows: list[dict] = []
        reached_end = False

        # 1페이지는 이미 로드돼 있음
        rows = _extract_rows(page)
        for row in rows:
            all_rows.append(row)
            if row["순번"] <= until_no:
                reached_end = True
                break
        print(f"  p1/{total}: {len(rows)}건 수집 (누계 {len(all_rows)})")

        for page_no in range(2, total + 1):
            if reached_end:
                break
            _go_to_page(page, page_no)
            rows = _extract_rows(page)
            if not rows:
                print(f"  p{page_no}: 행 없음 — 종료")
                break
            for row in rows:
                all_rows.append(row)
                if row["순번"] <= until_no:
                    reached_end = True
                    break
            print(f"  p{page_no}/{total}: {len(rows)}건 수집 (누계 {len(all_rows)})")
            time.sleep(0.3)

        browser.close()

    df = pd.DataFrame(all_rows, columns=["순번", "출판사명", "지역", "전화번호"])
    print(f"수집 완료: 총 {len(df)}건")
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KPIPA 출판사 목록 테스트 수집")
    parser.add_argument("--pages", type=int, default=3, help="수집할 최대 페이지 수")
    args = parser.parse_args()

    df = fetch_all(until_no=1, max_pages=args.pages)
    print(df.to_string())