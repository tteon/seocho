"""Legacy extraction service.

This is a real package now. It was not one before: there was no `__init__.py`,
and `runtime/__init__.py` put this directory on `sys.path` so every module here
was importable under a bare top-level name. The consequence was not cosmetic —
`extraction/config.py` loaded twice, as `config` and as `extraction.config`,
which Python caches as two distinct module objects:

    flat.__file__ == pkg.__file__                   -> True
    flat is pkg                                     -> False
    flat.db_registry is pkg.db_registry             -> False
    flat.DatabaseRegistry is pkg.DatabaseRegistry   -> False

Two `DatabaseRegistry` classes and two `db_registry` singletons, so any process
touching both spellings got two connection registries and cross-registry
`isinstance` failures. Both spellings were in tracked code (seocho-60u).

Making it a package also makes the dependency graph legible: five `runtime/`
modules import twenty-eight modules from here, and under bare names none of
those edges were visible to grep or to an AST tool.

Per `CLAUDE.md` this stays a compatibility surface — legacy service behaviour
lives here, new canonical logic does not.
"""
