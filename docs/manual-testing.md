# Manual testing — end-to-end del pipeline desde terminal

Esta guía te lleva del PDF en disco al claim persistido en Neon, **a mano**, usando
4 terminales para los MCPs + 1 para el orquestador + 1 para `curl`. Pensada para
Windows + PowerShell, con notas para bash/git-bash al final.

---

## 0. Pre-requisitos

### 0.1 `.env` configurado

Asegúrate que tu `.env` tenga (gitignored, ya existe):

| Variable | Valor para esta prueba |
|---|---|
| `DATABASE_URL` | DSN de Neon (ya está) |
| `REDIS_URL` | Redis Cloud (ya está) |
| `MCP_SHARED_SECRET` | ≥ 32 chars (ya está) |
| `HMAC_PROPOSAL_KEY` | ≥ 32 chars y **distinto** del anterior (ya está) |
| `CLAIM_UPLOAD_DIR` | `./data/uploads` o donde quieras |
| `OPENAI_API_KEY` | **TU API KEY REAL** — sustituye el placeholder |
| `MEDIUM_MODEL` | `openai:gpt-4o-mini` (rápido y barato) o el que prefieras |
| `ANALYST_MODEL_TIER` | `medium` |
| `MCP_*_URL` | `http://localhost:80{01,02,03,04}` (defaults OK) |

> 💡 Para esta prueba, cambia `MEDIUM_MODEL` a `openai:gpt-4o-mini` y rellena `OPENAI_API_KEY`. Deja `ANTHROPIC_API_KEY` vacío o como placeholder — no se usa si el tier activo no es Anthropic.

### 0.2 Migraciones aplicadas + seed cargado

```powershell
# Una vez por base de datos
python -m alembic upgrade head
python -m scripts.seed.seed_dev
```

Esto crea las 4 tablas y siembra `analyst@example.com` (ANALYST) + `admin@example.com` (ADMIN) + 2 policies activas.

---

## 1. Prepara tu PDF

```powershell
python scripts\prepare_test_pdf.py "C:\ruta\a\tu-claim.pdf"
```

Verás algo como:

```
======================================================================
 PDF READY FOR TESTING
======================================================================
  source       : C:\ruta\a\tu-claim.pdf
  copied to    : C:\Users\octav\insurance-automation-system\data\uploads\3f9b1d5e-1234-...-89ab.pdf
  size         : 47.3 KB
  sha256       : a8f3...d1b4

  pipeline_id  : 3f9b1d5e-1234-...-89ab

 Suggested shell variables (PowerShell):
   $PID = "3f9b1d5e-1234-...-89ab"
======================================================================
```

**Copia ese `pipeline_id`.** Lo vas a usar en todos los comandos siguientes.

---

## 2. Levanta los 4 MCPs (4 terminales)

> Cada MCP en su propia terminal. Si cierras una, ese MCP muere. Los logs JSON salen ahí.

### Terminal 1 — claims (puerto 8001) **con escrituras activadas**

```powershell
cd C:\Users\octav\insurance-automation-system
$env:PYTHONPATH = ".;mcp-servers"
$env:MCP_WRITE_ENABLED = "1"
$env:MCP_DOMAIN = "claims"
$env:DATABASE_URL = (Get-Content .env | Select-String "^DATABASE_URL=").ToString().Substring(13)
$env:MCP_SHARED_SECRET = (Get-Content .env | Select-String "^MCP_SHARED_SECRET=").ToString().Substring(19)
$env:CLAIM_UPLOAD_DIR = "./data/uploads"
uvicorn claims.server:app --host 0.0.0.0 --port 8001 --app-dir mcp-servers
```

> ⚠ Solo el MCP claims necesita `MCP_WRITE_ENABLED=1` — los demás son read-only.

> Truco rápido: en lugar de exportar a mano, usa `dotenv` (`pip install python-dotenv[cli]`) y:
> ```powershell
> dotenv run -- uvicorn claims.server:app --host 0.0.0.0 --port 8001 --app-dir mcp-servers
> ```
> Asegúrate de tener `MCP_WRITE_ENABLED=1` y `MCP_DOMAIN=claims` también en `.env` o exportadas antes.

### Terminal 2 — policies (puerto 8002)

```powershell
cd C:\Users\octav\insurance-automation-system
$env:PYTHONPATH = ".;mcp-servers"
$env:MCP_DOMAIN = "policies"
# (exporta DATABASE_URL y MCP_SHARED_SECRET igual que arriba — o usa dotenv)
uvicorn policies.server:app --host 0.0.0.0 --port 8002 --app-dir mcp-servers
```

### Terminal 3 — users (puerto 8003)

```powershell
cd C:\Users\octav\insurance-automation-system
$env:PYTHONPATH = ".;mcp-servers"
$env:MCP_DOMAIN = "users"
uvicorn users.server:app --host 0.0.0.0 --port 8003 --app-dir mcp-servers
```

### Terminal 4 — documents (puerto 8004)

```powershell
cd C:\Users\octav\insurance-automation-system
$env:PYTHONPATH = ".;mcp-servers"
$env:MCP_DOMAIN = "documents"
$env:CLAIM_UPLOAD_DIR = "./data/uploads"
uvicorn documents.server:app --host 0.0.0.0 --port 8004 --app-dir mcp-servers
```

### Verifica que los 4 estén vivos

```powershell
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health
# Cada uno debe responder: {"status":"ok"}
```

---

## 3. Levanta el orquestador (terminal 5)

```powershell
cd C:\Users\octav\insurance-automation-system
$env:PYTHONPATH = ".;mcp-servers;ai-orchestrator"
uvicorn app:app --host 0.0.0.0 --port 8000 --app-dir ai-orchestrator
```

Espera a ver el evento `orchestrator boot` con el tier y modelo. Luego:

```powershell
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## 4. Corre el pipeline — `/analyze` → `/audit` → `/confirm`

Usa una **6ª terminal** para `curl`. Primero exporta variables que vas a reusar:

```powershell
$PID  = "PEGAR-AQUI-EL-pipeline_id-QUE-IMPRIMIO-prepare_test_pdf"
$SUB  = "analyst@example.com"
$ROLE = "ANALYST"
$SECRET = (Get-Content .env | Select-String "^MCP_SHARED_SECRET=").ToString().Substring(19)
```

### 4.1 `/analyze` — el analyst razona y emite el proposal firmado

```powershell
$body = @{ pipeline_id = $PID; caller_sub = $SUB; caller_role = $ROLE } | ConvertTo-Json
$resp = curl.exe -s -X POST http://localhost:8000/analyze `
    -H "Content-Type: application/json" `
    -H "X-MCP-Secret: $SECRET" `
    -d $body | ConvertFrom-Json
$resp | ConvertTo-Json -Depth 8
```

**Respuesta esperada (success):**

```json
{
  "status": "success",
  "steps_used": 4,
  "proposal_id": "...",
  "proposal": {
    "proposal_id": "...",
    "pipeline_id": "...",
    "actor": "analyst",
    "payload_sha256": "...",
    "signature_hex": "...",
    "issued_at": "...",
    "expires_at": "..."
  },
  "triage": {
    "pipeline_id": "...",
    "policy_id": "...",
    "pdf_sha256": "...",
    "reported_damage_amount": "3500.00",
    "severity": "MEDIUM",
    "summary": "...",
    "evidence_quotes": ["..."]
  }
}
```

Guarda el `proposal_id`:

```powershell
$PROPOSAL_ID = $resp.proposal_id
```

> Si ves `status: "manual_review"`, mira el campo `reason`. Causas comunes: timeout, max_steps agotados, PDF no parseable, validation pipeline reventó 3 veces.

### 4.2 `/audit` — el auditor desafía el proposal

```powershell
$body = @{ proposal_id = $PROPOSAL_ID; caller_sub = $SUB; caller_role = $ROLE } | ConvertTo-Json
$resp = curl.exe -s -X POST http://localhost:8000/audit `
    -H "Content-Type: application/json" `
    -H "X-MCP-Secret: $SECRET" `
    -d $body | ConvertFrom-Json
$resp | ConvertTo-Json -Depth 8
$DECISION_ID = $resp.decision_id
```

**Respuesta esperada (success):**

```json
{
  "status": "success",
  "steps_used": 3,
  "decision_id": "...",
  "decision": {
    "claim_id": "...",
    "decision": "APPROVED",
    "reason": "...",
    "evidence": ["..."],
    "model": "gpt-4o-mini",
    "model_version": "gpt-4o-mini",
    "decided_at": "..."
  },
  "signed_proposal": { ... }
}
```

### 4.3 `/pending` — verifica qué hay esperando aprobación

```powershell
curl.exe -s -X GET http://localhost:8000/pending `
    -H "X-MCP-Secret: $SECRET" `
    -H "X-Caller-Sub: $SUB" `
    -H "X-Caller-Role: $ROLE" | ConvertFrom-Json | ConvertTo-Json
```

Debe listar tu `proposal_id` y `decision_id`.

### 4.4 `/confirm` — apruebas como humano → claim persiste

```powershell
$body = @{ decision_id = $DECISION_ID; approval = "approve"; caller_sub = $SUB; caller_role = $ROLE } | ConvertTo-Json
$resp = curl.exe -s -X POST http://localhost:8000/confirm `
    -H "Content-Type: application/json" `
    -H "X-MCP-Secret: $SECRET" `
    -d $body | ConvertFrom-Json
$resp | ConvertTo-Json -Depth 5
```

**Respuesta esperada:**

```json
{
  "status": "persisted",
  "claim": {
    "id": "...",
    "status": "APPROVED",
    "created_at": "...",
    "updated_at": "..."
  }
}
```

---

## 5. Verifica en Neon que el claim quedó persistido con audit trail completo

```powershell
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2, json
conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor()
cur.execute(\"\"\"
    SELECT id, status, severity, submitted_by_sub, reported_damage_amount, decision_metadata
    FROM claims WHERE id = %s
\"\"\", (input('claim_id: '),))
row = cur.fetchone()
print('id            :', row[0])
print('status        :', row[1])
print('severity      :', row[2])
print('submitted_by  :', row[3])
print('amount        :', row[4])
print('decision_meta :', json.dumps(row[5], indent=2, default=str))
cur.close(); conn.close()
"
```

Confirma que `decision_metadata` contiene:
- `approver_sub` (tú)
- `analyst_signature` y `auditor_signature` (ambas HMAC hex)
- `decision`, `decision_reason`, `decision_evidence`
- `model`, `model_version`, `decided_at`

Eso **es** el audit trail inmutable que exige Rule 6.

---

## 6. Pruebas de seguridad live (opcional pero divertido)

### 6.1 Anti-replay: aprobar el mismo decision_id dos veces

```powershell
# Primera ya pasó arriba → status: persisted
# Re-ejecuta el mismo curl /confirm:
$body = @{ decision_id = $DECISION_ID; approval = "approve"; caller_sub = $SUB; caller_role = $ROLE } | ConvertTo-Json
curl.exe -s -X POST http://localhost:8000/confirm -H "Content-Type: application/json" -H "X-MCP-Secret: $SECRET" -d $body
# Esperado: { "status": "refused", "reason": "decision already consumed" }
```

### 6.2 Anti-IDOR: aprobar como un sub distinto al submitter

```powershell
# Genera otro PDF/proposal con $SUB1 = "analyst@example.com"
# Luego intenta /confirm con $SUB2 = "stranger@example.com":
$body = @{ decision_id = $DECISION_ID; approval = "approve"; caller_sub = "stranger@example.com"; caller_role = "ANALYST" } | ConvertTo-Json
curl.exe -s -X POST http://localhost:8000/confirm -H "Content-Type: application/json" -H "X-MCP-Secret: $SECRET" -d $body
# Esperado: HTTP 403 — el HITLStore rechaza porque owner_sub != caller_sub
```

### 6.3 Rechazo humano

```powershell
# Para un proposal/decision distintos, llama:
$body = @{ decision_id = $OTRO_DECISION_ID; approval = "reject"; caller_sub = $SUB; caller_role = $ROLE } | ConvertTo-Json
curl.exe -s -X POST http://localhost:8000/confirm -H "Content-Type: application/json" -H "X-MCP-Secret: $SECRET" -d $body
# Esperado: { "status": "rejected_by_human", "reason": "human rejected" }
# El claim NO se persiste; el decision_id queda consumido (no se puede luego aprobar).
```

### 6.4 X-MCP-Secret incorrecto

```powershell
curl.exe -s -X POST http://localhost:8000/analyze -H "X-MCP-Secret: wrong-secret" -d "{}"
# Esperado: 401 unauthorized
```

### 6.5 Sin escrituras durante el agente

Mira los logs del orquestador. **No debe aparecer** ningún `analyst.tool` con `tool: create_claim` o `update_claim_status`. Si aparece — bug. Las únicas escrituras válidas vienen del mutation handler post-`/confirm`, identificadas con el evento `mutation.persisted`.

---

## 7. Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| `401 unauthorized` en `/analyze` | `X-MCP-Secret` mal | Verifica que `$SECRET` esté seteado y coincida con `.env` |
| `pdf not found` | El PDF no está como `{pipeline_id}.pdf` en `CLAIM_UPLOAD_DIR` | Re-corre `prepare_test_pdf.py` y usa el pipeline_id NUEVO que imprime |
| Analyst `manual_review` con `timeout` | Tu modelo tarda > 60s | Cambia a un modelo más rápido o sube `ANALYST_TIMEOUT_SECONDS` en `.env` |
| Analyst `manual_review` con `reached max_steps=10` | LLM loopea | Probablemente prompt injection del PDF o policy ambigua. Mira los logs para ver qué tools llamó |
| MCP claims dice `write operations are disabled` | Olvidaste `MCP_WRITE_ENABLED=1` | Reinicia el MCP claims con esa env var |
| Auditor emite `manual_review` con `validation failed 3 times` | El LLM no emite JSON válido en el formato pedido | Suele ser un modelo débil; sube a gpt-4o |
| `/audit` devuelve 404 `proposal not found or expired` | Pasó el TTL=300s | Re-corre `/analyze` para obtener un nuevo `proposal_id` |
| `/confirm` devuelve 403 `forbidden` | El `caller_sub` que mandas no es el dueño del proposal | Usa el mismo `caller_sub` que en `/analyze`, o usa role ADMIN |
| `claim could not be inserted nor located` | El INSERT colisionó con uno previo del MISMO sub + mismo sha256 | Usa otro PDF, o borra el claim previo con `DELETE FROM claims WHERE id = ...` |

---

## 8. Limpieza después de probar

```powershell
# Mata los 5 uvicorns: Ctrl+C en cada terminal, o desde otra terminal:
Get-Process | Where-Object { $_.Path -like "*python*" -and $_.CommandLine -like "*uvicorn*" } | Stop-Process

# Borra los PDFs de prueba si quieres:
Remove-Item .\data\uploads\*.pdf

# Borra los claims de prueba en Neon (cuidado):
python -c "
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL']); cur = conn.cursor()
cur.execute(\"DELETE FROM claims WHERE submitted_by_sub IN ('analyst@example.com', 'admin@example.com')\")
print('deleted:', cur.rowcount)
conn.commit(); cur.close(); conn.close()
"

# Vacía Redis HITL keys:
python -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
import os
from redis.asyncio import from_url
async def main():
    r = from_url(os.environ['REDIS_URL'], decode_responses=True)
    cursor = 0; deleted = 0
    while True:
        cursor, keys = await r.scan(cursor, match='hitl:*', count=100)
        if keys:
            await r.delete(*keys); deleted += len(keys)
        if cursor == 0: break
    print(f'deleted {deleted} hitl keys')
    await r.aclose()
asyncio.run(main())
"
```

---

## 9. Notas para bash / git-bash

Si usas git-bash en lugar de PowerShell:

```bash
export PYTHONPATH=".:mcp-servers"
export $(grep -v '^#' .env | xargs)   # carga todo el .env de un golpe
export MCP_WRITE_ENABLED=1    # solo para el terminal del claims MCP

# Lanzar MCP:
uvicorn claims.server:app --host 0.0.0.0 --port 8001 --app-dir mcp-servers

# Lanzar orquestador:
export PYTHONPATH=".:mcp-servers:ai-orchestrator"
uvicorn app:app --host 0.0.0.0 --port 8000 --app-dir ai-orchestrator

# curl directo (sin variables PowerShell):
PID="el-uuid-que-imprimio-prepare"
SUB="analyst@example.com"
SECRET="$(grep ^MCP_SHARED_SECRET .env | cut -d= -f2-)"

curl -sX POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -H "X-MCP-Secret: $SECRET" \
  -d "{\"pipeline_id\":\"$PID\",\"caller_sub\":\"$SUB\",\"caller_role\":\"ANALYST\"}" | jq .
```

---

**Listo.** Si algo no funciona, copia el output del MCP correspondiente — los logs JSON estructurados te dirán exactamente qué pasó.
