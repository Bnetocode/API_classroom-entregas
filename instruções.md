# Instruções de instalação, execução e continuidade

Este é o manual técnico do **Painel de entregas do Google Classroom**. Ele
explica como preparar o ambiente, configurar o acesso ao Google, executar os
testes, publicar o sistema e entregar sua manutenção a outra pessoa.

Para conhecer o propósito, as funcionalidades e o uso do site, consulte o
[`README.md`](README.md).

> **Regra de segurança:** a senha da conta Google nunca é usada pelo código.
> A conta é escolhida manualmente na página oficial do Google. Nunca coloque
> e-mail, senha, authorization code, access token, refresh token ou client
> secret no código, no README, neste documento, em issues, commits ou logs
> compartilhados.

## Teste inicial no localhost

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

Depois, abra `http://127.0.0.1:8501` no navegador. Se `python` não existir no
Linux, use `python3.12` ou `python3` no primeiro comando:

```bash
python3.12 -m venv .venv
```

Nos demais comandos deste manual, no Windows substitua `.venv/bin/python` por
`.\.venv\Scripts\python.exe`.

Nas execuções seguintes, com o ambiente já criado e as dependências instaladas,
normalmente basta executar o terceiro comando. A primeira conexão completa
também requer `credentials.json`; a configuração segura desse arquivo está
explicada em **Reconstruir o ambiente local do zero**.

## Visão geral do funcionamento

```text
credentials.json ──> OAuth Desktop no navegador ──> token.json
                                                    │
Google Classroom API <── classroom_client.py <──────┘
          │
          ├── turmas em que teacherId="me" é professor
          ├── roster atual
          ├── atividades publicadas
          └── submissões
                    │
                    v
               analytics.py
                    │
                    v
                 app.py
                    │
                    v
             painel Streamlit
```

No fluxo local:

1. `app.py` tenta carregar `token.json`.
2. Se o token estiver válido, ele é reutilizado sem abrir o Google.
3. Se estiver expirado e possuir refresh token, `classroom_client.py` o renova
   e salva a versão atualizada.
4. Se não houver autorização utilizável, o navegador abre o OAuth oficial do
   Google usando `credentials.json`.
5. Depois do consentimento, o token é salvo e o Streamlit faz um novo ciclo de
   execução para mostrar o painel autenticado.
6. `teacherId="me"` passa a significar a conta escolhida nessa tela do Google.

Nenhum e-mail fica fixado no projeto.

## Estrutura do projeto

### Arquivos versionados

| Arquivo | Responsabilidade |
| --- | --- |
| `README.md` | Apresentação do propósito, das funcionalidades e do uso do site. |
| `instruções.md` | Manual técnico de instalação, testes, publicação, recuperação e continuidade. |
| `app.py` | Interface Streamlit, seleção de turma, filtros, mensagens de autenticação, cache e troca de conta. |
| `classroom_client.py` | OAuth, caminhos absolutos, persistência e renovação do token, chamadas paginadas e coleta mínima da Classroom API. |
| `analytics.py` | Normalização de prazos para `America/Recife`, inferência de etapas, taxas e classificação dos sinais de risco. |
| `anexo_api_classroom_etapas_1a5.py` | Execução opcional pelo terminal para validar a API e gerar um CSV pontual. |
| `provision_cloud_token.py` | Provisionamento separado do refresh token de um cliente Web para o Streamlit Community Cloud, com a mesma validação estrita da resposta OAuth. |
| `requirements.txt` | Dependências Python com versões fixadas para tornar o ambiente reproduzível. |
| `.gitignore` | Impede o versionamento de ambientes, credenciais, tokens, temporários, Secrets e CSVs. |
| `.streamlit/config.toml` | Tema visual e desativação da coleta de estatísticas de uso do Streamlit. |
| `.streamlit/secrets.toml.example` | Modelo sem valores reais para configurar o Community Cloud. |
| `tests/test_analytics.py` | Testes das regras de prazo, entrega, agregação e risco. |
| `tests/test_classroom_client.py` | Testes de OAuth, token, refresh, concorrência, caminhos, paginação e separação local/Cloud. |
| `tests/test_app_render.py` | Testes de renderização do painel sem acessar uma conta real. |
| `tests/__init__.py` | Marca o diretório como pacote de testes. |

### Artefatos locais que nunca devem ser versionados

| Arquivo/diretório | Origem e recuperação |
| --- | --- |
| `.venv/` | Ambiente Python recriado a partir de `requirements.txt`. |
| `credentials.json` | Cliente OAuth **Desktop app** usado somente no localhost. Guarde uma cópia em local seguro ou crie outro cliente Desktop se ele for perdido. |
| `token.json` | Autorização local criada depois do consentimento. Não precisa de backup: pode ser recriada autorizando a conta novamente. |
| `credentials_web.json` | Cliente OAuth **Web application** usado somente pelo provisionador Cloud. |
| `token_cloud.json` | Resultado sensível e temporário do provisionamento Cloud. Seus valores necessários vão para o Streamlit Secrets. |
| `.streamlit/secrets.toml` | Secrets reais usados ao testar o modo Cloud. |
| `entregas_classroom.csv` | Exportação opcional que pode conter nomes e situação acadêmica. |
| `__pycache__/` e `*.pyc` | Caches Python descartáveis. |

## Execução rápida no computador já configurado

Na raiz do projeto, use o comando correspondente ao seu sistema.

Linux, macOS ou WSL:

```bash
.venv/bin/streamlit run app.py --server.address=127.0.0.1
```

Windows PowerShell:

```powershell
.\.venv\Scripts\streamlit.exe run app.py --server.address=127.0.0.1
```

Abra:

```text
http://127.0.0.1:8501
```

Se `token.json` estiver válido, o painel abrirá diretamente. Na primeira
execução, ou depois de trocar/revogar a conta, o Google abrirá outra página
para a autorização manual.

## Reconstruir o ambiente local do zero

Use estas etapas em um computador novo ou depois de perder o ambiente local.

### 1. Restaurar o código

Clone ou copie somente os arquivos versionados do projeto. Entre na pasta que
contém `app.py`:

```bash
cd /caminho/para/projeto_exe
```

O projeto foi validado com Python 3.12. No Linux, macOS ou WSL, crie o ambiente
e instale exatamente as versões registradas:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Se `python3.12` não estiver disponível pelo nome, use o executável Python 3.12
instalado no sistema. No Windows, use o bloco de PowerShell apresentado em
**Teste inicial no localhost**, onde o primeiro comando é `python`. Não copie
`.venv` de outro computador; recrie-o.

### 2. Criar ou recuperar a configuração no Google Cloud

No projeto correto do Google Cloud:

1. Ative a **Google Classroom API**.
2. Abra **Google Auth Platform** e configure **Branding**, **Audience** e
   **Data Access**.
3. Se o aplicativo estiver como **External / Testing**, adicione como test user
   cada conta que precisará autorizar o aplicativo.
4. Mantenha somente estes três escopos solicitados pelo código:

```text
https://www.googleapis.com/auth/classroom.courses.readonly
https://www.googleapis.com/auth/classroom.rosters.readonly
https://www.googleapis.com/auth/classroom.coursework.students.readonly
```

5. Em **Google Auth Platform > Clients**, crie um cliente com o tipo
   **Desktop app**.
6. Baixe o JSON no momento da criação e salve-o na raiz do projeto com o nome
   exato `credentials.json`, ao lado de `app.py`.

O JSON local precisa ter a chave raiz `installed`. Não converta esse cliente em
Web e não use `credentials_web.json` no fluxo local. Se o arquivo antigo foi
perdido e o Console não permitir baixá-lo novamente, crie um novo cliente
Desktop.

Não é necessário cadastrar manualmente a porta de callback para o cliente
Desktop. O programa usa `run_local_server(port=0)`, e o sistema escolhe uma
porta livre em `localhost` a cada autorização.

### 3. Executar e autorizar

```bash
.venv/bin/streamlit run app.py --server.address=127.0.0.1
```

Depois:

1. abra `http://127.0.0.1:8501`;
2. na página oficial do Google, escolha manualmente a conta que é professora;
3. conceda somente as permissões solicitadas;
4. aguarde a mensagem de autorização concluída;
5. volte ao painel.

O Streamlit usa a porta `8501`. O callback OAuth é outro servidor temporário,
em um endereço como `http://localhost:42149/`. Essa diferença é normal.

### 4. Confirmar sem revelar o token

```bash
ls -l token.json
.venv/bin/python -m unittest discover -s tests -v
```

Não use `cat token.json` e não copie seu conteúdo para o terminal, chat ou
issue. A confirmação completa é:

- `token.json` existe na mesma pasta de `app.py`;
- no Linux, sua permissão aparece como `-rw-------` (`0600`);
- a tela **Conectar ao Google Classroom** desaparece;
- ao recarregar a página não surge outro OAuth;
- ao reiniciar o Streamlit o token é reutilizado;
- as turmas ativas da conta docente aparecem no seletor.

## Como o OAuth local foi implementado

Os caminhos são derivados de `Path(__file__).resolve().parent`. Portanto,
`credentials.json` e `token.json` são lidos e gravados na raiz real do projeto,
independentemente do diretório corrente do terminal.

O callback do `google-auth-oauthlib` mostra a mensagem de sucesso assim que a
resposta chega ao servidor local. A troca e validação final do token ainda
ocorrem depois disso. Por essa razão, a página **Autorização concluída** sozinha
não prova que o token foi persistido.

Neste projeto, o Google pode devolver o identificador
`classroom.student-submissions.students.readonly` para a permissão somente
leitura solicitada como `classroom.coursework.students.readonly`. A biblioteca
OAuth transforma essa substituição em `Warning` depois de o token já ter sido
emitido. `classroom_client.py` trata somente esse mapeamento observado e exige:

- igualdade exata dos conjuntos de escopos depois da normalização;
- nenhum escopo extra ou ausente;
- access token, validade e refresh token presentes;
- credenciais finais utilizáveis.

Não remova essa validação e não habilite relaxamento global de escopos. A
persistência usa arquivo temporário exclusivo, criado com permissão `0600`,
sincronizado e substituído atomicamente por `token.json`. Um lock também evita
que duas abas iniciem OAuth simultaneamente no mesmo processo.

O provisionador Cloud reutiliza a mesma validação do mapeamento de escopo antes
de criar `token_cloud.json`; os arquivos e tipos de cliente continuam separados.

Quando o token expira, `load_local_credentials()` tenta renová-lo. Se o refresh
token tiver sido revogado ou expirado, a interface solicita nova autorização.

## Trocar a conta Google

No modo local, o botão **Trocar conta Google**:

1. remove somente o `token.json` local;
2. limpa os caches associados à autorização;
3. reinicia o fluxo OAuth oficial;
4. passa a usar a nova conta escolhida como `"me"`.

Isso não remove turmas, usuários ou arquivos do Google Classroom e não revoga
automaticamente a autorização anterior no painel da conta Google. Se o Google
reutilizar uma sessão já aberta, escolha **Usar outra conta** na página oficial.

No modo Cloud não existe troca interativa de conta para visitantes. Para trocar
a conta fixa do Cloud, execute novamente o provisionamento Web e substitua o
Secrets do aplicativo.

### Trocar a conta fixa do Classroom no Community Cloud

Não é necessário alterar o código, o endereço do site ou fazer outro deploy.
Usando o mesmo `credentials_web.json`, faça o seguinte:

1. se o OAuth estiver em **External / Testing**, adicione a nova conta em
   **Google Auth Platform > Audience > Test users**;
2. execute localmente:

```bash
.venv/bin/python provision_cloud_token.py
```

3. na página oficial do Google, escolha **Usar outra conta**, selecione a nova
   conta docente e conclua o consentimento;
4. aguarde a criação do novo `token_cloud.json`;
5. em **Streamlit Community Cloud > aplicativo > Settings > Secrets**, troque
   somente o valor de `refresh_token` na seção `[google_oauth]`;
6. mantenha os valores atuais de `client_id`, `client_secret` e `token_uri`;
7. salve os Secrets e reinicie o aplicativo se isso não ocorrer
   automaticamente.

O `token_uri` continuará normalmente como
`https://oauth2.googleapis.com/token`. Todos os visitantes autorizados passarão
a ver as turmas da nova conta, pois `teacherId="me"` agora representa essa
conta. Somente se outro cliente OAuth Web for criado será necessário substituir
também `client_id` e `client_secret`.

Se apenas novas turmas forem adicionadas à conta que já está conectada, não
gere outro token. Aguarde até 10 minutos pelo cache da lista de turmas ou
reinicie o aplicativo.

## Como os dados do Classroom são coletados

`classroom_client.py` usa somente operações de leitura:

- `courses.list(teacherId="me", courseStates=["ACTIVE"])` lista, com
  paginação, apenas turmas ativas em que a conta autorizada é professora;
- `students.list` coleta o roster atual;
- `courseWork.list` coleta atividades publicadas;
- `studentSubmissions.list` coleta entregas;
- os objetos são reduzidos aos campos necessários antes de chegar à interface.

As respostas da API podem trazer campos adicionais antes dessa redução. Depois
da resposta, o código conserva nos objetos repassados ao restante do aplicativo
somente os campos selecionados em `classroom_client.py`; ele não persiste, exibe
nem exporta e-mails, notas, anexos ou descrições das entregas. A Classroom API
também não fornece número de logins nem tempo de permanência; esses dados
dependeriam da Admin Reports API de uma organização Workspace.

## Como os indicadores são calculados

- `TURNED_IN` e `RETURNED` contam como entrega.
- A taxa principal considera somente atividades cujo prazo já venceu.
- Atividade futura aparece como pendente dentro do prazo e não gera risco.
- Atividade sem prazo fica em revisão manual e não gera risco por padrão; a
  barra lateral permite incluí-la explicitamente.
- Qualquer pendência elegível na **Aula 0/Módulo 1** vira alerta crítico.
- Nas demais etapas, o limite de pendências para risco alto é configurável.
- A queda da taxa entre etapas é mostrada em pontos percentuais.
- Datas e horários são normalizados para `America/Recife`.

A etapa é inferida do título da atividade (`Aula 0`, `Módulo 1`, etc.). Isso
evita solicitar um quarto escopo apenas para consultar os nomes dos tópicos.
Mantenha esse padrão nos títulos do Classroom. O painel avisa quando não
consegue reconhecer uma etapa.

Submissões de participantes que saíram do roster continuam apenas nas taxas
agregadas, com nome genérico, para uma desmatrícula não melhorar artificialmente
o indicador. A lista nominal de intervenção contém somente o roster atual.

## Cache e atualização

- A lista de turmas fica em cache por 10 minutos.
- O snapshot da turma fica em cache por 24 horas.
- **Atualizar agora** limpa o cache de snapshots do processo; a próxima leitura
  de cada turma consultada volta à API.
- Com o painel aberto, um fragmento verifica o cache a cada hora e faz nova
  consulta quando as 24 horas expiram.
- Trocar autorização altera a chave de cache e impede misturar dados de contas.

O Community Cloud pode hibernar sem tráfego. Ele não executa coleta em segundo
plano enquanto estiver dormindo. Uma série histórica diária sem acessos exige
agendador externo e armazenamento persistente; isso não faz parte deste MVP.

## Execução opcional pelo terminal

Para validar a integração e gerar uma exportação pontual:

```bash
.venv/bin/python anexo_api_classroom_etapas_1a5.py
```

O script reutiliza `token.json`, lista as turmas de `teacherId="me"`, pede a
seleção pelo número e gera `entregas_classroom.csv`.

O CSV pode conter nomes e situação acadêmica. Ele está no `.gitignore`, mas
continua sendo responsabilidade de quem o gerar armazená-lo e compartilhá-lo
somente com pessoas autorizadas.

## Testes e validação antes de publicar alterações

Execute:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py classroom_client.py analytics.py tests
git diff --check
git status --short
```

No estado documentado deste projeto, a suíte contém 32 testes. Os testes são
isolados e não acessam a conta Google real.

Depois de uma alteração no OAuth ou nas chamadas da API, faça também o teste
manual completo:

1. autorize uma conta de teste pela página oficial;
2. confirme a criação de `token.json` sem abrir seu conteúdo;
3. confirme que uma turma ativa é listada;
4. atualize a página e verifique que o OAuth não abre novamente;
5. reinicie o Streamlit e confirme a reutilização do token;
6. teste **Atualizar agora**;
7. teste **Trocar conta Google** apenas quando quiser invalidar o token local.

Antes de `git add`, confirme que os arquivos sensíveis continuam ignorados:

```bash
git check-ignore -v \
  credentials.json credentials_web.json token.json token_cloud.json \
  .streamlit/secrets.toml
```

Nunca use `git add -f` nesses arquivos.

## Backup, recuperação e passagem para outra pessoa

### O que deve ficar no Git

- todo o código-fonte;
- `README.md`;
- `instruções.md`;
- `requirements.txt`;
- testes;
- `.gitignore`;
- `.streamlit/config.toml`;
- `.streamlit/secrets.toml.example`, apenas com placeholders.

### O que deve ficar fora do Git

- credenciais OAuth;
- tokens local e Cloud;
- Secrets reais;
- CSVs e outros dados pessoais.

Guarde em um cofre seguro, fora do repositório:

- identificação e responsáveis pelo projeto Google Cloud;
- quais contas administram o projeto;
- status do aplicativo OAuth (`Testing` ou `In production`);
- lista autorizada de test users, quando aplicável;
- existência do cliente Desktop e do cliente Web;
- backup seguro dos Secrets usados no Community Cloud.

Não é necessário guardar `token.json`: uma nova pessoa autorizada pode gerar
outro token escolhendo a conta correta no Google. Ela não precisa e não deve
receber a senha dessa conta.

Se todo o ambiente local for perdido:

1. clone o repositório;
2. recrie `.venv` com `requirements.txt`;
3. recupere com segurança ou recrie o cliente Desktop no Google Cloud;
4. coloque o novo `credentials.json` ao lado de `app.py`;
5. confirme API, Audience, Data Access e test users;
6. execute o Streamlit e refaça o consentimento;
7. rode a suíte e o checklist manual.

Se o responsável anterior sair, transfira primeiro o acesso administrativo ao
repositório, ao projeto Google Cloud e ao aplicativo Streamlit. Não transfira
esses acessos compartilhando senhas pessoais.

## Publicar no Streamlit Community Cloud

O localhost e o Cloud usam clientes OAuth separados:

| Ambiente | Cliente | Arquivo local | Callback |
| --- | --- | --- | --- |
| Streamlit local | Desktop app | `credentials.json` | `http://localhost:<porta-dinâmica>/` |
| Provisionamento Cloud | Web application | `credentials_web.json` | `http://localhost:8080/` |

Não misture os dois fluxos.

Antes de publicar, envie ao GitHub somente os arquivos versionados. No
Community Cloud, selecione `app.py` como entrypoint, use Python 3.12 em
**Advanced settings** e mantenha o aplicativo privado.

Para provisionar a conta fixa do Cloud:

1. crie outro cliente OAuth do tipo **Web application** no mesmo projeto;
2. adicione exatamente `http://localhost:8080/` em **Authorized redirect URIs**;
3. baixe o JSON como `credentials_web.json` para a raiz do projeto;
4. execute:

```bash
.venv/bin/python provision_cloud_token.py
```

5. autorize manualmente a conta docente;
6. copie de `token_cloud.json` apenas os valores necessários para o Secrets do
   Streamlit, seguindo `.streamlit/secrets.toml.example`:

```toml
[google_oauth]
client_id = "..."
client_secret = "..."
refresh_token = "..."
token_uri = "https://oauth2.googleapis.com/token"
```

`client_id`, `client_secret` e `refresh_token` precisam pertencer ao mesmo
cliente Web.

O retorno `localhost:8080` é usado somente durante o provisionamento feito pelo
responsável. Depois disso, o Cloud renova o token já emitido e não apresenta
login Google aos visitantes. Antes de salvar `token_cloud.json`, o provisionador
também exige credenciais válidas, refresh token e o conjunto exato de escopos.

### Limite do modo Testing

Autorizações de aplicativos **External / Testing** expiram em sete dias,
inclusive o refresh token. Enquanto o aplicativo continuar em Testing, será
necessário executar novamente `provision_cloud_token.py` e atualizar o Secrets
quando o token expirar.

Para operação duradoura, avalie publicar o aplicativo OAuth em produção e
seguir o processo de verificação exigido para os escopos utilizados. Depois da
mudança, gere um token novo.

## Diagnóstico de problemas comuns

### O callback conclui, mas não existe `token.json` ou `token_cloud.json`

A página confirma apenas a chegada do callback. Verifique o erro seguro no
terminal e confirme as versões de `requirements.txt`. O código atual já trata
o mapeamento conhecido de escopo do Classroom e só persiste credenciais
completas. Não imprima o conteúdo do callback nem do token.

### `credentials.json` não foi encontrado ou foi rejeitado

- confirme que o nome é exatamente `credentials.json`;
- confirme que ele está ao lado de `app.py`;
- use um cliente **Desktop app**, cuja chave raiz é `installed`;
- não renomeie `credentials_web.json` para tentar fazê-lo funcionar localmente.

### Erro `access_denied`, aplicativo bloqueado ou usuário não permitido

Revise **Audience**, o status `Testing/In production`, os test users e os três
escopos em **Data Access**. A conta escolhida também precisa ter acesso ao
Google Classroom.

### O token expirou, foi revogado ou tem escopos antigos

Use **Trocar conta Google** e conclua novamente o consentimento. Em
`External / Testing`, lembre que o refresh token expira em sete dias.

### Nenhuma turma aparece

O projeto usa `teacherId="me"` e somente `courseStates=["ACTIVE"]`. Confirme
que a conta escolhida é professora, e não apenas aluna, de pelo menos uma turma
ativa.

### Erro 403 da Classroom API

Confirme que a Google Classroom API está ativada, que todos os escopos foram
concedidos e que a conta tem permissão docente sobre a turma.

### Porta ocupada

- `8501`: porta do Streamlit; encerre a instância anterior ou informe outra
  porta ao Streamlit.
- `8080`: usada somente por `provision_cloud_token.py`.
- OAuth Desktop local: usa automaticamente uma porta dinâmica livre.

### Problemas depois de atualizar dependências

As versões são fixadas porque mudanças em OAuth podem alterar o comportamento
entre callback e persistência. Atualize uma dependência por vez, rode os 31
testes e repita o fluxo real antes de publicar.

## Privacidade e segurança

O painel trata nomes e situação acadêmica, que são dados pessoais:

- mantenha o app Cloud privado e controle seus convidados;
- não persista, exiba nem exporte e-mails, notas, anexos ou descrições;
- compartilhe exportações somente com coordenação/tutoria autorizada;
- defina prazo de retenção e exclusão com a coordenação;
- valide a política institucional antes de hospedar dados identificáveis;
- revogue a autorização do aplicativo no Google quando ele deixar de ser usado.

## Referências oficiais

- [Quickstart Python da Classroom API](https://developers.google.com/workspace/classroom/quickstart/python)
- [OAuth para aplicativos Desktop](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Gerenciar clientes OAuth](https://support.google.com/cloud/answer/15549257)
- [Gerenciar Audience e test users](https://support.google.com/cloud/answer/15549945?hl=en)
- [Escopos da Classroom API](https://developers.google.com/workspace/classroom/guides/auth)
- [Secrets no Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Dependências no Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
