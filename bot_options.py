from dataclasses import dataclass


OPTIONS_FILE = "options.txt"


@dataclass(frozen=True)
class BotOptions:
    personal_prevent_duplicates: bool = True
    master_prevent_duplicates: bool = True
    admin_alerts: bool = True
    personal_alerts: bool = True
    entry_alerts: bool = True
    imminent_alerts: bool = True
    imminent_alert_minutes: int = 75
    recent_exit_lookback_minutes: int = 600


TRUE_VALUES = {"1", "true", "yes", "y", "on", "켜기", "사용", "방지"}
FALSE_VALUES = {"0", "false", "no", "n", "off", "끄기", "미사용", "가능"}


KEY_ALIASES = {
    "중복방지": "legacy_prevent_duplicates",
    "duplicate_prevention": "legacy_prevent_duplicates",
    "prevent_duplicates": "legacy_prevent_duplicates",
    "개인중복방지": "personal_prevent_duplicates",
    "personal_prevent_duplicates": "personal_prevent_duplicates",
    "마스터중복방지": "master_prevent_duplicates",
    "어드민중복방지": "master_prevent_duplicates",
    "master_prevent_duplicates": "master_prevent_duplicates",
    "어드민알림": "admin_alerts",
    "관리자알림": "admin_alerts",
    "마스터알림": "admin_alerts",
    "admin_alerts": "admin_alerts",
    "개인알림": "personal_alerts",
    "개인메세지": "personal_alerts",
    "개인메시지": "personal_alerts",
    "personal_alerts": "personal_alerts",
    "입차알림": "entry_alerts",
    "개인입차알림": "entry_alerts",
    "entry_alerts": "entry_alerts",
    "출차임박알림": "imminent_alerts",
    "개인출차임박알림": "imminent_alerts",
    "imminent_alerts": "imminent_alerts",
    "출차임박알림분": "imminent_alert_minutes",
    "출차임박분": "imminent_alert_minutes",
    "imminent_alert_minutes": "imminent_alert_minutes",
    "최근출차조회분": "recent_exit_lookback_minutes",
    "recent_exit_lookback_minutes": "recent_exit_lookback_minutes",
    "최근출차조회시간": "recent_exit_lookback_hours",
    "recent_exit_lookback_hours": "recent_exit_lookback_hours",
}


def parse_bool(value, default):
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def load_options(path=OPTIONS_FILE):
    options = BotOptions()
    values = {
        "personal_prevent_duplicates": options.personal_prevent_duplicates,
        "master_prevent_duplicates": options.master_prevent_duplicates,
        "admin_alerts": options.admin_alerts,
        "personal_alerts": options.personal_alerts,
        "entry_alerts": options.entry_alerts,
        "imminent_alerts": options.imminent_alerts,
        "imminent_alert_minutes": options.imminent_alert_minutes,
        "recent_exit_lookback_minutes": options.recent_exit_lookback_minutes,
    }

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return options

    for line in lines:
        body = line.split("#", 1)[0].strip()
        if not body or "=" not in body:
            continue
        key, value = [part.strip() for part in body.split("=", 1)]
        canonical = KEY_ALIASES.get(key)
        if not canonical:
            continue

        if canonical == "recent_exit_lookback_hours":
            try:
                values["recent_exit_lookback_minutes"] = max(1, int(value) * 60)
            except ValueError:
                pass
        elif canonical in ("imminent_alert_minutes", "recent_exit_lookback_minutes"):
            try:
                values[canonical] = max(1, int(value))
            except ValueError:
                pass
        elif canonical == "legacy_prevent_duplicates":
            parsed = parse_bool(value, values["personal_prevent_duplicates"])
            values["personal_prevent_duplicates"] = parsed
            values["master_prevent_duplicates"] = parsed
        else:
            values[canonical] = parse_bool(value, values[canonical])

    if not values["personal_alerts"]:
        values["entry_alerts"] = False
        values["imminent_alerts"] = False

    return BotOptions(**values)
