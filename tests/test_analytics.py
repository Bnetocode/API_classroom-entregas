from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from analytics import build_dashboard_data, build_student_risk_summary
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
    def test_future_and_no_deadline_do_not_create_false_alerts(self) -> None:
        snapshot = sample_snapshot()
        data = build_dashboard_data(snapshot, now=NOW)
        risks = build_student_risk_summary(data, snapshot.students)

        bob = risks.loc[risks["aluno_id"] == "bob"].iloc[0]
        self.assertEqual(bob["nivel_risco"], "Crítico — início")
        self.assertEqual(int(bob["pendencias_vencidas"]), 1)
        self.assertEqual(int(bob["pendencias_sem_prazo"]), 1)

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
        self.assertIn("taxa_entrega_vencida", data.activity_summary.columns)


if __name__ == "__main__":
    unittest.main()
