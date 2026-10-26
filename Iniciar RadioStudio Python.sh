#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo 'O RadioStudio precisa de uma sessão Linux com interface gráfica (X11 ou Wayland).' >&2
    exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import PySide6, imageio_ffmpeg, keyring, tinytag, pypresence' >/dev/null 2>&1; then
    echo 'Instalando dependências do RadioStudio Python...'
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python -m radiostudio "$@"
