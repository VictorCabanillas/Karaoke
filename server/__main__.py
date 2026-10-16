"""Start the server: `uv run python -m server` (add --reload during development).

  --check   diagnose the installation (nothing is started)
  --open    open the main screen in the browser once the server is up
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser

import uvicorn

from server.config import lan_ip, settings
from server.doctor import certificate_problem, frontend_problem, print_report

SHUTDOWN_TIMEOUT_S = 3


def open_when_ready(url: str) -> None:
    """Open the browser once the server answers (in the background)."""

    def wait_and_open() -> None:
        import httpx

        for _ in range(120):
            try:
                httpx.get(f"{url}api/songs", timeout=1, verify=False)
                webbrowser.open(url)
                return
            except httpx.HTTPError:
                time.sleep(0.5)

    threading.Thread(target=wait_and_open, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Karaoke entre amigos")
    parser.add_argument("--reload", action="store_true", help="reload on code changes (development)")
    parser.add_argument("--check", action="store_true", help="diagnose the installation and exit")
    parser.add_argument("--open", action="store_true", help="open the main screen in the browser")
    args = parser.parse_args()

    if args.check:
        sys.exit(print_report(settings))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    scheme = "https" if settings.https else "http"
    local_url = f"{scheme}://127.0.0.1:{settings.port}/"
    print(f"\n  Pantalla principal: {local_url}")
    print(f"  En la red local:    {scheme}://{lan_ip()}:{settings.port}/\n")
    warnings = [w for w in (frontend_problem(settings), certificate_problem(settings)) if w]
    if not settings.https:
        warnings.append("sin certificados en certs/ los móviles no podrán usar el micrófono: «uv run python scripts/make_certs.py»")
    for warning in warnings:
        print(f"  AVISO: {warning}")
    if warnings:
        print("  (diagnóstico completo: «uv run python -m server --check»)\n")
    if args.open:
        open_when_ready(local_url)

    # Shutting down while a song is processed must not hang (see server.main.exit_now).
    os.environ["KARAOKE_HARD_EXIT"] = "1"  # for the --reload worker process
    settings.hard_exit = True
    uvicorn.run(
        "server.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        ssl_certfile=str(settings.cert_file) if settings.https else None,
        ssl_keyfile=str(settings.key_file) if settings.https else None,
        # A phone that went to sleep never completes the TLS close: without a limit, Ctrl+C would wait
        # forever at "Waiting for connections to close".
        timeout_graceful_shutdown=SHUTDOWN_TIMEOUT_S,
    )


if __name__ == "__main__":
    main()
