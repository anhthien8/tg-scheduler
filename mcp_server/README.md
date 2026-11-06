# TG Scheduler MCP server

Cho AI agent (Claude Code/Desktop, Codex, Antigravity, Hermes, OpenClaw, harness DeepSeek…) điều khiển TG Scheduler qua tool MCP. Server chạy **stdio**, chỉ gọi HTTP API của app local (`http://127.0.0.1:8888`), không đụng DB hay Telethon trực tiếp.

## Mô hình an toàn
- **Không có tool "start"**: tool ghi chỉ tạo **campaign nháp (draft)**. Muốn chạy thì người bấm Start trên dashboard.
- `pause_campaign` chỉ dừng; không có tool resume.
- Thông tin account trả về đã bỏ `api_hash`, `api_id`, `phone`, `proxy_url`, `session_name`.
- Sender không khỏe (lỗi session, offline, tạm dừng, bị flag, PeerFlood, SpamBot giới hạn) bị từ chối khi tạo campaign.
- Danh sách mục tiêu tự loại admin/owner (lúc cào), bot và **account nội bộ** (không bao giờ nhắn qua lại giữa account của mình). Lúc chạy, backend còn loại blacklist và người đã từng được DM.
- Cache TTL dài (account 60s, SpamBot 10 phút, danh sách nhóm 30 phút); chờ cào dùng backoff 3s→15s, tối đa 300s.
- API key không bao giờ in ra log hay trả về tool.

## Tools
| Tool | Việc làm |
|---|---|
| `list_accounts(only_healthy, refresh)` | Account + trạng thái sức khỏe giống tab Engagement, Premium, SpamBot |
| `find_groups(title_contains, account_id, refresh)` | Tìm nhóm theo tên trong dialog của các account, kèm số member và account nào đang ở trong nhóm |
| `scrape_group_members(account_id, group_id, group_title, filter_active_days, method, wait_seconds)` | Cào member (chỉ đọc), loại admin/bot; hỗ trợ cả basic group lẫn supergroup |
| `list_scrape_jobs()` | Các lần cào đã có |
| `preview_targets(scrape_job_id, exclude_keywords, exclude_user_ids, exclude_usernames, require_username, only_premium)` | **Dry-run**: ai giữ, ai bị loại, lý do |
| `create_target_list(...)` | Lưu danh sách đã lọc thành scrape job mới (dùng làm nguồn campaign) |
| `create_dm_campaign_draft(name, scrape_job_id, sender_account_ids, messages, delay_min, delay_max, daily_limit_premium, daily_limit_normal, use_ai_remix, exclude_previous_dms)` | Tạo campaign **draft**, trả về sức chứa/ngày và số ngày ước tính |
| `list_campaigns(status, limit)` | Danh sách campaign |
| `campaign_progress(campaign_id, log_limit)` | Tiến độ + log gửi gần nhất |
| `pause_campaign(campaign_id)` | Dừng campaign đang chạy |

## Cấu hình chung
Cần: app TG Scheduler đang chạy, Python có `mcp` và `httpx` (`C:/Python314/python.exe -m pip install mcp httpx`).

Biến môi trường (tùy chọn):
| Biến | Mặc định | Ghi chú |
|---|---|---|
| `TGS_BASE_URL` | `http://127.0.0.1:8888` | URL app |
| `TGS_API_KEY` | — | Nếu không đặt, đọc `DASHBOARD_SECRET_KEY` từ env hoặc file `.env` của repo |
| `TGS_ENV_FILE` | `<repo>/.env` | File .env khác |

Thường **không cần đặt gì**: server tự đọc key từ `.env` của repo, nên không phải dán key vào file config của client.

Lệnh chạy: `C:/Python314/python.exe H:/Hermes/tg-scheduler/mcp_server/server.py`

### Claude Code
```bash
claude mcp add tg-scheduler --scope user -- C:/Python314/python.exe H:/Hermes/tg-scheduler/mcp_server/server.py
```
Kiểm tra: `claude mcp list`. Muốn đổi URL thì thêm `-e TGS_BASE_URL=http://127.0.0.1:8888` trước `--`.

### Claude Desktop
`%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tg-scheduler": {
      "command": "C:/Python314/python.exe",
      "args": ["H:/Hermes/tg-scheduler/mcp_server/server.py"]
    }
  }
}
```
Khởi động lại Claude Desktop.

### Codex (CLI / IDE)
`~/.codex/config.toml` (Windows: `C:\Users\<user>\.codex\config.toml`):
```toml
[mcp_servers.tg-scheduler]
command = "C:/Python314/python.exe"
args = ["H:/Hermes/tg-scheduler/mcp_server/server.py"]
startup_timeout_sec = 20
tool_timeout_sec = 330   # scrape_group_members có thể chờ tới 300s

# [mcp_servers.tg-scheduler.env]
# TGS_BASE_URL = "http://127.0.0.1:8888"
```
Hoặc: `codex mcp add tg-scheduler -- C:/Python314/python.exe H:/Hermes/tg-scheduler/mcp_server/server.py`

### Antigravity
Agent panel → `…` → **MCP Servers** → **Manage MCP Servers** → **View raw config** (`mcp_config.json`), thêm cùng khối JSON như Claude Desktop:
```json
{
  "mcpServers": {
    "tg-scheduler": {
      "command": "C:/Python314/python.exe",
      "args": ["H:/Hermes/tg-scheduler/mcp_server/server.py"]
    }
  }
}
```

### Hermes / OpenClaw / harness DeepSeek / client stdio khác
Mọi client MCP stdio đều cần 3 thứ: `command` = `C:/Python314/python.exe`, `args` = `["H:/Hermes/tg-scheduler/mcp_server/server.py"]`, `env` (tùy chọn) như bảng trên. Phần lớn dùng chung dạng `mcpServers` JSON ở trên. Với harness tự viết bằng Python SDK:
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
params = StdioServerParameters(command="C:/Python314/python.exe",
                               args=["H:/Hermes/tg-scheduler/mcp_server/server.py"])
async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
    await s.initialize()
    print(await s.call_tool("list_accounts", {"only_healthy": True}))
```
Model không hỗ trợ tool calling (một số endpoint DeepSeek reasoning) thì cần harness làm cầu nối: harness gọi tool, đưa kết quả vào prompt.

## Ví dụ: mời KOL WEEX từ nhóm "WEEX SEA Offline meetup"
Prompt mẫu cho agent:
> Dùng tg-scheduler: tìm nhóm "WEEX SEA Offline meetup", cào member, preview loại người có "weex" trong tên/username và account nội bộ, chỉ giữ người có username. Cho tôi xem danh sách bị loại. Sau đó tạo campaign nháp mời KOL bằng các account khỏe (ưu tiên Premium), delay 5–12 phút, tối đa 15 tin/ngày với Premium. KHÔNG start.

Luồng:
1. `list_accounts(only_healthy=true)` → chọn sender khỏe, ưu tiên Premium.
2. `find_groups("WEEX SEA")` → lấy `group_id` và account đang ở trong nhóm (account này chỉ dùng để cào, không cần khỏe để gửi).
3. `scrape_group_members(...)` → admin/owner, bot đã bị loại.
4. `preview_targets(job, exclude_keywords=["weex"], require_username=true)` → người xem lại danh sách loại/giữ; loại thêm nhân sự WEEX không có chữ "weex" trong tên bằng `exclude_usernames`.
5. `create_target_list(...)` → job đã lọc.
6. `create_dm_campaign_draft(...)` → campaign **draft**; xem lại trên dashboard rồi tự bấm Start.

Nhịp gửi gợi ý cho nhóm nhỏ: delay 300–720s, Premium ≤15/ngày, thường ≤5/ngày, chia đều 2–3 sender; tin nhắn dùng spintax `{Hi|Hello}` + `{first_name}`, không link ở tin đầu.
