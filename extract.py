import re

with open('database.py', 'r', encoding='utf-8') as f:
    content = f.read()

funcs = ['get_analytics_overview', 'get_all_schedules', 'get_analytics_account_health']
for func in funcs:
    match = re.search(rf'async def {func}\b', content)
    if match:
        start = match.start()
        print(f"--- {func} ---")
        lines = content[start:].split('\n')[:80]
        print('\n'.join(lines))
