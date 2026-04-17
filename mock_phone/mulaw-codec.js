/**
 * µ-law (G.711) Codec for Telephony Audio
 *
 * µ-law is the standard audio encoding for North American telephony.
 * It compresses 16-bit PCM to 8-bit with logarithmic companding,
 * providing better dynamic range for voice at low bitrates.
 *
 * Sample rate: 8000 Hz (telephony standard)
 * Encoding: 8-bit unsigned
 */

// µ-law constants
const MULAW_BIAS = 0x84;  // 132
const MULAW_MAX = 0x7FFF; // 32767 (max 16-bit signed value)

// Pre-computed decode table for fast decoding
// µ-law byte -> 16-bit PCM value
const MULAW_DECODE_TABLE = new Int16Array(256);

// Initialize decode table
(function initDecodeTables() {
  for (let i = 0; i < 256; i++) {
    // Complement the input (µ-law is stored complemented)
    const mulaw = ~i & 0xFF;

    // Extract sign, exponent, and mantissa
    const sign = mulaw & 0x80;
    const exponent = (mulaw >> 4) & 0x07;
    const mantissa = mulaw & 0x0F;

    // Reconstruct the linear value
    // Formula: ((mantissa << 3) + BIAS) << exponent - BIAS
    let sample = ((mantissa << 3) + MULAW_BIAS) << exponent;
    sample -= MULAW_BIAS;

    // Apply sign
    MULAW_DECODE_TABLE[i] = sign ? -sample : sample;
  }
})();

/**
 * Encode a single 16-bit PCM sample to µ-law
 * @param {number} pcm16 - 16-bit signed PCM sample (-32768 to 32767)
 * @returns {number} - 8-bit µ-law encoded value (0-255)
 */
export function encodeMulawSample(pcm16) {
  // Get the sign and make sample positive
  const sign = (pcm16 < 0) ? 0x80 : 0;
  if (pcm16 < 0) pcm16 = -pcm16;

  // Add bias and clamp to max
  pcm16 = Math.min(pcm16 + MULAW_BIAS, MULAW_MAX);

  // Find the exponent (position of highest bit)
  let exponent = 7;
  let mask = 0x4000;
  while (!(pcm16 & mask) && exponent > 0) {
    exponent--;
    mask >>= 1;
  }

  // Extract 4-bit mantissa (the bits after the leading 1)
  const mantissa = (pcm16 >> (exponent + 3)) & 0x0F;

  // Combine sign, exponent, mantissa and complement
  const mulawByte = ~(sign | (exponent << 4) | mantissa) & 0xFF;

  return mulawByte;
}

/**
 * Decode a single µ-law sample to 16-bit PCM
 * @param {number} mulaw - 8-bit µ-law value (0-255)
 * @returns {number} - 16-bit signed PCM sample
 */
export function decodeMulawSample(mulaw) {
  return MULAW_DECODE_TABLE[mulaw & 0xFF];
}

/**
 * Encode PCM Int16Array to µ-law Uint8Array
 * @param {Int16Array} pcm16Array - Array of 16-bit signed PCM samples
 * @returns {Uint8Array} - Array of 8-bit µ-law encoded values
 */
export function encodeMulaw(pcm16Array) {
  const mulaw = new Uint8Array(pcm16Array.length);
  for (let i = 0; i < pcm16Array.length; i++) {
    mulaw[i] = encodeMulawSample(pcm16Array[i]);
  }
  return mulaw;
}

/**
 * Decode µ-law Uint8Array to PCM Int16Array
 * @param {Uint8Array} mulawArray - Array of 8-bit µ-law values
 * @returns {Int16Array} - Array of 16-bit signed PCM samples
 */
export function decodeMulaw(mulawArray) {
  const pcm16 = new Int16Array(mulawArray.length);
  for (let i = 0; i < mulawArray.length; i++) {
    pcm16[i] = MULAW_DECODE_TABLE[mulawArray[i]];
  }
  return pcm16;
}

/**
 * Convert Float32Array (Web Audio format) to Int16Array (PCM)
 * Web Audio uses normalized floats in range [-1, 1]
 * @param {Float32Array} float32Array - Normalized audio samples
 * @returns {Int16Array} - 16-bit PCM samples
 */
export function float32ToPcm16(float32Array) {
  const pcm16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    // Clamp to [-1, 1] and scale to 16-bit range
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return pcm16;
}

/**
 * Convert Int16Array (PCM) to Float32Array (Web Audio format)
 * @param {Int16Array} pcm16Array - 16-bit PCM samples
 * @returns {Float32Array} - Normalized audio samples [-1, 1]
 */
export function pcm16ToFloat32(pcm16Array) {
  const float32 = new Float32Array(pcm16Array.length);
  for (let i = 0; i < pcm16Array.length; i++) {
    float32[i] = pcm16Array[i] / 32768;
  }
  return float32;
}

/**
 * Encode Float32Array directly to µ-law
 * Convenience function for mic capture
 * @param {Float32Array} float32Array - Normalized audio samples
 * @returns {Uint8Array} - µ-law encoded bytes
 */
export function encodeFloat32ToMulaw(float32Array) {
  const pcm16 = float32ToPcm16(float32Array);
  return encodeMulaw(pcm16);
}

/**
 * Decode µ-law directly to Float32Array
 * Convenience function for audio playback
 * @param {Uint8Array} mulawArray - µ-law encoded bytes
 * @returns {Float32Array} - Normalized audio samples for Web Audio
 */
export function decodeMulawToFloat32(mulawArray) {
  const pcm16 = decodeMulaw(mulawArray);
  return pcm16ToFloat32(pcm16);
}
