// Runs on the audio thread: forwards each 128-sample input quantum to the page. No processing, no storage.
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('capture', Capture);
