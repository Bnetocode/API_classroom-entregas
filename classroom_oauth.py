"""OAuth Web em memória e passagem do callback entre abas do Streamlit.

O fluxo e os tokens pertencem à sessão que iniciou a autorização. A caixa de
retorno compartilhada recebe apenas o código descartável, nunca credenciais.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from classroom_client import (
    SCOPES,
    ClassroomAPIError,
    ClassroomAuthenticationRequired,
    ClassroomConfigurationError,
    _credentials_have_required_scopes,
    recover_equivalent_scope_credentials,
    validate_cloud_secrets,
)

AUTHORIZATION_TTL = 600


def google_secret_section(secrets_source: Mapping[str, Any]) -> dict[str, Any] | None:
    """A seção nova tem prioridade, mesmo vazia; senha do app não participa."""
    for name in ("google_credentials", "google_oauth"):
        if name in secrets_source:
            value = secrets_source[name]
            if not isinstance(value, Mapping):
                raise ClassroomConfigurationError(f"[{name}] deve ser uma seção TOML.")
            return dict(value)
    return None


def read_google_secrets() -> dict[str, Any] | None:
    """Lê st.secrets sem exigir arquivo TOML no ambiente local."""
    import streamlit as st

    try:
        return google_secret_section(st.secrets)
    except (FileNotFoundError, KeyError):
        return None


@dataclass(repr=False)
class CloudAuthorization:
    flow: Flow
    url: str
    state: str
    created_at: float = field(default_factory=time.monotonic)
    consumed: bool = False


def start_cloud_authorization(
    secret: Mapping[str, Any], redirect_uri: str
) -> CloudAuthorization:
    validate_cloud_secrets(secret)
    if not isinstance(redirect_uri, str):
        raise ClassroomConfigurationError("redirect_uri deve ser uma URL em texto.")
    try:
        parsed = urlsplit(redirect_uri)
    except ValueError as exc:
        raise ClassroomConfigurationError("redirect_uri deve ser uma URL válida.") from exc
    if (
        not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or not (parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ))
    ):
        raise ClassroomConfigurationError(
            "Use a URL HTTPS do app como redirect_uri (HTTP somente em localhost)."
        )
    flow = Flow.from_client_config(
        {"web": {
            "client_id": secret["client_id"].strip(),
            "client_secret": secret["client_secret"].strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }},
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True,
    )
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return CloudAuthorization(flow, url, state)


def finish_cloud_authorization(
    pending: CloudAuthorization, response: Mapping[str, str]
) -> Credentials:
    """Confere state e prazo antes de trocar o código; cada fluxo é de uso único."""
    if pending.consumed or time.monotonic() - pending.created_at >= AUTHORIZATION_TTL:
        raise ClassroomAuthenticationRequired("Autorização expirada. Conecte novamente.")
    state = response.get("state", "")
    if not state or not secrets.compare_digest(state, pending.state):
        raise ClassroomAuthenticationRequired("Retorno OAuth inválido. Conecte novamente.")
    pending.consumed = True
    if response.get("error"):
        raise ClassroomAuthenticationRequired(
            "O consentimento Google foi cancelado ou recusado. Conecte novamente."
        )
    if not response.get("code"):
        raise ClassroomAuthenticationRequired("O Google não retornou um código de autorização.")
    try:
        # O state já foi validado acima. Usar code permite também localhost HTTP
        # sem desativar a exigência de HTTPS da biblioteca para endpoints OAuth.
        pending.flow.fetch_token(code=response["code"], timeout=30)
        credentials = pending.flow.credentials
    except Warning as exc:
        credentials = recover_equivalent_scope_credentials(pending.flow, exc)
    except OAuth2Error as exc:
        raise ClassroomAuthenticationRequired(
            "Não foi possível concluir a autorização Google. Conecte novamente."
        ) from exc
    except RequestException as exc:
        raise ClassroomAPIError(
            "Falha de conexão ao autorizar no Google. Conecte novamente."
        ) from exc
    if not credentials.valid or not credentials.refresh_token:
        raise ClassroomAuthenticationRequired(
            "O Google não devolveu um refresh token utilizável. Conecte novamente."
        )
    if not _credentials_have_required_scopes(credentials):
        raise ClassroomAuthenticationRequired(
            "Autorize as três permissões de leitura do Classroom e conecte novamente."
        )
    return credentials


class OAuthCallbackMailbox:
    """Códigos de uso único com prazo curto; nenhum token ou cliente é guardado."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, str] | None]] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for state, (created_at, _) in list(self._entries.items()):
            if now - created_at >= AUTHORIZATION_TTL:
                del self._entries[state]

    def register(self, pending: CloudAuthorization) -> None:
        with self._lock:
            self._prune()
            if len(self._entries) >= 1000:
                raise ClassroomAPIError("Muitas autorizações em andamento. Tente mais tarde.")
            self._entries[pending.state] = (pending.created_at, None)

    def deliver(self, response: Mapping[str, str]) -> bool:
        with self._lock:
            self._prune()
            state = response.get("state", "")
            entry = self._entries.get(state)
            if entry is None or entry[1] is not None:
                return False
            # Escopos e mensagens arbitrárias da URL não são usados nem exibidos.
            self._entries[state] = (entry[0], {
                key: response[key] for key in ("state", "code", "error") if key in response
            })
            return True

    def take(self, state: str) -> dict[str, str] | None:
        with self._lock:
            self._prune()
            entry = self._entries.get(state)
            if entry is None or entry[1] is None:
                return None
            del self._entries[state]
            return entry[1]

    def discard(self, state: str) -> None:
        with self._lock:
            self._entries.pop(state, None)
