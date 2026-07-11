import seocho.tracing as tracing


class _Context:
    trace_id = int("0123456789abcdef0123456789abcdef", 16)


class _NativeSpan:
    def get_span_context(self):
        return _Context()


class _NativeBackend(tracing.TracingBackend):
    def log_span(self, name, **kwargs):
        return None

    def open_span(self, name, attributes=None):
        return _NativeSpan()

    def close_span(self, span, **kwargs):
        return None


def test_root_handle_uses_native_otel_trace_id() -> None:
    tracing.enable_tracing(backend=_NativeBackend())
    try:
        with tracing.start_span("root") as root:
            assert root.trace_id == "0123456789abcdef0123456789abcdef"
            with tracing.start_span("child") as child:
                assert child.trace_id == root.trace_id
    finally:
        tracing.disable_tracing()
