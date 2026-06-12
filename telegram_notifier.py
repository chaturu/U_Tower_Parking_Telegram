import os
import requests

from time_utils import format_remaining, get_exit_time

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
MASTER_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("  ⚠️ Telegram token is not configured.")
        return False
    if not chat_id:
        print("  ⚠️ Telegram chat_id is not configured.")
        return False
    try:
        res = requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
        if not res.ok:
            print(f"  ⚠️ Telegram API error: {res.status_code} {res.text[:100]}")
            return False
        return True
    except Exception as e:
        print(f"  ⚠️ Telegram send failed: {e}")
        return False


def send_entry_alert(chat_id, vehicle, entry_dt, exit_dt=None):
    exit_dt = exit_dt or get_exit_time(entry_dt)
    return send_message(chat_id,
        f"🚗 {vehicle}\n"
        f"{entry_dt.strftime('%m-%d %H:%M')} → {exit_dt.strftime('%m-%d %H:%M')}\n"
        f"남은시간: {format_remaining(exit_dt)}")


def send_imminent_alert(chat_id, vehicle, entry_dt, exit_dt=None):
    exit_dt = exit_dt or get_exit_time(entry_dt)
    return send_message(chat_id,
        f"⏰ {vehicle}\n"
        f"{entry_dt.strftime('%m-%d %H:%M')} → {exit_dt.strftime('%m-%d %H:%M')}\n"
        f"남은시간: {format_remaining(exit_dt)}")


def send_master_entry_summary(entries):
    if not entries:
        return False

    lines = ["🚗 입차 현황"]
    for index, entry in enumerate(sorted(entries, key=lambda item: item['entry_dt']), start=1):
        description = entry.get('description') or '-'
        lines.append(
            f"{index}. {entry['vehicle']} | "
            f"{entry['entry_dt'].strftime('%m-%d %H:%M')} | "
            f"{description}"
        )

    return send_message(MASTER_CHAT_ID, "\n".join(lines))


def send_master_recent_exit_summary(exits, minutes=60, since_dt=None):
    if not exits:
        return False

    if since_dt:
        lines = [f"🚙 출차 현황 ({since_dt.strftime('%m-%d %H:%M')} 이후)"]
    else:
        lines = [f"🚙 출차 현황 (최근 {minutes}분)"]
    for index, item in enumerate(sorted(exits, key=lambda item: item['exit_dt']), start=1):
        description = item.get('description') or '-'
        lines.append(
            f"{index}. {item['vehicle']} | "
            f"{item['exit_dt'].strftime('%m-%d %H:%M')} | "
            f"{description}"
        )

    return send_message(MASTER_CHAT_ID, "\n".join(lines))
