const SETTINGS = 'fall-monitor-telegram';
const LINK = 'fall-monitor-telegram-link';

function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || 'null');
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
  } catch {
    // Linking still works for the current session if storage is disabled.
  }
}

async function call(path, body, headers = { 'Content-Type': 'application/json' }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 24000);
  try {
    const response = await fetch(`/telegram/${path}`, {
      method: 'POST', headers, body: body instanceof Blob ? body : JSON.stringify(body),
      credentials: 'omit', cache: 'no-store', signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Telegram could not complete the request.');
    return result;
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError || error.name === 'AbortError') {
      throw new Error('The shared Telegram bot is unavailable. Try again shortly.');
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export class Telegram {
  settings = read(SETTINGS);
  pairing = read(LINK);

  get status() {
    return { managed: true, linked: Boolean(this.settings?.grant),
      recipient: this.settings?.recipient, bot: this.settings?.bot };
  }

  async action(action) {
    if (action === 'link') {
      if (!this.pairing || Date.now() >= this.pairing.expires) {
        const link = await call('link', {});
        this.pairing = { ...link, expires: Date.now() + 600000 };
        save(LINK, this.pairing);
      }
      const { url, command } = this.pairing;
      return { url, command };
    }
    if (action === 'finish') {
      if (!this.pairing) throw new Error('Open a new Telegram link.');
      try {
        this.settings = await call('finish', { ticket: this.pairing.ticket });
      } catch (error) {
        if (error.message.includes('expired')) {
          this.pairing = null;
          save(LINK, null);
        }
        throw error;
      }
      this.pairing = null;
      save(SETTINGS, this.settings);
      save(LINK, null);
    } else if (action === 'disconnect') {
      this.settings = this.pairing = null;
      save(SETTINGS, null);
      save(LINK, null);
    } else if (action === 'test') {
      if (!this.settings?.grant) throw new Error('Link a Telegram chat first.');
      const { status } = await call('notify', { grant: this.settings.grant, kind: 'test' });
      if (status !== 'telegram_accepted_no_acknowledgement') throw new Error('Could not confirm test delivery. Check your Telegram chat.');
      return { message: 'Telegram accepted the test message. Check your chat.' };
    }
    return {};
  }

  async alert(id, clip) {
    const grant = this.settings?.grant;
    if (!grant) return 'simulated_accepted';
    try {
      if (clip?.size) {
        const type = clip.type.split(';')[0];
        try {
          const result = await call('notify', clip, {
            Authorization: `Bearer ${grant}`, 'X-Incident-ID': id, 'Content-Type': type,
          });
          return result.status;
        } catch (error) {
          if (error.message !== 'Invalid relay request.') throw error;
        }
      }
      const result = await call('notify', { grant, kind: 'fall', incident_id: id, replies: true });
      return result.status;
    } catch {
      return 'uncertain';
    }
  }

  async reply(id) {
    const grant = this.settings?.grant;
    if (!grant) return null;
    const result = await call('response', { grant, incident_id: id });
    return result.action;
  }
}
