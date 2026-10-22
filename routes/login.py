"""Platform login browser routes.

Implements: FR-068 (platform login browser).
"""

from __future__ import annotations

import logging
import os
import subprocess
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

import app_state
from config.settings import get_data_dir
from core.i18n import t

logger = logging.getLogger(__name__)

login_bp = Blueprint("login", __name__)


def _find_system_chrome() -> str | None:
    """Find system-installed Chrome/Chromium."""
    import platform

    candidates = []
    if platform.system() == "Windows":
        for base in [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.path.expandvars(r"%LOCALAPPDATA%"),
        ]:
            candidates.append(
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            )
    elif platform.system() == "Darwin":
        candidates.append(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    else:
        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
        ])

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return None


def _find_chrome_executable() -> str | None:
    """Resolve Chrome, falling back to Playwright's bundled Chromium."""
    system_chrome = _find_system_chrome()
    if system_chrome:
        return system_chrome

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = playwright.chromium.executable_path

        if executable and os.path.isfile(executable):
            return executable
    except Exception as e:
        logger.debug("Could not resolve Playwright Chromium: %s", e)

    return None


@login_bp.route("/api/login/open", methods=["POST"])
def login_open():
    """Open system Chrome with a dedicated profile for platform login."""
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": t("errors.url_required")}), 400

    url = data["url"]
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return jsonify({"error": t("errors.invalid_url")}), 400
    allowed_domains = {"linkedin.com", "indeed.com"}
    if not any(host == d or host.endswith("." + d) for d in allowed_domains):
        return jsonify({"error": t("errors.unsupported_login_url")}), 400

    chrome_path = _find_chrome_executable()
    if not chrome_path:
        return jsonify({
            "error": t("errors.chrome_not_found"),
        }), 500

    profile_dir = str(get_data_dir() / "browser_profile")

    with app_state.login_lock:
        if app_state.login_proc is not None:
            try:
                app_state.login_proc.terminate()
            except Exception as e:
                logger.debug("Failed to terminate previous login browser: %s", e)
            app_state.login_proc = None

        try:
            proc = subprocess.Popen(
                [
                    chrome_path,
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-default-apps",
                    "--new-window",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            app_state.login_proc = proc
            logger.info(
                "Login browser opened (Chrome PID %d): %s", proc.pid, url,
            )
        except Exception as e:
            logger.error("Failed to open login browser: %s", e)
            return jsonify({"error": t("errors.chrome_failed", error=str(e))}), 500

    return jsonify({"status": "opening"})


@login_bp.route("/api/login/close", methods=["POST"])
def login_close():
    """Close the login browser."""
    with app_state.login_lock:
        if app_state.login_proc is None:
            return jsonify({"status": "already_closed"})
        proc = app_state.login_proc
        app_state.login_proc = None

    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as e:
        logger.debug("Failed to terminate login browser cleanly: %s", e)
    return jsonify({"status": "closed"})


@login_bp.route("/api/login/status", methods=["GET"])
def login_status():
    """Check if a login browser is currently open."""
    with app_state.login_lock:
        if app_state.login_proc is None:
            return jsonify({"open": False})
        if app_state.login_proc.poll() is not None:
            app_state.login_proc = None
            return jsonify({"open": False})
        return jsonify({"open": True})


@login_bp.route("/api/login/sessions", methods=["GET"])
def login_sessions():
    """Check which platforms have active login sessions."""
    import shutil
    import sqlite3

    profile_dir = get_data_dir() / "browser_profile" / "Default"
    cookie_candidates = [
        profile_dir / "Network" / "Cookies",
        profile_dir / "Cookies",
    ]
    profile_cookies = next(
        (candidate for candidate in cookie_candidates if candidate.exists()),
        None,
    )

    result = {"linkedin": False, "indeed": False}

    if profile_cookies is None:
        return jsonify(result)

    tmp_cookies = get_data_dir() / "browser_profile" / "_cookies_check.db"
    try:
        shutil.copy2(str(profile_cookies), str(tmp_cookies))
        conn = sqlite3.connect(str(tmp_cookies))
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%linkedin.com' "
            "AND name = 'li_at'"
        )
        result["linkedin"] = cursor.fetchone()[0] > 0

        cursor.execute(
            "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%indeed.com' "
            "AND name IN ('INDEED_CSRF_TOKEN', 'indeed_rcc')"
        )
        result["indeed"] = cursor.fetchone()[0] > 0

        conn.close()
    except Exception as e:
        logger.debug("Could not read cookies DB: %s", e)
    finally:
        try:
            tmp_cookies.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Failed to clean up temp cookies file: %s", e)

    return jsonify(result)
