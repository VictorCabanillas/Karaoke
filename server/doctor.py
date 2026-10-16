"""Installation diagnosis: `uv run python -m server --check` (or `start.cmd --check`).

Everything is read-only. Each check gives a level (ok / aviso / error) and how to fix it.
"""

from __future__ import annotations

import importlib.util
import platform
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from processing import audio as audio_tools
from processing import device as device_mod
from server.config import Settings, lan_ip

OK, WARN, FAIL = "ok", "aviso", "error"


@dataclass
class Check:
    level: str
    title: str
    detail: str = ""


def cert_names(settings: Settings) -> tuple[set[str], float | None]:
    """IPs/hostnames the HTTPS certificate is valid for, and its expiry (epoch)."""
    try:
        info = ssl._ssl._test_decode_cert(str(settings.cert_file))  # type: ignore[attr-defined]
    except Exception:
        return set(), None
    names = {value for _kind, value in info.get("subjectAltName", ())}
    expires = ssl.cert_time_to_seconds(info["notAfter"]) if "notAfter" in info else None
    return names, expires


def certificate_problem(settings: Settings) -> str | None:
    """Why phones would fail to connect with the current certificate (None if fine or no HTTPS)."""
    if not settings.https:
        return None
    names, expires = cert_names(settings)
    ip = lan_ip()
    if names and ip not in names:
        return f"el certificado no incluye la IP actual del portátil ({ip}): ejecuta «uv run python scripts/make_certs.py»"
    if expires and expires < time.time():
        return "el certificado ha caducado: ejecuta «uv run python scripts/make_certs.py»"
    return None


def frontend_problem(settings: Settings) -> str | None:
    if not (settings.web_dist / "host" / "index.html").exists():
        return "falta compilar la página: ejecuta «npm run build» (o vuelve a pasar setup)"
    return None


def _powershell(command: str) -> list[str]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    return [part.strip() for part in out.replace("\n", ",").split(",") if part.strip()]


def _windows_network_categories() -> list[str]:
    return list(dict.fromkeys(_powershell("(Get-NetConnectionProfile).NetworkCategory -join ','")))


def _python_firewall_profiles() -> set[str]:
    """Network profiles where an enabled inbound rule lets Python in (Any / Public / Private / Domain)."""
    return set(
        _powershell(
            "(Get-NetFirewallApplicationFilter | Where-Object Program -match 'python' | Get-NetFirewallRule | "
            "Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Allow' -and $_.Direction -eq 'Inbound' })"
            ".Profile -join ','"
        )
    )


def run_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check(OK, f"Python {platform.python_version()} en {platform.system()} {platform.release()}"))

    problem = frontend_problem(settings)
    checks.append(Check(FAIL, "Página web", problem) if problem else Check(OK, "Página web compilada"))

    if settings.https:
        problem = certificate_problem(settings)
        names, expires = cert_names(settings)
        if problem:
            checks.append(Check(FAIL, "Certificado HTTPS", problem))
        else:
            until = datetime.fromtimestamp(expires).strftime("%d/%m/%Y") if expires else "?"
            checks.append(Check(OK, "Certificado HTTPS", f"válido para {', '.join(sorted(names))} hasta {until}"))
    else:
        checks.append(Check(WARN, "Sin HTTPS", "los móviles no podrán usar el micrófono: pasa setup o «scripts/make_certs.py»"))

    missing = device_mod.missing_modules()
    if missing:
        checks.append(Check(WARN, "Procesado de canciones no instalado",
                            f"falta {', '.join(missing)}: «uv sync» (sin voz separada, melodía ni transcripción)"))  # fmt: skip
    else:
        device = device_mod.detect_device()
        label = {"cuda": "GPU NVIDIA", "mps": "GPU Apple", "cpu": "CPU (lento: varios minutos por canción)"}[device]
        checks.append(Check(OK, "Procesado de canciones", label))

    checks.append(
        Check(OK, "ffmpeg") if audio_tools.ffmpeg_path()
        else Check(WARN, "ffmpeg no encontrado", "no se normaliza el volumen y no se pueden usar algunos formatos")
    )  # fmt: skip
    checks.append(
        Check(OK, "Descargas (yt-dlp)") if importlib.util.find_spec("yt_dlp")
        else Check(WARN, "yt-dlp no instalado", "no funcionará el buscador de canciones: «uv sync»")
    )  # fmt: skip

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((settings.host, settings.port))
        checks.append(Check(OK, f"Puerto {settings.port} libre"))
    except OSError:
        checks.append(Check(FAIL, f"Puerto {settings.port} ocupado",
                            "¿hay otro servidor del karaoke abierto? Ciérralo o usa KARAOKE_PORT"))  # fmt: skip
    finally:
        probe.close()

    ip = lan_ip()
    if ip.startswith("127."):
        checks.append(Check(FAIL, "Sin red local", "conecta el portátil a la Wi-Fi (la misma que los móviles)"))
    else:
        scheme = "https" if settings.https else "http"
        checks.append(Check(OK, "Red local", f"los móviles entrarán por {scheme}://{ip}:{settings.public_port}/mic/"))

    if sys.platform == "win32":
        categories = _windows_network_categories()
        allowed = _python_firewall_profiles()
        names = {"Public": "Pública", "Private": "Privada", "DomainAuthenticated": "Dominio"}
        network = ", ".join(names.get(c, c) for c in categories) or "desconocida"
        if not categories or "Any" in allowed or all(c.replace("Authenticated", "") in allowed for c in categories):
            if allowed:
                checks.append(Check(OK, "Firewall de Windows", f"Python tiene permiso en esta red ({network})"))
            else:
                checks.append(Check(WARN, "Firewall de Windows",
                                    "la primera vez que arranques, Windows preguntará: marca «Redes privadas» y "
                                    "«Permitir acceso»"))  # fmt: skip
        elif "Public" in categories:
            checks.append(Check(WARN, f"Red {network} sin permiso para Python",
                                "Windows bloqueará a los móviles: pon la Wi-Fi como Privada (Configuración → Red e "
                                "Internet → la Wi-Fi → Tipo de perfil de red) y permite el acceso cuando lo pida"))  # fmt: skip
        else:
            checks.append(Check(WARN, "Firewall de Windows",
                                f"Python aún no tiene permiso en la red {network}: cuando Windows pregunte al arrancar, "
                                "pulsa «Permitir acceso»"))  # fmt: skip

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        test = settings.data_dir / ".write-test"
        test.write_text("ok")
        test.unlink()
        checks.append(Check(OK, "Carpeta de datos", str(settings.data_dir)))
    except OSError as exc:
        checks.append(Check(FAIL, "No se puede escribir en la carpeta de datos", str(exc)))
    return checks


ICONS = {OK: "✔", WARN: "!", FAIL: "✘"}


def print_report(settings: Settings) -> int:
    """Print the diagnosis; exit code 1 if something blocks the karaoke."""
    print("\nDiagnóstico del karaoke\n")
    checks = run_checks(settings)
    for c in checks:
        line = f"  [{ICONS[c.level]}] {c.title}"
        print(line + (f": {c.detail}" if c.detail else ""))
    failures = sum(c.level == FAIL for c in checks)
    print("\n" + ("Todo listo." if not failures else f"{failures} problema(s) que arreglar antes de jugar.") + "\n")
    return 1 if failures else 0
