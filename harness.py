#!/usr/bin/env python3
"""
정부지원사업 주간 알림 하네스 — 단일 진입점
실행: python harness.py [--dry-run] [--force]
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STEPS = [
    {"name": "데이터 수집", "module": "fetcher",  "func": "fetch_all"},
    {"name": "Claude 분석", "module": "analyzer", "func": "analyze_programs"},
    {"name": "알림 발송",   "module": "notifier", "func": "send_notification"},
]


def run_harness(dry_run: bool = False, force: bool = False) -> dict:
    Path("logs").mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)

    log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logger = logging.getLogger(__name__)
    logger.info(f"{'=' * 50}")
    logger.info(f"하네스 시작  {'[DRY-RUN]' if dry_run else '[실제 실행]'}")
    logger.info(f"{'=' * 50}")

    profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
    context: dict = {
        "profile": profile,
        "dry_run": dry_run,
        "date":    datetime.now().strftime("%Y%m%d"),
    }

    for step in STEPS:
        logger.info(f"━━━ {step['name']} 시작 ━━━")
        try:
            module = __import__(step["module"])
            func   = getattr(module, step["func"])
            context = func(context)
            logger.info(f"✅ {step['name']} 완료")
        except Exception as e:
            logger.error(f"❌ {step['name']} 실패: {e}", exc_info=True)
            # 알림 실패는 무시, 수집·분석 실패는 중단
            if step["module"] != "notifier":
                raise

    # ── 최종 요약 ─────────────────────────────────────────────────────────────
    analyzed    = context.get("analyzed", [])
    recommended = [a for a in analyzed if a.get("recommended")]

    logger.info("─" * 50)
    logger.info("🎉 하네스 실행 완료")
    logger.info(f"   수집: {context.get('raw_count', 0)}건")
    logger.info(f"   분석: {len(analyzed)}건 / 추천: {len(recommended)}건")
    notif_ok = context.get("notification_sent")
    logger.info(f"   알림: {'발송 완료' if notif_ok else 'DRY-RUN 또는 조건 미충족'}")
    logger.info(f"   로그: {log_file}")

    return context


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force   = "--force"   in sys.argv
    try:
        run_harness(dry_run=dry_run, force=force)
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 하네스 실패: {e}")
        sys.exit(1)
