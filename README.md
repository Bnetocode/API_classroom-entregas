# Painel de entregas do Google Classroom

Aplicação local em Streamlit para acompanhar o curso **Os 4D's do Negócio**.
Ela consulta diretamente a API oficial do Google Classroom em modo somente
leitura e mostra entregas, atrasos, taxa por etapa e os três sinais de atenção
descritos na proposta do projeto.

## Executar no localhost

Na pasta do projeto:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py --server.address=127.0.0.1
```

O terminal mostrará a URL do Streamlit, normalmente
`http://localhost:8501`. Na primeira abertura do painel, outra página será
aberta pelo Google para escolher a conta que é professora das turmas. A URL de
autorização também será impressa no terminal caso o navegador não abra.

### Onde informar o e-mail da conta que possui as turmas?

**Não se informa o e-mail no código.** O OAuth do Google pede que a conta seja
escolhida/digitada na página oficial de consentimento. Depois disso:

- `teacherId="me"` lista apenas as turmas em que essa conta é professora;
- o painel apresenta um seletor quando a conta tem mais de uma turma;
- `token.json` é salvo localmente e renovado sem novo login enquanto a
  autorização continuar válida;
- o botão **Trocar conta Google** apaga apenas o token local e abre o fluxo outra
  vez.

O comentário que marca exatamente a requisição da conta/turmas está em
`anexo_api_classroom_etapas_1a5.py`.

## Pré-requisitos no Google Cloud

1. Classroom API ativada no projeto.
2. Tela de consentimento OAuth configurada com os três escopos de leitura usados
   em `classroom_client.py`.
3. A conta docente adicionada em **Audience > Test users** enquanto o aplicativo
   estiver no modo *Testing*.
4. `credentials.json` do tipo **Desktop app** ao lado de `app.py`.

`credentials.json`, `token.json`, o Secrets real e os CSVs estão no `.gitignore`
e nunca devem ser enviados ao GitHub.

## Como os alertas são calculados

- `TURNED_IN` e `RETURNED` contam como entrega.
- A taxa principal considera apenas atividades cujo prazo já venceu.
- Atividade futura aparece como pendente dentro do prazo e não gera risco.
- Atividade sem prazo fica em revisão manual e não gera risco por padrão. Há um
  controle explícito na barra lateral para incluí-la.
- Qualquer pendência elegível na **Aula 0/Módulo 1** vira alerta crítico.
- Nas demais etapas, o limite de pendências para risco alto é configurável.
- A queda da taxa entre etapas é mostrada em pontos percentuais.

A etapa é inferida do título da atividade (`Aula 0`, `Módulo 1`, etc.). Isso
evita solicitar um quarto escopo OAuth apenas para consultar os nomes dos
tópicos. Portanto, mantenha esse padrão nos títulos do Classroom. O painel
mostra um aviso explícito quando não consegue reconhecer uma etapa.

Submissões de participantes que já saíram do roster continuam apenas nas taxas
agregadas, com nome genérico, para uma desmatrícula não melhorar artificialmente
o indicador. A lista nominal de intervenção contém somente o roster atual.

## Atualização diária

O snapshot fica em cache por 24 horas e existe um botão **Atualizar agora**. O
comportamento garantido é:

- primeiro acesso depois de 24 horas: consulta nova na API;
- painel mantido aberto: o fragmento verifica o cache a cada hora e consulta a
  API assim que as 24 horas expirarem;
- atualização manual: limpa o cache imediatamente.

O Streamlit Community Cloud pode hibernar sem tráfego. Assim, ele não executa
uma coleta em segundo plano enquanto estiver dormindo. Se for necessário manter
uma série histórica diária mesmo sem ninguém abrir o painel, será preciso um
agendador externo e armazenamento persistente; isso é uma etapa diferente deste
MVP.

## Publicar no Streamlit Community Cloud

Antes de publicar, transforme esta pasta em um repositório próprio e envie-o ao
GitHub **sem** arquivos secretos. No Community Cloud, selecione `app.py` como
entrypoint, escolha Python **3.12** em **Advanced settings** e cole em
**Secrets** um conteúdo baseado em `.streamlit/secrets.toml.example`.

O modo Cloud usa uma conta fixa pré-autorizada. O Google exige um cliente por
plataforma, portanto mantenha o `credentials.json` do tipo Desktop apenas para o
localhost e provisione o Cloud assim:

1. Crie outro cliente OAuth do tipo **Web application** no mesmo projeto.
2. Adicione exatamente `http://localhost:8080/` em **Authorized redirect URIs**.
3. Baixe-o como `credentials_web.json` para esta pasta.
4. Execute `.venv/bin/python provision_cloud_token.py` e autorize a conta docente.
5. Copie de `token_cloud.json` somente os valores abaixo para o Secrets:

```toml
[google_oauth]
client_id = "..."
client_secret = "..."
refresh_token = "..."
token_uri = "https://oauth2.googleapis.com/token"

[app]
password = "uma-senha-longa-e-exclusiva"
```

`client_id`, `client_secret` e `refresh_token` precisam pertencer ao **mesmo
cliente Web**; não misture um token emitido para um cliente com os dados de
outro. O retorno localhost acima serve somente para o provisionamento feito por
você. Depois, o Cloud renova o token já emitido e não expõe um login Google aos
visitantes. Um login interativo dentro do site exigiria callback público,
validação de `state` e armazenamento individual seguro dos tokens.

### Limite de sete dias no modo Testing

Segundo a política do Google, autorizações de aplicativos **External/Testing**
que usam escopos do Classroom expiram em sete dias, inclusive o refresh token.
Para uma operação diária duradoura, altere o status OAuth para produção e siga o
processo de publicação/verificação aplicável. Enquanto continuar em Testing,
execute novamente `provision_cloud_token.py` e atualize o `refresh_token` no
Secrets quando ele expirar. Depois de mudar para produção, gere um token novo
para não depender do token emitido ainda durante Testing.

## Privacidade

O painel trata nomes e situação acadêmica, que são dados pessoais:

- mantenha o app **privado**, controle os convidados no Community Cloud e
  configure `[app].password` com pelo menos 12 caracteres como camada adicional;
- não colete e-mails, notas, anexos ou descrições — o código não os armazena;
- compartilhe exportações somente com coordenação/tutoria;
- defina com a coordenação o prazo de retenção e a exclusão ao final do projeto;
- valide a política institucional antes de hospedar dados identificáveis no
  Community Cloud.

A Classroom API não fornece número de logins nem tempo de permanência. Esses
dados dependeriam da Admin Reports API de uma organização Google Workspace. Por
isso, o projeto usa entregas como indicador substituto de engajamento.

## Estrutura

- `app.py`: interface Streamlit, filtros, cache e mensagens de autenticação.
- `classroom_client.py`: OAuth, paginação e coleta mínima da API.
- `analytics.py`: prazos, taxas e sinais de risco.
- `anexo_api_classroom_etapas_1a5.py`: execução opcional pelo terminal/CSV.
- `provision_cloud_token.py`: cria o token do cliente Web usado no Cloud.
- `tests/`: testes sem acesso à conta real.

Para executar os testes:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

O teste automatizado valida regras e chamadas simuladas. A confirmação dos IDs,
nomes e permissões da turma real só ocorre depois do consentimento da conta
docente.

## Referências oficiais

- [Quickstart Python da Classroom API](https://developers.google.com/workspace/classroom/quickstart/python)
- [OAuth para aplicativos Desktop](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Escopos da Classroom API](https://developers.google.com/workspace/classroom/guides/auth)
- [Secrets no Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Dependências no Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
