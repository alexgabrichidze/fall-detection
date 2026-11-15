import { createHmac } from 'node:crypto';

const token = process.env.TELEGRAM_BOT_TOKEN;
const site = URL.canParse(process.argv[2]) ? new URL(process.argv[2]) : null;
if (!token || !/^[0-9]+:[A-Za-z0-9_-]+$/.test(token) || site?.protocol !== 'https:'
    || site.username || site.password) {
  console.error('Set TELEGRAM_BOT_TOKEN and supply the production HTTPS site URL.');
  process.exit(1);
}
const url = new URL('/telegram/webhook', site).href;
const secret = createHmac('sha256', token).update('fall-monitor/telegram-webhook/v1').digest('hex');

async function call(method, body = {}) {
  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(8000),
    });
    const payload = await response.json();
    if (response.ok && payload.ok) return payload.result;
  } catch {
    // Report the operation without revealing a URL containing the token.
  }
  throw new Error(`Telegram could not complete ${method}.`);
}

try {
  const existing = await call('getWebhookInfo');
  if (existing.url && existing.url !== url) throw new Error('This bot already has an incoming webhook at another URL.');
  await call('setWebhook', { url, secret_token: secret, allowed_updates: ['message', 'callback_query'] });
  if ((await call('getWebhookInfo')).url !== url) throw new Error('Telegram did not confirm the webhook.');
  console.log('Telegram webhook registered at ' + url);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
