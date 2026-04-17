/**
 * Twilio Media Streams Protocol Emulator
 *
 * Emulates the Twilio WebSocket protocol so the backend thinks
 * it's receiving a real phone call.
 *
 * Protocol messages:
 * - SEND: connected, start, media (mic audio), mark (acks), stop
 * - RECEIVE: media (agent audio), mark (playback positions), clear (barge-in)
 *
 * Reference: telephony/routes.py (twilio_websocket handler)
 */

import { base64ToBytes, bytesToBase64, generateDevCallId, generateStreamId, logger } from './utils.js';

/**
 * Connection states
 */
export const ConnectionState = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  ERROR: 'error',
};

/**
 * SignalWire Protocol Emulator
 */
export class TwilioEmulator {
  /**
   * @param {Object} config - Configuration options
   * @param {string} config.url - Backend WebSocket URL
   * @param {string} config.fromNumber - Caller phone number
   * @param {string} config.toNumber - DID being called
   */
  constructor(config = {}) {
    this.url = config.url || 'ws://localhost:8080/twilio';
    this.fromNumber = config.fromNumber || '+15108675309';
    this.toNumber = config.toNumber || '+15513249448';

    // Connection state
    this.ws = null;
    this.state = ConnectionState.DISCONNECTED;
    this.callSid = null;
    this.streamSid = null;

    // Callbacks
    this.onStateChange = null;   // (state) => void
    this.onMedia = null;         // (mulawBytes: Uint8Array) => void
    this.onMark = null;          // (markName: string) => void
    this.onClear = null;         // () => void
    this.onError = null;         // (error: Error) => void

    // Stats
    this.stats = {
      mediaReceived: 0,
      mediaSent: 0,
      marksReceived: 0,
      marksSent: 0,
      bytesReceived: 0,
      bytesSent: 0,
    };
  }

  /**
   * Update configuration (must be disconnected)
   */
  configure(config) {
    if (this.state !== ConnectionState.DISCONNECTED) {
      throw new Error('Cannot configure while connected');
    }
    if (config.url) this.url = config.url;
    if (config.fromNumber) this.fromNumber = config.fromNumber;
    if (config.toNumber) this.toNumber = config.toNumber;
  }

  /**
   * Connect to the backend and initiate the call.
   */
  connect() {
    if (this.state !== ConnectionState.DISCONNECTED) {
      logger.warn('WS', 'Already connected or connecting');
      return;
    }

    // Generate call identifiers
    this.callSid = generateDevCallId();
    this.streamSid = generateStreamId(this.callSid);

    logger.info('WS', `Connecting to ${this.url}`);
    logger.info('WS', `Call ID: ${this.callSid}`);
    logger.info('WS', `From: ${this.fromNumber} → To: ${this.toNumber}`);

    this._setState(ConnectionState.CONNECTING);

    // Reset stats
    this.stats = {
      mediaReceived: 0,
      mediaSent: 0,
      marksReceived: 0,
      marksSent: 0,
      bytesReceived: 0,
      bytesSent: 0,
    };

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => this._handleOpen();
      this.ws.onmessage = (event) => this._handleMessage(event);
      this.ws.onclose = (event) => this._handleClose(event);
      this.ws.onerror = (event) => this._handleError(event);
    } catch (err) {
      logger.error('WS', `Failed to create WebSocket: ${err.message}`);
      this._setState(ConnectionState.ERROR);
      this.onError?.(err);
    }
  }

  /**
   * Disconnect from the backend.
   */
  disconnect() {
    if (!this.ws) return;

    logger.info('WS', 'Disconnecting...');

    // Send stop event before closing
    if (this.ws.readyState === WebSocket.OPEN) {
      this._send({ event: 'stop' });
    }

    this.ws.close();
    this.ws = null;
    this._setState(ConnectionState.DISCONNECTED);
  }

  /**
   * Send microphone audio to the backend.
   * @param {Uint8Array} mulawBytes - µ-law encoded audio
   */
  sendMedia(mulawBytes) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;

    this._send({
      event: 'media',
      streamSid: this.streamSid,
      media: {
        payload: bytesToBase64(mulawBytes),
      },
    });

    this.stats.mediaSent++;
    this.stats.bytesSent += mulawBytes.length;
  }

  /**
   * Send a mark acknowledgment to the backend.
   * @param {string} markName - Name of the mark being acknowledged
   */
  sendMarkAck(markName) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;

    logger.debug('WS', `Sending mark ack: ${markName}`);

    this._send({
      event: 'mark',
      mark: {
        name: markName,
      },
    });

    this.stats.marksSent++;
  }

  /**
   * Get current connection stats.
   */
  getStats() {
    return { ...this.stats };
  }

  // ─────────────────────────────────────────────────────────────────
  // Private methods
  // ─────────────────────────────────────────────────────────────────

  _setState(state) {
    this.state = state;
    this.onStateChange?.(state);
  }

  _send(data) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(data));
  }

  _handleOpen() {
    logger.info('WS', 'WebSocket connected');

    // Send connected event (Twilio Media Streams protocol)
    this._send({
      event: 'connected',
      protocol: 'wss',
      version: '2.0.0',
    });

    // Send start event with call metadata
    this._send({
      event: 'start',
      start: {
        callSid: this.callSid,
        streamSid: this.streamSid,
        mediaFormat: {
          encoding: 'audio/x-mulaw',
          sampleRate: 8000,
          channels: 1,
        },
        customParameters: {
          from: this.fromNumber,
          to: this.toNumber,
          env: 'development',
        },
      },
    });

    logger.info('WS', 'Sent connected + start events');
    this._setState(ConnectionState.CONNECTED);
  }

  _handleMessage(event) {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (err) {
      logger.error('WS', `Failed to parse message: ${err.message}`);
      return;
    }

    switch (data.event) {
      case 'media':
        this._handleMediaEvent(data);
        break;

      case 'mark':
        this._handleMarkEvent(data);
        break;

      case 'clear':
        this._handleClearEvent(data);
        break;

      default:
        logger.debug('WS', `Unknown event: ${data.event}`);
    }
  }

  _handleMediaEvent(data) {
    const payload = data.media?.payload;
    if (!payload) {
      logger.warn('WS', 'Media event missing payload');
      return;
    }

    const audioBytes = base64ToBytes(payload);
    this.stats.mediaReceived++;
    this.stats.bytesReceived += audioBytes.length;

    // Forward to callback
    this.onMedia?.(audioBytes);
  }

  _handleMarkEvent(data) {
    const markName = data.mark?.name;
    if (!markName) {
      logger.warn('WS', 'Mark event missing name');
      return;
    }

    logger.debug('WS', `Received mark: ${markName}`);
    this.stats.marksReceived++;

    // Forward to callback
    this.onMark?.(markName);
  }

  _handleClearEvent(data) {
    logger.info('WS', 'Received clear event (barge-in)');
    this.onClear?.();
  }

  _handleClose(event) {
    logger.info('WS', `WebSocket closed: code=${event.code}, reason=${event.reason || 'none'}`);
    this.ws = null;
    this._setState(ConnectionState.DISCONNECTED);
  }

  _handleError(event) {
    logger.error('WS', 'WebSocket error');
    this._setState(ConnectionState.ERROR);
    this.onError?.(new Error('WebSocket error'));
  }
}
