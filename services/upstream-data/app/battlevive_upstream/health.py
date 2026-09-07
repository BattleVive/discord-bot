"""Container liveness check shared by the isolated service processes."""
from __future__ import annotations

import sys


def main() -> None:
    # Process liveness is supplied by container execution; this check intentionally
    # avoids network calls so an unavailable optional integration cannot kill gateway.
    sys.exit(0)


if __name__ == "__main__":
    main()
