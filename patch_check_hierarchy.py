import os
from pathlib import Path

p = Path("scripts/ci/check-root-hierarchy-contract.sh")
content = p.read_text()

# We accidentally inserted the block twice.
import re
block = """# .jules/ is untracked EXCEPT .jules/bolt.md and .jules/palette.md. Flag any tracked
# file under .jules/ that is not one of those.
jules_forbidden="$(git ls-files -- ".jules" ":(exclude).jules/bolt.md" ":(exclude).jules/palette.md" 2>/dev/null | head -1)"
if [ -n "$jules_forbidden" ]; then
  echo "Forbidden tracked path under .jules/ (only bolt.md and palette.md may be tracked): $jules_forbidden" >&2
  e""" + """xit 1
fi
"""

# Let's just remove one of the duplicates.
content = content.replace(block + "\n\n" + block, block)

p.write_text(content)
print(f"Patched {p}")
