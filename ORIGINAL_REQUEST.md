# Original User Request

## Initial Request — 2026-07-12T21:38:22Z

Optimize the tg-scheduler Telegram automation platform for maximum end-user performance and KISS experience. The app is a FastAPI + SQLite + vanilla JS single-page application serving 16+ Telegram accounts with keyword watchers, DM campaigns, reactions, analytics, and auto-reply features.

Working directory: c:\Users\DELL\.gemini\antigravity\playground\ecliptic-universe\tg-scheduler
Integrity mode: development

## Performance Audit Findings (Research Results)

The following critical issues were identified:

### Database Layer (26 HIGH/MEDIUM issues)
- **N+1 query patterns** in `get_all_schedules()`, `get_active_schedules()`, `get_all_watchers()`, `get_active_watchers()` — each loops with 2-3 sub-queries per row
- **Analytics N+1 explosion**: `get_analytics_account_health()` runs **6 queries × N accounts** on every request; `get_analytics_overview()` runs **11 sequential COUNT queries**
- **No connection pooling** — every function opens a new `aiosqlite.connect()`
- **Missing indexes** on `send_logs` (schedule_id, account_id, status, sent_at), `reaction_logs` (target_id, account_id), `schedule_messages/targets` (schedule_id FK)
- **Row-by-row INSERT** in `save_scraped_members()` instead of `executemany()`

### Frontend (208KB unminified JS)
- **Campaign polling every 10s** re-fetches ALL campaigns + full re-render
- **Dashboard overfetch** — loads ALL schedules when only showing top 10 active
- **No debounce** on chat search filters
- **Redundant API calls** — accounts fetched independently 4+ times across pages with no shared cache
- **Deep crawl polling** at 3s intervals with no backoff

### Backend Routes
- **CSV exports** load up to 100K rows into memory
- **Analytics endpoints** have no caching (heavy queries re-run on every request)
- **Watcher operations** fetch full watcher data just for existence checks

### Static Serving
- **No GZip compression** — 208KB JS + 69KB HTML served raw
- **No cache headers** — every page load re-downloads everything
- **No minification** on any JS or CSS files

## Requirements

### R1. Database Query Optimization
Eliminate N+1 query patterns across the codebase. Consolidate multi-query analytics functions into efficient single queries. Add missing indexes on frequently-queried columns. Implement a shared connection approach for SQLite to reduce per-function connection overhead.

### R2. Frontend Performance & UX
Reduce unnecessary API calls through centralized caching (especially accounts). Add debounce/throttle to search filters and polling mechanisms. Replace full-table re-renders with targeted updates for campaign polling. Reduce data overfetch on dashboard.

### R3. Backend Response Optimization
Add server-side in-memory caching (TTL-based) for slowly-changing analytics data. Stream CSV exports instead of buffering in memory. Optimize watcher/schedule endpoints to avoid loading unnecessary data.

### R4. Static Asset Delivery
Add GZip compression middleware. Set proper Cache-Control headers for static assets. The JS/CSS files should be delivered compressed to reduce the 208KB+ payload.

### R5. Data Integrity During Optimization
All database schema changes must be backward-compatible. No existing functionality should break. All Vietnamese text in the UI must be preserved correctly (previous encoding corruption was caused by PowerShell — avoid using PowerShell Set-Content for file writes).

## Acceptance Criteria

### Database Performance
- [ ] Zero N+1 query patterns in schedule, watcher, and analytics functions
- [ ] Analytics overview endpoint uses ≤3 queries instead of 11
- [ ] Account health endpoint uses ≤2 queries instead of 6×N
- [ ] All foreign key columns have indexes (send_logs, reaction_logs, schedule_messages, schedule_targets)
- [ ] `save_scraped_members()` uses batch INSERT (`executemany`)
- [ ] SQLite connection reuse — no per-function `aiosqlite.connect()` overhead

### Frontend Performance
- [ ] Accounts data cached client-side — max 1 API call per page session
- [ ] Campaign polling fetches only changed campaign data, not full list
- [ ] Search filters debounced (≥250ms)
- [ ] Dashboard loads only active/recent schedules (not all)
- [ ] Deep crawl polling implements exponential backoff

### Backend & Static
- [ ] Analytics endpoints cache results for ≥30 seconds
- [ ] CSV exports use streaming response (not in-memory buffer)
- [ ] GZip middleware enabled for all responses
- [ ] Static files served with `Cache-Control: public, max-age=3600` minimum
- [ ] Server starts and all existing features work correctly after changes

### Regression Safety
- [ ] All existing API endpoints return the same data structure
- [ ] File encoding is UTF-8 throughout (no PowerShell Set-Content)
- [ ] Server boots successfully with all 16 accounts connecting
- [ ] Keyword watchers, DM campaigns, reactions, analytics, auto-reply all functional
