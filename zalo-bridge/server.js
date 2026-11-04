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
const DATA_DIR = path.join(__dirname, "data");
const SESSION_FILE = path.join(DATA_DIR, "session.json");
const PORT = parseInt(process.env.ZALO_BRIDGE_PORT || "8897", 10);
const SECRET = process.env.ZALO_BRIDGE_SECRET || "";

if (!SECRET) {
  console.error("FATAL: ZALO_BRIDGE_SECRET is not set");
  process.exit(1);
}
fs.mkdirSync(DATA_DIR, { recursive: true });

let api = null;          // logged-in zca-js API
let me = null;           // { uid, name, avatar }
let login = { state: "idle", qr: null, scanned: null, error: null };

function saveSession(cred) {
  fs.writeFileSync(SESSION_FILE, JSON.stringify(cred), { mode: 0o600 });
}

async function afterLogin(newApi) {
  api = newApi;
  const uid = api.getOwnId();
  let name = "", avatar = "";
  try {
    const info = await api.getUserInfo(uid);
    const p = info.changed_profiles?.[uid] || info.changed_profiles?.[`${uid}_0`] || {};
    name = p.displayName || p.zaloName || "";
    avatar = p.avatar || "";
  } catch { /* profile is cosmetic */ }
  me = { uid, name, avatar };
  login = { state: "logged_in", qr: null, scanned: null, error: null };
  console.log(`Zalo logged in as ${name || uid}`);
}

async function restoreSession() {
  if (!fs.existsSync(SESSION_FILE)) return;
  try {
    const cred = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));
    await afterLogin(await new Zalo({ selfListen: false, logging: false }).login(cred));
  } catch (e) {
    console.error("Saved Zalo session invalid:", e.message);
    login = { state: "idle", qr: null, scanned: null, error: "Phiên đăng nhập hết hạn, quét QR lại" };
  }
}

function startQrLogin() {
  login = { state: "waiting_qr", qr: null, scanned: null, error: null };
  const zalo = new Zalo({ selfListen: false, logging: false });
  zalo.loginQR({}, (ev) => {
    switch (ev.type) {
      case LoginQRCallbackEventType.QRCodeGenerated:
        login.state = "qr_ready";
        login.qr = ev.data.image; // base64 PNG
        break;
      case LoginQRCallbackEventType.QRCodeExpired:
        login = { state: "expired", qr: null, scanned: null, error: "QR hết hạn" };
        ev.actions.abort();
        break;
      case LoginQRCallbackEventType.QRCodeScanned:
        login.state = "scanned";
        login.scanned = ev.data.display_name;
        break;
      case LoginQRCallbackEventType.QRCodeDeclined:
        login = { state: "declined", qr: null, scanned: null, error: "Đã từ chối trên điện thoại" };
        ev.actions.abort();
        break;
      case LoginQRCallbackEventType.GotLoginInfo:
        saveSession({ cookie: ev.data.cookie, imei: ev.data.imei, userAgent: ev.data.userAgent });
        break;
    }
  })
    .then(afterLogin)
    .catch((e) => {
      if (login.state !== "expired" && login.state !== "declined") {
        login = { state: "error", qr: null, scanned: null, error: e.message };
      }
    });
}

// Zalo throws for a single unknown uid inside a batch; fetch profiles in chunks.
async function fetchProfiles(uids) {
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

const app = express();
app.use(express.json());
app.use((req, res, next) => {
  if (req.get("X-Bridge-Secret") !== SECRET) return res.status(403).json({ error: "forbidden" });
  next();
});
const needLogin = (req, res, next) => (api ? next() : res.status(409).json({ error: "Zalo chưa đăng nhập" }));
const wrap = (fn) => (req, res) => fn(req, res).catch((e) => {
  console.error(req.path, e);
  res.status(502).json({ error: e.message || String(e), code: e.code ?? null });
});

app.get("/status", (req, res) => res.json({ logged_in: !!api, me, login }));

app.post("/login/qr", (req, res) => {
  if (api) return res.json({ ok: true, already: true });
  if (!["waiting_qr", "qr_ready", "scanned"].includes(login.state)) startQrLogin();
  res.json({ ok: true });
});

app.post("/logout", (req, res) => {
  api = null; me = null;
  login = { state: "idle", qr: null, scanned: null, error: null };
  if (fs.existsSync(SESSION_FILE)) fs.unlinkSync(SESSION_FILE);
  res.json({ ok: true });
});

app.get("/groups", needLogin, wrap(async (req, res) => {
  const all = await api.getAllGroups();
  const ids = Object.keys(all.gridVerMap || {});
  const groups = [];
  for (let i = 0; i < ids.length; i += 50) {
    const info = await api.getGroupInfo(ids.slice(i, i + 50));
    for (const [id, g] of Object.entries(info.gridInfoMap || {})) {
      groups.push({ id, name: g.name, avatar: g.avt || g.fullAvt || "", total: g.totalMember || 0 });
    }
  }
  res.json(groups.sort((a, b) => b.total - a.total));
}));

app.get("/groups/:id/members", needLogin, wrap(async (req, res) => {
  const info = await api.getGroupInfo(req.params.id);
  const g = info.gridInfoMap?.[req.params.id];
  if (!g) return res.status(404).json({ error: "Không tìm thấy nhóm" });
  // memVerList entries look like "<uid>_<version>"
  const uids = [...new Set((g.memVerList || []).map((s) => s.split("_")[0]).concat(g.memberIds || []))]
    .filter((u) => u && u !== me?.uid);
  res.json({ group: { id: req.params.id, name: g.name, total: g.totalMember }, members: await fetchProfiles(uids) });
}));

app.get("/groups/:id/events", needLogin, wrap(async (req, res) => {
  const items = [];
  for (let page = 1; page <= 10; page++) {
    const batch = await api.getListReminder({ page, count: 20 }, req.params.id, ThreadType.Group);
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
  })));
}));

app.get("/events/:id/responses", needLogin, wrap(async (req, res) => {
  const r = await api.getReminderResponses(req.params.id);
  const accept = (r.acceptMember || []).filter((u) => u !== me?.uid);
  res.json({ accept: await fetchProfiles(accept), reject_count: (r.rejectMember || []).length });
}));

app.get("/friend-status/:uid", needLogin, wrap(async (req, res) => {
  res.json(await api.getFriendRequestStatus(req.params.uid));
}));

app.post("/friend-request", needLogin, wrap(async (req, res) => {
  const { uid, message } = req.body || {};
  if (!uid) return res.status(400).json({ error: "uid required" });
  await api.sendFriendRequest(message || "", uid);
  res.json({ ok: true });
}));

app.post("/send", needLogin, wrap(async (req, res) => {
  const { uid, message } = req.body || {};
  if (!uid || !message) return res.status(400).json({ error: "uid and message required" });
  const r = await api.sendMessage(message, uid, ThreadType.User);
  res.json({ ok: true, msg_id: r.message?.msgId ?? null });
}));

app.listen(PORT, "127.0.0.1", () => console.log(`zalo-bridge listening on 127.0.0.1:${PORT}`));
restoreSession();
