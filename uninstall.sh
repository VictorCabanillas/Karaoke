#!/usr/bin/env bash
# Deshace lo que hizo setup.sh, sin tocar lo que ya estaba instalado antes (ver .setup-state).
# Siempre quita .venv, node_modules, web/dist y certs. data/ (la biblioteca) solo con --remove-data.
#
# Uso: ./uninstall.sh [--remove-data] [--yes] [--dry-run]
set -uo pipefail
cd "$(dirname "$0")"

REMOVE_DATA=0
YES=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --remove-data) REMOVE_DATA=1 ;;
    --yes) YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

STATE=.setup-state
PROBLEMS=0
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
warn() { printf '    \033[33m%s\033[0m\n' "$1"; PROBLEMS=$((PROBLEMS + 1)); }
has() { command -v "$1" >/dev/null 2>&1; }
run() { # los fallos se avisan pero no paran la desinstalación
  printf '    \033[90m> %s\033[0m\n' "$*"
  [ "$DRY_RUN" -eq 1 ] && return 0
  "$@" || warn "Falló: $*"
}
remove() { [ -e "$1" ] && run rm -rf "$1"; return 0; }
state() { [ -f "$STATE" ] && grep "$1" "$STATE" || true; }

TOOLS=$(state '^tool ' | cut -d' ' -f2-)
CACHES=$(state '^cache-new ' | cut -d' ' -f2-)
CA_CREATED=$(state '^ca-created$')
DIRS=".venv node_modules web/dist certs"
[ "$REMOVE_DATA" -eq 1 ] && DIRS="$DIRS data"

[ "$DRY_RUN" -eq 1 ] && printf '\033[33m    Modo prueba (--dry-run): no se borra nada.\033[0m\n'
echo
echo "Se va a quitar:"
for d in $DIRS; do [ -e "$d" ] && echo "  - $d"; done
while IFS= read -r c; do [ -n "$c" ] && [ -e "$c" ] && echo "  - caché de modelos $c"; done <<<"$CACHES"
[ -n "$CA_CREATED" ] && echo "  - la autoridad de certificados local de mkcert"
for t in $TOOLS; do echo "  - $t (instalado por setup)"; done
[ "$REMOVE_DATA" -eq 0 ] && [ -d data ] && echo "Se conserva data/ (tu biblioteca). Usa --remove-data para borrarla."
[ -f "$STATE" ] || echo "No hay .setup-state: no se desinstala ninguna herramienta."
if [ "$YES" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  read -r -p "¿Continuar? (s/N) " answer
  case "$answer" in s | S | si | sí | y | yes) ;; *) echo "Cancelado."; exit 0 ;; esac
fi

step "Archivos del proyecto"
for d in $DIRS; do remove "$d"; done

if [ -n "$CACHES" ]; then
  step "Cachés de modelos"
  while IFS= read -r c; do [ -n "$c" ] && remove "$c"; done <<<"$CACHES"
fi

if [ -n "$CA_CREATED" ]; then
  step "Autoridad de certificados de mkcert"
  if has mkcert; then
    caroot=$(mkcert -CAROOT)
    run mkcert -uninstall
    remove "$caroot"
  else
    warn "mkcert ya no está instalado: quita a mano la CA «mkcert» del sistema."
  fi
fi

if [ -n "$TOOLS" ]; then
  step "Herramientas instaladas por setup"
  for t in $TOOLS; do
    case "$t" in
      uv-script)
        if has uv; then
          run uv python uninstall --all
          run uv cache clean
        fi
        remove "$HOME/.local/bin/uv"
        remove "$HOME/.local/bin/uvx"
        ;;
      brew:*) run brew uninstall "${t#brew:}" ;;
      apt:*) run sudo apt-get remove -y "${t#apt:}" ;;
    esac
  done
fi

[ "$DRY_RUN" -eq 0 ] && rm -f "$STATE"
step "Listo"
if [ "$PROBLEMS" -gt 0 ]; then
  printf '    \033[33mTerminado con %s aviso(s): revisa los mensajes de arriba.\033[0m\n' "$PROBLEMS"
else
  printf '    \033[32mDesinstalado. El código sigue en esta carpeta; bórrala si ya no la quieres.\033[0m\n'
fi
