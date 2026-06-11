// U-Tower Parking Telegram Bot — Cloudflare Worker
//
// 역할 1 (fetch):    텔레그램 웹훅 수신 → 명령 처리
//   /상태 [번호]  - 입차 현황 조회 (data/status.json 기반, 누구나)
//   /추가 ...     - vip_list.txt에 차량 추가 커밋 (마스터만)
//   /삭제 차량번호 - vip_list.txt에서 차량 제거 커밋 (마스터만)
//   /목록         - 등록 차량 목록 (마스터만)
//   /실행         - GitHub Action 즉시 실행 (마스터만)
// 역할 2 (scheduled): KST 08~20시 매 정각 GitHub Action 트리거 (repository_dispatch)

const VIP_LIST_PATH = 'vip_list.txt';
const BLACKLIST_PATH = 'blacklist.txt';
const STATUS_PATH = 'data/status.json';
const DISPATCH_EVENT = 'run-parking-vip';
const CAR_NO_RE = /^\d{2,3}[가-힣]\d{4}$/u;
const KST_OFFSET_MS = 9 * 3600 * 1000;

export default {
  async fetch(request, env) {
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
      return addCar(env, chatId, args);

    case '/삭제':
      if (!isMaster) return sendMessage(env, chatId, '⛔ 차량 삭제는 마스터만 가능합니다.');
      return removeCar(env, chatId, args[0]);

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
    '/상태 8905 - 끝 4자리로 특정 차량 조회',
  ];
  if (isMaster) {
    lines.push(
      '/추가 241다8905 [채팅ID] [설명] - 차량 등록',
      '/삭제 241다8905 - 차량 제거',
      '/목록 - 등록 차량 목록',
      '/실행 - 봇 즉시 실행',
    );
  }
  lines.push('', '입차 확인과 할인 등록은 08~20시 매 정각에 실행됩니다.');
  return lines.join('\n');
}

// --- /상태 ---

async function replyStatus(env, chatId, isMaster, filter) {
  const file = await ghGetFile(env, STATUS_PATH);
  if (!file) {
    return sendMessage(env, chatId, 'ℹ️ 아직 상태 정보가 없습니다. 봇이 한 번 실행된 뒤 조회할 수 있습니다.');
  }
  const status = JSON.parse(file.content);

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

// --- /추가, /삭제, /목록 ---

async function addCar(env, chatId, args) {
  const [carNo, ...rest] = args;
  if (!carNo || !CAR_NO_RE.test(carNo)) {
    return sendMessage(env, chatId, '사용법: /추가 241다8905 [채팅ID] [설명]\n차량번호 형식이 올바르지 않습니다.');
  }

  let targetChatId = String(chatId);
  let description = rest.join(' ');
  if (rest.length && /^-?\d+$/.test(rest[0])) {
    targetChatId = rest[0];
    description = rest.slice(1).join(' ');
  }

  const file = await ghGetFile(env, VIP_LIST_PATH);
  if (!file) throw new Error(`${VIP_LIST_PATH}을 읽지 못했습니다.`);

  const existing = activeCarNumbers(file.content);
  if (existing.includes(carNo)) {
    return sendMessage(env, chatId, `ℹ️ ${carNo}는 이미 등록되어 있습니다.`);
  }

  const newLine = `${carNo},${targetChatId},1,1,${description}`;
  const content = file.content.replace(/\n*$/, '\n') + newLine + '\n';
  await ghPutFile(env, VIP_LIST_PATH, content, file.sha, `Add vehicle ${carNo} via Telegram`);

  // 블랙리스트에 있으면 vip_list에 넣어도 제외되므로 자동으로 빼준다.
  let blacklistNote = '';
  const blacklist = await ghGetFile(env, BLACKLIST_PATH);
  if (blacklist) {
    const lines = blacklist.content.split('\n');
    const kept = lines.filter((line) => line.trim() !== carNo);
    if (kept.length !== lines.length) {
      await ghPutFile(env, BLACKLIST_PATH, kept.join('\n'), blacklist.sha, `Remove ${carNo} from blacklist via Telegram`);
      blacklistNote = '\n⚠️ 블랙리스트에 있던 차량이라 블랙리스트에서도 제거했습니다.';
    }
  }

  return sendMessage(
    env,
    chatId,
    `✅ 차량을 등록했습니다.\n차량번호: ${carNo}\n알림 채팅ID: ${targetChatId}\n설명: ${description || '-'}${blacklistNote}\n다음 정각 실행부터 할인 등록됩니다. 바로 적용하려면 /실행`,
  );
}

async function removeCar(env, chatId, carNo) {
  if (!carNo) {
    return sendMessage(env, chatId, '사용법: /삭제 241다8905');
  }
  const file = await ghGetFile(env, VIP_LIST_PATH);
  if (!file) throw new Error(`${VIP_LIST_PATH}을 읽지 못했습니다.`);

  const lines = file.content.split('\n');
  const kept = lines.filter((line) => {
    const body = line.trim();
    if (!body || body.startsWith('#')) return true;
    return body.split(',')[0].trim() !== carNo;
  });
  if (kept.length === lines.length) {
    return sendMessage(env, chatId, `ℹ️ ${carNo}는 목록에 없습니다.`);
  }
  await ghPutFile(env, VIP_LIST_PATH, kept.join('\n'), file.sha, `Remove vehicle ${carNo} via Telegram`);
  return sendMessage(env, chatId, `🗑 ${carNo}를 목록에서 제거했습니다.`);
}

async function listCars(env, chatId) {
  const file = await ghGetFile(env, VIP_LIST_PATH);
  if (!file) throw new Error(`${VIP_LIST_PATH}을 읽지 못했습니다.`);

  const rows = activeRows(file.content);
  if (!rows.length) {
    return sendMessage(env, chatId, 'ℹ️ 등록된 차량이 없습니다.');
  }
  const lines = [`🚗 등록 차량 (${rows.length}대)`];
  rows.forEach((cells, index) => {
    lines.push(`${index + 1}. ${cells[0]} | ${cells[4] || '-'}`);
  });
  return sendMessage(env, chatId, lines.join('\n'));
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

// --- GitHub API ---

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GH_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'User-Agent': 'u-tower-parking-telegram-worker',
  };
}

async function ghGetFile(env, path) {
  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}?ref=main`,
    { headers: ghHeaders(env) },
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub GET ${path} 실패 (${res.status})`);
  const data = await res.json();
  return { content: decodeBase64Utf8(data.content), sha: data.sha };
}

async function ghPutFile(env, path, content, sha, message) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`, {
    method: 'PUT',
    headers: { ...ghHeaders(env), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      content: encodeBase64Utf8(content),
      sha,
      branch: 'main',
    }),
  });
  if (!res.ok) throw new Error(`GitHub PUT ${path} 실패 (${res.status})`);
}

async function triggerDispatch(env) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: 'POST',
    headers: { ...ghHeaders(env), 'Content-Type': 'application/json' },
    body: JSON.stringify({ event_type: DISPATCH_EVENT }),
  });
  if (!res.ok) throw new Error(`repository_dispatch 실패 (${res.status})`);
}

function decodeBase64Utf8(b64) {
  const binary = atob(b64.replace(/\n/g, ''));
  const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function encodeBase64Utf8(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
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
