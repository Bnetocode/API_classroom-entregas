from __future__ import annotations

import unittest
from dataclasses import replace

from streamlit.testing.v1 import AppTest

from analytics import build_dashboard_data, build_student_risk_summary
from tests.test_analytics import NOW, sample_snapshot


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


def render_full_app(snapshot, auth_mode: str = "local") -> None:
    from unittest.mock import patch

    import streamlit as st

    import app
    from analytics import build_dashboard_data
    from classroom_client import ClassroomAPIError
    from tests.test_analytics import NOW

    def fetch_snapshot(*args):
        if st.session_state.get("simulate_snapshot_error"):
            raise ClassroomAPIError("Falha de conexão simulada.")
        return snapshot

    def record_refresh(*args):
        st.session_state["refreshed_snapshot"] = args

    courses = [
        {
            "id": snapshot.course["id"],
            "name": snapshot.course["name"],
            "section": "Turma teste",
            "alternateLink": "https://classroom.google.com/",
        }
    ]
    with (
        patch.object(app, "_resolve_auth_mode", return_value=(auth_mode, "test")),
        patch.object(app, "_cached_courses", return_value=courses),
        patch.object(app, "_cached_snapshot", side_effect=fetch_snapshot) as cached_snapshot,
        patch.object(
            app,
            "build_dashboard_data",
            side_effect=lambda value: build_dashboard_data(value, now=NOW),
        ),
    ):
        cached_snapshot.clear.side_effect = record_refresh
        app.main()


def render_auth_connection_failure() -> None:
    from unittest.mock import patch

    import streamlit as st

    import app
    from classroom_client import ClassroomAPIError

    with (
        patch.object(app, "_secret_section", return_value={}),
        patch.object(app, "load_local_credentials", side_effect=ClassroomAPIError("Sem conexão.")),
        patch.object(app, "authorize_local_account") as authorize,
    ):
        app.main()
        st.session_state["oauth_started"] = authorize.called


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

    def test_cloud_app_renders_with_google_oauth_only(self) -> None:
        app_test = AppTest.from_function(
            render_full_app,
            args=(sample_snapshot(), "cloud"),
            default_timeout=10,
        ).run()

        self.assertEqual(len(app_test.exception), 0)
        self.assertEqual(app_test.title[0].value, "Acompanhamento do Google Classroom")

    def test_default_risk_filter_and_student_history(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),)).run()
        self.assertEqual(len(app_test.exception), 0)
        risk_table = next(table.value for table in app_test.dataframe if "nivel_risco" in table.value)
        self.assertEqual(risk_table["aluno"].tolist(), ["Bob"])
        self.assertEqual(risk_table["nivel_risco"].tolist(), ["Crítico — início"])

        app_test.multiselect[0].set_value(["Em dia"]).run()
        risk_table = next(table.value for table in app_test.dataframe if "nivel_risco" in table.value)
        self.assertEqual(risk_table["aluno"].tolist(), ["Alice"])
        history_selector = next(
            widget for widget in app_test.selectbox
            if widget.label == "Consultar histórico de um aluno"
        )
        history_selector.select("alice").run()
        history = next(table.value for table in app_test.dataframe if "link_atividade" in table.value)
        self.assertEqual(len(history), 3)
        self.assertEqual(set(history["situacao"]), {"Entregue"})
        self.assertEqual(len(app_test.exception), 0)

    def test_empty_search_result_is_not_reported_as_no_risk(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),)).run()
        app_test.text_input[0].input("[aluno inexistente]").run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("Nenhum aluno corresponde" in item.value for item in app_test.info))
        self.assertEqual(len(app_test.success), 0)

    def test_no_selected_risk_levels_explains_how_to_restore_list(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),)).run()
        app_test.multiselect[0].set_value([]).run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("Selecione ao menos um nível" in item.value for item in app_test.info))

    def test_all_students_up_to_date_explains_default_filter(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(
            snapshot,
            submissions=[
                dict(item, state="TURNED_IN", late=False)
                for item in snapshot.submissions
            ],
        )
        app_test = AppTest.from_function(render_full_app, args=(snapshot,)).run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("Nenhum aluno em atenção" in item.value for item in app_test.success))
        self.assertTrue(any("inclua “Em dia”" in item.value for item in app_test.info))
        app_test.multiselect[0].set_value(["Em dia"]).run()
        risk_table = next(table.value for table in app_test.dataframe if "nivel_risco" in table.value)
        self.assertEqual(set(risk_table["aluno"]), {"Alice", "Bob"})

    def test_empty_course_renders_with_and_without_students(self) -> None:
        for keep_students in (False, True):
            with self.subTest(keep_students=keep_students):
                snapshot = sample_snapshot()
                snapshot = replace(
                    snapshot,
                    students=snapshot.students if keep_students else [],
                    coursework=[],
                    submissions=[],
                )
                app_test = AppTest.from_function(render_full_app, args=(snapshot,)).run()
                self.assertEqual(len(app_test.exception), 0)
                self.assertTrue(any("não tem atividades publicadas" in item.value for item in app_test.info))
                if keep_students:
                    app_test.multiselect[0].set_value(["Sem atividades"]).run()
                    risk_table = next(
                        table.value for table in app_test.dataframe
                        if "nivel_risco" in table.value
                    )
                    self.assertEqual(len(risk_table), 2)
                else:
                    self.assertTrue(any("não tem alunos" in item.value for item in app_test.info))

    def test_without_deadline_toggle_updates_risk_and_explanation(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(
            snapshot,
            coursework=[snapshot.coursework[2]],
            submissions=[
                item for item in snapshot.submissions
                if item["courseWorkId"] == "extra"
            ],
        )
        app_test = AppTest.from_function(render_full_app, args=(snapshot,)).run()
        self.assertTrue(any("Nenhum aluno em atenção" in item.value for item in app_test.success))
        app_test.toggle[0].set_value(True).run()
        self.assertEqual(len(app_test.exception), 0)
        risk_table = next(table.value for table in app_test.dataframe if "nivel_risco" in table.value)
        self.assertEqual(risk_table["nivel_risco"].tolist(), ["Atenção"])
        self.assertTrue(any("Pendências sem prazo estão incluídas" in item.value for item in app_test.info))
        app_test.slider[0].set_value(1).run()
        risk_table = next(table.value for table in app_test.dataframe if "nivel_risco" in table.value)
        self.assertEqual(risk_table["nivel_risco"].tolist(), ["Alto"])

    def test_snapshot_failure_keeps_last_successful_data(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),)).run()
        initial_metrics = [metric.value for metric in app_test.metric]
        app_test.session_state["simulate_snapshot_error"] = True
        app_test.run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("último snapshot válido" in item.value for item in app_test.warning))
        self.assertEqual([metric.value for metric in app_test.metric], initial_metrics)

    def test_snapshot_failure_without_previous_data_is_handled(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),))
        app_test.session_state["simulate_snapshot_error"] = True
        app_test.run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertTrue(any("Falha de conexão simulada" in item.value for item in app_test.error))
        self.assertEqual(len(app_test.metric), 0)

    def test_refresh_invalidates_only_selected_course(self) -> None:
        app_test = AppTest.from_function(render_full_app, args=(sample_snapshot(),)).run()
        refresh = next(button for button in app_test.button if button.label == "Atualizar agora")
        refresh.click().run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertEqual(app_test.session_state["refreshed_snapshot"], ("course-1", "test", "local"))

    def test_connection_failure_during_auth_does_not_start_oauth(self) -> None:
        app_test = AppTest.from_function(render_auth_connection_failure).run()
        self.assertEqual(len(app_test.exception), 0)
        self.assertEqual(app_test.error[0].value, "Sem conexão.")
        self.assertFalse(app_test.session_state["oauth_started"])


if __name__ == "__main__":
    unittest.main()
