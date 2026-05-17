"""External-benchmark loaders for ``seocho.eval``.

Each submodule wraps a public benchmark (HuggingFace dataset, CSV, ...) and
exposes a uniform sampler surface: ``load()``, ``by_category(name)``,
``sample_random(n)``, ``sample_per_category(n_per)``.

Closes seocho-ci24 — the teaching curriculum's ``_shared/finder_loader.py``
becomes a thin re-export over this module so other downstream users (eval
scripts, demo notebooks) don't have to reinvent the loaders.
"""

from . import finder

__all__ = ["finder"]
