from pathlib import Path

for path in ["scripts/beads-path-guard.sh", "scripts/gt-doctor.sh", "scripts/task-context-trail.sh"]:
    f = Path(path)
    content = f.read_text()
    content = content.replace(
        '''            raw = raw.rstrip("\\n")
            line = raw.strip()''',
        '''            line = raw.strip()'''
    )
    content = content.replace(
        '''            raw = raw.rstrip("\\n")
                line = raw.strip()''',
        '''            line = raw.strip()'''
    )
    f.write_text(content)
