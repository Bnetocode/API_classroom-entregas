from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from analytics import (
    _build_module_summary,
    build_dashboard_data,
    build_student_risk_summary,
    infer_stage,
)
from classroom_client import ClassroomSnapshot


NOW = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)


def sample_snapshot() -> ClassroomSnapshot:
    return ClassroomSnapshot(
        course={"id": "course-1", "name": "Os 4D's do Negócio"},
        students=[
            {"userId": "alice", "fullName": "Alice"},
            {"userId": "bob", "fullName": "Bob"},
        ],
        coursework=[
            {
                "id": "aula0",
                "title": "Aula 0 — Ambientação",
                "state": "PUBLISHED",
                "dueDate": {"year": 2026, "month": 8, "day": 10},
                "dueTime": {"hours": 12},
            },
            {
                "id": "modulo2",
                "title": "Módulo 2 — Diferenciais",
                "state": "PUBLISHED",
                "dueDate": {"year": 2026, "month": 8, "day": 20},
                "dueTime": {"hours": 12},
            },
            {
                "id": "extra",
                "title": "Atividade extra sem prazo",
                "state": "PUBLISHED",
            },
        ],
        submissions=[
            {
                "id": "s1",
                "courseWorkId": "aula0",
                "userId": "alice",
                "state": "RETURNED",
                "late": False,
                "updateTime": "2026-08-10T11:00:00Z",
            },
            {
                "id": "s2",
                "courseWorkId": "aula0",
                "userId": "bob",
                "state": "CREATED",
                "late": True,
            },
            {
                "id": "s3",
                "courseWorkId": "modulo2",
                "userId": "alice",
                "state": "TURNED_IN",
                "late": False,
            },
            {
                "id": "s4",
                "courseWorkId": "modulo2",
                "userId": "bob",
                "state": "CREATED",
                "late": False,
            },
            {
                "id": "s5",
                "courseWorkId": "extra",
                "userId": "alice",
                "state": "TURNED_IN",
                "late": False,
            },
            {
                "id": "s6",
                "courseWorkId": "extra",
                "userId": "bob",
                "state": "CREATED",
                "late": False,
            },
        ],
        collected_at="2026-08-14T12:00:00Z",
    )


class DashboardAnalyticsTests(unittest.TestCase):
    def test_module_summary_derives_pending_without_input_column(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(snapshot, coursework=[
            dict(activity, title="Módulo 2") for activity in snapshot.coursework
        ])
        activities = build_dashboard_data(snapshot, now=NOW).activity_summary
        modules = _build_module_summary(activities.drop(columns="pendentes"))
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules.iloc[0]["atribuicoes"], 6)
        self.assertEqual(modules.iloc[0]["entregues"], 3)
        self.assertEqual(modules.iloc[0]["pendentes"], 3)
        self.assertEqual(modules.iloc[0]["taxa_entrega_geral"], 50)
        self.assertEqual(modules.iloc[0]["pendencias_vencidas"], 1)
        empty = _build_module_summary(activities.iloc[:0].drop(columns="pendentes"))
        self.assertTrue(empty.empty)
        self.assertIn("pendentes", empty.columns)

    def test_deadline_changes_do_not_change_rates_pending_counts_or_risk(self) -> None:
        snapshot = sample_snapshot()
        baseline = build_dashboard_data(snapshot, now=NOW)
        baseline_risks = build_student_risk_summary(baseline, snapshot.students)
        for deadline in (None, {"year": 2020, "month": 1, "day": 1},
                         {"year": 2030, "month": 1, "day": 1}):
            with self.subTest(deadline=deadline):
                changed = replace(snapshot, coursework=[
                    dict(activity, dueDate=deadline) for activity in snapshot.coursework
                ])
                data = build_dashboard_data(changed, now=NOW)
                for table in ("activity_summary", "module_summary"):
                    fields = ["atribuicoes", "entregues", "pendentes", "taxa_entrega_geral"]
                    if table == "module_summary":
                        fields.append("variacao_pp")
                    pd.testing.assert_frame_equal(
                        getattr(data, table)[fields], getattr(baseline, table)[fields]
                    )
                risks = build_student_risk_summary(data, changed.students)
                fields = ["aluno_id", "nivel_risco", "motivo", "pendentes", "taxa_entrega_geral"]
                pd.testing.assert_frame_equal(risks[fields], baseline_risks[fields])
                if deadline is None:
                    pending = data.submissions[data.submissions["entregue"].eq(False)]
                    self.assertTrue(pending["situacao"].eq("Sem prazo — revisão manual").all())

    def test_stage_without_assignments_breaks_rate_comparison(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(snapshot, coursework=[
            {"id": "aula0", "title": "Módulo 2"},
            {"id": "empty", "title": "Módulo 3"},
            {"id": "extra", "title": "Módulo 4"},
        ])
        modules = build_dashboard_data(snapshot, now=NOW).module_summary.set_index("etapa")
        self.assertTrue(pd.isna(modules.loc["Módulo 3", "taxa_entrega_geral"]))
        self.assertTrue(pd.isna(modules.loc["Módulo 4", "variacao_pp"]))
        self.assertEqual(modules.loc["Módulo 3", "pendentes"], 0)

    def test_non_deliveries_count_with_past_future_and_missing_deadlines(self) -> None:
        snapshot = sample_snapshot()
        data = build_dashboard_data(snapshot, now=NOW)
        risks = build_student_risk_summary(data, snapshot.students)

        bob = risks.loc[risks["aluno_id"] == "bob"].iloc[0]
        self.assertEqual(bob["nivel_risco"], "Crítico — início")
        self.assertEqual(int(bob["pendencias_vencidas"]), 1)
        self.assertEqual(int(bob["pendencias_sem_prazo"]), 1)
        self.assertEqual(int(bob["pendentes"]), 3)
        self.assertEqual(int(data.module_summary["pendentes"].sum()), 3)

        due = data.submissions[data.submissions["atividade_vencida"]]
        self.assertEqual(len(due), 2)
        self.assertAlmostEqual(float(due["entregue"].mean() * 100), 50.0)

    def test_current_delivered_states_are_counted(self) -> None:
        data = build_dashboard_data(sample_snapshot(), now=NOW)
        states = data.submissions.set_index("entrega_id")["entregue"].to_dict()
        self.assertTrue(states["s1"])
        self.assertTrue(states["s3"])
        self.assertTrue(states["s5"])

    def test_pending_late_is_not_counted_as_late_delivery(self) -> None:
        data = build_dashboard_data(sample_snapshot(), now=NOW)
        aula0 = data.activity_summary.set_index("atividade_id").loc["aula0"]
        self.assertEqual(int(aula0["entregas_atrasadas"]), 0)
        self.assertEqual(int(aula0["pendencias_vencidas"]), 1)

    def test_former_student_remains_in_aggregate_cohort(self) -> None:
        snapshot = sample_snapshot()
        former_submission = {
            "id": "former-1",
            "courseWorkId": "aula0",
            "userId": "former-user",
            "state": "CREATED",
            "late": True,
        }
        snapshot = replace(
            snapshot,
            submissions=[*snapshot.submissions, former_submission],
        )

        data = build_dashboard_data(snapshot, now=NOW)
        former = data.submissions.loc[
            data.submissions["aluno_id"] == "former-user"
        ].iloc[0]
        self.assertFalse(bool(former["ativo_no_roster"]))
        self.assertEqual(former["aluno"], "Participante fora do roster")

    def test_due_time_is_converted_to_recife(self) -> None:
        data = build_dashboard_data(sample_snapshot(), now=NOW)
        due = data.activities.set_index("atividade_id").loc["aula0", "prazo"]
        self.assertEqual(pd.Timestamp(due).hour, 9)

    def test_empty_due_time_means_midnight_utc(self) -> None:
        snapshot = sample_snapshot()
        midnight_activity = {
            **snapshot.coursework[0],
            "dueDate": {"year": 2026, "month": 8, "day": 14},
            "dueTime": {},
        }
        snapshot = replace(snapshot, coursework=[midnight_activity])

        data = build_dashboard_data(snapshot, now=NOW)
        activity = data.activities.iloc[0]
        self.assertEqual(activity["prazo"], pd.Timestamp("2026-08-14T00:00:00Z"))
        self.assertFalse(bool(activity["prazo_inferido"]))
        self.assertTrue(bool(activity["atividade_vencida"]))
        risks = build_student_risk_summary(data, snapshot.students).set_index("aluno_id")
        self.assertEqual(risks.loc["bob", "nivel_risco"], "Crítico — início")

    def test_missing_due_time_retains_explicit_conservative_fallback(self) -> None:
        snapshot = sample_snapshot()
        activity = dict(snapshot.coursework[0])
        activity.pop("dueTime")
        data = build_dashboard_data(replace(snapshot, coursework=[activity]), now=NOW)

        row = data.activities.iloc[0]
        self.assertEqual(row["prazo"], pd.Timestamp("2026-08-10T23:59:59Z"))
        self.assertTrue(bool(row["prazo_inferido"]))

    def test_no_deadline_alerts_keep_submission_ids_from_different_activities(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(
            snapshot,
            coursework=[
                {**snapshot.coursework[0], "title": "Módulo 2 — Exercício"},
                snapshot.coursework[2],
            ],
            submissions=[
                {**snapshot.submissions[1], "id": "same-id"},
                {**snapshot.submissions[5], "id": "same-id"},
            ],
        )
        data = build_dashboard_data(snapshot, now=NOW)

        risks = build_student_risk_summary(data, snapshot.students).set_index("aluno_id")
        self.assertEqual(risks.loc["bob", "nivel_risco"], "Alto")
        self.assertEqual(risks.loc["bob", "pendentes"], 2)
        self.assertEqual(risks.loc["bob", "motivo"], "2 pendências acumuladas")

    def test_risk_levels_follow_non_deliveries_and_configured_threshold(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(
            snapshot,
            students=[*snapshot.students, {"userId": "carol", "fullName": "Carol"}],
            coursework=[
                {**snapshot.coursework[0], "title": "Módulo 2 — Exercício 1"},
                {
                    **snapshot.coursework[0],
                    "id": "second",
                    "title": "Módulo 3 — Exercício 2",
                },
            ],
            submissions=[
                snapshot.submissions[0],
                snapshot.submissions[1],
                {
                    **snapshot.submissions[1],
                    "id": "second-bob",
                    "courseWorkId": "second",
                },
            ],
        )
        data = build_dashboard_data(snapshot, now=NOW)
        risks = build_student_risk_summary(data, snapshot.students).set_index("aluno_id")
        higher_threshold = build_student_risk_summary(
            data, snapshot.students, high_risk_threshold=3
        ).set_index("aluno_id")

        self.assertEqual(risks.loc["alice", "nivel_risco"], "Em dia")
        self.assertEqual(risks.loc["bob", "nivel_risco"], "Alto")
        self.assertEqual(risks.loc["carol", "nivel_risco"], "Sem atividades")
        self.assertEqual(higher_threshold.loc["bob", "nivel_risco"], "Atenção")
        self.assertEqual(
            higher_threshold.loc["bob", "motivo"], "2 pendências que requerem contato"
        )

    def test_reclaimed_submission_is_pending_and_returned_is_delivered(self) -> None:
        snapshot = sample_snapshot()
        snapshot = replace(
            snapshot,
            submissions=[
                {**snapshot.submissions[0], "late": True},
                {**snapshot.submissions[1], "state": "RECLAIMED_BY_STUDENT"},
            ],
        )
        data = build_dashboard_data(snapshot, now=NOW)
        submissions = data.submissions.set_index("aluno_id")
        self.assertEqual(submissions.loc["alice", "situacao"], "Entregue com atraso")
        self.assertEqual(submissions.loc["bob", "situacao"], "Atrasada não entregue")
        self.assertEqual(int(data.activity_summary["entregas_atrasadas"].sum()), 1)

    def test_module_rates_weight_assignments_and_compare_stages_without_deadline_filter(self) -> None:
        snapshot = sample_snapshot()
        states = [
            ("m2-a", "alice", "TURNED_IN"),
            ("m2-b", "alice", "NEW"),
            ("m2-b", "bob", "NEW"),
            ("m10", "bob", "NEW"),
            ("m11", "bob", "NEW"),
            ("m12", "bob", "NEW"),
            ("extra", "bob", "NEW"),
        ]
        snapshot = replace(
            snapshot,
            coursework=[
                {**snapshot.coursework[0], "id": "m10", "title": "Módulo 10"},
                {**snapshot.coursework[0], "id": "m2-a", "title": "Módulo 2 — A"},
                {**snapshot.coursework[0], "id": "m2-b", "title": "Módulo 2 — B"},
                {**snapshot.coursework[1], "id": "m11", "title": "Módulo 11"},
                {**snapshot.coursework[0], "id": "m12", "title": "Módulo 12"},
                {**snapshot.coursework[0], "id": "extra", "title": "Extra"},
            ],
            submissions=[
                {"id": str(index), "courseWorkId": activity, "userId": user, "state": state}
                for index, (activity, user, state) in enumerate(states)
            ],
        )
        modules = build_dashboard_data(snapshot, now=NOW).module_summary.set_index("etapa")

        self.assertEqual(
            list(modules.index),
            ["Módulo 2", "Módulo 10", "Módulo 11", "Módulo 12", "Extra"],
        )
        self.assertAlmostEqual(float(modules.loc["Módulo 2", "taxa_entrega_geral"]), 100 / 3)
        self.assertAlmostEqual(float(modules.loc["Módulo 10", "variacao_pp"]), -100 / 3)
        self.assertEqual(modules.loc["Módulo 11", "taxa_entrega_geral"], 0)
        self.assertEqual(modules.loc["Módulo 11", "variacao_pp"], 0)
        self.assertEqual(modules.loc["Módulo 12", "variacao_pp"], 0)
        self.assertTrue(pd.isna(modules.loc["Extra", "variacao_pp"]))

    def test_activities_without_submissions_keep_zero_counts_and_missing_rates(self) -> None:
        snapshot = replace(sample_snapshot(), submissions=[])
        data = build_dashboard_data(snapshot, now=NOW)
        self.assertTrue(data.activity_summary["atribuicoes"].eq(0).all())
        self.assertTrue(data.activity_summary["taxa_entrega_geral"].isna().all())
        self.assertTrue(data.module_summary["atribuicoes"].eq(0).all())
        self.assertTrue(data.module_summary["taxa_entrega_geral"].isna().all())
        risks = build_student_risk_summary(
            data, snapshot.students
        )
        self.assertTrue(risks["nivel_risco"].eq("Sem atividades").all())

    def test_invalid_due_time_becomes_manual_review(self) -> None:
        snapshot = replace(
            sample_snapshot(),
            coursework=[
                {
                    "id": "invalid-due",
                    "title": "Módulo-3 — Atividade de teste",
                    "dueDate": {"year": 2026, "month": 8, "day": 10},
                    "dueTime": {"hours": "inválida"},
                }
            ],
            submissions=[],
        )

        data = build_dashboard_data(snapshot, now=NOW)
        activity = data.activities.iloc[0]

        self.assertTrue(bool(activity["sem_prazo"]))
        self.assertFalse(bool(activity["prazo_inferido"]))
        self.assertTrue(pd.isna(activity["prazo"]))
        self.assertEqual(activity["etapa"], "Módulo 3")

    def test_stage_detection_accepts_common_separators(self) -> None:
        self.assertEqual(infer_stage("Aula_0 — Boas-vindas"), "Aula 0")
        self.assertEqual(infer_stage("Módulo-12 — Prática"), "Módulo 12")
        self.assertEqual(infer_stage("Módulo_1_Entrega"), "Módulo 1")
        self.assertEqual(infer_stage("Aula_0_Apresentação"), "Aula 0")

    def test_empty_snapshot_has_stable_schema(self) -> None:
        snapshot = ClassroomSnapshot(
            course={"id": "empty", "name": "Vazia"},
            students=[],
            coursework=[],
            submissions=[],
            collected_at="2026-08-14T12:00:00Z",
        )
        data = build_dashboard_data(snapshot, now=NOW)
        self.assertTrue(data.activities.empty)
        self.assertTrue(data.submissions.empty)
        self.assertIn("situacao", data.submissions.columns)
        self.assertIn("taxa_entrega_geral", data.activity_summary.columns)
        self.assertIn("pendentes", data.module_summary.columns)


if __name__ == "__main__":
    unittest.main()
