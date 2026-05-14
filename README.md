# SGC - AGMEAL (Sistema de Gestão de Cadastros)

Sistema desenvolvido em **Python (Flask + SQLAlchemy)** para a gestão de registros de associados, servido por **Gunicorn** em contêiner **Docker** e publicado via **Nginx** como Proxy Reverso.

## Funcionalidades
- **Autenticação:** login com setup guiado de primeira execução e rate limiting.
- **Recuperação de Acesso:** fluxo de redefinição de senha via Frase de Segurança.
- **Gestão de Associados (CRUD):** cadastro, busca, edição, exclusão e listagem paginada.
- **Fotos 3x4:** upload com crop client-side (Cropper.js), validação de tipo/tamanho no servidor e limpeza automática de fotos órfãs.
- **Geração de PDF:** fichas individuais e relatórios em lote utilizando `WeasyPrint`.
- **Backup (admin):** ZIP com banco (cópia segura SQLite), fotos e segredo de sessão; cópia opcional para pasta sincronizada (Google Drive / OneDrive).
- **Restaurar (admin):** upload de ZIP com confirmações explícitas (texto + caixa) e backup de segurança automático antes de substituir dados; ver secção *Restauração* abaixo.
- **Interface:** front-end responsivo com Bootstrap 5 e FontAwesome.

## Tecnologias
- **Backend:** Python 3.12, Flask 3, SQLAlchemy, Flask-Migrate (Alembic), Flask-WTF, Flask-Limiter.
- **Servidor:** Gunicorn atrás de Nginx (com `ProxyFix` no Flask).
- **Banco:** SQLite em volume local (`data/sgc.db`).
- **Frontend:** HTML5, CSS3, Bootstrap 5, Cropper.js.
- **Infra:** Docker Compose, Nginx Alpine, healthcheck de serviço.

## Como Executar

### Opção 1 — Windows (recomendado para uso em PCs comuns)

1. Copie a pasta `sgc-agmeal` para o computador
2. Duplo clique em **`INSTALAR.bat`** e aceite o pedido de Administrador
3. O script faz tudo sozinho:
   - Instala WSL2 + Ubuntu (se necessário)
   - Copia o projeto e instala todas as dependências
   - Configura a URL amigável **`http://sgc.local`** (mapeia em `hosts` +
     redireciona porta 80 → 5000 via `netsh portproxy`)
   - Cria o atalho **SGC-AGMEAL** na Área de Trabalho (abre direto no navegador)
   - Configura **auto-start no boot** (script `.vbs` invisível em `shell:startup`)
4. Para usar: duplo clique em **SGC-AGMEAL** na Área de Trabalho

O servidor passa a iniciar **sozinho** toda vez que o PC ligar — o atalho só
abre o navegador na URL configurada.

> Na primeira instalação do WSL, o Windows pede para reiniciar o PC. Após
> reiniciar e criar usuário/senha no Ubuntu (janela que abre sozinha),
> execute `INSTALAR.bat` novamente para concluir.

#### Atalhos avançados (uso manual / manutenção)

Também são criados na Área de Trabalho:
- `SGC-Iniciar.bat` — força o início manual do servidor
- `SGC-Parar.bat` — encerra o servidor

### Opção 2 — Docker (servidor / desenvolvimento)

```bash
git clone https://github.com/yuuhgod/sgc-agmeal.git
cd sgc-agmeal
docker compose up -d --build
```

Depois acesse `http://localhost` e conclua a tela de **primeira configuração**.

### Opção 3 — Linux/WSL manual

```bash
cd sgc-agmeal
bash instalar.sh   # instala deps, gera scripts e atalhos
bash start.sh      # inicia o servidor
```

### Variáveis de Ambiente Suportadas
| Variável | Padrão | Descrição |
|---|---|---|
| `SECRET_KEY` | gerada em `data/.flask_secret` | Chave de sessão/CSRF. Defina uma fixa em produção. |
| `SESSION_COOKIE_SECURE` | `false` | Deixe `true` quando servir via HTTPS. |
| `GUNICORN_WORKERS` | `3` | Número de workers do Gunicorn. |
| `GUNICORN_TIMEOUT` | `60` | Timeout (s) por requisição. |
| `TZ` | `America/Maceio` | Fuso horário do contêiner. |
| `BACKUP_SYNC_DIR` | *(vazio)* | Caminho absoluto de uma pasta sincronizada pela nuvem; usado pelo menu **Backup** e por `scripts/backup_cli.py`. |
| `BACKUP_KEEP_LOCAL` | `14` | Quantidade de arquivos ZIP a manter em `data/backups/`. |
| `BACKUP_KEEP_SYNC` | `60` | Quantidade de ZIPs a manter na pasta `BACKUP_SYNC_DIR`. |
| `MAX_CONTENT_LENGTH_MB` | `128` | Limite máximo de upload (útil para restaurar ZIPs grandes). |

### `data/.flask_secret` (permissões)

O ficheiro `data/.flask_secret` guarda a chave de sessão quando não define `SECRET_KEY` no ambiente. Deve ser **legível e gravável só pelo utilizador que corre o Flask/Gunicorn** (modo recomendado `600`). Se for criado ou copiado como **root** (ex.: `docker compose` sem `user:`), o processo normal pode falhar a ler a chave ou a **restauração pela interface** pode não conseguir substituir o ficheiro — nesse caso ajuste dono e permissões (ex.: `sudo chown …:… data/.flask_secret && chmod 600 data/.flask_secret`) ou copie manualmente o `.flask_secret` do ZIP após parar o servidor.

### Backup (banco + fotos)

Administradores acessam **Backup do sistema** no menu do usuário. O ZIP inclui
`data/sgc.db` (via API de backup do SQLite), `data/.flask_secret` (se existir) e
todas as fotos em `app/static/uploads/fotos/`.

#### Google Drive / nuvem — **instale o cliente no computador**

Este projeto **não** integra com a API OAuth do Google. Tudo o que envolve
“backup na nuvem” pressupõe uma **pasta no disco** que um cliente oficial
sincronize para a conta (Google Drive, OneDrive, etc.).

- **No Windows:** instale o [Google Drive para computador](https://www.google.com/drive/download/)
  (ou o OneDrive que já vem no sistema), faça login e use a pasta **Google Drive**
  que aparece no Explorador de ficheiros. Sem esse programa, **não existe** pasta
  local a apontar em `BACKUP_SYNC_DIR` nem destino fiável para o
  `backup_diario_GoogleDrive.bat`.
- **No WSL / Linux:** aponte `BACKUP_SYNC_DIR` para o caminho montado dessa pasta
  (ex.: `/mnt/c/Users/Nome/Google Drive/SGC-Backups`). O servidor só grava
  ficheiros; a sincronização para a nuvem é feita pelo cliente no Windows.

Marque a opção na tela de backup ou use o agendamento abaixo.

#### Backup automático **uma vez por dia** no Google Drive (recomendado)

A ideia é: o script grava o ZIP **numa pasta do disco que o Google Drive já
sincroniza** — não há login OAuth nem token; o cliente oficial do Google faz o
resto (por isso o passo de instalar o **Google Drive para computador** acima é
obrigatório para este fluxo).

1. **Instale** [Google Drive para computador](https://www.google.com/drive/download/) e faça login com a conta da associação.
2. **Crie uma pasta** só para estes backups, por exemplo `SGC-AGMEAL-Backup`, dentro do Google Drive no Explorador de ficheiros (aparece como “Google Drive” no utilizador).
3. **Copie** o ficheiro [scripts/backup_diario_GoogleDrive.bat](scripts/backup_diario_GoogleDrive.bat) para o Ambiente de Trabalho (ou outro sítio fixo). Por defeito ele usa `%USERPROFILE%\Google Drive\SGC-AGMEAL-Backup` — ajuste a variável `GFOLDER` dentro do `.bat` se usar outro nome/caminho.
4. **Teste**: duplo clique no `.bat`. Deve criar a pasta se não existir, correr o backup no WSL e copiar o ZIP para lá; minutos depois o ficheiro deve aparecer na web do Google Drive.
5. **Agende no Windows** (mais fiável do que cron no WSL quando o PC dorme):
   - Abra o **Agendador de Tarefas** → *Criar Tarefa…* (não “Criar tarefa básica” se quiser mais controlo).
   - **Geral**: nome `SGC-AGMEAL Backup diário`; marque “Executar quer o utilizador tenha iniciado sessão ou não” se quiser correr mesmo sem janela aberta (opcional).
   - **Accionadores**: Novo → Diariamente → hora (ex.: 02:00).
   - **Acções**: Novo → *Iniciar um programa* → Programa: caminho completo para `backup_diario_GoogleDrive.bat`.
   - **Condições**: desmarque “Iniciar só se o computador estiver ligado à corrente elétrica” se for portátil e quiser backup na bateria.
   - **Definições**: marque “Executar tarefa o mais breve possível após uma inicialização agendada ter sido perdida” para recuperar um dia em que o PC esteve desligado à hora do backup.

O script `backup_diario_GoogleDrive.bat` define `BACKUP_SYNC_DIR` em caminho
Linux (via `wslpath`) e chama `scripts/backup_cli.py`, que também mantém cópias
em `data/backups/` e aplica `BACKUP_KEEP_SYNC` na pasta da nuvem (predefinição
**60** ficheiros — ajustável por variável de ambiente no sistema ou no próprio
`.bat` com `set BACKUP_KEEP_SYNC=30` antes da linha `wsl.exe`).

**Alternativa só em Linux/WSL** (se o computador ficar ligado nessa hora):

```bash
# Adicione ao crontab (crontab -e), ajustando o caminho da pasta sincronizada:
BACKUP_SYNC_DIR='/mnt/c/Users/SEU_USUARIO/Google Drive/SGC-AGMEAL-Backup'
0 2 * * * cd ~/sgc-agmeal && .venv/bin/python scripts/backup_cli.py >>/tmp/sgc-backup.log 2>&1
```

Ou exporte `BACKUP_SYNC_DIR` no `~/.profile` e use só a linha `cd ... && python ...` no cron.

Backup agendado (exemplo mínimo **sem** pasta na nuvem — só `data/backups/`):

```bash
0 2 * * * cd ~/sgc-agmeal && .venv/bin/python scripts/backup_cli.py >>/tmp/sgc-backup.log 2>&1
```

### Restauração

- **Pela interface (admin):** menu **Restaurar backup**. É obrigatório enviar um
  ZIP válido do próprio sistema, marcar a caixa de confirmação e digitar
  exatamente a palavra indicada na página (por defeito `RESTAURAR`). Antes de
  substituir dados, o servidor gera um **ZIP de segurança** em `data/backups/`.
  Após concluir, a sessão é encerrada e deve voltar a fazer login. Com **Gunicorn
  e vários workers** (ou várias instâncias), **reinicie o serviço** para todos os
  processos abrirem o novo `sgc.db`.
  A substituição de `data/.flask_secret` depende de permissões no disco; se falhar,
  veja a secção **`data/.flask_secret` (permissões)** acima.
- **Manual / outro servidor:** siga o `LEIA-ME.txt` dentro do ZIP (parar servidor,
  extrair ficheiros, permissões, arranque). Útil quando não há acesso à UI ou
  para recuperação em máquina limpa.

### Migrações de base de dados (Flask-Migrate)

O schema versionado vive em `app/migrations/`. Em instalações novas ou após
`git pull` com revisões novas, aplique as migrações **a partir da pasta `app/`**
(com o mesmo interpretador Python / venv do projeto):

```bash
cd sgc-agmeal/app
export FLASK_APP=main:app
# Ex.: ../.venv/bin/flask db upgrade
flask db upgrade
```

Em Docker, execute o mesmo comando no contentor com o código montado, ou
incorpore `flask db upgrade` no arranque da imagem se for a política da sua
equipa.

### Integração contínua (GitHub Actions)

O workflow [.github/workflows/ci.yml](.github/workflows/ci.yml) instala as
dependências de sistema do WeasyPrint, instala o `requirements.txt` e executa
`python3 test_app.py` a cada push ou pull request. **Não** é necessário token
pessoal (PAT): o GitHub fornece `GITHUB_TOKEN` automaticamente.

### Backup manual do arquivo único
O arquivo `data/sgc.db` pode ser copiado manualmente quando o servidor estiver
parado; o fluxo via interface é preferível porque inclui fotos e gera cópia
consistente do SQLite com o servidor em execução.

## Estrutura
```
app/
  main.py            Rotas e configuração do app
  database.py        Modelos SQLAlchemy
  restore_service.py Validação e aplicação de ZIP de restauração
  migrations/        Alembic (Flask-Migrate)
  templates/         Jinja2
  static/            CSS, JS e imagens (inclui uploads/fotos)
data/                Banco SQLite, backups ZIP, restore_pending/ e segredo de sessão
scripts/             backup_cli.py, backup_diario_GoogleDrive.bat
.github/workflows/ CI (testes)
dockerfile
docker-compose.yml
nginx.conf
```

## Próximos Passos Recomendados
- Habilitar HTTPS no Nginx (Let's Encrypt / Caddy).
- Expandir testes automatizados (por exemplo `pytest`) cobrindo CRUD e autenticação.
- Refatorar rotas em Blueprints conforme o projeto crescer.
