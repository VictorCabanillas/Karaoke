"""Start the server: `uv run python -m server` (add --reload during development)."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from server.config import lan_ip, settings


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

    uvicorn.run(
        "server.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        ssl_certfile=str(settings.cert_file) if settings.https else None,
        ssl_keyfile=str(settings.key_file) if settings.https else None,
    )


if __name__ == "__main__":
    main()
