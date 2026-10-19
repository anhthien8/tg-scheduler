# Design Doc — C4: Self-Learning 2.0 (Embedding-Based)

> **Mục tiêu:** Nâng cấp knowledge retrieval từ token-overlap sang embedding-based similarity, thêm duplicate merging, và auto-clustering.
> **Trạng thái:** Design → Implement
> **Ngày:** 2026-10-18

---

## 0. TL;DR

- **Hiện trạng:** `database.py:5300-5347` dùng `_rule_tokens()` (lowercased alphanumeric tokens) và đếm overlap >= 2 để rank relevant rules. Comment tại line 5316 ghi rõ: *"upgrade path = embeddings/FTS5 if quality lags."*
- **Vấn đề:** Token overlap không capture semantic meaning. "Phí giao dịch WEEX bao nhiêu?" và "Trading fee là bao nhiêu?" overlap rất ít token nhưng cùng ý nghĩa.
- **Đề xuất:** Phase 1: SQLite FTS5 full-text search. Phase 2 (tùy chọn): embedding vectors nếu FTS5 chưa đủ.

---

## 1. Architecture Decision: FTS5 trước, Embeddings sau

### Lý do chọn FTS5 trước:
1. **Zero dependencies** — SQLite FTS5 đã built-in, không cần thêm library
2. **Nhanh** — FTS5 BM25 ranking tốt cho Vietnamese/English mixed text
3. **Đủ tốt** cho use case hiện tại (100-500 rules)
4. **Embeddings** cần thêm model (sentence-transformers), storage, và compute

### Upgrade path:
- FTS5 → sử dụng ngay, test quality
- Nếu quality lag → thêm embedding layer song song
- FTS5 vẫn giữ làm fast pre-filter trước embedding similarity

---

## 2. Phase 1: FTS5 Full-Text Search

### 2.1 Schema

```sql
-- Virtual table for full-text search on learned knowledge
CREATE VIRTUAL TABLE IF NOT EXISTS agent_learned_knowledge_fts 
USING fts5(
    question, 
    answer, 
    content='agent_learned_knowledge', 
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS learned_knowledge_ai AFTER INSERT ON agent_learned_knowledge
BEGIN
    INSERT INTO agent_learned_knowledge_fts(rowid, question, answer)
    VALUES (new.id, new.question, new.answer);
END;

CREATE TRIGGER IF NOT EXISTS learned_knowledge_ad AFTER DELETE ON agent_learned_knowledge
BEGIN
    INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts, rowid, question, answer)
    VALUES ('delete', old.id, old.question, old.answer);
END;

CREATE TRIGGER IF NOT EXISTS learned_knowledge_au AFTER UPDATE ON agent_learned_knowledge
BEGIN
    INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts, rowid, question, answer)
    VALUES ('delete', old.id, old.question, old.answer);
    INSERT INTO agent_learned_knowledge_fts(rowid, question, answer)
    VALUES (new.id, new.question, new.answer);
END;
```

### 2.2 Retrieval Function

```python
async def get_relevant_learned_knowledge_v2(
    self,
    agent_name: str,
    prospect_query: str,
    limit: int = 20,
) -> list[dict]:
    """FTS5-based knowledge retrieval with BM25 ranking."""
    
    # 1. FTS5 search (semantic matching)
    fts_results = await self.execute_fetchall(
        """
        SELECT k.id, k.question, k.answer, k.created_at,
               bm25(agent_learned_knowledge_fts) AS rank
        FROM agent_learned_knowledge_fts fts
        JOIN agent_learned_knowledge k ON k.id = fts.rowid
        WHERE agent_learned_knowledge_fts MATCH ?
          AND k.agent_name = ?
          AND k.status = 'approved'
        ORDER BY rank
        LIMIT ?
        """,
        (prospect_query, agent_name, limit)
    )
    
    # 2. Fallback: recent rules (always include top 5 newest)
    recent = await self.execute_fetchall(
        """
        SELECT id, question, answer, created_at
        FROM agent_learned_knowledge
        WHERE agent_name = ? AND status = 'approved'
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (agent_name,)
    )
    
    # 3. Merge, deduplicate by id
    seen_ids = set()
    results = []
    for row in fts_results:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            results.append(row)
    for row in recent:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            results.append(row)
    
    return results[:limit]
```

### 2.3 FTS5 Migration — Rebuild Index

```python
async def _rebuild_fts_index(self):
    """One-time rebuild FTS index from existing data."""
    await self.execute(
        "INSERT INTO agent_learned_knowledge_fts(agent_learned_knowledge_fts) VALUES('rebuild')"
    )
```

---

## 3. Duplicate Detection & Merging

### 3.1 Problem

Admin answers the same question multiple times → duplicate rules. Example:
- Rule 1: Q="phí giao dịch?" A="WEEX phí 0.02%"
- Rule 2: Q="trading fee bao nhiêu?" A="WEEX charges 0.02%"

### 3.2 Solution: FTS5 similarity check before insert

```python
async def add_learned_knowledge_with_dedup(
    self, agent_name: str, question: str, answer: str
) -> tuple[int, bool]:
    """Add knowledge, auto-merging with existing similar rules.
    Returns (rule_id, is_new)."""
    
    # 1. Search for similar existing rules
    similar = await self.execute_fetchall(
        """
        SELECT k.id, k.question, k.answer,
               bm25(agent_learned_knowledge_fts) AS rank
        FROM agent_learned_knowledge_fts fts
        JOIN agent_learned_knowledge k ON k.id = fts.rowid
        WHERE agent_learned_knowledge_fts MATCH ?
          AND k.agent_name = ? AND k.status = 'approved'
        ORDER BY rank
        LIMIT 3
        """,
        (question, agent_name)
    )
    
    # 2. If top result has very high similarity (BM25 rank < threshold)
    if similar and abs(similar[0]["rank"]) > 5.0:
        # High similarity → update existing rule with newer answer
        existing_id = similar[0]["id"]
        await self.execute(
            """UPDATE agent_learned_knowledge 
               SET answer = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (answer, existing_id)
        )
        return existing_id, False
    
    # 3. No duplicate → insert new
    row_id = await self.execute_insert(
        """INSERT INTO agent_learned_knowledge 
           (agent_name, question, answer, status)
           VALUES (?, ?, ?, 'approved')""",
        (agent_name, question, answer)
    )
    return row_id, True
```

---

## 4. Auto-Clustering (v2.1 — Future)

> Deferred: Khi số rules > 200, thêm clustering layer:
> - Group similar Q&A pairs vào clusters
> - UI hiện cluster tree thay vì flat list
> - Admin có thể merge/split clusters

---

## 5. Phase 2 (Optional): Embedding Vectors

### 5.1 Khi nào cần

- FTS5 chất lượng kém cho multilingual queries
- Cross-language matching cần thiết (EN question → VN answer)
- Số rules > 500

### 5.2 Implementation Sketch

```python
# Use sentence-transformers (multilingual-e5-small, 384 dims)
# Store embeddings in new column:
# ALTER TABLE agent_learned_knowledge ADD COLUMN embedding BLOB;
# 
# Query: compute query embedding → cosine similarity search
# SQLite doesn't have native vector ops → use numpy in Python
```

---

## 6. Code Changes Summary

| File | Change |
|------|--------|
| `database.py` | FTS5 table + triggers, `get_relevant_learned_knowledge_v2()`, `add_learned_knowledge_with_dedup()`, `_rebuild_fts_index()` |
| `dm_reply_tracker.py` | Switch call from `get_relevant_learned_knowledge` → `_v2` |
| `ai_remix.py` | No change (receives knowledge list, format unchanged) |

---

## 7. Test Plan

| Test | Expected |
|------|----------|
| `test_fts5_semantic_match` | "phí giao dịch" matches "trading fee" rule |
| `test_fts5_bm25_ranking` | Most relevant rule ranked first |
| `test_dedup_similar_question` | Similar Q → updates existing, not duplicate |
| `test_dedup_different_question` | Different Q → creates new |
| `test_fallback_recent_rules` | Always includes 5 newest rules |
| `test_rebuild_fts_index` | Existing rules get indexed |
| `test_trigger_sync_insert` | New rule auto-added to FTS |
| `test_trigger_sync_delete` | Deleted rule removed from FTS |

---

## 8. Migration

1. Create FTS5 virtual table + triggers (in `ensure_tables`)
2. Run `_rebuild_fts_index()` once to index existing rules
3. No data loss — FTS5 is a read-only index layer
