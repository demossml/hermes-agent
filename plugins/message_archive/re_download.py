"""
Re-download files from Telegram using stored file_id.

Use when the local copy in archive/files/ is lost but
the telegram_file_id is still in messages.db.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Default Telegram API IP (bypasses DPI/censorship when needed)
DEFAULT_TELEGRAM_IP = "149.154.167.220"


def re_download_file(
    bot_token: str,
    telegram_file_id: str,
    dest_dir: str,
    *,
    telegram_ip: str = DEFAULT_TELEGRAM_IP,
    timeout: int = 30,
) -> str | None:
    """Re-download a file from Telegram using its file_id.

    Returns absolute path to downloaded file, or None on failure.

    Example:
        path = re_download_file(
            bot_token="12345:abc...",
            telegram_file_id="AgACAgIAAxkBAAIC...",
            dest_dir="/tmp/",
        )
    """
    if not bot_token or not telegram_file_id:
        return None

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # Step 1: getFile → resolve file_path
    try:
        result = subprocess.run(
            [
                "curl", "-sk",
                "--resolve", f"api.telegram.org:443:{telegram_ip}",
                f"https://api.telegram.org/bot{bot_token}/getFile",
                "-d", f"file_id={telegram_file_id}",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        logger.warning("re_download: getFile failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning("re_download: getFile non-zero exit: %s", result.returncode)
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("re_download: getFile returned non-JSON")
        return None

    if not data.get("ok"):
        logger.warning("re_download: getFile not ok: %s", data)
        return None

    file_path = data["result"]["file_path"]
    file_name = Path(file_path).name
    dest_file = dest / file_name

    # Step 2: download the file
    try:
        dl = subprocess.run(
            [
                "curl", "-sk", "-o", str(dest_file),
                "--resolve", f"api.telegram.org:443:{telegram_ip}",
                f"https://api.telegram.org/file/bot{bot_token}/{file_path}",
            ],
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("re_download: download failed: %s", exc)
        return None

    if dl.returncode != 0:
        logger.warning("re_download: download non-zero exit: %s", dl.returncode)
        return None

    if not dest_file.exists() or dest_file.stat().st_size == 0:
        logger.warning("re_download: downloaded file empty or missing")
        return None

    logger.info("re_download: saved %s (%d bytes)", dest_file, dest_file.stat().st_size)
    return str(dest_file)
