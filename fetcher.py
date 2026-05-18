#!/usr/bin/env python3
"""
정부지원사업 데이터 수집 모듈
Sources: 기업마당(bizinfo.go.kr) RSS/스크래핑, K-Startup API, data.go.kr API
"""

import hashlib
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_OUTPUT = Path("output")
OPEN_DATA_API_KEY = os.getenv("OPEN_DATA_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

logger = logging.getLogger(__name__)


def retry_request(url, params=None, max_retries=3, timeout=30, **kwargs):
    """지수 백오프(exponential backoff)로 HTTP GET 재시도
    4xx 오류는 재시도하지 않음 (클라이언트 오류로 재시도 무의미)
    """
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            if e.response is not None and 400 <= e.response.status_code < 500:
                raise  # 4xx: 재시도 불필요
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


# ─── 소스 1: 기업마당 RSS ───────────────────────────────────────────────────────

def fetch_bizinfo_rss() -> list:
    """기업마당(bizinfo.go.kr) 지원사업 목록 페이지에서 수집 (rows=100 고용량 요청)"""
    url = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"
    programs = []
    try:
        resp = retry_request(url, params={"cpage": 1, "rows": 100, "schEndAt": "N"}, timeout=30)
        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select("table tbody tr")
        logger.info(f"bizinfo 메인 목록: {len(rows)}행 발견")

        for row in rows:
            cols = row.find_all("td")
            link_tag = row.find("a", href=True)
            if not link_tag or len(cols) < 4:
                continue

            title = link_tag.text.strip()
            href  = link_tag.get("href", "")
            if href.startswith("/"):
                href = "https://www.bizinfo.go.kr" + href

            # 컬럼 구조: [번호, 지원분야, 사업명, 신청기간, 소관부처, 수행기관, 등록일, 조회수]
            category = cols[1].text.strip() if len(cols) > 1 else ""
            period   = cols[3].text.strip() if len(cols) > 3 else ""
            agency   = cols[4].text.strip() if len(cols) > 4 else ""
            region   = cols[5].text.strip() if len(cols) > 5 else "전국"

            # 신청기간에서 시작일·마감일 추출 (예: "2026-05-15 ~ 2026-05-29")
            start_date = ""
            deadline   = ""
            if "~" in period:
                parts      = period.split("~")
                start_date = parts[0].strip()[:10]
                deadline   = parts[-1].strip()[:10]

            programs.append({
                "id":         make_id("bizinfo", href),
                "title":      title,
                "agency":     agency,
                "category":   category,
                "target":     "",
                "amount":     "",
                "start_date": start_date,
                "deadline":   deadline,
                "region":     region or "전국",
                "url":        href,
                "fetched_at": datetime.now().isoformat(),
            })
    except Exception as e:
        logger.warning(f"bizinfo 목록 수집 실패: {e}")
    return programs


# ─── 소스 2: 기업마당 웹 스크래핑 ─────────────────────────────────────────────

def fetch_bizinfo_scrape(max_pages: int = 3) -> list:
    """기업마당 추가 페이지 스크래핑 (2페이지~)"""
    base_url = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do"
    programs = []

    for page_num in range(2, max_pages + 2):  # 2페이지부터 시작 (1페이지는 RSS에서 처리)
        try:
            params = {"cpage": page_num, "rows": 100, "schEndAt": "N"}
            resp = retry_request(base_url, params=params, timeout=30)
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr")

            page_count = 0
            for row in rows:
                cols = row.find_all("td")
                link_tag = row.find("a", href=True)
                if not link_tag or len(cols) < 4:
                    continue

                title = link_tag.text.strip()
                href  = link_tag.get("href", "")
                if href.startswith("/"):
                    href = "https://www.bizinfo.go.kr" + href

                category = cols[1].text.strip() if len(cols) > 1 else ""
                period   = cols[3].text.strip() if len(cols) > 3 else ""
                agency   = cols[4].text.strip() if len(cols) > 4 else ""
                region   = cols[5].text.strip() if len(cols) > 5 else "전국"

                start_date = ""
                deadline   = ""
                if "~" in period:
                    parts      = period.split("~")
                    start_date = parts[0].strip()[:10]
                    deadline   = parts[-1].strip()[:10]

                programs.append({
                    "id": make_id("bizinfo_p", href),
                    "title": title, "agency": agency, "category": category,
                    "target": "", "amount": "", "start_date": start_date,
                    "deadline": deadline, "region": region or "전국", "url": href,
                    "fetched_at": datetime.now().isoformat(),
                })
                page_count += 1

            logger.info(f"bizinfo 페이지 {page_num}: {page_count}건")
            if page_count == 0:
                break
            time.sleep(1)
        except Exception as e:
            logger.warning(f"bizinfo 페이지 {page_num} 실패: {e}")
            break

    return programs


# ─── 소스 3: K-Startup / 공공데이터포털 API ────────────────────────────────────

def _parse_api_items(data: dict | list, source: str) -> list:
    """다양한 data.go.kr 응답 구조에서 항목 추출"""
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        body = data.get("response", data).get("body", data)
        raw = body.get("items", body.get("data", []))
        if isinstance(raw, dict):
            items = raw.get("item", [])
        elif isinstance(raw, list):
            items = raw

    if isinstance(items, dict):
        items = [items]

    programs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = make_id(source, str(
            item.get("pbanc_no") or item.get("id") or item.get("sn") or item.get("pbancNo", "")
        ))
        deadline_raw = (
            item.get("rcpt_end_ymd") or item.get("rcptEndYmd")
            or item.get("deadline") or ""
        )
        programs.append({
            "id": pid,
            "title":    item.get("pbanc_nm") or item.get("pbancNm") or item.get("title") or "",
            "agency":   item.get("supt_inst_nm") or item.get("suptInstNm") or item.get("agency") or "",
            "category": item.get("biz_trgt_cd_nm") or item.get("category") or "",
            "target":   item.get("biz_trgt_desc") or item.get("target") or "",
            "amount":   item.get("supt_amt_desc") or item.get("suptAmtDesc") or item.get("amount") or "",
            "deadline": str(deadline_raw)[:10],
            "region":   item.get("supt_regin_nm") or item.get("region") or "전국",
            "url":      item.get("dtl_pg_url") or item.get("url") or "",
            "fetched_at": datetime.now().isoformat(),
        })
    return programs


def fetch_opendata_api() -> list:
    """data.go.kr 공공데이터 API로 지원사업 수집"""
    if not OPEN_DATA_API_KEY:
        logger.warning("OPEN_DATA_API_KEY 미설정 — API 수집 생략")
        return []

    # data.go.kr 키는 URL-encoded 상태로 .env에 저장됨
    # requests가 자동으로 재인코딩하므로 반드시 디코딩 후 전달
    api_key = unquote(OPEN_DATA_API_KEY)

    # 기업마당 자체 오픈API (bizinfo.go.kr API)
    bizinfo_api_endpoints = [
        "https://www.bizinfo.go.kr/api/pbanc/getBizPbancInfo.do",
    ]
    # data.go.kr 공식 API
    data_go_kr_endpoints = [
        ("https://apis.data.go.kr/B551112/PbanInfoService/selectPbanInfo", "kstartup"),
        ("https://apis.data.go.kr/1051000/bizSuppInfoService/getBizSuppInfo", "mss"),
    ]

    programs = []

    # 기업마당 자체 API 시도
    for url in bizinfo_api_endpoints:
        try:
            params = {"serviceKey": api_key, "pageNo": 1, "numOfRows": 100}
            resp = retry_request(url, params=params, timeout=20)
            if resp.status_code == 200 and resp.content:
                try:
                    data = resp.json()
                    batch = _parse_api_items(data, "bizinfo_api")
                    if batch:
                        logger.info(f"bizinfo 자체 API: {len(batch)}건")
                        programs.extend(batch)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"bizinfo 자체 API 실패: {e}")

    # data.go.kr 공식 API 시도
    for url, source in data_go_kr_endpoints:
        try:
            params = {"serviceKey": api_key, "pageNo": 1, "numOfRows": 100, "type": "json"}
            resp = retry_request(url, params=params, timeout=25)
            try:
                data = resp.json()
                batch = _parse_api_items(data, source)
                logger.info(f"{source} API: {len(batch)}건")
                programs.extend(batch)
            except Exception:
                logger.warning(f"{source} API JSON 파싱 실패 (상태: {resp.status_code})")
        except Exception as e:
            logger.warning(f"{source} API 실패: {e}")

    return programs


# ─── 중복 제거 & 만료 필터 ──────────────────────────────────────────────────────

def dedup(programs: list) -> list:
    seen: dict = {}
    for p in programs:
        pid = p.get("id", "")
        if pid and pid not in seen:
            seen[pid] = p
    return list(seen.values())


def filter_expired(programs: list) -> list:
    today = date.today().isoformat()
    return [p for p in programs if not p.get("deadline") or p["deadline"] >= today]


# ─── 메인 진입점 ────────────────────────────────────────────────────────────────

def fetch_all(context: dict) -> dict:
    """하네스에서 호출하는 메인 수집 함수"""
    logger.info("데이터 수집 시작")
    BASE_OUTPUT.mkdir(exist_ok=True)

    all_programs: list = []

    logger.info("공공데이터 API 수집 중...")
    api_programs = fetch_opendata_api()
    all_programs.extend(api_programs)

    logger.info("기업마당 RSS 수집 중...")
    rss_programs = fetch_bizinfo_rss()
    all_programs.extend(rss_programs)

    # API/RSS 결과가 부족할 때만 스크래핑 (서버 부하 최소화)
    if len(all_programs) < 20:
        logger.info("기업마당 웹 스크래핑 중...")
        scraped = fetch_bizinfo_scrape(max_pages=3)
        all_programs.extend(scraped)

    deduped  = dedup(all_programs)
    filtered = filter_expired(deduped)
    logger.info(
        f"수집 합계: {len(all_programs)}건 → 중복제거: {len(deduped)}건 → 유효: {len(filtered)}건"
    )

    today_str = context.get("date", datetime.now().strftime("%Y%m%d"))
    out_path  = BASE_OUTPUT / f"raw_{today_str}.json"
    out_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"수집 결과 저장: {out_path}")

    context["programs"]  = filtered
    context["raw_count"] = len(filtered)
    return context


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ctx = {"date": datetime.now().strftime("%Y%m%d"), "dry_run": "--test" in sys.argv}
    result = fetch_all(ctx)
    programs = result.get("programs", [])
    print(f"\n수집 결과: {len(programs)}건")
    for p in programs[:5]:
        print(f"  - {p['title'][:50]} | {p['agency']} | 마감: {p['deadline']}")
