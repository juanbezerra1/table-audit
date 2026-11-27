"""py -m table_audit: the same as the table-audit command."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
