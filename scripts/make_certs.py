"""Create local HTTPS certificates with mkcert (phones only allow the microphone over HTTPS).

Usage: uv run python scripts/make_certs.py [extra hostnames or IPs...]

Requires mkcert (https://github.com/FiloSottile/mkcert). Run `mkcert -install` once beforehand so the
laptop's browser trusts the certificates; see the README to make phones trust them too.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import lan_ip, settings  # noqa: E402


def main() -> None:
    mkcert = shutil.which("mkcert")
    if not mkcert:
        sys.exit("mkcert no está instalado: https://github.com/FiloSottile/mkcert#installation")
    certs = settings.certs_dir
    certs.mkdir(parents=True, exist_ok=True)
    names = ["localhost", "127.0.0.1", "::1", lan_ip(), f"{socket.gethostname()}.local", *sys.argv[1:]]
    names = list(dict.fromkeys(n for n in names if n))
    subprocess.run(
        [mkcert, "-cert-file", str(settings.cert_file), "-key-file", str(settings.key_file), *names],
        check=True,
    )
    caroot = subprocess.run([mkcert, "-CAROOT"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"\nCertificados creados en {certs} para: {', '.join(names)}")
    print("Si la IP del portátil cambia, vuelve a ejecutar este script.")
    print(f"Certificado raíz (para instalar en los móviles): {Path(caroot) / 'rootCA.pem'}")


if __name__ == "__main__":
    main()
