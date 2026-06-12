# U-Tower Parking Telegram

유타워 주차 등록 자동화 + 텔레그램 봇 파이프라인입니다.

차량 목록(VIP/일반/블랙리스트)과 입차 상태는 **Cloudflare KV에만 저장**되며
GitHub 저장소에는 올라가지 않습니다 (개인정보 노출 방지).

```
[텔레그램 사용자]
   │  /추가, /삭제, /일반추가, /일반삭제, /목록, /상태, /실행, /도움말
   ▼
[Cloudflare Worker + KV]  ←─ Cron Trigger (KST 08~20시 매 정각)
   │  KV: vip_list, car_list, blacklist, status
   ├─ 텔레그램 명령으로 KV 차량 목록 관리
   ├─ /상태 → KV status 즉시 응답
   ├─ GET /lists  → Action에 차량 목록 제공 (Bearer LIST_TOKEN)
   ├─ POST /status ← Action이 실행 결과 업로드 (Bearer LIST_TOKEN)
   └─ repository_dispatch (run-parking-vip)
   ▼
[GitHub Actions: parking_vip.yml]
   └─ parking_bot_vip.py
      ├─ 주차장 시스템 로그인 → 차량 검색
      ├─ VIP 차량: 할인 등록 + 개인 입차/출차임박/경과 알림
      ├─ 일반 차량: 입차 상태 확인만 (할인 등록 없음, 마스터 알림에 포함)
      ├─ 마스터 입차 요약 / 최근 출차 요약
      └─ data/parking_stats.db + docs/index.html 커밋
```

## 차량 구분

| 구분 | 할인 등록 | 개인 알림 | 마스터 알림 |
|---|---|---|---|
| VIP (`/추가`) | O (4계정) | 입차/출차임박/경과 | 입차/출차 요약 포함 |
| 일반 (`/일반추가`) | X | X | 입차/출차 요약 포함 |

## 텔레그램 명령

| 명령 | 권한 | 동작 |
|---|---|---|
| `/상태` | 누구나 | 내 채팅에 등록된 차량의 입차시간·출차예정·남은시간 |
| `/상태 1234` | 누구나 | 끝 4자리로 특정 차량 조회 |
| `/추가 123가1234 [채팅ID] [설명]` | 마스터 | VIP 차량 추가 (채팅ID 생략 시 보낸 채팅) |
| `/삭제 123가1234` | 마스터 | VIP 차량 제거 |
| `/일반추가 123가1234 [설명]` | 마스터 | 일반 차량 추가 (입차 확인만) |
| `/일반삭제 123가1234` | 마스터 | 일반 차량 제거 |
| `/목록` | 마스터 | VIP/일반 차량 목록 |
| `/실행` | 마스터 | GitHub Action 즉시 실행 |
| `/도움말` | 누구나 | 명령 안내 |

상태 조회는 마지막 정각 실행 결과(KV `status`) 기준이며,
남은시간은 조회 시점에 다시 계산됩니다. 마지막 실행 이후 입차한 차량은 다음 정각에 반영됩니다.

## 최초 설정 절차

### 1. GitHub 저장소

1. 이 폴더를 `U_Tower_Parking_Telegram` 저장소로 push
2. 저장소 **Settings → Secrets and variables → Actions → Secrets** 등록:
   - `USER1_ID`/`USER1_PW` ~ `USER4_ID`/`USER4_PW` — 주차장 시스템 계정
   - `TELEGRAM_BOT_TOKEN` — 봇 토큰
   - `TELEGRAM_CHAT_ID` — 마스터(요약 알림) 채팅 ID
   - `LIST_TOKEN` — Worker `/lists`, `/status` 호출용 토큰 (3단계와 동일 값)

### 2. Fine-grained PAT 발급 (Worker → GitHub 용)

GitHub **Settings → Developer settings → Fine-grained tokens → Generate new token**
- Repository access: `U_Tower_Parking_Telegram`만
- Permissions: **Contents → Read and write** (repository_dispatch용)

### 3. Cloudflare Worker 배포

전제조건: [Node.js](https://nodejs.org) 설치, Cloudflare 계정(무료)

```powershell
cd cloudflare
npx wrangler login
npx wrangler kv namespace create PARKING_KV   # 출력된 id를 wrangler.toml에 반영
npx wrangler deploy
```

시크릿 등록은 PowerShell 파이프(`echo | wrangler secret put`)를 쓰면 CR(\r)이 섞여 오작동하므로
**반드시 JSON 파일 + `secret bulk`**를 사용하세요:

```powershell
# secrets.json: {"TELEGRAM_BOT_TOKEN":"...","WEBHOOK_SECRET":"...","GH_TOKEN":"...","MASTER_CHAT_ID":"...","LIST_TOKEN":"..."}
npx wrangler secret bulk secrets.json
del secrets.json
```

차량 목록 초기 데이터 등록 (CSV 형식: `차량번호,텔레그램_채팅ID,입차알림,출차임박알림,설명`):

```powershell
npx wrangler kv key put vip_list --path vip_list.txt --binding KV --remote
npx wrangler kv key put car_list --path cars.txt --binding KV --remote
npx wrangler kv key put blacklist --path blacklist.txt --binding KV --remote
```

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
- `data/parking_stats.db`와 `docs/index.html` 리포트에는 차량번호가 포함됩니다.
  저장소가 공개라면 비공개 전환을 권장합니다.
