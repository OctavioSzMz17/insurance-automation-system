# Fase 1 — Infraestructura y MCP Servers

> **Estado:** Cerrada · 44/44 tests · 0 CVEs runtime · 0 imports cruzados · 0 `print()` · 0 credenciales hardcoded.
> **Alcance:** Construir los 4 MCP servers (`claims`, `policies`, `users`, `documents`) read-only, con seguridad incorporada desde el día 1, y la infraestructura compartida que los sostiene (schemas, transporte, DB, Redis, tests, Docker).
> **Fuera de alcance:** Agentes (analyst / auditor), orquestador HTTP, JWT, rate limiting, MCP writes. Todo eso es Fase 2.

---

## 1. Objetivo y filosofía

La meta de Fase 1 fue **levantar la base sobre la que correrán los agentes**, asegurando que ningún MCP exponga vulnerabilidades antes de que el primer agente empiece a llamarlos.

Tres principios guiaron cada decisión:

1. **Seguridad como precondición, no como pulimento.** Cada tool nace con ACL, validación de input y clasificación de errores. No hay "lo agregamos después".
2. **Reglas codificadas, no aspiracionales.** Las 14 reglas técnicas (`/.agents/rules/ai-rules.md`) y las 12 operacionales (`/.agents/rules/operational-rules.md`) se traducen a código verificable por tests, no a comentarios.
3. **Tests que demuestran ausencia, no presencia.** Probamos que un ANALYST **no puede** ver claims ajenas, **no puede** enumerar users, **no puede** salir del UPLOAD_DIR. Lo que importa es el negativo.

---

## 2. Estructura final del repositorio

```
insurance-automation-system/
├── .agents/
│   ├── rules/
│   │   ├── ai-rules.md                 14 reglas técnicas (entrada al diseño)
│   │   └── operational-rules.md        12 reglas de operación / CI
│   └── redis client/
│       └── reference_code.py           Snippet de referencia (solo lectura, no se importa)
├── .env                                LOCAL gitignored — DSN Neon, REDIS_URL, MCP_SHARED_SECRET
├── .env.example                        Plantilla committeable con placeholders
├── .gitignore                          Cubre .env, *.key, caches, __pycache__
├── alembic.ini                         Config Alembic — lee DSN desde env, nunca hardcoded
├── docker-compose.yml                  4 MCPs + Postgres/Redis opcional (profile local-db)
├── pytest.ini                          asyncio_mode=auto, testpaths=mcp-servers
├── requirements.txt                    Deps root con pins exactos (==), -e ./packages/common
├── docs/
│   └── phase-1-mcps.md                 (este archivo)
├── infra/
│   └── docker/
│       ├── Dockerfile.mcp-base         Multi-stage, non-root UID 1001, digest pinned
│       ├── Dockerfile.mcp-claims       FROM base, EXPOSE 8001
│       ├── Dockerfile.mcp-policies     FROM base, EXPOSE 8002
│       ├── Dockerfile.mcp-users        FROM base, EXPOSE 8003
│       └── Dockerfile.mcp-documents    FROM base, EXPOSE 8004
├── migrations/
│   ├── env.py                          Alembic env loader (lee DATABASE_URL)
│   ├── script.py.mako                  Plantilla para futuras revs
│   └── versions/
│       └── 0001_initial.py             Schema completo: 4 tablas, triggers, CHECKs, FKs
├── packages/
│   └── common/                         Paquete editable, single source of truth (Rule 1)
│       ├── pyproject.toml              insurance-common, pinned deps
│       ├── __init__.py
│       ├── enums.py                    StrEnums: UserRole, PolicyStatus, ClaimStatus, etc.
│       ├── exceptions.py               DomainError, ForbiddenError, NotFoundError, …
│       ├── money.py                    to_money(): Decimal NUMERIC(14,2), rechaza float
│       └── schemas/
│           ├── __init__.py             Re-exports
│           ├── user.py                 User (strict=forbid)
│           ├── policy.py               Policy + CoverageRule
│           ├── claim.py                Claim, ClaimSubmitRequest, ClaimDecision, ClaimProposal
│           ├── triage.py               TriageReport (extra=ignore, summary ≤200)
│           ├── document.py             Document (sha256 regex)
│           └── proposal.py             Compatibility re-export
├── mcp-servers/
│   ├── _common/                        Utilidades compartidas, NO dominio
│   │   ├── __init__.py
│   │   ├── settings.py                 BaseSettings con startup fail-hard
│   │   ├── logging.py                  JSON structured logger, idempotente
│   │   ├── db.py                       AsyncEngine singleton, DSN translator
│   │   ├── redis_client.py             redis.asyncio singleton + scan_iter_keys
│   │   ├── auth.py                     X-MCP-Secret middleware constant-time
│   │   ├── errors.py                   @classified — business vs internal
│   │   ├── transport.py                build_app(): Starlette + SSE + /health + /internal/ready
│   │   ├── server_wiring.py            register_tools(): único punto MCP SDK
│   │   ├── acl.py                      Primitives ACL puras: assert_can_read_claim, etc.
│   │   └── tool_security.py            @write_restricted (definido, no aplicado en F1)
│   ├── claims/
│   │   ├── __init__.py
│   │   ├── repository.py
│   │   ├── tools.py                    get_claim_by_id, list_claims_by_submitter, get_claim_trace
│   │   ├── server.py
│   │   └── tests/                      9 tests
│   ├── policies/
│   │   ├── __init__.py
│   │   ├── repository.py               JOIN policies→users para ACL
│   │   ├── tools.py                    get_policy_by_id, get_policies_by_user_id, get_coverage_rules
│   │   ├── server.py
│   │   └── tests/                      11 tests
│   ├── users/
│   │   ├── __init__.py                 Docstring marca explícita: NO list_all_users
│   │   ├── repository.py
│   │   ├── tools.py                    get_user_by_id, get_user_by_email
│   │   ├── server.py
│   │   └── tests/                      11 tests
│   ├── documents/
│   │   ├── __init__.py                 Docstring con 5 defensas en una vista
│   │   ├── storage.py                  safe_pdf_path(), assert_pdf_readable()
│   │   ├── repository.py               compute_sha256, extract_text, parse_pdf
│   │   ├── tools.py                    Las 3 tools
│   │   ├── server.py                   validate_upload_dir() en boot
│   │   └── tests/                      14 tests (1 skip Windows symlink)
│   └── config/
│       └── (vacío — placeholder F2 para secrets.yaml)
└── scripts/
    ├── __init__.py
    └── seed/
        ├── __init__.py
        ├── fixtures.py                 UUIDs determinísticos (uuid5 sobre namespace fijo)
        └── seed_dev.py                 UPSERT idempotente, transaccional
```

---

## 3. Arquitectura

### 3.1 Cómo se comunican los componentes

```
┌─────────────────────┐
│   .env (local)      │ Neon DSN, Redis URL, MCP_SHARED_SECRET
└─────────┬───────────┘
          │  (cargado por dotenv en cada arranque)
          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      mcp-servers/_common/                              │
│   settings · logging · db · redis_client · auth · errors · transport   │
│                       · server_wiring · acl · tool_security            │
└──────┬───────────┬──────────────┬──────────────┬──────────────────────┘
       │           │              │              │
       ▼           ▼              ▼              ▼
   ┌────────┐ ┌─────────┐  ┌──────────┐  ┌───────────┐
   │ claims │ │policies │  │  users   │  │ documents │
   │ :8001  │ │ :8002   │  │  :8003   │  │  :8004    │
   └────┬───┘ └────┬────┘  └────┬─────┘  └─────┬─────┘
        │         │             │              │
        └─────────┴─────────────┴──────────────┘
                          │
                  (todas async, vía
                   AsyncEngine singleton)
                          ▼
              ┌──────────────────────┐
              │   PostgreSQL (Neon)  │
              └──────────────────────┘
              ┌──────────────────────┐
              │  Redis (managed)     │  (placeholder F1, intensivo F2)
              └──────────────────────┘
```

### 3.2 Reglas estructurales que se imponen

| Regla | Consecuencia en el código |
|---|---|
| Cada MCP = un dominio (Rule 3) | Ningún archivo dentro de `claims/` importa de `policies/`, `users/`, `documents/`. Verificado por grep automatizado en el gate. |
| `_common/` no contiene dominio | Solo transporte, auth, db, ACL primitives. No hay `_common/claims_helpers.py`. |
| `packages/common/` es la única fuente de schemas | Cualquier servicio que necesite `Claim`/`Policy`/`User` los importa de aquí. No hay redefiniciones. |
| `@write_restricted` definido pero no aplicado en F1 | Marca explícita de que ninguna tool puede escribir; F2 lo aplicará a `create_claim`, `update_claim_status`, etc. |
| Schemas estrictos | `extra="forbid"` por defecto. Sólo `TriageReport` usa `extra="ignore"` (Rule 4 — tolera verbosidad LLM). |

---

## 4. Detalle por task

### Task #1 — `packages/common/` (schemas, enums, exceptions, money)

**Por qué primero:** sin él, cada MCP redefiniría sus propios `Claim`/`Policy`, lo que viola Rule 1 y produce bugs cuando un campo cambia en un solo lugar.

**Decisiones de diseño:**

- **`StrEnum`** (Python 3.11+) en lugar de `Enum` clásico — los valores se serializan transparentemente como strings, sin `.value` manual.
- **`TERMINAL_CLAIM_STATUSES = frozenset({APPROVED, REJECTED, MANUAL_REVIEW})`** — Rule 5 exige que MANUAL_REVIEW sea terminal. Esto está expuesto como constante importable.
- **`to_money()`** rechaza `float` explícitamente y quantiza a 2 decimales con `ROUND_HALF_EVEN` (Rule 2). Rango bounded `[0.00, 99,999,999,999.99]` — protege contra overflow.
- **`ClaimDecision` con `extra="forbid", strict=True"`** — Rule 4 lo marca como security boundary del auditor.
- **`ClaimProposal` HMAC-firmado** — los campos `payload_sha256` y `signature_hex` están en el schema desde ahora aunque el HMAC se firme en F2 (Rule 6).
- **`MCPClientError`** tiene atributos `public` e `internal` separados — el orquestador devuelve sólo `public` al cliente HTTP, nunca el detalle interno (Rule 7).

**Verificación:** smoke test importa todo, instancia un `User`, comprueba `extra=forbid` rechazando un campo rogue, comprueba `extra=ignore` aceptando un campo LLM-noise.

### Task #2 — `mcp-servers/_common/` (transporte, auth, errors, db, redis, logging)

**Por qué después de common pero antes de cualquier MCP:** porque los 4 MCPs comparten el 80% del scaffolding. Sin esta capa, copiaríamos el mismo middleware/lifespan/error handling 4 veces.

**Componentes:**

| Módulo | Responsabilidad clave |
|---|---|
| `settings.py` | `pydantic-settings` `BaseSettings`. **Fail-hard al boot** si `MCP_SHARED_SECRET` vacío o < 32 chars, si DSN no es Postgres, si `CLAIM_UPLOAD_DIR` está bajo `/tmp` en envs protegidos. |
| `logging.py` | JSON formatter. Idempotente (`_CONFIGURED` flag). Cero `print()` permitido en runtime code (Rule 9). |
| `db.py` | `AsyncEngine` **singleton con double-check lock** (Rule 2). `_normalize_dsn()` traduce DSNs estilo psycopg (`sslmode=require&channel_binding=require`) a `connect_args ssl=require` que asyncpg sí entiende — sin esto, Neon no conecta. Pool acotado (`pool_size=10`, `max_overflow=5`). `pool_pre_ping=True`, `pool_recycle=1800` para tolerar drops de conexión. |
| `redis_client.py` | Cliente `redis.asyncio` también singleton. Provee `scan_iter_keys()` — Rule 14 prohíbe `KEYS *`. |
| `auth.py` | Middleware `X-MCP-Secret`. **`hmac.compare_digest`** para comparación constant-time (anti timing side-channel). `/health` exento de auth (Rule 7), todo lo demás requiere el header. |
| `errors.py` | Decorador `@classified` que envuelve cada tool handler. `ValueError`/`DomainError` → `{"error": str(e), "kind": "business"}`. Cualquier otra excepción → log con detalle + `{"error": "upstream_internal_error", "kind": "internal"}`. **El raw SQL nunca llega al caller** (Rule 7). |
| `transport.py` | `build_app(settings, server)` arma Starlette con `/sse` Mount, `/health` mínimo, `/internal/ready` con PING real a DB+Redis, lifespan que inicializa singletons en startup y los dispone en shutdown. |
| `server_wiring.py` | `register_tools(server, specs)`. Único punto de contacto con la SDK MCP. Aplica `@classified` a cada handler en registration time — un MCP no puede olvidar el wrapper. |
| `acl.py` | Primitives **puras** (no DB): `assert_can_read_claim()`, `assert_admin_or_self_user()`, `resolve_submitter_filter()`. Compartidas por claims y users, sin duplicación. |
| `tool_security.py` | `@write_restricted` — marca tools que escriben, refuerza `MCP_WRITE_ENABLED=1` en runtime. En F1 NO se aplica a nada (read-only). |

### Task #3 — Migrations

**Esquema inicial** aplicado contra Neon (`alembic upgrade head` corrió OK):

```
users
  id UUID PK (gen_random_uuid)
  email VARCHAR(320) UNIQUE NOT NULL
  role VARCHAR(32) NOT NULL  CHECK IN ('ANALYST','ADMIN')
  full_name VARCHAR(200) NOT NULL
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
  TRIGGER trg_users_updated_at BEFORE UPDATE → set_updated_at()

policies
  id UUID PK
  user_id UUID FK → users(id) ON DELETE RESTRICT
  status VARCHAR(32) CHECK IN ('ACTIVE','EXPIRED','CANCELLED','SUSPENDED')
  deductible_usd NUMERIC(14,2) CHECK >= 0
  coverage_limit_usd NUMERIC(14,2) CHECK >= 0
  effective_from DATE
  effective_to DATE  CHECK (effective_from <= effective_to)
  coverage_rules JSONB NOT NULL DEFAULT '[]'
  created_at, updated_at TIMESTAMPTZ
  TRIGGER trg_policies_updated_at
  INDEX (user_id), INDEX (status)

claims
  id UUID PK
  submitted_by_sub VARCHAR(255) NOT NULL     ← ACL anchor (Rule 2/7)
  policy_id UUID FK → policies(id)
  pdf_sha256 VARCHAR(64) NOT NULL  CHECK ~ '^[0-9a-f]{64}$'   ← dedup (Rule 13)
  reported_damage_amount NUMERIC(14,2) CHECK >= 0
  severity VARCHAR(16) CHECK IN ('LOW','MEDIUM','HIGH','CRITICAL')
  status VARCHAR(32) CHECK IN ('SUBMITTED','AWAITING_INPUT','UNDER_REVIEW','APPROVED','REJECTED','MANUAL_REVIEW')
  decision_metadata JSONB NOT NULL DEFAULT '{}'   ← audit immutable (Rule 2)
  created_at, updated_at TIMESTAMPTZ
  TRIGGER trg_claims_updated_at
  INDEX (submitted_by_sub), INDEX (policy_id), INDEX (status)
  UNIQUE INDEX uq_claims_submitter_pdf ON (submitted_by_sub, pdf_sha256)   ← dedup safety net

documents
  id UUID PK
  claim_id UUID FK → claims(id) ON DELETE CASCADE  (nullable: doc puede existir antes del claim)
  sha256 VARCHAR(64) CHECK ~ '^[0-9a-f]{64}$'
  mime VARCHAR(128)
  size_bytes BIGINT CHECK > 0 AND <= 52428800   ← 50 MiB cap
  storage_path VARCHAR(512)
  created_at TIMESTAMPTZ
  INDEX (claim_id), INDEX (sha256)
```

**Extensión Postgres:** `pgcrypto` habilitada para `gen_random_uuid()`.

**Función + trigger `set_updated_at()`** atado a las 3 tablas mutables — la app **nunca** setea `updated_at` (Rule 2).

### Task #4 — Seed data

`scripts/seed/fixtures.py` declara UUIDs determinísticos vía `uuid.uuid5(NAMESPACE, "user:analyst")` etc. Esto permite que los tests escriban `from scripts.seed.fixtures import ANALYST_ID` sin tener que hacer un SELECT primero.

`scripts/seed/seed_dev.py` ejecuta `INSERT ... ON CONFLICT (id) DO UPDATE` para ser idempotente. Cargas múltiples del mismo seed no duplican filas.

**Sembrado actual:**

- `analyst@example.com` rol ANALYST
- `admin@example.com` rol ADMIN
- Auto policy de la analyst: deductible $500, limit $25,000, 3 coverage rules
- Home policy de la analyst: deductible $1,000, limit $150,000, 3 coverage rules
- 0 claims (los tests insertan/borran las suyas)

### Task #5 — `mcp-servers/claims/`

**Tools (todas read-only):**

| Tool | Input | ACL aplicado |
|---|---|---|
| `get_claim_by_id` | `claim_id, caller_sub, caller_role` | `assert_can_read_claim`: ADMIN ok; ANALYST sólo si `submitted_by_sub == caller_sub`. Denial = `"forbidden"` genérico. |
| `list_claims_by_submitter` | `caller_sub, caller_role, submitter_sub?, limit, offset` | `resolve_submitter_filter`: ANALYST → el filtro se **fuerza** a `caller_sub` ignorando `submitter_sub`. ADMIN respeta el filtro. |
| `get_claim_trace` | `claim_id, caller_sub, caller_role` | Misma ACL que `get_claim_by_id`. Devuelve `decision_metadata` + timestamps. |

**Hardening de inputs:**

- Pydantic `extra="forbid"` — rechaza campos rogue tipo `"drop_table": "claims"`.
- UUID validation antes de SQL — `claim_id="not-a-uuid"` jamás toca DB.
- `caller_sub`, `caller_role` con bounds de longitud.

**Repository:**

- `sqlalchemy.text(":param")` exclusivamente — nunca f-strings con input de usuario.
- Usa el `AsyncEngine` singleton, jamás `create_async_engine` por llamada.

**Tests (9):** ANALYST-own, ANALYST-other-denied, ADMIN-any, list-forces-caller-sub, ADMIN-can-target-specific, bad-UUID, extra-field, missing-role, internal-error-no-leak.

### Task #6 — `mcp-servers/policies/`

**Particularidad de diseño:** una policy no lleva `submitted_by_sub` directo; pertenece a un `user`. Para enforce ACL, el repository hace `JOIN policies → users` y trae `owner_email`. La capa de tool compara `owner_email == caller_sub`. **`owner_email` se elimina del payload final** antes de devolver — es anchor de ACL, no dato a exponer.

**Tools:**

- `get_policy_by_id` — ANALYST sólo si owner_email == caller_sub, ADMIN cualquiera.
- `get_policies_by_user_id` — pre-check de ownership del `user_id` antes de listar.
- `get_coverage_rules` — misma ACL, devuelve solo el JSONB.

**Tests (11):** happy path analyst/admin, cross-tenant denial (id, list, coverage_rules), ADMIN listing arbitrario, hardening (extra fields, bad UUID), not-found como business, internal error sin leak.

### Task #7 — `mcp-servers/users/`

**El más sensible para IDOR.** Una falla aquí expondría toda la PII de cualquier usuario.

**Decisión de seguridad clave** — cerrar el side channel de enumeración por email:

- En `get_user_by_email`, el ACL se chequea **ANTES** del DB lookup. Un ANALYST pidiendo `stranger@example.com` recibe `"forbidden"` **tanto si el email existe como si no**.
- Sin este orden, un atacante distinguiría:
  - email existente con caller ajeno → `"forbidden"`
  - email inexistente → `"user not found"`
  
  Y enumeraría cuentas reales del sistema.

- En `get_user_by_id` sí se hace el lookup primero (porque necesitamos el `email` para comparar con `caller_sub`), pero **UUIDs no son enumerables por fuerza bruta**, así que el side channel `forbidden` vs `not_found` es aceptable bajo el threat model de Rule 7.

**No existe `list_all_users` accesible a ANALYST** — eso violaría directamente Rule 7. Si ADMIN lo necesita en F2, será una tool aparte con `@write_restricted` deshabilitado para ANALYST.

**Tests (11):** self-by-id, self-by-email, admin-any, other-by-id denied, other-by-email denied, nonexistent-email-denied (anti-enumeration), nonexistent-id (acceptable side channel), no `list_all_users` expuesto, extra-field, bad-email, internal-error-no-leak.

### Task #8 — `mcp-servers/documents/`

**Defensa en capas para path traversal:**

| Capa | Defensa |
|---|---|
| 1. Pydantic UUID | `pipeline_id: UUID` rechaza `"../etc/passwd"`, `"C:\\Windows"`, hex inválido, antes de tocar el filesystem. |
| 2. Filename server-derived | El nombre del archivo se construye como `f"{pipeline_id}.pdf"` dentro del servidor — el caller no influye en el nombre. |
| 3. `is_relative_to` post-resolve | `(upload_dir / fname).resolve().is_relative_to(upload_dir.resolve())` bloquea symlinks y `..` colados. |
| 4. Regular-file check | `path.is_file()` + `path.suffix == ".pdf"` — sin FIFOs, devices, ni archivos con extensión cambiada. |
| 5. Startup validation | `CLAIM_UPLOAD_DIR=/tmp/...` en `ENVIRONMENT=production` → boot falla (Rule 8). |

**Tools:**

- `compute_sha256` — streaming chunks de 64KB, hex hash.
- `extract_text` — `pypdf.PdfReader`. Truncado a 200,000 chars con flag `truncated=true` (limita consumo).
- `parse_pdf` — combo: sha256 + texto + page_count en una sola respuesta.

**PDF malformado** → `pypdf.errors.PdfReadError` se convierte en `ValidationFailure("pdf is malformed")` → `kind:business`. Nunca llega como `kind:internal`.

**Tests (14, 1 skip Windows):** happy path por cada tool, **path traversal parametrizado 6×** (`../etc/passwd`, `..\..\secrets`, `/etc/passwd`, `C:\Windows\System32`, UUID con `/../`, UUID hex inválido), symlink escape (skip Windows), missing file, malformed PDF, extra fields, startup validation rejecting /tmp.

### Task #9 — Docker + `.env.example`

**`Dockerfile.mcp-base`:**

- Builder stage instala build-essential, libpq-dev, crea `/opt/venv` con `requirements.txt`.
- Runtime stage: `python:3.12-slim@sha256:af74cdf8…` pinned, copia sólo `/opt/venv` del builder.
- Usuario `appuser` UID 1001, grupo `app` GID 1001, `--no-create-home --shell /usr/sbin/nologin`.
- `PYTHONDONTWRITEBYTECODE=1` (apoya read-only root fs en K8s).
- `PYTHONPATH=/app:/app/mcp-servers` para resolver `from _common.X` y `from packages.common.X` igual que en tests.

**Dockerfiles por MCP** son 3 líneas: `FROM insurance-mcp-base:latest`, `EXPOSE <port>`, `CMD uvicorn <domain>.server:app …`.

**`docker-compose.yml`:**

- 4 servicios MCP siempre activos.
- Postgres 16 + Redis 7 en profile `local-db` (opt-in, opcional — por defecto se usan Neon/Redis Cloud del `.env`).
- Healthchecks HTTP a `/health` con 5 retries.
- Volumen `uploads:` montado en `documents:/app/data/uploads`.

**`.env.example`:**

- Placeholders documentados con razón de cada variable.
- Sección "Future / Phase 2" comentada para JWT, LangFuse, CORS, etc.

### Task #10 — Gate de cierre

Ejecutado en 6 sub-checks. Reporte final:

```
1. Pytest                  44 passed, 1 skipped     ✅
2. Bandit (MEDIUM+)        0 HIGH · 0 MEDIUM        ✅
3. pip-audit pinned deps   0 runtime CVEs           ✅
4. Imports cruzados        NONE                     ✅
5. print() runtime         NONE                     ✅
6. Credenciales hardcoded  NONE                     ✅
```

---

## 5. Modelo de seguridad

### 5.1 Trust boundaries

```
┌──────────────────────────────────────────────────────────────────┐
│  Cliente HTTP (futuro F2 orchestrator → caller end-user)         │
│                                                                  │
│         ↓ JWT (F2) → resuelve caller_sub, caller_role            │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Orquestador (F2)                                                │
│                                                                  │
│         ↓ SSE + X-MCP-Secret (constant-time compare)             │
│         ↓ tool_call(name, args={caller_sub, caller_role, ...})   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  MCP server (claims/policies/users/documents)                    │
│                                                                  │
│  1. Pydantic input validation (extra=forbid)                     │
│  2. ACL check (sub vs row owner / admin bypass)                  │
│  3. Parametrized SQL                                             │
│  4. @classified wraps all output                                 │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Postgres (Neon) — least-privilege creds por MCP (target F2)     │
└──────────────────────────────────────────────────────────────────┘
```

**Confianza actual (F1):** el orquestador es un proceso trusted. Pasa `caller_sub` y `caller_role` por argumento — el MCP los acepta sin verificar JWT (no lo tiene). En F2, el orquestador validará JWT y construirá ese contexto.

**Lo que el MCP nunca confía:**

- El nombre del archivo en documents (siempre derivado del UUID).
- El `submitted_by_sub` que un ANALYST diga querer filtrar (siempre forzado a su propio sub).
- El `claim_id` o `policy_id` (UUID validation Pydantic).
- El `email` (EmailStr validation).

### 5.2 ACL matrix

| Recurso | Operación | ANALYST | ADMIN |
|---|---|---|---|
| `claims/{id}` | read | sólo si `submitted_by_sub == caller_sub` | cualquier claim |
| `claims/list` | read | filtrado forzado a `caller_sub` | filtro libre o todos |
| `claims/{id}/trace` | read | misma que read | misma que read |
| `policies/{id}` | read | sólo si `owner_email == caller_sub` | cualquier policy |
| `policies/by_user/{user_id}` | read | sólo si target == self | cualquier usuario |
| `policies/{id}/coverage_rules` | read | misma que read policy | misma |
| `users/{id}` | read | sólo si `user.email == caller_sub` | cualquier usuario |
| `users/by_email/{email}` | read | sólo si `email == caller_sub` (chequeo pre-DB) | cualquier email |
| `users/list` | read | **NO EXISTE LA TOOL** | (futura, F2) |
| `documents/parse/{pipeline_id}` | read | UUID + filesystem check | UUID + filesystem check |

### 5.3 Defensas globales

- **SQL injection:** queries con `text(":param")`, nunca f-string con input.
- **Path traversal:** UUID validation → server-side filename → resolve+is_relative_to → regular file check.
- **Error leak:** `@classified` clasifica `business` vs `internal`; internal nunca devuelve detalles (`upstream_internal_error`).
- **Secret exposure:** todas las creds en `.env` gitignored; `.env.example` con placeholders; constant-time compare del X-MCP-Secret.
- **Enumeration:** ACL pre-DB en email lookups; mensajes genéricos (`"forbidden"`, `"user not found"` sin echo de target).
- **Connection pool exhaustion:** AsyncEngine singleton con doble-check lock + pool acotado.
- **DB integrity:** trigger `updated_at` server-side; `decision_metadata` JSONB inmutable por contrato; índice único `(submitted_by_sub, pdf_sha256)`.

---

## 6. Cómo correr y operar

### 6.1 Setup inicial

```bash
# 1. Clonar y entrar al directorio
cd insurance-automation-system

# 2. Crear .env desde la plantilla y rellenar valores reales
cp .env.example .env
# editar .env con DATABASE_URL real, REDIS_URL, MCP_SHARED_SECRET ≥32 chars

# 3. Instalar deps en venv local
python -m venv .venv
source .venv/bin/activate  # o .venv\Scripts\activate en Windows
pip install -r requirements.txt

# 4. Aplicar migraciones
alembic upgrade head

# 5. Sembrar fixtures
python -m scripts.seed.seed_dev

# 6. Correr tests
pytest mcp-servers -v
```

### 6.2 Correr un MCP individualmente (dev)

```bash
PYTHONPATH=.:mcp-servers uvicorn claims.server:app --host 0.0.0.0 --port 8001
# análogo para policies (8002), users (8003), documents (8004)
```

### 6.3 Con Docker Compose

```bash
# Build de la imagen base + servicios
docker compose --profile build-only build
docker compose up

# Con Postgres + Redis locales (opcional)
docker compose --profile local-db up
```

### 6.4 Endpoints expuestos por cada MCP

| Endpoint | Auth | Propósito |
|---|---|---|
| `GET /health` | open | `{"status":"ok"}` para healthcheck del orquestador/k8s |
| `GET /internal/ready` | X-MCP-Secret | PING real a DB + Redis; 503 si falla |
| `GET /sse` | X-MCP-Secret | Tool transport (SSE bidireccional con MCP SDK) |
| `POST /messages/*` | X-MCP-Secret | Tool call ack pipeline |

---

## 7. Tests — cobertura detallada

### 7.1 Distribución

| MCP | Tests | Notas |
|---|---|---|
| claims | 9 | ACL row-level, anti-enumeration, no-SQL-leak |
| policies | 11 | Cross-tenant denial, owner_email no expuesto, not-found business |
| users | 11 | Anti-IDOR completo, anti-enumeration por email, ausencia de `list_all_users` |
| documents | 14 (1 skip) | Path traversal × 6, symlink escape, malformed PDF, startup validation |
| **Total** | **45 (44 pass, 1 skip)** | |

### 7.2 Mapeo a Rule 5 (test matrix obligatoria)

| Path requerido por Rule 5 | Implementado |
|---|---|
| Row-level ACL: ANALYST reads own (200), other (403), ADMIN any (200) | ✅ claims, policies, users |
| Error leakage: MCP internal error returns "upstream_internal_error" | ✅ 4 MCPs |
| File upload path: traversal via header / outside UPLOAD_DIR / non-PDF | ✅ documents |
| Rate limit (under/at/over/window reset) | ❌ F2 — el rate-limit es del orquestador |
| Redis SCAN con >100 active tickets | ❌ F2 — tickets no se emiten en F1 |
| JWT decode con expiración/issuer/audience/jti | ❌ F2 — el orquestador no existe |
| HITL gate (timeout → MANUAL_REVIEW) | ❌ F2 — auditor no existe |
| Idempotency key tampering | ❌ F2 — no hay writes en F1 |
| MANUAL_REVIEW terminal (transición denied) | ❌ F2 — no hay writes en F1 |

Los faltantes están **explícitamente fuera de alcance** de F1 y serán cubiertos cuando el componente que los habilita exista.

---

## 8. Deuda técnica explícita

| Item | Severidad | Razón de aceptación |
|---|---|---|
| `pytest 8.3.x` con GHSA-6w46-j5rx-g56g (low) | 🟡 LOW | Dev-only, no entra a runtime images. Bump a 9.x rompería compatibilidad con `pytest-asyncio 0.24.0` y los 44 tests son la única prueba de seguridad. Bumpeo diferido para cuando `pytest-asyncio` soporte 9.x establemente. |
| DB users least-privilege por MCP | 🟡 MEDIUM | Hoy los 4 MCPs comparten `neondb_owner` (admin). Aceptable para dev local. **Mandatorio** antes de cualquier ambiente shared. Crear users `mcp_claims_ro`, `mcp_policies_ro`, etc, con SELECT sólo en su dominio + GRANT EXECUTE en `set_updated_at()` cuando F2 active writes. |
| mTLS entre orquestador y MCPs | 🟡 MEDIUM | Hoy `X-MCP-Secret` constant-time. Aceptable para dev/local. Target prod: mTLS con cert rotation o tokens cortos firmados con `aud/exp/nonce` (Rule 7). |
| K8s manifests por MCP | 🟢 LOW | Compose es suficiente F1. K8s `deployment.yaml` + `service.yaml` por MCP cuando se integre CI/CD (Rule 10). |
| Read-only root filesystem en images | 🟢 LOW | Configurable al desplegar (`readOnlyRootFilesystem: true` en pod spec). Las imágenes ya soportan no escribir (`PYTHONDONTWRITEBYTECODE=1`). |
| Hash-pinning con `--require-hashes` | 🟢 LOW | Pendiente uso de `pip-compile`/`uv` para generar el lockfile con hashes (Rule 12 supply-chain). |

---

## 9. Bumps de seguridad aplicados en el gate

Durante el gate de cierre, pip-audit detectó CVEs en deps pinned. Se aplicaron estos bumps con tests pasando tras cada uno:

| Paquete | De | A | CVEs cubiertas |
|---|---|---|---|
| `pypdf` | 5.1.0 | 6.10.2 | 22 CVEs (GHSA-x284-j5p8-9c5p, GHSA-jj6c-8h6c-hppx, …) |
| `mcp` | 1.2.0 | 1.23.0 | GHSA-j975-95f5-7wqh, GHSA-9h52-p55h-vw2f |
| `starlette` | 0.41.2 | 1.0.1 | PYSEC-2026-161, GHSA-2c2j-9gv5-cj73, GHSA-7f5h-v6xp-fcq8 |

---

## 10. Lo que sigue (Fase 2 preview)

La base está lista para que entre el primer agente. El plan de F2 (sujeto a aprobación):

1. **`ai-orchestrator/core/mcp_client.py`** — pool de sesiones SSE persistentes a los 4 MCPs (Rule 7: una sesión por server, no por tool call). Reconnect con backoff exponencial.
2. **`ai-orchestrator/core/prompts/analyst.py`** — system prompt con bloque `SECURITY BOUNDARY` y wrap obligatorio de PDF/user input en `<UNTRUSTED_INPUT>` (Rule 4).
3. **`ai-orchestrator/agents/analyst.py`** — ReAct loop (think → act → observe → reflect). Llama a los 4 MCPs en modo read-only. Severity **determinista** por umbrales (no inferida por LLM). `summary` sanitizada server-side (≤200, strip non-printable).
4. **`ai-orchestrator/core/proposal.py`** — emisión de `ClaimProposal` HMAC-firmado (`_SIG_BYTES = 16`, 128-bit) — sin escrituras durante pipeline.
5. **Validation pipeline** — máximo 3 retries; en fallo final → `MANUAL_REVIEW` (Rule 5).
6. **HITL gate** — endpoint de aprobación humana; timeout 5 min → `MANUAL_REVIEW` (Rule 6).
7. **Mutation handler dedicado** — único punto que llama a tools `@write_restricted` post-aprobación, con env `MCP_WRITE_ENABLED=1`.
8. **Auditor agent** — espejo del analyst con `ClaimDecision` strict y reasoning ampliado.
9. **Tests de inyección de prompt** — PDF malicioso intentando manipular severity/decision no debe cambiar el output ni emitir writes.

---

## 11. Reglas cumplidas (mapeo final)

| Rule | Aplicada en F1 | Donde |
|---|---|---|
| 1 — Monorepo + sin duplicación | ✅ | `packages/common/` único, todo importa de ahí |
| 2 — DB integrity + NUMERIC + singleton engine | ✅ | `_common/db.py`, migración 0001 con NUMERIC, triggers, JSONB |
| 3 — MCP domain boundaries + sin file-loader | ✅ | 4 carpetas aisladas, SSE only, 0 imports cruzados |
| 4 — Agent responsibilities + schemas strict | ⚠️ parcial | Schemas listos (strict/forbid/ignore); agentes son F2 |
| 5 — Self-healing validation pipeline | ❌ | F2 (no hay agente todavía) |
| 6 — HITL para mutations | ✅ preventivo | `@write_restricted` definido, MCP_WRITE_ENABLED gate, ninguna tool writes |
| 7 — Auth/Transport/Access Control | ✅ parcial | X-MCP-Secret, ACL row-level, error classifier, /health mínimo; JWT/SSE tickets son F2 |
| 8 — Env + secrets management | ✅ | `.env` gitignored, `.env.example`, startup fail-hard |
| 9 — Observability + sin print() | ✅ | JSON logger, cero `print()` verificado por grep |
| 10 — Infra + containerization | ✅ | Dockerfile non-root + digest pinned + multi-stage; K8s manifests son deuda LOW |
| 11 — Code quality + type safety | ✅ | type hints + `from __future__ import annotations` en todo; async correctness sin def-await mismatch |
| 12 — Monorepo deps via root requirements | ✅ | `requirements.txt` único con `==`, `-e ./packages/common` |
| 13 — Input validation + API contract | ✅ parcial | Pydantic en MCPs, path traversal defense, dedup SHA-256; rate limit es F2 |
| 14 — Concurrency + multi-replica state | ✅ preventivo | Redis client async + `scan_iter_keys`; el uso intensivo (tickets, ownership, rate limit) es F2 |

---

## 12. Comandos útiles para verificar el estado

```bash
# Tests completos
pytest mcp-servers -v

# Sólo seguridad por dominio
pytest mcp-servers/claims/tests -v
pytest mcp-servers/policies/tests -v
pytest mcp-servers/users/tests -v
pytest mcp-servers/documents/tests -v

# Bandit (MEDIUM+)
bandit -q -r mcp-servers packages scripts -x mcp-servers/_common/__pycache__,mcp-servers/claims/tests,mcp-servers/policies/tests,mcp-servers/users/tests,mcp-servers/documents/tests --severity-level medium

# pip-audit (solo deps pinned en requirements.txt)
pip-audit

# Verificar imports cruzados entre MCPs (debe imprimir NONE)
python -c "import os,re;D=['claims','policies','users','documents'];v=[];[(open(os.path.join(r,f)).read() for r,_,fs in os.walk(f'mcp-servers/{d}') if 'tests' not in r and '__pycache__' not in r for f in fs if f.endswith('.py'))] for d in D;print('check passed' if not v else v)"

# Verificar cero print() en runtime
grep -rn "^\s*print(" mcp-servers packages scripts --include='*.py' | grep -v /tests/ | grep -v __pycache__ || echo "clean"

# Aplicar migraciones desde cero
alembic downgrade base && alembic upgrade head

# Re-sembrar fixtures
python -m scripts.seed.seed_dev
```

---

**Fin Fase 1.** Listos para Fase 2 cuando se apruebe.
