# Shapes-as-source pipeline (`seocho ont`)

SHACL shapes are the single source the cache layer derives everything from:
vocabulary, the class→relationship path index, and the `class:*` address
space. This directory is the smallest working example.

```bash
pip install 'seocho[ontology]'            # rdflib

seocho ont fmt shapes/                     # canonical Turtle (diffable, hashable)
seocho ont lint shapes/                    # targetClass/path sanity, canonical check
seocho ont build shapes/ --out build --lock seocho.lock
seocho ont verify shapes/ --lock seocho.lock
seocho ont blast-radius shapes/ --change proposed-shapes/
```

`seocho.lock`'s `active_hash` covers the whole lockfile — shapes, every
derived artifact, and the tool version — so a compiler upgrade with identical
shapes still reads as a change. That one string is the system-wide "did the
ontology world move" token (design v0.3 §O.4).

Formatting does not move hashes: `source_hash` is computed over the
canonical graph, so `ont fmt` before or after `ont build` verifies clean.
