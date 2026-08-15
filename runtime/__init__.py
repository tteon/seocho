"""Canonical runtime deployment shell package.

This package is the long-term replacement for the historically overloaded
``extraction/`` shell. Runtime modules still depend on modules that live under
``extraction/``, and they now import them as ``extraction.<name>``.

This module used to insert both the repository root and ``extraction/`` onto
``sys.path`` so those helpers could be imported under bare top-level names.
That is gone (seocho-60u). It made ``extraction/config.py`` load twice — once
as ``config`` and once as ``extraction.config`` — which Python caches as two
distinct module objects, producing two ``DatabaseRegistry`` classes and two
``db_registry`` singletons. It also hid twenty-eight ``runtime`` →
``extraction`` edges from every static tool, including the boundary checks that
were supposed to police exactly this direction.

Nothing replaced it: ``import runtime`` already requires the repository root on
``sys.path``, which is the same condition ``import extraction`` needs.
"""
