"""``python -m cheeseless``; the pane relay and operators use the same entry point."""

import sys

from cheeseless.bootstrap import main

raise SystemExit(main(sys.argv[1:]))
