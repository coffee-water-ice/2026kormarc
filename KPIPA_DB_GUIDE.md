# KPIPA 출판사 DB 구축 지시서

## 개요

KPIPA(한국출판인회의) 출판사 정보를 수집하여 서지데이터 자동화 시스템의
발행지 조회 DB(`출판사 DB` Google Sheets)를 구축·갱신하는 스크립트 모음.

### 파일 구성

| 파일 | 역할 | 단독 실행 |
|------|------|----------|
| `kpipa_scraper.py` | KPIPA 크롤링 공통 모듈 | ✗ (import 전용) |
| `kpipa_step1.py` | 최초 전체 수집 → Excel 저장 | ✓ |
| `kpipa_step2.py` | 기존 Excel에 신규 데이터 추가·중복 처리 | ✓ |
| `kpipa_step3.py` | Google Sheets 갱신 (최종 목표, 독립 실행 가능) | ✓ |
| `.github/workflows/kpipa_weekly.yml` | 매주 자동 실행 (GitHub Actions) | — |

### 의존 라이브러리 (requirements.txt에 추가)
```
requests
beautifulsoup4
pandas
openpyxl
gspread
google-auth
```

---

## 공통 환경 설정

### 크롤링 대상
- URL: `https://bnk.kpipa.or.kr/home/v3/addition/adiPblshrInfoList`
- 접근 방식: **requests + BeautifulSoup** (로그인 불필요, 공개 접근 확인됨)
- 페이지당 행 수: 약 20개
- 총 페이지: 약 209페이지 (순번 내림차순 — 첫 페이지가 가장 최신)

### Google Sheets 인증
- `api/external_apis.py`의 `load_publisher_db()`와 동일한 인증 패턴 사용
- `.streamlit/secrets.toml`의 `[gspread]` 섹션에서 Service Account 읽기
- 환경변수 `GSPREAD_CREDENTIALS` (JSON 문자열)도 지원 — GitHub Actions용

### 대상 스프레드시트
- 파일명: `출판사 DB`
- 시트명: `발행처명–주소 연결표`
- 백업 시트명: `구)발행처명–주소 연결표`

### 시트/Excel 컬럼 구조
| A: 순번 | B: 출판사명 | C: 지역 | D: 전화번호 | E: 비고 |
|---------|------------|---------|------------|--------|

> ⚠️ 기존 `load_publisher_db()`는 B열(출판사명), C열(지역)을 읽으므로
> 이 순서를 반드시 유지해야 기존 코드가 수정 없이 동작한다.

---

## kpipa_scraper.py — 공통 크롤링 모듈

### 역할
KPIPA 출판사 목록 페이지를 순회하며 데이터를 수집한다.
step1~step3가 모두 이 모듈을 import해서 사용한다.

### 구현할 함수

#### `fetch_page(page_no: int) -> list[dict]`
- 지정 페이지의 출판사 정보를 수집하여 리스트 반환
- 각 dict: `{"순번": int, "출판사명": str, "지역": str, "전화번호": str}`
- CSS 선택자: `table.srch tbody tr`
- 컬럼 인덱스: [0]=순번, [1]=출판사명, [2]=지역, [3]=전화번호

#### `fetch_all(until_no: int = 1, max_pages: int = None) -> pd.DataFrame`
- `fetch_page()`를 반복 호출하여 전체 데이터 수집
- `until_no=1`: 순번 1이 나올 때까지 수집 (기본값 — 전체 수집)
- `max_pages`: 페이지 수 제한 (테스트용, 예: `--pages 3`)
- 페이지 이동: URL 파라미터 또는 POST body에 `pageIndex` 파라미터 사용
  - 실제 파라미터명은 코드 작성 전 사이트 Network 탭(F12)에서 확인 필요
- 반환: pandas DataFrame (컬럼: 순번, 출판사명, 지역, 전화번호)

#### `get_total_pages() -> int`
- 첫 페이지 응답에서 전체 페이지 수 추출
- 기존 `crawler_step1.py`의 `li.fraction` 텍스트 ("1 / 209") 파싱 참고

### 실행 인수 (argparse)
```
python kpipa_scraper.py --pages 3   # 3페이지만 테스트 수집 후 콘솔 출력
```

---

## kpipa_step1.py — 최초 전체 수집 → Excel

### 역할
KPIPA 출판사 목록 전체를 수집하여 Excel 파일로 저장한다.
최초 1회 실행용이지만 언제든 재실행 가능.

### 구현 내용

```python
from kpipa_scraper import fetch_all
import pandas as pd
from datetime import date
from pathlib import Path

def main():
    df = fetch_all(until_no=1)          # 순번 1까지 전체 수집
    df["비고"] = ""                      # 비고 컬럼 추가 (빈 값)

    filename = f"출판사정리_리스트_{date.today().strftime('%Y%m%d')}.xlsx"
    path = Path(__file__).parent / filename
    df.to_excel(path, index=False)
    print(f"저장 완료: {path} ({len(df)}개)")

if __name__ == "__main__":
    main()
```

### 실행 방법
```bash
python kpipa_step1.py
# 결과: 출판사정리_리스트_20260513.xlsx 생성
```

### 실행 인수
```
--pages N   테스트용: N페이지만 수집
```

---

## kpipa_step2.py — 기존 Excel 갱신 + 중복 처리

### 역할
기존 Excel 파일에 새로 수집한 데이터를 추가하고 중복을 처리한다.
GitHub Actions 없이 로컬에서 수동 또는 자동 실행 가능.

### 구현 내용

#### 1. 기존 파일 탐색
```python
# my project 폴더에서 '출판사정리_리스트_*.xlsx' 패턴으로 가장 최근 파일 자동 탐색
files = sorted(Path(__file__).parent.glob("출판사정리_리스트_*.xlsx"))
latest = files[-1]   # 날짜순 마지막 = 가장 최신
```

#### 2. 데이터 수집 및 병합
```python
old_df = pd.read_excel(latest)
new_df = fetch_all(until_no=1)          # 신규 수집
combined = pd.concat([old_df, new_df], ignore_index=True)
```

#### 3. 중복 처리 함수 `process_duplicates(combined: pd.DataFrame) -> pd.DataFrame`
중복 기준: **출판사명 + 지역 모두 일치**

| 상황 | 비고 값 |
|------|--------|
| 신규 수집에만 있는 행 | `신규 등록` |
| 기존에만 있는 행 (최신 목록에서 사라짐) | `확인필요` |
| 양쪽 있음 + 지역 동일 | 중복 행 삭제, 남은 행에 `유지` |
| 양쪽 있음 + 지역 다름 | 기존 삭제, 신규 유지, 비고: `"구지역"에서 변경` |

#### 4. 저장
```python
filename = f"출판사정리_리스트_{date.today().strftime('%Y%m%d')}.xlsx"
path = Path(__file__).parent / filename
result_df.to_excel(path, index=False)
```

### 실행 방법
```bash
python kpipa_step2.py
# 자동으로 최신 Excel 파일 찾아서 갱신 후 새 날짜 파일로 저장
```

---

## kpipa_step3.py — Google Sheets 갱신 (독립 실행 가능)

### 역할
KPIPA 데이터를 수집하여 Google Sheets `발행처명–주소 연결표`를 갱신한다.
**이 파일 단독으로 전체 기능이 동작**해야 한다. (STEP 1·2 없이도 실행 가능)
GitHub Actions에서 이 파일만 실행하면 된다.

### 구현 내용

#### Google Sheets 인증 (`get_gspread_client()`)
`api/external_apis.py`의 기존 인증 패턴을 그대로 복사·적용:
```python
# 우선순위 1: 환경변수 GSPREAD_CREDENTIALS (GitHub Actions용)
# 우선순위 2: .streamlit/secrets.toml [gspread] 섹션 (로컬용)
```

#### 실행 흐름
```
1. KPIPA 전체 수집 (fetch_all — kpipa_scraper.py 또는 내장 fallback)
2. Sheets에서 기존 데이터 읽기
3. 기존 데이터를 '구)발행처명–주소 연결표'에 전체 복사 (백업)
4. '발행처명–주소 연결표'에 신규 데이터 추가 (마지막 행 아래)
5. process_duplicates() 실행 → 비고 컬럼 처리
6. 처리된 DataFrame을 Sheets에 일괄 반영 (batch_update)
```

#### 독립 실행 보장 방법
- `kpipa_scraper.py`를 import하되, import 실패 시 내장 fallback 함수로 대체
- `kpipa_step3.py` 안에 최소한의 스크래핑 코드를 직접 포함시켜
  파일 하나만 복사해도 동작하도록 구성

### 실행 방법
```bash
# 로컬 실행
python kpipa_step3.py

# 테스트 (Sheets 실제 반영 없이 결과만 출력)
python kpipa_step3.py --dry-run

# 페이지 제한 (테스트용)
python kpipa_step3.py --pages 3
```

---

## GitHub Actions 설정

### `.github/workflows/kpipa_weekly.yml`

```yaml
name: KPIPA 출판사 DB 주간 갱신

on:
  schedule:
    - cron: '0 1 * * 1'    # 매주 월요일 01:00 UTC = 10:00 KST
  workflow_dispatch:         # GitHub UI에서 수동 실행 버튼

jobs:
  update-publisher-db:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Python 설정
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 의존성 설치
        run: pip install -r requirements.txt

      - name: KPIPA DB 갱신 실행
        env:
          GSPREAD_CREDENTIALS: ${{ secrets.GSPREAD_CREDENTIALS }}
        run: python kpipa_step3.py
```

### GitHub Secrets 등록 방법
1. GitHub 리포지토리 → Settings → Secrets and variables → Actions
2. `GSPREAD_CREDENTIALS` 이름으로 New repository secret 추가
3. Value: `.streamlit/secrets.toml`의 `[gspread]` 섹션 JSON 값 전체 붙여넣기

---

## 개발 순서 권장

1. **`kpipa_scraper.py`** 작성 및 테스트
   - `python kpipa_scraper.py --pages 3` 으로 소량 수집 확인
   - 페이지네이션 파라미터명을 Network 탭에서 먼저 확인
2. **`kpipa_step1.py`** 작성 → Excel 생성 확인
3. **`kpipa_step2.py`** 작성 → 중복 처리 로직 샘플 데이터로 테스트
4. **`kpipa_step3.py`** 작성 → `--dry-run`으로 Sheets 반영 전 미리보기
5. **`.github/workflows/kpipa_weekly.yml`** 작성 → `workflow_dispatch`로 수동 트리거 테스트

---

## 주의사항

- `kpipa_scraper.py`의 페이지네이션 파라미터명(`pageIndex` 등)은
  실제 사이트의 Network 탭(F12)에서 요청 형식 확인 후 결정
- 수집 중 오류 발생 시 중단 지점부터 재시작할 수 있도록
  `checkpoint.json` 저장 로직 추가 권장 (`crawler_1_100.py` 참고)
- Google Sheets `batch_update` 사용 시 API 할당량(분당 60회) 주의
  → 1,000행 이상 반영 시 `time.sleep(1)` 추가
- `.streamlit/secrets.toml`은 절대 GitHub에 커밋하지 말 것
  (`.gitignore`에 `.streamlit/` 추가 확인)
