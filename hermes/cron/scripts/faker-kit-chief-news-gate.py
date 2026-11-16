import sys

import faker_kit_gate_news as news

KIT_OWNED = True

lines = news.pending_lines()
if lines:
    sys.stdout.write("\n".join(lines) + "\n")
