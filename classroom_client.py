"""Cliente somente leitura para a API do Google Classroom.

Este módulo não depende do Streamlit. Ele concentra autenticação, paginação e
coleta para que a interface possa ser testada sem executar chamadas ao importar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS_PATH = BASE_DIR / "credentials.json"
DEFAULT_TOKEN_PATH = BASE_DIR / "token.json"
LOGGER = logging.getLogger(__name__)

COURSEWORK_STUDENTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly"
)
STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/classroom.student-submissions.students.readonly"
)

# Escopos mínimos de leitura já previstos no projeto. Não solicitamos e-mail,
# anexos, escrita no Classroom nem dados de login/tempo de permanência.
SCOPES = (
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    COURSEWORK_STUDENTS_READONLY_SCOPE,
)

# O Google devolveu o identificador abaixo para a permissão somente leitura
# solicitada pelo primeiro. O oauthlib transforma essa substituição observada
# em uma exceção Warning; qualquer mudança diferente continua sendo rejeitada.
_SCOPE_ALIASES = {
    COURSEWORK_STUDENTS_READONLY_SCOPE: STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE,
}
_LOCAL_OAUTH_LOCK = threading.Lock()


class ClassroomConfigurationError(RuntimeError):
    """Configuração local/Cloud ausente ou inválida."""


class ClassroomAuthenticationRequired(RuntimeError):
    """A conta precisa ser autorizada ou autorizada novamente."""


class ClassroomAPIError(RuntimeError):
    """Erro amigável produzido ao consultar a API do Classroom."""


def _normalized_scopes(scopes: Any) -> frozenset[str]:
    if not scopes:
        return frozenset()
    if isinstance(scopes, str):
        scopes = scopes.split()
    return frozenset(_SCOPE_ALIASES.get(str(scope), str(scope)) for scope in scopes)


def recover_equivalent_scope_credentials(
    flow: InstalledAppFlow, scope_warning: Warning
) -> Credentials:
    """Recupera o token emitido apenas para o mapeamento observado do Classroom."""

    token = getattr(scope_warning, "token", None)
    old_scopes = getattr(scope_warning, "old_scope", ())
    new_scopes = getattr(scope_warning, "new_scope", ())
    required = _normalized_scopes(SCOPES)
    required_token_fields = ("access_token", "expires_at", "refresh_token")
    if (
        not isinstance(token, Mapping)
        or any(not token.get(field) for field in required_token_fields)
        or _normalized_scopes(old_scopes) != required
        or _normalized_scopes(new_scopes) != required
    ):
        raise ClassroomAuthenticationRequired(
            "O Google retornou permissões diferentes das solicitadas. "
            "Revise os escopos OAuth e autorize novamente."
        ) from scope_warning

    # A troca do authorization code já ocorreu. O oauthlib anexa a resposta de
    # token à exceção antes de interromper a atribuição à sessão.
    flow.oauth2session.token = dict(token)
    credentials = flow.credentials
    LOGGER.info("OAuth retornou o mapeamento equivalente de escopo do Classroom.")
    return credentials


@dataclass(frozen=True)
class ClassroomSnapshot:
    """Recorte mínimo usado pelo painel, sem e-mails, notas ou anexos."""

    course: dict[str, Any]
    students: list[dict[str, Any]]
    coursework: list[dict[str, Any]]
    submissions: list[dict[str, Any]]
    collected_at: str


def save_authorized_user_credentials(
    credentials: Credentials, token_path: Path = DEFAULT_TOKEN_PATH
) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{token_path.name}.tmp.",
        dir=token_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1  # a partir daqui o context manager é dono do descritor.
        with handle:
            handle.write(credentials.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(token_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def load_local_credentials(
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> Credentials:
    """Carrega e renova o token local, sem iniciar interação com o navegador."""

    if not token_path.is_file():
        raise ClassroomAuthenticationRequired("A conta Google ainda não foi autorizada.")

    try:
        # Não passe SCOPES aqui: isso sobrescreveria os escopos registrados no
        # JSON e faria ``has_scopes`` aprovar até um token incompleto.
        credentials = Credentials.from_authorized_user_file(str(token_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ClassroomAuthenticationRequired(
            "O token local está ilegível. Autorize a conta novamente."
        ) from exc

    if not credentials.has_scopes(SCOPES):
        raise ClassroomAuthenticationRequired(
            "Os escopos do token mudaram. Autorize a conta novamente."
        )

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            raise ClassroomAuthenticationRequired(
                "A autorização expirou ou foi revogada. Autorize a conta novamente."
            ) from exc
        save_authorized_user_credentials(credentials, token_path)

    if not credentials.valid:
        raise ClassroomAuthenticationRequired(
            "O token não pode ser renovado. Autorize a conta novamente."
        )

    return credentials


def authorize_local_account(
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    token_path: Path = DEFAULT_TOKEN_PATH,
    *,
    timeout_seconds: int = 300,
) -> Credentials:
    """Abre a página Google, recebe o retorno localhost e salva ``token.json``.

    IMPORTANTE SOBRE O E-MAIL: não existe um campo de e-mail neste código. A
    conta docente é escolhida/digitada na página oficial do Google. O fluxo
    também imprime no terminal a URL de autorização caso o navegador não abra.
    """

    if not _LOCAL_OAUTH_LOCK.acquire(blocking=False):
        raise ClassroomAuthenticationRequired(
            "Já existe uma autorização Google em andamento. Conclua a aba aberta "
            "e atualize o painel."
        )

    try:
        if not credentials_path.is_file():
            raise ClassroomConfigurationError(
                f"Arquivo OAuth não encontrado: {credentials_path.name}."
            )

        try:
            client_config = json.loads(credentials_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ClassroomConfigurationError(
                f"{credentials_path.name} não é um JSON OAuth válido."
            ) from exc

        if "installed" not in client_config:
            raise ClassroomConfigurationError(
                "Para o localhost, credentials.json deve ser do tipo Desktop app."
            )

        LOGGER.info("OAuth local iniciado. Token path: %s", token_path.resolve())
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        try:
            credentials = flow.run_local_server(
                port=0,
                open_browser=True,
                timeout_seconds=timeout_seconds,
                authorization_prompt_message=(
                    "Abra esta URL para escolher a conta Google que administra "
                    "as turmas: {url}"
                ),
                success_message=(
                    "Autorização concluída. Você pode fechar esta aba e voltar "
                    "ao painel."
                ),
                access_type="offline",
                prompt="consent",
            )
        except Warning as exc:
            credentials = recover_equivalent_scope_credentials(flow, exc)

        if credentials is None or not credentials.token or not credentials.valid:
            raise ClassroomAuthenticationRequired(
                "O Google não devolveu credenciais utilizáveis. Autorize novamente."
            )
        if not credentials.refresh_token:
            raise ClassroomAuthenticationRequired(
                "O Google não devolveu um refresh token. Autorize novamente."
            )

        LOGGER.info(
            "Credenciais OAuth recebidas: True. Refresh token presente: True."
        )
        save_authorized_user_credentials(credentials, token_path)
        LOGGER.info("Token salvo: %s", token_path.is_file())
        return credentials
    finally:
        _LOCAL_OAUTH_LOCK.release()


def credentials_from_cloud_secrets(secret: Mapping[str, Any]) -> Credentials:
    """Monta credenciais da conta fixa guardadas no Secrets do Streamlit."""

    required = ("client_id", "client_secret", "refresh_token")
    missing = [
        key
        for key in required
        if not str(secret.get(key, "")).strip()
        or str(secret.get(key, "")).strip().startswith("SEU_")
    ]
    if missing:
        raise ClassroomConfigurationError(
            "Secrets incompletos em [google_oauth]: " + ", ".join(missing)
        )

    credentials = Credentials(
        token=None,
        refresh_token=str(secret["refresh_token"]),
        token_uri=str(
            secret.get("token_uri", "https://oauth2.googleapis.com/token")
        ),
        client_id=str(secret["client_id"]),
        client_secret=str(secret["client_secret"]),
        scopes=SCOPES,
    )
    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        raise ClassroomAuthenticationRequired(
            "O refresh token configurado no Streamlit Secrets expirou ou é inválido."
        ) from exc
    return credentials


def local_auth_cache_key(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Fingerprint estável: renovar só o access token não invalida o snapshot."""

    try:
        info = json.loads(token_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "local:sem-token"
    stored_scopes = info.get("scopes") or []
    if isinstance(stored_scopes, str):
        stored_scopes = stored_scopes.split()
    material = {
        "version": 1,
        "client_id": info.get("client_id", ""),
        "client_secret": info.get("client_secret", ""),
        "refresh_token": info.get("refresh_token", ""),
        "token_uri": info.get("token_uri", ""),
        "scopes": sorted(stored_scopes),
        "required_scopes": sorted(SCOPES),
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"local:{digest}"


def cloud_auth_cache_key(secret: Mapping[str, Any]) -> str:
    """Identificador irreversível para separar caches de autorizações Cloud."""

    material = {
        "version": 1,
        "client_id": secret.get("client_id", ""),
        "client_secret": secret.get("client_secret", ""),
        "refresh_token": secret.get("refresh_token", ""),
        "token_uri": secret.get("token_uri", "https://oauth2.googleapis.com/token"),
        "required_scopes": sorted(SCOPES),
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"cloud:{digest}"


def build_classroom_service(credentials: Credentials) -> Resource:
    return build(
        "classroom",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )


def _execute_paginated(
    request_factory: Callable[[str | None], Any], response_key: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = request_factory(page_token).execute(num_retries=3)
        records.extend(response.get(response_key, []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return records


def list_teacher_courses(service: Resource) -> list[dict[str, Any]]:
    """Lista, com paginação, somente turmas ativas em que a conta é docente."""

    def request(page_token: str | None) -> Any:
        params: dict[str, Any] = {
            "teacherId": "me",
            "courseStates": ["ACTIVE"],
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        return service.courses().list(**params)

    try:
        courses = _execute_paginated(request, "courses")
    except HttpError as exc:
        raise classroom_api_error(exc) from exc
    sanitized = [
        {
            "id": course.get("id", ""),
            "name": course.get("name", "Turma sem nome"),
            "section": course.get("section", ""),
            "descriptionHeading": course.get("descriptionHeading", ""),
            "courseState": course.get("courseState", ""),
            "alternateLink": course.get("alternateLink", ""),
        }
        for course in courses
        if course.get("id")
    ]
    return sorted(sanitized, key=lambda course: course["name"].casefold())


def _get_course(service: Resource, course_id: str) -> dict[str, Any]:
    course = service.courses().get(id=course_id).execute(num_retries=3)
    return {
        "id": course.get("id", course_id),
        "name": course.get("name", "Turma sem nome"),
        "section": course.get("section", ""),
        "descriptionHeading": course.get("descriptionHeading", ""),
        "courseState": course.get("courseState", ""),
        "alternateLink": course.get("alternateLink", ""),
    }


def _list_students(service: Resource, course_id: str) -> list[dict[str, Any]]:
    def request(page_token: str | None) -> Any:
        params: dict[str, Any] = {"courseId": course_id, "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        return service.courses().students().list(**params)

    students = _execute_paginated(request, "students")
    result: list[dict[str, Any]] = []
    for student in students:
        profile = student.get("profile", {})
        name = profile.get("name", {})
        user_id = student.get("userId", "")
        if user_id:
            result.append(
                {
                    "userId": user_id,
                    "fullName": name.get("fullName") or "Nome não informado",
                }
            )
    return sorted(result, key=lambda student: student["fullName"].casefold())


def _list_coursework(service: Resource, course_id: str) -> list[dict[str, Any]]:
    def request(page_token: str | None) -> Any:
        params: dict[str, Any] = {
            "courseId": course_id,
            "courseWorkStates": ["PUBLISHED"],
            "orderBy": "dueDate asc,updateTime asc",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        return service.courses().courseWork().list(**params)

    coursework = _execute_paginated(request, "courseWork")
    fields = (
        "id",
        "title",
        "state",
        "creationTime",
        "updateTime",
        "dueDate",
        "dueTime",
        "scheduledTime",
        "workType",
        "topicId",

        "alternateLink",
    )
    return [
        {field: item[field] for field in fields if field in item}
        for item in coursework
        if item.get("id")
    ]


def _list_submissions(
    service: Resource, course_id: str, *, has_coursework: bool
) -> list[dict[str, Any]]:
    if not has_coursework:
        return []

    def request(page_token: str | None) -> Any:
        params: dict[str, Any] = {
            "courseId": course_id,
            # O hífen é suportado oficialmente e traz as entregas de todas as
            # atividades, evitando uma chamada separada para cada atividade.
            "courseWorkId": "-",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        return (
            service.courses()
            .courseWork()
            .studentSubmissions()
            .list(**params)
        )

    submissions = _execute_paginated(request, "studentSubmissions")
    fields = (
        "id",
        "courseWorkId",
        "userId",
        "state",
        "late",
        "creationTime",
        "updateTime",
    )
    return [
        {field: item[field] for field in fields if field in item}
        for item in submissions
        if item.get("courseWorkId") and item.get("userId")
    ]


def collect_course_snapshot(service: Resource, course_id: str) -> ClassroomSnapshot:
    """Coleta um snapshot mínimo da turma selecionada."""

    try:
        course = _get_course(service, course_id)
        students = _list_students(service, course_id)
        coursework = _list_coursework(service, course_id)
        submissions = _list_submissions(
            service, course_id, has_coursework=bool(coursework)
        )
    except HttpError as exc:
        raise classroom_api_error(exc) from exc

    published_ids = {item["id"] for item in coursework}
    submissions = [
        item for item in submissions if item.get("courseWorkId") in published_ids
    ]
    return ClassroomSnapshot(
        course=course,
        students=students,
        coursework=coursework,
        submissions=submissions,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


def classroom_api_error(error: HttpError) -> ClassroomAPIError:
    status = getattr(error.resp, "status", None)
    messages = {
        400: "A API recusou um parâmetro da consulta.",
        401: "A autorização Google não é mais válida. Conecte a conta novamente.",
        403: (
            "A conta não tem permissão de professora para consultar todos os dados "
            "desta turma, ou algum escopo ainda não foi autorizado."
        ),
        404: "A turma selecionada não existe mais ou não está acessível.",
        429: "O limite temporário de consultas da API foi atingido. Tente novamente.",
    }
    if status in messages:
        return ClassroomAPIError(messages[status])
    if status and status >= 500:
        return ClassroomAPIError(
            "O Google Classroom está temporariamente indisponível. Tente novamente."
        )
    return ClassroomAPIError("Não foi possível consultar o Google Classroom.")
