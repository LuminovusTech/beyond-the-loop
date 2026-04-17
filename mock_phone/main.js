/**
 * Main entry point for the Mock Phone
 *
 * Wires together:
 * - TwilioEmulator (WebSocket protocol)
 * - AudioPlaybackEngine (speaker output)
 * - AudioCaptureEngine (mic input)
 * - UI elements
 */

import { TwilioEmulator, ConnectionState } from './websocket-handler.js';
import { AudioPlaybackEngine } from './audio-playback.js';
import { AudioCaptureEngine } from './audio-capture.js';
import { logger } from './utils.js';

// ─────────────────────────────────────────────────────────────────
// DOM Elements
// ─────────────────────────────────────────────────────────────────

const elements = {
  // Config inputs
  backendUrl: document.getElementById('backend-url'),
  fromNumber: document.getElementById('from-number'),
  toNumber: document.getElementById('to-number'),

  // Buttons
  btnConnect: document.getElementById('btn-connect'),
  btnDisconnect: document.getElementById('btn-disconnect'),
  btnClearLog: document.getElementById('btn-clear-log'),

  // Status displays
  statusConnection: document.getElementById('status-connection'),
  statusCallId: document.getElementById('status-call-id'),
  statusMic: document.getElementById('status-mic'),
  statusAgent: document.getElementById('status-agent'),

  // Log
  logPanel: document.getElementById('log-panel'),

  // Stats
  statAudioIn: document.getElementById('stat-audio-in'),
  statAudioOut: document.getElementById('stat-audio-out'),
  statMarksIn: document.getElementById('stat-marks-in'),
  statMarksOut: document.getElementById('stat-marks-out'),
};

// ─────────────────────────────────────────────────────────────────
// Core Components
// ─────────────────────────────────────────────────────────────────

const emulator = new TwilioEmulator();
const playback = new AudioPlaybackEngine();
const capture = new AudioCaptureEngine();

// Track if agent is currently speaking
let agentSpeaking = false;

// ─────────────────────────────────────────────────────────────────
// UI Update Functions
// ─────────────────────────────────────────────────────────────────

function updateConnectionStatus(state) {
  const el = elements.statusConnection;
  el.textContent = state.charAt(0).toUpperCase() + state.slice(1);
  el.className = 'value ' + state;

  // Enable/disable controls
  const connected = state === ConnectionState.CONNECTED;
  const disconnected = state === ConnectionState.DISCONNECTED;

  elements.btnConnect.disabled = !disconnected;
  elements.btnDisconnect.disabled = disconnected;

  // Config inputs
  elements.backendUrl.disabled = !disconnected;
  elements.fromNumber.disabled = !disconnected;
  elements.toNumber.disabled = !disconnected;

  if (disconnected) {
    elements.statusCallId.textContent = '—';
    elements.statusAgent.textContent = '—';
    agentSpeaking = false;
  }
}

function updateMicStatus(status) {
  const el = elements.statusMic;
  if (status === 'active') {
    el.textContent = 'Active';
    el.className = 'value connected';
  } else if (status === 'denied') {
    el.textContent = 'Denied';
    el.className = 'value error';
  } else if (status === 'initializing') {
    el.textContent = 'Initializing...';
    el.className = 'value connecting';
  } else {
    el.textContent = 'Inactive';
    el.className = 'value disconnected';
  }
}

function updateAgentStatus(speaking) {
  agentSpeaking = speaking;
  const el = elements.statusAgent;
  if (speaking) {
    el.textContent = 'Speaking';
    el.className = 'value speaking';
  } else {
    el.textContent = 'Listening';
    el.className = 'value listening';
  }
}

function updateStats() {
  const wsStats = emulator.getStats();

  elements.statAudioIn.textContent = wsStats.mediaReceived.toString();
  elements.statAudioOut.textContent = wsStats.mediaSent.toString();
  elements.statMarksIn.textContent = wsStats.marksReceived.toString();
  elements.statMarksOut.textContent = wsStats.marksSent.toString();
}

// Update stats periodically
setInterval(updateStats, 500);

// ─────────────────────────────────────────────────────────────────
// Event Handlers
// ─────────────────────────────────────────────────────────────────

async function handleConnect() {
  // Initialize AudioContext for playback (requires user gesture)
  const playbackReady = await playback.initialize();
  if (!playbackReady) {
    logger.error('AUDIO', 'Failed to initialize audio playback');
    return;
  }

  // Initialize mic capture
  updateMicStatus('initializing');
  const captureReady = await capture.initialize();
  if (!captureReady) {
    logger.warn('MIC', 'Microphone not available - playback only mode');
    updateMicStatus('denied');
  } else {
    updateMicStatus('active');
  }

  // Configure emulator from UI
  emulator.configure({
    url: elements.backendUrl.value,
    fromNumber: elements.fromNumber.value,
    toNumber: elements.toNumber.value,
  });

  // Connect
  emulator.connect();
}

async function handleDisconnect() {
  emulator.disconnect();
  playback.stopAll();
  capture.stop();
  updateMicStatus('inactive');
}

// ─────────────────────────────────────────────────────────────────
// Wire up Twilio Emulator Callbacks
// ─────────────────────────────────────────────────────────────────

emulator.onStateChange = (state) => {
  updateConnectionStatus(state);

  if (state === ConnectionState.CONNECTED) {
    elements.statusCallId.textContent = emulator.callSid;
    updateAgentStatus(false);  // Start in listening mode
    logger.info('CALL', `Call started: ${emulator.callSid}`);

    // Start mic capture if available
    if (capture.getStatus().initialized) {
      capture.start();
      logger.info('MIC', 'Microphone capture started');
    }
  } else if (state === ConnectionState.DISCONNECTED) {
    capture.stop();
    updateMicStatus('inactive');
  }
};

emulator.onMedia = (mulawBytes) => {
  // Schedule audio for playback
  const endTime = playback.scheduleChunk(mulawBytes);

  // First audio chunk means agent started speaking
  if (!agentSpeaking) {
    updateAgentStatus(true);
    logger.info('AGENT', 'Agent started speaking');
  }
};

emulator.onMark = (markName) => {
  // Schedule mark ack to fire when current audio finishes
  playback.scheduleMark(markName);
  logger.debug('MARK', `Received mark: ${markName}`);
};

emulator.onClear = () => {
  // Barge-in: stop all audio immediately
  playback.stopAll();
  updateAgentStatus(false);
  logger.info('BARGE', 'Audio cleared (barge-in)');
};

emulator.onError = (error) => {
  logger.error('WS', `Error: ${error.message}`);
};

// ─────────────────────────────────────────────────────────────────
// Wire up Audio Playback Callbacks
// ─────────────────────────────────────────────────────────────────

playback.onMarkFire = (markName) => {
  // Send mark acknowledgment back to backend
  emulator.sendMarkAck(markName);
  logger.info('MARK', `Sent mark ack: ${markName}`);

  // If this is a turn end mark, agent is done speaking
  if (markName.startsWith('tts_turn_end_')) {
    updateAgentStatus(false);
    logger.info('AGENT', 'Agent finished speaking');
  }
};

// ─────────────────────────────────────────────────────────────────
// Wire up Audio Capture Callbacks
// ─────────────────────────────────────────────────────────────────

capture.onAudioChunk = (mulawBytes) => {
  // Send mic audio to backend
  emulator.sendMedia(mulawBytes);
};

// ─────────────────────────────────────────────────────────────────
// Wire up UI Events
// ─────────────────────────────────────────────────────────────────

elements.btnConnect.addEventListener('click', handleConnect);
elements.btnDisconnect.addEventListener('click', handleDisconnect);
elements.btnClearLog.addEventListener('click', () => logger.clear());

// ─────────────────────────────────────────────────────────────────
// Initialize
// ─────────────────────────────────────────────────────────────────

// Set up logger to write to log panel
logger.setLogElement(elements.logPanel);

// Initial status
updateConnectionStatus(ConnectionState.DISCONNECTED);
updateMicStatus('inactive');

logger.info('INIT', 'Mock Phone ready');
logger.info('INIT', 'Click "Connect" to start a call');
