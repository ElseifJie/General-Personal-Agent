class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.buffer = new Float32Array(0);
    this.pos = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || !input.length) return true;

    const merged = new Float32Array(this.buffer.length + input.length);
    merged.set(this.buffer);
    merged.set(input, this.buffer.length);
    this.buffer = merged;

    const out = [];
    let pos = this.pos;
    while (pos < this.buffer.length - 1) {
      const index = Math.floor(pos);
      const frac = pos - index;
      out.push(this.buffer[index] * (1 - frac) + this.buffer[index + 1] * frac);
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    if (consumed > 0) {
      this.buffer = this.buffer.slice(consumed);
      this.pos = pos - consumed;
    }
    if (out.length) {
      const pcm = new Int16Array(out.length);
      for (let i = 0; i < out.length; i += 1) {
        const s = Math.max(-1, Math.min(1, out[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
