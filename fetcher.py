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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

BOKJIRO_CATEGORIES = {
    "001": "생활안정",
    "002": "주거",
    "004": "의료",
    "008": "고용/창업",
    "009": "보호/돌봄",
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


# ─── 소스 1: 복지로 API ────────────────────────────────────────────────────────

def fetch_bokjiro_api() -> list:
    """복지로 API — 생활안정/의료/주거/고용 복지서비스 수집"""
    if not OPEN_DATA_API_KEY:
        logger.warning("OPEN_DATA_API_KEY 미설정 — 복지로 API 생략")
        return []

    api_key = unquote(OPEN_DATA_API_KEY)
    url = "https://apis.data.go.kr/B554287/NationalWelfareInformationsService/getNationalWelfareList"
    programs = []

    for code, category_name in BOKJIRO_CATEGORIES.items():
        try:
            params = {
                "serviceKey": api_key,
                "pageNo": "1",
                "numOfRows": "100",
                "srchKeyCode": code,
            }
            resp = retry_request(url, params=params, timeout=30)

            try:
                data = resp.json()
            except Exception:
                logger.warning(f"복지로 {category_name} JSON 파싱 실패, 스킵")
                continue

            # 복지로 API 응답 구조 파싱
            items = []
            if isinstance(data, dict):
                body = data.get("wantedServiceList", {})
                if not body:
                    body = data.get("response", {}).get("body", {})
                raw = body.get("wantedServiceInfo", body.get("items", []))
                if isinstance(raw, dict):
                    items = raw.get("wantedService", raw.get("item", []))
                elif isinstance(raw, list):
                    items = raw

            if isinstance(items, dict):
                items = [items]

            count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                serv_id = item.get("servId", item.get("servCD", ""))
                title = item.get("servNm", item.get("servName", ""))
                if not title:
                    continue
                programs.append({
                    "id": make_id("bokjiro", serv_id or title),
                    "title": title,
                    "agency": item.get("jurMnofNm", ""),
                    "category": category_name,
                    "target": item.get("tgtrDsc", item.get("tgtrDesc", "")),
                    "amount": item.get("alwnInfo", item.get("alwInfo", "")),
                    "deadline": "",
                    "region": item.get("sido", "전국"),
                    "url": item.get("wlfareInfoDtlLink", item.get("servDtlLink", "")),
                    "source": "복지로",
                    "fetched_at": datetime.now().isoformat(),
                })
                count += 1

            logger.info(f"복지로 API {category_name}: {count}건")
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"복지로 API {category_name} 실패: {e}")

    return programs


# ─── 소스 2: 복지로 웹 스크래핑 (API 폴백) ─────────────────────────────────────

def fetch_bokjiro_scrape() -> list:
    """복지로 웹 스크래핑 — API 실패 시 폴백"""
    base_url = "https://www.bokjiro.go.kr/ssis-tbu/twataa/welfareInfo/moveTWAT52011M.do"
    programs = []

    for code, category_name in list(BOKJIRO_CATEGORIES.items())[:3]:  # 상위 3개 분야만
        try:
            params = {"srchKeyCode": code, "pageIndex": "1"}
            resp = retry_request(base_url, params=params, timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")

            items = (
                soup.select("ul.welfare-list li")
                or soup.select(".search-result-list li")
                or soup.select("li.item")
            )

            count = 0
            for item in items:
                link = item.find("a", href=True)
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = link.get("href", "")
                if href.startswith("/"):
                    href = "https://www.bokjiro.go.kr" + href

                agency_tag = item.find(class_=lambda x: x and any(k in x for k in ["dept", "agency", "org"]))
                agency = agency_tag.get_text(strip=True) if agency_tag else ""

                desc_tag = item.find("p") or item.find(class_=lambda x: x and "desc" in str(x))
                amount = desc_tag.get_text(strip=True)[:100] if desc_tag else ""

                programs.append({
                    "id": make_id("bokjiro_web", href),
                    "title": title,
                    "agency": agency,
                    "category": category_name,
                    "target": "",
                    "amount": amount,
                    "deadline": "",
                    "region": "전국",
                    "url": href,
                    "source": "복지로",
                    "fetched_at": datetime.now().isoformat(),
                })
                count += 1

            logger.info(f"복지로 스크래핑 {category_name}: {count}건")
            time.sleep(1)

        except Exception as e:
            logger.warning(f"복지로 스크래핑 {category_name} 실패: {e}")

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
        resp = retry_request("https://www.work24.go.kr/cm/c/a103/selectCmCa103List.do", timeout=25)
        soup = BeautifulSoup(resp.text, "lxml")
        items = (
            soup.select("ul.policy-list li")
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
                "id": make_id("work24_fixed", item["url"]),
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
    """중소벤처24 — 금융지원 분야 사업 공고 수집"""
    if not SMES_API_KEY:
        logger.warning("SMES_API_KEY 미설정 — 중소벤처24 API 생략")
        return []

    programs = []

    # 중소벤처24 공고 API (금융 분야 필터)
    endpoints = [
        "https://www.smes.go.kr/openapi/api/pbanc/getPbancInfo.do",
        "https://apis.data.go.kr/1051000/bizSuppInfoService/getBizSuppInfo",
    ]

    financial_keywords = ["금융", "융자", "대출", "보증", "보조금", "지원금", "바우처", "수당", "보상"]

    for url in endpoints:
        try:
            params = {
                "serviceKey": SMES_API_KEY,
                "pageNo": 1,
                "numOfRows": 100,
                "type": "json",
            }
            resp = retry_request(url, params=params, timeout=25)

            try:
                data = resp.json()
            except Exception:
                logger.warning(f"중소벤처24 JSON 파싱 실패: {url}")
                continue

            # 응답 구조 파싱
            items = []
            if isinstance(data, dict):
                body = data.get("response", data).get("body", data)
                raw = body.get("items", body.get("data", []))
                if isinstance(raw, dict):
                    items = raw.get("item", [])
                elif isinstance(raw, list):
                    items = raw
            if isinstance(items, dict):
                items = [items]

            count = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("pbanc_nm") or item.get("pbancNm") or item.get("title") or ""
                category = item.get("biz_trgt_cd_nm") or item.get("category") or ""

                # 금융 관련 항목만 필터링
                text = f"{title} {category}".replace(" ", "")
                if not any(kw in text for kw in financial_keywords):
                    continue

                deadline_raw = item.get("rcpt_end_ymd") or item.get("rcptEndYmd") or ""
                programs.append({
                    "id": make_id("smes", str(item.get("pbanc_no") or item.get("pbancNo") or title)),
                    "title": title,
                    "agency": item.get("supt_inst_nm") or item.get("suptInstNm") or "중소벤처기업부",
                    "category": category or "금융지원",
                    "target": item.get("biz_trgt_desc") or "",
                    "amount": item.get("supt_amt_desc") or item.get("suptAmtDesc") or "",
                    "deadline": str(deadline_raw)[:10],
                    "region": item.get("supt_regin_nm") or "전국",
                    "url": item.get("dtl_pg_url") or "",
                    "source": "중소벤처24",
                    "fetched_at": datetime.now().isoformat(),
                })
                count += 1

            logger.info(f"중소벤처24 ({url.split('/')[-1]}): {count}건 (금융 필터)")
            break  # 첫 번째 성공한 엔드포인트에서 중단

        except Exception as e:
            logger.warning(f"중소벤처24 API 실패 ({url.split('/')[-1]}): {e}")

    return programs


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

    logger.info("복지로 API 수집 중...")
    bokjiro_api = fetch_bokjiro_api()
    all_programs.extend(bokjiro_api)

    if len(bokjiro_api) < 10:
        logger.info("복지로 웹 스크래핑 중 (API 보완)...")
        all_programs.extend(fetch_bokjiro_scrape())

    logger.info("중소벤처24 API 수집 중...")
    all_programs.extend(fetch_smes())

    logger.info("서민금융진흥원 수집 중...")
    all_programs.extend(fetch_kinfa())

    logger.info("고용24 수집 중...")
    all_programs.extend(fetch_work24())

    logger.info("긴급복지/재난·피해 지원 수집 중...")
    all_programs.extend(fetch_emergency_support())

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
