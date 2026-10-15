#!/usr/bin/env bash
# Instala todo lo necesario para el karaoke en macOS o Linux (Debian/Ubuntu) y deja la app lista.
# En Windows usa setup.cmd. Lo que instala por primera vez queda apuntado en .setup-state (ver uninstall.sh).
#
# Uso: ./setup.sh [--no-processing] [--no-certs] [--dry-run]
set -euo pipefail
cd "$(dirname "$0")"

PROCESSING=1
CERTS=1
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-processing) PROCESSING=0 ;;
    --no-certs) CERTS=0 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok() { printf '    \033[32m%s\033[0m\n' "$1"; }
run() {
  printf '    \033[90m> %s\033[0m\n' "$*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}
has() { command -v "$1" >/dev/null 2>&1; }
STATE=.setup-state
remember() { # apunta lo que añade este script, para que uninstall.sh lo quite
  [ "$DRY_RUN" -eq 1 ] && return 0
  grep -qxF "$1" "$STATE" 2>/dev/null || echo "$1" >>"$STATE"
}

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  *) echo "En Windows ejecuta setup.cmd" >&2; exit 1 ;;
esac

install_pkg() { # comando, paquete brew, paquete apt
  if has "$1"; then ok "$1 ya está instalado"; return; fi
  if [ "$OS" = mac ]; then
    has brew || { echo "Instala Homebrew primero: https://brew.sh" >&2; exit 1; }
    run brew install "$2"
    remember "tool brew:$2"
  elif has apt-get; then
    run sudo apt-get install -y "$3"
    remember "tool apt:$3"
  else
    echo "Instala $1 con el gestor de paquetes de tu sistema y vuelve a ejecutar." >&2; exit 1
  fi
}

[ "$DRY_RUN" -eq 1 ] && printf '\033[33m    Modo prueba (--dry-run): no se instala ni se cambia nada.\033[0m\n'

# La app descarga sus modelos más tarde: apuntar qué cachés no existían antes de la primera instalación.
if ! grep -qxF caches-recorded "$STATE" 2>/dev/null; then
  for dir in "${HF_HOME:-$HOME/.cache/huggingface}" "${TORCH_HOME:-$HOME/.cache/torch}"; do
    [ -e "$dir" ] || remember "cache-new $dir"
  done
  remember caches-recorded
fi

step "Herramientas del sistema"
if has uv; then
  ok "uv ya está instalado"
else
  run sh -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
  remember "tool uv-script"
  export PATH="$HOME/.local/bin:$PATH"
fi
[ "$OS" = linux ] && has apt-get && run sudo apt-get update
install_pkg node node nodejs
has npm || install_pkg npm node npm
install_pkg ffmpeg ffmpeg ffmpeg
if [ "$CERTS" -eq 1 ]; then
  install_pkg mkcert mkcert mkcert
  if [ "$OS" = linux ] && has apt-get && ! dpkg -s libnss3-tools >/dev/null 2>&1; then
    run sudo apt-get install -y libnss3-tools
    remember "tool apt:libnss3-tools"
  fi
fi

step "Dependencias de Python"
if [ "$PROCESSING" -eq 1 ]; then
  echo "    Incluye el procesado de canciones (PyTorch, Demucs, Whisper): puede tardar y ocupar varios GB."
  run uv sync --extra processing
else
  run uv sync
fi

step "Frontend"
run npm install --no-fund --no-audit
run npm run build

if [ "$CERTS" -eq 1 ]; then
  step "Certificados HTTPS"
  if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$(mkcert -CAROOT)/rootCA.pem" ]; then remember ca-created; fi
  run mkcert -install
  run uv run python scripts/make_certs.py
fi

step "Listo"
ok "Arranca el karaoke con:  uv run python -m server"
ok "Para desinstalarlo todo: ./uninstall.sh"
if [ "$CERTS" -eq 1 ]; then
  ok "Abre https://127.0.0.1:8000/ en el portátil."
  [ "$DRY_RUN" -eq 0 ] && ok "Para los móviles, instala en ellos: $(mkcert -CAROOT)/rootCA.pem (ver README)."
fi
exit 0
