"""Non-destructive runtime checks for source and packaged desktop builds."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path


def run() -> int:
    from .config import ABOUT_LOGO_FILE, APP_DIR, APP_ICON_FILE, ASSET_DIR, USER_MANUAL_FILE

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

        application = QApplication.instance() or QApplication([])
        missing_assets = [
            name for name in (
                "icon.ico", "icon.png", "logo-about-window.png",
                "manual_usuario.pdf",
            ) if not (ASSET_DIR / name).is_file()
        ]
        if missing_assets:
            raise FileNotFoundError("Recursos ausentes: " + ", ".join(missing_assets))
        bundled_license = ASSET_DIR / "LICENSE.txt"
        source_license = Path(__file__).resolve().parents[1] / "LICENSE"
        if not bundled_license.is_file() and not source_license.is_file():
            raise FileNotFoundError("A licença do projeto não foi incluída")
        if not APP_ICON_FILE.is_file() or not ABOUT_LOGO_FILE.is_file():
            raise FileNotFoundError("Ícone ou imagem da janela Sobre não foi incluído")
        if not USER_MANUAL_FILE.is_file() or USER_MANUAL_FILE.stat().st_size < 10_000:
            raise FileNotFoundError("Manual PDF não foi incluído ou está incompleto")

        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg = get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True,
        )
        if "ffmpeg version" not in (result.stdout or "").lower():
            raise RuntimeError("O FFmpeg empacotado não respondeu corretamente")

        player = QMediaPlayer()
        player.setAudioOutput(QAudioOutput())
        QMediaDevices.audioOutputs()  # Enumerates outputs without opening an audio stream.

        import keyring

        backend = keyring.get_keyring()
        if not backend or backend.priority <= 0:
            raise RuntimeError("O backend do Gerenciador de Credenciais não foi carregado")

        # Keep Qt objects alive until all checks finish; no file is played and no
        # microphone or external transmission is opened.
        _ = application, player
        return 0
    except Exception:
        from .runtime_safety import configure_exception_logging

        configure_exception_logging(APP_DIR)
        logger = logging.getLogger("radiostudio.packaging")
        logger.exception(
            "Verificação do pacote falhou (perfil=%s)", APP_DIR
        )
        return 1
