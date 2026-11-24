/**
 * WhatsApp bridge — tiny HTTP wrapper around Baileys (unofficial WhatsApp Web API).
 * The Python app talks to it on 127.0.0.1 only, authenticated by a shared secret.
 * One always-connected socket per number; events are buffered and drained by Python.
 *
 * Env:
 *   WA_BRIDGE_PORT     (default 8895)
 *   WA_BRIDGE_SECRET   (required)
 *   WA_BRIDGE_DATA_DIR (default ./data)
 */
import express from "express";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import pino from "pino";
import QRCode from "qrcode";
import makeWASocket, {
  useMultiFileAuthState, DisconnectReason, isJidGroup, isJidBroadcast, isJidNewsletter,
  isLidUser, isPnUser, jidNormalizedUser,
} from "baileys";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = process.env.WA_BRIDGE_DATA_DIR || path.join(__dirname, "data");
const AUTH_DIR = path.join(DATA_DIR, "auth");
const PORT = parseInt(process.env.WA_BRIDGE_PORT || "8895", 10);
const SECRET = process.env.WA_BRIDGE_SECRET || "";

if (!SECRET) {
  console.error("FATAL: WA_BRIDGE_SECRET is not set");
  process.exit(1);
}
fs.mkdirSync(AUTH_DIR, { recursive: true });

const logger = pino({ level: "warn" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (j) => (j ? jidNormalizedUser(j) : "");
const ID_RE = /^[a-z0-9]{4,12}$/; // slot ids become directory names
const authDir = (id) => path.join(AUTH_DIR, id);
const GROUPS_TTL_MS = 5 * 60 * 1000;
const QUEUE_MAX = 5000;
// Closing with one of these means the session is gone — reconnecting would loop.
const TERMINAL = {
  [DisconnectReason.loggedOut]: "Đã bị đăng xuất khỏi điện thoại — quét QR lại",
  [DisconnectReason.forbidden]: "Số bị WhatsApp khoá/cấm",
  419: "Phiên hết hiệu lực — quét QR lại",
  [DisconnectReason.connectionReplaced]: "Phiên bị thay thế (số này đang chạy ở một bridge khác)",
  [DisconnectReason.multideviceMismatch]: "Điện thoại chưa bật chế độ nhiều thiết bị",
};

/** id -> { id, sock, open, me, login, retry, sent, groups, spoke, restricted_until } */
const accounts = new Map();
const idle = () => ({ state: "idle", qr: null, error: null });

/** gid -> account id that listens there (set by Python on every /events call). */
let tracked = new Map();
const events = { joins: [], replies: [], manual: [], failed: [], speakers: [] };
function push(kind, item) {
  const q = events[kind];
  q.push(item);
  if (q.length > QUEUE_MAX) q.splice(0, q.length - QUEUE_MAX);
}

function slot(id) {
  if (!accounts.has(id)) {
    accounts.set(id, { id, sock: null, open: false, me: null, login: idle(), retry: 0,
                       sent: new Map(), groups: null, spoke: new Map(), restricted_until: null });
  }
  return accounts.get(id);
}

async function dropSlot(id) {
  const a = accounts.get(id);
  accounts.delete(id);
  if (a?.sock) {
    const sock = a.sock;
    a.sock = null; // the close handler sees a stale socket and stays out of the way
    try { if (a.open) await sock.logout(); else sock.end(undefined); } catch { /* already closed */ }
  }
  fs.rmSync(authDir(id), { recursive: true, force: true });
}

/** One person, whichever of their two WhatsApp ids we were given: {uid, lid, phone, name}. */
function person(id, alt, name) {
  const a = norm(id), b = norm(alt);
  const lid = isLidUser(a) ? a : isLidUser(b) ? b : "";
  const pn = isPnUser(a) ? a : isPnUser(b) ? b : "";
  if (!lid && !pn) return null;
  return { uid: lid || pn, lid, phone: pn.split("@")[0], name: name || "" };
}
const ofParticipant = (p) => person(p.id, p.lid || p.phoneNumber, p.notify || p.name);
const isMe = (a, p) => !!p && ((p.lid && p.lid === a.me?.lid) || (p.phone && p.phone === a.me?.phone));

async function loadGroups(a, force = false) {
  if (!force && a.groups && Date.now() - a.groups.at < GROUPS_TTL_MS) return a.groups.data;
  a.groups = { at: Date.now(), data: await a.sock.groupFetchAllParticipating() };
  return a.groups.data;
}

async function groupOf(a, gid) {
  return a.groups?.data?.[gid] || (await loadGroups(a, true))[gid];
}

const tracks = (a, gid) => tracked.get(gid) === a.id;

async function onMessage(a, m) {
  const jid = m.key?.remoteJid;
  if (!jid) return;
  if (isJidGroup(jid)) {
    // Someone spoke in a tracked group. An undecryptable message still counts as activity.
    if (m.key.fromMe || !tracks(a, jid)) return;
    const p = person(m.key.participant, m.key.participantAlt, m.pushName);
    if (!p || isMe(a, p)) return;
    const seen = a.spoke.get(jid) || a.spoke.set(jid, new Set()).get(jid);
    if (seen.has(p.uid)) return;
    seen.add(p.uid);
    const g = await groupOf(a, jid);
    const admin = (g?.participants || []).some((x) => x.admin && [norm(x.id), norm(x.lid), norm(x.phoneNumber)]
      .some((j) => j && (j === p.lid || j.split("@")[0] === p.phone)));
    if (!admin) push("speakers", { account: a.id, group_id: jid, ...p });
    return;
  }
  if (!isPnUser(jid) && !isLidUser(jid)) return;
  const body = m.message;
  if (!body || body.protocolMessage || body.reactionMessage) return;
  const p = person(jid, m.key.remoteJidAlt, m.pushName);
  if (!p || isMe(a, p)) return; // note-to-self
  const ev = { account: a.id, jid: norm(jid), alt_jid: norm(m.key.remoteJidAlt) };
  // emitOwnEvents is off, so a fromMe message here was typed by the owner on another device.
  if (m.key.fromMe) push("manual", ev);
  else push("replies", { ...ev, name: m.pushName || "", ts: Number(m.messageTimestamp?.toString() || 0) });
}

async function connect(a) {
  if (a.sock) return;
  const dir = authDir(a.id);
  const { state, saveCreds } = await useMultiFileAuthState(dir);
  const sock = a.sock = makeWASocket({
    auth: state,
    logger,
    syncFullHistory: false,
    markOnlineOnConnect: false, // the phone keeps getting its notifications
    emitOwnEvents: false,       // what this bridge sends never comes back through messages.upsert
    shouldIgnoreJid: (j) => isJidBroadcast(j) || isJidNewsletter(j) || (isJidGroup(j) && !tracks(a, j)),
    getMessage: async (k) => a.sent.get(k.id)?.message,
  });
  const reconnect = (wait) => setTimeout(() => {
    if (accounts.get(a.id) === a) connect(a).catch((e) => { a.login = { ...idle(), state: "error", error: e.message }; });
  }, wait);

  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", async (u) => {
    if (a.sock !== sock) return;
    if (u.qr) a.login = { state: "qr_ready", qr: await QRCode.toDataURL(u.qr, { width: 320 }), error: null };
    if (u.reachoutTimeLock) {
      const t = u.reachoutTimeLock;
      a.restricted_until = t.isActive ? (t.timeEnforcementEnds ? new Date(t.timeEnforcementEnds).toISOString() : "unknown") : null;
    }
    if (u.connection === "open") {
      a.open = true;
      a.retry = 0;
      a.login = { ...idle(), state: "logged_in" };
      const jid = norm(sock.user.id);
      a.me = { jid, lid: norm(sock.user.lid), phone: jid.split("@")[0], name: sock.user.name || "" };
      // Same number scanned into a second slot — keep only the newest.
      for (const other of [...accounts.values()]) {
        if (other.id !== a.id && other.me?.jid === jid) await dropSlot(other.id);
      }
      console.log(`[${a.id}] WhatsApp connected as ${a.me.phone}`);
    }
    if (u.connection !== "close") return;
    a.open = false;
    a.sock = null;
    const code = u.lastDisconnect?.error?.output?.statusCode;
    if (TERMINAL[code]) {
      a.login = { ...idle(), state: "error", error: TERMINAL[code] };
      if (code === DisconnectReason.loggedOut) fs.rmSync(dir, { recursive: true, force: true });
      console.error(`[${a.id}] closed for good (${code}): ${TERMINAL[code]}`);
      return;
    }
    if (code === DisconnectReason.restartRequired) { // follows every successful QR scan
      if (!a.me) a.login = { ...idle(), state: "scanned" };
      return reconnect(0);
    }
    if (!state.creds.me) { // never paired: the QR ran out
      a.login = { ...idle(), state: "expired", error: "QR hết hạn" };
      return;
    }
    reconnect(Math.min(60000, 2000 * 2 ** a.retry++));
  });
  sock.ev.on("messages.upsert", ({ messages }) => {
    for (const m of messages) onMessage(a, m).catch((e) => console.warn(`[${a.id}] message handler:`, e.message));
  });
  // A send the server refused arrives later as status 0 (ERROR) — e.g. 463 on a restricted number.
  sock.ev.on("messages.update", (ups) => {
    for (const { key, update } of ups) {
      const s = key?.fromMe && update?.status === 0 && a.sent.get(key.id);
      if (s) push("failed", { account: a.id, jid: s.jid, code: String(update.messageStubParameters?.[0] || "") });
    }
  });
  sock.ev.on("group-participants.update", (ev) => {
    if (ev.action !== "add" || !tracks(a, ev.id)) return;
    const members = (ev.participants || []).map((p) => (typeof p === "string" ? person(p) : ofParticipant(p)))
      .filter((p) => p && !isMe(a, p));
    if (!members.length) return;
    push("joins", { account: a.id, group_id: ev.id, group_name: a.groups?.data?.[ev.id]?.subject || "", members });
    console.log(`[${a.id}] ${members.length} joined ${ev.id}`);
  });
}

function startLogin(a) {
  a.login = { ...idle(), state: "waiting_qr" };
  connect(a).catch((e) => { a.login = { ...idle(), state: "error", error: e.message }; });
}

function restoreSessions() {
  for (const id of fs.readdirSync(AUTH_DIR)) {
    let paired = false;
    try { paired = !!JSON.parse(fs.readFileSync(path.join(authDir(id), "creds.json"), "utf8")).me; } catch { /* half-made dir */ }
    if (!ID_RE.test(id) || !paired) { fs.rmSync(authDir(id), { recursive: true, force: true }); continue; }
    const a = slot(id);
    connect(a).catch((e) => { a.login = { ...idle(), state: "error", error: e.message }; });
  }
}

const publicAccount = (a) => ({ id: a.id, logged_in: a.open, me: a.me, login: a.login, restricted_until: a.restricted_until });

const app = express();
app.use(express.json());
app.use((req, res, next) => {
  if (req.get("X-Bridge-Secret") !== SECRET) return res.status(403).json({ error: "forbidden" });
  next();
});
app.param("id", (req, res, next, id) => {
  if (req.path.startsWith("/accounts/") && !ID_RE.test(id)) return res.status(400).json({ error: "bad account id" });
  next();
});
// X-WA-Account picks the number; without it, the first connected one is used.
const needLogin = (req, res, next) => {
  const want = req.get("X-WA-Account");
  const a = want ? accounts.get(want) : [...accounts.values()].find((x) => x.open);
  if (!a?.open) return res.status(409).json({ error: want ? `Số ${want} chưa kết nối` : "WhatsApp chưa đăng nhập" });
  req.a = a;
  next();
};
const wrap = (fn) => (req, res) => fn(req, res).catch((e) => {
  console.error(req.path, e.message);
  res.status(502).json({ error: e.message || String(e), code: e.output?.statusCode ?? null });
});

app.get("/status", (req, res) => {
  const list = [...accounts.values()].map(publicAccount);
  res.json({ logged_in: list.some((a) => a.logged_in), accounts: list });
});

// New number: create a slot and start its QR login.
app.post("/accounts", (req, res) => {
  const id = Math.random().toString(36).slice(2, 10).padEnd(4, "0");
  startLogin(slot(id));
  res.json({ ok: true, id });
});

// Re-scan for an existing number (logged out / QR expired).
app.post("/accounts/:id/login", (req, res) => {
  const a = slot(req.params.id);
  if (a.open) return res.json({ ok: true, already: true });
  if (!a.sock) startLogin(a);
  res.json({ ok: true });
});

app.delete("/accounts/:id", wrap(async (req, res) => {
  await dropSlot(req.params.id);
  res.json({ ok: true });
}));

app.get("/groups", needLogin, wrap(async (req, res) => {
  const data = await loadGroups(req.a, req.query.refresh === "1");
  res.json(Object.values(data).filter((g) => !g.isCommunity) // the community shell has no chat of its own
    .map((g) => ({ id: g.id, name: g.subject || "", total: g.size || g.participants.length, announce: !!g.announce }))
    .sort((x, y) => y.total - x.total));
}));

app.get("/groups/:id/members", needLogin, wrap(async (req, res) => {
  const g = await groupOf(req.a, req.params.id);
  if (!g) return res.status(404).json({ error: "Không tìm thấy nhóm" });
  const members = [], admins = [];
  for (const raw of g.participants) {
    const p = ofParticipant(raw);
    if (!p || isMe(req.a, p)) continue;
    (raw.admin ? admins : members).push(p);
  }
  res.json({ group: { id: g.id, name: g.subject || "", total: g.size || g.participants.length }, members, admins });
}));

app.get("/invite/:code", needLogin, wrap(async (req, res) => {
  const g = await req.a.sock.groupGetInviteInfo(req.params.code);
  res.json({ id: g.id, name: g.subject || "", total: g.size || 0, needs_approval: !!g.joinApprovalMode,
             joined: !!req.a.groups?.data?.[g.id] });
}));

app.post("/groups/join", needLogin, wrap(async (req, res) => {
  const code = req.body?.code;
  if (!code) return res.status(400).json({ error: "code required" });
  const gid = await req.a.sock.groupAcceptInvite(code); // undefined = waiting for an admin to approve
  req.a.groups = null;
  res.json({ ok: true, group_id: gid || null, pending: !gid });
}));

app.post("/send", needLogin, wrap(async (req, res) => {
  const { jid, message } = req.body || {};
  if (!jid || !message) return res.status(400).json({ error: "jid and message required" });
  const { a } = req, sock = a.sock;
  const presence = (...args) => sock.sendPresenceUpdate(...args).catch(() => {}); // cosmetic
  await presence("available");
  await presence("composing", jid);
  await sleep(2000 + Math.random() * 3000);
  try {
    const m = await sock.sendMessage(jid, { text: message });
    a.sent.set(m.key.id, { jid: norm(jid), message: m.message });
    if (a.sent.size > 200) a.sent.delete(a.sent.keys().next().value);
    res.json({ ok: true, msg_id: m.key.id });
  } finally {
    await presence("unavailable"); // staying "online" would mute the owner's phone
  }
}));

// Drain buffered events; Python also says which groups each number should listen to.
app.post("/events", (req, res) => {
  tracked = new Map(Object.entries(req.body?.tracked || {}));
  const out = {};
  for (const k of Object.keys(events)) out[k] = events[k].splice(0, events[k].length);
  res.json(out);
});

app.listen(PORT, "127.0.0.1", () => console.log(`wa-bridge listening on 127.0.0.1:${PORT}`));
restoreSessions();
