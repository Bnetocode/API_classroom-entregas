# -*- coding: utf-8 -*-
"""Coleta manual/CLI dos mesmos dados usados pelo painel Streamlit.

O painel principal é executado com ``streamlit run app.py``. Este anexo continua
útil para validar a API no terminal e gerar um CSV pontual.
"""

from __future__ import annotations

from pathlib import Path

from analytics import build_dashboard_data
from classroom_client import (
    DEFAULT_CREDENTIALS_PATH,
    ClassroomAPIError,
    ClassroomAuthenticationRequired,
    authorize_local_account,
    build_classroom_service,
    collect_course_snapshot,
    list_teacher_courses,
    load_local_credentials,
)


OUTPUT_PATH = Path(__file__).resolve().parent / "entregas_classroom.csv"


# %% Célula 1 — Autenticação local
def autenticar():
    """Reaproveita token.json ou abre o Google no primeiro acesso."""

    try:
        return load_local_credentials()
    except ClassroomAuthenticationRequired:
        # NÃO coloque o e-mail da professora no código. A chamada abaixo abre a
        # página oficial do Google (e imprime a URL no terminal), onde você
        # escolhe/digita a conta que possui as turmas do Classroom.
        return authorize_local_account(DEFAULT_CREDENTIALS_PATH)


# %% Célula 2 — Listar e selecionar uma turma da conta docente
def selecionar_turma(cursos: list[dict[str, str]]) -> str:
    if not cursos:
        raise RuntimeError(
            "A conta autorizada não é professora de nenhuma turma ativa. "
            "Confirme a conta escolhida no Google Classroom."
        )

    print(f"{len(cursos)} turma(s) ativa(s) em que a conta é professora:")
    for index, curso in enumerate(cursos, start=1):
        print(f"  {index}. {curso['name']} (id={curso['id']})")

    if len(cursos) == 1:
        print("Única turma disponível selecionada automaticamente.")
        return str(cursos[0]["id"])

    while True:
        answer = input("Digite o número da turma que deseja consultar: ").strip()
        try:
            selected = int(answer) - 1
        except ValueError:
            print("Informe apenas o número mostrado na lista.")
            continue
        if 0 <= selected < len(cursos):
            return str(cursos[selected]["id"])
        print("Número de turma inválido.")


def main() -> None:
    credentials = autenticar()
    service = build_classroom_service(credentials)
    print("Autenticação concluída.")

    # ----------------------------------------------------------------------
    # ESTA É A PARTE QUE PUXA AS TURMAS DA CONTA AUTORIZADA.
    # ``list_teacher_courses`` executa internamente:
    # service.courses().list(teacherId="me", courseStates=["ACTIVE"], ...)
    # com paginação. ``me`` significa a conta escolhida na tela do Google;
    # portanto NÃO há um lugar correto para escrever o e-mail neste arquivo.
    # ----------------------------------------------------------------------
    cursos = list_teacher_courses(service)
    course_id = selecionar_turma(cursos)

    # %% Células 3 a 5 — atividades, alunos, entregas e consolidação
    # A coleta usa courseWorkId="-" para trazer todas as entregas com paginação,
    # evitando uma chamada separada para cada atividade.
    snapshot = collect_course_snapshot(service, course_id)
    data = build_dashboard_data(snapshot)

    print(f"Turma: {snapshot.course['name']}")
    print(f"{len(snapshot.students)} aluno(s) matriculado(s).")
    print(f"{len(snapshot.coursework)} atividade(s) publicada(s).")

    export_columns = [
        "atividade_id",
        "atividade",
        "etapa",
        "aluno_id",
        "aluno",
        "estado_api",
        "situacao",
        "entregue",
        "atrasada",
        "em_atraso",
        "prazo",
        "ultima_atualizacao",
    ]
    export = data.submissions.reindex(columns=export_columns)
    export.to_csv(OUTPUT_PATH, index=False)
    print(f"CSV gerado com {len(export)} linha(s): {OUTPUT_PATH.name}")
    if export.empty:
        print("A turma ainda não possui registros de entrega publicados.")
    else:
        print(export.head().to_string(index=False))


if __name__ == "__main__":
    try:
        main()
    except (ClassroomAPIError, ClassroomAuthenticationRequired, RuntimeError) as exc:
        raise SystemExit(f"Erro: {exc}") from exc

