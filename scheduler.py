#!/usr/bin/env python3
"""
매월 자동 실행 스케줄러
실행: python scheduler.py [--run-now]

cron 대안 (더 안정적):
  0 9 1 * * cd /path/to/gov-support-tracker && python harness.py >> logs/cron.log 2>&1
"""

import logging
import subprocess
import sys
from datetime import datetime

import schedule
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def run_monthly() -> None:
    logger.info(f"월간 하네스 실행 시작: {datetime.now().isoformat()}")
    try:
        result = subprocess.run(
            [sys.executable, "harness.py"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3600,  # 최대 1시간
        )
        if result.returncode == 0:
            logger.info("하네스 실행 성공")
            if result.stdout:
                logger.info(result.stdout[-1000:])
        else:
            logger.error(f"하네스 실행 실패 (종료코드: {result.returncode})")
            if result.stderr:
                logger.error(result.stderr[-2000:])
    except subprocess.TimeoutExpired:
        logger.error("하네스 타임아웃 (1시간 초과)")
    except Exception as e:
        logger.error(f"하네스 실행 오류: {e}")


def main() -> None:
    # profile.json의 send_day_of_month / send_time 설정 반영
    try:
        import json
        from pathlib import Path
        profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
        notif          = profile.get("notification", {})
        send_day_of_month = int(notif.get("send_day_of_month", 1))  # 매월 며칠 (기본: 1일)
        send_time      = notif.get("send_time", "09:00")
    except Exception:
        send_day_of_month, send_time = 1, "09:00"

    # schedule 라이브러리는 월간 스케줄을 직접 지원하지 않으므로 매일 확인 후 해당 날짜에만 실행
    def monthly_check():
        if datetime.now().day == send_day_of_month:
            run_monthly()

    schedule.every().day.at(send_time).do(monthly_check)

    logger.info(f"스케줄러 시작: 매월 {send_day_of_month}일 {send_time} 실행")
    next_run = schedule.next_run()
    if next_run:
        logger.info(f"다음 체크 예정: {next_run}")

    if "--run-now" in sys.argv:
        logger.info("--run-now: 즉시 실행")
        run_monthly()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
