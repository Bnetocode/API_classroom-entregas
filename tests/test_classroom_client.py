from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch
from urllib.parse import parse_qs, urlsplit

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence
from httplib2 import ServerNotFoundError

import provision_cloud_token as cloud_provisioner
from classroom_client import (
    SCOPES,
    STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE,
    ClassroomAPIError,
    ClassroomAuthenticationRequired,
    ClassroomConfigurationError,
    _execute_paginated,
    authorize_local_account,
    cloud_auth_cache_key,
    collect_course_snapshot,
    credentials_from_cloud_secrets,
    list_teacher_courses,
    load_local_credentials,
    local_auth_cache_key,
    save_authorized_user_credentials,
)
from provision_cloud_token import validate_web_credentials


def _scope_change_warning(
    new_scopes: list[str] | None = None, *, include_token: bool = True
) -> Warning:
    warning = Warning("scope changed")
    warning.old_scope = list(SCOPES)
    warning.new_scope = new_scopes or [
        STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE
        if scope.endswith("classroom.coursework.students.readonly")
        else scope
        for scope in SCOPES
    ]
    if include_token:
        warning.token = {
            "access_token": "access-test",
            "refresh_token": "refresh-test",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": 4_102_444_800,
            "scope": list(warning.new_scope),
        }
    return warning


class ClassroomClientTests(unittest.TestCase):
    def test_courses_are_teacher_only_paginated_and_sorted(self) -> None:
        first_request = MagicMock()
        first_request.execute.return_value = {
            "courses": [{"id": "2", "name": "Zeta"}],
            "nextPageToken": "next",
        }
        second_request = MagicMock()
        second_request.execute.return_value = {
            "courses": [{"id": "1", "name": "Alfa"}]
        }

        courses_api = MagicMock()
        courses_api.list.side_effect = [first_request, second_request]
        service = MagicMock()
        service.courses.return_value = courses_api

        result = list_teacher_courses(service)

        self.assertEqual([course["name"] for course in result], ["Alfa", "Zeta"])
        self.assertEqual(
            courses_api.list.call_args_list,
            [
                call(
                    teacherId="me",
                    courseStates=["ACTIVE"],
                    pageSize=100,
                    fields=(
                        "nextPageToken,courses(id,name,section,descriptionHeading,"
                        "courseState,alternateLink)"
                    ),
                ),
                call(
                    teacherId="me",
                    courseStates=["ACTIVE"],
                    pageSize=100,
                    fields=(
                        "nextPageToken,courses(id,name,section,descriptionHeading,"
                        "courseState,alternateLink)"
                    ),
                    pageToken="next",
                ),
            ],
        )

    def test_snapshot_paginates_with_real_client_and_minimal_fields(self) -> None:
        # Usa o discovery instalado e o transporte HTTP simulado. Assim a
        # validação dos nomes dos parâmetros e a montagem das URLs são reais.
        responses = [
            {"id": "course", "name": "Teste", "ownerId": "private-owner"},
            {
                "students": [{
                    "userId": "student-2",
                    "profile": {
                        "name": {"fullName": "Zeta"},
                        "emailAddress": "private@example.test",
                    },
                }],
                "nextPageToken": "students-next",
            },
            {"students": [{
                "userId": "student-1", "profile": {"name": {"fullName": "Alfa"}}
            }]},
            {
                "courseWork": [{
                    "id": "activity-1", "title": "Módulo 1",
                    "state": "PUBLISHED", "description": "private-description",
                    "materials": [{"link": {"url": "https://example.test"}}],
                }],
                "nextPageToken": "work-next",
            },
            {"courseWork": [{"id": "activity-2", "title": "Módulo 2"}]},
            {
                "studentSubmissions": [{
                    "id": "submission-1", "courseWorkId": "activity-1",
                    "userId": "student-1", "state": "TURNED_IN",
                    "assignedGrade": 10, "assignmentSubmission": {"attachments": []},
                }],
                "nextPageToken": "submissions-next",
            },
            {"studentSubmissions": [
                {"id": "submission-2", "courseWorkId": "activity-2",
                 "userId": "student-2", "state": "NEW"},
                {"id": "unpublished", "courseWorkId": "draft",
                 "userId": "student-1", "state": "NEW"},
            ]},
        ]
        http = HttpMockSequence([
            ({"status": "200"}, json.dumps(response)) for response in responses
        ])
        service = build("classroom", "v1", http=http, static_discovery=True)

        snapshot = collect_course_snapshot(service, "course")

        self.assertEqual([s["fullName"] for s in snapshot.students], ["Alfa", "Zeta"])
        self.assertEqual(
            [a["id"] for a in snapshot.coursework], ["activity-1", "activity-2"]
        )
        self.assertEqual(
            [s["id"] for s in snapshot.submissions], ["submission-1", "submission-2"]
        )
        self.assertNotIn("ownerId", snapshot.course)
        self.assertNotIn("emailAddress", repr(snapshot.students))
        self.assertNotIn("materials", snapshot.coursework[0])
        self.assertNotIn("description", snapshot.coursework[0])
        self.assertNotIn("assignedGrade", snapshot.submissions[0])
        self.assertNotIn("assignmentSubmission", snapshot.submissions[0])
        self.assertTrue(snapshot.collected_at.endswith("+00:00"))
        self.assertEqual(len(http.request_sequence), 7)
        urls = [urlsplit(request[0]) for request in http.request_sequence]
        queries = [parse_qs(url.query) for url in urls]
        self.assertEqual(queries[2]["pageToken"], ["students-next"])
        self.assertEqual(queries[4]["pageToken"], ["work-next"])
        self.assertEqual(queries[6]["pageToken"], ["submissions-next"])
        self.assertTrue(all(request[1] == "GET" for request in http.request_sequence))
        for query in queries[1:]:
            self.assertIn("nextPageToken", query["fields"][0])
        for url in urls[-2:]:
            self.assertTrue(url.path.endswith("/courseWork/-/studentSubmissions"))
        for query in queries[3:5]:
            self.assertEqual(query["courseWorkStates"], ["PUBLISHED"])

    def test_snapshot_without_coursework_skips_submissions_request(self) -> None:
        responses = [{"id": "empty", "name": "Turma vazia"}, {}, {}]
        http = HttpMockSequence([
            ({"status": "200"}, json.dumps(response)) for response in responses
        ])
        service = build("classroom", "v1", http=http, static_discovery=True)

        snapshot = collect_course_snapshot(service, "empty")

        self.assertEqual(snapshot.students, [])
        self.assertEqual(snapshot.coursework, [])
        self.assertEqual(snapshot.submissions, [])
        self.assertEqual(len(http.request_sequence), 3)

    def test_api_network_failures_produce_retriable_message(self) -> None:
        for error in (
            TransportError("offline"), ServerNotFoundError("offline"),
            TimeoutError("offline"),
        ):
            with self.subTest(error=type(error).__name__):
                service = MagicMock()
                service.courses.return_value.list.return_value.execute.side_effect = error
                with self.assertRaisesRegex(ClassroomAPIError, "conexão"):
                    list_teacher_courses(service)
                service.courses.return_value.get.return_value.execute.side_effect = error
                with self.assertRaisesRegex(ClassroomAPIError, "conexão"):
                    collect_course_snapshot(service, "course")

    def test_refresh_failure_during_api_call_requests_new_authorization(self) -> None:
        service = MagicMock()
        service.courses.return_value.list.return_value.execute.side_effect = (
            RefreshError("revoked")
        )
        with self.assertRaises(ClassroomAuthenticationRequired):
            list_teacher_courses(service)
        service.courses.return_value.get.return_value.execute.side_effect = (
            RefreshError("revoked")
        )
        with self.assertRaises(ClassroomAuthenticationRequired):
            collect_course_snapshot(service, "course")

    def test_paginated_data_rejects_invalid_record_shape(self) -> None:
        request = MagicMock()
        request.execute.return_value = {"courses": ["invalid-record"]}

        with self.assertRaises(ClassroomAPIError):
            _execute_paginated(lambda _: request, "courses")

    def test_paginated_data_rejects_repeated_page_token(self) -> None:
        request = MagicMock()
        request.execute.return_value = {
            "courses": [],
            "nextPageToken": "repeat-token",
        }

        with self.assertRaises(ClassroomAPIError):
            _execute_paginated(lambda _: request, "courses")

        self.assertEqual(request.execute.call_count, 2)

    @patch("classroom_client.Credentials.refresh")
    def test_cloud_credentials_use_matching_secret_values(self, refresh) -> None:
        credentials = credentials_from_cloud_secrets(
            {
                "client_id": "client-test",
                "client_secret": "secret-test",
                "refresh_token": "refresh-test",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )

        self.assertEqual(credentials.client_id, "client-test")
        self.assertEqual(credentials.client_secret, "secret-test")
        self.assertEqual(credentials.refresh_token, "refresh-test")
        refresh.assert_called_once()

    def test_cloud_credentials_reject_incomplete_secrets(self) -> None:
        with self.assertRaises(ClassroomConfigurationError):
            credentials_from_cloud_secrets({"client_id": "only-one-value"})

    @patch("classroom_client.Credentials.refresh")
    def test_cloud_credentials_reject_non_text_values_without_network(
        self, refresh
    ) -> None:
        base = {
            "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"
        }
        for key in base:
            for value in (None, 123, [], {}, "  ", "SEU_VALOR"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ClassroomConfigurationError):
                        credentials_from_cloud_secrets({**base, key: value})
        refresh.assert_not_called()

    @patch("classroom_client.Credentials.refresh", side_effect=TransportError("offline"))
    def test_cloud_network_error_does_not_request_reauthorization(self, refresh) -> None:
        with self.assertRaisesRegex(ClassroomAPIError, "conexão"):
            credentials_from_cloud_secrets({
                "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"
            })
        refresh.assert_called_once()

    @patch(
        "classroom_client.Credentials.refresh",
        side_effect=RefreshError("expired"),
    )
    def test_cloud_refresh_error_requests_new_authorization(self, refresh) -> None:
        with self.assertRaises(ClassroomAuthenticationRequired):
            credentials_from_cloud_secrets(
                {
                    "client_id": "client-test",
                    "client_secret": "secret-test",
                    "refresh_token": "refresh-test",
                }
            )
        refresh.assert_called_once()

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_prints_url_and_saves_private_json(self, from_file) -> None:
        fake_credentials = MagicMock()
        fake_credentials.to_json.return_value = '{"refresh_token": "test"}'
        fake_flow = MagicMock()
        fake_flow.run_local_server.return_value = fake_credentials
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            token_path = Path(directory) / "token.json"
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            result = authorize_local_account(credentials_path, token_path)

            self.assertIs(result, fake_credentials)
            kwargs = fake_flow.run_local_server.call_args.kwargs
            self.assertEqual(kwargs["port"], 0)
            self.assertTrue(kwargs["open_browser"])
            self.assertIn("{url}", kwargs["authorization_prompt_message"])
            self.assertNotIn("include_granted_scopes", kwargs)
            self.assertTrue(token_path.is_file())
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_token_temporary_file_is_private_from_creation(self) -> None:
        credentials = MagicMock()

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            observed_modes: list[int] = []

            def serialize() -> str:
                temporary_files = list(token_path.parent.glob("token.json.tmp.*"))
                observed_modes.extend(
                    path.stat().st_mode & 0o777 for path in temporary_files
                )
                return '{"token": "test"}'

            credentials.to_json.side_effect = serialize
            save_authorized_user_credentials(credentials, token_path)

            self.assertEqual(observed_modes, [0o600])
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(token_path.parent.glob("token.json.tmp.*")), [])

    def test_concurrent_token_saves_use_independent_temporary_files(self) -> None:
        workers = 8
        barrier = threading.Barrier(workers)

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"

            def save(index: int) -> None:
                credentials = MagicMock()

                def serialize() -> str:
                    barrier.wait(timeout=5)
                    return json.dumps({"token": f"test-{index}"})

                credentials.to_json.side_effect = serialize
                save_authorized_user_credentials(credentials, token_path)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(save, range(workers)))

            saved = json.loads(token_path.read_text(encoding="utf-8"))
            self.assertIn(saved["token"], {f"test-{index}" for index in range(workers)})
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(token_path.parent.glob("token.json.tmp.*")), [])

    def test_failed_token_serialization_cleans_temporary_file(self) -> None:
        credentials = MagicMock()
        credentials.to_json.side_effect = RuntimeError("test failure")

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"

            with self.assertRaises(RuntimeError):
                save_authorized_user_credentials(credentials, token_path)

            self.assertFalse(token_path.exists())
            self.assertEqual(list(token_path.parent.glob("token.json.tmp.*")), [])

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_recovers_equivalent_classroom_scope(self, from_file) -> None:
        scope_warning = _scope_change_warning()
        fake_credentials = MagicMock()
        fake_credentials.token = "access-test"
        fake_credentials.refresh_token = "refresh-test"
        fake_credentials.valid = True
        fake_credentials.to_json.return_value = '{"refresh_token": "test"}'
        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = scope_warning
        fake_flow.credentials = fake_credentials
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            token_path = Path(directory) / "token.json"
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            result = authorize_local_account(credentials_path, token_path)

            self.assertIs(result, fake_credentials)
            self.assertEqual(fake_flow.oauth2session.token, dict(scope_warning.token))
            self.assertTrue(token_path.is_file())

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_rejects_non_equivalent_scope_change(self, from_file) -> None:
        new_scopes = list(SCOPES) + [
            "https://www.googleapis.com/auth/drive.readonly"
        ]
        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = _scope_change_warning(new_scopes)
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            token_path = Path(directory) / "token.json"
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            with self.assertRaises(ClassroomAuthenticationRequired):
                authorize_local_account(credentials_path, token_path)

            self.assertFalse(token_path.exists())

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_rejects_scope_warning_without_token(self, from_file) -> None:
        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = _scope_change_warning(
            include_token=False
        )
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            token_path = Path(directory) / "token.json"
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            with self.assertRaises(ClassroomAuthenticationRequired):
                authorize_local_account(credentials_path, token_path)

            self.assertFalse(token_path.exists())

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_rejects_web_client(self, from_file) -> None:
        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            credentials_path.write_text('{"web": {}}', encoding="utf-8")

            with self.assertRaises(ClassroomConfigurationError):
                authorize_local_account(credentials_path)

        from_file.assert_not_called()

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_concurrent_local_oauth_does_not_start_second_flow(self, from_file) -> None:
        import classroom_client

        self.assertTrue(classroom_client._LOCAL_OAUTH_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(
                ClassroomAuthenticationRequired, "em andamento"
            ):
                authorize_local_account()
        finally:
            classroom_client._LOCAL_OAUTH_LOCK.release()

        from_file.assert_not_called()

    @patch("classroom_client.InstalledAppFlow.from_client_secrets_file")
    def test_local_oauth_lock_is_released_after_failure(self, from_file) -> None:
        import classroom_client

        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = RuntimeError("test failure")
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials.json"
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                authorize_local_account(credentials_path)

        acquired = classroom_client._LOCAL_OAUTH_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            classroom_client._LOCAL_OAUTH_LOCK.release()

    def test_missing_local_token_requires_auth_without_starting_oauth(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            with self.assertRaises(ClassroomAuthenticationRequired):
                load_local_credentials(token_path)

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_valid_local_token_is_reused_without_refresh(self, from_file) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = True
        credentials.expired = False
        credentials.valid = True
        from_file.return_value = credentials

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("{}", encoding="utf-8")

            result = load_local_credentials(token_path)

        self.assertIs(result, credentials)
        credentials.refresh.assert_not_called()

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_local_token_accepts_equivalent_classroom_scope_alias(
        self, from_file
    ) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = False
        credentials.granted_scopes = None
        credentials.scopes = [
            STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE
            if scope.endswith("classroom.coursework.students.readonly")
            else scope
            for scope in SCOPES
        ]
        credentials.expired = False
        credentials.valid = True
        from_file.return_value = credentials

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("{}", encoding="utf-8")

            result = load_local_credentials(token_path)

        self.assertIs(result, credentials)

    @patch(
        "classroom_client.Credentials.from_authorized_user_file",
        side_effect=AttributeError("invalid token structure"),
    )
    def test_invalid_token_structure_requires_new_authorization(self, from_file) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("[]", encoding="utf-8")

            with self.assertRaises(ClassroomAuthenticationRequired):
                load_local_credentials(token_path)

        from_file.assert_called_once()

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_expired_local_token_is_refreshed_and_saved_same_path(
        self, from_file
    ) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = True
        credentials.expired = True
        credentials.refresh_token = "refresh-test"
        credentials.valid = True
        credentials.to_json.return_value = '{"token": "renewed-test"}'
        from_file.return_value = credentials

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("{}", encoding="utf-8")

            result = load_local_credentials(token_path)

            self.assertIs(result, credentials)
            credentials.refresh.assert_called_once()
            self.assertEqual(
                token_path.read_text(encoding="utf-8"),
                '{"token": "renewed-test"}',
            )
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_local_token_with_missing_scope_requires_new_authorization(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text(
                """{
                    "token": "access-test",
                    "refresh_token": "refresh-test",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "client_id": "client-test",
                    "client_secret": "secret-test",
                    "scopes": [
                        "https://www.googleapis.com/auth/classroom.courses.readonly"
                    ]
                }""",
                encoding="utf-8",
            )

            with self.assertRaises(ClassroomAuthenticationRequired):
                load_local_credentials(token_path)

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_local_refresh_error_requests_new_authorization(self, from_file) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = True
        credentials.expired = True
        credentials.refresh_token = "refresh-test"
        credentials.refresh.side_effect = RefreshError("expired")
        from_file.return_value = credentials

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ClassroomAuthenticationRequired):
                load_local_credentials(token_path)

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_local_network_error_keeps_saved_token(self, from_file) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = True
        credentials.expired = True
        credentials.refresh_token = "refresh-test"
        credentials.refresh.side_effect = TransportError("offline")
        from_file.return_value = credentials

        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text('{"token": "original-test"}', encoding="utf-8")
            with self.assertRaisesRegex(ClassroomAPIError, "conexão"):
                load_local_credentials(token_path)
            self.assertEqual(token_path.read_text(), '{"token": "original-test"}')

    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_local_refresh_token_recovers_missing_access_token(self, from_file) -> None:
        credentials = MagicMock()
        credentials.has_scopes.return_value = True
        credentials.expired = False
        credentials.valid = False
        credentials.refresh_token = "refresh-test"
        credentials.to_json.return_value = '{"token": "renewed-test"}'

        def refresh(request):
            credentials.valid = True

        credentials.refresh.side_effect = refresh
        from_file.return_value = credentials
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_text("{}", encoding="utf-8")
            self.assertIs(load_local_credentials(token_path), credentials)
            credentials.refresh.assert_called_once()
            self.assertEqual(token_path.read_text(), '{"token": "renewed-test"}')

    def test_local_cache_key_handles_invalid_token_encoding(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            token_path.write_bytes(b"\xff\xfe")
            self.assertEqual(local_auth_cache_key(token_path), "local:sem-token")

    def test_local_cache_key_ignores_access_token_refresh(self) -> None:
        with TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.json"
            base = {
                "token": "access-one",
                "refresh_token": "refresh-stable",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-stable",
                "client_secret": "secret-stable",
                "scopes": list(SCOPES),
            }
            token_path.write_text(json.dumps(base), encoding="utf-8")
            first = local_auth_cache_key(token_path)
            base["token"] = "access-two"
            base["expiry"] = "2026-08-14T14:00:00Z"
            token_path.write_text(json.dumps(base), encoding="utf-8")
            second = local_auth_cache_key(token_path)

            self.assertEqual(first, second)

    def test_cloud_cache_key_changes_when_any_credential_changes(self) -> None:
        base = {
            "client_id": "client",
            "client_secret": "secret-one",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        first = cloud_auth_cache_key(base)
        rotated = {**base, "client_secret": "secret-two"}

        self.assertNotEqual(first, cloud_auth_cache_key(rotated))

    def test_cloud_provisioner_requires_exact_web_redirect(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "credentials_web.json"
            path.write_text(
                '{"web": {"redirect_uris": ["http://localhost:8080/"]}}',
                encoding="utf-8",
            )
            validate_web_credentials(path)

            path.write_text(
                '{"web": {"redirect_uris": ["http://localhost:9999/"]}}',
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                validate_web_credentials(path)

            path.write_text('{"web": []}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                validate_web_credentials(path)

    @patch("provision_cloud_token.InstalledAppFlow.from_client_secrets_file")
    def test_cloud_provisioner_recovers_equivalent_classroom_scope(
        self, from_file
    ) -> None:
        scope_warning = _scope_change_warning()
        fake_credentials = MagicMock()
        fake_credentials.token = "access-test"
        fake_credentials.refresh_token = "refresh-test"
        fake_credentials.valid = True
        fake_credentials.to_json.return_value = '{"refresh_token": "test"}'
        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = scope_warning
        fake_flow.credentials = fake_credentials
        from_file.return_value = fake_flow

        with TemporaryDirectory() as directory:
            credentials_path = Path(directory) / "credentials_web.json"
            token_path = Path(directory) / "token_cloud.json"
            credentials_path.write_text(
                '{"web": {"redirect_uris": ["http://localhost:8080/"]}}',
                encoding="utf-8",
            )
            with (
                patch.object(
                    cloud_provisioner, "WEB_CREDENTIALS_PATH", credentials_path
                ),
                patch.object(cloud_provisioner, "CLOUD_TOKEN_PATH", token_path),
            ):
                cloud_provisioner.main()

            self.assertEqual(fake_flow.oauth2session.token, dict(scope_warning.token))
            self.assertTrue(token_path.is_file())
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            from_file.assert_called_once_with(str(credentials_path), SCOPES)
            fake_flow.run_local_server.assert_called_once_with(
                host="localhost",
                port=8080,
                open_browser=True,
                timeout_seconds=300,
                redirect_uri_trailing_slash=True,
                authorization_prompt_message=(
                    "Abra esta URL para autorizar a conta docente usada no Cloud: "
                    "{url}"
                ),
                success_message=(
                    "Autorização Cloud recebida. Você pode fechar esta aba e voltar "
                    "ao terminal."
                ),
                access_type="offline",
                prompt="consent",
            )


if __name__ == "__main__":
    unittest.main()
