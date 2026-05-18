# 대한민국 정부지원사업 주간 자동알림 시스템

## 프로젝트 목적
`profile.json`에 정의된 사용자(기업/개인) 프로필을 기준으로,
매주 자동으로 대한민국 정부 지원사업을 수집·분석·정리하여 알림을 발송한다.

## 하네스 엔지니어링 원칙
이 프로젝트는 **하네스(Harness) 방식**으로 구현한다:
1. `harness.py`가 모든 단계를 순서대로 실행하는 단일 진입점이다
2. 각 단계는 독립 모듈(fetcher, analyzer, notifier)로 분리한다
3. 각 단계의 성공/실패를 로깅하고, 실패 시 재시도한다
4. 중간 결과물을 `output/` 폴더에 JSON으로 저장하여 디버깅을 가능하게 한다
5. 실행 결과는 항상 `logs/` 폴더에 기록한다

## 파일 구조
```
gov-support-tracker/
├── CLAUDE.md           ← 이 파일 (Claude Code 지시서)
├── profile.json        ← 사용자 프로필 (수정 필요)
├── .env                ← API 키 (절대 커밋 금지)
├── requirements.txt    ← Python 의존성
├── harness.py          ← 메인 실행 파일 (단일 진입점)
├── fetcher.py          ← 공공데이터 API 수집 모듈
├── analyzer.py         ← Claude API 분석 모듈
├── notifier.py         ← 알림 발송 모듈
├── scheduler.py        ← 매주 자동 실행 스케줄러
├── output/             ← 중간 결과물 저장 (자동 생성)
└── logs/               ← 실행 로그 저장 (자동 생성)
```

## 구현 지시사항

### Step 1: fetcher.py 구현
다음 API에서 지원사업 데이터를 수집한다:

**우선순위 API (공공데이터포털 - data.go.kr):**
- 중소벤처기업부 지원사업 공고 API
  - 엔드포인트: `https://www.bizinfo.go.kr/web/lay1/bBS/S1T122C128/AS/74/view.do` (스크래핑 또는 API)
  - 또는 공공데이터포털 `bizSupportService` API 활용
- K-Startup 창업지원포털: `https://www.k-startup.go.kr/`
- 기업마당: `https://www.bizinfo.go.kr/`

**수집 필드:**
```python
{
    "id": "고유식별자",
    "title": "사업명",
    "agency": "주관기관",
    "category": "분야 (창업/R&D/수출/고용/시설 등)",
    "target": "지원대상",
    "amount": "지원금액",
    "deadline": "신청마감일 (YYYY-MM-DD)",
    "region": "지역 (전국/서울 등)",
    "url": "상세페이지 URL",
    "fetched_at": "수집시각 (ISO8601)"
}
```

**구현 규칙:**
- requests + BeautifulSoup 또는 공식 API 사용
- 공공데이터포털 API 키는 `.env`의 `OPEN_DATA_API_KEY` 사용
- 수집 결과를 `output/raw_YYYYMMDD.json`으로 저장
- 네트워크 오류 시 최대 3회 재시도 (exponential backoff)
- 중복 제거: id 기준 dedup 처리

### Step 2: analyzer.py 구현
Claude API를 사용하여 수집된 사업 중 프로필에 맞는 것을 분석한다.

**Claude API 호출 방식:**
```python
import anthropic

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY는 .env에서 자동 로드

def analyze_programs(programs: list, profile: dict) -> list:
    """각 지원사업의 적합도를 분석하여 점수와 이유를 반환"""
    
    system_prompt = """당신은 대한민국 정부 지원사업 전문가입니다.
    주어진 기업/개인 프로필을 기준으로 각 지원사업의 적합도를 분석하세요.
    반드시 JSON 형식으로만 응답하세요."""
    
    user_prompt = f"""
    ## 사용자 프로필
    {json.dumps(profile, ensure_ascii=False, indent=2)}
    
    ## 분석할 지원사업 목록
    {json.dumps(programs, ensure_ascii=False, indent=2)}
    
    ## 응답 형식 (JSON 배열)
    각 사업에 대해 다음 필드를 포함하세요:
    - id: 사업 ID
    - score: 적합도 점수 (0-100)
    - reason: 적합한/부적합한 핵심 이유 (2문장 이내)
    - highlight: 사용자가 주목해야 할 핵심 혜택 (1문장)
    - action: 당장 해야 할 행동 (예: "3일 내 신청 필요")
    - recommended: true/false
    """
    
    # 비용 절감: 한 번에 최대 20개씩 배치 처리
    # score >= 60인 것만 recommended = true로 표시
```

**분석 결과를 `output/analyzed_YYYYMMDD.json`으로 저장**

### Step 3: notifier.py 구현
분석 결과 중 `recommended=true`인 사업만 추려서 알림 발송.

**지원 알림 채널 (profile.json의 `notification` 설정 기준):**

1. **이메일** (기본):
   - smtplib 사용, Gmail SMTP
   - HTML 형식의 주간 리포트 이메일
   - `.env`의 `GMAIL_USER`, `GMAIL_APP_PASSWORD` 사용

2. **슬랙** (선택):
   - Slack Webhook URL 사용
   - `.env`의 `SLACK_WEBHOOK_URL` 사용

3. **노션** (선택):
   - Notion API로 데이터베이스에 자동 추가
   - `.env`의 `NOTION_API_KEY`, `NOTION_DATABASE_ID` 사용

**이메일 본문 형식:**
```
제목: [주간 정부지원] 이번 주 추천 사업 N건 (YYYY.MM.DD)

안녕하세요! 이번 주 추천 정부지원사업입니다.

🏆 TOP 추천 (적합도 80점 이상)
1. [사업명] - [기관명]
   💰 지원금액: XXX
   📅 마감: YYYY-MM-DD (D-N일)
   ⭐ 추천이유: ...
   🔗 신청하기: URL

...

📊 이번 주 수집 현황: 총 XX건 검토, N건 추천
```

### Step 4: harness.py 구현 (핵심)
모든 단계를 조율하는 하네스 메인 파일.

```python
#!/usr/bin/env python3
"""
정부지원사업 주간 알림 하네스
실행: python harness.py [--dry-run] [--force]
"""

import json, logging, os, sys
from datetime import datetime
from pathlib import Path

# 하네스 설정
STEPS = [
    {"name": "데이터 수집", "module": "fetcher", "func": "fetch_all"},
    {"name": "Claude 분석", "module": "analyzer", "func": "analyze_programs"},
    {"name": "알림 발송", "module": "notifier", "func": "send_notification"},
]

def run_harness(dry_run=False):
    """하네스 실행 - 각 단계를 순서대로 실행하고 결과를 다음 단계에 전달"""
    
    # 로그 설정
    Path("logs").mkdir(exist_ok=True)
    Path("output").mkdir(exist_ok=True)
    
    log_file = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
    )
    
    # 프로필 로드
    profile = json.loads(Path("profile.json").read_text(encoding="utf-8"))
    
    context = {"profile": profile, "dry_run": dry_run, "date": datetime.now().strftime("%Y%m%d")}
    
    for step in STEPS:
        logging.info(f"━━━ {step['name']} 시작 ━━━")
        try:
            module = __import__(step["module"])
            func = getattr(module, step["func"])
            context = func(context)  # 각 단계는 context를 받아서 업데이트된 context를 반환
            logging.info(f"✅ {step['name']} 완료")
        except Exception as e:
            logging.error(f"❌ {step['name']} 실패: {e}")
            # 알림 단계 실패는 무시, 수집/분석 실패는 중단
            if step["module"] != "notifier":
                raise
    
    logging.info("🎉 하네스 실행 완료")
    return context

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run_harness(dry_run=dry_run)
```

### Step 5: scheduler.py 구현
매주 자동 실행 설정.

```python
# schedule 라이브러리 사용
import schedule, time, subprocess

def run_weekly():
    subprocess.run(["python", "harness.py"], check=True)

# 매주 월요일 오전 9시 실행
schedule.every().monday.at("09:00").do(run_weekly)

# 또는 cron 방식 (더 안정적):
# 0 9 * * 1 cd /path/to/gov-support-tracker && python harness.py
```

## 환경변수 (.env 파일)
```
ANTHROPIC_API_KEY=sk-ant-...
OPEN_DATA_API_KEY=...          # data.go.kr 공공데이터포털 API 키
GMAIL_USER=your@gmail.com
GMAIL_APP_PASSWORD=...         # Gmail 앱 비밀번호 (2단계 인증 필요)
SLACK_WEBHOOK_URL=https://...  # 선택
NOTION_API_KEY=secret_...      # 선택
NOTION_DATABASE_ID=...         # 선택
```

## 구현 순서 (Claude Code가 이 순서로 작업할 것)
1. `requirements.txt` 생성
2. `profile.json` 템플릿 생성
3. `fetcher.py` 구현 및 테스트 (`python fetcher.py --test`)
4. `analyzer.py` 구현 및 테스트 (`python analyzer.py --test`)
5. `notifier.py` 구현 및 테스트 (`python notifier.py --dry-run`)
6. `harness.py` 통합 및 테스트 (`python harness.py --dry-run`)
7. `scheduler.py` 구현
8. README.md 작성

## 테스트 방법
```bash
# 전체 파이프라인 테스트 (실제 알림 발송 없이)
python harness.py --dry-run

# 개별 모듈 테스트
python fetcher.py --test     # 샘플 데이터 5건 수집
python analyzer.py --test    # output/raw_*.json 분석
python notifier.py --dry-run # 알림 내용 콘솔 출력
```

## 주의사항
- `.env` 파일은 절대 git에 커밋하지 말 것 (`.gitignore`에 추가)
- 공공데이터포털 API 키는 data.go.kr에서 무료 발급 가능
- Claude API 비용 절감을 위해 배치 처리 및 캐싱 구현
- 마감일이 지난 사업은 자동 필터링
