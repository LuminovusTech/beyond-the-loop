/**
 * AudioWorklet Processor for Microphone Capture
 *
 * This runs on the Audio Rendering Thread (separate from main JS thread),
 * providing low-latency, glitch-free audio capture.
 *
 * AudioWorklet processes audio in 128-sample frames at the context's sample rate.
 * We buffer samples and send chunks to the main thread via postMessage.
 *
 * Note: This file must be loaded separately via audioWorklet.addModule()
 */

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();

    // Buffer for accumulating samples before sending
    // At 8kHz, 160 samples = 20ms (telephony standard chunk size)
    this.buffer = new Float32Array(160);
    this.bufferIndex = 0;

    // Target samples per chunk (20ms at 8kHz = 160 samples)
    this.samplesPerChunk = 160;

    // Handle messages from main thread
    this.port.onmessage = (event) => {
      if (event.data.type === 'configure') {
        // Could use this for dynamic configuration
      }
    };
  }

  /**
   * Process audio frames from the microphone.
   *
   * @param {Float32Array[][]} inputs - Input audio data [input][channel][samples]
   * @param {Float32Array[][]} outputs - Output audio data (unused for capture)
   * @param {Object} parameters - AudioParam values
   * @returns {boolean} - Return true to keep processor alive
   */
  process(inputs, outputs, parameters) {
    // Get the first input, first channel (mono)
    const input = inputs[0];
    if (!input || !input[0]) {
      return true;  // No input yet, keep alive
    }

    const samples = input[0];  // Float32Array of 128 samples

    // Copy samples to our buffer
    for (let i = 0; i < samples.length; i++) {
      this.buffer[this.bufferIndex++] = samples[i];

      // When buffer is full, send to main thread
      if (this.bufferIndex >= this.samplesPerChunk) {
        // Send a copy of the buffer
        this.port.postMessage({
          type: 'audio',
          samples: this.buffer.slice(0, this.samplesPerChunk),
        });

        // Reset buffer
        this.bufferIndex = 0;
      }
    }

    return true;  // Keep processor alive
  }
}

// Register the processor
registerProcessor('mic-processor', MicProcessor);
