from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest

from analytics import build_dashboard_data, build_student_risk_summary
from test_analytics import NOW, sample_snapshot


def render_fixture(snapshot, data, risks) -> None:
    import streamlit as st

    import app

    overview, risk, activities, diagnostics = st.tabs(
        ["Visão", "Risco", "Atividades", "Diagnóstico"]
    )
    with overview:
        app._render_overview(snapshot, data, risks)
    with risk:
        app._render_risk_tab(data, risks)
    with activities:
        app._render_activities_tab(data, 10)
    with diagnostics:
        app._render_diagnostics_tab(snapshot, data, "local")


def render_full_app(snapshot) -> None:
    from unittest.mock import patch

    import app

    courses = [
        {
            "id": snapshot.course["id"],
            "name": snapshot.course["name"],
            "section": "Turma teste",
            "alternateLink": "https://classroom.google.com/",
        }
    ]
    with (
        patch.object(app, "_resolve_auth_mode", return_value=("local", "test")),
        patch.object(app, "_cached_courses", return_value=courses),
        patch.object(app, "_cached_snapshot", return_value=snapshot),
    ):
        app.main()


class AppRenderTests(unittest.TestCase):
    def test_dashboard_components_render_without_exception(self) -> None:
        snapshot = sample_snapshot()
        data = build_dashboard_data(snapshot, now=NOW)
        risks = build_student_risk_summary(data, snapshot.students)
        app_test = AppTest.from_function(
            render_fixture,
            args=(snapshot, data, risks),
            default_timeout=10,
        ).run()

        self.assertEqual(len(app_test.exception), 0)
        self.assertGreaterEqual(len(app_test.metric), 5)

    def test_complete_app_renders_without_real_credentials(self) -> None:
        app_test = AppTest.from_function(
            render_full_app,
            args=(sample_snapshot(),),
            default_timeout=10,
        ).run()

        self.assertEqual(len(app_test.exception), 0)
        self.assertEqual(app_test.title[0].value, "Acompanhamento do Google Classroom")


if __name__ == "__main__":
    unittest.main()
