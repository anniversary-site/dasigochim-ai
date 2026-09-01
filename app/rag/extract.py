"""재해조사의견서 PDF → 문체 견본 조각.

**여기서 뽑는 것은 '사실' 이 아니라 '문장의 골격' 이다.** 이 코퍼스는 산업재해
(톤백·지게차·축사 지붕) 이고 우리 서비스는 대학 캠퍼스 안전신고라, 내용은 애초에
전용될 수 없다. 쓸모 있는 것은 감독관이 사실을 적는 방식 — 개조식 종결, 확인과
추정을 어미로 가르는 습관, [상태]→[사고형태]→[결과] 로 이어지는 위험 서술,
[대상]+[방법]+[기준] 으로 끝나는 조치 문장 — 이다.

## 색인에서 통째로 빼는 것

1·2장(사업장 개요, 재해자 인적사항)은 **색인하지 않는다.** 상호·소재지·성명·
출생년도·전화번호가 표로 들어 있는 구간이고, 문체 견본으로서의 값은 0 이다.
개인정보를 지우는 가장 확실한 방법은 잘 지우는 것이 아니라 안 넣는 것이다.
남은 3·4·5장에서도 날짜·시각·행정구역·인명 라벨은 ``redact()`` 로 가린다.

## 조각의 단위

조각 하나 = 불릿 하나 + 그에 딸린 ``-``·``※`` 줄들이다. 이 단위가 곧 감독관이
'사실 하나' 를 적는 단위여서, 잘라 놓아도 문장 구조가 온전하다.

    ❍ 톤백 적재상태 불량                      ← headline (명사구 견본)
      - 800kg 에 달하는 … 보관하고 있었음.     ← body (서술 견본)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger(__name__)

# ── 역할 ────────────────────────────────────────────────────────────────
# 신고서 항목과 짝을 맞춘 이름이다. 검색은 이 역할을 주 신호로 쓴다.
ROLE_HAZARD = "hazard"            # 위험요인 → hazard / risk
ROLE_CAUSE = "cause"              # 사고원인 → risk
ROLE_ACTION = "action"            # 안전조치 권고 → action_urgent / action_fundamental
ROLE_OBSERVATION = "observation"  # 조사·확인 내용 → photo_analysis
ROLE_NARRATIVE = "narrative"      # 경위·과정 → risk 의 서사 부분

ROLE_LABELS = {
    ROLE_HAZARD: "작업 현장의 위험요인",
    ROLE_CAUSE: "사고원인",
    ROLE_ACTION: "안전조치 권고",
    ROLE_OBSERVATION: "조사·확인 내용",
    ROLE_NARRATIVE: "재해발생 경위·과정",
}

# 소제목에서 역할을 정한다. 12건의 목차를 훑어 만든 목록이라 표기 흔들림
# (조사·확인 / 조사ㆍ확인 / 조사․확인 / 조사 및 확인) 을 그대로 담았다.
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ROLE_ACTION, ("안전조치", "재해예방", "재발방지", "예방대책", "개선대책", "방지조치", "권고")),
    (ROLE_HAZARD, ("위험요인", "위험 요인")),
    (ROLE_CAUSE, ("사고원인", "사고 원인", "재해발생원인", "재해발생 원인", "발생원인")),
    (ROLE_OBSERVATION, ("조사", "확인", "적정성", "착용", "상태", "실시 여부")),
    (ROLE_NARRATIVE, ("경위", "과정", "공정", "상황", "작업")),
)

# 최상위 장 번호 → 기본 역할. 소제목이 어느 키워드에도 안 걸릴 때 쓴다.
_CHAPTER_ROLE = {"3": ROLE_NARRATIVE, "4": ROLE_OBSERVATION, "5": ROLE_CAUSE}
INDEXED_CHAPTERS = frozenset(_CHAPTER_ROLE)

# ── 줄 분류 ─────────────────────────────────────────────────────────────
_CHAPTER = re.compile(r"^(\d)\.\s*(\S.*)$")
_SUBHEAD = re.compile(r"^([가-힣])\.\s*(\S.*)$")
_NUMHEAD = re.compile(r"^(\d)\)\s*(\S.*)$")
# 추출기가 □·■ 를 Wingdings 그대로 옮겨 'm'/'o' 로 떨어지는 문서가 있다.
_BULLET = re.compile(r"^(?:[❍○■⦁•◇]\s*|[mo]\s+)(?=\S)")
_SUBBULLET = re.compile(r"^[-–‐]\s+")
_NOTE = re.compile(r"^[※*]\s*")
_PAGE_NO = re.compile(r"^-\s*\d+\s*-$")
_MARKER_ONLY = re.compile(r"^[-–‐❍○■⦁•◇※*mo]$")   # 값 없이 불릿만 남은 줄
# 시각별 경과 항목. 불릿을 떼고 redact 를 거친 표제가 시각 표기로 시작한다.
_TIMELINE = re.compile(r"^[\[【(]?\s*○○:○○")
_CAPTION = re.compile(r"^[\[【〔]")  # [사진 캡션] · 【조사 주석】
_ENUM = re.compile(r"^[➊-➎▶⇨⇒→]\s*")


@dataclass(frozen=True)
class Chunk:
    """문체 견본 한 조각."""

    chunk_id: str
    doc_id: str
    section: str          # "5. 조사자 의견 > 가. 작업 현장의 위험요인"
    role: str
    headline: str         # 불릿 한 줄. 명사구 견본으로 그 자체가 쓸모 있다
    body: list[str]       # 딸린 서술 줄들

    @property
    def text(self) -> str:
        return "\n".join([self.headline, *self.body]).strip()

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "section": self.section,
            "role": self.role,
            "headline": self.headline,
            "body": list(self.body),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        return cls(
            chunk_id=str(data["chunk_id"]),
            doc_id=str(data["doc_id"]),
            section=str(data.get("section", "")),
            role=str(data.get("role", ROLE_OBSERVATION)),
            headline=str(data.get("headline", "")),
            body=[str(x) for x in data.get("body", [])],
        )


# ── 비식별 처리 ─────────────────────────────────────────────────────────
# 1·2장을 통째로 빼고 남은 본문에도 사건을 특정하는 값이 흩어져 있다. 문체를 배우는 데
# 필요 없는 값이므로 지운다. 지우고 나면 문장의 뼈대만 남는다 — 그게 목적이다.
_SIDO = (
    "서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청북|충청남|충북|충남"
    "|전라북|전라남|전북|전남|경상북|경상남|경북|경남|제주"
)
# 시/도 → (특별자치)(도|시) → 하위 행정구역이 여러 단계 붙는다: "제주특별자치도 제주시 애월읍"
_ADDRESS = re.compile(
    "(?:" + _SIDO + r")(?:특별자치|특별|광역)?(?:도|시)?"
    r"(?:\s*[가-힣]{1,6}(?:시|군|구|읍|면|동|리))*"
)
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 3장 표 머리("발생형태 깔림 기인물 톤백")가 본문 앞에 붙어 나온다. 날짜를 ○ 로
    # 바꾸기 전에 떼어내야 한다 — 뒤로 미루면 값의 경계를 잡을 수 없다.
    (re.compile(r"^발생형태\s*[가-힣]+\s*기인물\s*[가-힣]+\s*"), ""),
    # 날짜 — 표기가 제각각이라 넓게 잡는다
    (re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일(\s*\([월화수목금토일]\))?"), "○○○○년 ○월 ○일"),
    (re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월"), "○○○○년 ○월"),
    # 24.07.28.(일) / ’24.7.28. — 앞의 따옴표는 있을 수도 없을 수도 있다
    (re.compile(r"[‘’']?\s*\d{2,4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?(\s*\([월화수목금토일]\))?"), "○○○○.○.○."),
    (re.compile(r"[‘’']\s*\d{2}\s*년(?=산|도|\s)"), "’○○년"),
    (re.compile(r"\d{4}\s*년(?=\s*수확|산|도)"), "○○○○년"),
    # 시각
    (re.compile(r"\d{1,2}\s*시\s*\d{1,2}\s*분\s*(경|쯤)?"), "○○:○○경"),
    # "12:00분경" 처럼 분 표기가 섞여 나오는 문서가 있다. 분까지 삼켜야 '분경' 이 남지 않는다
    (re.compile(r"\d{1,2}\s*:\s*\d{2}\s*분?\s*(경|쯤)?"), "○○:○○경"),
    # 행정구역
    (_ADDRESS, "○○"),
    # 시/도 없이 시·군만 적힌 주소. '소재' 가 붙은 자리만 지운다 — 그 표지가 없으면
    # "작업 시", "공사 시" 까지 함께 지워진다.
    (re.compile(r"[가-힣]{2,6}(?:시|군|구|읍|면|리)\s*(?=소재)"), "○○ "),
    # 연락처·관리번호
    (re.compile(r"\d{2,4}-\d{3,4}-\d{4}"), "○○○-○○○○-○○○○"),
    (re.compile(r"\d{3}-\d{2}-\d{5}"), "○○○-○○-○○○○○"),
    # 익명 라벨. 원문이 이미 성명을 A·B·C 로 가려 두었지만, 그 알파벳조차 남기면
    # 견본을 여러 개 붙였을 때 같은 사건의 등장인물이 재구성된다.
    (re.compile(r"\(\s*[A-Z]\s*\)"), "(○)"),
    (re.compile(r"(?<![A-Za-z])[A-Z]\s*(대표자?|사장|소장|반장|팀장|씨)(?![A-Za-z])"), r"○ \1"),
    # 홀로 선 대문자 한 글자 = 가려진 이름·상호("C 개인주택 태양광 …"). 'A형 사다리'
    # 같은 규격 표기는 뒤에 한글이 붙으므로 걸리지 않는다.
    (re.compile(r"(?<![A-Za-z0-9])[A-Z](?![A-Za-z0-9가-힣])"), "○"),
    # 원문이 상호를 지우고 남긴 따옴표 껍데기
    (re.compile("[“\"]\\s*[”\"]"), "○○"),
)

# 결합 발음 부호·제로폭 문자 등 추출 부산물. 눈에 안 보이지만 남겨 두면 날짜·인명
# 패턴이 어긋난다 (실제로 "폐트레일러́'24.07.28." 가 날짜 규칙을 비껴갔다).
_COMBINING = re.compile("[̀-ͯ​-‏﻿]")


def redact(text: str) -> str:
    """사건을 특정하는 값을 ○ 로 가린다. 문장 구조는 건드리지 않는다."""
    text = _COMBINING.sub("", text)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ── PDF → 줄 ────────────────────────────────────────────────────────────

def read_lines(pdf_path: Path) -> list[str]:
    """PDF 한 건을 줄 목록으로 만든다. 줄바꿈으로 끊긴 문장을 다시 잇는다.

    추출기는 조판 줄바꿈을 그대로 내보내서 한 문장이 서너 줄로 쪼개진다. 이어 붙일 때
    앞 줄이 **공백으로 끝났는지**를 보고 띄어쓰기 여부를 정한다 — 한글은 어절 중간에서도
    줄이 넘어가므로("예상/되므로"), 무조건 공백으로 이으면 없던 띄어쓰기가 생긴다.
    그래서 이어 붙이는 동안에는 줄 끝 공백을 지우지 않는다. 그 공백이 유일한 단서다.

    제목 줄(장·소제목·번호)은 이어 붙이지 않는다. 제목 뒤에 오는 본문 첫 줄까지
    빨아들이면 제목이 문단 하나만큼 길어져 역할 판정이 어긋난다.
    """
    from pypdf import PdfReader

    out: list[str] = []
    open_for_join = False
    for page in PdfReader(str(pdf_path)).pages:
        for raw in (page.extract_text() or "").splitlines():
            line = raw.rstrip("\r\n")
            stripped = line.strip()
            if not stripped or _PAGE_NO.match(stripped) or _MARKER_ONLY.match(stripped):
                continue
            if not out or not open_for_join or _is_new_line(stripped):
                out.append(line)
                open_for_join = not _is_heading(stripped)
            else:
                joiner = " " if out[-1].endswith((" ", "\t")) else ""
                out[-1] = out[-1].rstrip(" \t") + joiner + line.lstrip()
    return [line.strip() for line in out]


def _is_heading(stripped: str) -> bool:
    return bool(
        _CHAPTER.match(stripped) or _SUBHEAD.match(stripped) or _NUMHEAD.match(stripped)
    )


def _is_new_line(stripped: str) -> bool:
    """이 줄이 새 항목의 시작인가, 앞 줄의 이어짐인가."""
    return bool(
        _is_heading(stripped)
        or _BULLET.match(stripped)
        or _SUBBULLET.match(stripped)
        or _NOTE.match(stripped)
        or _CAPTION.match(stripped)
        or _ENUM.match(stripped)
    )


# ── 줄 → 조각 ───────────────────────────────────────────────────────────

# 한 조각이 문단 하나를 넘어가면 견본이 아니라 사건 요약이 된다. 문체를 보여 주는 데는
# 서너 줄이면 충분하고, 길수록 내용이 딸려 들어간다.
MAX_BODY_LINES = 4

# 조판이 어긋난 구간은 띄어쓰기가 통째로 사라진다("지인을통하여재해자는당일첫출근하여").
# 문체 견본으로 쓰면 모델이 그 띄어쓰기를 따라 하므로 버린다.
_UNSPACED_RUN = re.compile(r"[가-힣]{22,}")

# 신고서에 대응하는 칸이 없는 서술. 재해조사의견서에는 있어야 하지만 견본으로 뽑히면
# 모델이 "관계자 면담을 근거로 작성함", "병원으로 이송하였으나 사망함" 같은,
# 신고서에 있을 수 없는 문장을 흉내 낸다.
_META = re.compile(
    r"의견서는|근거로\s*작성|합동\s*조사|관계자\s*(면담|진술)|진술에\s*의함|작성하였음"
    r"|조사자\s*의견|첨부|참조|아래와\s*같음|다음과\s*같음"
    r"|119|구급|병원으로\s*이송|이송하였으나|사망함|응급실|심폐소생"
)
# "1) 사업장 개요", "가. 현장개요", "규 격 :" 처럼 값이 뒤따라야 뜻이 서는 표제.
_LABEL_ONLY = re.compile(r"^[(\[]?\d+[)\].]|^[가-힣][가-힣\s]{0,10}[:：]\s*$")

# 개조식 종결. 긴 것부터 본다 — "하여야 함" 을 "함" 으로 세면 조치 문장의 특징이 사라진다.
# 문체 통계(profile)와 조각 선별이 같은 목록을 봐야 "규격에 맞는 문장" 의 정의가 하나다.
ENDINGS: tuple[str, ...] = (
    "하여야 함", "하여야 할 것으로 판단됨", "해야 함", "하도록 함", "필요함", "권고함",
    "요망", "바람", "것으로 추정됨", "것으로 판단됨", "것으로 보임", "추정됨", "판단됨",
    "확인됨", "위험이 있음", "가능성이 있음", "있음", "없음", "되었음", "하였음", "이었음",
    "하고 있었음", "되어 있음", "재해임", "됨", "임", "함", "음",
)
ENDING_RE = re.compile("(" + "|".join(re.escape(e) for e in ENDINGS) + r")\s*[.]?\s*$")


# 표제 하나로 홀로 설 수 있는 최소 길이. 이보다 짧으면 문체가 아니라 딱지다.
MIN_STANDALONE_HEADLINE = 14


def _is_exemplar(headline: str, body: list[str]) -> bool:
    """이 조각이 '문체 견본' 인가.

    기준은 하나다 — **개조식 문장이 최소 한 줄은 있을 것.** 재해조사의견서에는 서술
    말고도 공사 개요표·기인물 제원표·시각별 경과표가 섞여 있는데, 그 조각들은 값을
    나열할 뿐 문체를 보여 주지 않는다. 종결어미가 하나도 없다는 사실이 그 구분선이다.

    명사구 표제는 아래 서술과 짝일 때만 문체를 보여 준다. 짝이 없으면 표제 자체가
    개조식 한 문장이어야 한다 — "교체용 칼라강판 설치작업(2명)" 은 문체가 아니라 항목명이다.
    """
    if not body:
        return len(headline) >= MIN_STANDALONE_HEADLINE and bool(ENDING_RE.search(headline))
    return any(ENDING_RE.search(line) for line in (headline, *body))


def _keeps(line: str) -> bool:
    """견본으로 쓸 만한 줄인가."""
    return bool(
        line
        and not _UNSPACED_RUN.search(line)
        and not _META.search(line)
        and not _LABEL_ONLY.match(line)
    )


def _role_for(*titles: str) -> str | None:
    """소제목에서 역할을 고른다. 목록 순서가 우선순위다 (조치 > 위험 > 원인 > …)."""
    for role, keywords in _ROLE_KEYWORDS:
        for title in titles:
            if title and any(word in title for word in keywords):
                return role
    return None


def parse_document(pdf_path: Path) -> list[Chunk]:
    """PDF 한 건 → 조각 목록. 3·4·5장만 본다."""
    doc_id = pdf_path.stem
    lines = read_lines(pdf_path)

    chunks: list[Chunk] = []
    chapter = ""       # "5. 조사자 의견"
    chapter_no = ""
    subhead = ""       # "가. 작업 현장의 위험요인"
    numhead = ""       # "1) 톤백 적재 상태"
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and _keeps(current["headline"]):
            body = [b for b in current["body"] if _keeps(b)][:MAX_BODY_LINES]
            # 표제만 있고 서술이 없는 조각은 표제 자체가 문체를 보여 줘야 한다.
            # "1) 사업장 개요" 같은 목차 잔해는 그러지 못하므로 버린다.
            if _is_exemplar(current["headline"], body):
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}#{len(chunks):03d}",
                        doc_id=doc_id,
                        section=current["section"],
                        role=current["role"],
                        headline=current["headline"],
                        body=body,
                    )
                )
        current = None

    def start(headline: str, role: str) -> None:
        nonlocal current
        flush()
        section = " > ".join(p for p in (chapter, subhead, numhead) if p)
        current = {"headline": headline, "body": [], "section": section, "role": role}

    for raw in lines:
        line = raw.strip()

        m = _CHAPTER.match(line)
        if m:
            flush()
            chapter_no = m.group(1)
            chapter, subhead, numhead = f"{chapter_no}. {redact(m.group(2))}", "", ""
            continue

        if chapter_no not in INDEXED_CHAPTERS:
            continue  # 1·2장(개인정보 표)은 아예 읽지 않는다

        m = _SUBHEAD.match(line)
        if m:
            flush()
            subhead, numhead = f"{m.group(1)}. {redact(m.group(2))}", ""
            continue
        m = _NUMHEAD.match(line)
        if m:
            flush()
            numhead = f"{m.group(1)}) {redact(m.group(2))}"
            continue
        if _CAPTION.match(line):
            continue  # 사진 캡션·조사 주석. 문장이 아니다

        role = _role_for(numhead, subhead, chapter) or _CHAPTER_ROLE[chapter_no]

        if _BULLET.match(line):
            headline = redact(_BULLET.sub("", line, count=1))
            # "[08:25경] 재해자가 …" 는 어느 절에 있든 시각별 경과다. 관찰 서술로 두면
            # photo_analysis 견본 자리에 사건 타임라인이 올라온다.
            start(headline, ROLE_NARRATIVE if _TIMELINE.match(headline) else role)
        elif _SUBBULLET.match(line) or _NOTE.match(line):
            text = redact(_NOTE.sub("", _SUBBULLET.sub("", line, count=1)))
            if current is None:
                # 불릿 없이 서술만 이어지는 문서가 있다. 서술 자체를 표제로 세운다
                if text:
                    start(text, role)
            else:
                current["body"].append(text)
        elif line:
            # 3장(재해발생 경위)은 불릿 없이 한 문단으로 쓰인다. 육하원칙 개조식
            # 한 덩어리라 문체 견본으로는 오히려 값이 크므로 통째로 조각을 연다.
            text = redact(line)
            if current is None:
                if text:
                    start(text, role)
            else:
                current["body"].append(text)

    flush()
    return chunks


def parse_directory(docs_dir: Path) -> list[Chunk]:
    """폴더의 모든 PDF 를 조각으로 만든다. 한 건이 깨져도 나머지는 살린다."""
    files = sorted(docs_dir.glob("*.pdf"))
    if not files:
        log.warning("문체 참조 PDF 가 없다: %s", docs_dir)
        return []

    out: list[Chunk] = []
    for path in files:
        try:
            got = parse_document(path)
        except Exception as exc:  # noqa: BLE001 - 한 건의 파손이 색인 전체를 막지 않는다
            log.warning("PDF 파싱 실패 — 건너뛴다 (%s): %s", path.name, exc)
            continue
        log.info("파싱 %s → 조각 %d개", path.name, len(got))
        out.extend(got)
    return out


def by_role(chunks: Iterable[Chunk]) -> dict[str, list[Chunk]]:
    grouped: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.role, []).append(chunk)
    return grouped


def sentences(chunks: Iterable[Chunk]) -> Iterator[str]:
    """문체 통계용 문장 흐름. 마침표가 없는 표제 줄도 한 문장으로 센다."""
    for chunk in chunks:
        for line in [chunk.headline, *chunk.body]:
            for piece in re.split(r"(?<=[.!?])\s+", line):
                piece = piece.strip()
                if len(piece) >= 8:
                    yield piece
