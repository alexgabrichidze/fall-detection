import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { getStore } from '@netlify/blobs';

const PAIR_SECONDS = 600;
const REPLY_MS = 30 * 60 * 1000;
const MAX_CLIP_BYTES = 4 * 1024 * 1024;
const ACCEPTED = 'telegram_accepted_no_acknowledgement';
const FAILED = 'failed';
const UNCERTAIN = 'uncertain';
const INCIDENT = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const MEDIA = { 'video/mp4': ['sendVideo', 'video', 'fall-clip.mp4'],
  'video/webm': ['sendDocument', 'document', 'fall-clip.webm'] };

export const config = {
  path: ['/telegram/link', '/telegram/finish', '/telegram/notify', '/telegram/response', '/telegram/webhook'],
  method: 'POST',
};

class InvalidRequest extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

const mac = (key, data) => createHmac('sha256', key).update(data).digest();
const equal = (a, b) => a.length === b.length && timingSafeEqual(a, b);
const json = (value, status = 200) => Response.json(value, {
  status, headers: { 'Cache-Control': 'no-store' },
});

function signedPair(key, purpose, payload) {
  const signature = createHmac('sha256', key).update(purpose).update(payload).digest().subarray(0, 16);
  return Buffer.concat([payload, signature]).toString('base64url');
}

function readPair(key, purpose, value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{48}$/.test(value)) return null;
  const payload = Buffer.from(value, 'base64url').subarray(0, 20);
  if (!equal(Buffer.from(value), Buffer.from(signedPair(key, purpose, payload)))) return null;
  const expiresAt = payload.readUInt32BE() * 1000;
  if (expiresAt <= Date.now()) return null;
  return { id: payload.subarray(4).toString('hex'), expiresAt };
}

function startLink(key, bot) {
  // Give the caregiver and browser different proofs for the same short-lived link.
  const pair = randomBytes(20);
  pair.writeUInt32BE(Math.floor(Date.now() / 1000) + PAIR_SECONDS);
  const code = 'fall_' + signedPair(key, 'start', pair);
  return {
    ticket: signedPair(key, 'finish', pair),
    url: `https://t.me/${bot}?start=${code}`,
    command: `/start ${code}`,
  };
}

function grantFor(key, chat) {
  // Match grants issued by previous builds so connected phones stay linked.
  return `${chat}.${mac(key, chat).toString('hex')}`;
}

function grantedChat(key, grant) {
  if (typeof grant !== 'string' || !/^[0-9]{1,20}\.[0-9a-f]{64}$/.test(grant)) return null;
  const chat = grant.split('.')[0];
  return equal(Buffer.from(grant), Buffer.from(grantFor(key, chat))) ? chat : null;
}

async function readJson(request, limit) {
  if (request.headers.get('content-type')?.split(';')[0] !== 'application/json') {
    throw new InvalidRequest('Invalid relay request.');
  }
  const body = await request.text();
  if (Buffer.byteLength(body) > limit) throw new InvalidRequest('Invalid relay request.');
  try {
    const data = JSON.parse(body);
    if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error();
    return data;
  } catch {
    throw new InvalidRequest('Invalid relay request.');
  }
}

function telegram(token) {
  return async (method, body, timeout = 6500) => {
    const fields = body instanceof FormData;
    try {
      const result = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
        method: 'POST',
        body: fields ? body : JSON.stringify(body),
        headers: fields ? undefined : { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(timeout),
      });
      const data = await result.json().catch(() => null);
      if (result.ok && data?.ok === true) return ACCEPTED;
      if (result.status >= 400 && result.status < 500 || result.ok && data?.ok === false) return FAILED;
      return UNCERTAIN;
    } catch {
      return UNCERTAIN;
    }
  };
}

async function sendAlert(store, send, chat, id, clip, type) {
  const key = `alert/${chat}/${id}`;
  const expiresAt = Date.now() + REPLY_MS;
  const pending = await store.setJSON(key, { expiresAt, status: UNCERTAIN }, { onlyIfNew: true });
  if (!pending.modified) return (await store.get(key, { type: 'json' }))?.status || UNCERTAIN;

  const text = 'Safe&Sound detected a possible fall. The response countdown expired. Please check on the person.';
  const replyMarkup = { inline_keyboard: [[
    { text: 'Help is on the way', callback_data: `fall:help:${id}` },
    { text: 'False alarm', callback_data: `fall:false_alarm:${id}` },
  ]] };
  let status = FAILED;
  if (clip) {
    const [method, field, name] = MEDIA[type];
    const body = new FormData();
    body.set('chat_id', chat);
    body.set('caption', text);
    body.set('reply_markup', JSON.stringify(replyMarkup));
    body.set(field, new Blob([clip], { type }), name);
    status = await send(method, body, 16000);
  }
  if (status === FAILED) status = await send('sendMessage', { chat_id: chat, text, reply_markup: replyMarkup });
  await store.setJSON(key, { expiresAt, status });
  return status;
}

async function handleUpdate(update, store, grantKey, send) {
  const message = update.message;
  if (message?.chat?.type === 'private' && Number.isSafeInteger(message.chat.id) && message.chat.id > 0) {
    const code = /^\/start (?:fall_)([A-Za-z0-9_-]{48})$/.exec(message.text || '')?.[1];
    const pair = readPair(grantKey, 'start', code);
    if (pair) {
      const recipient = [message.chat.first_name, message.chat.last_name].filter(Boolean).join(' ').slice(0, 128);
      await store.setJSON('pair/' + pair.id, {
        expiresAt: pair.expiresAt, chat: String(message.chat.id), recipient: recipient || 'Private chat',
      }, { onlyIfNew: true });
    }
  }

  const query = update.callback_query;
  const chat = query?.message?.chat;
  if (chat?.type !== 'private' || !Number.isSafeInteger(chat.id) || chat.id <= 0
      || query.from?.id !== chat.id || typeof query.id !== 'string') return;
  const [, action, id] = /^fall:(help|false_alarm):(.+)$/.exec(query.data || '') || [];
  if (!id || !INCIDENT.test(id)) return;
  const alert = await store.get(`alert/${chat.id}/${id}`, { type: 'json' });
  let accepted = false;
  if (alert && alert.expiresAt > Date.now() && alert.status !== FAILED) {
    ({ modified: accepted } = await store.setJSON(`reply/${chat.id}/${id}`, {
      action, expiresAt: alert.expiresAt,
    }, { onlyIfNew: true }));
  }
  const ack = send('answerCallbackQuery', {
    callback_query_id: query.id,
    text: accepted ? 'Response recorded.' : 'This alert already has a reply or has expired.',
  });
  if (accepted && Number.isSafeInteger(query.message.message_id)) {
    await Promise.all([ack, send('editMessageReplyMarkup', {
      chat_id: chat.id, message_id: query.message.message_id, reply_markup: { inline_keyboard: [] },
    })]);
  } else {
    await ack;
  }
}

export async function route(request, { token, bot, store, send = telegram(token) }) {
  if (request.method !== 'POST') return json({ error: 'Use POST.' }, 405);
  const action = new URL(request.url).pathname.split('/').at(-1);
  const grantKey = mac(token, 'fall-monitor/telegram-grant/v1');
  try {
    if (action === 'webhook') {
      const expected = mac(token, 'fall-monitor/telegram-webhook/v1').toString('hex');
      const received = request.headers.get('X-Telegram-Bot-Api-Secret-Token') || '';
      if (!equal(Buffer.from(received), Buffer.from(expected))) return json({ error: 'Unauthorized.' }, 401);
      await handleUpdate(await readJson(request, 128 * 1024), store, grantKey, send);
      return json({});
    }
    if (action === 'link') return json(startLink(grantKey, bot));

    const type = request.headers.get('content-type')?.split(';')[0];
    if (action === 'notify' && Object.hasOwn(MEDIA, type)) {
      const grant = request.headers.get('authorization')?.match(/^Bearer (.+)$/)?.[1];
      const chat = grantedChat(grantKey, grant);
      if (!chat) return json({ status: FAILED });
      const id = request.headers.get('X-Incident-ID') || '';
      if (!INCIDENT.test(id) || Number(request.headers.get('content-length')) > MAX_CLIP_BYTES) {
        throw new InvalidRequest('Invalid relay request.');
      }
      const clip = Buffer.from(await request.arrayBuffer());
      const playable = type === 'video/mp4' ? clip.subarray(4, 8).toString() === 'ftyp'
        : clip.subarray(0, 4).toString('hex') === '1a45dfa3';
      if (!playable || clip.length > MAX_CLIP_BYTES) throw new InvalidRequest('Invalid relay request.');
      return json({ status: await sendAlert(store, send, chat, id, clip, type) });
    }
    const body = await readJson(request, 2048);
    if (action === 'finish') {
      const pair = readPair(grantKey, 'finish', body.ticket);
      if (!pair) throw new InvalidRequest('This link expired. Open a new Telegram link.');
      const match = await store.get('pair/' + pair.id, { type: 'json' });
      if (!match) throw new InvalidRequest('Tap Start in Telegram using this link, then try again.');
      return json({ grant: grantFor(grantKey, match.chat), bot, recipient: match.recipient });
    }
    const chat = grantedChat(grantKey, body.grant);
    if (action === 'response') {
      if (!chat || typeof body.incident_id !== 'string' || !INCIDENT.test(body.incident_id)) {
        throw new InvalidRequest('Invalid caregiver response request.');
      }
      const reply = await store.get(`reply/${chat}/${body.incident_id}`, { type: 'json' });
      return json({ action: reply?.expiresAt > Date.now() ? reply.action : null });
    }
    if (action !== 'notify') return json({ error: 'Unknown Telegram request.' }, 404);
    if (chat && body.kind === 'test') {
      const now = Date.now();
      const limited = await store.setJSON(`test/${chat}/${Math.floor(now / 5000)}`, {
        expiresAt: now + 5000,
      }, { onlyIfNew: true });
      return json({ status: limited.modified ? await send('sendMessage', {
        chat_id: chat, text: 'TEST ONLY: Safe&Sound Telegram setup check. No fall detected.',
      }) : FAILED });
    }
    if (chat && body.kind === 'fall' && typeof body.incident_id === 'string' && INCIDENT.test(body.incident_id)) {
      return json({ status: await sendAlert(store, send, chat, body.incident_id) });
    }
    return json({ status: FAILED });
  } catch (error) {
    if (error instanceof InvalidRequest) return json({ error: error.message }, error.status);
    return json({ error: 'Telegram is temporarily unavailable.' }, 503);
  }
}

export default async function handler(request) {
  const { TELEGRAM_BOT_TOKEN: token, TELEGRAM_BOT_USERNAME: bot } = process.env;
  if (!token || !bot || !/^[A-Za-z0-9_]{5,32}$/.test(bot)) {
    return json({ error: 'Telegram is temporarily unavailable.' }, 503);
  }
  return route(request, { token, bot, store: getStore({ name: 'telegram-state', consistency: 'strong' }) });
}
