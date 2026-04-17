# Mock Phone

Browser-based Twilio emulator for testing the voice agent locally without a real phone call, Twilio account, or tunnel.

## How it works

The mock phone opens a WebSocket directly to the server's `/twilio` endpoint and speaks the same protocol Twilio Media Streams uses (`connected`, `start`, `media`, `mark`, `clear`, `stop`). The server can't tell the difference.

Your browser's mic captures audio, encodes it to mulaw 8kHz, and sends it as `media` events. Agent audio comes back the same way and plays through your speakers via the Web Audio API.

## Usage

1. Start the server (no tunnel needed):
   ```bash
   python run.py --no-tunnel
   ```

2. Open http://localhost:8080/mock-phone in your browser

3. Click **Connect** and allow microphone access

## Troubleshooting

**No audio playback?** Check the browser console. AudioContext requires a user gesture (the Connect button click satisfies this).

**Mic not working?** Allow microphone permission when prompted. Check your browser's site permissions.

**Connection refused?** Make sure the server is running (`python run.py --no-tunnel`).
