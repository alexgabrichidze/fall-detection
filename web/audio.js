const Audio = window.AudioContext || window.webkitAudioContext;

export class PromptAudio {
  buffers = new Map();
  generation = 0;
  status = 'not requested';

  async unlock() {
    if (!Audio) return;
    try {
      this.context ??= new Audio();
      if (this.context.state !== 'running') await this.context.resume();
    } catch {
      this.status = 'unavailable';
    }
  }

  cancel() {
    this.generation++;
    if (this.source) {
      this.source.stop();
      this.source = null;
    }
    if (this.status === 'playing') this.status = 'cancelled';
  }

  async play(name) {
    this.cancel();
    const generation = this.generation;
    try {
      if (!this.context) throw new Error('Audio output is unavailable.');
      this.status = 'playing';
      if (!this.buffers.has(name)) {
        const response = await fetch(`/prompts/${name}.wav`);
        if (!response.ok) throw new Error('Prompt unavailable.');
        this.buffers.set(name, await this.context.decodeAudioData(await response.arrayBuffer()));
      }
      if (generation !== this.generation) return false;
      const source = this.context.createBufferSource();
      source.buffer = this.buffers.get(name);
      source.connect(this.context.destination);
      this.source = source;
      await new Promise(resolve => {
        source.onended = resolve;
        source.start();
      });
      source.disconnect();
      if (generation !== this.generation) return false;
      this.source = null;
      this.status = 'played';
      return true;
    } catch {
      if (generation === this.generation) this.status = 'unavailable';
      return false;
    }
  }
}
