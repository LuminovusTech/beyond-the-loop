#!/usr/bin/env python3
"""
Start the voice agent — tunnel + server in one command.

Launches the zrok tunnel and uvicorn server together.
Ctrl+C stops both.

Usage:
  python run.py              # Start tunnel + server
  python run.py --no-tunnel  # Server only (if you have your own tunnel/URL)
  python run.py -v           # Show dev-iteration signals (barge-in, commit, etc.)
  python run.py -vv          # Raw firehose — disables the TUI, dumps all logs
"""

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE = ".quickstart_state.json"
ENV_FILE = ".env"
DEFAULT_PORT = 8080

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_phone(number: str) -> str:
    if number.startswith("+1") and len(number) == 12:
        return f"+1 ({number[2:5]}) {number[5:8]}-{number[8:]}"
    return number


def _load_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _get_env_value(key: str) -> str | None:
    if not os.path.exists(ENV_FILE):
        return None
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                v = v.strip()
                return v if v else None
    return None


def _find_zrok_process() -> int | None:
    """Find an existing zrok share process."""
    try:
        if platform.system().lower() == "windows":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq zrok.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                if "zrok" in line.lower():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        try:
                            return int(parts[1].strip('" '))
                        except ValueError:
                            continue
        else:
            result = subprocess.run(
                ["pgrep", "-f", "zrok share"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    return int(result.stdout.strip().splitlines()[0])
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_verbosity(argv: list[str]) -> int:
    """Parse -v / -vv / -vvv / --verbose flags. Returns 0..2 (clamped)."""
    v = 0
    for arg in argv[1:]:
        if arg == "--verbose":
            v += 1
        elif arg.startswith("-") and not arg.startswith("--"):
            # -v, -vv, -vvv — count the v's
            body = arg[1:]
            if body and all(c == "v" for c in body):
                v += len(body)
    return min(2, v)


def main():
    no_tunnel = "--no-tunnel" in sys.argv
    verbosity = _parse_verbosity(sys.argv)

    state = _load_state()

    if not no_tunnel:
        if not state or "zrok" not in state:
            print("\n  Not set up yet. Run: python setup/quickstart.py\n")
            sys.exit(1)

        share_name = state["zrok"]["share_name"]
        tunnel_url = state["zrok"]["tunnel_url"]
        zrok_binary = shutil.which("zrok") or shutil.which("zrok.exe")

        if not zrok_binary:
            print("\n  zrok not found. Install it or use --no-tunnel\n")
            sys.exit(1)

    port = int(_get_env_value("SERVER_PORT") or DEFAULT_PORT)
    host = _get_env_value("SERVER_HOST") or "0.0.0.0"

    tunnel_proc = None
    server_proc = None

    def cleanup(signum=None, frame=None):
        """Shut down both processes."""
        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()

        if tunnel_proc and tunnel_proc.poll() is None:
            tunnel_proc.terminate()
            try:
                tunnel_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel_proc.kill()

        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # --- Start tunnel ---
    if not no_tunnel:
        # Stop any existing zrok share process
        existing_pid = _find_zrok_process()
        if existing_pid:
            try:
                os.kill(existing_pid, signal.SIGTERM)
                time.sleep(1)
            except OSError:
                pass

        print(f"\n  Starting tunnel: {tunnel_url}")
        tunnel_proc = subprocess.Popen(
            [zrok_binary, "share", "reserved", share_name, "--headless"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Give it a moment to connect
        time.sleep(2)
        if tunnel_proc.poll() is not None:
            output = tunnel_proc.stdout.read() if tunnel_proc.stdout else ""
            print(f"  Tunnel failed to start: {output.strip()}")
            sys.exit(1)

        print(f"  Tunnel running (PID {tunnel_proc.pid})")

    # --- Start server ---
    print(f"  Starting server on {host}:{port}")

    # Show phone number if available
    phone_number = None
    if state:
        phone_number = state.get("twilio", {}).get("phone_number")

    if phone_number:
        formatted = _format_phone(phone_number)
        print(f"\n  Call {formatted} to talk to the agent.")
    else:
        print(f"\n  Call your Twilio phone number to talk to the agent.")
    print(f"  Press Ctrl+C to stop.\n")

    server_env = {**os.environ, "LOG_VERBOSITY": str(verbosity)}
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app",
         "--host", host, "--port", str(port), "--log-level", "info"],
        env=server_env,
    )

    # Wait for either process to exit
    try:
        while True:
            # Check tunnel health
            if tunnel_proc and tunnel_proc.poll() is not None:
                print("\n  Tunnel process exited unexpectedly. Shutting down.")
                cleanup()

            # Check server health
            if server_proc.poll() is not None:
                print("\n  Server exited.")
                cleanup()

            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
