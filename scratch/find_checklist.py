import os

search_dir = '/Users/gowtham/.gemini/antigravity-ide'
matches = []

for root, dirs, files in os.walk(search_dir):
    for f in files:
        if not f.endswith(('.md', '.txt', '.json', '.py', '.js', '.ts', '.html', '.css', '.sh', '.yml', '.yaml')):
            continue
        full_path = os.path.join(root, f)
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as file_obj:
                content = file_obj.read()
                if 'Milestone 0' in content:
                    matches.append(full_path)
        except Exception:
            pass

print(f"Found {len(matches)} files in App Data Dir containing Milestone 0:")
for m in sorted(matches):
    print(m)
