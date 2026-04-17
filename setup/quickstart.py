#!/usr/bin/env python3
"""
Quickstart Wizard

One command to go from zero to a working inbound voice agent:
  1. Collect API keys (Deepgram, OpenAI)
  2. Configure Twilio (credentials, phone number)
  3. Start a zrok tunnel for a stable public URL
  4. Wire the Twilio webhook to the tunnel URL

Usage:
  python setup/quickstart.py                 # Full interactive setup
  python setup/quickstart.py --status        # Show current configuration
  python setup/quickstart.py --update-url    # Update Twilio webhook to current tunnel URL
  python setup/quickstart.py --teardown      # Release tunnel + clear webhook

After setup, start the agent with:
  python run.py

Prerequisites:
  - Python 3.12+
  - zrok CLI installed (https://zrok.io)
  - A free zrok account (https://myzrok.io)
  - A Twilio account (https://console.twilio.com)
"""

import argparse
import getpass
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from pathlib import Path

# Repo root is the parent of setup/ — resolve everything relative to it
# so the wizard works regardless of the user's current working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = str(REPO_ROOT / ".quickstart_state.json")
ENV_FILE = str(REPO_ROOT / ".env")
ENV_EXAMPLE_FILE = str(REPO_ROOT / ".env.example")
DEFAULT_PORT = 8080

# ---------------------------------------------------------------------------
# Console output helpers
# ---------------------------------------------------------------------------

def print_header(text: str):
    width = len(text) + 4
    print(f"\n  {'=' * width}")
    print(f"  | {text} |")
    print(f"  {'=' * width}")


def print_section(text: str):
    print(f"\n  --- {text} {'-' * max(0, 44 - len(text))}")


def print_ok(text: str):
    print(f"  {text}")


def print_error(text: str):
    print(f"\n  ERROR: {text}", file=sys.stderr)


def print_warn(text: str):
    print(f"  NOTE: {text}")


def print_success(text: str):
    print(f"  {text}")


# ---------------------------------------------------------------------------
# User input helpers
# ---------------------------------------------------------------------------

def prompt(label: str, default: str = "") -> str:
    if default:
        raw = input(f"  {label} [{default}]: ").strip()
        return raw if raw else default
    return input(f"  {label}: ").strip()


def prompt_secret(label: str) -> str:
    return getpass.getpass(f"  {label}: ")


def prompt_choice(options: list[str], default: int = 1) -> int:
    print()
    for i, option in enumerate(options, 1):
        print(f"    {i}. {option}")
    print()
    while True:
        raw = input(f"  Choice [{default}]: ").strip()
        if not raw:
            return default
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(options)}.")


def prompt_confirm(text: str, default_yes: bool = False) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    raw = input(f"  {text} {suffix}: ").strip().lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Phone number formatting
# ---------------------------------------------------------------------------

def format_phone(number: str) -> str:
    """Format E.164 number for display: +14155550123 -> +1 (415) 555-0123"""
    if number.startswith("+1") and len(number) == 12:
        return f"+1 ({number[2:5]}) {number[5:8]}-{number[8:]}"
    return number


# ---------------------------------------------------------------------------
# State management (.quickstart_state.json)
# ---------------------------------------------------------------------------

def load_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state: dict):
    state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    if "created_at" not in state:
        state["created_at"] = state["last_updated_at"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# .env file management
# ---------------------------------------------------------------------------

def read_env_file() -> str:
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            return f.read()
    if os.path.exists(ENV_EXAMPLE_FILE):
        with open(ENV_EXAMPLE_FILE) as f:
            content = f.read()
        with open(ENV_FILE, "w") as f:
            f.write(content)
        return content
    content = "# Created by quickstart.py\n"
    with open(ENV_FILE, "w") as f:
        f.write(content)
    return content


def get_env_value(key: str) -> str | None:
    content = read_env_file()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            v = v.strip()
            return v if v else None
    return None


def update_env_file(updates: dict[str, str]):
    content = read_env_file()
    lines = content.splitlines()
    updated_keys = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            updated_keys.add(key)

    new_keys = set(updates.keys()) - updated_keys
    if new_keys:
        if lines and lines[-1].strip():
            lines.append("")
        for key in sorted(new_keys):
            lines.append(f"{key}={updates[key]}")

    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines))
        if not lines[-1].endswith("\n"):
            f.write("\n")


def _is_placeholder(value: str | None) -> bool:
    """Check if an env value is unset or still a placeholder."""
    if not value:
        return True
    placeholders = {"your_deepgram_api_key", "your_openai_api_key", "your_elevenlabs_api_key", "your_key_here"}
    return value.strip().lower() in placeholders


# ---------------------------------------------------------------------------
# API key collection
# ---------------------------------------------------------------------------

def collect_api_keys():
    """Prompt for Deepgram, OpenAI, and ElevenLabs API keys if not already set."""
    print_section("API Keys")

    env_updates = {}

    # Deepgram
    dg_key = get_env_value("DEEPGRAM_API_KEY")
    if _is_placeholder(dg_key):
        print()
        print_ok("Deepgram API key required (for speech-to-text).")
        print_ok("Get a free key: https://console.deepgram.com")
        print()
        dg_key = prompt_secret("Deepgram API Key")
        if not dg_key.strip():
            print_error("Deepgram API key is required.")
            sys.exit(1)
        env_updates["DEEPGRAM_API_KEY"] = dg_key.strip()
    else:
        print_ok(f"Deepgram API key:    ...{dg_key[-6:]}")

    # OpenAI
    oai_key = get_env_value("OPENAI_API_KEY")
    if _is_placeholder(oai_key):
        print()
        print_ok("OpenAI API key required (for the LLM).")
        print_ok("Get a key: https://platform.openai.com/")
        print()
        oai_key = prompt_secret("OpenAI API Key")
        if not oai_key.strip():
            print_error("OpenAI API key is required.")
            sys.exit(1)
        env_updates["OPENAI_API_KEY"] = oai_key.strip()
    else:
        print_ok(f"OpenAI API key:      ...{oai_key[-6:]}")

    # ElevenLabs
    el_key = get_env_value("ELEVENLABS_API_KEY")
    if _is_placeholder(el_key):
        print()
        print_ok("ElevenLabs API key required (for text-to-speech).")
        print_ok("Get a free key: https://elevenlabs.io")
        print()
        el_key = prompt_secret("ElevenLabs API Key")
        if not el_key.strip():
            print_error("ElevenLabs API key is required.")
            sys.exit(1)
        env_updates["ELEVENLABS_API_KEY"] = el_key.strip()
    else:
        print_ok(f"ElevenLabs API key:  ...{el_key[-6:]}")

    if env_updates:
        update_env_file(env_updates)
        print()
        print_ok("Saved to .env")


# ---------------------------------------------------------------------------
# zrok: binary detection & install guidance
# ---------------------------------------------------------------------------

def _get_zrok_binary() -> str | None:
    """Find the zrok binary. Returns the path or None."""
    return shutil.which("zrok") or shutil.which("zrok.exe")


def _get_system_info() -> tuple[str, str]:
    """Returns (os_name, arch) for install guidance."""
    os_name = platform.system().lower()  # 'darwin', 'linux', 'windows'
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    return os_name, arch


def check_zrok_installed() -> str:
    """Check if zrok is installed. Returns the binary path or exits with install instructions."""
    binary = _get_zrok_binary()
    if binary:
        return binary

    os_name, _ = _get_system_info()
    print_error("zrok is not installed.")
    print()
    if os_name == "darwin":
        print_ok("  Install on macOS:")
        print_ok("    brew install zrok")
    elif os_name == "linux":
        print_ok("  Install on Linux:")
        print_ok("    curl -sSf https://get.openziti.io/install.bash | sudo bash -s zrok")
    elif os_name == "windows":
        print_ok("  Install on Windows:")
        print_ok("    Download from: https://github.com/openziti/zrok/releases")
        print_ok("    Extract zrok.exe and add to your PATH.")
    print()
    print_ok("  More info: https://docs.zrok.io/docs/guides/install/")
    sys.exit(1)


def check_zrok_version(binary: str) -> str | None:
    """Get the zrok version string, or None on failure."""
    try:
        result = subprocess.run(
            [binary, "version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# zrok: environment (enable/status)
# ---------------------------------------------------------------------------

def check_zrok_enabled(binary: str) -> bool:
    """Check if zrok is enabled on this machine (i.e., linked to an account)."""
    try:
        result = subprocess.run(
            [binary, "status"],
            capture_output=True, text=True, timeout=10,
        )
        # "zrok status" exits 0 and prints config/environment info when enabled.
        # When NOT enabled, it prints an error about loading the environment.
        if result.returncode != 0:
            return False
        combined = result.stdout + result.stderr
        if "unable to load" in combined.lower() or "not enabled" in combined.lower():
            return False
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def enable_zrok(binary: str):
    """Walk the user through enabling zrok (linking this machine to their account)."""
    print()
    print_ok("zrok needs to be linked to your account on this machine (one-time setup).")
    print()
    print_ok("1. Create a free account at: https://myzrok.io")
    print_ok("2. After signing in, copy your enable token from your account page.")
    print()

    token = prompt("zrok enable token")
    if not token.strip():
        print_error("Enable token is required.")
        sys.exit(1)

    sys.stdout.write("  Enabling zrok... ")
    sys.stdout.flush()

    try:
        result = subprocess.run(
            [binary, "enable", token.strip()],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print("FAILED")
            error_text = (result.stderr or result.stdout).strip()
            if "already enabled" in error_text.lower():
                print_ok("  Already enabled! Continuing.")
            else:
                print_error(f"zrok enable failed: {error_text}")
                sys.exit(1)
        else:
            print("OK")
    except subprocess.TimeoutExpired:
        print("FAILED")
        print_error("zrok enable timed out.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# zrok: reserved shares
# ---------------------------------------------------------------------------

def _generate_share_name() -> str:
    """Generate a short, unique share name for the reserved tunnel."""
    return f"voiceagent{secrets.token_hex(4)}"


def create_reserved_share(binary: str, port: int, share_name: str | None = None) -> str:
    """Create a zrok reserved share. Returns the share name (used as the URL slug)."""
    if not share_name:
        share_name = _generate_share_name()

    sys.stdout.write(f"  Reserving tunnel ({share_name})... ")
    sys.stdout.flush()

    try:
        result = subprocess.run(
            [
                binary, "reserve", "public",
                f"http://localhost:{port}",
                "--unique-name", share_name,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout).strip()
            print("FAILED")
            if "already exists" in error_text.lower():
                print_ok(f"  Share '{share_name}' already exists. Reusing it.")
                return share_name
            print_error(f"zrok reserve failed: {error_text}")
            sys.exit(1)
        print("OK")
        return share_name
    except subprocess.TimeoutExpired:
        print("FAILED")
        print_error("zrok reserve timed out.")
        sys.exit(1)


def release_reserved_share(binary: str, share_name: str) -> bool:
    """Release a zrok reserved share."""
    sys.stdout.write(f"  Releasing tunnel ({share_name})... ")
    sys.stdout.flush()
    try:
        result = subprocess.run(
            [binary, "release", share_name],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout).strip()
            print("FAILED")
            print_warn(f"Could not release: {error_text}")
            return False
        print("OK")
        return True
    except subprocess.TimeoutExpired:
        print("FAILED")
        return False


def get_share_url(share_name: str) -> str:
    """Construct the public URL for a reserved share."""
    return f"https://{share_name}.share.zrok.io"


# ---------------------------------------------------------------------------
# zrok: tunnel subprocess management
# ---------------------------------------------------------------------------

def find_zrok_process() -> int | None:
    """Find an existing zrok share process. Returns the PID or None."""
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq zrok.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                if "zrok" in line.lower():
                    # CSV format: "zrok.exe","1234","Console","1","12,345 K"
                    parts = line.split(",")
                    if len(parts) >= 2:
                        pid_str = parts[1].strip('" ')
                        try:
                            return int(pid_str)
                        except ValueError:
                            continue
        else:
            result = subprocess.run(
                ["pgrep", "-f", "zrok share"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Return the first PID found
                pid_str = result.stdout.strip().splitlines()[0]
                try:
                    return int(pid_str)
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def stop_zrok_tunnel():
    """Stop any running zrok share process."""
    pid = find_zrok_process()
    if pid:
        sys.stdout.write(f"  Stopping existing tunnel (PID {pid})... ")
        sys.stdout.flush()
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            print("OK")
        except OSError:
            print("FAILED (may already be stopped)")
    else:
        print_ok("  No existing tunnel process found.")


def start_zrok_tunnel(binary: str, share_name: str) -> subprocess.Popen:
    """Start the zrok share reserved process in the background.

    Returns the Popen object. The tunnel URL is already known from the share name.
    """
    sys.stdout.write("  Starting tunnel... ")
    sys.stdout.flush()

    # Stop any existing tunnel first
    existing_pid = find_zrok_process()
    if existing_pid:
        try:
            os.kill(existing_pid, signal.SIGTERM)
            time.sleep(1)
        except OSError:
            pass

    # Start the tunnel
    proc = subprocess.Popen(
        [binary, "share", "reserved", share_name, "--headless"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Give it a moment to start and check it didn't immediately crash
    time.sleep(2)
    if proc.poll() is not None:
        output = proc.stdout.read() if proc.stdout else ""
        print("FAILED")
        print_error(f"Tunnel exited immediately: {output.strip()}")
        sys.exit(1)

    print(f"OK (PID {proc.pid})")
    return proc


# ---------------------------------------------------------------------------
# Twilio: credential handling
# ---------------------------------------------------------------------------

def get_twilio_client():
    """Get or prompt for Twilio credentials, validate, return (client, sid, token, type)."""
    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException
    except ImportError:
        print_error("Twilio SDK not installed. Run: pip install twilio")
        sys.exit(1)

    existing_sid = get_env_value("TWILIO_ACCOUNT_SID")
    existing_token = get_env_value("TWILIO_AUTH_TOKEN")

    account_sid = None
    auth_token = None

    if existing_sid and existing_token:
        print_ok(f"Found Twilio credentials in .env (Account: {existing_sid[:8]}...)")
        if prompt_confirm("Use existing credentials?", default_yes=True):
            account_sid = existing_sid
            auth_token = existing_token

    if not account_sid:
        print()
        print_ok("You'll need your Account SID and Auth Token from:")
        print_ok("https://console.twilio.com")
        print()
        account_sid = prompt("Account SID")
        auth_token = prompt_secret("Auth Token")

    if not account_sid or not auth_token:
        print_error("Account SID and Auth Token are required.")
        sys.exit(1)

    sys.stdout.write("  Validating credentials... ")
    sys.stdout.flush()

    try:
        client = Client(account_sid, auth_token)
        account = client.api.accounts(account_sid).fetch()
        account_type = getattr(account, "type", "Unknown")
        friendly_name = getattr(account, "friendly_name", "")
        print(f'OK ("{friendly_name}", {account_type})')

        if account_type == "Trial":
            print_warn("Trial accounts play a Twilio disclaimer before connecting.")
            print_ok("  Upgrade at https://console.twilio.com to remove it.")

        return client, account_sid, auth_token, account_type

    except TwilioRestException:
        print("FAILED")
        print_error("Could not authenticate. Check your Account SID and Auth Token.")
        sys.exit(1)
    except Exception as e:
        print("FAILED")
        print_error(f"Could not reach Twilio API: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Twilio: phone number selection
# ---------------------------------------------------------------------------

def select_phone_number(client) -> tuple[str, str, bool]:
    """List voice-capable numbers or purchase new. Returns (number, sid, provisioned)."""
    from twilio.base.exceptions import TwilioRestException

    sys.stdout.write("  Fetching your phone numbers... ")
    sys.stdout.flush()
    numbers = client.incoming_phone_numbers.list()
    voice_numbers = [n for n in numbers if getattr(n, "capabilities", {}).get("voice", False)]
    print("OK")

    options = []
    for n in voice_numbers:
        label = format_phone(n.phone_number)
        if n.friendly_name and n.friendly_name != n.phone_number:
            label += f'  "{n.friendly_name}"'
        options.append(label)

    options.append("Search for a new number to purchase")

    if voice_numbers:
        print()
        print_ok("Voice-capable numbers on your account:")
    else:
        print()
        print_ok("No voice-capable numbers found on your account.")

    choice = prompt_choice(options, default=1)

    if choice <= len(voice_numbers):
        selected = voice_numbers[choice - 1]
        print_ok(f"  Selected: {format_phone(selected.phone_number)}")
        return selected.phone_number, selected.sid, False
    else:
        return _search_and_purchase(client)


def _search_and_purchase(client) -> tuple[str, str, bool]:
    """Search for available numbers and purchase one."""
    from twilio.base.exceptions import TwilioRestException

    area_code = prompt("Area code (or Enter to skip)")

    sys.stdout.write("  Searching... ")
    sys.stdout.flush()

    kwargs = {"limit": 5}
    if area_code:
        kwargs["area_code"] = area_code

    try:
        available = client.available_phone_numbers("US").local.list(**kwargs)
    except TwilioRestException as e:
        print("FAILED")
        print_error(f"Could not search: {e}")
        sys.exit(1)

    if not available:
        print("no results")
        print_error("No numbers found. Try a different area code.")
        sys.exit(1)

    print(f"found {len(available)}")

    options = []
    for n in available:
        label = format_phone(n.phone_number)
        locality = getattr(n, "locality", "")
        region = getattr(n, "region", "")
        if locality and region:
            label += f"  ({locality}, {region})"
        options.append(label)

    choice = prompt_choice(options, default=1)
    selected = available[choice - 1]

    print()
    if not prompt_confirm(f"Purchase {format_phone(selected.phone_number)}?", default_yes=False):
        print_ok("  Cancelled.")
        sys.exit(0)

    sys.stdout.write("  Purchasing... ")
    sys.stdout.flush()
    try:
        purchased = client.incoming_phone_numbers.create(phone_number=selected.phone_number)
        print("OK")
        print_ok(f"  {format_phone(purchased.phone_number)} is now yours.")
        return purchased.phone_number, purchased.sid, True
    except TwilioRestException as e:
        print("FAILED")
        error_msg = str(e)
        if "balance" in error_msg.lower() or "fund" in error_msg.lower():
            print_error("Insufficient Twilio balance to purchase a number.")
            print_ok("  Add funds: https://console.twilio.com")
        else:
            print_error(f"Could not purchase: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Twilio: webhook configuration
# ---------------------------------------------------------------------------

def configure_webhook(client, phone_number_sid: str, server_url: str, webhook_secret: str | None = None) -> str:
    """Set the voice webhook URL on a Twilio phone number. Returns the full webhook URL."""
    from twilio.base.exceptions import TwilioRestException

    webhook_path = "/incoming-call"
    if webhook_secret:
        webhook_path = f"/incoming-call/{webhook_secret}"
    webhook_url = f"{server_url}{webhook_path}"

    sys.stdout.write("  Configuring Twilio webhook... ")
    sys.stdout.flush()

    try:
        client.incoming_phone_numbers(phone_number_sid).update(
            voice_url=webhook_url,
            voice_method="POST",
        )

        updated = client.incoming_phone_numbers(phone_number_sid).fetch()
        if updated.voice_url == webhook_url:
            print("OK")
            print_ok(f"  Webhook: {webhook_url}")
        else:
            print("OK (set — verify in console)")

        return webhook_url

    except TwilioRestException as e:
        print("FAILED")
        print_error(f"Could not configure webhook: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Full setup wizard
# ---------------------------------------------------------------------------

def run_full_setup():
    """Run the full quickstart wizard from scratch."""
    print_header("Voice Agent Provisioning")
    print()
    print_ok("This will set up everything you need to receive phone calls:")
    print_ok("  1. API keys (Deepgram, OpenAI, ElevenLabs)")
    print_ok("  2. Twilio phone number")
    print_ok("  3. zrok tunnel (public URL for Twilio to reach your machine)")
    print_ok("  4. Wire it all together")

    # --- API keys ---
    collect_api_keys()

    # --- Twilio ---
    print_section("Twilio")
    client, account_sid, auth_token, account_type = get_twilio_client()
    phone_number, phone_number_sid, provisioned = select_phone_number(client)

    # --- zrok ---
    print_section("Tunnel (zrok)")

    binary = check_zrok_installed()
    version = check_zrok_version(binary)
    print_ok(f"zrok: {version or 'installed'}")

    if not check_zrok_enabled(binary):
        enable_zrok(binary)
    else:
        print_ok("zrok: enabled")

    # Create reserved share
    port = int(get_env_value("SERVER_PORT") or DEFAULT_PORT)
    share_name = create_reserved_share(binary, port)
    tunnel_url = get_share_url(share_name)

    # Generate a webhook secret
    webhook_secret = secrets.token_urlsafe(16)

    # --- Configure webhook ---
    print_section("Wiring")
    webhook_url = configure_webhook(client, phone_number_sid, tunnel_url, webhook_secret)

    # --- Save everything ---
    state = {
        "twilio": {
            "account_sid": account_sid,
            "phone_number": phone_number,
            "phone_number_sid": phone_number_sid,
            "webhook_url": webhook_url,
            "provisioned_by_wizard": provisioned,
        },
        "zrok": {
            "share_name": share_name,
            "tunnel_url": tunnel_url,
        },
        "webhook_secret": webhook_secret,
        "port": port,
    }
    save_state(state)

    env_updates = {
        "TWILIO_ACCOUNT_SID": account_sid,
        "TWILIO_AUTH_TOKEN": auth_token,
        "TWILIO_PHONE_NUMBER": phone_number,
        "SERVER_EXTERNAL_URL": tunnel_url,
        "WEBHOOK_SECRET": webhook_secret,
    }
    update_env_file(env_updates)

    # --- Done ---
    print_section("Ready!")
    print()
    print_ok(f"  Phone number:  {format_phone(phone_number)}")
    print_ok(f"  Tunnel URL:    {tunnel_url}")
    print_ok(f"  Webhook:       {webhook_url}")
    print()
    print_ok("To start taking calls:")
    print()
    print_ok("  python run.py")
    print_ok(f"  Then call {format_phone(phone_number)}")
    print()


# ---------------------------------------------------------------------------
# Update webhook URL
# ---------------------------------------------------------------------------

def run_update_url():
    """Re-read the tunnel URL from state and update the Twilio webhook."""
    state = load_state()
    if not state:
        print_error("Not set up yet. Run: python setup/quickstart.py")
        sys.exit(1)

    twilio_state = state.get("twilio", {})
    zrok_state = state.get("zrok", {})
    phone_number_sid = twilio_state.get("phone_number_sid")
    tunnel_url = zrok_state.get("tunnel_url")
    webhook_secret = state.get("webhook_secret")

    if not phone_number_sid or not tunnel_url:
        print_error("Incomplete state. Run: python setup/quickstart.py")
        sys.exit(1)

    account_sid = get_env_value("TWILIO_ACCOUNT_SID")
    auth_token = get_env_value("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        print_error("Twilio credentials not found in .env.")
        sys.exit(1)

    from twilio.rest import Client
    client = Client(account_sid, auth_token)

    print()
    webhook_url = configure_webhook(client, phone_number_sid, tunnel_url, webhook_secret)

    state["twilio"]["webhook_url"] = webhook_url
    save_state(state)
    update_env_file({"SERVER_EXTERNAL_URL": tunnel_url})

    print()
    print_ok("Webhook updated.")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def show_status():
    """Show current status."""
    state = load_state()
    if not state:
        print()
        print_ok("Not set up yet. Run: python setup/quickstart.py")
        return

    print_header("Provisioning Status")

    twilio_state = state.get("twilio", {})
    zrok_state = state.get("zrok", {})

    print()
    print_ok(f"Phone number:  {format_phone(twilio_state.get('phone_number', 'not set'))}")
    print_ok(f"Webhook:       {twilio_state.get('webhook_url', 'not set')}")

    if zrok_state:
        print_ok(f"Tunnel:        {zrok_state.get('tunnel_url', 'not set')}")
        print_ok(f"Share name:    {zrok_state.get('share_name', 'not set')}")

    print_ok(f"Last updated:  {state.get('last_updated_at', 'unknown')}")

    # Check tunnel
    pid = find_zrok_process()
    if pid:
        print_ok(f"Tunnel status: running (PID {pid})")
    else:
        print_ok("Tunnel status: not running")
        print_ok("  Start with: python run.py")

    print()


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def run_teardown():
    """Tear down: stop tunnel, release share, clear webhook."""
    state = load_state()
    if not state:
        print_error("Nothing to tear down (no state file found).")
        sys.exit(1)

    twilio_state = state.get("twilio", {})
    zrok_state = state.get("zrok", {})
    phone_number = twilio_state.get("phone_number", "unknown")

    print()
    print_ok("Current configuration:")
    print_ok(f"  Phone:   {format_phone(phone_number)}")
    if zrok_state:
        print_ok(f"  Tunnel:  {zrok_state.get('tunnel_url', 'unknown')}")
    print()

    if not prompt_confirm("Tear down?", default_yes=False):
        print_ok("  Cancelled.")
        return

    # Stop tunnel process
    stop_zrok_tunnel()

    # Release reserved share
    if zrok_state:
        share_name = zrok_state.get("share_name")
        if share_name:
            binary = _get_zrok_binary()
            if binary:
                release_reserved_share(binary, share_name)

    # Clear Twilio webhook
    phone_number_sid = twilio_state.get("phone_number_sid")
    account_sid = get_env_value("TWILIO_ACCOUNT_SID")
    auth_token = get_env_value("TWILIO_AUTH_TOKEN")

    if phone_number_sid and account_sid and auth_token:
        try:
            from twilio.rest import Client
            from twilio.base.exceptions import TwilioRestException
            client = Client(account_sid, auth_token)
            sys.stdout.write("  Clearing Twilio webhook... ")
            sys.stdout.flush()
            client.incoming_phone_numbers(phone_number_sid).update(voice_url="")
            print("OK")
        except Exception as e:
            print(f"FAILED ({e})")

    # Optionally release phone number
    provisioned = twilio_state.get("provisioned_by_wizard", False)
    if provisioned and phone_number_sid and account_sid and auth_token:
        print()
        print_ok(f"  {format_phone(phone_number)} was provisioned by this wizard.")
        if prompt_confirm(f"Release {format_phone(phone_number)}? (cannot be undone)", default_yes=False):
            try:
                from twilio.rest import Client
                from twilio.base.exceptions import TwilioRestException
                client = Client(account_sid, auth_token)
                sys.stdout.write("  Releasing number... ")
                sys.stdout.flush()
                client.incoming_phone_numbers(phone_number_sid).delete()
                print("OK")
            except Exception as e:
                print(f"FAILED ({e})")
                print_ok("  Release manually: https://console.twilio.com")
        else:
            print_ok("  Number kept.")
    elif phone_number != "unknown":
        print()
        print_ok(f"  {format_phone(phone_number)} is still active on your Twilio account.")

    # Remove state file
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        print_ok("  Removed state file.")

    print()


# ---------------------------------------------------------------------------
# Re-run menu (when state already exists)
# ---------------------------------------------------------------------------

def run_rerun_menu(state: dict):
    """Show options when already set up."""
    print_header("Voice Agent Provisioning")

    # Always check for missing API keys first
    collect_api_keys()

    twilio_state = state.get("twilio", {})
    zrok_state = state.get("zrok", {})

    print()
    print_ok("Already set up:")
    print_ok(f"  Phone:   {format_phone(twilio_state.get('phone_number', 'unknown'))}")
    if zrok_state:
        print_ok(f"  Tunnel:  {zrok_state.get('tunnel_url', 'unknown')}")
    print_ok(f"  Webhook: {twilio_state.get('webhook_url', 'unknown')}")

    pid = find_zrok_process()
    if pid:
        print_ok(f"  Tunnel:  running (PID {pid})")
    else:
        print_ok("  Tunnel:  not running")

    options = [
        "Start the agent (python run.py)",
        "Update Twilio webhook URL",
        "Switch to a different phone number",
        "Start fresh (redo everything)",
        "Exit",
    ]

    choice = prompt_choice(options, default=1)
    chosen = options[choice - 1]

    if "Start the agent" in chosen:
        print()
        print_ok("Run:  python run.py")
        print()
    elif "Update" in chosen:
        run_update_url()
    elif "Switch" in chosen:
        _handle_switch_number(state)
    elif "fresh" in chosen.lower():
        run_full_setup()
    else:
        print()
        print_ok("No changes.")


def _handle_switch_number(state: dict):
    """Switch to a different Twilio phone number."""
    account_sid = get_env_value("TWILIO_ACCOUNT_SID")
    auth_token = get_env_value("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        print_error("Twilio credentials not found in .env.")
        return

    from twilio.rest import Client
    client = Client(account_sid, auth_token)

    phone_number, phone_number_sid, provisioned = select_phone_number(client)

    zrok_state = state.get("zrok", {})
    tunnel_url = zrok_state.get("tunnel_url")
    webhook_secret = state.get("webhook_secret")

    if not tunnel_url:
        print_error("No tunnel URL in state. Run quickstart again.")
        return

    print()
    webhook_url = configure_webhook(client, phone_number_sid, tunnel_url, webhook_secret)

    state["twilio"]["phone_number"] = phone_number
    state["twilio"]["phone_number_sid"] = phone_number_sid
    state["twilio"]["webhook_url"] = webhook_url
    state["twilio"]["provisioned_by_wizard"] = provisioned
    save_state(state)

    update_env_file({"TWILIO_PHONE_NUMBER": phone_number})

    print()
    print_ok("Phone number updated.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Voice Agent Quickstart — Twilio + zrok tunnel setup",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current status",
    )
    parser.add_argument(
        "--update-url", action="store_true",
        help="Re-sync the Twilio webhook to the current tunnel URL",
    )
    parser.add_argument(
        "--teardown", action="store_true",
        help="Release tunnel, clear webhook, remove state",
    )

    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.update_url:
        run_update_url()
        return

    if args.teardown:
        run_teardown()
        return

    # Default: full setup or re-run menu
    state = load_state()
    if state:
        run_rerun_menu(state)
    else:
        run_full_setup()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.\n")
        sys.exit(0)
