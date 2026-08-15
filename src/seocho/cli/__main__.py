"""Keep ``python -m seocho.cli`` working after the module became a package."""

from . import main

raise SystemExit(main())
