"""Transformações e indicadores do painel de acompanhamento do Classroom."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from classroom_client import ClassroomSnapshot


DISPLAY_TIMEZONE = ZoneInfo("America/Recife")
DELIVERED_STATES = {
    "TURNED_IN",
    "RETURNED",
}
_AULA_0_PATTERN = re.compile(r"\baula[\s_-]*0\b")
_MODULE_PATTERN = re.compile(r"\bmodulo[\s_-]*([1-9][0-9]*)\b")
_MODULE_STAGE_PATTERN = re.compile(r"modulo\s*(\d+)")

ACTIVITY_COLUMNS = [
    "atividade_id",
    "atividade",
    "etapa",
    "prazo",
    "prazo_inferido",
    "atividade_vencida",
    "sem_prazo",
    "publicada_em",
    "atualizada_em",
    "tipo",
    "link",
]
SUBMISSION_COLUMNS = [
    "entrega_id",
    "atividade_id",
    "atividade",
    "etapa",
    "aluno_id",
    "aluno",
    "estado_api",
    "situacao",
    "entregue",
    "atrasada",
    "entrega_atrasada",
    "em_atraso",
    "ativo_no_roster",
    "atividade_vencida",
    "sem_prazo",
    "prazo",
    "ultima_atualizacao",
    "link_atividade",
]


@dataclass(frozen=True)
class DashboardData:
    activities: pd.DataFrame
    submissions: pd.DataFrame
    activity_summary: pd.DataFrame
    module_summary: pd.DataFrame
    collected_at: pd.Timestamp


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize(
        "NFKD", "" if value is None else str(value)
    )
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def _text_or_default(value: Any, default: str) -> str:
    text = "" if value is None else str(value).strip()
    return text or default


def infer_stage(title: str | None) -> str:
    """Infere Aula 0/Módulo pelo título sem solicitar um escopo extra de tópicos."""

    clean_title = _text_or_default(title, "Atividade sem título")
    normalized = _normalize_text(clean_title).replace("_", " ")
    if _AULA_0_PATTERN.search(normalized):
        return "Aula 0"
    match = _MODULE_PATTERN.search(normalized)
    if match:
        return f"Módulo {int(match.group(1))}"
    return clean_title


def is_critical_stage(stage: str) -> bool:
    normalized = _normalize_text(stage)
    return normalized == "aula 0" or normalized == "modulo 1"


def _parse_timestamp(value: Any) -> pd.Timestamp:
    if value is None:
        return pd.NaT
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except (TypeError, ValueError):
        return pd.NaT
    if not isinstance(parsed, pd.Timestamp) or pd.isna(parsed):
        return pd.NaT
    return parsed.tz_convert(DISPLAY_TIMEZONE)


def _due_timestamp(coursework: Mapping[str, Any]) -> tuple[pd.Timestamp, bool]:
    date_value = coursework.get("dueDate")
    if not isinstance(date_value, Mapping):
        return pd.NaT, False

    try:
        year = int(date_value["year"])
        month = int(date_value["month"])
        day = int(date_value["day"])
        due_time = coursework.get("dueTime")
        if due_time is None:
            # Sem horário explícito, usamos o fim do dia UTC de forma conservadora.
            # Um objeto presente, mesmo {}, representa 00:00:00 UTC: o JSON
            # protobuf pode omitir os campos numéricos com valor zero.
            value = time(23, 59, 59)
            inferred = True
        elif not isinstance(due_time, Mapping):
            return pd.NaT, False
        else:
            value = time(
                int(due_time.get("hours", 0)),
                int(due_time.get("minutes", 0)),
                int(due_time.get("seconds", 0)),
                int(due_time.get("nanos", 0)) // 1_000,
            )
            inferred = False
        due_utc = datetime.combine(
            datetime(year, month, day).date(), value, tzinfo=timezone.utc
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return pd.NaT, False
    return pd.Timestamp(due_utc).tz_convert(DISPLAY_TIMEZONE), inferred


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def build_dashboard_data(
    snapshot: ClassroomSnapshot, *, now: datetime | pd.Timestamp | None = None
) -> DashboardData:
    """Cria tabelas estáveis, inclusive quando a turma ainda não tem dados."""

    reference_now = pd.Timestamp(now or datetime.now(timezone.utc))
    if reference_now.tzinfo is None:
        reference_now = reference_now.tz_localize(timezone.utc)
    reference_now = reference_now.tz_convert(DISPLAY_TIMEZONE)

    activity_records: list[dict[str, Any]] = []
    for item in snapshot.coursework:
        if not isinstance(item, Mapping):
            continue
        due_at, inferred = _due_timestamp(item)
        title = _text_or_default(item.get("title"), "Atividade sem título")
        activity_records.append(
            {
                "atividade_id": str(item.get("id", "")),
                "atividade": title,
                "etapa": infer_stage(title),
                "prazo": due_at,
                "prazo_inferido": inferred,
                "atividade_vencida": bool(
                    not pd.isna(due_at) and due_at <= reference_now
                ),
                "sem_prazo": bool(pd.isna(due_at)),
                "publicada_em": _parse_timestamp(item.get("creationTime")),
                "atualizada_em": _parse_timestamp(item.get("updateTime")),
                "tipo": item.get("workType", ""),
                "link": item.get("alternateLink", ""),
            }
        )

    activities = (
        pd.DataFrame.from_records(activity_records, columns=ACTIVITY_COLUMNS)
        if activity_records
        else _empty_frame(ACTIVITY_COLUMNS)
    )
    activity_map = {
        record["atividade_id"]: record for record in activity_records
    }
    student_map: dict[str, str] = {}
    for student in snapshot.students:
        if not isinstance(student, Mapping):
            continue
        user_id = _text_or_default(student.get("userId"), "")
        if user_id:
            student_map[user_id] = _text_or_default(
                student.get("fullName"), "Nome não informado"
            )

    submission_records: list[dict[str, Any]] = []
    for item in snapshot.submissions:
        if not isinstance(item, Mapping):
            continue
        activity_id = _text_or_default(item.get("courseWorkId"), "")
        activity = activity_map.get(activity_id)
        if not activity:
            continue

        user_id = _text_or_default(item.get("userId"), "")
        active_in_roster = user_id in student_map

        state = _text_or_default(item.get("state"), "STATE_UNSPECIFIED")
        delivered = state in DELIVERED_STATES
        late = item.get("late") is True
        overdue = bool(activity["atividade_vencida"] and not delivered)

        if delivered and late:
            situation = "Entregue com atraso"
        elif delivered:
            situation = "Entregue"
        elif overdue:
            situation = "Atrasada não entregue"
        elif activity["sem_prazo"]:
            situation = "Sem prazo — revisão manual"
        else:
            situation = "Pendente dentro do prazo"

        submission_records.append(
            {
                "entrega_id": str(item.get("id", "")),
                "atividade_id": activity_id,
                "atividade": activity["atividade"],
                "etapa": activity["etapa"],
                "aluno_id": user_id,
                "aluno": student_map.get(user_id, "Participante fora do roster"),
                "estado_api": state,
                "situacao": situation,
                "entregue": delivered,
                "atrasada": late,
                "entrega_atrasada": bool(delivered and late),
                "em_atraso": overdue,
                "ativo_no_roster": active_in_roster,
                "atividade_vencida": activity["atividade_vencida"],
                "sem_prazo": activity["sem_prazo"],
                "prazo": activity["prazo"],
                # updateTime é o último movimento, não necessariamente a hora
                # exata da entrega (uma devolução do professor também o altera).
                "ultima_atualizacao": _parse_timestamp(item.get("updateTime")),
                "link_atividade": activity["link"],
            }
        )

    submissions = (
        pd.DataFrame.from_records(submission_records, columns=SUBMISSION_COLUMNS)
        if submission_records
        else _empty_frame(SUBMISSION_COLUMNS)
    )

    activity_summary = _build_activity_summary(activities, submissions)
    module_summary = _build_module_summary(activity_summary)
    collected_at = _parse_timestamp(snapshot.collected_at)
    return DashboardData(
        activities=activities,
        submissions=submissions,
        activity_summary=activity_summary,
        module_summary=module_summary,
        collected_at=collected_at,
    )


def _build_activity_summary(
    activities: pd.DataFrame, submissions: pd.DataFrame
) -> pd.DataFrame:
    base_columns = [
        "atividade_id",
        "atividade",
        "etapa",
        "prazo",
        "atividade_vencida",
        "sem_prazo",
        "link",
    ]
    result_columns = base_columns + [
        "atribuicoes",
        "entregues",
        "pendentes",
        "pendencias_vencidas",
        "entregas_atrasadas",
        "taxa_entrega_geral",
        "taxa_entrega_vencida",
    ]
    if activities.empty:
        return _empty_frame(result_columns)

    summary = activities[base_columns].copy()
    if submissions.empty:
        for column in (
            "atribuicoes",
            "entregues",
            "pendentes",
            "pendencias_vencidas",
            "entregas_atrasadas",
        ):
            summary[column] = 0
    else:
        grouped = (
            submissions.groupby("atividade_id", as_index=False)
            .agg(
                atribuicoes=("entrega_id", "size"),
                entregues=("entregue", "sum"),
                pendencias_vencidas=("em_atraso", "sum"),
                entregas_atrasadas=("entrega_atrasada", "sum"),
            )
        )
        grouped["pendentes"] = grouped["atribuicoes"] - grouped["entregues"]
        summary = summary.merge(grouped, on="atividade_id", how="left")
        count_columns = [
            "atribuicoes",
            "entregues",
            "pendentes",
            "pendencias_vencidas",
            "entregas_atrasadas",
        ]
        summary[count_columns] = summary[count_columns].fillna(0).astype(int)

    summary["taxa_entrega_geral"] = (
        summary["entregues"].div(
            summary["atribuicoes"].where(summary["atribuicoes"].ne(0))
        ) * 100
    )
    summary["taxa_entrega_vencida"] = summary["taxa_entrega_geral"].where(
        summary["atividade_vencida"]
    )
    return summary[result_columns]


def _stage_sort_key(stage: str) -> tuple[int, int, str]:
    normalized = _normalize_text(stage)
    if normalized == "aula 0":
        return (0, 0, normalized)
    match = _MODULE_STAGE_PATTERN.fullmatch(normalized)
    if match:
        return (1, int(match.group(1)), normalized)
    return (2, 0, normalized)


def is_recognized_stage(stage: str) -> bool:
    normalized = _normalize_text(stage)
    return normalized == "aula 0" or bool(
        _MODULE_STAGE_PATTERN.fullmatch(normalized)
    )


def _build_module_summary(activity_summary: pd.DataFrame) -> pd.DataFrame:
    """Reutiliza os totais por atividade sem percorrer entregas a cada módulo."""

    columns = [
        "etapa",
        "atividades",
        "atividades_vencidas",
        "atribuicoes",
        "entregues",
        "pendentes",
        "atribuicoes_vencidas",
        "entregues_vencidas",
        "pendencias_vencidas",
        "taxa_entrega_geral",
        "taxa_entrega_vencida",
        "variacao_pp",
    ]
    if activity_summary.empty:
        return _empty_frame(columns)

    due = activity_summary["atividade_vencida"]
    totals = activity_summary.assign(
        atribuicoes_vencidas=activity_summary["atribuicoes"].where(due, 0),
        entregues_vencidas=activity_summary["entregues"].where(due, 0),
    )
    result = totals.groupby("etapa", as_index=False, sort=False).agg(
        atividades=("atividade_id", "size"),
        atividades_vencidas=("atividade_vencida", "sum"),
        atribuicoes=("atribuicoes", "sum"),
        entregues=("entregues", "sum"),
        atribuicoes_vencidas=("atribuicoes_vencidas", "sum"),
        entregues_vencidas=("entregues_vencidas", "sum"),
        pendencias_vencidas=("pendencias_vencidas", "sum"),
    )
    result["pendentes"] = result["atribuicoes"] - result["entregues"]
    stage_order = sorted(
        result.index, key=lambda index: _stage_sort_key(result.at[index, "etapa"])
    )
    result = result.loc[stage_order].reset_index(drop=True)
    result["taxa_entrega_geral"] = result["entregues"].div(
        result["atribuicoes"].where(result["atribuicoes"].ne(0))
    ) * 100
    result["taxa_entrega_vencida"] = result["entregues_vencidas"].div(
        result["atribuicoes_vencidas"].where(result["atribuicoes_vencidas"].ne(0))
    ) * 100
    result["variacao_pp"] = float("nan")
    ordered_mask = result["etapa"].map(is_recognized_stage)
    result.loc[ordered_mask, "variacao_pp"] = result.loc[
        ordered_mask, "taxa_entrega_geral"
    ].diff()
    return result[columns]


def build_student_risk_summary(
    data: DashboardData,
    students: list[dict[str, Any]],
    *,
    high_risk_threshold: int = 2,
) -> pd.DataFrame:
    """Conta toda não entrega nos alertas, independentemente de prazo."""

    columns = [
        "aluno_id",
        "aluno",
        "nivel_risco",
        "motivo",
        "atividades_atribuidas",
        "entregues",
        "pendentes",
        "taxa_entrega_geral",
        "taxa_entrega_vencida",
        "pendencias_vencidas",
        "pendencias_sem_prazo",
        "entregas_atrasadas",
        "ultimo_movimento",
    ]
    records: list[dict[str, Any]] = []
    submissions = data.submissions
    empty_rows = submissions.iloc[0:0]
    submissions_by_student = {
        str(user_id): rows
        for user_id, rows in submissions.groupby("aluno_id", sort=False)
    }

    for student in students:
        if not isinstance(student, Mapping):
            continue
        user_id = _text_or_default(student.get("userId"), "")
        name = _text_or_default(student.get("fullName"), "Nome não informado")
        rows = submissions_by_student.get(user_id, empty_rows)
        due_rows = rows[rows["atividade_vencida"]]
        overdue_rows = rows[rows["em_atraso"]]
        without_deadline = rows[(rows["sem_prazo"]) & (~rows["entregue"])]
        # Cada registro é uma atribuição aluno/atividade. Não deduplicar apenas
        # pelo id da submissão, que pode se repetir em atividades diferentes.
        alert_rows = rows[rows["entregue"].eq(False)]

        critical = bool(
            not alert_rows.empty
            and alert_rows["etapa"].map(is_critical_stage).any()
        )
        pending_for_alert = len(alert_rows)
        if critical:
            level = "Crítico — início"
            reason = "Não entrega na Aula 0/Módulo 1"
        elif pending_for_alert >= max(1, high_risk_threshold):
            level = "Alto"
            reason = f"{pending_for_alert} pendências acumuladas"
        elif pending_for_alert:
            level = "Atenção"
            reason = (
                "1 pendência que requer contato"
                if pending_for_alert == 1
                else f"{pending_for_alert} pendências que requerem contato"
            )
        elif rows.empty:
            level = "Sem atividades"
            reason = "Nenhuma atividade atribuída"
        else:
            level = "Em dia"
            reason = "Todas as atividades atribuídas foram entregues"

        last_movement = rows["ultima_atualizacao"].dropna()
        records.append(
            {
                "aluno_id": user_id,
                "aluno": name,
                "nivel_risco": level,
                "motivo": reason,
                "atividades_atribuidas": len(rows),
                "entregues": int(rows["entregue"].sum()) if not rows.empty else 0,
                "pendentes": pending_for_alert,
                "taxa_entrega_geral": (
                    float(rows["entregue"].mean() * 100)
                    if not rows.empty
                    else pd.NA
                ),
                "taxa_entrega_vencida": (
                    float(due_rows["entregue"].mean() * 100)
                    if not due_rows.empty
                    else pd.NA
                ),
                "pendencias_vencidas": len(overdue_rows),
                "pendencias_sem_prazo": len(without_deadline),
                "entregas_atrasadas": int(rows["entrega_atrasada"].sum())
                if not rows.empty
                else 0,
                "ultimo_movimento": (
                    last_movement.max() if not last_movement.empty else pd.NaT
                ),
            }
        )

    if not records:
        return _empty_frame(columns)

    rank = {
        "Crítico — início": 0,
        "Alto": 1,
        "Atenção": 2,
        "Em dia": 3,
        "Sem atividades": 4,
    }
    result = pd.DataFrame.from_records(records, columns=columns)
    result["_rank"] = result["nivel_risco"].map(rank).fillna(99)
    result = result.sort_values(
        ["_rank", "pendentes", "aluno"],
        ascending=[True, False, True],
    ).drop(columns="_rank")
    return result.reset_index(drop=True)
