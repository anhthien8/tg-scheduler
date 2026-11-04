/**
 * Zalo bridge — tiny HTTP wrapper around zca-js (unofficial Zalo personal API).
 * The Python app talks to it on 127.0.0.1 only, authenticated by a shared secret.
 *
 * Env:
 *   ZALO_BRIDGE_PORT   (default 8897)
 *   ZALO_BRIDGE_SECRET (required; same value in the Python .env)
 */
import express from "express";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Zalo, ThreadType, LoginQRCallbackEventType } from "zca-js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = process.env.ZALO_BRIDGE_DATA_DIR || path.join(__dirname, "data");
const SESSIONS_DIR = path.join(DATA_DIR, "sessions");
const LEGACY_SESSION = path.join(DATA_DIR, "session.json");
const PORT = parseInt(process.env.ZALO_BRIDGE_PORT || "8897", 10);
const SECRET = process.env.ZALO_BRIDGE_SECRET || "";

if (!SECRET) {
  console.error("FATAL: ZALO_BRIDGE_SECRET is not set");
  process.exit(1);
}
fs.mkdirSync(SESSIONS_DIR, { recursive: true });
// Single-account installs kept one session.json — it becomes account "main".
if (fs.existsSync(LEGACY_SESSION)) fs.renameSync(LEGACY_SESSION, path.join(SESSIONS_DIR, "main.json"));

/** id -> { id, api, me: {uid,name,avatar}|null, login: {state,qr,scanned,error} } */
const accounts = new Map();
const idle = () => ({ state: "idle", qr: null, scanned: null, error: null });
const sessionFile = (id) => path.join(SESSIONS_DIR, `${id}.json`);

function slot(id) {
  if (!accounts.has(id)) accounts.set(id, { id, api: null, me: null, login: idle() });
  return accounts.get(id);
}

function dropSlot(id) {
  const a = accounts.get(id);
  try { if (a?.listening) a.api.listener.stop(); } catch { /* not started */ }
  accounts.delete(id);
  if (fs.existsSync(sessionFile(id))) fs.unlinkSync(sessionFile(id));
}

async function afterLogin(a, newApi) {
  const uid = newApi.getOwnId();
  // Same Zalo account scanned into a second slot — keep only the newest.
  for (const other of accounts.values()) {
    if (other.id !== a.id && other.me?.uid === uid) dropSlot(other.id);
  }
  let name = "", avatar = "";
  try {
    const info = await newApi.getUserInfo(uid);
    const p = info.changed_profiles?.[uid] || info.changed_profiles?.[`${uid}_0`] || {};
    name = p.displayName || p.zaloName || "";
    avatar = p.avatar || "";
  } catch { /* profile is cosmetic */ }
  a.api = newApi;
  a.me = { uid, name, avatar };
  a.login = idle();
  a.login.state = "logged_in";
  console.log(`[${a.id}] Zalo logged in as ${name || uid}`);
}

async function restoreSessions() {
  for (const f of fs.readdirSync(SESSIONS_DIR).filter((f) => f.endsWith(".json"))) {
    const a = slot(f.slice(0, -5));
    try {
      const cred = JSON.parse(fs.readFileSync(sessionFile(a.id), "utf8"));
      await afterLogin(a, await new Zalo({ selfListen: false, logging: false }).login(cred));
    } catch (e) {
      console.error(`[${a.id}] saved session invalid:`, e.message);
      a.login = { ...idle(), error: "Phiên đăng nhập hết hạn, quét QR lại" };
    }
  }
}

function startQrLogin(a) {
  a.login = { ...idle(), state: "waiting_qr" };
  const zalo = new Zalo({ selfListen: false, logging: false });
  zalo.loginQR({}, (ev) => {
    switch (ev.type) {
      case LoginQRCallbackEventType.QRCodeGenerated:
        a.login.state = "qr_ready";
        a.login.qr = ev.data.image; // base64 PNG
        break;
      case LoginQRCallbackEventType.QRCodeExpired:
        a.login = { ...idle(), state: "expired", error: "QR hết hạn" };
        ev.actions.abort();
        break;
      case LoginQRCallbackEventType.QRCodeScanned:
        a.login.state = "scanned";
        a.login.scanned = ev.data.display_name;
        break;
      case LoginQRCallbackEventType.QRCodeDeclined:
        a.login = { ...idle(), state: "declined", error: "Đã từ chối trên điện thoại" };
        ev.actions.abort();
        break;
      case LoginQRCallbackEventType.GotLoginInfo:
        fs.writeFileSync(sessionFile(a.id),
          JSON.stringify({ cookie: ev.data.cookie, imei: ev.data.imei, userAgent: ev.data.userAgent }), { mode: 0o600 });
        break;
    }
  })
    .then((newApi) => afterLogin(a, newApi))
    .catch((e) => {
      if (a.login.state !== "expired" && a.login.state !== "declined") {
        a.login = { ...idle(), state: "error", error: e.message };
      }
    });
}

const publicAccount = (a) => ({ id: a.id, logged_in: !!a.api, me: a.me, login: a.login,
                                 listening: !!a.listening, listen_error: a.listenError || null });

// ── New-member watch ── join events from every listening nick, drained by Python.
const joinQueue = [];
const JOIN_QUEUE_MAX = 5000;

const RELISTEN_AFTER_MS = 10 * 60 * 1000;

function startListener(a) {
  if (a.listening || !a.api) return;
  // Closed (e.g. Zalo Web opened elsewhere for this nick) → wait before taking the socket back.
  if (a.closedAt && Date.now() - a.closedAt < RELISTEN_AFTER_MS) return;
  const l = a.api.listener;
  if (a.listenerWired) { l.start({ retryOnClose: true }); a.listening = true; return; }
  a.listenerWired = true;
  l.on("group_event", (ev) => {
    if (ev.type !== "join" || ev.isSelf) return;
    const d = ev.data || {};
    const members = (d.updateMembers || [])
      .filter((m) => m?.id && String(m.id) !== a.me?.uid)
      .map((m) => ({ uid: String(m.id), name: m.dName || "", avatar: m.avatar || "" }));
    if (!members.length) return;
    joinQueue.push({ account: a.id, group_id: String(ev.threadId || d.groupId), group_name: d.groupName || "",
                     members, time: Date.now() });
    if (joinQueue.length > JOIN_QUEUE_MAX) joinQueue.splice(0, joinQueue.length - JOIN_QUEUE_MAX);
    console.log(`[${a.id}] ${members.length} joined group ${d.groupName || ev.threadId}`);
  });
  l.on("connected", () => { a.listenError = null; });
  l.on("closed", (code, reason) => {
    a.listening = false;
    a.closedAt = Date.now();
    a.listenError = code === 3000 || code === 3003
      ? "Nick đang mở Zalo Web/PC ở nơi khác — tạm dừng theo dõi 10 phút"
      : `Mất kết nối nhận sự kiện (${code}) ${reason || ""}`.trim();
  });
  l.on("error", (e) => { a.listenError = String(e?.message || e); });
  l.start({ retryOnClose: true });
  a.listening = true;
}

// Zalo throws for a single unknown uid inside a batch; fetch profiles in chunks.
async function fetchProfiles(api, uids) {
  const out = {};
  for (let i = 0; i < uids.length; i += 50) {
    const chunk = uids.slice(i, i + 50);
    try {
      const res = await api.getGroupMembersInfo(chunk);
      for (const [id, p] of Object.entries(res.profiles || {})) {
        out[id.replace(/_0$/, "")] = { name: p.displayName || p.zaloName || "", avatar: p.avatar || "" };
      }
    } catch (e) {
      console.warn("profile chunk failed:", e.message);
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return uids.map((uid) => ({ uid, name: out[uid]?.name || "", avatar: out[uid]?.avatar || "" }));
}

// Owner + deputies of a group — never outreach targets.
function adminsOf(g) {
  return new Set([g?.creatorId, ...(g?.adminIds || [])].filter(Boolean).map(String));
}

async function groupAdmins(api, groupId) {
  if (!groupId) return new Set();
  const info = await api.getGroupInfo(groupId);
  return adminsOf(info.gridInfoMap?.[groupId]);
}

const app = express();
app.use(express.json());
app.use((req, res, next) => {
  if (req.get("X-Bridge-Secret") !== SECRET) return res.status(403).json({ error: "forbidden" });
  next();
});
// X-Zalo-Account picks the nick; without it, the first logged-in nick is used.
const needLogin = (req, res, next) => {
  const want = req.get("X-Zalo-Account");
  const a = want ? accounts.get(want) : [...accounts.values()].find((x) => x.api);
  if (!a?.api) return res.status(409).json({ error: want ? `Nick ${want} chưa đăng nhập` : "Zalo chưa đăng nhập" });
  req.api = a.api;
  req.me = a.me;
  next();
};
const wrap = (fn) => (req, res) => fn(req, res).catch((e) => {
  console.error(req.path, e);
  res.status(502).json({ error: e.message || String(e), code: e.code ?? null });
});

app.get("/status", (req, res) => {
  const list = [...accounts.values()].map(publicAccount);
  res.json({ logged_in: list.some((a) => a.logged_in), accounts: list });
});

// New nick: create a slot and start its QR login.
app.post("/accounts", (req, res) => {
  const id = Math.random().toString(36).slice(2, 10);
  startQrLogin(slot(id));
  res.json({ ok: true, id });
});

// Re-scan for an existing nick (expired session).
app.post("/accounts/:id/login", (req, res) => {
  const a = slot(req.params.id);
  if (a.api) return res.json({ ok: true, already: true });
  if (!["waiting_qr", "qr_ready", "scanned"].includes(a.login.state)) startQrLogin(a);
  res.json({ ok: true });
});

app.delete("/accounts/:id", (req, res) => {
  dropSlot(req.params.id);
  res.json({ ok: true });
});

app.get("/groups", needLogin, wrap(async (req, res) => {
  const all = await req.api.getAllGroups();
  const ids = Object.keys(all.gridVerMap || {});
  const groups = [];
  for (let i = 0; i < ids.length; i += 50) {
    const info = await req.api.getGroupInfo(ids.slice(i, i + 50));
    for (const [id, g] of Object.entries(info.gridInfoMap || {})) {
      groups.push({ id, name: g.name, avatar: g.avt || g.fullAvt || "", total: g.totalMember || 0 });
    }
  }
  res.json(groups.sort((a, b) => b.total - a.total));
}));

app.get("/groups/:id/members", needLogin, wrap(async (req, res) => {
  const info = await req.api.getGroupInfo(req.params.id);
  const g = info.gridInfoMap?.[req.params.id];
  if (!g) return res.status(404).json({ error: "Không tìm thấy nhóm" });
  // memVerList entries look like "<uid>_<version>"
  const admins = adminsOf(g);
  const uids = [...new Set((g.memVerList || []).map((s) => s.split("_")[0]).concat(g.memberIds || []))]
    .filter((u) => u && u !== req.me?.uid && !admins.has(u));
  res.json({ group: { id: req.params.id, name: g.name, total: g.totalMember },
             members: await fetchProfiles(req.api, uids), admins: [...admins] });
}));

// Members who posted in the group's recent history (= active), newest first.
// Zalo may cap a single history call, so fall back to smaller counts.
app.get("/groups/:id/active", needLogin, wrap(async (req, res) => {
  const want = Math.min(parseInt(req.query.count || "1000", 10) || 1000, 2000);
  let msgs = null, lastErr = null;
  for (const n of [want, 500, 200, 50].filter((n, i, arr) => n <= want && arr.indexOf(n) === i)) {
    try {
      msgs = (await req.api.getGroupChatHistory(req.params.id, n)).groupMsgs || [];
      if (msgs.length) break;
    } catch (e) { lastErr = e; }
  }
  if (msgs === null) throw lastErr || new Error("Không đọc được lịch sử nhóm");
  const admins = await groupAdmins(req.api, req.params.id);
  const seen = new Map();
  let oldest = null;
  for (const m of msgs) {
    const d = m.data || {};
    const ts = parseInt(d.ts, 10) || 0;
    if (ts && (oldest === null || ts < oldest)) oldest = ts;
    const uid = String(d.uidFrom || "");
    if (!uid || uid === "0" || m.isSelf || uid === req.me?.uid || admins.has(uid)) continue;
    const cur = seen.get(uid) || { uid, name: d.dName || "", count: 0, last_ts: 0 };
    cur.count += 1;
    cur.last_ts = Math.max(cur.last_ts, ts);
    seen.set(uid, cur);
  }
  const active = [...seen.values()].sort((x, y) => y.last_ts - x.last_ts);
  const profiles = await fetchProfiles(req.api, active.map((x) => x.uid));
  const members = active.map((x, i) => ({ ...x, name: profiles[i].name || x.name, avatar: profiles[i].avatar }));
  res.json({ members, messages_scanned: msgs.length, oldest_ts: oldest, admins: [...admins] });
}));

// Start receiving group events (new members) on this nick. Idempotent.
app.post("/listen", needLogin, (req, res) => {
  const a = [...accounts.values()].find((x) => x.api === req.api);
  startListener(a);
  res.json({ ok: true, listening: !!a.listening });
});

// Drain buffered join events.
app.get("/joins", (req, res) => {
  res.json(joinQueue.splice(0, joinQueue.length));
});

app.get("/groups/:id/events", needLogin, wrap(async (req, res) => {
  const items = [];
  for (let page = 1; page <= 10; page++) {
    const batch = await req.api.getListReminder({ page, count: 20 }, req.params.id, ThreadType.Group);
    if (!batch?.length) break;
    items.push(...batch);
    if (batch.length < 20) break;
  }
  res.json(items.map((r) => ({
    id: r.id || r.reminderId,
    title: r.params?.title || "",
    start_time: r.startTime,
    accept_count: r.responseMem?.acceptMember ?? null,
    reject_count: r.responseMem?.rejectMember ?? null,
    creator_id: r.creatorId ? String(r.creatorId) : null,
  })));
}));

app.get("/events/:id/responses", needLogin, wrap(async (req, res) => {
  const r = await req.api.getReminderResponses(req.params.id);
  // ?group=<id> drops that group's admins; ?creator=<uid> drops the event's creator.
  const exclude = await groupAdmins(req.api, req.query.group);
  if (req.query.creator) exclude.add(String(req.query.creator));
  const accept = (r.acceptMember || []).filter((u) => u !== req.me?.uid && !exclude.has(String(u)));
  res.json({ accept: await fetchProfiles(req.api, accept), reject_count: (r.rejectMember || []).length,
             admins: [...exclude] });
}));

app.get("/friend-status/:uid", needLogin, wrap(async (req, res) => {
  res.json(await req.api.getFriendRequestStatus(req.params.uid));
}));

app.post("/friend-request", needLogin, wrap(async (req, res) => {
  const { uid, message } = req.body || {};
  if (!uid) return res.status(400).json({ error: "uid required" });
  await req.api.sendFriendRequest(message || "", uid);
  res.json({ ok: true });
}));

app.post("/send", needLogin, wrap(async (req, res) => {
  const { uid, message } = req.body || {};
  if (!uid || !message) return res.status(400).json({ error: "uid and message required" });
  const r = await req.api.sendMessage(message, uid, ThreadType.User);
  res.json({ ok: true, msg_id: r.message?.msgId ?? null });
}));

app.listen(PORT, "127.0.0.1", () => console.log(`zalo-bridge listening on 127.0.0.1:${PORT}`));
restoreSessions();
