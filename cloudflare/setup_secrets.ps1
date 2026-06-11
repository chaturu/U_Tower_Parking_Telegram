# 남은 Worker 시크릿 등록 + 텔레그램 웹훅 연결
# 실행: PowerShell에서 이 폴더로 이동 후 .\setup_secrets.ps1
# (WEBHOOK_SECRET은 이미 등록되어 있고, 값은 ~\.utower_webhook_secret에 있음)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# wrangler 로그인 정보가 저장된 위치를 지정 (이게 없으면 CLOUDFLARE_API_TOKEN 오류 발생)
$env:XDG_CONFIG_HOME = "$env:APPDATA\xdg.config"

# 콘솔 한글 출력 설정
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$bot = Read-Host '텔레그램 봇 토큰 (BotFather 발급, 123456:ABC... 형식)'
$pat = Read-Host 'GitHub fine-grained PAT (github_pat_...)'
$master = Read-Host '마스터 텔레그램 채팅 ID (기존 TELEGRAM_CHAT_ID와 동일)'

$bot.Trim() | npx wrangler secret put TELEGRAM_BOT_TOKEN
$pat.Trim() | npx wrangler secret put GH_TOKEN
$master.Trim() | npx wrangler secret put MASTER_CHAT_ID

# 텔레그램 웹훅 연결
$webhookSecret = (Get-Content "$env:USERPROFILE\.utower_webhook_secret" -Raw).Trim()
$workerUrl = 'https://u-tower-parking-telegram.ypd0004.workers.dev'
$res = Invoke-RestMethod "https://api.telegram.org/bot$($bot.Trim())/setWebhook?url=$workerUrl&secret_token=$webhookSecret"
Write-Host "setWebhook 결과: ok=$($res.ok) $($res.description)"
$info = Invoke-RestMethod "https://api.telegram.org/bot$($bot.Trim())/getWebhookInfo"
Write-Host "연결된 웹훅: $($info.result.url)"
Write-Host ''
Write-Host '완료! 텔레그램에서 봇에게 /도움말 을 보내 응답을 확인하세요.'
