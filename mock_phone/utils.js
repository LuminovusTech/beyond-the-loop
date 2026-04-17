/**
 * Utility functions for the local dev harness
 */

/**
 * Convert a base64 string to Uint8Array
 * @param {string} base64 - Base64 encoded string
 * @returns {Uint8Array} - Decoded bytes
 */
export function base64ToBytes(base64) {
  const binaryString = atob(base64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes;
}

/**
 * Convert Uint8Array to base64 string
 * @param {Uint8Array} bytes - Byte array
 * @returns {string} - Base64 encoded string
 */
export function bytesToBase64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

/**
 * Generate a dev-format call ID: devXXXXX-YYYY-MM-DDTHHMMSS
 *
 * This format:
 * - Clearly identifies dev harness calls with 'dev' prefix
 * - Has 5 random chars for uniqueness
 * - Includes timestamp for easy log correlation
 * - Short ID (first 8 chars, no hyphens) = 'devXXXXX'
 *
 * @returns {string} - Call ID in dev format
 */
export function generateDevCallId() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let random = '';
  for (let i = 0; i < 5; i++) {
    random += chars.charAt(Math.floor(Math.random() * chars.length));
  }

  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');

  // Format: devXXXXX-YYYY-MM-DDTHHMMSS
  return `dev${random}-${year}-${month}-${day}T${hours}${minutes}${seconds}`;
}

/**
 * Generate a stream ID based on call ID
 * @param {string} callId - The call ID
 * @returns {string} - Stream ID
 */
export function generateStreamId(callId) {
  // Replace 'dev' prefix with 'devstream-'
  if (callId.startsWith('dev')) {
    return `devstream-${callId.slice(3)}`;
  }
  return `stream-${callId}`;
}

/**
 * Format a timestamp for logging
 * @param {Date} date - Date to format (defaults to now)
 * @returns {string} - Formatted timestamp HH:MM:SS.mmm
 */
export function formatTime(date = new Date()) {
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  const millis = String(date.getMilliseconds()).padStart(3, '0');
  return `${hours}:${minutes}:${seconds}.${millis}`;
}

/**
 * Simple logger that writes to console and optionally to a DOM element
 */
export class Logger {
  constructor(logElement = null) {
    this.logElement = logElement;
    this.maxLines = 500;  // Keep last N lines in DOM
  }

  /**
   * Set the DOM element for log output
   * @param {HTMLElement} element - Element to append logs to
   */
  setLogElement(element) {
    this.logElement = element;
  }

  /**
   * Log a message
   * @param {string} level - Log level (INFO, WARN, ERROR, DEBUG)
   * @param {string} category - Event category
   * @param {string} message - Log message
   */
  log(level, category, message) {
    const time = formatTime();
    const logLine = `[${time}] [${level}] [${category}] ${message}`;

    // Console output
    switch (level) {
      case 'ERROR':
        console.error(logLine);
        break;
      case 'WARN':
        console.warn(logLine);
        break;
      case 'DEBUG':
        console.debug(logLine);
        break;
      default:
        console.log(logLine);
    }

    // DOM output
    if (this.logElement) {
      const lineEl = document.createElement('div');
      lineEl.className = `log-line log-${level.toLowerCase()}`;
      lineEl.textContent = logLine;
      this.logElement.appendChild(lineEl);

      // Trim old lines
      while (this.logElement.children.length > this.maxLines) {
        this.logElement.removeChild(this.logElement.firstChild);
      }

      // Auto-scroll to bottom
      this.logElement.scrollTop = this.logElement.scrollHeight;
    }
  }

  info(category, message) {
    this.log('INFO', category, message);
  }

  warn(category, message) {
    this.log('WARN', category, message);
  }

  error(category, message) {
    this.log('ERROR', category, message);
  }

  debug(category, message) {
    this.log('DEBUG', category, message);
  }

  /**
   * Clear the log display
   */
  clear() {
    if (this.logElement) {
      this.logElement.innerHTML = '';
    }
  }
}

// Default logger instance
export const logger = new Logger();
