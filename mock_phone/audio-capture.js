/**
 * Audio Capture Engine using Web Audio API
 *
 * Captures microphone audio, resamples to 8kHz, encodes to µ-law,
 * and sends to the provided callback.
 *
 * Uses AudioWorklet for low-latency capture on the Audio Rendering Thread.
 */

import { encodeFloat32ToMulaw, float32ToPcm16, encodeMulaw } from './mulaw-codec.js';
import { logger } from './utils.js';

// Target sample rate for telephony
const TARGET_SAMPLE_RATE = 8000;

export class AudioCaptureEngine {
  constructor() {
    this.audioContext = null;
    this.mediaStream = null;
    this.sourceNode = null;
    this.workletNode = null;

    // Resampling state (if needed)
    this.needsResampling = false;
    this.resampleRatio = 1;

    // Callbacks
    this.onAudioChunk = null;  // (mulawBytes: Uint8Array) => void

    // State
    this.isCapturing = false;

    // Stats
    this.chunksSent = 0;
  }

  /**
   * Request microphone access and initialize capture.
   * Must be called from a user gesture.
   *
   * @returns {boolean} - True if initialization succeeded
   */
  async initialize() {
    if (this.audioContext) {
      return true;
    }

    try {
      // Request microphone access
      logger.info('MIC', 'Requesting microphone access...');
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // Create AudioContext at target sample rate
      // Many browsers will resample internally
      this.audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });

      // Check actual sample rate
      const actualRate = this.audioContext.sampleRate;
      if (actualRate !== TARGET_SAMPLE_RATE) {
        logger.warn('MIC', `Browser using ${actualRate}Hz, need ${TARGET_SAMPLE_RATE}Hz - enabling resampling`);
        this.needsResampling = true;
        this.resampleRatio = actualRate / TARGET_SAMPLE_RATE;
      } else {
        logger.info('MIC', `AudioContext at ${TARGET_SAMPLE_RATE}Hz - no resampling needed`);
      }

      // Load AudioWorklet
      try {
        await this.audioContext.audioWorklet.addModule('mic-processor.js');
        logger.info('MIC', 'AudioWorklet loaded');
      } catch (err) {
        logger.error('MIC', `Failed to load AudioWorklet: ${err.message}`);
        // Could fall back to ScriptProcessorNode here, but it's deprecated
        throw err;
      }

      // Create source from microphone stream
      this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);

      // Create worklet node
      this.workletNode = new AudioWorkletNode(this.audioContext, 'mic-processor');

      // Handle audio from worklet
      this.workletNode.port.onmessage = (event) => {
        if (event.data.type === 'audio') {
          this._handleAudioChunk(event.data.samples);
        }
      };

      logger.info('MIC', 'Microphone initialized successfully');
      return true;

    } catch (err) {
      if (err.name === 'NotAllowedError') {
        logger.error('MIC', 'Microphone access denied by user');
      } else if (err.name === 'NotFoundError') {
        logger.error('MIC', 'No microphone found');
      } else {
        logger.error('MIC', `Failed to initialize: ${err.message}`);
      }
      return false;
    }
  }

  /**
   * Start capturing and sending audio.
   */
  start() {
    if (!this.audioContext || !this.sourceNode || !this.workletNode) {
      logger.error('MIC', 'Not initialized');
      return false;
    }

    if (this.isCapturing) {
      return true;
    }

    // Connect the audio graph
    this.sourceNode.connect(this.workletNode);
    // Note: We don't connect worklet to destination - we're just capturing

    // Resume context if suspended
    if (this.audioContext.state === 'suspended') {
      this.audioContext.resume();
    }

    this.isCapturing = true;
    this.chunksSent = 0;
    logger.info('MIC', 'Started capturing');
    return true;
  }

  /**
   * Stop capturing.
   */
  stop() {
    if (!this.isCapturing) {
      return;
    }

    // Disconnect audio graph
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch (e) {
        // May already be disconnected
      }
    }

    this.isCapturing = false;
    logger.info('MIC', `Stopped capturing (sent ${this.chunksSent} chunks)`);
  }

  /**
   * Clean up all resources.
   */
  async dispose() {
    this.stop();

    // Stop media stream tracks
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }

    // Close audio context
    if (this.audioContext) {
      await this.audioContext.close();
      this.audioContext = null;
    }

    this.sourceNode = null;
    this.workletNode = null;
  }

  /**
   * Handle audio chunk from worklet.
   * @param {Float32Array} samples - Audio samples from worklet
   */
  _handleAudioChunk(samples) {
    if (!this.isCapturing || !this.onAudioChunk) {
      return;
    }

    let processedSamples = samples;

    // Resample if needed (simple linear interpolation)
    if (this.needsResampling) {
      processedSamples = this._resample(samples);
    }

    // Encode to µ-law
    const mulawBytes = encodeFloat32ToMulaw(processedSamples);

    // Send to callback
    this.onAudioChunk(mulawBytes);
    this.chunksSent++;
  }

  /**
   * Simple linear interpolation resampling.
   * For production, consider using a proper resampling library.
   *
   * @param {Float32Array} samples - Input samples at source rate
   * @returns {Float32Array} - Output samples at target rate
   */
  _resample(samples) {
    const outputLength = Math.round(samples.length / this.resampleRatio);
    const output = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i++) {
      const srcIndex = i * this.resampleRatio;
      const srcIndexFloor = Math.floor(srcIndex);
      const srcIndexCeil = Math.min(srcIndexFloor + 1, samples.length - 1);
      const frac = srcIndex - srcIndexFloor;

      // Linear interpolation
      output[i] = samples[srcIndexFloor] * (1 - frac) + samples[srcIndexCeil] * frac;
    }

    return output;
  }

  /**
   * Get capture status.
   */
  getStatus() {
    return {
      initialized: !!this.audioContext,
      capturing: this.isCapturing,
      sampleRate: this.audioContext?.sampleRate || 0,
      needsResampling: this.needsResampling,
      chunksSent: this.chunksSent,
    };
  }
}
