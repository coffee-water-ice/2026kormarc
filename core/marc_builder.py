"""
core/marc_builder.py
- MarcBuilder: pymarc Record 객체 + MRK 텍스트를 동시에 조립하는 클래스
- mrk_str_to_field: MRK 문자열 한 줄 → pymarc.Field 객체 변환 유틸
"""

import re
from pymarc import Record, Field, Subfield


class MarcBuilder:
    def __init__(self):
        self.rec = Record(to_unicode=True, force_utf8=True)
        self.lines: list[str] = []

    # 컨트롤필드 (001–008)
    def add_ctl(self, tag: str, data: str):
        if not data:
            return
        self.rec.add_field(Field(tag=tag, data=str(data)))
        self.lines.append(f"={tag}  {data}")

    # 데이터필드 (020/041/245/260/300/490/500/700/90010 …)
    def add(self, tag: str, ind1: str, ind2: str, subfields: list[tuple[str, str]]):
        sf = [(c, v) for c, v in subfields if (v or "") != ""]
        if not sf:
            return

        # 인디케이터 자동 보정 (백슬래시 → 공백)
        ind1 = " " if not ind1 or ind1 == "\\" else ind1
        ind2 = " " if not ind2 or ind2 == "\\" else ind2

        self.rec.add_field(Field(
            tag=tag,
            indicators=[ind1, ind2],
            subfields=[Subfield(c, v) for c, v in sf]
        ))

        parts = "".join(f"${c}{v}" for c, v in sf)
        self.lines.append(f"={tag}  {ind1}{ind2}{parts}")

    def mrk_text(self) -> str:
        return "\n".join(self.lines)


def mrk_str_to_field(line) -> Field | None:
    """
    MRK 형식 문자열 한 줄을 pymarc.Field 객체로 변환한다.
    예: '=260  \\\\$a서울 :$b민음사,$c2023' → Field(260, ...)
    변환 불가 시 None 반환.
    """
    if line is None:
        return None

    # 이미 Field 유사 객체면 그대로 반환 (덕타이핑)
    try:
        if getattr(line, "tag", None) is not None and (
            hasattr(line, "data") or hasattr(line, "subfields")
        ):
            return line
    except Exception:
        pass

    # 문자열 확보
    if not isinstance(line, str):
        try:
            line = str(line)
        except Exception:
            return None

    s = line.strip()
    if not s.startswith("=") or len(s) < 8:
        return None

    # 태그/인디케이터/본문 분해
    m = re.match(r"^=(\d{3})\s{2}(.)(.)(.*)$", s)
    if m:
        tag, ind1_raw, ind2_raw, tail = m.groups()
    else:
        # 컨트롤필드 패턴 (=008  <data>)
        m_ctl = re.match(r"^=(\d{3})\s\s(.*)$", s)
        if not m_ctl:
            return None
        tag, data = m_ctl.group(1), m_ctl.group(2).strip()
        if tag.isdigit() and int(tag) < 10:
            return Field(tag=tag, data=data) if data else None
        return None

    # 컨트롤필드 (태그 번호 < 10)
    if tag.isdigit() and int(tag) < 10:
        data = (ind1_raw + ind2_raw + tail).strip()
        return Field(tag=tag, data=data) if data else None

    # 인디케이터 역슬래시(\) → 공백
    ind1 = " " if ind1_raw == "\\" else ind1_raw
    ind2 = " " if ind2_raw == "\\" else ind2_raw

    subs_part = tail or ""
    if "$" not in subs_part:
        return None  # 서브필드 없으면 의미없음

    # 서브필드 파싱 ($a ... $b ... 대소문자 모두 허용)
    subfields: list[Subfield] = []
    i, L = 0, len(subs_part)
    while i < L:
        if subs_part[i] != "$":
            i += 1
            continue
        if i + 1 >= L:
            break
        code = subs_part[i + 1]
        j = i + 2
        while j < L and subs_part[j] != "$":
            j += 1
        value = subs_part[i + 2:j].strip()
        if code and value:
            subfields.append(Subfield(code, value))
        i = j

    if not subfields:
        return None

    return Field(tag=tag, indicators=[ind1, ind2], subfields=subfields)


def record_to_mrk(rec: Record) -> str:
    """pymarc.Record → 사람이 읽을 수 있는 MRK 텍스트 전체 변환."""
    lines = []
    leader = (
        rec.leader.decode("utf-8")
        if isinstance(rec.leader, (bytes, bytearray))
        else str(rec.leader)
    )
    lines.append("=LDR  " + leader)

    for f in rec.get_fields():
        tag = f.tag
        if tag.isdigit() and int(tag) < 10:
            lines.append(f"={tag}  " + (f.data or ""))
            continue

        ind1 = (f.indicators[0] if getattr(f, "indicators", None) else " ") or " "
        ind2 = (f.indicators[1] if getattr(f, "indicators", None) else " ") or " "
        ind1_disp = "\\" if ind1 == " " else ind1
        ind2_disp = "\\" if ind2 == " " else ind2

        parts = ""
        subs = getattr(f, "subfields", None)
        if isinstance(subs, list) and subs and isinstance(subs[0], Subfield):
            for s in subs:
                parts += f"${s.code}{s.value}"
        elif isinstance(subs, list):
            it = iter(subs)
            for code, val in zip(it, it):
                parts += f"${code}{val}"
        else:
            try:
                for s in f:
                    parts += f"${s.code}{s.value}"
            except Exception:
                pass

        lines.append(f"={tag}  {ind1_disp}{ind2_disp}{parts}")

    return "\n".join(lines)
