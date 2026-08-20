# Painel de entregas do Google Classroom

Aplicação em Streamlit para acompanhar o curso **Os 4D's do Negócio** por meio
da API oficial do Google Classroom. O painel transforma dados de atividades e
entregas em uma visão objetiva para a coordenação, tutoria e docentes
autorizados identificarem pendências e agirem no momento certo.

O sistema é uma ferramenta interna de acompanhamento. Ele não substitui o
Google Classroom e não altera turmas, atividades ou entregas: o acesso aos
dados do Classroom é somente leitura.

## Propósito do sistema

O painel foi criado para tornar o acompanhamento do curso mais rápido e
consistente. Em vez de conferir atividade por atividade no Classroom, a equipe
pode visualizar em um só lugar:

- quantos alunos e atividades existem na turma;
- a taxa de entrega das atividades cujo prazo já venceu;
- entregas realizadas, entregas com atraso e pendências vencidas;
- desempenho por Aula 0, Módulo 1 e etapas seguintes;
- alunos que precisam de atenção da tutoria;
- quedas coletivas de participação entre etapas.

Os sinais são apoio à tomada de decisão. A equipe responsável ainda deve
considerar o contexto de cada aluno antes de realizar uma intervenção.

## Como o site funciona

1. O responsável inicia o painel e autoriza uma conta Google docente. No modo
   local, a escolha da conta acontece na página oficial do Google.
2. O sistema lista somente as turmas ativas em que essa conta aparece como
   professora.
3. Depois que uma turma é selecionada, o painel consulta o roster atual, as
   atividades publicadas e suas submissões.
4. Os dados são organizados por etapa e transformados em indicadores de
   entrega, atraso, pendência e risco.
5. A interface apresenta os resultados em quatro abas e permite ajustar os
   critérios de acompanhamento pela barra lateral.
6. Quando necessário, **Atualizar agora** descarta o snapshot em cache e faz
   uma nova consulta à API.

```text
Google Classroom → coleta somente leitura → cálculo dos indicadores → painel Streamlit
```

## Funcionalidades

### Visão geral

Mostra os totais de alunos e atividades, a taxa de entrega das atividades
vencidas, a quantidade de pendências vencidas e o número de alunos em atenção.
Também apresenta gráficos da taxa de entrega por etapa e da situação das
entregas.

### Alunos em atenção

Cria uma lista de intervenção para a tutoria, com nível de risco, motivo do
alerta, quantidade de pendências e último movimento. É possível:

- filtrar por nível de risco;
- buscar um aluno pelo nome;
- consultar o histórico individual;
- abrir a atividade correspondente diretamente no Classroom.

### Módulos e atividades

Resume o desempenho de cada etapa, compara a taxa de entrega com a etapa
anterior e avisa quando existe uma queda coletiva acima do limite configurado.
A tabela detalhada mostra prazos, entregas, pendências e links das atividades.

### Diagnóstico e exportação

Informa a turma selecionada, o modo de autenticação, a hora da última coleta e
os totais recebidos. Usuários autorizados também podem baixar um CSV do snapshot
atual; esse arquivo contém dados pessoais e deve ser tratado de forma restrita.

### Controles da barra lateral

A barra lateral permite selecionar a turma, definir o número de pendências que
gera risco alto, incluir ou não atividades sem prazo nos alertas, configurar a
queda coletiva mínima e solicitar uma atualização. No modo local, também é
possível trocar a conta Google conectada.

## Regras principais dos indicadores

- Estados `TURNED_IN` e `RETURNED` são considerados entregues.
- A taxa principal usa somente atividades cujo prazo já venceu.
- Uma atividade futura não gera alerta de atraso.
- Atividades sem prazo ficam em revisão manual e só entram nos alertas quando
  essa opção é ativada.
- Uma pendência elegível na **Aula 0** ou no **Módulo 1** gera nível crítico.
- Nas demais etapas, o limite de pendências para risco alto é configurável.
- A etapa é identificada pelo texto `Aula 0` ou `Módulo N` no título da
  atividade; títulos fora desse padrão são sinalizados pelo painel.
- Datas e horários são exibidos no fuso `America/Recife`.

Participantes que saíram do roster atual continuam anonimamente nos indicadores
agregados, para que uma saída não melhore a taxa de forma artificial. Eles não
aparecem na lista nominal de intervenção.

## Atualização dos dados

A lista de turmas fica em cache por 10 minutos e os dados de uma turma por 24
horas. O botão **Atualizar agora** permite antecipar a próxima consulta. Se o
Streamlit Community Cloud estiver hibernando por falta de acessos, não existe
coleta em segundo plano; este MVP também não mantém uma série histórica
persistente.

## Limites do painel

- Exibe apenas turmas ativas em que a conta conectada é professora.
- Usa entregas como indicador de acompanhamento; a Classroom API utilizada não
  fornece número de logins nem tempo de permanência.
- Não persiste, exibe nem exporta notas, e-mails, anexos ou textos das entregas.
- Os indicadores ajudam a priorizar o acompanhamento, mas não explicam sozinhos
  o motivo de uma pendência.

## Rodar no localhost para os testes iniciais

O projeto foi validado com Python 3.12. Na raiz do projeto, execute conforme o
seu sistema.

Linux, macOS ou WSL:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py --server.address=127.0.0.1
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\streamlit.exe run app.py --server.address=127.0.0.1
```

Abra `http://127.0.0.1:8501` no navegador. Se `python` não existir no Linux,
use `python3.12` ou `python3` no primeiro comando.

Na primeira utilização completa, o projeto também precisa de uma credencial
OAuth Desktop chamada `credentials.json`. O passo a passo de configuração,
autorização e validação está em [`instruções.md`](instruções.md).

## Privacidade e segurança

O painel lida com nomes e situações acadêmicas. Por isso, seu acesso e qualquer
CSV exportado devem ficar restritos à coordenação, tutoria e docentes
autorizados. Nunca envie ao Git credenciais OAuth, tokens, Secrets reais ou
arquivos com dados dos alunos. A senha da conta Google nunca é solicitada pelo
código.

## Documentação técnica

Consulte [`instruções.md`](instruções.md) para reproduzir o ambiente e dar
continuidade ao projeto. O documento inclui:

- configuração do Google Cloud e do OAuth;
- instalação local detalhada;
- testes e validações;
- publicação no Streamlit Community Cloud;
- backup, recuperação e passagem para outra pessoa;
- diagnóstico de problemas comuns.
