"""Shared helpers for the teaching-resource chapter notebooks.

Modules:
    trace_setup      Per-chapter JSONL tracing to ./traces/chapter_NN.jsonl.
    providers       Unified 4-provider (Kimi/DeepSeek/OpenAI/Grok) factory + comparator.
    finder_loader   HuggingFace FinDER loader with category/random/balanced samplers.
    slide_template  Reveal.js HTML deck builder matching the existing repo style.
"""
