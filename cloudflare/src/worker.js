// U-Tower Parking Telegram Bot — Cloudflare Worker
//
// 차량 목록(vip_list, car_list, blacklist)과 status는 GitHub이 아닌 KV에 저장합니다.
//
// 역할 1 (fetch):    텔레그램 웹훅 수신 → 명령 처리
//   /상태 [번호]      - 입차 현황 조회 (KV status 기반, 누구나)
//   /추가 ...         - VIP 차량 등록: 할인등록 + 개인알림 (마스터만)
//   /삭제 차량번호     - VIP 차량 제거 (마스터만)
//   /일반추가 ...      - 일반 차량 등록: 입차 확인만, 마스터 알림 (마스터만)
//   /일반삭제 차량번호 - 일반 차량 제거 (마스터만)
//   /목록             - VIP/일반 차량 목록 (마스터만)
//   /실행             - GitHub Action 즉시 실행 (마스터만)
// 역할 2 (fetch):    GitHub Action 연동 API (Bearer LIST_TOKEN 인증)
//   GET  /lists       - {vip_list, car_list, blacklist} 반환
//   POST /status      - 실행 결과 status JSON 저장
// 역할 3 (scheduled): KST 08~20시 매 정각 GitHub Action 트리거 (repository_dispatch)

const KEY_VIP = 'vip_list';
const KEY_CARS = 'car_list';
const KEY_BLACKLIST = 'blacklist';
const KEY_STATUS = 'status';
const CSV_HEADER = '차량번호,텔레그램_채팅ID,입차알림,출차임박알림,설명';
const DISPATCH_EVENT = 'run-parking-vip';
const CAR_NO_RE = /^\d{2,3}[가-힣]\d{4}$/u;
const KST_OFFSET_MS = 9 * 3600 * 1000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/lists' || url.pathname === '/status') {
      return handleSyncApi(request, env, url.pathname);
    }

    if (request.method !== 'POST') {
      return new Response('U-Tower Parking Telegram Bot', { status: 200 });
    }
    const secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token');
    if (secret !== env.WEBHOOK_SECRET) {
      return new Response('forbidden', { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response('ok');
    }

    const message = update.message || update.edited_message;
    const chatId = message?.chat?.id;
    const text = (message?.text || '').trim();
    console.log(
      `update keys=[${Object.keys(update).join(',')}] chat=${chatId ?? 'none'} ` +
      `type=${message?.chat?.type ?? '?'} text=${text ? JSON.stringify(text.slice(0, 40)) : '(empty)'}`,
    );
    if (!chatId || !text) {
      return new Response('ok');
    }

    try {
      await handleCommand(env, chatId, text);
    } catch (err) {
      await sendMessage(env, chatId, `⚠️ 처리 중 오류가 발생했습니다.\n${err.message}`);
    }
    // 텔레그램 재전송 폭주를 막기 위해 항상 200을 반환합니다.
    return new Response('ok');
  },

  async scheduled(_event, env) {
    await triggerDispatch(env);
  },
};

// --- GitHub Action 연동 API ---

async function handleSyncApi(request, env, pathname) {
  const auth = request.headers.get('Authorization') || '';
  if (auth !== `Bearer ${env.LIST_TOKEN}`) {
    return new Response('forbidden', { status: 403 });
  }

  if (pathname === '/lists' && request.method === 'GET') {
    const [vip, cars, blacklist] = await Promise.all([
      env.KV.get(KEY_VIP),
      env.KV.get(KEY_CARS),
      env.KV.get(KEY_BLACKLIST),
    ]);
    return Response.json({
      vip_list: vip || '',
      car_list: cars || '',
      blacklist: blacklist || '',
    });
  }

  if (pathname === '/status' && request.method === 'POST') {
    const body = await request.text();
    try {
      JSON.parse(body);
    } catch {
      return new Response('invalid json', { status: 400 });
    }
    await env.KV.put(KEY_STATUS, body);
    return new Response('ok');
  }

  return new Response('method not allowed', { status: 405 });
}

// --- 텔레그램 명령 ---

async function handleCommand(env, chatId, text) {
  const isMaster = String(chatId) === String(env.MASTER_CHAT_ID);
  const [cmd, ...args] = text.split(/\s+/);
  const command = cmd.replace(/@\S+$/, ''); // 그룹에서 /상태@봇이름 형식 지원

  switch (command) {
    case '/start':
    case '/help':
    case '/도움말':
      return sendMessage(env, chatId, helpText(isMaster));

    case '/상태':
    case '상태':
      return replyStatus(env, chatId, isMaster, args[0]);

    case '/추가':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 추가는 마스터만 가능합니다.');
      return addVipCar(env, chatId, args);

    case '/삭제':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 삭제는 마스터만 가능합니다.');
      return removeCar(env, chatId, args[0], KEY_VIP, 'VIP');

    case '/일반추가':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 추가는 마스터만 가능합니다.');
      return addNormalCar(env, chatId, args);

    case '/일반삭제':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 삭제는 마스터만 가능합니다.');
      return removeCar(env, chatId, args[0], KEY_CARS, '일반');

    case '/목록':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 목록은 마스터만 조회할 수 있습니다.');
      return listCars(env, chatId);

    case '/실행':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 즉시 실행은 마스터만 가능합니다.');
      await triggerDispatch(env);
      return sendMessage(env, chatId, '▶️ 주차 등록 봇 실행을 요청했습니다. 1~2분 내 결과 알림이 옵니다.');

    default:
      if (text.includes('상태')) {
        return replyStatus(env, chatId, isMaster, undefined);
      }
      return; // 알 수 없는 메시지는 무시 (그룹 잡담에 반응하지 않음)
  }
}

function helpText(isMaster) {
  const lines = [
    '🅿️ U-Tower 주차봇 명령',
    '/상태 - 내 차량 입차/남은시간 조회',
    '/상태 1234 - 끝 4자리로 특정 차량 조회',
  ];
  if (isMaster) {
    lines.push(
      '/추가 123가1234 [채팅ID] [설명] - VIP 차량 (할인등록+개인알림)',
      '/삭제 123가1234 - VIP 차량 제거',
      '/일반추가 123가1234 [설명] - 일반 차량 (입차 확인만, 마스터 알림)',
      '/일반삭제 123가1234 - 일반 차량 제거',
      '/목록 - 등록 차량 목록',
      '/실행 - 봇 즉시 실행',
    );
  }
  lines.push('', '입차 확인과 할인 등록은 08~20시 매 정각에 실행됩니다.');
  return lines.join('\n');
}

// --- /상태 ---

async function replyStatus(env, chatId, isMaster, filter) {
  const raw = await env.KV.get(KEY_STATUS);
  if (!raw) {
    return sendMessage(env, chatId, 'ℹ️ 아직 상태 정보가 없습니다. 봇이 한 번 실행된 뒤 조회할 수 있습니다.');
  }
  const status = JSON.parse(raw);

  let cars = status.cars || [];
  if (!isMaster) {
    cars = cars.filter((car) => String(car.chat_id) === String(chatId));
  }
  if (filter) {
    cars = cars.filter((car) => car.vehicle.endsWith(filter) || car.vehicle === filter);
  }

  const footer = `\n(기준: ${status.generated_at} 실행, 이후 입차는 다음 정각에 반영)`;
  if (!cars.length) {
    return sendMessage(env, chatId, `ℹ️ 현재 입차 확인된 차량이 없습니다.${footer}`);
  }

  const lines = ['🅿️ 주차 현황'];
  cars.forEach((car, index) => {
    const desc = car.description ? ` (${car.description})` : '';
    lines.push(
      `${index + 1}. ${car.vehicle}${desc}`,
      `   입차 ${car.entry.slice(11)} → 출차 ${car.exit.slice(11)} | ${formatRemaining(car.exit)}`,
    );
  });
  return sendMessage(env, chatId, lines.join('\n') + footer);
}

function parseKst(text) {
  // "YYYY-MM-DD HH:MM" (KST) → epoch ms
  const m = text.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (!m) return null;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - KST_OFFSET_MS;
}

function formatRemaining(exitText) {
  const exitMs = parseKst(exitText);
  if (exitMs === null) return '';
  const minutes = Math.ceil((exitMs - Date.now()) / 60000);
  const abs = Math.abs(minutes);
  const hm = abs >= 60 ? `${Math.floor(abs / 60)}시간 ${abs % 60}분` : `${abs}분`;
  return minutes <= 0 ? `⛔ 출차시간 경과 ${hm}` : `남은시간 ${hm}`;
}

// --- /추가, /일반추가, /삭제, /일반삭제, /목록 ---

async function addVipCar(env, chatId, args) {
  const [carNo, ...rest] = args;
  if (!carNo || !CAR_NO_RE.test(carNo)) {
    return sendMessage(env, chatId, '사용법: /추가 123가1234 [채팅ID] [설명]\n차량번호 형식이 올바르지 않습니다.');
  }

  let targetChatId = String(chatId);
  let description = rest.join(' ');
  if (rest.length && /^-?\d+$/.test(rest[0])) {
    targetChatId = rest[0];
    description = rest.slice(1).join(' ');
  }

  const vip = await getListText(env, KEY_VIP);
  if (activeCarNumbers(vip).includes(carNo)) {
    return sendMessage(env, chatId, `ℹ️ ${carNo}는 이미 VIP로 등록되어 있습니다.`);
  }

  await env.KV.put(KEY_VIP, appendLine(vip, `${carNo},${targetChatId},1,1,${description}`));

  const notes = [];
  // 일반 목록에 있던 차량이면 VIP로 승격되므로 일반에서 빼준다.
  if (await removeFromList(env, KEY_CARS, carNo)) {
    notes.push('⚠️ 일반 목록에 있던 차량이라 일반에서 제거했습니다.');
  }
  if (await removeFromBlacklist(env, carNo)) {
    notes.push('⚠️ 블랙리스트에 있던 차량이라 블랙리스트에서도 제거했습니다.');
  }

  return sendMessage(
    env,
    chatId,
    `✅ VIP 차량을 등록했습니다.\n차량번호: ${carNo}\n알림 채팅ID: ${targetChatId}\n설명: ${description || '-'}` +
      (notes.length ? '\n' + notes.join('\n') : '') +
      '\n다음 정각 실행부터 할인 등록됩니다. 바로 적용하려면 /실행',
  );
}

async function addNormalCar(env, chatId, args) {
  const [carNo, ...rest] = args;
  if (!carNo || !CAR_NO_RE.test(carNo)) {
    return sendMessage(env, chatId, '사용법: /일반추가 123가1234 [설명]\n차량번호 형식이 올바르지 않습니다.');
  }
  const description = rest.join(' ') || '일반';

  const vip = await getListText(env, KEY_VIP);
  if (activeCarNumbers(vip).includes(carNo)) {
    return sendMessage(env, chatId, `ℹ️ ${carNo}는 이미 VIP로 등록되어 있습니다. 일반으로 바꾸려면 /삭제 후 /일반추가 하세요.`);
  }

  const cars = await getListText(env, KEY_CARS);
  if (activeCarNumbers(cars).includes(carNo)) {
    return sendMessage(env, chatId, `ℹ️ ${carNo}는 이미 일반으로 등록되어 있습니다.`);
  }

  await env.KV.put(KEY_CARS, appendLine(cars, `${carNo},,1,1,${description}`));

  const notes = [];
  if (await removeFromBlacklist(env, carNo)) {
    notes.push('⚠️ 블랙리스트에 있던 차량이라 블랙리스트에서도 제거했습니다.');
  }

  return sendMessage(
    env,
    chatId,
    `✅ 일반 차량을 등록했습니다.\n차량번호: ${carNo}\n설명: ${description}` +
      (notes.length ? '\n' + notes.join('\n') : '') +
      '\n할인 등록 없이 입차 상태만 확인하며, 마스터에게 알립니다.',
  );
}

async function removeCar(env, chatId, carNo, key, label) {
  if (!carNo) {
    return sendMessage(env, chatId, `사용법: /${label === 'VIP' ? '삭제' : '일반삭제'} 123가1234`);
  }
  if (await removeFromList(env, key, carNo)) {
    return sendMessage(env, chatId, `🗑 ${carNo}를 ${label} 목록에서 제거했습니다.`);
  }
  return sendMessage(env, chatId, `ℹ️ ${carNo}는 ${label} 목록에 없습니다.`);
}

async function listCars(env, chatId) {
  const [vip, cars] = await Promise.all([
    getListText(env, KEY_VIP),
    getListText(env, KEY_CARS),
  ]);

  const lines = [];
  const sections = [
    ['⭐ VIP 차량 (할인등록+개인알림)', activeRows(vip)],
    ['🚗 일반 차량 (입차 확인만)', activeRows(cars)],
  ];
  for (const [title, rows] of sections) {
    lines.push(`${title}: ${rows.length}대`);
    rows.forEach((cells, index) => {
      lines.push(`${index + 1}. ${cells[0]} | ${cells[4] || '-'}`);
    });
    lines.push('');
  }
  return sendMessage(env, chatId, lines.join('\n').trim());
}

// --- KV 차량 목록 헬퍼 ---

async function getListText(env, key) {
  return (await env.KV.get(key)) || `${CSV_HEADER}\n`;
}

function appendLine(content, line) {
  return content.replace(/\n*$/, '\n') + line + '\n';
}

async function removeFromList(env, key, carNo) {
  const content = await env.KV.get(key);
  if (!content) return false;
  const lines = content.split('\n');
  const kept = lines.filter((line) => {
    const body = line.trim();
    if (!body || body.startsWith('#')) return true;
    return body.split(',')[0].trim() !== carNo;
  });
  if (kept.length === lines.length) return false;
  await env.KV.put(key, kept.join('\n'));
  return true;
}

async function removeFromBlacklist(env, carNo) {
  const content = await env.KV.get(KEY_BLACKLIST);
  if (!content) return false;
  const lines = content.split('\n');
  const kept = lines.filter((line) => line.trim() !== carNo);
  if (kept.length === lines.length) return false;
  await env.KV.put(KEY_BLACKLIST, kept.join('\n'));
  return true;
}

function activeRows(content) {
  return content
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#') && !line.startsWith('차량번호'))
    .map((line) => line.split(',').map((cell) => cell.trim()))
    .filter((cells) => CAR_NO_RE.test(cells[0]));
}

function activeCarNumbers(content) {
  return activeRows(content).map((cells) => cells[0]);
}

// --- GitHub API (repository_dispatch 전용) ---

async function triggerDispatch(env) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'u-tower-parking-telegram-worker',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ event_type: DISPATCH_EVENT }),
  });
  if (!res.ok) throw new Error(`repository_dispatch 실패 (${res.status})`);
}

// --- Telegram ---

async function sendMessage(env, chatId, text) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  if (!res.ok) {
    console.log(`sendMessage failed: ${res.status} ${(await res.text()).slice(0, 200)}`);
  }
}
