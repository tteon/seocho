"""Loopback-only read UI; explicit Mock intervention jobs use separate stores."""
from __future__ import annotations

import asyncio
import json
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from seocho.eval.agent_observability.config import Experiment, load_experiment
from seocho.eval.agent_observability.harness import Case, Harness
from seocho.eval.agent_observability.intervention import (CostGuard, CostLimits,
    Intervention, load_snapshot, run_intervention)

from .config import RecorderConfig
from .mock_backend import create_mock_app
from .proxy import create_app
from .views import Views
from .store import RecorderStore


class JobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str
    workspace_id: str
    intervention: Intervention
    samples: int = Field(default=1,ge=1,le=8)
    max_tokens: int = Field(default=100_000,gt=0,le=1_000_000)


class MockJobs:
    def __init__(self, source: Views, experiment: Experiment | None, output: Path, origin_url: str | None=None) -> None:
        self.source, self.experiment, self.output = source, experiment, output
        self.origin_url = origin_url
        self.jobs: dict[str,dict[str,Any]] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        if output.resolve()==source.directory.resolve() or source.directory.resolve() in output.resolve().parents:
            raise ValueError('UI jobs must be separate from source Recorder directory')

    def submit(self, request: JobRequest) -> str:
        if not self.experiment or request.workspace_id!=self.source.workspace_id or self.experiment.workspace_id!=request.workspace_id:
            raise ValueError('Matching experiment configuration required for Mock jobs')
        source = next((r for r in self.source.index if r['run_id']==request.run_id),None)
        if self.experiment.harness_backend != 'openai_agents':
            raise ValueError('UI jobs require the in-process SDK Mock backend')
        if not source or not source['mock'] or not all(v=='mock-fixed' for v in self.experiment.models.values()):
            raise ValueError('UI execution supports recorded mock-fixed runs only; paid providers are disabled')
        if any(job['status'] in ('queued','running') for job in self.jobs.values()):
            raise ValueError('A Mock job is already running')
        job_id = uuid4().hex
        self.jobs[job_id] = {'job_id':job_id,'status':'queued','run_id':request.run_id,'request':request.model_dump(mode='json')}
        task = asyncio.create_task(self.execute(job_id,request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job_id

    async def execute(self, job_id: str, request: JobRequest) -> None:
        job = self.jobs[job_id]
        job['status'] = 'running'
        directory = self.output/job_id/'store'
        job['directory'] = str(directory)
        proxy, harness = None,None
        try:
            # Source is immutable/offline. Copy to an isolated branch so pinned
            # origins can be validated by Phase 3 without writing the source DB.
            shutil.copytree(self.source.directory,directory)
            source_store = RecorderStore(directory,read_only=True)
            try:
                source_calls = source_store.calls(run_id=request.run_id,domain='inference')
                namespaces = {c['start']['namespace'] for c in source_calls}
                origin_urls = {c['start'].get('upstream_url',self.origin_url) for c in source_calls}
            finally:
                source_store.close()
            if len(origin_urls)!=1 or None in origin_urls:
                raise ValueError('Recorded upstream identity missing; supply --origin-recorder-config for legacy records')
            if len(namespaces)!=1:
                raise ValueError('One recorded cache namespace required for pinned replay')
            proxy = create_app(RecorderConfig(upstream_url=next(iter(origin_urls)),directory=directory,cache_namespace=next(iter(namespaces))),
                transport=httpx.ASGITransport(app=create_mock_app()))
            experiment = self.experiment.model_copy(update={'recorder_url':'http://proxy/v1'})
            harness = Harness(experiment,transport=httpx.ASGITransport(app=proxy))
            snapshot = await load_snapshot(harness.events,experiment.recorder_url,request.run_id,request.workspace_id)
            root = next(e['payload'] for e in snapshot['events'] if e['action']=='run' and e['kind']=='decision')
            condition = next(c for c in experiment.conditions if c.condition_id==root['condition']['condition_id'])
            row = next(r for r in json.loads(experiment.dataset.read_text()) if r['case_id']==root['case_id'])
            from seocho.eval.agent_observability.evaluator import load_evaluation
            gold, metadata = load_evaluation(json.loads(experiment.evaluation.read_text())[row['case_id']])
            case = Case.model_validate({**row,'gold_answer':gold,'evaluation_metadata':metadata})
            guard = CostGuard(CostLimits(max_tokens=request.max_tokens))
            results = await run_intervention(harness,case,condition,snapshot,request.intervention,
                samples=request.samples,guard=guard,seed=root['seed'])
            job.update(status='completed' if results and all(r['status']=='completed' for r in results) else 'failed',
                results=results, reserved_tokens=guard.reserved_tokens,observed_tokens=guard.observed_tokens,
                directory=str(directory),execution_backend='local deterministic Mock; no paid API')
        except Exception as exc:
            job.update(status='failed',error=str(exc))
        finally:
            if harness:
                await harness.close()
            if proxy:
                await proxy.state.client.aclose()
                proxy.state.store.close()
            directory.parent.mkdir(parents=True,exist_ok=True)
            (directory.parent/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))


def create_ui(directory: Path, workspace_id: str, *, experiment: Path | None=None,
              job_output: Path=Path('outputs/agent-observability-ui-jobs'), origin_recorder_config: Path | None=None) -> FastAPI:
    source = Views(directory,workspace_id)
    origin_url = None
    if origin_recorder_config:
        import yaml
        origin_url = yaml.safe_load(origin_recorder_config.read_text())['upstream_url']
    jobs = MockJobs(source,load_experiment(experiment) if experiment else None,job_output,origin_url)
    token = secrets.token_urlsafe(32)
    branches: dict[str,Views] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if jobs.tasks:
            await asyncio.gather(*jobs.tasks,return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.state.source,app.state.jobs = source,jobs
    app.state.mutation_token = token

    @app.middleware('http')
    async def local_only(request: Request, call_next: Any) -> Any:
        host = request.url.hostname
        if host not in ('localhost','127.0.0.1','[::1]','::1'):
            return JSONResponse({'detail':'Loopback host required'},status_code=403)
        if request.method not in ('GET','HEAD'):
            origin = request.headers.get('origin')
            if request.headers.get('X-UI-Token')!=token or (origin and origin!=str(request.base_url).rstrip('/')):
                return JSONResponse({'detail':'Same-origin UI token required'},status_code=403)
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def view(branch: str | None) -> Views:
        if not branch:
            return source
        job = jobs.jobs.get(branch)
        if not job or job['status'] not in ('completed','failed') or not Path(job.get('directory','')).joinpath('recorder.duckdb').is_file():
            raise HTTPException(404,'Finished branch not found')
        if branch not in branches:
            branches[branch] = Views(Path(job['directory']),workspace_id)
        return branches[branch]

    @app.get('/api/config')
    def config() -> dict[str,Any]:
        return {'workspace_id':workspace_id,'source_hash':source.source_hash,'mutation_token':token,
            'mock_interventions':jobs.experiment is not None and jobs.experiment.harness_backend=='openai_agents','execution_backend':'Mock only; paid execution disabled'}

    @app.get('/api/runs')
    def runs(offset: int=Query(0,ge=0),limit: int=Query(100,ge=1,le=200),branch: str | None=None) -> dict[str,Any]:
        v = view(branch)
        return {'runs':v.index[offset:offset+limit],'total':len(v.index),'next_offset':offset+limit if offset+limit<len(v.index) else None}

    @app.get('/api/runs/{run_id}')
    def graph(run_id: str,offset: int=Query(0,ge=0),limit: int=Query(12,ge=1,le=100),branch: str | None=None) -> dict[str,Any]:
        try:
            return view(branch).graph(run_id,offset=offset,limit=limit)
        except KeyError:
            raise HTTPException(404,'Run not found in selected workspace') from None

    @app.get('/api/runs/{run_id}/calls/{call_id}')
    def call(run_id: str,call_id: str,domain: str=Query('inference',pattern='^(inference|evaluation)$'),branch: str | None=None) -> dict[str,Any]:
        try:
            return view(branch).call(run_id,call_id,domain)
        except KeyError:
            raise HTTPException(404,'Call not found in selected run/workspace/domain') from None

    @app.get('/api/aggregate')
    def aggregate(branch: str | None=None) -> dict[str,Any]:
        return view(branch).aggregate

    @app.get('/api/jobs/{job_id}')
    def job(job_id: str) -> dict[str,Any]:
        if job_id not in jobs.jobs:
            raise HTTPException(404,'Job not found')
        return jobs.jobs[job_id]

    @app.post('/api/interventions',status_code=202)
    async def intervene(request: JobRequest) -> dict[str,str]:
        try:
            return {'job_id':jobs.submit(request)}
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    assets = Path(__file__).with_name('static')
    app.mount('/static',StaticFiles(directory=assets),name='static')

    @app.get('/')
    def index() -> FileResponse:
        return FileResponse(assets/'index.html')

    return app
