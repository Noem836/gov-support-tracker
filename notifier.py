#!/usr/bin/env python3
"""
알림 발송 모듈 — Gmail SMTP, Slack Webhook, Notion API 지원
"""

import json
import logging
import os
import smtplib
import sys
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GMAIL_USER        = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID", "")


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

def days_until(deadline_str: str) -> int | None:
    try:
        return (date.fromisoformat(deadline_str[:10]) - date.today()).days
    except Exception:
        return None


def deadline_label(deadline_str: str, ongoing: bool = False) -> str:
    if not deadline_str:
        return "상시 모집" if ongoing else "미정"
    days = days_until(deadline_str)
    if days is None:
        return deadline_str
    if days < 0:
        return f"{deadline_str} (마감)"
    if days == 0:
        return f"{deadline_str} (D-Day)"
    return f"{deadline_str} (D-{days}일)"


def deadline_color(deadline_str: str) -> str:
    days = days_until(deadline_str) if deadline_str else None
    if days is not None and days <= 7:
        return "#e74c3c"
    return "#2c3e50"


def score_color(score: int) -> str:
    if score >= 80:
        return "#27ae60"
    if score >= 60:
        return "#e67e22"
    return "#95a5a6"


# ─── 이메일 본문 생성 ─────────────────────────────────────────────────────────

def _program_card_html(p: dict, rank: int) -> str:
    score = p.get("score", 0)
    dl    = p.get("deadline", "")
    return f"""
<div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
            padding:16px;margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;
              margin-bottom:8px;">
    <h3 style="margin:0;font-size:15px;color:#2c3e50;flex:1;">
      {rank}. {p.get('title','(제목 없음)')}
    </h3>
    <span style="background:{score_color(score)};color:#fff;padding:2px 8px;
                 border-radius:12px;font-size:12px;font-weight:bold;
                 white-space:nowrap;margin-left:8px;">
      {score}점
    </span>
  </div>
  <p style="margin:4px 0;color:#7f8c8d;font-size:13px;">
    🏢 {p.get('agency','미정')} &nbsp;|&nbsp; 🌏 {p.get('region','전국')}
  </p>
  <p style="margin:4px 0;font-size:13px;">
    💰 {p.get('amount','확인 필요')}
  </p>
  <p style="margin:4px 0;font-size:13px;color:{deadline_color(dl)};">
    📅 {deadline_label(dl)}
  </p>
  <p style="margin:8px 0;font-size:13px;color:#555;">⭐ {p.get('reason','')}</p>
  <p style="margin:4px 0;font-size:13px;color:#8e44ad;">💡 {p.get('highlight','')}</p>
  <p style="margin:4px 0;font-size:13px;color:#e74c3c;font-weight:bold;">
    🚀 {p.get('action','')}
  </p>
  <a href="{p.get('url','#')}"
     style="display:inline-block;margin-top:8px;background:#3498db;color:#fff;
            padding:6px 14px;border-radius:4px;text-decoration:none;font-size:13px;">
    🔗 신청하기
  </a>
</div>"""


def build_email_html(programs: list, profile: dict, total_fetched: int) -> str:
    today_str   = date.today().strftime("%Y.%m.%d")
    name        = profile.get("basic", {}).get("name", "")
    top_list    = [p for p in programs if p.get("score", 0) >= 80]
    other_list  = [p for p in programs if p.get("score", 0) < 80]

    top_html = "".join(_program_card_html(p, i + 1) for i, p in enumerate(top_list))
    if not top_html:
        top_html = '<p style="color:#999;">이번 주 80점 이상 사업이 없습니다.</p>'

    other_html = ""
    if other_list:
        items_html = "".join(
            f'<li style="margin-bottom:6px;">'
            f'<a href="{p.get("url","#")}">{p.get("title","")}</a>'
            f' &nbsp;—&nbsp; {p.get("agency","")} | {deadline_label(p.get("deadline",""))}'
            f' | {p.get("score",0)}점</li>'
            for p in other_list[:7]
        )
        other_html = f"""
<h2 style="color:#2c3e50;border-left:4px solid #95a5a6;
           padding-left:10px;margin-top:24px;">📋 기타 추천 사업</h2>
<ul style="padding-left:20px;">{items_html}</ul>"""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>주간 정부지원 알림 {today_str}</title>
</head>
<body style="font-family:'Apple SD Gothic Neo',Arial,sans-serif;
             max-width:680px;margin:0 auto;background:#f5f6fa;padding:20px;">
<div style="background:#fff;border-radius:12px;padding:24px;
            box-shadow:0 2px 8px rgba(0,0,0,0.08);">

  <div style="text-align:center;
              background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
              padding:20px;border-radius:8px;margin-bottom:20px;">
    <h1 style="color:#fff;margin:0;font-size:20px;">🏛️ 주간 정부지원사업 알림</h1>
    <p style="color:rgba(255,255,255,0.9);margin:4px 0 0;">{today_str}</p>
  </div>

  <p style="color:#555;">
    안녕하세요! 이번 주 <strong>{name}</strong>님을 위한
    추천 정부지원사업 <strong>{len(programs)}건</strong>입니다.
  </p>

  <h2 style="color:#e74c3c;border-left:4px solid #e74c3c;padding-left:10px;">
    🏆 TOP 추천 (80점 이상)
  </h2>
  {top_html}

  {other_html}

  <div style="background:#ecf0f1;padding:12px;border-radius:6px;
              margin-top:20px;font-size:12px;color:#7f8c8d;">
    📊 총 <strong>{total_fetched}건</strong> 검토 →
    <strong>{len(programs)}건</strong> 추천<br>
    🤖 Claude AI 자동 분석 · 매주 월요일 자동 발송
  </div>

</div>
</body>
</html>"""


def build_email_text(programs: list, profile: dict, total_fetched: int) -> str:
    today_str = date.today().strftime("%Y.%m.%d")
    name      = profile.get("basic", {}).get("name", "")
    lines     = [
        f"[주간 정부지원] 이번 주 추천 사업 {len(programs)}건 ({today_str})",
        "",
        f"안녕하세요! {name}님을 위한 추천 정부지원사업입니다.",
        "",
    ]

    top_list   = [p for p in programs if p.get("score", 0) >= 80]
    other_list = [p for p in programs if p.get("score", 0) < 80]

    if top_list:
        lines += ["🏆 TOP 추천 (적합도 80점 이상)", "─" * 40]
        for i, p in enumerate(top_list, 1):
            lines += [
                f"{i}. {p.get('title','')} — {p.get('agency','')}",
                f"   💰 {p.get('amount','확인 필요')}",
                f"   📅 {deadline_label(p.get('deadline',''))}",
                f"   ⭐ {p.get('reason','')}",
                f"   🚀 {p.get('action','')}",
                f"   🔗 {p.get('url','')}",
                "",
            ]

    if other_list:
        lines += ["📋 기타 추천 사업"]
        for p in other_list[:7]:
            lines.append(
                f"  - {p.get('title','')} ({p.get('score',0)}점) | {p.get('url','')}"
            )
        lines.append("")

    lines += [f"📊 총 {total_fetched}건 검토 → {len(programs)}건 추천", ""]
    return "\n".join(lines)


# ─── 채널별 발송 ──────────────────────────────────────────────────────────────

def send_email(to_addr: str, subject: str, html_body: str, text_body: str) -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning("Gmail 자격증명 미설정 — 이메일 발송 생략")
        return False
    if GMAIL_APP_PASSWORD in ("여기에-앱비밀번호-입력", ""):
        logger.warning("Gmail 앱 비밀번호가 플레이스홀더입니다. .env를 업데이트하세요.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to_addr
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html",  "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_USER, to_addr, msg.as_string())

        logger.info(f"이메일 발송 완료 → {to_addr}")
        return True
    except Exception as e:
        logger.error(f"이메일 발송 실패: {e}")
        return False


def send_slack(programs: list, total_fetched: int) -> bool:
    if not SLACK_WEBHOOK_URL:
        return False

    today_str = date.today().strftime("%Y.%m.%d")
    top_list  = [p for p in programs if p.get("score", 0) >= 80][:3]

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🏛️ 주간 정부지원사업 알림 ({today_str})"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"총 *{total_fetched}건* 검토 → *{len(programs)}건* 추천"},
        },
        {"type": "divider"},
    ]

    for p in top_list:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*[{p.get('score',0)}점] {p.get('title','')}*\n"
                    f"🏢 {p.get('agency','')} | 📅 {deadline_label(p.get('deadline',''))}\n"
                    f"⭐ {p.get('reason','')}\n"
                    f"<{p.get('url','#')}|신청하기 →>"
                ),
            },
        })

    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=15)
        resp.raise_for_status()
        logger.info("슬랙 발송 완료")
        return True
    except Exception as e:
        logger.error(f"슬랙 발송 실패: {e}")
        return False


import re as _re


def _notion_rich_text(text: str) -> list:
    return [{"type": "text", "text": {"content": str(text)[:2000]}}]


def _make_notion_title(p: dict) -> str:
    """'사업명 (신청기간~마감일)' 형식 제목 생성"""
    title    = p.get("title", "(제목 없음)")[:80]
    start    = p.get("start_date", "")
    deadline = p.get("deadline", "")

    if start and deadline:
        s = start.replace("-", ".")
        e = deadline.replace("-", ".")
        return f"{title} ({s}~{e})"
    elif deadline:
        e = deadline.replace("-", ".")
        return f"{title} (~{e})"
    return title


def _notion_page_blocks(p: dict) -> list:
    """사업 상세를 Notion 블록으로 변환 (페이지 내부)"""
    dl      = p.get("deadline", "")
    score   = p.get("score", 0)
    d_label = deadline_label(dl, ongoing=p.get("ongoing", False))

    def divider():
        return {"object": "block", "type": "divider", "divider": {}}

    def heading(text: str, level: int = 3) -> dict:
        t = f"heading_{level}"
        return {"object": "block", "type": t, t: {"rich_text": _notion_rich_text(text)}}

    def para(text: str) -> dict:
        return {"object": "block", "type": "paragraph",
                "paragraph": {"rich_text": _notion_rich_text(text)}}

    def callout(text: str, emoji: str = "💡") -> dict:
        return {
            "object": "block", "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": emoji},
                "rich_text": _notion_rich_text(text),
            },
        }

    def bullet(items: list[str]) -> list[dict]:
        return [
            {"object": "block", "type": "bulleted_list_item",
             "bulleted_list_item": {"rich_text": _notion_rich_text(item)}}
            for item in items
        ]

    return [
        heading("📋 기본 정보"),
        *bullet([
            f"🏢 주관기관: {p.get('agency', '미정')}",
            f"📂 분야: {p.get('category', '미정')}",
            f"🌏 지역: {p.get('region', '전국')}",
            f"💰 지원금액: {p.get('amount', '확인 필요')}",
            f"📅 신청기간: {'상시 모집' if p.get('ongoing') and not dl else f\"{p.get('start_date', '미정')} ~ {dl or '미정'}  ({d_label})\"}",
        ]),
        divider(),
        heading("🤖 AI 분석"),
        *bullet([f"적합도 점수: {score}점"]),
        callout(p.get("reason", ""), "⭐"),
        para(f"💡 핵심 혜택: {p.get('highlight', '')}"),
        divider(),
        heading("🚀 다음 행동"),
        callout(p.get("action", "상세 페이지 확인 후 신청 여부 결정"), "🚀"),
        divider(),
        heading("🔗 신청 링크"),
        para(p.get("url", "")),
    ]


def _get_notion_child_pages(headers: dict) -> list[dict]:
    """부모 페이지의 자식 페이지 블록 목록을 페이지네이션하여 전부 반환"""
    pages, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        try:
            resp = requests.get(
                f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
                headers=headers,
                params=params,
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            for block in data.get("results", []):
                if block.get("type") == "child_page":
                    pages.append(block)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception as e:
            logger.warning(f"자식 페이지 목록 조회 실패: {e}")
            break
    return pages


def _deadline_from_title(title: str) -> str | None:
    """제목 '사업명 (~2026.06.30)' 에서 마감일 추출 → 'YYYY-MM-DD'"""
    m = _re.search(r'~(\d{4})\.(\d{2})\.(\d{2})', title)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _cleanup_expired_pages(headers: dict) -> int:
    """마감일이 오늘보다 이전인 자식 페이지를 아카이브(삭제)"""
    today   = date.today()
    removed = 0
    for block in _get_notion_child_pages(headers):
        title    = block.get("child_page", {}).get("title", "")
        deadline = _deadline_from_title(title)
        if not deadline:
            continue
        try:
            if date.fromisoformat(deadline) < today:
                resp = requests.patch(
                    f"https://api.notion.com/v1/pages/{block['id']}",
                    headers=headers,
                    json={"archived": True},
                    timeout=20,
                )
                if resp.status_code == 200:
                    removed += 1
                    logger.info(f"만료 페이지 삭제: {title}")
                else:
                    logger.warning(f"삭제 실패 ({block['id']}): {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"만료 체크 실패 ({title}): {e}")
    return removed


def send_notion(programs: list) -> bool:
    """Notion 페이지에 자식 페이지로 추천 사업 추가 + 만료 항목 자동 삭제

    제목 형식: '사업명 (2026.05.17~2026.06.30)'
    페이지 내부: 기본정보 / AI분석 / 다음행동 / 신청링크
    """
    if not NOTION_API_KEY or not NOTION_PAGE_ID:
        logger.warning("NOTION_API_KEY 또는 NOTION_PAGE_ID 미설정")
        return False

    headers = {
        "Authorization":  f"Bearer {NOTION_API_KEY}",
        "Content-Type":   "application/json",
        "Notion-Version": "2022-06-28",
    }

    removed = _cleanup_expired_pages(headers)
    if removed:
        logger.info(f"만료 페이지 {removed}건 삭제 완료")

    success = 0
    for p in programs:
        page_title = _make_notion_title(p)
        payload = {
            "parent":     {"page_id": NOTION_PAGE_ID},
            "properties": {"title": {"title": [{"text": {"content": page_title}}]}},
            "children":   _notion_page_blocks(p),
        }
        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload,
                timeout=20,
            )
            if resp.status_code in (200, 201):
                success += 1
                logger.info(f"Notion 페이지 생성: {page_title}")
            else:
                logger.warning(f"Notion API 오류 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"Notion 페이지 생성 실패 ({p.get('title','')}): {e}")

    logger.info(f"Notion {success}/{len(programs)}건 추가 완료")
    return success > 0


# ─── 메인 진입점 ─────────────────────────────────────────────────────────────

def send_notification(context: dict) -> dict:
    """하네스에서 호출하는 메인 알림 발송 함수"""
    profile       = context.get("profile", {})
    analyzed      = context.get("analyzed", [])
    raw_count     = context.get("raw_count", 0)
    dry_run       = context.get("dry_run", False)
    notif_cfg     = profile.get("notification", {})

    min_score = notif_cfg.get("min_score", 60)
    max_items = notif_cfg.get("max_items", 10)
    email_addr = notif_cfg.get("email", "")

    # 분석 결과 없으면 파일에서 로드
    if not analyzed:
        files = sorted(Path("output").glob("analyzed_*.json"), reverse=True)
        if files:
            analyzed = json.loads(files[0].read_text(encoding="utf-8"))
            logger.info(f"{files[0]}에서 {len(analyzed)}건 로드")

    recommended = sorted(
        [p for p in analyzed if p.get("recommended") and p.get("score", 0) >= min_score],
        key=lambda x: x.get("score", 0),
        reverse=True,
    )[:max_items]

    logger.info(f"알림 대상: {len(recommended)}건 (기준: {min_score}점 이상)")

    if not recommended:
        logger.info("추천 사업 없음 — 알림 미발송")
        context["notification_sent"] = False
        return context

    today_str = date.today().strftime("%Y.%m.%d")
    subject   = f"[주간 정부지원] 이번 주 추천 사업 {len(recommended)}건 ({today_str})"
    html_body = build_email_html(recommended, profile, raw_count)
    text_body = build_email_text(recommended, profile, raw_count)

    # DRY-RUN: 콘솔에만 출력
    if dry_run:
        logger.info("[DRY-RUN] 알림 미리보기 출력")
        print("\n" + "=" * 60)
        print(f"제목: {subject}")
        print("=" * 60)
        print(text_body)
        print("=" * 60 + "\n")
        context["notification_sent"] = False
        return context

    results: dict = {}

    if email_addr:
        results["email"] = send_email(email_addr, subject, html_body, text_body)

    if notif_cfg.get("slack") and SLACK_WEBHOOK_URL:
        results["slack"] = send_slack(recommended, raw_count)

    if notif_cfg.get("notion") and NOTION_API_KEY and NOTION_PAGE_ID:
        results["notion"] = send_notion(recommended)

    context["notification_sent"]    = any(results.values())
    context["notification_results"] = results
    logger.info(f"알림 결과: {results}")
    return context


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
    ctx = {
        "profile":  profile,
        "date":     datetime.now().strftime("%Y%m%d"),
        "dry_run":  "--dry-run" in sys.argv,
    }
    result = send_notification(ctx)
    status = "DRY-RUN" if ctx["dry_run"] else ("성공" if result.get("notification_sent") else "실패/미발송")
    print(f"\n알림 결과: {status}")
