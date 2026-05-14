"""
kpipa_step3.py
KPIPA 데이터를 수집하여 Google Sheets '발행처명–주소 연결표'를 갱신한다.
이 파일 단독으로 전체 기능이 동작한다 (step1·step2 불필요).
GitHub Actions에서 이 파일만 실행한다.

실행:
  python kpipa_step3.py              # 실제 Sheets 반영
  python kpipa_step3.py --dry-run    # 결과 미리보기만 (Sheets 반영 없음)
  python kpipa_step3.py --pages 3    # 테스트: 3페이지만 수집
"""
from __future__ import annotations

import argparse
import json
import os
import time

import pandas as pd

# ── kpipa_scraper import ───────────────────────────────────────────────────
try:
    from kpipa_scraper import fetch_all  # type: ignore
except ImportError as _e:
    raise ImportError(
        "kpipa_scraper.py를 찾을 수 없습니다.\n"
        "kpipa_step3.py와 같은 폴더에 kpipa_scraper.py가 있어야 합니다.\n"
        f"원래 오류: {_e}"
    ) from _e


# ── Google Sheets 인증 ────────────────────────────────────────────────────

def get_gspread_client():
    """
    환경변수 GSPREAD_CREDENTIALS (GitHub Actions) 또는
    .streamlit/secrets.toml [gspread] (로컬) 에서 인증 정보를 읽는다.
    """
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    env_creds = os.environ.get("GSPREAD_CREDENTIALS", "").strip()
    keyfile_dict = None

    if env_creds:
        if (env_creds.startswith('"') and env_creds.endswith('"')) or \
           (env_creds.startswith("'") and env_creds.endswith("'")):
            env_creds = env_creds[1:-1]
        try:
            keyfile_dict = json.loads(env_creds)
        except json.JSONDecodeError:
            try:
                keyfile_dict = json.loads(env_creds.replace("\n", "\\n"))
            except Exception as e:
                raise ValueError(f"GSPREAD_CREDENTIALS JSON 형식 오류: {e}")
    else:
        # .streamlit/secrets.toml 읽기
        secrets_path = os.path.join(
            os.path.dirname(__file__), ".streamlit", "secrets.toml"
        )
        if not os.path.exists(secrets_path):
            raise FileNotFoundError(
                "GSPREAD_CREDENTIALS 환경변수도 없고 .streamlit/secrets.toml도 없습니다."
            )
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            import importlib
            _tomli = importlib.util.find_spec("tomli")
            if _tomli is None:
                raise ImportError("Python 3.10 이하에서는 'pip install tomli' 가 필요합니다.")
            import tomli as tomllib  # type: ignore[no-redef,import-not-found]
        with open(secrets_path, "rb") as f:
            toml_data = tomllib.load(f)
        keyfile_dict = toml_data.get("gspread")

    if not keyfile_dict:
        raise ValueError("gspread 인증 정보를 찾을 수 없습니다.")

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        keyfile_dict,
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )
    return gspread.authorize(creds)


# ── 중복 처리 (step2와 동일 로직) ─────────────────────────────────────────

def process_duplicates(combined: pd.DataFrame) -> pd.DataFrame:
    combined = combined.copy()
    combined["비고"] = combined["비고"].fillna("").astype(str)
    combined["_is_new"] = combined["비고"].str.strip() == ""

    result_rows: list[dict] = []
    for (_, _), group in combined.groupby(["출판사명", "지역"], sort=False):
        old_rows = group[~group["_is_new"]]
        new_rows = group[group["_is_new"]]

        if old_rows.empty and not new_rows.empty:
            for _, r in new_rows.iterrows():
                row = r.to_dict(); row["비고"] = "신규 등록"; result_rows.append(row)
        elif not old_rows.empty and new_rows.empty:
            for _, r in old_rows.iterrows():
                row = r.to_dict(); row["비고"] = "확인필요"; result_rows.append(row)
        else:
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


# ── Sheets 갱신 ───────────────────────────────────────────────────────────

_SHEET_NAME = "발행처명–주소 연결표"
_BACKUP_SHEET_NAME = "구)발행처명–주소 연결표"
_SPREADSHEET_NAME = "출판사 DB"


def _df_to_values(df: pd.DataFrame) -> list[list]:
    """DataFrame → Sheets에 쓸 2D 리스트 (헤더 포함)."""
    header = [["순번", "출판사명", "지역", "전화번호", "비고"]]
    rows = df[["순번", "출판사명", "지역", "전화번호", "비고"]].astype(str).values.tolist()
    return header + rows


def update_sheets(df: pd.DataFrame, dry_run: bool = False) -> None:
    """
    1. 기존 시트 데이터를 백업 시트에 복사
    2. 신규 데이터와 합쳐 중복 처리
    3. 처리된 DataFrame을 메인 시트에 일괄 반영
    """
    gc = get_gspread_client()
    sh = gc.open(_SPREADSHEET_NAME)

    # ── 기존 데이터 읽기 ──────────────────────────────────────────────────
    ws_main = sh.worksheet(_SHEET_NAME)
    existing_values = ws_main.get_all_values()
    data_rows = existing_values[1:] if len(existing_values) > 1 else []

    if data_rows:
        old_df = pd.DataFrame(data_rows, columns=["순번", "출판사명", "지역", "전화번호", "비고"])
        old_df["순번"] = pd.to_numeric(old_df["순번"], errors="coerce").fillna(0).astype(int)
        # 기존 행의 비고가 비어있으면 "기존"으로 채워 신규 행과 구별
        old_df["비고"] = old_df["비고"].fillna("").str.strip()
        old_df.loc[old_df["비고"] == "", "비고"] = "기존"
    else:
        old_df = pd.DataFrame(columns=["순번", "출판사명", "지역", "전화번호", "비고"])

    print(f"기존 시트 데이터: {len(old_df)}건")

    # ── 백업 ──────────────────────────────────────────────────────────────
    if not dry_run and data_rows:
        try:
            ws_backup = sh.worksheet(_BACKUP_SHEET_NAME)
        except Exception:
            ws_backup = sh.add_worksheet(_BACKUP_SHEET_NAME, rows=len(existing_values) + 10, cols=10)

        ws_backup.clear()
        ws_backup.update(values=existing_values, value_input_option="RAW")
        print(f"백업 완료: '{_BACKUP_SHEET_NAME}'")

    # ── 중복 처리 ─────────────────────────────────────────────────────────
    df["비고"] = ""
    combined = pd.concat([old_df, df], ignore_index=True)
    result_df = process_duplicates(combined)

    신규 = (result_df["비고"] == "신규 등록").sum()
    확인 = (result_df["비고"] == "확인필요").sum()
    print(f"처리 결과 — 신규: {신규}건 / 확인필요: {확인}건 / 전체: {len(result_df)}건")

    if dry_run:
        print("=== DRY-RUN: 아래 결과를 Sheets에 반영하지 않습니다 ===")
        print(result_df.head(20).to_string())
        return

    # ── Sheets 일괄 반영 ──────────────────────────────────────────────────
    values = _df_to_values(result_df)
    ws_main.clear()

    # 필요한 행 수보다 시트가 작으면 미리 확장
    needed_rows = len(values) + 50
    if ws_main.row_count < needed_rows:
        ws_main.resize(rows=needed_rows)
        print(f"시트 행 수 확장: {needed_rows}행")

    # 1,000행 이상이면 500행씩 나눠서 업데이트 (API 할당량 대응)
    chunk_size = 500
    for i in range(0, len(values), chunk_size):
        chunk = values[i : i + chunk_size]
        start_row = i + 1
        end_row = i + len(chunk)
        ws_main.update(
            range_name=f"A{start_row}:E{end_row}",
            values=chunk,
            value_input_option="RAW",
        )
        if i + chunk_size < len(values):
            time.sleep(1)  # API 할당량(분당 60회) 대응

    print(f"Google Sheets 갱신 완료: '{_SHEET_NAME}' ({len(result_df)}건)")


# ── 진입점 ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="KPIPA → Google Sheets 자동 갱신")
    parser.add_argument("--dry-run", action="store_true", help="Sheets 반영 없이 결과만 출력")
    parser.add_argument("--pages", type=int, default=None, help="테스트용: N 페이지만 수집")
    args = parser.parse_args()

    print("KPIPA 출판사 데이터 수집 중...")
    df = fetch_all(until_no=1, max_pages=args.pages)
    update_sheets(df, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
