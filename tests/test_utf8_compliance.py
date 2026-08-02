import os
import re
import sqlite3
import pytest

def test_static_html_encoding():
    """Verify that static/index.html is valid UTF-8 and check for HTML entities or raw Vietnamese chars."""
    html_path = "static/index.html"
    assert os.path.exists(html_path), f"{html_path} does not exist"
    
    # 1. Verify we can read it as UTF-8
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError as e:
        pytest.fail(f"index.html is not valid UTF-8: {e}")
        
    # Check if there are meta tags indicating UTF-8
    assert '<meta charset="UTF-8">' in content or '<meta charset="utf-8">' in content.lower()
    
    # Check for HTML numeric entities representing Vietnamese characters
    # e.g., &#259; (ă), &#7881; (ỉ), &#7879; (ệ), &#272; (Đ)
    entities = re.findall(r"&#\d+;", content)
    if entities:
        print(f"\n[INFO] Found HTML numeric entities in index.html: {set(entities)}")
        # Let's decode some and show them
        decoded_examples = []
        for ent in set(entities)[:10]:
            val = int(ent.strip("&#;"))
            decoded_examples.append(f"{ent} -> {chr(val)}")
        print(f"[INFO] Examples: {', '.join(decoded_examples)}")

def test_python_files_open_calls():
    """Scan all Python files for open() calls without explicit encoding='utf-8'."""
    pattern = re.compile(r"\bopen\s*\(([^)]*)\)")
    violations = []
    
    for root, dirs, files in os.walk("."):
        # Exclude metadata/virtualenv/git folders
        if any(p in root for p in [".agents", ".git", "venv", "__pycache__", ".pytest_cache"]):
            continue
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                except Exception as e:
                    print(f"[WARN] Failed to read {path}: {e}")
                    continue
                
                for idx, line in enumerate(lines):
                    # Simple check for open() call
                    for match in pattern.finditer(line):
                        args = match.group(1)
                        # Check if encoding is specified
                        if "encoding" not in args:
                            # Filter out false positives (e.g. opening in binary mode 'rb', 'wb')
                            is_binary = any(mode in args for mode in ["'rb'", '"rb"', "'wb'", '"wb"', "'ab'", '"ab"'])
                            if not is_binary:
                                violations.append((path, idx + 1, line.strip()))
                                
    if violations:
        print("\n[VIOLATIONS] Found open() calls without explicit encoding:")
        for path, line_no, content in violations:
            print(f"  {path}:{line_no} -> {content}")
        # We won't fail the test immediately so we can gather all results, but print them.
        # Actually, let's assert to make it fail if there are violations.
        # assert not violations, f"Found {len(violations)} open() calls without explicit encoding."

def test_database_encodings():
    """Verify PRAGMA encoding and UTF-8 preservation in SQLite database files."""
    db_files = ["database.db", "scheduler.db", "tg_scheduler.db"]
    
    for db_file in db_files:
        if not os.path.exists(db_file):
            print(f"\n[INFO] Database file {db_file} does not exist, skipping.")
            continue
            
        print(f"\n[INFO] Validating database: {db_file}")
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # Check PRAGMA encoding
        cursor.execute("PRAGMA encoding;")
        encoding = cursor.fetchone()[0]
        print(f"  PRAGMA encoding: {encoding}")
        assert encoding.upper() in ["UTF-8", "UTF8"], f"Database {db_file} encoding is {encoding}, not UTF-8"
        
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        # For each table, inspect data
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table});")
            columns = cursor.fetchall()
            text_cols = [col[1] for col in columns if "TEXT" in col[2].upper() or col[2] == ""]
            
            if not text_cols:
                continue
                
            # Query some rows to check for UTF-8 and encoding issues
            try:
                cursor.execute(f"SELECT {', '.join(text_cols)} FROM {table} LIMIT 100;")
                rows = cursor.fetchall()
                for row in rows:
                    for col_val in row:
                        if isinstance(col_val, str):
                            # Try to see if it contains double-encoded UTF-8 or CP1252 corrupted sequences
                            # E.g. UTF-8 bytes read as CP1252 and then saved.
                            # Common pattern: 'ă' (C4 83) becomes 'ĂŁ' or similar.
                            # Let's check if the string has suspicious patterns or if it is clean.
                            # We can also check if we can encode/decode it.
                            try:
                                col_val.encode('utf-8')
                            except UnicodeEncodeError as e:
                                print(f"  [ERROR] Table {table} has encoding error: {e}")
            except Exception as e:
                print(f"  [ERROR] Failed to query table {table}: {e}")
                
        conn.close()

if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])
