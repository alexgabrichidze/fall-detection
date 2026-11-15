import { createModel } from 'vosk-browser';

const grammar = [
  "i'm okay", 'i am okay', "i'm not okay", 'i am not okay',
  'help', 'i need help', 'are you okay', 'ten seconds left', '[unk]',
];
const positive = /^(?:i'm|i am) (?:okay|ok)(?: (?:i'm|i am) (?:okay|ok))*$/;

function matches(result) {
  const words = result?.result || [];
  const text = (result?.text || '').replaceAll('’', "'");
  return positive.test(text) && words.map(word => word.word).join(' ') === text
    && words.every(word => Number.isFinite(word.conf) && word.conf >= 0.45 && word.conf <= 1)
    && words.reduce((sum, word) => sum + word.conf, 0) / words.length >= 0.65;
}

export class Voice {
  enabled = false;
  listening = false;
  status = 'Disabled';
  generation = 0;

  async enable(context) {
    if (!context) throw new Error('Browser audio is unavailable.');
    const access = await navigator.mediaDevices.getUserMedia({ audio: true });
    access.getTracks().forEach(track => track.stop());
    this.context = context;
    this.status = 'Loading local voice model';
    try {
      this.model ??= await createModel('/models/voice.tar.gz', -1);
      this.enabled = true;
      this.status = 'Ready';
    } catch {
      this.enabled = false;
      this.status = 'Voice model unavailable';
      throw new Error('Could not load the offline voice model.');
    }
  }

  stop() {
    this.generation++;
    if (this.stream) this.stream.getTracks().forEach(track => track.stop());
    this.input?.disconnect();
    this.processor?.disconnect();
    this.mute?.disconnect();
    this.recognizer?.remove();
    this.stream = this.input = this.processor = this.mute = this.recognizer = null;
    this.listening = false;
    if (this.enabled && this.model) this.status = 'Ready';
  }

  async listen(cancel) {
    if (!this.enabled || !this.model || !this.context) return;
    this.stop();
    const generation = this.generation;
    this.status = 'Waiting for prompt echo';
    await new Promise(resolve => setTimeout(resolve, 400));
    if (generation !== this.generation) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
      if (generation !== this.generation) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      this.stream = stream;
      this.recognizer = new this.model.KaldiRecognizer(this.context.sampleRate, JSON.stringify(grammar));
      this.recognizer.setWords(true);
      this.recognizer.on('result', message => {
        if (generation === this.generation && matches(message.result)) cancel();
      });
      this.input = this.context.createMediaStreamSource(stream);
      this.processor = this.context.createScriptProcessor(4096, 1, 1);
      this.mute = this.context.createGain();
      this.mute.gain.value = 0;
      this.processor.onaudioprocess = event => this.recognizer?.acceptWaveform(event.inputBuffer);
      this.input.connect(this.processor);
      this.processor.connect(this.mute);
      this.mute.connect(this.context.destination);
      this.listening = true;
      this.status = "Say I'm okay";
    } catch {
      if (generation === this.generation) {
        this.stop();
        this.status = 'Microphone unavailable';
      }
    }
  }
}
