import time, statistics, httpx

key = ""
for line in open(".env", encoding="utf-8"):
    if line.startswith("DASHBOARD_SECRET_KEY="):
        key = line.split("=", 1)[1].strip()
        break

# NOTE: reuse one HTTP connection (keep-alive). Fresh-TCP-per-request clients
# (urllib/requests without a Session) add ~300ms of Windows socket accept cost
# and completely mask the true handler latency.
def bench(client, ep, n=15):
    for _ in range(5):
        client.get(ep)
    ts, sizes = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        r = client.get(ep)
        ts.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(r.content))
    return statistics.median(ts), max(sizes), None

eps = [
    "/api/ai-agents",
    "/api/ai-followup/settings",
    "/api/settings/ai_provider",
    "/api/settings/ai_keys_gemini",
    "/api/settings/ai_keys_deepseek",
    "/api/settings/ai_keys_openai",
    "/api/settings/ai_keys_groq",
    "/api/settings/ai_keys_openai_compatible",
    "/api/ai-followup/kol-channel-settings",
    "/api/settings/command-bot/config",
    "/api/settings/forum-inbox/config",
]
print(f"{'endpoint':48s} {'median':>10s} {'size':>10s}")
print("-" * 72)
with httpx.Client(base_url="http://127.0.0.1:8888", headers={"X-API-Key": key}, timeout=60) as client:
    for ep in eps:
        med, size, err = bench(client, ep)
        if err:
            print(f"{ep:48s} ERR: {err[:24]}")
        else:
            print(f"{ep:48s} {med:8.1f}ms {size:9d}B")
