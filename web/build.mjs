import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { cp, mkdir, readFile, readdir, rm, writeFile, stat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { fixTensorSignatures } from './static-model.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const root = resolve(here, '..');
const dist = resolve(here, 'dist');
const cache = resolve(here, '.assets');
const python = resolve(root, 'src/fall_detection');
const deps = resolve(here, 'node_modules');

async function download(url, path, expected) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not download ${url}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (expected && createHash('sha256').update(bytes).digest('hex') !== expected) {
    throw new Error(`Checksum mismatch for ${url}`);
  }
  await writeFile(path, bytes);
}

async function asset(local, filename, url, expected) {
  const existing = await readFile(local).catch(() => null);
  if (existing) {
    if (expected && createHash('sha256').update(existing).digest('hex') !== expected) {
      throw new Error(`Checksum mismatch for ${local}`);
    }
    return local;
  }
  const path = resolve(cache, filename);
  if (!(await stat(path).catch(() => null))) await download(url, path, expected);
  return path;
}

await rm(dist, { recursive: true, force: true });
await Promise.all([mkdir(resolve(dist, 'models'), { recursive: true }), mkdir(cache, { recursive: true })]);
const fall = await asset(resolve(root, 'models/fall_detection_transformer.tflite'), 'fall.tflite',
  'https://raw.githubusercontent.com/punpayut/Fall-Detection/087d88dfa9fb784cda13379157f983950974f616/deployment/raspberry_pi/fall_detection_transformer.tflite',
  'c66659c3ac6d37c60469bee4d6f88cb2ac4976928bde6d46b19e7f851e8980b0');
const pose = await asset(resolve(root, 'models/pose_landmarker_lite.task'), 'pose.task',
  'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task');
const voiceFolder = 'vosk-model-small-en-us-0.15';
let voiceRoot = resolve(root, 'models');
if (!(await stat(resolve(voiceRoot, voiceFolder)).catch(() => null))) {
  voiceRoot = cache;
  const zip = await asset(resolve(cache, 'voice.zip'), 'voice.zip', `https://alphacephei.com/vosk/models/${voiceFolder}.zip`,
    '30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498');
  execFileSync('unzip', ['-qo', zip, '-d', voiceRoot]);
}
execFileSync('tar', ['-czf', resolve(dist, 'models/voice.tar.gz'), '-C', voiceRoot, voiceFolder]);

const appPolicy = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self' blob:; media-src 'self' blob:; img-src 'self' blob: data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'";
await writeFile(resolve(dist, '_headers'), `/*
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Embedder-Policy: require-corp
  Permissions-Policy: camera=(self), microphone=(self)
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
/
  Content-Security-Policy: ${appPolicy}
/index.html
  Content-Security-Policy: ${appPolicy}
/runtime/voice/worker.js
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-eval' 'wasm-unsafe-eval'; connect-src 'self' blob:; object-src 'none'
/sw.js
  Cache-Control: no-cache
`);

const html = (await readFile(resolve(python, 'static/index.html'), 'utf8'))
  .replace('href="/monitor.css"', 'href="/monitor.css?v=__BUILD_ID__"')
  .replace('  <script src="/monitor.js" defer></script>', `  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/icons/192.png">
  <link rel="apple-touch-icon" href="/icons/192.png">
  <meta name="theme-color" content="#176448">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Safe&amp;Sound">
  <script type="module" src="/monitor.js?v=__BUILD_ID__"></script>`)
  .replace('<img id="preview" alt="Local camera preview with pose landmarks" hidden>',
    '<video id="preview" aria-label="Local camera preview" muted playsinline autoplay hidden></video>');
await Promise.all([
  readFile(fall).then(data => writeFile(resolve(dist, 'models/fall.tflite'), fixTensorSignatures(data))),
  cp(pose, resolve(dist, 'models/pose.task')),
  cp(resolve(root, 'models/LICENSE-punpayut.txt'), resolve(dist, 'models/LICENSE-punpayut.txt')),
  cp(resolve(python, 'prompts'), resolve(dist, 'prompts'), { recursive: true }),
  cp(resolve(here, 'manifest.webmanifest'), resolve(dist, 'manifest.webmanifest')),
  cp(resolve(here, 'icons'), resolve(dist, 'icons'), { recursive: true }),
  cp(resolve(python, 'static/monitor.css'), resolve(dist, 'monitor.css')),
  cp(resolve(python, 'static/geist.woff2'), resolve(dist, 'geist.woff2')),
  cp(resolve(python, 'static/geist-OFL.txt'), resolve(dist, 'geist-OFL.txt')),
  cp(resolve(deps, '@mediapipe/tasks-vision/wasm'), resolve(dist, 'runtime/pose'), { recursive: true }),
  cp(resolve(deps, '@litertjs/core/wasm'), resolve(dist, 'runtime/litert'), { recursive: true }),
]);
// Vosk's generated bindings need dynamic functions. Serve its worker as a
// separate file so the page can keep its stricter script policy.
const separateVoiceWorker = {
  name: 'voice-worker',
  setup(builder) {
    builder.onLoad({ filter: /vosk-browser[\\/]dist[\\/]vosk\.js$/ }, async ({ path }) => {
      const source = await readFile(path, 'utf8');
      const packed = source.match(/var WorkerFactory = createBase64WorkerFactory\('([A-Za-z0-9+/=]+)'[^;]*\);/);
      if (!packed) throw new Error('Could not locate the bundled offline speech worker.');
      await mkdir(resolve(dist, 'runtime/voice'), { recursive: true });
      await writeFile(resolve(dist, 'runtime/voice/worker.js'), Buffer.from(packed[1], 'base64'));
      return { contents: source.replace(packed[0],
        "var WorkerFactory = function(options) { return new Worker('/runtime/voice/worker.js', options); };") };
    });
  },
};
await build({
  entryPoints: { monitor: resolve(here, 'entry.js') }, outdir: dist,
  bundle: true, splitting: true, format: 'esm', minify: true, target: 'es2022',
  chunkNames: 'chunks/[name]-[hash]',
  plugins: [separateVoiceWorker],
});
const versionHash = createHash('sha256');
const prompts = (await readdir(resolve(dist, 'prompts'))).map(name => 'prompts/' + name);
versionHash.update(html);
for (const name of ['monitor.js', 'monitor.css', 'manifest.webmanifest', 'models/fall.tflite', ...prompts]) {
  versionHash.update(await readFile(resolve(dist, name)));
}
const version = versionHash.digest('hex').slice(0, 12);
const chunks = (await readdir(resolve(dist, 'chunks'))).map(name => 'chunks/' + name);
await writeFile(resolve(dist, 'index.html'), html.replaceAll('__BUILD_ID__', version));
await writeFile(resolve(dist, 'sw.js'), (await readFile(resolve(here, 'sw.js'), 'utf8'))
  .replaceAll('__BUILD_ID__', version).replace('__ASSETS__', JSON.stringify([...chunks, ...prompts].map(name => '/' + name))));
console.log('Built the installable Safe&Sound app in web/dist.');
