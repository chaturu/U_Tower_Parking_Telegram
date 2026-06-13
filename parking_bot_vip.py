import requests
from bs4 import BeautifulSoup
import os
import sys
import datetime
import json
import re
import time
import threading
import csv

from telegram_notifier import (
    send_entry_alert,
    send_imminent_alert,
    send_master_entry_summary,
    send_master_recent_exit_summary,
)
from bot_options import load_options
from stats_db import (
    init_db,
    log_registration,
    is_alert_sent,
    is_alert_sent_today,
    mark_alert_sent,
    get_alert_state,
    set_alert_state,
)
from time_utils import get_exit_time, now_kst, remaining_minutes, today_kst

# 한글 출력 설정
sys.stdout.reconfigure(encoding='utf-8')

# --- 설정 ---
BASE_URL = 'http://211.169.233.235:8090'
LOGIN_URL = BASE_URL + '/account/login.asp'
SEARCH_URL = BASE_URL + '/discount/discount_regist.asp'
DISCOUNT_LIST_URL = BASE_URL + '/discount/discount_list.asp'

ACCOUNTS = [
    {'uid': os.environ.get('USER1_ID'), 'pw': os.environ.get('USER1_PW')},
    {'uid': os.environ.get('USER2_ID'), 'pw': os.environ.get('USER2_PW')},
    {'uid': os.environ.get('USER3_ID'), 'pw': os.environ.get('USER3_PW')},
    {'uid': os.environ.get('USER4_ID'), 'pw': os.environ.get('USER4_PW')}
]

# 입차시각 전체 datetime 패턴 (YYYY-MM-DD HH:MM)
DT_RE = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)')
CAR_RE = re.compile(r'\d{2,3}[가-힣]\d{4}')
EXIT_HEADER_RE = re.compile(r'출차|출문|출고')


def parse_datetime(value):
    m = DT_RE.search(value)
    if not m:
        return None
    value = m.group(1).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def find_exit_time(cells, headers):
    for index, text in enumerate(cells):
        header = headers[index] if index < len(headers) else ''
        if EXIT_HEADER_RE.search(header):
            parsed = parse_datetime(text)
            if parsed:
                return parsed

    if not headers and len(cells) >= 4:
        return parse_datetime(cells[3])

    return None


def is_discount_list_header(cells):
    row_text = ' '.join(cells)
    return '차량번호' in row_text and EXIT_HEADER_RE.search(row_text)


def get_recent_exit_since(now, lookback_minutes):
    return now - datetime.timedelta(minutes=lookback_minutes)


def fetch_recent_exits(account, lookback_minutes=60, since_dt=None, now=None):
    uid = account.get('uid')
    pw = account.get('pw')
    if not uid or not pw:
        print("⚠️ USER1 credentials missing. Skip recent exit check.")
        return []

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    session.post(LOGIN_URL, data={'user_id': uid, 'password': pw})

    now = now or now_kst()
    since = since_dt or (now - datetime.timedelta(minutes=lookback_minutes))
    from_day = since.strftime('%Y-%m-%d')
    to_day = now.strftime('%Y-%m-%d')
    res = session.get(
        DISCOUNT_LIST_URL,
        params={
            'page': 1,
            'from_to_date': f'{from_day} ~ {to_day}',
            'dftd': f'{from_day} ~ {to_day}',
            'puser': 'all',
            'license_plate_number': '',
            'tcarno': ''
        },
        timeout=20
    )
    html = res.content.decode('euc-kr', 'replace')
    soup = BeautifulSoup(html, 'html.parser')

    recent_exits = []
    seen = set()

    for table in soup.find_all('table'):
        headers = []
        for row in table.find_all('tr'):
            cells = [cell.get_text(' ', strip=True) for cell in row.find_all(['th', 'td'])]
            if not cells:
                continue
            if row.find('th') or is_discount_list_header(cells):
                headers = cells
                continue

            row_text = ' '.join(cells)
            vehicle_match = CAR_RE.search(row_text)
            if not vehicle_match:
                continue

            vehicle = vehicle_match.group(0)
            exit_dt = find_exit_time(cells, headers)
            if not exit_dt:
                continue
            if not (since <= exit_dt <= now):
                continue

            key = (vehicle, exit_dt.strftime('%Y-%m-%d %H:%M'))
            if key in seen:
                continue
            seen.add(key)
            recent_exits.append({'vehicle': vehicle, 'exit_dt': exit_dt})

    return recent_exits


def parse_alert_flag(value, default=True):
    if value is None:
        return default
    value = str(value).strip().lower()
    if value in ('1', 'true', 'yes', 'y', 'on', '켜기'):
        return True
    if value in ('0', 'false', 'no', 'n', 'off', '끄기'):
        return False
    return default


def make_car(full_no, chat_id=None, entry_alert=True,
             imminent_alert=True, description='', source='일반', register=True):
    return {
        'full_no': full_no,
        'suffix': full_no[-4:],
        'chat_id': chat_id or None,
        'entry_alert': entry_alert,
        'imminent_alert': imminent_alert,
        'description': description,
        'source': source,
        'register': register,
    }


def read_vehicle_csv(file_path, default_source='일반', default_description='일반', register=True):
    """
    CSV format:
    차량번호,텔레그램채팅ID,입차알림,출차임박알림,설명

    입차알림/출차임박알림은 1=켜기, 0=끄기입니다.
    Legacy "차량번호 | chat_id # 설명" rows are also accepted.
    """
    cars = []
    if not os.path.exists(file_path):
        return cars
    with open(file_path, 'r', encoding='utf-8', newline='') as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith('#'):
                continue

            if '|' in stripped and ',' not in stripped:
                body, _, description = stripped.partition('#')
                parts = [part.strip() for part in body.split('|')]
                full_no = parts[0] if parts else ''
                chat_id = parts[1] if len(parts) > 1 else None
                if full_no:
                    cars.append(make_car(
                        full_no,
                        chat_id=chat_id,
                        description=description.strip() or default_description,
                        source=default_source,
                        register=register,
                    ))
                continue

            row = next(csv.reader([stripped]))
            row = [cell.strip() for cell in row]
            if not row or not row[0] or row[0] == '차량번호':
                continue
            cars.append(make_car(
                row[0],
                chat_id=row[1] if len(row) > 1 else None,
                entry_alert=parse_alert_flag(row[2] if len(row) > 2 else None, True),
                imminent_alert=parse_alert_flag(row[3] if len(row) > 3 else None, True),
                description=row[4] if len(row) > 4 and row[4] else default_description,
                source=default_source,
                register=register,
            ))
    return cars


def read_car_numbers(file_path):
    return [car['full_no'] for car in read_vehicle_csv(file_path)]


def build_car_list():
    """VIP(할인등록+개인알림)와 일반(입차 확인만) 차량 목록을 병합합니다.

    목록 파일은 GitHub Action이 실행 시 Cloudflare Worker(/lists)에서 받아 씁니다.
    """
    blacklist = set(read_car_numbers('blacklist.txt'))
    merged = []
    seen = set()

    for car in read_vehicle_csv('vip_list.txt', default_source='VIP',
                                default_description='VIP', register=True):
        full_no = car['full_no']
        if full_no in blacklist or full_no in seen:
            continue
        merged.append(car)
        seen.add(full_no)

    for car in read_vehicle_csv('cars.txt', register=False):
        full_no = car['full_no']
        if full_no in blacklist or full_no in seen:
            continue
        merged.append(car)
        seen.add(full_no)

    return merged


STATUS_PATH = 'data/status.json'


def write_status_file(cars, entry_times, fully_registered, now=None):
    """현재 입차 중인 차량 현황을 status.json으로 저장합니다.

    Cloudflare Worker가 텔레그램 '/상태' 조회 시 이 파일을 읽어 즉시 응답합니다.
    """
    now = now or now_kst()
    items = []
    for car in cars:
        vehicle = car['full_no']
        entry_dt = entry_times.get(vehicle) or fully_registered.get(vehicle)
        if not entry_dt:
            continue
        exit_dt = get_exit_time(entry_dt)
        items.append({
            'vehicle': vehicle,
            'chat_id': car.get('chat_id') or '',
            'entry': entry_dt.strftime('%Y-%m-%d %H:%M'),
            'exit': exit_dt.strftime('%Y-%m-%d %H:%M'),
            'description': car.get('description', ''),
        })

    items.sort(key=lambda item: item['entry'])
    os.makedirs('data', exist_ok=True)
    with open(STATUS_PATH, 'w', encoding='utf-8') as f:
        json.dump(
            {'generated_at': now.strftime('%Y-%m-%d %H:%M'), 'cars': items},
            f,
            ensure_ascii=False,
            indent=2
        )
    print(f"📝 Status file written: {STATUS_PATH} ({len(items)} car(s))")


def is_duplicate_sent(prevent_duplicates, vehicle, alert_type, entry_time=None, exit_time=None):
    return prevent_duplicates and is_alert_sent_today(
        vehicle,
        alert_type,
        entry_time=entry_time,
        exit_time=exit_time
    )


def process_account(account, cars, results_lock, registration_results, entry_times, imminent_alerted, fully_registered, options):
    uid = account['uid']
    pw = account['pw']

    if not uid:
        return

    if uid.upper().startswith('A'):
        code = '13'
        type_name = "Knowledge Center"
    elif uid.upper().startswith('B'):
        code = '11'
        type_name = "Officetel"
    elif uid.upper().startswith('C'):
        code = '12'
        type_name = "Retail"
    else:
        code = '11'
        type_name = "Default"

    print(f"👤 [{uid}] Thread Started. Type: {type_name} (Code {code})")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })

    try:
        session.post(LOGIN_URL, data={'user_id': uid, 'password': pw})

        res_search_page = session.get(SEARCH_URL)
        html_search_page = res_search_page.content.decode('euc-kr', 'replace')
        soup_search_page = BeautifulSoup(html_search_page, 'html.parser')
        cust_cd_inp = soup_search_page.find('input', {'id': 'cust_cd'})
        cust_cd = cust_cd_inp['value'] if cust_cd_inp else ''

        if not cust_cd:
            print(f"❌ [{uid}] Failed to get cust_cd after login. Aborting thread.")
            return

        for car in cars:
            full_no = car['full_no']
            suffix = car['suffix']
            chat_id = car.get('chat_id')

            try:
                # --- 검색 ---
                res = session.post(SEARCH_URL, data={'license_plate_number': suffix, 'search_type': '0'})
                try:
                    html = res.content.decode('euc-kr', 'replace')
                except Exception:
                    html = res.text

                soup = BeautifulSoup(html, 'html.parser')
                target_card_no = None
                checkboxes = soup.find_all('input', {'name': 'chk'})

                for chk in checkboxes:
                    parent_td = chk.find_parent('td')
                    if parent_td:
                        next_td = parent_td.find_next_sibling('td')
                        if next_td:
                            web_text = next_td.get_text().replace(' ', '').strip()
                            target_text = full_no.replace(' ', '').strip()
                            if target_text in web_text:
                                target_card_no = chk['value']

                                # 같은 행(tr)에서 입차시각 추출 (YYYY-MM-DD HH:MM[:SS])
                                row = chk.find_parent('tr')
                                if row:
                                    for cell in row.find_all('td'):
                                        entry_dt = parse_datetime(cell.get_text())
                                        if entry_dt:
                                            with results_lock:
                                                if full_no not in entry_times:
                                                    entry_times[full_no] = entry_dt
                                                    print(f"  📍 [{uid}] {full_no}: 입차시각 {entry_dt.strftime('%Y-%m-%d %H:%M')}")
                                            break
                                break

                if not target_card_no:
                    print(f"  ⚠️ [{uid}] {full_no}: Car not found in search results. Skip.")
                    continue

                # --- [알람 2: 출차 임박/경과 체크] ---
                # 남은시간이 출차임박알림분 이하면 임박(imminent), 출차시간이 지나면 경과(overdue)로
                # 같은 입차시각 기준 각각 1회씩 알림을 보냅니다.
                entry_dt = entry_times.get(full_no)
                if (
                    entry_dt and chat_id and
                    options.personal_alerts and
                    options.imminent_alerts and
                    car.get('imminent_alert', True)
                ):
                    exit_dt = get_exit_time(entry_dt)
                    remaining_min = remaining_minutes(exit_dt)
                    if remaining_min <= options.imminent_alert_minutes:
                        alert_type = 'imminent' if remaining_min > 0 else 'overdue'
                        alert_key = (full_no, alert_type)
                        should_send = False
                        with results_lock:
                            if not options.personal_prevent_duplicates or (
                                alert_key not in imminent_alerted
                                and not is_duplicate_sent(
                                    options.personal_prevent_duplicates,
                                    full_no,
                                    alert_type,
                                    entry_time=entry_dt
                                )
                            ):
                                imminent_alerted.add(alert_key)
                                should_send = True
                        if should_send:
                            if send_imminent_alert(chat_id, full_no, entry_dt, exit_dt):
                                mark_alert_sent(full_no, alert_type, entry_dt, exit_dt)
                                if remaining_min > 0:
                                    print(f"  ⏰ [{uid}] {full_no}: 출차 임박 알람 전송 ({remaining_min}분 남음)")
                                else:
                                    print(f"  ⏰ [{uid}] {full_no}: 출차시간 경과 알람 전송 ({-remaining_min}분 경과)")

                # 일반 차량은 입차 상태 확인까지만 하고 할인 등록은 하지 않습니다.
                if not car.get('register', True):
                    if entry_times.get(full_no):
                        print(f"  👀 [{uid}] {full_no}: 일반 차량 — 입차 확인만, 할인등록 생략.")
                    continue

                # --- 할인 코드 중복 체크 ---
                current_applied_codes = []
                target_card_hidden_input = soup.find('input', {'type': 'hidden', 'id': target_card_no})
                if target_card_hidden_input and 'value' in target_card_hidden_input.attrs:
                    value_parts = target_card_hidden_input['value'].split('|')
                    if len(value_parts) > 1:
                        current_applied_codes = value_parts[1:]

                # 히든 인풋에 코드 4개 이상 + 오늘 입차 = 완전 등록 완료로 판단
                entry_dt = entry_times.get(full_no)
                if len(current_applied_codes) >= 4 and entry_dt \
                        and entry_dt.date() == today_kst():
                    with results_lock:
                        fully_registered[full_no] = entry_dt

                if code in current_applied_codes:
                    print(f"  ⏩ [{uid}] {full_no}: Code {code} already present. Skip.")
                    log_registration(full_no, code, uid, 'skipped')
                    continue

                # --- 등록 ---
                payload = {
                    'license_plate_number': suffix,
                    'request_type_value': 'INSERTDISCOUNT',
                    'post_discount_value': code,
                    'chk': target_card_no,
                    'cust_cd': cust_cd,
                    f'memo_{target_card_no}': ''
                }

                res_apply = session.post(SEARCH_URL, data=payload)
                html_apply = res_apply.content.decode('euc-kr', 'replace')

                if "정상적으로 처리되었습니다" in html_apply or "등록되었습니다" in html_apply:
                    print(f"  ✅ [{uid}] {full_no}: Request Sent ({code}) - SUCCESS!")
                    log_registration(full_no, code, uid, 'success')
                    with results_lock:
                        if full_no not in registration_results:
                            registration_results[full_no] = set()
                        registration_results[full_no].add(uid)
                elif "한도초과" in html_apply or "등록 가능한 매수가 없습니다" in html_apply:
                    print(f"  ⚠️ [{uid}] {full_no}: Request Sent ({code}) - FAILED (Limit Exceeded)!")
                    log_registration(full_no, code, uid, 'limit_exceeded')
                else:
                    print(f"  ❌ [{uid}] {full_no}: Request Sent ({code}) - FAILED (Unknown Reason).")
                    log_registration(full_no, code, uid, 'failed')

                time.sleep(0.05)

            except Exception as e:
                print(f"  💥 [{uid}] Error processing {full_no}: {e}")

    except Exception as e:
        print(f"  💥 [{uid}] Login/Session Error: {e}")

    print(f"🏁 [{uid}] Thread Finished.")


def run_parallel_process():
    run_time = now_kst()
    print(f"🕒 Parallel Batch Job Started: {run_time}")

    init_db()
    options = load_options()
    print(
        "⚙️ Options: "
        f"개인중복방지={'켜기' if options.personal_prevent_duplicates else '끄기'}, "
        f"마스터중복방지={'켜기' if options.master_prevent_duplicates else '끄기'}, "
        f"어드민알림={'켜기' if options.admin_alerts else '끄기'}, "
        f"개인알림={'켜기' if options.personal_alerts else '끄기'}, "
        f"입차알림={'켜기' if options.entry_alerts else '끄기'}, "
        f"출차임박알림={'켜기' if options.imminent_alerts else '끄기'}, "
        f"출차임박알림분={options.imminent_alert_minutes}, "
        f"최근출차조회분={options.recent_exit_lookback_minutes}"
    )

    vip_cars = build_car_list()
    if not vip_cars:
        print("❌ No cars to process! Aborting.")
        return

    register_cars = [car for car in vip_cars if car.get('register', True)]
    print(
        f"🚗 Vehicle list: {len(vip_cars)} car(s) "
        f"(VIP 등록 {len(register_cars)}대, 일반 확인 {len(vip_cars) - len(register_cars)}대)."
    )

    # 스레드 공유 상태
    results_lock = threading.Lock()
    registration_results = {}  # {vehicle: set(uid, ...)} — 이번 실행 성공한 uid
    entry_times = {}           # {vehicle: entry_datetime} — 검색 시 추출
    imminent_alerted = set()   # 이번 실행에서 알람 2 발송한 차량
    fully_registered = {}      # {vehicle: entry_datetime} — 이미 4개 코드 완전 등록된 차량

    # 일반 차량은 입차 확인만 하면 되므로 첫 계정 스레드만 검색하고,
    # 나머지 스레드는 할인등록 대상(VIP)만 처리합니다.
    threads = []
    first_account = True
    for acc in ACCOUNTS:
        if acc['uid']:
            target_cars = vip_cars if first_account else register_cars
            first_account = False
            t = threading.Thread(
                target=process_account,
                args=(acc, target_cars, results_lock,
                      registration_results, entry_times, imminent_alerted, fully_registered, options)
            )
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    # --- [상태 파일: 텔레그램 /상태 조회용] ---
    try:
        write_status_file(vip_cars, entry_times, fully_registered)
    except Exception as e:
        print(f"  💥 Status file write failed: {e}")

    # --- [알람 1: VIP 차량 개인 입차 알림] ---
    print("\n🔔 Sending VIP entry alerts...")
    if options.personal_alerts and options.entry_alerts:
        for car in vip_cars:
            vehicle = car['full_no']
            chat_id = car.get('chat_id')
            entry_dt = entry_times.get(vehicle) or fully_registered.get(vehicle)

            if not entry_dt:
                continue
            if not car.get('entry_alert', True):
                print(f"  ⏩ [{vehicle}] 차량별 입차 알림 꺼짐. Skip.")
                continue
            if not chat_id:
                print(f"  ⚠️ [{vehicle}] 텔레그램 채팅 ID 없음 → 개인 알림 스킵")
                continue
            if is_duplicate_sent(
                options.personal_prevent_duplicates,
                vehicle,
                'entry',
                entry_time=entry_dt
            ):
                print(f"  ⏩ [{vehicle}] 개인 입차 알림 오늘 이미 발송됨. Skip.")
                continue

            exit_dt = get_exit_time(entry_dt)
            if send_entry_alert(chat_id, vehicle, entry_dt, exit_dt):
                mark_alert_sent(vehicle, 'entry', entry_dt, exit_dt)
                print(f"  🚗 [{vehicle}] 개인 입차 알림 전송 "
                      f"(입차: {entry_dt.strftime('%H:%M')}, "
                      f"출차예정: {exit_dt.strftime('%H:%M')})")
    else:
        print("  ⏸ 개인 알림 옵션 꺼짐. Skip.")

    # --- [마스터 알림: 입차한 모든 VIP 차량 요약] ---
    print("\n🔔 Sending master entry summary...")
    master_entries = []
    if options.admin_alerts:
        for car in vip_cars:
            vehicle = car['full_no']
            entry_dt = entry_times.get(vehicle) or fully_registered.get(vehicle)
            if not entry_dt:
                continue
            master_entries.append({
                'vehicle': vehicle,
                'entry_dt': entry_dt,
                'exit_dt': get_exit_time(entry_dt),
                'description': car.get('description', '')
            })

        master_entries.sort(key=lambda item: item['entry_dt'])

        if master_entries:
            snapshot = "\n".join(entry['vehicle'] for entry in master_entries)
            should_send_master_entry = True
            if options.master_prevent_duplicates:
                last_snapshot = get_alert_state('master_entry_vehicle_snapshot', '')
                should_send_master_entry = snapshot != last_snapshot

            if not should_send_master_entry:
                print("  ℹ️ Master entry summary unchanged. Skip.")
            elif send_master_entry_summary(master_entries, run_time=run_time):
                for entry in master_entries:
                    mark_alert_sent(
                        entry['vehicle'],
                        'master_entry',
                        entry['entry_dt'],
                        entry['exit_dt']
                    )
                set_alert_state('master_entry_vehicle_snapshot', snapshot)
                print(f"  ✅ 마스터 입차 요약 알림 전송 ({len(master_entries)}대)")
        else:
            if options.master_prevent_duplicates:
                set_alert_state('master_entry_vehicle_snapshot', '')
            print("  ⏩ 마스터에게 새로 보낼 입차 차량이 없습니다.")
    else:
        print("  ⏸ 어드민 알림 옵션 꺼짐. Skip.")

    # --- [마스터 알림: 최근 출차 차량] ---
    print("\n🔔 Checking recent exits with USER1...")
    if options.admin_alerts:
        try:
            exit_check_finished_at = now_kst()
            recent_exit_since = get_recent_exit_since(
                exit_check_finished_at,
                options.recent_exit_lookback_minutes
            )
            vip_descriptions = {
                car['full_no']: car.get('description', '')
                for car in vip_cars
            }
            recent_exits = sorted([
                {
                    **item,
                    'description': vip_descriptions.get(item['vehicle'], '')
                }
                for item in fetch_recent_exits(
                    ACCOUNTS[0],
                    options.recent_exit_lookback_minutes,
                    since_dt=recent_exit_since,
                    now=exit_check_finished_at
                )
                if not is_alert_sent(
                    item['vehicle'],
                    'recent_exit',
                    exit_time=item['exit_dt']
                )
            ], key=lambda item: item['exit_dt'])
            if recent_exits:
                if send_master_recent_exit_summary(
                    recent_exits,
                    options.recent_exit_lookback_minutes
                ):
                    for item in recent_exits:
                        mark_alert_sent(
                            item['vehicle'],
                            'recent_exit',
                            exit_time=item['exit_dt']
                        )
                    print(
                        f"  ✅ 최근 {options.recent_exit_lookback_minutes}분 출차 알림 전송 "
                        f"({recent_exit_since.strftime('%Y-%m-%d %H:%M')} 이후, {len(recent_exits)}대)"
                    )
            else:
                print(
                    f"  ⏩ 최근 {options.recent_exit_lookback_minutes}분 내 새 출차 차량이 없습니다. "
                    f"({recent_exit_since.strftime('%Y-%m-%d %H:%M')} 이후)"
                )
        except Exception as e:
            print(f"  💥 Recent exit check failed: {e}")
    else:
        print("  ⏸ 어드민 알림 옵션 꺼짐. Skip.")

    print("\n✅ All threads completed.")


if __name__ == "__main__":
    run_parallel_process()
