"""
SQLite DB에서 데이터를 읽어 docs/index.html 대시보드를 생성한다.
parking_vip.yml 워크플로우에서 봇 실행 후 호출됨.
"""
import sqlite3
import os
import datetime
import json

DB_PATH = "data/parking_stats.db"
OUT_PATH = "docs/index.html"


def read_data():
    if not os.path.exists(DB_PATH):
        return [], []

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        regs = conn.execute(
            "SELECT * FROM registrations ORDER BY timestamp DESC LIMIT 500"
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM alert_log ORDER BY id DESC LIMIT 100"
        ).fetchall()

    return [dict(r) for r in regs], [dict(a) for a in alerts]


def build_chart_data(registrations):
    # 일별 성공 등록 건수 (최근 30일)
    daily = {}
    top_vehicles = {}

    cutoff = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    for r in registrations:
        if r['status'] != 'success':
            continue
        date = r['timestamp'][:10]
        if date < cutoff:
            continue
        daily[date] = daily.get(date, 0) + 1
        top_vehicles[r['vehicle']] = top_vehicles.get(r['vehicle'], 0) + 1

    # 최근 30일 날짜 채우기
    dates = []
    today = datetime.date.today()
    for i in range(29, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        dates.append(d)
    counts = [daily.get(d, 0) for d in dates]

    # 상위 10개 차량
    sorted_vehicles = sorted(top_vehicles.items(), key=lambda x: x[1], reverse=True)[:10]
    vehicle_labels = [v[0] for v in sorted_vehicles]
    vehicle_counts = [v[1] for v in sorted_vehicles]

    return dates, counts, vehicle_labels, vehicle_counts


def alert_label(alert_type):
    labels = {
        'entry': '입차알림',
        'imminent': '출차임박',
        'master_entry': '마스터',
        'recent_exit': '최근출차',
    }
    return labels.get(alert_type, alert_type)


def generate_html(registrations, alerts):
    dates, counts, vehicle_labels, vehicle_counts = build_chart_data(registrations)

    recent_regs = registrations[:50]
    updated_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_success = sum(1 for r in registrations if r['status'] == 'success')

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="300">
  <title>U-Tower 주차 대시보드</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f0f2f5; color: #333;
    }}
    header {{
      background: #1a1a2e; color: #fff;
      padding: 20px 24px;
      display: flex; align-items: center; gap: 12px;
    }}
    header h1 {{ font-size: 1.4rem; }}
    header small {{ opacity: .65; font-size: .8rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px; padding: 20px;
    }}
    .card {{
      background: #fff; border-radius: 10px;
      padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,.08);
    }}
    .card h2 {{ font-size: 1rem; margin-bottom: 14px; color: #555; }}
    .stat-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }}
    .stat {{
      background: #f7f8fa; border-radius: 8px;
      padding: 12px 16px; flex: 1; min-width: 120px;
    }}
    .stat .val {{ font-size: 1.8rem; font-weight: 700; color: #1a1a2e; }}
    .stat .lbl {{ font-size: .75rem; color: #888; margin-top: 2px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
    th {{ text-align: left; padding: 8px 10px; background: #f0f2f5; color: #666; font-weight: 600; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #f0f2f5; }}
    tr:last-child td {{ border-bottom: none; }}
    .badge {{
      display: inline-block; padding: 2px 8px; border-radius: 10px;
      font-size: .72rem; font-weight: 600;
    }}
    .badge.success {{ background: #e6f4ea; color: #1e8449; }}
    .badge.skipped {{ background: #fef9e7; color: #b7950b; }}
    .badge.failed, .badge.limit_exceeded {{ background: #fdecea; color: #c0392b; }}
    .badge.entry {{ background: #e8f4fd; color: #1a6fa8; }}
    .badge.imminent {{ background: #fef5e7; color: #d68910; }}
    .badge.master_entry {{ background: #eef2ff; color: #3f51b5; }}
    .badge.recent_exit {{ background: #eaf7ef; color: #1f7a4d; }}
    .chart-wrap {{ position: relative; height: 220px; }}
    footer {{ text-align: center; padding: 20px; font-size: .78rem; color: #aaa; }}
  </style>
</head>
<body>
<header>
  <div>
    <h1>🏢 U-Tower 주차 할인 대시보드</h1>
    <small>마지막 업데이트: {updated_at} · 5분마다 자동 새로고침</small>
  </div>
</header>

<div class="grid">
  <!-- 요약 통계 -->
  <div class="card" style="grid-column: 1/-1">
    <h2>요약</h2>
    <div class="stat-row">
      <div class="stat">
        <div class="val">{len(registrations)}</div>
        <div class="lbl">총 처리 건수</div>
      </div>
      <div class="stat">
        <div class="val">{total_success}</div>
        <div class="lbl">등록 성공</div>
      </div>
      <div class="stat">
        <div class="val">{len(set(r['vehicle'] for r in registrations))}</div>
        <div class="lbl">처리 차량 수</div>
      </div>
      <div class="stat">
        <div class="val">{len(alerts)}</div>
        <div class="lbl">알람 발송 이력</div>
      </div>
    </div>
  </div>

  <!-- 일별 등록 건수 -->
  <div class="card">
    <h2>📈 일별 등록 성공 건수 (최근 30일)</h2>
    <div class="chart-wrap">
      <canvas id="dailyChart"></canvas>
    </div>
  </div>

  <!-- 상위 차량 -->
  <div class="card">
    <h2>🚗 차량별 등록 횟수 (상위 10)</h2>
    <div class="chart-wrap">
      <canvas id="vehicleChart"></canvas>
    </div>
  </div>

  <!-- 알람 이력 -->
  <div class="card">
    <h2>🔔 알람 발송 이력</h2>
    <table>
      <thead>
        <tr><th>날짜</th><th>차량번호</th><th>종류</th><th>입차</th><th>출차예정</th></tr>
      </thead>
      <tbody>
        {"".join(f'''<tr>
          <td>{a['alert_date']}</td>
          <td>{a['vehicle']}</td>
          <td><span class="badge {a['alert_type']}">{alert_label(a['alert_type'])}</span></td>
          <td>{a.get('entry_time', '') or '-'}</td>
          <td>{a.get('exit_time', '') or '-'}</td>
        </tr>''' for a in alerts[:20]) or '<tr><td colspan="5" style="text-align:center;color:#aaa">데이터 없음</td></tr>'}
      </tbody>
    </table>
  </div>

  <!-- 최근 등록 이력 -->
  <div class="card" style="grid-column: 1/-1">
    <h2>📋 최근 등록 이력</h2>
    <table>
      <thead>
        <tr><th>시각</th><th>차량번호</th><th>계정</th><th>코드</th><th>결과</th></tr>
      </thead>
      <tbody>
        {"".join(f'''<tr>
          <td>{r['timestamp'][:16]}</td>
          <td>{r['vehicle']}</td>
          <td>{r.get('user_id', '') or ''}</td>
          <td>{r.get('code', '') or ''}</td>
          <td><span class="badge {r['status']}">{r['status']}</span></td>
        </tr>''' for r in recent_regs) or '<tr><td colspan="5" style="text-align:center;color:#aaa">데이터 없음</td></tr>'}
      </tbody>
    </table>
  </div>
</div>

<footer>U-Tower Parking Bot · GitHub Pages</footer>

<script>
const dailyDates = {json.dumps(dates)};
const dailyCounts = {json.dumps(counts)};
const vehicleLabels = {json.dumps(vehicle_labels)};
const vehicleCounts = {json.dumps(vehicle_counts)};

new Chart(document.getElementById('dailyChart'), {{
  type: 'line',
  data: {{
    labels: dailyDates.map(d => d.slice(5)),
    datasets: [{{
      label: '등록 건수',
      data: dailyCounts,
      borderColor: '#4e79a7',
      backgroundColor: 'rgba(78,121,167,.12)',
      tension: 0.3,
      fill: true,
      pointRadius: 3
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
  }}
}});

new Chart(document.getElementById('vehicleChart'), {{
  type: 'bar',
  data: {{
    labels: vehicleLabels,
    datasets: [{{
      label: '등록 횟수',
      data: vehicleCounts,
      backgroundColor: '#4e79a7'
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
  }}
}});
</script>
</body>
</html>"""
    return html


def main():
    print("📊 Generating parking stats report...")
    os.makedirs("docs", exist_ok=True)

    registrations, alerts = read_data()
    html = generate_html(registrations, alerts)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ Report written to {OUT_PATH} "
          f"({len(registrations)} registrations, {len(alerts)} alerts)")


if __name__ == "__main__":
    main()
