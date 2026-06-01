/**
 * api.js — LIA shared API client
 * Usado por claim-upload.html, monitoring.html y hitl.html
 *
 * Para servir la UI y evitar bloqueos CORS:
 *   cd ui && python -m http.server 5500
 * Luego abrir: http://localhost:5500
 *
 * Asegúrate que en .env el backend tenga:
 *   CORS_ALLOWED_ORIGINS=http://localhost:5500,...
 */

const API_BASE = 'http://localhost:8000';
const SESSION_KEY = 'insureai_session';

/* ── Error tipado ──────────────────────────────────────────────────── */
class APIError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/* ── Sesión (sessionStorage — se limpia al cerrar la pestaña) ─────── */
function saveSession({ token, sub, role }) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token, sub, role }));
}

function loadSession() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)); }
  catch { return null; }
}

function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

/* ── Cache de propuestas entre páginas ────────────────────────────── */
function saveProposalCache(proposalId, data) {
  try { sessionStorage.setItem(`proposal_${proposalId}`, JSON.stringify(data)); }
  catch {}
}
function loadProposalCache(proposalId) {
  try { return JSON.parse(sessionStorage.getItem(`proposal_${proposalId}`)); }
  catch { return null; }
}

/* ── Fetch wrapper con auth ───────────────────────────────────────── */
async function apiFetch(path, options = {}) {
  const session = loadSession();
  const headers = { ...(options.headers || {}) };
  if (session?.token) headers['Authorization'] = `Bearer ${session.token}`;

  let res;
  try {
    res = await fetch(API_BASE + path, { ...options, headers });
  } catch (err) {
    throw new APIError(0, 'Backend no disponible — ¿está corriendo docker compose up?');
  }

  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new APIError(res.status, detail);
  }

  return res.json();
}

/* ── Auth ─────────────────────────────────────────────────────────── */
async function devToken(sub, role) {
  const data = await apiFetch('/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sub, role }),
  });
  saveSession({ token: data.access_token, sub, role: data.role });
  return data;
}

/* ── Upload ───────────────────────────────────────────────────────── */
async function uploadFile(file) {
  const form = new FormData();
  form.append('file', file);
  return apiFetch('/submit-upload', { method: 'POST', body: form });
}

/* ── Analyze (bloqueante — puede tardar 30-60s) ───────────────────── */
async function analyze(pipelineId, claimantSub = null) {
  const body = { pipeline_id: pipelineId };
  if (claimantSub) body.claimant_sub = claimantSub;
  return apiFetch('/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/* ── Clients directory (ADMIN — para elegir a nombre de quién) ─────── */
async function getClients() {
  return apiFetch('/clients');
}

/* ── SSE ticket ───────────────────────────────────────────────────── */
async function mintSSETicket() {
  return apiFetch('/sse/ticket', { method: 'POST' });
}

/* ── Pending proposals & decisions ───────────────────────────────── */
async function getPending() {
  return apiFetch('/pending');
}

/* ── Full proposal detail (triage + signed proposal) ──────────────── */
async function getProposal(proposalId) {
  return apiFetch('/proposal/' + encodeURIComponent(proposalId));
}

/* ── Persisted claims from Postgres (monitoring board) ────────────── */
async function getClaims() {
  return apiFetch('/claims');
}

/* ── PDF blob (auth header can't ride on an <iframe src>, so fetch the
      bytes and hand back an object URL the viewer can render) ────────── */
async function getPdfBlobUrl(pipelineId) {
  const session = loadSession();
  const headers = {};
  if (session?.token) headers['Authorization'] = `Bearer ${session.token}`;
  const res = await fetch(API_BASE + '/pdf/' + encodeURIComponent(pipelineId), { headers });
  if (!res.ok) throw new APIError(res.status, 'No se pudo cargar el PDF');
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/* ── Audit (bloqueante — corre el agente auditor) ─────────────────── */
async function auditProposal(proposalId) {
  return apiFetch('/audit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proposal_id: proposalId }),
  });
}

/* ── Confirm / Reject ─────────────────────────────────────────────── */
async function confirmDecision(decisionId, approval) {
  return apiFetch('/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision_id: decisionId, approval }),
  });
}

/* ── Health check ─────────────────────────────────────────────────── */
async function healthCheck() {
  try {
    const res = await fetch(API_BASE + '/health', { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch { return false; }
}

/* ── Login modal helper (shared across pages) ─────────────────────── */
function buildLoginModal({ requiredRole = 'analyst', presetSub = '', onSuccess }) {
  const existing = document.getElementById('login-modal-backdrop');
  if (existing) existing.remove();

  const ROLE_PRESETS = {
    analyst: [
      { label: 'Octavio Sánchez Mendoza (Analista)', sub: 'octavio.sanchez@insureai.com', role: 'analyst' },
    ],
    auditor: [
      { label: 'Octavio Sánchez Mendoza (Auditor)', sub: 'octavio.sanchez@insureai.com', role: 'auditor' },
    ],
    admin: [
      { label: 'Octavio Sánchez Mendoza (Operador / Admin)', sub: 'octavio.sanchez@insureai.com', role: 'admin' },
    ],
  };

  const presets = ROLE_PRESETS[requiredRole] || ROLE_PRESETS.analyst;

  const bd = document.createElement('div');
  bd.id = 'login-modal-backdrop';
  bd.style.cssText = `
    position:fixed;inset:0;background:rgba(17,24,39,.55);
    z-index:9999;display:grid;place-items:center;
    font-family:'Inter',system-ui,sans-serif;
  `;

  bd.innerHTML = `
    <div style="background:#fff;border-radius:14px;width:400px;max-width:calc(100vw - 32px);
                box-shadow:0 8px 40px rgba(0,0,0,.14);padding:28px 28px 24px;">
      <div style="width:40px;height:40px;background:#EDEDFB;border-radius:50%;
                  display:grid;place-items:center;margin-bottom:14px;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#5B5BD6" stroke-width="2.5" stroke-linecap="round">
          <path d="M8 2L13 5v4c0 2.5-2 4.5-5 5C5 14.5 3 12.5 3 9V5L8 2z"/>
        </svg>
      </div>
      <div style="font-size:17px;font-weight:700;color:#111827;margin-bottom:5px;letter-spacing:-.3px;">
        LIA — Acceso
      </div>
      <div style="font-size:13px;color:#6B7280;margin-bottom:20px;line-height:1.5;">
        Esta página requiere rol <strong style="color:#111">${requiredRole}</strong>.
        Selecciona un usuario de prueba o ingresa tus credenciales.
      </div>

      <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#9CA3AF;margin-bottom:8px;">
        Usuarios rápidos (dev)
      </div>
      <div id="lm-presets" style="display:flex;flex-direction:column;gap:6px;margin-bottom:16px;">
        ${presets.map(p => `
          <button onclick="loginModal_quickLogin('${p.sub}','${p.role}')"
            style="padding:9px 14px;background:#F9FAFB;border:1px solid #E4E7EC;
                   border-radius:8px;font-size:13.5px;font-weight:500;color:#111827;
                   cursor:pointer;text-align:left;transition:background .12s;font-family:inherit;"
            onmouseover="this.style.background='#F0F2F5'" onmouseout="this.style.background='#F9FAFB'">
            ${p.label}
          </button>
        `).join('')}
      </div>

      <div style="border-top:1px solid #F0F2F5;padding-top:16px;">
        <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#9CA3AF;margin-bottom:8px;">
          O ingresa manualmente
        </div>
        <input id="lm-sub" type="text" placeholder="sub (email o ID)"
          value="${presetSub}"
          style="width:100%;padding:8px 12px;border:1px solid #E4E7EC;border-radius:8px;
                 font-size:13.5px;font-family:inherit;outline:none;margin-bottom:8px;
                 box-sizing:border-box;" />
        <select id="lm-role"
          style="width:100%;padding:8px 12px;border:1px solid #E4E7EC;border-radius:8px;
                 font-size:13.5px;font-family:inherit;outline:none;margin-bottom:12px;
                 box-sizing:border-box;background:#fff;cursor:pointer;">
          <option value="analyst" ${requiredRole==='analyst'?'selected':''}>analyst</option>
          <option value="auditor" ${requiredRole==='auditor'?'selected':''}>auditor</option>
          <option value="admin">admin</option>
        </select>
        <button id="lm-btn"
          onclick="loginModal_login()"
          style="width:100%;padding:10px;background:#5B5BD6;border:none;border-radius:8px;
                 font-size:13.5px;font-weight:700;color:#fff;cursor:pointer;font-family:inherit;">
          Ingresar
        </button>
        <div id="lm-err" style="font-size:12.5px;color:#DC2626;margin-top:8px;display:none;"></div>
      </div>
    </div>
  `;

  document.body.appendChild(bd);

  // exposed globals para los onclick inline
  window._loginModal_onSuccess = onSuccess;

  window.loginModal_quickLogin = async (sub, role) => {
    await _doLogin(sub, role);
  };

  window.loginModal_login = async () => {
    const sub  = document.getElementById('lm-sub').value.trim();
    const role = document.getElementById('lm-role').value;
    if (!sub) { showLmErr('Ingresa un sub (email).'); return; }
    await _doLogin(sub, role);
  };

  async function _doLogin(sub, role) {
    const btn = document.getElementById('lm-btn');
    if (btn) { btn.textContent = 'Conectando…'; btn.disabled = true; }
    try {
      await devToken(sub, role);
      bd.remove();
      if (window._loginModal_onSuccess) window._loginModal_onSuccess({ sub, role });
    } catch (err) {
      showLmErr(err.message || 'Error de conexión');
      if (btn) { btn.textContent = 'Ingresar'; btn.disabled = false; }
    }
  }

  function showLmErr(msg) {
    const el = document.getElementById('lm-err');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
  }
}

/* ── Barra de sesión (esquina inferior derecha) ────────────────────── */
function mountSessionBar() {
  const existing = document.getElementById('session-bar');
  if (existing) existing.remove();

  const session = loadSession();
  if (!session) return;

  const bar = document.createElement('div');
  bar.id = 'session-bar';
  bar.style.cssText = `
    position:fixed;bottom:12px;right:16px;z-index:500;
    background:rgba(255,255,255,.9);backdrop-filter:blur(8px);
    border:1px solid #E4E7EC;border-radius:99px;
    padding:6px 14px 6px 10px;
    display:flex;align-items:center;gap:8px;
    font-family:'Inter',system-ui,sans-serif;font-size:12px;color:#6B7280;
    box-shadow:0 2px 8px rgba(0,0,0,.07);
  `;
  bar.innerHTML = `
    <div style="width:22px;height:22px;background:#EDEDFB;border-radius:50%;
                display:grid;place-items:center;font-size:9px;font-weight:700;color:#5B5BD6;">
      ${session.sub.substring(0,2).toUpperCase()}
    </div>
    <span style="font-weight:500;color:#111827;">${session.sub}</span>
    <span style="background:#EDEDFB;color:#5B5BD6;padding:1px 7px;border-radius:99px;
                 font-size:10.5px;font-weight:600;">${session.role}</span>
    <button onclick="clearSession();location.reload();"
      style="background:none;border:none;cursor:pointer;color:#9CA3AF;font-size:11px;
             padding:2px 4px;border-radius:4px;font-family:inherit;"
      onmouseover="this.style.color='#DC2626'" onmouseout="this.style.color='#9CA3AF'">
      salir
    </button>
  `;
  document.body.appendChild(bar);
}

/* ── Backend status chip (esquina superior derecha de cada página) ── */
async function mountBackendStatus() {
  const chip = document.createElement('div');
  chip.id = 'backend-status';
  chip.style.cssText = `
    position:fixed;top:68px;right:16px;z-index:490;
    padding:4px 12px;border-radius:99px;font-size:11.5px;font-weight:600;
    font-family:'Inter',system-ui,sans-serif;
    display:flex;align-items:center;gap:5px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);
  `;

  const ok = await healthCheck();
  chip.style.background = ok ? '#D1FAE5' : '#FEE2E2';
  chip.style.color       = ok ? '#065F46' : '#991B1B';
  chip.innerHTML = `
    <span style="width:6px;height:6px;border-radius:50%;background:currentColor;
                 ${ok ? 'animation:pulse 1.5s infinite' : ''}"></span>
    ${ok ? 'Backend conectado' : 'Backend no disponible'}
  `;

  document.body.appendChild(chip);
  return ok;
}
