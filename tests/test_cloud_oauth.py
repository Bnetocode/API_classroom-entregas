from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from requests import Request, Response
from streamlit.testing.v1 import AppTest

from classroom_client import (
    SCOPES,
    ClassroomAPIError,
    ClassroomAuthenticationRequired,
    ClassroomConfigurationError,
    credentials_from_cloud_secrets,
)
from classroom_oauth import (
    AUTHORIZATION_TTL,
    OAuthCallbackMailbox,
    finish_cloud_authorization,
    google_secret_section,
    start_cloud_authorization,
)

CLIENT = {
    "client_id": "client-test",
    "client_secret": "secret-test",
    "token_uri": "https://oauth2.googleapis.com/token",
}
REDIRECT = "https://example.streamlit.app/"


def token_response(*args, **kwargs):
    response = Response()
    response.status_code = 200
    response.url = CLIENT["token_uri"]
    response.request = Request("POST", response.url).prepare()
    response._content = json.dumps({
        "access_token": "access-synthetic",
        "refresh_token": "refresh-synthetic",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": " ".join(SCOPES),
    }).encode()
    return response


def render_cloud_auth_fixture() -> None:
    from unittest.mock import patch
    from types import SimpleNamespace

    import app
    import streamlit as st
    from classroom_client import ClassroomAPIError, ClassroomConfigurationError
    from tests.test_cloud_oauth import token_response

    with (
        patch.object(st, "context", SimpleNamespace(url="https://example.streamlit.app/")),
        patch("requests.sessions.Session.request", side_effect=token_response),
        patch.object(app, "load_local_credentials", side_effect=AssertionError("Leitura local indevida")),
        patch("classroom_client.save_authorized_user_credentials", side_effect=AssertionError("Gravação indevida")),
    ):
        if app._handle_cloud_callback():
            return
        try:
            resolved = app._resolve_auth_mode()
            if resolved is not None:
                credentials = app._credentials_for_mode(resolved[0])
                if credentials.valid:
                    st.success("Autenticado sem arquivos")
        except (ClassroomAPIError, ClassroomConfigurationError) as exc:
            st.error(str(exc))


class CloudOAuthTests(unittest.TestCase):
    def test_secret_precedence_and_unrelated_app_password(self):
        self.assertEqual(google_secret_section({"google_credentials": CLIENT}), CLIENT)
        self.assertEqual(google_secret_section({"google_oauth": CLIENT}), CLIENT)
        self.assertEqual(google_secret_section({
            "google_credentials": {}, "google_oauth": CLIENT,
        }), {})
        self.assertIsNone(google_secret_section({"app_password": "unrelated"}))
        with self.assertRaises(ClassroomConfigurationError):
            google_secret_section({"google_credentials": "invalid"})

    def test_absent_google_secrets_preserves_local_fallback(self):
        import app
        with (
            patch.object(app, "read_google_secrets", return_value=None),
            patch.object(app, "load_local_credentials") as local,
            patch.object(app, "local_auth_cache_key", return_value="local:test"),
            patch.object(app, "start_cloud_authorization") as cloud,
        ):
            self.assertEqual(app._resolve_auth_mode(), ("local", "local:test"))
            local.assert_called_once()
            cloud.assert_not_called()

    def test_cli_prioritizes_secrets_and_never_starts_local_oauth(self):
        import anexo_api_classroom_etapas_1a5 as cli
        with (
            patch.object(cli, "read_google_secrets", return_value=CLIENT) as read,
            patch.object(cli, "load_local_credentials") as local,
            patch.object(cli, "authorize_local_account") as authorize,
            patch("requests.sessions.Session.request", side_effect=token_response),
        ):
            with self.assertRaisesRegex(ClassroomAuthenticationRequired, "painel Streamlit"):
                cli.autenticar()
            read.return_value = {**CLIENT, "refresh_token": "refresh-synthetic"}
            self.assertTrue(cli.autenticar().valid)
            local.assert_not_called()
            authorize.assert_not_called()

    def test_real_flow_requests_offline_consent_state_and_pkce(self):
        pending = start_cloud_authorization(CLIENT, REDIRECT)
        query = parse_qs(urlsplit(pending.url).query)
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["redirect_uri"], [REDIRECT])
        self.assertEqual(query["state"], [pending.state])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(pending.flow.code_verifier)
        self.assertEqual(set(query["scope"][0].split()), set(SCOPES))
        self.assertNotIn("secret-test", pending.url)

    @patch("requests.sessions.Session.request", side_effect=token_response)
    @patch("classroom_client.save_authorized_user_credentials")
    @patch("classroom_client.Credentials.from_authorized_user_file")
    def test_complete_flow_then_refresh_without_files(self, read_file, save_file, request):
        pending = start_cloud_authorization(CLIENT, REDIRECT)
        mailbox = OAuthCallbackMailbox()
        mailbox.register(pending)
        self.assertTrue(mailbox.deliver({"state": pending.state, "code": "synthetic-code"}))
        credentials = finish_cloud_authorization(pending, mailbox.take(pending.state))
        self.assertEqual(credentials.refresh_token, "refresh-synthetic")
        self.assertTrue(credentials.valid)
        self.assertEqual(request.call_count, 1)
        grant = request.call_args.kwargs["data"]
        self.assertEqual(grant["grant_type"], "authorization_code")
        self.assertEqual(grant["code_verifier"], pending.flow.code_verifier)
        with patch("classroom_oauth.Flow.from_client_config") as flow:
            refreshed = credentials_from_cloud_secrets({
                **CLIENT, "refresh_token": credentials.refresh_token,
            })
            self.assertTrue(refreshed.valid)
            flow.assert_not_called()
        self.assertEqual(request.call_count, 2)
        read_file.assert_not_called()
        save_file.assert_not_called()

    @patch("requests.sessions.Session.request")
    def test_state_denial_expiry_and_replay_prevent_exchange(self, request):
        pending = start_cloud_authorization(CLIENT, REDIRECT)
        for response in ({"state": "wrong", "code": "x"}, {"code": "x"}):
            with self.assertRaises(ClassroomAuthenticationRequired):
                finish_cloud_authorization(pending, response)
        self.assertFalse(pending.consumed)
        with self.assertRaisesRegex(ClassroomAuthenticationRequired, "cancelado"):
            finish_cloud_authorization(pending, {"state": pending.state, "error": "access_denied"})
        with self.assertRaises(ClassroomAuthenticationRequired):
            finish_cloud_authorization(pending, {"state": pending.state, "code": "x"})
        expired = start_cloud_authorization(CLIENT, REDIRECT)
        expired.created_at -= AUTHORIZATION_TTL
        with self.assertRaisesRegex(ClassroomAuthenticationRequired, "expirada"):
            finish_cloud_authorization(expired, {"state": expired.state, "code": "x"})
        request.assert_not_called()

    def test_mailbox_isolated_single_use_and_expiring(self):
        mailbox = OAuthCallbackMailbox()
        first = start_cloud_authorization(CLIENT, REDIRECT)
        second = start_cloud_authorization(CLIENT, REDIRECT)
        mailbox.register(first)
        mailbox.register(second)
        self.assertFalse(mailbox.deliver({"state": "unknown", "code": "x"}))
        self.assertTrue(mailbox.deliver({"state": first.state, "code": "x"}))
        self.assertFalse(mailbox.deliver({"state": first.state, "code": "y"}))
        self.assertIsNone(mailbox.take(second.state))
        self.assertEqual(mailbox.take(first.state)["code"], "x")
        self.assertIsNone(mailbox.take(first.state))
        with patch("classroom_oauth.time.monotonic", return_value=time.monotonic() + AUTHORIZATION_TTL):
            self.assertFalse(mailbox.deliver({"state": second.state, "code": "x"}))

    def test_invalid_endpoints_rejected_before_network(self):
        for redirect in (123, "https://[bad/", "http://example.com/", "https://x/?q=1", "javascript:x", "https://u:p@x/"):
            with self.subTest(redirect=redirect):
                with self.assertRaises(ClassroomConfigurationError):
                    start_cloud_authorization(CLIENT, redirect)
        with self.assertRaises(ClassroomConfigurationError):
            start_cloud_authorization({**CLIENT, "token_uri": "https://other.invalid/"}, REDIRECT)
        self.assertTrue(start_cloud_authorization(CLIENT, "http://localhost:8501/").url)

    def test_missing_secrets_falls_back_without_reading_secret_values(self):
        import streamlit as st
        from classroom_oauth import read_google_secrets
        from streamlit.errors import StreamlitSecretNotFoundError
        with patch.object(st, "secrets") as missing:
            missing.__contains__.side_effect = StreamlitSecretNotFoundError("Missing")
            self.assertIsNone(read_google_secrets())

    def test_google_failures_are_safe_and_missing_token_is_rejected(self):
        from requests.exceptions import ConnectionError
        for payload in ({"access_token": "x", "token_type": "Bearer", "expires_in": 3600},
                        {"error": "invalid_grant", "error_description": "sensitive-code"}):
            pending = start_cloud_authorization(CLIENT, REDIRECT)
            response = token_response()
            response._content = json.dumps(payload).encode()
            with patch("requests.sessions.Session.request", return_value=response):
                with self.assertRaises(ClassroomAuthenticationRequired) as error:
                    finish_cloud_authorization(pending, {"state": pending.state, "code": "x"})
                self.assertNotIn("sensitive-code", str(error.exception))
        pending = start_cloud_authorization(CLIENT, REDIRECT)
        with patch("requests.sessions.Session.request", side_effect=ConnectionError("private")):
            with self.assertRaisesRegex(ClassroomAPIError, "conexão"):
                finish_cloud_authorization(pending, {"state": pending.state, "code": "x"})

    def test_scope_alias_recovery_in_web_flow(self):
        from classroom_client import STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE
        response = token_response()
        payload = json.loads(response.content)
        payload["scope"] = " ".join([
            *SCOPES[:2], STUDENT_SUBMISSIONS_STUDENTS_READONLY_SCOPE,
        ])
        response._content = json.dumps(payload).encode()
        pending = start_cloud_authorization(CLIENT, REDIRECT)
        with patch("requests.sessions.Session.request", return_value=response):
            credentials = finish_cloud_authorization(pending, {"state": pending.state, "code": "x"})
        self.assertTrue(credentials.valid)


class CloudOAuthInterfaceTests(unittest.TestCase):
    def setUp(self):
        import app
        self.mailbox = OAuthCallbackMailbox()
        self.mailbox_patch = patch.object(app, "_oauth_mailbox", return_value=self.mailbox)
        self.mailbox_patch.start()
        self.addCleanup(self.mailbox_patch.stop)

    def make_app(self, secret):
        app_test = AppTest.from_function(render_cloud_auth_fixture, default_timeout=10)
        app_test.secrets["google_credentials"] = secret
        return app_test

    def test_no_token_callback_other_tab_display_then_configured_token(self):
        for empty in ({}, {"refresh_token": ""}, {"refresh_token": "   "}):
            with self.subTest(empty=empty):
                original = self.make_app({**CLIENT, **empty}).run()
                self.assertEqual(len(original.exception), 0)
                self.assertEqual(original.title[0].value, "Conectar ao Google Classroom")
                pending = original.session_state["classroom_cloud_pending"]
                self.assertNotIn("refresh-synthetic", [item.value for item in original.code])
                callback = self.make_app({**CLIENT, **empty, "redirect_uri": REDIRECT})
                callback.query_params = {"state": pending.state, "code": "synthetic-code"}
                callback.run()
                self.assertEqual(len(callback.exception), 0)
                self.assertFalse(callback.code)
                self.assertNotIn("code", callback.query_params)
                original.run()
                self.assertEqual(len(original.exception), 0)
                self.assertIn("refresh-synthetic", [item.value for item in original.code])
                self.assertTrue(any("Copie esse token" in item.value for item in original.info))
                self.assertTrue(any("Autenticado sem arquivos" in item.value for item in original.success))
                original.run()
                self.assertEqual(len(original.get("link_button")), 0)
                other_visitor = self.make_app({**CLIENT, "redirect_uri": REDIRECT}).run()
                self.assertNotIn("refresh-synthetic", [item.value for item in other_visitor.code])
                with patch("app.start_cloud_authorization") as start:
                    configured = self.make_app({**CLIENT, "refresh_token": "refresh-synthetic"}).run()
                    self.assertEqual(len(configured.exception), 0)
                    self.assertEqual(len(configured.get("link_button")), 0)
                    self.assertFalse(configured.code)
                    self.assertTrue(any("Autenticado sem arquivos" in item.value for item in configured.success))
                    start.assert_not_called()

    def test_legacy_section_still_authenticates_directly(self):
        app_test = AppTest.from_function(render_cloud_auth_fixture)
        app_test.secrets["google_oauth"] = {**CLIENT, "refresh_token": "refresh-synthetic"}
        app_test.run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertFalse(app_test.code)
        self.assertTrue(any("Autenticado sem arquivos" in item.value for item in app_test.success))

    def test_invalid_refresh_token_type_gives_configuration_message(self):
        app_test = self.make_app({**CLIENT, "refresh_token": 123}).run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("refresh_token deve ser texto" in item.value for item in app_test.error))

    def test_new_section_empty_is_error_not_disk_fallback(self):
        app_test = self.make_app({}).run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("client_id" in item.value for item in app_test.error))

    def test_denied_callback_and_unknown_state_do_not_show_token(self):
        original = self.make_app({**CLIENT, "redirect_uri": REDIRECT}).run()
        pending = original.session_state["classroom_cloud_pending"]
        callback = self.make_app(CLIENT)
        callback.query_params = {"state": "wrong", "code": "x"}
        callback.run()
        self.assertTrue(callback.warning)
        self.assertFalse(callback.code)
        callback.query_params = {"state": pending.state, "error": "access_denied"}
        callback.run()
        original.run()
        self.assertEqual(len(original.exception), 0)
        self.assertTrue(any("cancelado" in item.value for item in original.error))
        self.assertNotIn("refresh-synthetic", [item.value for item in original.code])

    def test_config_rotation_discards_session_authorization(self):
        original = self.make_app({**CLIENT, "redirect_uri": REDIRECT}).run()
        pending = original.session_state["classroom_cloud_pending"]
        self.mailbox.deliver({"state": pending.state, "code": "x"})
        original.run()
        original.secrets["google_credentials"] = {**CLIENT, "client_id": "different", "redirect_uri": REDIRECT}
        original.run()
        self.assertEqual(len(original.exception), 0)
        self.assertNotIn("refresh-synthetic", [item.value for item in original.code])
        self.assertTrue(original.get("link_button"))


if __name__ == "__main__":
    unittest.main()
