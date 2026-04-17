/**
 * Audio Playback Engine using Web Audio API
 *
 * Uses timeline-based scheduling for gap-free audio playback.
 * The browser's Audio Rendering Thread handles all timing - we just
 * schedule chunks on a continuous timeline.
 *
 * Key concepts:
 * - `startTime` tracks where the next chunk should be scheduled
 * - `scheduledSources` tracks all sources for barge-in handling
 * - Marks are scheduled to fire when their associated audio plays
 *
 */

import { decodeMulawToFloat32 } from './mulaw-codec.js';

// Sample rate for telephony audio
const SAMPLE_RATE = 8000;

export class AudioPlaybackEngine {
  constructor() {
    // AudioContext created on first use (requires user gesture)
    this.audioContext = null;

    // Timeline tracking
    this.startTime = -1;

    // Track scheduled sources for barge-in
    this.scheduledSources = [];

    // Pending marks: { markName, fireTime }
    this.pendingMarks = [];

    // Callbacks
    this.onMarkFire = null;  // Called when a mark should be acked

    // Stats
    this.totalChunksScheduled = 0;
    this.totalBytesScheduled = 0;
  }

  /**
   * Initialize the AudioContext.
   * Must be called from a user gesture (click/keypress) in most browsers.
   */
  async initialize() {
    if (this.audioContext) {
      return true;
    }

    try {
      this.audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });

      // Check if browser accepted our sample rate
      if (this.audioContext.sampleRate !== SAMPLE_RATE) {
        console.warn(
          `Browser using ${this.audioContext.sampleRate}Hz instead of ${SAMPLE_RATE}Hz. ` +
          `Audio may need resampling.`
        );
      }

      // Resume if suspended (autoplay policy)
      if (this.audioContext.state === 'suspended') {
        await this.audioContext.resume();
      }

      console.log(`AudioContext initialized at ${this.audioContext.sampleRate}Hz`);
      return true;
    } catch (err) {
      console.error('Failed to initialize AudioContext:', err);
      return false;
    }
  }

  /**
   * Schedule a µ-law audio chunk for playback.
   * Returns the time when this chunk will finish playing.
   *
   * @param {Uint8Array} mulawBytes - µ-law encoded audio data
   * @returns {number} - AudioContext time when chunk finishes playing
   */
  scheduleChunk(mulawBytes) {
    if (!this.audioContext) {
      console.error('AudioContext not initialized');
      return -1;
    }

    // Decode µ-law to Float32 for Web Audio
    const float32Data = decodeMulawToFloat32(mulawBytes);

    // Create AudioBuffer
    const audioBuffer = this.audioContext.createBuffer(
      1,                          // mono
      float32Data.length,         // number of samples
      this.audioContext.sampleRate
    );
    audioBuffer.getChannelData(0).set(float32Data);

    // Create source node
    const source = this.audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this.audioContext.destination);

    // Schedule on timeline
    const now = this.audioContext.currentTime;
    if (this.startTime < now) {
      // First chunk or gap in audio - start from now
      this.startTime = now;
    }

    source.start(this.startTime);

    // Calculate when this chunk ends
    const chunkEndTime = this.startTime + audioBuffer.duration;

    // Track for barge-in handling
    const sourceInfo = { source, endTime: chunkEndTime };
    this.scheduledSources.push(sourceInfo);

    // Auto-cleanup when source finishes
    source.onended = () => {
      const index = this.scheduledSources.indexOf(sourceInfo);
      if (index > -1) {
        this.scheduledSources.splice(index, 1);
      }
    };

    // Advance timeline
    this.startTime = chunkEndTime;

    // Stats
    this.totalChunksScheduled++;
    this.totalBytesScheduled += mulawBytes.length;

    return chunkEndTime;
  }

  /**
   * Schedule a mark to fire at a specific playback time.
   * The mark ack will be sent when the audio at that position plays.
   *
   * @param {string} markName - Name of the mark to ack
   * @param {number} fireTime - AudioContext time when mark should fire
   *                           (if not provided, fires when current audio ends)
   */
  scheduleMark(markName, fireTime = null) {
    if (!this.audioContext) {
      console.error('AudioContext not initialized');
      return;
    }

    // Default: fire when current scheduled audio ends
    if (fireTime === null) {
      fireTime = this.startTime > 0 ? this.startTime : this.audioContext.currentTime;
    }

    const now = this.audioContext.currentTime;
    const delayMs = Math.max(0, (fireTime - now) * 1000);

    // Store pending mark
    const markInfo = { markName, fireTime };
    this.pendingMarks.push(markInfo);

    // Schedule the callback
    setTimeout(() => {
      // Remove from pending
      const index = this.pendingMarks.indexOf(markInfo);
      if (index > -1) {
        this.pendingMarks.splice(index, 1);
      }

      // Fire callback
      if (this.onMarkFire) {
        this.onMarkFire(markName);
      }
    }, delayMs);
  }

  /**
   * Stop all scheduled audio immediately (barge-in).
   * Also cancels any pending marks.
   */
  stopAll() {
    // Stop all scheduled sources
    for (const { source } of this.scheduledSources) {
      try {
        source.stop();
      } catch (e) {
        // May already be stopped
      }
    }
    this.scheduledSources = [];

    // Cancel pending marks
    this.pendingMarks = [];

    // Reset timeline
    this.startTime = -1;

    console.log('Audio playback stopped (barge-in)');
  }

  /**
   * Check if audio is currently playing or scheduled.
   */
  isPlaying() {
    if (!this.audioContext) return false;
    return this.scheduledSources.length > 0 ||
           this.startTime > this.audioContext.currentTime;
  }

  /**
   * Get time until all scheduled audio finishes.
   */
  getRemainingTime() {
    if (!this.audioContext || this.startTime < 0) return 0;
    const remaining = this.startTime - this.audioContext.currentTime;
    return Math.max(0, remaining);
  }

  /**
   * Get current playback stats.
   */
  getStats() {
    return {
      chunksScheduled: this.totalChunksScheduled,
      bytesScheduled: this.totalBytesScheduled,
      pendingMarks: this.pendingMarks.length,
      scheduledSources: this.scheduledSources.length,
      remainingTime: this.getRemainingTime(),
      sampleRate: this.audioContext?.sampleRate || 0,
    };
  }

  /**
   * Reset stats (e.g., at start of new turn).
   */
  resetStats() {
    this.totalChunksScheduled = 0;
    this.totalBytesScheduled = 0;
  }

  /**
   * Clean up resources.
   */
  async dispose() {
    this.stopAll();
    if (this.audioContext) {
      await this.audioContext.close();
      this.audioContext = null;
    }
  }
}
