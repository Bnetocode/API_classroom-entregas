"""Provisiona o refresh token de um cliente Web para o Community Cloud.

Antes de executar, crie no Google Cloud um cliente OAuth ``Web application``,
registre exatamente ``http://localhost:8080/`` como redirect URI e salve o JSON
baixado nesta pasta com o nome ``credentials_web.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError

from classroom_client import SCOPES, save_authorized_user_credentials


BASE_DIR = Path(__file__).resolve().parent
WEB_CREDENTIALS_PATH = BASE_DIR / "credentials_web.json"
CLOUD_TOKEN_PATH = BASE_DIR / "token_cloud.json"
REDIRECT_URI = "http://localhost:8080/"


def validate_web_credentials(path: Path = WEB_CREDENTIALS_PATH) -> None:
    if not path.is_file():
        raise RuntimeError(
            "credentials_web.json não encontrado. Baixe um cliente OAuth do "
            "tipo Web application."
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("credentials_web.json não é um JSON OAuth válido.") from exc

    if "web" not in config:
        raise RuntimeError(
            "credentials_web.json deve ser do tipo Web application, não Desktop."
        )
    redirects = config["web"].get("redirect_uris", [])
    if REDIRECT_URI not in redirects:
        raise RuntimeError(
            f"Adicione exatamente {REDIRECT_URI} em Authorized redirect URIs "
            "do cliente Web e baixe o JSON novamente."
        )


def main() -> None:
    validate_web_credentials()
    os.chmod(WEB_CREDENTIALS_PATH, 0o600)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(WEB_CREDENTIALS_PATH), SCOPES
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=8080,
        open_browser=True,
        timeout_seconds=300,
        redirect_uri_trailing_slash=True,
        authorization_prompt_message=(
            "Abra esta URL para autorizar a conta docente usada no Cloud: {url}"
        ),
        success_message=(
            "Token Cloud criado. Você pode fechar esta aba e voltar ao terminal."
        ),
        access_type="offline",
        prompt="consent",
    )
    save_authorized_user_credentials(credentials, CLOUD_TOKEN_PATH)
    print(
        "token_cloud.json criado com permissão 600. Copie client_id, "
        "client_secret, refresh_token e token_uri para o Streamlit Secrets; "
        "não envie este arquivo ao GitHub."
    )


if __name__ == "__main__":
    try:
        main()
    except WSGITimeoutError as exc:
        raise SystemExit("Tempo de autorização encerrado; execute novamente.") from exc
    except RuntimeError as exc:
        raise SystemExit(f"Erro: {exc}") from exc
