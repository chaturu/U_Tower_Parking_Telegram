# U-Tower Parking Telegram

유타워 주차 등록 자동화 + 텔레그램 봇 파이프라인입니다.

```
[텔레그램 사용자]
   │  /추가, /삭제, /목록, /상태, /실행, /도움말
   ▼
[Cloudflare Worker]  ←─ Cron Trigger (KST 08~20시 매 정각)
   │  GitHub API
   ├─ vip_list.txt 커밋 (차량 추가/삭제)
   ├─ data/status.json 조회 (상태 즉시 응답)
   └─ repository_dispatch (run-parking-vip)
   ▼
[GitHub Actions: parking_vip.yml]
   └─ parking_bot_vip.py
      ├─ 주차장 시스템 로그인 → 차량 검색 → 할인 등록
      ├─ 개인 입차 알림 (입차시간 + 출차시간 = 입차 + 5시간)
      ├─ 개인 출차임박 알림 (60분 이내) / 출차시간 경과 알림 (각 1회)
      ├─ 마스터 입차 요약 / 최근 출차 요약
      └─ data/status.json + data/parking_stats.db + docs/index.html 커밋
```

## 텔레그램 명령

| 명령 | 권한 | 동작 |
|---|---|---|
| `/상태` | 누구나 | 내 채팅에 등록된 차량의 입차시간·출차예정·남은시간 |
| `/상태 1234` | 누구나 | 끝 4자리로 특정 차량 조회 |
| `/추가 123가1234 [채팅ID] [설명]` | 마스터 | vip_list.txt에 차량 추가 (채팅ID 생략 시 보낸 채팅) |
| `/삭제 123가1234` | 마스터 | vip_list.txt에서 차량 제거 |
| `/목록` | 마스터 | 등록 차량 목록 |
| `/실행` | 마스터 | GitHub Action 즉시 실행 |
| `/도움말` | 누구나 | 명령 안내 |

상태 조회는 마지막 정각 실행 시점의 `data/status.json` 기준이며,
남은시간은 조회 시점에 다시 계산됩니다. 마지막 실행 이후 입차한 차량은 다음 정각에 반영됩니다.

## 최초 설정 절차

### 1. GitHub 저장소

1. 이 폴더를 `U_Tower_Parking_Telegram` 저장소로 push
2. 저장소 **Settings → Secrets and variables → Actions → Secrets** 등록:
   - `USER1_ID`/`USER1_PW` ~ `USER4_ID`/`USER4_PW` — 주차장 시스템 계정 (기존 저장소와 동일 값)
   - `TELEGRAM_BOT_TOKEN` — 봇 토큰
   - `TELEGRAM_CHAT_ID` — 마스터(요약 알림) 채팅 ID

### 2. Fine-grained PAT 발급 (Worker → GitHub 용)

GitHub **Settings → Developer settings → Fine-grained tokens → Generate new token**
- Repository access: `U_Tower_Parking_Telegram`만
- Permissions: **Contents → Read and write** (파일 커밋 + repository_dispatch 둘 다 이 권한으로 가능)

### 3. Cloudflare Worker 배포

전제조건: [Node.js](https://nodejs.org) 설치 (wrangler 실행에 필요), Cloudflare 계정(무료)

```powershell
cd cloudflare
npx wrangler login
npx wrangler deploy
npx wrangler secret put TELEGRAM_BOT_TOKEN   # 봇 토큰
npx wrangler secret put WEBHOOK_SECRET       # 임의의 긴 문자열 (4단계에서 재사용)
npx wrangler secret put GH_TOKEN             # 2단계에서 만든 PAT
npx wrangler secret put MASTER_CHAT_ID       # 마스터 채팅 ID
```

배포 후 출력되는 Worker URL을 기억해 두세요 (예: `https://u-tower-parking-telegram.<계정>.workers.dev`).

### 4. 텔레그램 웹훅 연결

```powershell
curl "https://api.telegram.org/bot<봇토큰>/setWebhook?url=<Worker URL>&secret_token=<WEBHOOK_SECRET 값>"
```

확인: `curl "https://api.telegram.org/bot<봇토큰>/getWebhookInfo"`

### 5. 그룹에서 사용할 경우

- 봇을 그룹에 초대
- `/명령` 형식은 바로 동작합니다. "상태" 같은 일반 텍스트에도 반응하게 하려면
  BotFather → Bot Settings → **Group Privacy → Turn off**

## 스케줄

Cloudflare Cron(`cloudflare/wrangler.toml`)이 **KST 08~20시 매 정각** `repository_dispatch`를 보내
GitHub Action을 실행합니다. 시간 변경 시 `crons` 값을 수정 후 `npx wrangler deploy`.

## 옵션

알림 동작은 [options.txt](options.txt)에서 조정합니다 (출차임박알림분=60 등).

## 주의

- 기존 `U_Tower_Parking_Bot` 저장소의 스케줄 트리거와 동시에 돌리면 할인 등록이 중복 시도됩니다.
  이 파이프라인 검증 후 기존 트리거를 중지하세요.
