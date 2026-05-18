#!/usr/bin/env python3
"""
매주 자동 실행 스케줄러
실행: python scheduler.py [--run-now]

cron 대안 (더 안정적):
  0 9 * * 1 cd /path/to/gov-support-tracker && python harness.py >> logs/cron.log 2>&1
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


def run_weekly() -> None:
    logger.info(f"주간 하네스 실행 시작: {datetime.now().isoformat()}")
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
    # profile.json의 send_day / send_time 설정 반영
    try:
        import json
        from pathlib import Path
        profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
        notif   = profile.get("notification", {})
        send_day  = notif.get("send_day", "monday").lower()
        send_time = notif.get("send_time", "09:00")
    except Exception:
        send_day, send_time = "monday", "09:00"

    day_map = {
        "monday": schedule.every().monday,
        "tuesday": schedule.every().tuesday,
        "wednesday": schedule.every().wednesday,
        "thursday": schedule.every().thursday,
        "friday": schedule.every().friday,
        "saturday": schedule.every().saturday,
        "sunday": schedule.every().sunday,
    }
    trigger = day_map.get(send_day, schedule.every().monday)
    trigger.at(send_time).do(run_weekly)

    logger.info(f"스케줄러 시작: 매주 {send_day} {send_time} 실행")
    next_run = schedule.next_run()
    if next_run:
        logger.info(f"다음 실행 예정: {next_run}")

    if "--run-now" in sys.argv:
        logger.info("--run-now: 즉시 실행")
        run_weekly()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
