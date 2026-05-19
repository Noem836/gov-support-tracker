#!/usr/bin/env python3
"""
금융지원 정보 수집 모듈
Sources: 복지로(생활/의료/주거), 서민금융진흥원(대출), 고용24(실업급여), 긴급복지/재난지원
"""

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_OUTPUT = Path("output")
OPEN_DATA_API_KEY = os.getenv("OPEN_DATA_API_KEY", "")
SMES_API_KEY = os.getenv("SMES_API_KEY", "")
BOKJIRO_API_KEY = os.getenv("BOKJIRO_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 현금·금전 지원 여부 판단 키워드 (이 중 하나라도 있으면 수집 대상)
CASH_KEYWORDS = [
    "지원금", "보조금", "장려금", "수당", "급여", "장학금", "활동비", "창작비",
    "제작비", "사업비", "운영비", "인건비", "연구비", "융자", "대출", "보증",
    "바우처", "현금", "직불금", "보상금", "구조금", "배상", "환급", "지급",
    "창업자금", "R&D", "사업화", "정착금", "이주비", "훈련비", "교육비",
]

BOKJIRO_LIFE_CODES = {
    "003": "청소년",
    "004": "청년",
    "005": "중장년",
}
BOKJIRO_THEME_CODES = {
    "030": "생활지원",
    "040": "주거",
    "050": "일자리",
    "100": "교육",
    "130": "서민금융",
}

logger = logging.getLogger(__name__)


def retry_request(url, params=None, max_retries=3, timeout=30, **kwargs):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            if e.response is not None and 400 <= e.response.status_code < 500:
                raise
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"요청 실패 (시도 {attempt+1}/{max_retries}), {wait}초 후 재시도: {e}")
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"요청 실패 (시도 {attempt+1}/{max_retries}), {wait}초 후 재시도: {e}")
            time.sleep(wait)


def make_id(source: str, raw_key: str) -> str:
    return hashlib.md5(f"{source}:{raw_key}".encode()).hexdigest()[:12]


# ─── 소스 1: 복지로 지자체복지서비스 API ──────────────────────────────────────────

import xml.etree.ElementTree as ET

def _parse_bokjiro_xml(xml_text: str) -> list:
    """복지로 XML 응답 → 사업 목록"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for s in root.findall(".//servList"):
        t = lambda tag: (s.findtext(tag) or "").strip()
        serv_id = t("servId")
        title   = t("servNm")
        if not title:
            continue
        def fmt_date(raw: str) -> str:
            raw = raw.strip()[:8]
            if len(raw) == 8 and raw.isdigit():
                return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
            return ""

        items.append({
            "id":         make_id("bokjiro", serv_id or title),
            "title":      title,
            "agency":     t("bizChrDeptNm"),
            "category":   t("intrsThemaNmArray") or "복지",
            "target":     t("trgterIndvdlNmArray"),
            "amount":     "",  # 복지로 API는 금액 미제공
            "start_date": fmt_date(t("servBgngYmd")),
            "deadline":   fmt_date(t("servEndYmd")),
            "region":     t("ctpvNm") or t("sggNm") or "지자체",
            "url":        t("servDtlLink"),
            "source":     "복지로",
            "fetched_at": datetime.now().isoformat(),
        })
    return items


def fetch_bokjiro_api() -> list:
    """복지로 지자체복지서비스 API (한국사회보장정보원)"""
    if not BOKJIRO_API_KEY:
        logger.warning("BOKJIRO_API_KEY 미설정 — 복지로 API 생략")
        return []

    url = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"
    programs = []

    # 생애주기 × 관심주제 조합으로 수집
    for life_code in BOKJIRO_LIFE_CODES:
        for theme_code in BOKJIRO_THEME_CODES:
            try:
                params = {
                    "serviceKey": BOKJIRO_API_KEY,
                    "pageNo":     "1",
                    "numOfRows":  "10",
                    "lifeArray":  life_code,
                    "intrsThemaArray": theme_code,
                }
                resp = retry_request(url, params=params, timeout=20, max_retries=2)
                programs.extend(_parse_bokjiro_xml(resp.text))
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"복지로 API (생애{life_code}/주제{theme_code}) 실패: {e}")

    logger.info(f"복지로 지자체 API: {len(programs)}건")
    return programs


# ─── 소스 2: 복지로 중앙부처복지서비스 API ──────────────────────────────────────

def fetch_national_welfare_api() -> list:
    """복지로 중앙부처복지서비스 API (한국사회보장정보원)"""
    if not BOKJIRO_API_KEY:
        logger.warning("BOKJIRO_API_KEY 미설정 — 중앙부처복지서비스 API 생략")
        return []

    url = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
    programs = []

    for life_code in BOKJIRO_LIFE_CODES:
        for theme_code in BOKJIRO_THEME_CODES:
            try:
                params = {
                    "serviceKey":      BOKJIRO_API_KEY,
                    "callTp":          "L",
                    "pageNo":          "1",
                    "numOfRows":       "10",
                    "srchKeyCode":     "003",
                    "lifeArray":       life_code,
                    "intrsThemaArray": theme_code,
                }
                resp = retry_request(url, params=params, timeout=20, max_retries=2)
                try:
                    root = ET.fromstring(resp.text)
                except ET.ParseError:
                    continue
                for s in root.findall(".//servList"):
                    t = lambda tag: (s.findtext(tag) or "").strip()
                    serv_id = t("servId")
                    title   = t("servNm")
                    if not title:
                        continue
                    def fmt_date(raw: str) -> str:
                        raw = raw.strip()[:8]
                        if len(raw) == 8 and raw.isdigit():
                            return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
                        return ""

                    programs.append({
                        "id":         make_id("national_welfare", serv_id or title),
                        "title":      title,
                        "agency":     t("jurMnofNm") or t("jurOrgNm"),
                        "category":   t("intrsThemaArray") or "복지",
                        "target":     t("trgterIndvdlArray"),
                        "amount":     "",  # 복지로 API는 금액 미제공
                        "start_date": fmt_date(t("servBgngYmd")),
                        "deadline":   fmt_date(t("servEndYmd")),
                        "region":     "전국",
                        "url":        t("servDtlLink"),
                        "source":     "복지로(중앙부처)",
                        "fetched_at": datetime.now().isoformat(),
                    })
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"중앙부처복지서비스 API (생애{life_code}/주제{theme_code}) 실패: {e}")

    logger.info(f"복지로 중앙부처 API: {len(programs)}건")
    return programs


# ─── 소스 3: 서민금융진흥원 ────────────────────────────────────────────────────

def fetch_kinfa() -> list:
    """서민금융진흥원 — 햇살론, 미소금융, 소액생계비대출 등"""
    fixed_products = [
        {
            "title": "햇살론17",
            "amount": "최대 700만원, 연 17.9% 이하",
            "target": "연소득 3,500만원 이하 또는 신용점수 하위 20% 이하",
            "url": "https://www.kinfa.or.kr/product/sunshine.do",
            "category": "저금리 대출",
        },
        {
            "title": "햇살론 유스(Youth)",
            "amount": "최대 1,200만원, 연 3.5%",
            "target": "만 19~34세 취업준비생·사회초년생",
            "url": "https://www.kinfa.or.kr/product/youth.do",
            "category": "청년 대출",
        },
        {
            "title": "미소금융 창업·운영자금",
            "amount": "최대 7,000만원",
            "target": "금융소외계층, 저소득층 창업자",
            "url": "https://www.kinfa.or.kr/product/micro.do",
            "category": "저금리 대출",
        },
        {
            "title": "소액생계비대출",
            "amount": "최대 100만원, 연 15.9%",
            "target": "연소득 3,500만원 이하, 신용점수 하위 20% 이하",
            "url": "https://www.kinfa.or.kr/product/living.do",
            "category": "생활비 대출",
        },
        {
            "title": "최저신용자 특례보증",
            "amount": "최대 500만원",
            "target": "신용점수 하위 10% 이하 최저신용자",
            "url": "https://www.kinfa.or.kr/product/lowcredit.do",
            "category": "저금리 대출",
        },
    ]

    programs = []

    # 웹 스크래핑 시도 (최신 정보 반영)
    try:
        resp = retry_request("https://www.kinfa.or.kr/product/list.do", timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select(".product-list li") or soup.select(".loan-list li")

        if items:
            for item in items:
                link = item.find("a", href=True)
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if href.startswith("/"):
                    href = "https://www.kinfa.or.kr" + href
                desc = item.find("p")
                programs.append({
                    "id": make_id("kinfa", href),
                    "title": title,
                    "agency": "서민금융진흥원",
                    "category": "저금리 대출",
                    "target": "",
                    "amount": desc.get_text(strip=True)[:100] if desc else "",
                    "deadline": "",
                    "region": "전국",
                    "url": href,
                    "source": "서민금융진흥원",
                    "fetched_at": datetime.now().isoformat(),
                })
            logger.info(f"서민금융진흥원 스크래핑: {len(programs)}건")
            return programs
    except Exception as e:
        logger.warning(f"서민금융진흥원 스크래핑 실패, 고정 상품 사용: {e}")

    for p in fixed_products:
        programs.append({
            "id": make_id("kinfa", p["url"]),
            "title": p["title"],
            "agency": "서민금융진흥원",
            "category": p["category"],
            "target": p["target"],
            "amount": p["amount"],
            "deadline": "",
            "region": "전국",
            "url": p["url"],
            "source": "서민금융진흥원",
            "fetched_at": datetime.now().isoformat(),
        })

    logger.info(f"서민금융진흥원 고정 상품: {len(programs)}건")
    return programs


# ─── 소스 4: 고용24 ───────────────────────────────────────────────────────────

def fetch_work24() -> list:
    """고용24 — 실업급여, 취업성공패키지, 직업훈련 생계비"""
    programs = []

    try:
        resp = retry_request(
            "https://www.work24.go.kr/cm/c/f/1100/selecPolicyList.do",
            params={"systClId": "SC00000028", "currentPageNo": 1, "recordCountPerPage": 20},
            timeout=25,
        )
        soup = BeautifulSoup(resp.text, "lxml")
        items = (
            soup.select(".policy-list li")
            or soup.select(".policy-item")
            or soup.select("ul.list li")
            or soup.select(".support-list .item")
            or soup.select("table tbody tr")
        )
        for item in items:
            link = item.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.work24.go.kr" + href
            programs.append({
                "id": make_id("work24", href),
                "title": title,
                "agency": "고용노동부",
                "category": "고용지원",
                "target": "",
                "amount": "",
                "deadline": "",
                "region": "전국",
                "url": href,
                "source": "고용24",
                "fetched_at": datetime.now().isoformat(),
            })
        logger.info(f"고용24 스크래핑: {len(programs)}건")
    except Exception as e:
        logger.warning(f"고용24 스크래핑 실패: {e}")

    # 핵심 항목 고정 추가 (스크래핑 결과와 중복 없을 때만)
    fixed = [
        {
            "title": "실업급여 (구직급여)",
            "amount": "퇴직 전 평균임금의 60%, 최대 9개월",
            "target": "비자발적 이직자 (고용보험 가입 180일 이상)",
            "url": "https://www.work24.go.kr/cm/c/b101/selectCmCb101List.do",
            "category": "실업급여",
        },
        {
            "title": "취업성공패키지",
            "amount": "최대 195만원 취업장려수당",
            "target": "취업취약계층·저소득 구직자",
            "url": "https://www.work24.go.kr/cm/c/a103/selectCmCa103View.do",
            "category": "취업지원",
        },
        {
            "title": "직업훈련 생계비 대출",
            "amount": "월 최대 116만원 (훈련기간 동안)",
            "target": "직업능력개발훈련 참여 실업자",
            "url": "https://www.work24.go.kr",
            "category": "훈련생계비",
        },
        {
            "title": "국민내일배움카드",
            "amount": "최대 500만원 훈련비 지원",
            "target": "실업자·재직자·자영업자",
            "url": "https://www.work24.go.kr/cm/c/a105/selectCmCa105List.do",
            "category": "훈련지원",
        },
    ]

    existing_titles = {p["title"] for p in programs}
    for item in fixed:
        if item["title"] not in existing_titles:
            programs.append({
                "id": make_id("work24_fixed", item["url"] + item["title"]),
                "title": item["title"],
                "agency": "고용노동부",
                "category": item["category"],
                "target": item["target"],
                "amount": item["amount"],
                "deadline": "",
                "region": "전국",
                "url": item["url"],
                "source": "고용24",
                "fetched_at": datetime.now().isoformat(),
            })

    return programs


# ─── 소스 5: 중소벤처24 API ──────────────────────────────────────────────────────

def fetch_smes() -> list:
    """중소벤처24 — fetch_bizinfo_financial()과 동일 소스, API 인증 필요로 비활성"""
    return []


# ─── 소스 6: 긴급복지 / 재난·피해 지원 ──────────────────────────────────────────

def fetch_emergency_support() -> list:
    """긴급복지지원, 재난피해보상, 범죄피해구조금 등"""
    fixed = [
        {
            "title": "긴급복지지원 — 생계지원",
            "agency": "보건복지부",
            "amount": "4인가구 기준 월 최대 162만원 (최대 6개월)",
            "target": "갑작스러운 위기상황(실직·질병·가족해체 등)에 처한 저소득 가구",
            "url": "https://www.mohw.go.kr/react/policy/index.jsp?PAR_MENU_ID=06&MENU_ID=06320101",
            "category": "긴급생계지원",
        },
        {
            "title": "긴급복지지원 — 의료지원",
            "agency": "보건복지부",
            "amount": "최대 300만원 의료비",
            "target": "갑작스러운 질병·부상으로 위기에 처한 가구",
            "url": "https://www.mohw.go.kr/react/policy/index.jsp?PAR_MENU_ID=06&MENU_ID=06320101",
            "category": "긴급의료지원",
        },
        {
            "title": "긴급복지지원 — 주거지원",
            "agency": "보건복지부",
            "amount": "임시 거소 제공 또는 월 최대 64만원",
            "target": "주거를 잃은 위기가구",
            "url": "https://www.mohw.go.kr/react/policy/index.jsp?PAR_MENU_ID=06&MENU_ID=06320101",
            "category": "긴급주거지원",
        },
        {
            "title": "재난적 의료비 지원",
            "agency": "국민건강보험공단",
            "amount": "본인부담 의료비의 50~80%, 최대 3,000만원",
            "target": "과도한 의료비 발생으로 경제적 어려움을 겪는 가구",
            "url": "https://www.nhis.or.kr/nhis/policy/wbhada23400m01.do",
            "category": "의료비지원",
        },
        {
            "title": "주거급여 (임차급여)",
            "agency": "국토교통부",
            "amount": "지역·가구원수별 월 최대 52만원",
            "target": "기준 중위소득 48% 이하 가구",
            "url": "https://www.lh.or.kr/contents/cont.do?sKey=2282",
            "category": "주거지원",
        },
        {
            "title": "에너지 바우처",
            "agency": "산업통상자원부",
            "amount": "연 최대 59만 2천원 (냉·난방비)",
            "target": "기초생활수급자 중 노인·장애인·영유아 포함 가구",
            "url": "https://www.energyvoucher.or.kr/",
            "category": "생활비지원",
        },
        {
            "title": "자연재해 피해주민 지원",
            "agency": "행정안전부",
            "amount": "주택복구비·생계지원금 (피해규모에 따라 상이)",
            "target": "태풍·홍수·지진 등 자연재해 피해 주민",
            "url": "https://www.mois.go.kr/frt/sub/a06/b08/disasterRecovery_screen.do",
            "category": "재난피해지원",
        },
        {
            "title": "범죄피해자 구조금",
            "agency": "법무부 범죄피해자지원센터",
            "amount": "사망 시 최대 1억원, 중상해 시 최대 4,500만원",
            "target": "범죄로 인한 사망·중상해 피해자 및 유족",
            "url": "https://www.kvcrc.or.kr/",
            "category": "범죄피해보상",
        },
        {
            "title": "의사상자 보상",
            "agency": "보건복지부",
            "amount": "의사자 유족 최대 2억 3천만원 / 의상자 최대 1억 1,500만원",
            "target": "타인을 구하다 부상·사망한 의사상자 및 유족",
            "url": "https://www.mohw.go.kr/react/policy/index.jsp?PAR_MENU_ID=06&MENU_ID=06340101",
            "category": "피해보상",
        },
        {
            "title": "기초생활보장 — 생계급여",
            "agency": "보건복지부",
            "amount": "4인가구 기준 월 최대 183만원",
            "target": "기준 중위소득 32% 이하 가구",
            "url": "https://www.bokjiro.go.kr/ssis-tbu/twataa/welfareInfo/moveTWAT52011M.do",
            "category": "생활비지원",
        },
        {
            "title": "의료급여",
            "agency": "보건복지부",
            "amount": "의료비 본인부담 1~2종 차등 지원 (최대 전액 지원)",
            "target": "기준 중위소득 40% 이하 가구",
            "url": "https://www.mohw.go.kr/react/policy/index.jsp?PAR_MENU_ID=06&MENU_ID=06320301",
            "category": "의료비지원",
        },
    ]

    programs = []
    for item in fixed:
        programs.append({
            "id": make_id("emergency", item["url"] + item["title"]),
            "title": item["title"],
            "agency": item["agency"],
            "category": item["category"],
            "target": item["target"],
            "amount": item["amount"],
            "deadline": "",
            "region": "전국",
            "url": item["url"],
            "source": "정부지원",
            "fetched_at": datetime.now().isoformat(),
        })

    logger.info(f"긴급복지/재난·피해 지원: {len(programs)}건")
    return programs


# ─── 현금지원 필터 헬퍼 ──────────────────────────────────────────────────────────

def is_cash_support(title: str, category: str = "", amount: str = "", target: str = "") -> bool:
    text = f"{title} {category} {amount} {target}"
    return any(kw in text for kw in CASH_KEYWORDS)


# ─── 소스 7: 기업마당 금융/보조금 공고 ───────────────────────────────────────────

def fetch_bizinfo_financial() -> list:
    """기업마당 — 금융·창업 카테고리 공고 (현금지원 필터 적용)"""
    base_url = "https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/list.do"
    # pBizSe: 01=금융, 06=창업, 07=R&D (현금 지원 포함 카테고리만)
    categories = [("01", "금융"), ("06", "창업"), ("07", "R&D")]
    programs = []

    for code, cat_name in categories:
        try:
            params = {"pBizSe": code, "pageUnit": 100, "pageIndex": 1}
            resp = retry_request(base_url, params=params, timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr") or soup.select("ul.list-type li")

            count = 0
            for row in rows:
                link = row.find("a", href=True)
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if href.startswith("/"):
                    href = "https://www.bizinfo.go.kr" + href

                cols = row.find_all("td")
                agency     = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                period     = cols[3].get_text(strip=True) if len(cols) > 3 else ""
                start_date = ""
                deadline   = ""
                if "~" in period:
                    parts = period.split("~")
                    start_date = parts[0].strip()[:10].replace(".", "-")
                    deadline   = parts[1].strip()[:10].replace(".", "-")

                # 현금지원 키워드 필터
                if not is_cash_support(title, cat_name):
                    continue

                programs.append({
                    "id":         make_id("bizinfo_fin", href),
                    "title":      title,
                    "agency":     agency,
                    "category":   cat_name,
                    "target":     "",
                    "amount":     "",
                    "start_date": start_date,
                    "deadline":   deadline,
                    "region":     "전국",
                    "url":        href,
                    "source":     "기업마당",
                    "fetched_at": datetime.now().isoformat(),
                })
                count += 1

            logger.info(f"기업마당 {cat_name}: {count}건")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"기업마당 {cat_name} 실패: {e}")

    return programs


# ─── 소스 8: 한국문화예술위원회 ───────────────────────────────────────────────────

def fetch_arko() -> list:
    """한국문화예술위원회 — 예술활동지원금, 창작지원금 (현금지원만)"""
    programs = []

    # 공모·지원사업 목록 스크래핑
    try:
        resp = retry_request("https://thearts.arko.or.kr/thearts/news/contest", timeout=25)
        soup = BeautifulSoup(resp.text, "lxml")
        items = (
            soup.select("ul.list-wrap li")
            or soup.select(".contest-list li")
            or soup.select(".board-list li")
            or soup.select("ul.list li")
            or soup.select("table tbody tr")
        )
        count = 0
        for item in items:
            link = item.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not href.startswith("http"):
                href = "https://thearts.arko.or.kr" + href

            period_tag = item.find(class_=lambda x: x and any(k in str(x) for k in ["period", "date", "term"]))
            deadline = ""
            if period_tag:
                text = period_tag.get_text(strip=True)
                parts = text.split("~")
                if len(parts) == 2:
                    deadline = parts[-1].strip()[:10].replace(".", "-")

            programs.append({
                "id": make_id("arko", href),
                "title": title,
                "agency": "한국문화예술위원회",
                "category": "예술활동지원",
                "target": "예술인·예술단체",
                "amount": "",
                "deadline": deadline,
                "region": "전국",
                "url": href,
                "source": "예술위원회",
                "fetched_at": datetime.now().isoformat(),
            })
            count += 1
        logger.info(f"예술위원회 스크래핑: {count}건")
    except Exception as e:
        logger.warning(f"예술위원회 스크래핑 실패: {e}")

    # 대표 지원사업 고정 항목 (상시)
    fixed = [
        {
            "title": "예술인 활동준비금 지원",
            "amount": "1인당 최대 300만원",
            "target": "예술활동증명 완료 예술인",
            "url": "https://www.arko.or.kr/business/artSupport/list.do",
            "category": "예술활동지원",
        },
        {
            "title": "창작준비금 지원 (예술인복지재단)",
            "amount": "1인당 최대 300만원",
            "target": "예술활동증명 완료 예술인 (소득기준 충족)",
            "url": "https://www.kawf.or.kr/",
            "category": "예술활동지원",
        },
        {
            "title": "예술인 창작지원금 (문화예술진흥기금)",
            "amount": "프로젝트별 수백만~수천만원",
            "target": "개인 예술인 및 예술단체",
            "url": "https://www.arko.or.kr/business/artSupport/list.do",
            "category": "창작지원금",
        },
    ]
    existing = {p["title"] for p in programs}
    for item in fixed:
        if item["title"] not in existing:
            programs.append({
                "id": make_id("arko_fixed", item["url"] + item["title"]),
                "title": item["title"],
                "agency": "한국문화예술위원회 / 예술인복지재단",
                "category": item["category"],
                "target": item["target"],
                "amount": item["amount"],
                "deadline": "",
                "region": "전국",
                "url": item["url"],
                "source": "예술위원회",
                "fetched_at": datetime.now().isoformat(),
            })

    logger.info(f"예술위원회 최종: {len(programs)}건")
    return programs


# ─── 소스 9: 청년정책포털 ────────────────────────────────────────────────────────

def fetch_youth_portal() -> list:
    """청년정책포털 — 청년 현금지원 (월세·저축·도약계좌·장려금 등)"""
    programs = []

    try:
        resp = retry_request(
            "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch",
            timeout=25,
        )
        soup = BeautifulSoup(resp.text, "lxml")
        items = (
            soup.select(".policy-list li")
            or soup.select(".policy-item")
            or soup.select("ul.list li")
            or soup.select("table tbody tr")
        )
        count = 0
        for item in items:
            link = item.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.youthcenter.go.kr" + href

            desc = item.find("p") or item.find(class_=lambda x: x and "desc" in str(x))
            amount = desc.get_text(strip=True)[:100] if desc else ""

            if not is_cash_support(title, "청년지원", amount):
                continue

            programs.append({
                "id": make_id("youth", href),
                "title": title,
                "agency": "청년정책조정위원회",
                "category": "청년지원",
                "target": "만 19~34세 청년",
                "amount": amount,
                "deadline": "",
                "region": "전국",
                "url": href,
                "source": "청년포털",
                "fetched_at": datetime.now().isoformat(),
            })
            count += 1
        logger.info(f"청년포털 스크래핑: {count}건")
    except Exception as e:
        logger.warning(f"청년포털 스크래핑 실패: {e}")

    # 대표 청년 현금지원 고정 항목
    fixed = [
        {
            "title": "청년 월세 한시 특별지원",
            "amount": "월 최대 20만원, 최대 12개월 (총 240만원)",
            "target": "만 19~34세, 독립거주 청년 (부모와 별거, 소득기준 충족)",
            "url": "https://www.youthcenter.go.kr/youthPolicy/ythPlcyInfoMain",
            "category": "주거비지원",
        },
        {
            "title": "청년 내일저축계좌",
            "amount": "월 10만원 저축 시 정부 30만원 적립 (3년 만기 최대 1,440만원)",
            "target": "일하는 청년 (중위소득 100% 이하, 만 19~34세)",
            "url": "https://www.bokjiro.go.kr",
            "category": "자산형성지원",
        },
        {
            "title": "청년도약계좌",
            "amount": "월 40~70만원 납입 시 정부 기여금 최대 월 2.4만원 + 비과세 이자",
            "target": "만 19~34세, 개인소득 6,000만원 이하",
            "url": "https://www.youthcenter.go.kr/youthPolicy/ythPlcyInfoMain",
            "category": "자산형성지원",
        },
        {
            "title": "근로장려금 (청년)",
            "amount": "단독가구 최대 165만원 / 홑벌이 285만원 / 맞벌이 330만원",
            "target": "소득·재산 기준 충족 근로자·사업자",
            "url": "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2325&cntntsId=7726",
            "category": "장려금",
        },
        {
            "title": "청년창업지원금 (창업진흥원)",
            "amount": "최대 1억원 (사업화 자금)",
            "target": "만 39세 이하 예비창업자·초기창업자",
            "url": "https://www.k-startup.go.kr",
            "category": "창업지원금",
        },
    ]
    existing = {p["title"] for p in programs}
    for item in fixed:
        if item["title"] not in existing:
            programs.append({
                "id": make_id("youth_fixed", item["url"] + item["title"]),
                "title": item["title"],
                "agency": "정부",
                "category": item["category"],
                "target": item["target"],
                "amount": item["amount"],
                "deadline": "",
                "region": "전국",
                "url": item["url"],
                "source": "청년포털",
                "fetched_at": datetime.now().isoformat(),
            })

    logger.info(f"청년포털 최종: {len(programs)}건")
    return programs


# ─── 소스 10: 추가 현금지원 기관 ─────────────────────────────────────────────────

def fetch_additional_cash_support() -> list:
    """K-Startup, 예술인복지재단, 콘텐츠진흥원, 국민체육진흥공단 등 현금지원 고정 항목"""
    fixed = [
        # K-Startup 창업지원
        {
            "title": "예비창업패키지 (사업화 자금)",
            "agency": "창업진흥원",
            "amount": "최대 1억원",
            "target": "예비창업자 (만 39세 이하 우대)",
            "url": "https://www.k-startup.go.kr",
            "category": "창업지원금",
            "source": "K-Startup",
        },
        {
            "title": "초기창업패키지",
            "agency": "창업진흥원",
            "amount": "최대 1억원 (평균 5천만원)",
            "target": "창업 3년 이내 기업",
            "url": "https://www.k-startup.go.kr",
            "category": "창업지원금",
            "source": "K-Startup",
        },
        # 콘텐츠진흥원
        {
            "title": "콘텐츠 창업도약 패키지",
            "agency": "한국콘텐츠진흥원",
            "amount": "최대 1억원 (사업화 자금)",
            "target": "웹툰·음악·영상·게임 등 콘텐츠 창업자",
            "url": "https://www.kocca.kr/",
            "category": "창업지원금",
            "source": "콘텐츠진흥원",
        },
        {
            "title": "독립예술영화 제작지원",
            "agency": "영화진흥위원회",
            "amount": "편당 최대 5천만원",
            "target": "독립영화 제작자·감독",
            "url": "https://www.kofic.or.kr/",
            "category": "창작지원금",
            "source": "영화진흥위원회",
        },
        # 예술인복지재단
        {
            "title": "예술인 파견지원 (예술로)",
            "agency": "한국예술인복지재단",
            "amount": "활동비 월 최대 180만원",
            "target": "예술활동증명 완료 예술인",
            "url": "https://www.kawf.or.kr/",
            "category": "예술활동지원",
            "source": "예술인복지재단",
        },
        {
            "title": "예술인 고용보험",
            "agency": "한국예술인복지재단",
            "amount": "실업급여 상당 (이직 전 평균보수의 60%)",
            "target": "문화예술용역 계약 체결 예술인",
            "url": "https://www.kawf.or.kr/",
            "category": "고용보험",
            "source": "예술인복지재단",
        },
        # 국민체육진흥공단
        {
            "title": "체육인 복지지원금",
            "agency": "국민체육진흥공단",
            "amount": "생활체육 지도자 활동비 등 (사업별 상이)",
            "target": "체육인·체육지도자",
            "url": "https://www.kspo.or.kr/",
            "category": "체육지원금",
            "source": "국민체육진흥공단",
        },
        # 중소기업기술정보진흥원
        {
            "title": "중소기업 기술개발 지원 (R&D 자금)",
            "agency": "중소기업기술정보진흥원",
            "amount": "과제당 최대 수억원 (매칭 방식)",
            "target": "중소기업·스타트업",
            "url": "https://www.tipa.or.kr/",
            "category": "R&D지원금",
            "source": "기술정보진흥원",
        },
        # 문화체육관광부
        {
            "title": "지역문화 예술지원사업",
            "agency": "문화체육관광부",
            "amount": "사업별 수백만~수천만원",
            "target": "지역 문화예술단체·기획자",
            "url": "https://www.mcst.go.kr/",
            "category": "문화예술지원",
            "source": "문화체육관광부",
        },
    ]

    programs = []
    for item in fixed:
        programs.append({
            "id": make_id(item["source"], item["url"] + item["title"]),
            "title": item["title"],
            "agency": item["agency"],
            "category": item["category"],
            "target": item["target"],
            "amount": item["amount"],
            "deadline": "",
            "region": "전국",
            "url": item["url"],
            "source": item["source"],
            "fetched_at": datetime.now().isoformat(),
        })

    logger.info(f"추가 현금지원 기관: {len(programs)}건")
    return programs


# ─── 중복 제거 ──────────────────────────────────────────────────────────────────

def dedup(programs: list) -> list:
    seen: dict = {}
    for p in programs:
        pid = p.get("id", "")
        if pid and pid not in seen:
            seen[pid] = p
    return list(seen.values())


# ─── 메인 진입점 ────────────────────────────────────────────────────────────────

def fetch_all(context: dict) -> dict:
    logger.info("금융지원 데이터 수집 시작")
    BASE_OUTPUT.mkdir(exist_ok=True)

    all_programs: list = []

    logger.info("복지로 지자체복지서비스 API 수집 중...")
    all_programs.extend(fetch_bokjiro_api())

    logger.info("복지로 중앙부처복지서비스 API 수집 중...")
    all_programs.extend(fetch_national_welfare_api())

    logger.info("중소벤처24 API 수집 중...")
    all_programs.extend(fetch_smes())

    logger.info("서민금융진흥원 수집 중...")
    all_programs.extend(fetch_kinfa())

    logger.info("고용24 수집 중...")
    all_programs.extend(fetch_work24())

    logger.info("긴급복지/재난·피해 지원 수집 중...")
    all_programs.extend(fetch_emergency_support())

    logger.info("기업마당 금융/보조금 공고 수집 중...")
    all_programs.extend(fetch_bizinfo_financial())

    logger.info("한국문화예술위원회 수집 중...")
    all_programs.extend(fetch_arko())

    logger.info("청년정책포털 수집 중...")
    all_programs.extend(fetch_youth_portal())

    logger.info("추가 현금지원 기관 수집 중...")
    all_programs.extend(fetch_additional_cash_support())

    deduped = dedup(all_programs)
    logger.info(f"수집 합계: {len(all_programs)}건 → 중복제거: {len(deduped)}건")

    today_str = context.get("date", datetime.now().strftime("%Y%m%d"))
    out_path = BASE_OUTPUT / f"raw_{today_str}.json"
    out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"수집 결과 저장: {out_path}")

    context["programs"] = deduped
    context["raw_count"] = len(deduped)
    return context


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ctx = {"date": datetime.now().strftime("%Y%m%d"), "dry_run": "--test" in sys.argv}
    result = fetch_all(ctx)
    programs = result.get("programs", [])
    print(f"\n수집 결과: {len(programs)}건")
    for p in programs[:5]:
        print(f"  - {p['title'][:50]} | {p['agency']} | {p['category']}")
