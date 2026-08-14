"""Painel Streamlit de acompanhamento do curso Os 4D's do Negócio."""

from __future__ import annotations

import hmac
from typing import Any

import pandas as pd
import streamlit as st
from google_auth_oauthlib.flow import WSGITimeoutError
from googleapiclient.errors import HttpError

from analytics import (
    DashboardData,
    build_dashboard_data,
    build_student_risk_summary,
    is_recognized_stage,
)
from classroom_client import (
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_TOKEN_PATH,
    ClassroomAPIError,
    ClassroomAuthenticationRequired,
    ClassroomConfigurationError,
    ClassroomSnapshot,
    authorize_local_account,
    build_classroom_service,
    classroom_api_error,
    cloud_auth_cache_key,
    collect_course_snapshot,
    credentials_from_cloud_secrets,
    list_teacher_courses,
    load_local_credentials,
    local_auth_cache_key,
)


DAILY_CACHE_SECONDS = 24 * 60 * 60
COURSE_CACHE_SECONDS = 10 * 60
RISK_LEVELS = {"Crítico — início", "Alto", "Atenção"}
MIN_DASHBOARD_PASSWORD_LENGTH = 12


def _secret_section(name: str) -> dict[str, Any]:
    try:
        if name in st.secrets:
            return dict(st.secrets[name])
    except (FileNotFoundError, KeyError, TypeError):
        pass
    return {}


def _credentials_for_mode(auth_mode: str):
    if auth_mode == "cloud":
        return credentials_from_cloud_secrets(_secret_section("google_oauth"))
    return load_local_credentials()


@st.cache_data(ttl=COURSE_CACHE_SECONDS, show_spinner=False)
def _cached_courses(auth_cache_key: str, auth_mode: str) -> list[dict[str, Any]]:
    del auth_cache_key  # participa da chave do cache; o valor não é segredo.
    service = build_classroom_service(_credentials_for_mode(auth_mode))
    try:
        return list_teacher_courses(service)
    except HttpError as exc:
        raise classroom_api_error(exc) from exc


@st.cache_data(ttl=DAILY_CACHE_SECONDS, show_spinner=False)
def _cached_snapshot(
    course_id: str, auth_cache_key: str, auth_mode: str
) -> ClassroomSnapshot:
    del auth_cache_key  # invalida o cache quando a autorização for trocada.
    service = build_classroom_service(_credentials_for_mode(auth_mode))
    return collect_course_snapshot(service, course_id)


def _clear_data_caches() -> None:
    _cached_courses.clear()
    _cached_snapshot.clear()


def _dashboard_password() -> str:
    app_secrets = _secret_section("app")
    expected = str(app_secrets.get("password", "")).strip()
    if expected == "DEFINA_UMA_SENHA_FORTE":
        return ""
    return expected


def _require_dashboard_password() -> bool:
    expected = _dashboard_password()
    if not expected:
        return True
    if len(expected) < MIN_DASHBOARD_PASSWORD_LENGTH:
        st.error(
            f"A senha em `[app].password` precisa ter pelo menos "
            f"{MIN_DASHBOARD_PASSWORD_LENGTH} caracteres."
        )
        return False
    if st.session_state.get("dashboard_authorized") is True:
        return True

    st.title("Painel restrito")
    st.caption("Informe a senha definida em `[app].password` no Streamlit Secrets.")
    supplied = st.text_input("Senha de acesso", type="password")
    if st.button("Entrar", type="primary"):
        if hmac.compare_digest(supplied, expected):
            st.session_state["dashboard_authorized"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    return False


def _render_local_authorization(error_message: str | None = None) -> None:
    st.title("Conectar ao Google Classroom")
    st.write(
        "No primeiro acesso, o Google abrirá uma tela para você escolher a conta "
        "que é professora das turmas. Não digite nem fixe o e-mail neste código."
    )
    st.info(
        "A URL de autorização também aparece no terminal. Depois do consentimento, "
        "o token local é renovado automaticamente nas próximas execuções."
    )
    if error_message:
        st.warning(error_message)

    if not DEFAULT_CREDENTIALS_PATH.is_file():
        st.error(
            "Não encontrei credentials.json ao lado de app.py. Adicione uma "
            "credencial OAuth do tipo Desktop app."
        )
        return

    auto_start = not st.session_state.get("oauth_auto_started", False)
    retry = st.button("Abrir autorização Google", type="primary")
    if auto_start or retry:
        st.session_state["oauth_auto_started"] = True
        try:
            with st.spinner("Aguardando a autorização no navegador..."):
                authorize_local_account()
        except (ClassroomConfigurationError, ClassroomAuthenticationRequired) as exc:
            st.error(str(exc))
        except WSGITimeoutError:
            st.error("O tempo de autorização terminou. Clique no botão para tentar de novo.")
        except Exception as exc:  # a biblioteca usa uma exceção própria para timeout
            st.error(
                "A autorização não foi concluída. Verifique o terminal e tente novamente."
            )
            st.caption(type(exc).__name__)
        else:
            st.session_state.pop("oauth_auto_started", None)
            _clear_data_caches()
            st.rerun()


def _resolve_auth_mode() -> tuple[str, str] | None:
    cloud_secret = _secret_section("google_oauth")
    if cloud_secret:
        try:
            cache_key = cloud_auth_cache_key(cloud_secret)
            # A validação real do refresh token ocorre na primeira consulta.
            missing = [
                key
                for key in ("client_id", "client_secret", "refresh_token")
                if not str(cloud_secret.get(key, "")).strip()
                or str(cloud_secret.get(key, "")).strip().startswith("SEU_")
            ]
            if missing:
                raise ClassroomConfigurationError(
                    "Secrets incompletos: " + ", ".join(missing)
                )
            return "cloud", cache_key
        except ClassroomConfigurationError as exc:
            st.error(str(exc))
            st.stop()

    try:
        load_local_credentials()
    except ClassroomAuthenticationRequired as exc:
        _render_local_authorization(str(exc))
        return None
    return "local", local_auth_cache_key()


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.1f}%".replace(".", ",")


def _format_datetime(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).strftime("%d/%m/%Y %H:%M")


def _course_label(course: dict[str, Any]) -> str:
    section = str(course.get("section", "")).strip()
    return f"{course['name']} — {section}" if section else str(course["name"])


def _overview_metrics(
    snapshot: ClassroomSnapshot, data: DashboardData, risks: pd.DataFrame
) -> None:
    due = data.submissions[data.submissions["atividade_vencida"]]
    delivery_rate = float(due["entregue"].mean() * 100) if not due.empty else pd.NA
    risk_count = int(risks["nivel_risco"].isin(RISK_LEVELS).sum()) if not risks.empty else 0

    columns = st.columns(5)
    columns[0].metric("Alunos", len(snapshot.students))
    columns[1].metric("Atividades publicadas", len(snapshot.coursework))
    columns[2].metric("Entrega nas vencidas", _format_percent(delivery_rate))
    columns[3].metric("Pendências vencidas", int(data.submissions["em_atraso"].sum()))
    columns[4].metric("Alunos em atenção", risk_count)


def _render_overview(
    snapshot: ClassroomSnapshot, data: DashboardData, risks: pd.DataFrame
) -> None:
    _overview_metrics(snapshot, data, risks)
    st.caption(
        f"Dados consultados diretamente no Classroom em {_format_datetime(data.collected_at)}. "
        "O cache vence em 24 horas; use “Atualizar agora” para antecipar."
    )

    if data.submissions.empty:
        if data.activities.empty:
            st.info("Esta turma ainda não tem atividades publicadas.")
        else:
            st.info("Não há submissões retornadas para as atividades publicadas.")
        return

    chart_col, status_col = st.columns([3, 2])
    with chart_col:
        st.subheader("Taxa de entrega por etapa")
        chart = data.module_summary.dropna(subset=["taxa_entrega_vencida"])
        if chart.empty:
            st.info("Ainda não há atividade com prazo vencido para calcular a taxa.")
        else:
            st.bar_chart(
                chart.set_index("etapa")[["taxa_entrega_vencida"]],
                y_label="% das atribuições vencidas",
                color="#1f7a5a",
            )

    with status_col:
        st.subheader("Situação das entregas")
        status = (
            data.submissions["situacao"]
            .value_counts()
            .rename_axis("situação")
            .to_frame("quantidade")
        )
        st.bar_chart(status, horizontal=True, color="#dc7f32")

    no_deadline = int(data.activities["sem_prazo"].sum())
    future = int(
        ((~data.activities["sem_prazo"]) & (~data.activities["atividade_vencida"])).sum()
    )
    if no_deadline or future:
        st.info(
            f"{future} atividade(s) ainda dentro do prazo e {no_deadline} sem prazo não "
            "reduzem a taxa principal nem geram alerta automático."
        )

    outside_roster = int(
        data.submissions.loc[
            data.submissions["ativo_no_roster"].eq(False), "aluno_id"
        ].nunique()
    )
    if outside_roster:
        st.info(
            f"{outside_roster} participante(s) fora do roster atual permanecem nas "
            "taxas agregadas para não melhorar artificialmente o indicador após uma saída."
        )


def _render_risk_tab(
    data: DashboardData,
    risks: pd.DataFrame,
    high_risk_threshold: int = 2,
    include_without_deadline: bool = False,
) -> None:
    st.subheader("Lista de intervenção da tutoria")
    st.caption(
        "Alto = quantidade configurada de pendências; Crítico = qualquer pendência "
        f"elegível detectada na Aula 0/Módulo 1. Parâmetros ativos: alto a partir "
        f"de {high_risk_threshold} pendência(s); sem prazo "
        f"{'incluídas' if include_without_deadline else 'excluídas'} dos alertas."
    )
    selected_levels = st.multiselect(
        "Níveis exibidos",
        options=["Crítico — início", "Alto", "Atenção", "Em dia", "Sem atividades"],
        default=["Crítico — início", "Alto", "Atenção"],
    )
    search = st.text_input("Buscar aluno", placeholder="Digite parte do nome")
    filtered = risks[risks["nivel_risco"].isin(selected_levels)].copy()
    if search:
        filtered = filtered[
            filtered["aluno"].str.contains(search, case=False, na=False, regex=False)
        ]

    display_columns = [
        "aluno",
        "nivel_risco",
        "motivo",
        "entregues",
        "taxa_entrega_vencida",
        "pendencias_vencidas",
        "pendencias_sem_prazo",
        "entregas_atrasadas",
        "ultimo_movimento",
    ]
    if filtered.empty:
        st.success("Nenhum aluno corresponde aos filtros atuais.")
    else:
        table = filtered[display_columns].copy()
        table["taxa_entrega_vencida"] = table["taxa_entrega_vencida"].map(
            _format_percent
        )
        table["ultimo_movimento"] = table["ultimo_movimento"].map(_format_datetime)
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                "aluno": "Aluno",
                "nivel_risco": "Risco",
                "motivo": "Motivo",
                "entregues": "Entregues",
                "taxa_entrega_vencida": "Taxa vencidas",
                "pendencias_vencidas": "Pendências vencidas",
                "pendencias_sem_prazo": "Sem prazo",
                "entregas_atrasadas": "Entregas atrasadas",
                "ultimo_movimento": "Último movimento",
            },
        )

    if risks.empty:
        return
    student_label = risks.set_index("aluno_id")["aluno"].to_dict()
    student_id = st.selectbox(
        "Consultar histórico de um aluno",
        options=risks.sort_values("aluno")["aluno_id"].tolist(),
        index=None,
        placeholder="Selecione um aluno",
        format_func=lambda value: student_label.get(value, "Nome não informado"),
    )
    if student_id:
        student_rows = data.submissions[data.submissions["aluno_id"] == student_id][
            [
                "etapa",
                "atividade",
                "situacao",
                "prazo",
                "ultima_atualizacao",
                "link_atividade",
            ]
        ].copy()
        student_rows["prazo"] = student_rows["prazo"].map(_format_datetime)
        student_rows["ultima_atualizacao"] = student_rows[
            "ultima_atualizacao"
        ].map(_format_datetime)
        st.dataframe(
            student_rows,
            hide_index=True,
            width="stretch",
            column_config={
                "etapa": "Etapa",
                "atividade": "Atividade",
                "situacao": "Situação",
                "prazo": "Prazo",
                "ultima_atualizacao": "Último movimento",
                "link_atividade": st.column_config.LinkColumn(
                    "Classroom", display_text="Abrir"
                ),
            },
        )


def _render_activities_tab(data: DashboardData, drop_threshold: int) -> None:
    st.subheader("Módulos e atividades")
    st.caption(
        "A etapa é inferida do título (por exemplo, “Aula 0” e “Módulo 1”) para "
        "manter somente os três escopos OAuth já configurados."
    )

    modules = data.module_summary.copy()
    if not modules.empty:
        modules["taxa_entrega_geral"] = modules["taxa_entrega_geral"].map(
            _format_percent
        )
        modules["taxa_entrega_vencida"] = modules["taxa_entrega_vencida"].map(
            _format_percent
        )
        modules["variacao_pp"] = modules["variacao_pp"].map(
            lambda value: "—" if pd.isna(value) else f"{float(value):+.1f} p.p."
        )
        st.dataframe(
            modules[
                [
                    "etapa",
                    "atividades",
                    "atividades_vencidas",
                    "atribuicoes_vencidas",
                    "entregues_vencidas",
                    "pendencias_vencidas",
                    "taxa_entrega_vencida",
                    "variacao_pp",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "etapa": "Etapa",
                "atividades": "Atividades",
                "atividades_vencidas": "Vencidas",
                "atribuicoes_vencidas": "Atribuições vencidas",
                "entregues_vencidas": "Entregues",
                "pendencias_vencidas": "Pendências",
                "taxa_entrega_vencida": "Taxa de entrega",
                "variacao_pp": "Variação",
            },
        )

        numeric = data.module_summary.dropna(subset=["variacao_pp"])
        drops = numeric[numeric["variacao_pp"] <= -drop_threshold]
        if not drops.empty:
            names = ", ".join(drops["etapa"].astype(str))
            st.warning(
                f"Sinal coletivo: queda de pelo menos {drop_threshold} p.p. em {names}. "
                "Revisar clareza, carga e comunicação do módulo."
            )

    activities = data.activity_summary.copy()
    if activities.empty:
        st.info("Nenhuma atividade publicada.")
        return
    activities["prazo"] = activities["prazo"].map(_format_datetime)
    activities["taxa_entrega_geral"] = activities["taxa_entrega_geral"].map(
        _format_percent
    )
    activities["taxa_entrega_vencida"] = activities["taxa_entrega_vencida"].map(
        _format_percent
    )
    st.dataframe(
        activities[
            [
                "etapa",
                "atividade",
                "prazo",
                "atribuicoes",
                "entregues",
                "pendencias_vencidas",
                "entregas_atrasadas",
                "taxa_entrega_vencida",
                "link",
            ]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "etapa": "Etapa",
            "atividade": "Atividade",
            "prazo": "Prazo (Recife)",
            "atribuicoes": "Atribuições",
            "entregues": "Entregues",
            "pendencias_vencidas": "Pendências vencidas",
            "entregas_atrasadas": "Entregas atrasadas",
            "taxa_entrega_vencida": "Taxa vencidas",
            "link": st.column_config.LinkColumn("Classroom", display_text="Abrir"),
        },
    )


def _render_diagnostics_tab(
    snapshot: ClassroomSnapshot, data: DashboardData, auth_mode: str
) -> None:
    st.subheader("Dados e diagnóstico")
    st.write(
        {
            "Turma": snapshot.course.get("name", ""),
            "Modo de autenticação": "Streamlit Secrets" if auth_mode == "cloud" else "OAuth local",
            "Última coleta": _format_datetime(data.collected_at),
            "Alunos": len(snapshot.students),
            "Atividades": len(snapshot.coursework),
            "Registros de entrega": len(snapshot.submissions),
            "Participantes fora do roster atual": int(
                data.submissions.loc[
                    data.submissions["ativo_no_roster"].eq(False), "aluno_id"
                ].nunique()
            ),
        }
    )
    st.info(
        "Limitação conhecida: a Classroom API não fornece número de logins nem "
        "tempo de permanência. O painel usa entregas como indicador substituto."
    )
    st.caption(
        "`ultima_atualizacao` representa o último movimento registrado pelo Google; "
        "uma correção/devolução também pode alterar esse horário."
    )

    with st.expander("Exportar o snapshot atual (contém dados pessoais)"):
        st.warning(
            "Use o arquivo apenas com a coordenação/tutoria, não publique e defina "
            "um prazo de retenção conforme a política institucional."
        )
        export = data.submissions[
            [
                "atividade",
                "etapa",
                "aluno",
                "situacao",
                "prazo",
                "ultima_atualizacao",
            ]
        ].copy()
        st.download_button(
            "Baixar CSV",
            data=export.to_csv(index=False).encode("utf-8-sig"),
            file_name="entregas_classroom.csv",
            mime="text/csv",
        )


@st.fragment(run_every="1h")
def _render_dashboard_fragment(
    course_id: str,
    auth_cache_key: str,
    auth_mode: str,
    high_risk_threshold: int,
    include_without_deadline: bool,
    drop_threshold: int,
) -> None:
    fallback_key = f"last_good_snapshot:{auth_cache_key}:{course_id}"
    try:
        with st.spinner("Consultando o Google Classroom..."):
            snapshot = _cached_snapshot(course_id, auth_cache_key, auth_mode)
    except (
        ClassroomAPIError,
        ClassroomAuthenticationRequired,
        ClassroomConfigurationError,
    ) as exc:
        snapshot = st.session_state.get(fallback_key)
        if snapshot is None:
            st.error(str(exc))
            st.info("Use “Trocar conta Google” ou corrija o Secrets e tente novamente.")
            return
        st.warning(
            "A atualização falhou; exibindo o último snapshot válido desta sessão. "
            f"Motivo: {exc}"
        )
    else:
        st.session_state[fallback_key] = snapshot

    data = build_dashboard_data(snapshot)
    risks = build_student_risk_summary(
        data,
        snapshot.students,
        high_risk_threshold=high_risk_threshold,
        include_without_deadline=include_without_deadline,
    )
    recognized_mask = data.activities["etapa"].map(is_recognized_stage).astype(bool)
    unrecognized = data.activities[~recognized_mask]
    if not unrecognized.empty:
        examples = ", ".join(unrecognized["atividade"].astype(str).head(3))
        suffix = "…" if len(unrecognized) > 3 else ""
        st.warning(
            f"{len(unrecognized)} atividade(s) sem etapa reconhecida ({examples}{suffix}). "
            "Inclua “Aula 0” ou “Módulo N” no título para habilitar a janela crítica "
            "e a comparação sequencial."
        )
    overview, risk, activities, diagnostics = st.tabs(
        ["Visão geral", "Alunos em atenção", "Módulos e atividades", "Diagnóstico"]
    )
    with overview:
        _render_overview(snapshot, data, risks)
    with risk:
        _render_risk_tab(
            data,
            risks,
            high_risk_threshold,
            include_without_deadline,
        )
    with activities:
        _render_activities_tab(data, drop_threshold)
    with diagnostics:
        _render_diagnostics_tab(snapshot, data, auth_mode)


def main() -> None:
    st.set_page_config(
        page_title="Painel 4Ds — Classroom",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not _require_dashboard_password():
        return

    resolved = _resolve_auth_mode()
    if resolved is None:
        return
    auth_mode, auth_cache_key = resolved

    cloud_password = _dashboard_password()
    if auth_mode == "cloud" and (
        not cloud_password or cloud_password == "DEFINA_UMA_SENHA_FORTE"
    ):
        st.error(
            "Por segurança, o modo Cloud exige `[app].password` no Streamlit "
            "Secrets antes de consultar dados pessoais."
        )
        return

    try:
        courses = _cached_courses(auth_cache_key, auth_mode)
    except (
        ClassroomAPIError,
        ClassroomAuthenticationRequired,
        ClassroomConfigurationError,
    ) as exc:
        st.error(str(exc))
        if auth_mode == "local" and st.button("Autorizar outra conta Google"):
            DEFAULT_TOKEN_PATH.unlink(missing_ok=True)
            st.session_state.pop("oauth_auto_started", None)
            _clear_data_caches()
            st.rerun()
        return

    st.title("Acompanhamento do Google Classroom")
    st.caption(
        "Curso Os 4D's do Negócio · entregas, atrasos e sinais objetivos de risco"
    )
    if not courses:
        st.error(
            "A conta autorizada não aparece como professora de nenhuma turma ativa. "
            "Troque a conta ou confirme o vínculo no Classroom."
        )
        if auth_mode == "local" and st.button("Trocar conta Google", type="primary"):
            DEFAULT_TOKEN_PATH.unlink(missing_ok=True)
            st.session_state.pop("oauth_auto_started", None)
            _clear_data_caches()
            st.rerun()
        return

    course_by_id = {str(course["id"]): course for course in courses}
    with st.sidebar:
        st.header("Configuração")
        course_id = st.selectbox(
            "Turma",
            options=list(course_by_id),
            format_func=lambda value: _course_label(course_by_id[value]),
        )
        high_risk_threshold = st.slider(
            "Pendências para risco alto",
            min_value=1,
            max_value=5,
            value=2,
            help="Aula 0/Módulo 1 continua crítica já na primeira pendência.",
        )
        include_without_deadline = st.toggle(
            "Incluir atividades sem prazo nos alertas",
            value=False,
            help=(
                "Desativado por padrão para não classificar como atraso uma atividade "
                "sem data objetiva."
            ),
        )
        drop_threshold = st.slider(
            "Queda coletiva mínima (p.p.)",
            min_value=5,
            max_value=50,
            value=10,
            step=5,
        )
        if st.button("Atualizar agora", type="primary", width="stretch"):
            _cached_snapshot.clear()
            st.rerun()
        if auth_mode == "local" and st.button(
            "Trocar conta Google", width="stretch"
        ):
            DEFAULT_TOKEN_PATH.unlink(missing_ok=True)
            st.session_state.pop("oauth_auto_started", None)
            _clear_data_caches()
            st.rerun()
        if _dashboard_password() and st.button("Sair do painel", width="stretch"):
            st.session_state.pop("dashboard_authorized", None)
            st.rerun()
        st.divider()
        st.caption(
            "Atualização garantida na primeira abertura após 24h. Sem acessos, o "
            "Community Cloud pode hibernar e não executa coleta em segundo plano."
        )

    selected_course = course_by_id[course_id]
    link = selected_course.get("alternateLink")
    if link:
        st.link_button("Abrir turma no Classroom", str(link))

    _render_dashboard_fragment(
        course_id,
        auth_cache_key,
        auth_mode,
        high_risk_threshold,
        include_without_deadline,
        drop_threshold,
    )


if __name__ == "__main__":
    main()
