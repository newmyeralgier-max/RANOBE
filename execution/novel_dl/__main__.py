"""Allow ``python -m novel_dl`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
