"""Start the server: `uv run python -m server` (add --reload during development)."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from server.config import lan_ip, settings

SHUTDOWN_TIMEOUT_S = 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Karaoke entre amigos")
    parser.add_argument("--reload", action="store_true", help="reload on code changes (development)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    scheme = "https" if settings.https else "http"
    print(f"\n  Pantalla principal: {scheme}://127.0.0.1:{settings.port}/")
    print(f"  En la red local:    {scheme}://{lan_ip()}:{settings.port}/\n")
    if not settings.https:
        print("  AVISO: sin certificados en certs/ los móviles no podrán usar el micrófono.")
        print("         Genera certificados con: uv run python scripts/make_certs.py\n")

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
