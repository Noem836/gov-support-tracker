#!/usr/bin/env python3
"""
Claude API를 사용한 지원사업 적합도 분석 모듈
프롬프트 캐싱으로 비용 절감, 20개씩 배치 처리
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

BASE_OUTPUT = Path("output")
BATCH_SIZE  = 20
MODEL       = "claude-sonnet-4-6"

logger = logging.getLogger(__name__)


def build_user_prompt(programs: list, profile: dict) -> str:
    return f"""## 사용자 프로필
{json.dumps(profile, ensure_ascii=False, indent=2)}

## 분석할 지원사업 목록
{json.dumps(programs, ensure_ascii=False, indent=2)}

## 응답 형식 (반드시 순수 JSON 배열만 출력, 다른 텍스트 금지)
[
  {{
    "id": "사업 ID (원본 그대로)",
    "score": 적합도 점수 0~100 (정수),
    "reason": "핵심 이유 2문장 이내",
    "highlight": "주목해야 할 핵심 혜택 1문장",
    "action": "당장 해야 할 행동 (예: '3일 내 신청 필요')",
    "recommended": true 또는 false
  }}
]
score >= 60이면 recommended = true로 설정하세요."""


def analyze_batch(client: anthropic.Anthropic, programs: list, profile: dict) -> list:
    """단일 배치를 Claude API로 분석하고 결과 반환"""
    if not programs:
        return []

    system_prompt = (
        "당신은 대한민국 정부 금융지원·복지제도 전문가입니다. "
        "주어진 개인 프로필(나이, 소득, 고용상태, 주거상황, 관심분야 등)을 기준으로 "
        "각 지원제도의 수혜 가능성과 적합도를 분석하세요. "
        "반드시 '돈으로 직접 지원'하는 제도만 높은 점수를 부여하세요: "
        "현금지급·보조금·장려금·활동비·창작지원금·융자·바우처·장학금·실업급여·보상금 등. "
        "교육·컨설팅·공간제공 등 현금이 아닌 지원은 낮은 점수(30점 이하)를 부여하세요. "
        "신청 자격 충족 여부와 예상 수혜 금액을 구체적으로 언급하세요. "
        "반드시 JSON 배열 형식으로만 응답하세요."
    )

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},  # 프롬프트 캐싱으로 비용 절감
                }
            ],
            messages=[{"role": "user", "content": build_user_prompt(programs, profile)}],
        )

        raw = resp.content[0].text.strip()

        # 코드 블록 래핑 제거
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        results = json.loads(raw)
        if not isinstance(results, list):
            logger.warning("분석 결과가 배열이 아닙니다")
            return []
        return results

    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 실패: {e}")
        return []
    except anthropic.APIError as e:
        logger.error(f"Claude API 오류: {e}")
        return []


def prefilter(programs: list, profile: dict) -> list:
    """관심 키워드 기반 1차 필터링으로 API 비용 절감"""
    interests    = profile.get("interests", {})
    keywords     = [k.lower() for k in interests.get("keywords", [])]
    exclude_kws  = [k.lower() for k in interests.get("exclude_keywords", [])]

    result = []
    for p in programs:
        text = f"{p.get('title','')} {p.get('category','')} {p.get('target','')}".lower()
        if exclude_kws and any(ex in text for ex in exclude_kws):
            continue
        result.append(p)
    return result


def analyze_programs(context: dict) -> dict:
    """하네스에서 호출하는 메인 분석 함수"""
    profile   = context.get("profile", {})
    programs  = context.get("programs", [])
    today_str = context.get("date", datetime.now().strftime("%Y%m%d"))
    dry_run   = context.get("dry_run", False)

    BASE_OUTPUT.mkdir(exist_ok=True)

    # 프로그램 목록이 없으면 raw 파일에서 로드
    if not programs:
        raw_files = sorted(BASE_OUTPUT.glob("raw_*.json"), reverse=True)
        if raw_files:
            programs = json.loads(raw_files[0].read_text(encoding="utf-8"))
            logger.info(f"{raw_files[0]}에서 {len(programs)}건 로드")
        else:
            logger.warning("분석할 데이터가 없습니다")
            context["analyzed"] = []
            return context

    logger.info(f"분석 대상: {len(programs)}건")

    # ── DRY-RUN 모드 ──────────────────────────────────────────────────────────
    if dry_run:
        logger.info("[DRY-RUN] Claude API 미호출, 더미 결과 생성")
        analyzed = []
        for i, p in enumerate(programs[:15]):
            score = 80 if i % 4 == 0 else (65 if i % 2 == 0 else 40)
            analyzed.append({
                **p,
                "score":       score,
                "reason":      f"[DRY-RUN] 프로필과 {score}% 일치하는 사업입니다.",
                "highlight":   f"[DRY-RUN] 지원금: {p.get('amount','확인필요')}",
                "action":      "상세 페이지 확인 후 신청 여부 결정",
                "recommended": score >= 60,
            })
        out_path = BASE_OUTPUT / f"analyzed_{today_str}.json"
        out_path.write_text(json.dumps(analyzed, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"DRY-RUN 결과 저장: {out_path}")
        context["analyzed"] = analyzed
        return context

    # ── 실제 분석 ─────────────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-여기에"):
        logger.error("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env를 확인하세요.")
        context["analyzed"] = []
        return context

    client = anthropic.Anthropic(api_key=api_key)

    filtered_programs = prefilter(programs, profile)
    logger.info(f"1차 필터링 후: {len(filtered_programs)}건")

    id_to_program = {p["id"]: p for p in programs}
    all_results: list = []
    total_batches = (len(filtered_programs) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(filtered_programs), BATCH_SIZE):
        batch      = filtered_programs[i: i + BATCH_SIZE]
        batch_num  = i // BATCH_SIZE + 1
        logger.info(f"배치 {batch_num}/{total_batches} 처리 ({len(batch)}건)")

        batch_results = analyze_batch(client, batch, profile)
        for result in batch_results:
            original = id_to_program.get(result.get("id", ""), {})
            all_results.append({**original, **result})

    # 점수 내림차순 정렬
    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

    recommended_count = sum(1 for r in all_results if r.get("recommended"))
    logger.info(f"분석 완료: {len(all_results)}건 / 추천: {recommended_count}건")

    out_path = BASE_OUTPUT / f"analyzed_{today_str}.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"분석 결과 저장: {out_path}")

    context["analyzed"] = all_results
    return context


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
    ctx = {
        "profile":  profile,
        "date":     datetime.now().strftime("%Y%m%d"),
        "dry_run":  "--test" in sys.argv,
    }
    result = analyze_programs(ctx)
    analyzed    = result.get("analyzed", [])
    recommended = [a for a in analyzed if a.get("recommended")]
    print(f"\n분석: {len(analyzed)}건 / 추천: {len(recommended)}건")
    for item in recommended[:5]:
        print(f"  [{item.get('score')}점] {item.get('title','')[:50]}")
        print(f"        {item.get('reason','')[:80]}")
