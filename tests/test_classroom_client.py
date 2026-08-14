from __future__ import annotations

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, call, patch

from google.auth.exceptions import RefreshError

from classroom_client import (
    SCOPES,
    ClassroomAuthenticationRequired,
    ClassroomConfigurationError,
    authorize_local_account,
    cloud_auth_cache_key,
    credentials_from_cloud_secrets,
    list_teacher_courses,
    load_local_credentials,
    local_auth_cache_key,
)
from provision_cloud_token import validate_web_credentials


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
                ),
                call(
                    teacherId="me",
                    courseStates=["ACTIVE"],
                    pageSize=100,
                    pageToken="next",
                ),
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
