# Nosso Dindin

App de controle financeiro doméstico em Flask + PostgreSQL, pronto para deploy no Railway.

## Recursos

- **Login multiusuário** com gerenciamento via painel admin (foto, email, senha)
- **Rendas** por usuário (categorias, recorrência, observações)
- **Gastos** com três modos de compartilhamento:
  - **Solo** — o pagador absorve a despesa
  - **Repasse integral** — um paga no cartão, o outro fica devendo o total (ex: corte de cabelo)
  - **Dividido** — valor ou percentual personalizado por pessoa, com botão de divisão igualitária e validação de soma
- **Painel de saldos** entre usuários: mostra a receber e a pagar consolidado por par
- **Projetos de meta** com:
  - Barra de progresso visual
  - Aportes manuais (com data e nota)
  - **Aportes automáticos mensais** — cotas por membro lançadas todo dia X via APScheduler (idempotente)
  - Histórico de contribuições por usuário
  - Prazo opcional
  - Projetos compartilhados com múltiplos membros
- **Marcação automática de meta atingida**

## Stack

- Flask 3 + SQLAlchemy + Flask-Login + Flask-Migrate
- PostgreSQL (SQLite em dev)
- Gunicorn + APScheduler
- Pillow para processamento de fotos de perfil
- CSS puro com tipografia Fraunces + Manrope

## Deploy no Railway (passo a passo)

### 1. Suba o código ao GitHub

```bash
git init
git add .
git commit -m "Domus Finanças - versão inicial"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/domus-financas.git
git push -u origin main
```

### 2. Crie o projeto no Railway

1. Acesse [railway.app](https://railway.app) e crie um **New Project**
2. Escolha **Deploy from GitHub repo** e selecione o repositório
3. Clique em **+ New → Database → Add PostgreSQL**

A variável `DATABASE_URL` será injetada automaticamente.

### 3. Configure as variáveis de ambiente

Na aba **Variables** do serviço web (não do banco), adicione:

| Variável | Valor | Obrigatório |
|---|---|---|
| `SECRET_KEY` | string longa e aleatória (use `python -c "import secrets; print(secrets.token_hex(32))"`) | sim |
| `ADMIN_USERNAME` | nome de login do admin inicial | sim |
| `ADMIN_PASSWORD` | senha inicial do admin (você troca depois pelo app) | sim |

A `DATABASE_URL` vem do plugin do Postgres automaticamente.

### 4. Deploy

O Railway detecta `Procfile`, `requirements.txt` e `runtime.txt` automaticamente e faz o deploy via Nixpacks.

No primeiro boot, o `wsgi.py` cria as tabelas e o usuário admin. **Faça login** com as credenciais que você configurou e cadastre os demais usuários da casa pelo painel **Administração → Usuários**.

### 5. Domínio

Em **Settings → Networking**, clique em **Generate Domain** para obter uma URL pública (`*.up.railway.app`). Opcionalmente, configure um domínio custom.

## Rodando localmente

```bash
# Crie um virtualenv
python -m venv venv
source venv/bin/activate  # Linux/Mac
# .\venv\Scripts\activate  # Windows

pip install -r requirements.txt

# Configure variáveis
cp .env.example .env
# Edite .env conforme necessário

# Rode
python wsgi.py
```

Acesse [http://localhost:5000](http://localhost:5000) e faça login com as credenciais do `.env`.

Sem `DATABASE_URL` definido, usa SQLite em `instance/finapp.db`.

## Estrutura

```
finapp/
├── wsgi.py                     # entry point + bootstrap admin
├── Procfile                    # gunicorn para Railway
├── requirements.txt
├── runtime.txt                 # Python 3.11
├── .env.example
└── app/
    ├── __init__.py             # factory Flask
    ├── models.py               # User, Income, Expense, ExpenseShare,
    │                           # Project, ProjectMember, Contribution, AutoTransfer
    ├── utils.py                # cálculos de saldo, BRL, upload foto
    ├── scheduler.py            # APScheduler — aportes automáticos
    ├── routes/                 # blueprints (auth, admin, dashboard, income, expenses, projects)
    ├── static/                 # CSS + assets
    └── templates/              # Jinja2
```

## Modelo de dados

- **User** — usuários com foto, admin/comum
- **Income** — rendas do usuário (com flag recorrente)
- **Expense** — gasto com pagador e modo (`solo` | `integral` | `split`)
- **ExpenseShare** — quem deve quanto de cada gasto (1 linha solo, 1 linha integral, N linhas split)
- **Project** — meta com valor, prazo, aporte automático
- **ProjectMember** — quem participa e cota mensal automática
- **Contribution** — aportes (manuais ou automáticos)
- **AutoTransfer** — controle de idempotência dos aportes automáticos por mês

## Segurança

- Senhas com hash via Werkzeug (PBKDF2)
- CSRF via cookies de sessão (Flask-Login)
- Upload de fotos validado e redimensionado (256×256) com Pillow
- Limite de upload de 5MB
- Permissões: pagador/dono pode editar seu próprio gasto/projeto; admin tem acesso total

## Aportes automáticos

O APScheduler roda **todo dia às 03:00 (horário de Brasília)** e processa projetos cujo `auto_day` é o dia atual. Para cada projeto:

1. Verifica se já houve aporte automático naquele mês (tabela `AutoTransfer`) — se sim, pula
2. Para cada membro com `monthly_share > 0`, cria uma `Contribution` marcada como `is_auto=True`
3. Registra o `AutoTransfer` (ano + mês)

Se o servidor reiniciar, não há risco de duplicação.

## Trocando senha do admin

Faça login com admin → menu **Usuários** → **Editar** → defina nova senha.

## Licença

Uso privado/doméstico.
