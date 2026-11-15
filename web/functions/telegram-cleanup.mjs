import { getStore } from '@netlify/blobs';

export const config = { schedule: '17 * * * *' };

export default async function handler() {
  const store = getStore({ name: 'telegram-state', consistency: 'strong' });
  const now = Date.now();
  for await (const page of store.list({ paginate: true })) {
    for (let start = 0; start < page.blobs.length; start += 20) {
      await Promise.all(page.blobs.slice(start, start + 20).map(async ({ key }) => {
        const entry = await store.get(key, { type: 'json' });
        if (entry && entry.expiresAt <= now) await store.delete(key);
      }));
    }
  }
  return new Response(null, { status: 204 });
}
