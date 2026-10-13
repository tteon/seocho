from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from .config import RecorderConfig
from .store import RecorderStore

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve(config: Annotated[Path, typer.Option()], host: str = '127.0.0.1', port: int = 8800) -> None:
    import uvicorn
    import yaml
    from .proxy import create_app
    data = yaml.safe_load(config.read_text())
    key_env = data.pop('upstream_api_key_env', None)
    if key_env:
        if not os.getenv(key_env):
            raise typer.BadParameter(f'Missing upstream credential environment: {key_env}')
        data['upstream_api_key'] = os.environ[key_env]
    settings = RecorderConfig.model_validate(data)
    uvicorn.run(create_app(settings), host=host, port=port)


@app.command()
def mock(host: str = '127.0.0.1', port: int = 8801) -> None:
    import uvicorn
    from .mock_backend import create_mock_app
    uvicorn.run(create_mock_app(), host=host, port=port)


@app.command()
def inspect(call: Annotated[str, typer.Option()], directory: Path = Path('outputs/agent-observability'),
            domain: str = 'inference') -> None:
    store = RecorderStore(directory, read_only=True)
    try:
        record = next((c for c in store.calls(domain=domain) if c['call_id'] == call), None)
        if record is None:
            raise typer.BadParameter('Call not found in selected domain')
        bodies = {stage: store.blobs.get(data[key], domain).decode(errors='replace')
                  for stage, key in [('start', 'request_blob'), ('finish', 'response_blob')]
                  if (data := record.get(stage)) and data.get(key)}
        typer.echo(json.dumps({'record': record, 'bodies': bodies}, ensure_ascii=False, indent=2))
    finally:
        store.close()


@app.command()
def audit(run: Annotated[str, typer.Option()], directory: Path = Path('outputs/agent-observability'),
          manifest: Path | None = None) -> None:
    store = RecorderStore(directory, read_only=True)
    try:
        expected = None
        if manifest:
            data = json.loads(manifest.read_text())
            entries = data if isinstance(data, list) else data['attempts']
            expected = [a['call_id'] for a in entries if a['run_id'] == run]
        result = store.audit(run, expected)
        typer.echo(json.dumps(result, indent=2))
        if not result['valid']:
            raise typer.Exit(1)
    finally:
        store.close()


@app.command()
def audit_events(run: Annotated[str, typer.Option()], directory: Path = Path('outputs/agent-observability')) -> None:
    store = RecorderStore(directory, read_only=True)
    try:
        result = store.audit_agent_events(run)
        typer.echo(json.dumps(result, indent=2))
        if not result['valid']:
            raise typer.Exit(1)
    finally:
        store.close()


@app.command()
def inspect_events(run: Annotated[str, typer.Option()], directory: Path = Path('outputs/agent-observability'),
                   domain: str = 'inference') -> None:
    store = RecorderStore(directory, read_only=True)
    try:
        events = [dict(e, payload=json.loads(store.blobs.get(e['payload_blob'], domain)))
                  for e in store.agent_events(run) if e['domain'] == domain]
        typer.echo(json.dumps(events, ensure_ascii=False, indent=2))
    finally:
        store.close()


@app.command()
def signals(workspace: Annotated[str, typer.Option()], output: Annotated[Path, typer.Option()],
            directory: Path = Path('outputs/agent-observability'), config: Path | None = None,
            embedding_vectors: Path | None = None) -> None:
    from seocho.eval.agent_observability.signal_store import analyze
    from seocho.eval.agent_observability.signals import SignalConfig
    import yaml
    settings = SignalConfig.model_validate(yaml.safe_load(config.read_text())) if config else SignalConfig()
    from seocho.eval.agent_observability.encoders import PrecomputedEncoder
    encoder = PrecomputedEncoder(embedding_vectors) if embedding_vectors else None
    typer.echo(json.dumps(analyze(directory,output,workspace,config=settings,encoder=encoder),ensure_ascii=False,indent=2))


@app.command()
def inspect_signals(job: Annotated[str, typer.Option()], kind: Annotated[str, typer.Option()],
                    database: Annotated[Path, typer.Option()]) -> None:
    import duckdb
    db = duckdb.connect(str(database),read_only=True)
    try:
        rows = [json.loads(data) for data, in db.execute(
            'SELECT data FROM signal_rows WHERE job_id=? AND kind=? ORDER BY ordinal',[job,kind]).fetchall()]
        typer.echo(json.dumps(rows,ensure_ascii=False,indent=2))
    finally:
        db.close()


@app.command()
def report(experiment: Path) -> None:
    from seocho.eval.agent_observability.report import report_from_yaml
    typer.echo(json.dumps(report_from_yaml(experiment),ensure_ascii=False,indent=2))


@app.command()
def export(output: Annotated[Path, typer.Option()], directory: Path = Path('outputs/agent-observability')) -> None:
    store = RecorderStore(directory, read_only=True)
    try:
        store.export(output)
        typer.echo(str(output))
    finally:
        store.close()


@app.command()
def ui(workspace: Annotated[str, typer.Option()], directory: Path = Path('outputs/agent-observability'),
       experiment: Path | None = None, job_output: Path = Path('outputs/agent-observability-ui-jobs'),
       origin_recorder_config: Path | None = None, port: int = 8810) -> None:
    """Inspect offline Recorder records; optional isolated Mock replay jobs."""
    import uvicorn
    from .ui import create_ui
    uvicorn.run(create_ui(directory,workspace,experiment=experiment,job_output=job_output,origin_recorder_config=origin_recorder_config),host='127.0.0.1',port=port)
