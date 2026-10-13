"""Isolated Pi CLI turns; every provider request must acknowledge the Recorder."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


def final_message(stdout: str) -> str:
    """Read LF-framed Pi JSON events without exposing private reasoning."""
    messages = [row['message'] for line in stdout.split('\n') if line.strip()
                if (row := json.loads(line)).get('type') == 'message_end'
                and row.get('message', {}).get('role') == 'assistant']
    if len(messages) != 1 or messages[0].get('stopReason') not in ('stop', 'length'):
        raise ValueError('Pi must complete exactly one assistant turn')
    if messages[0]['stopReason'] == 'length':
        raise ValueError('Pi response exhausted output cap')
    return ''.join(c['text'] for c in messages[0].get('content', []) if c.get('type') == 'text')


async def execute_pi(*, executable: str, recorder_url: str, model: str,
                     instructions: str, prompt: str, schema: type[BaseModel],
                     tags: dict[str, str], attempts: list[dict[str, str]],
                     artifact_directory: Path, seed: int, hard_output_limit: int,
                     timeout: float = 120) -> Any:
    binary = shutil.which(executable)
    if not binary:
        raise ValueError('Pi executable unavailable')
    call_id = uuid4().hex
    path = artifact_directory / tags['X-Run-Id'] / tags['X-Step-Id']
    path.mkdir(parents=True, exist_ok=False)
    config = path / 'agent'
    config.mkdir()
    headers = {**tags, 'X-Call-Id': call_id}
    (config / 'models.json').write_text(json.dumps({'providers': {'recorder': {
        'baseUrl': recorder_url, 'api': 'openai-completions', 'apiKey': 'recorder-local',
        'headers': headers, 'models': [{'id': model, 'reasoning': False,
        'input': ['text'], 'contextWindow': 16384, 'maxTokens': hard_output_limit,
        'compat': {'maxTokensField': 'max_tokens', 'supportsStore': False,
                   'supportsDeveloperRole': True, 'supportsUsageInStreaming': True}}]}}}, indent=2))
    (config / 'settings.json').write_text(json.dumps({'retry': {'enabled': False,
        'provider': {'maxRetries': 0, 'timeoutMs': int(timeout * 1000)}},
        'compaction': {'enabled': False}, 'cacheWarming': False,
        'enableInstallTelemetry': False, 'defaultTools': [], 'quietStartup': True}))
    extension = path / 'recorder.ts'
    extension.write_text('''export default function(pi) {
 let calls = 0;
 pi.on("before_provider_request", (event) => {
  if (++calls > 1) throw new Error("One-call admission cap exceeded");
  const payload = {...event.payload};
  if (payload.model !== MODEL || payload.tools?.length) throw new Error("Unexpected model/tools");
  delete payload.max_completion_tokens;
  payload.max_tokens = LIMIT; payload.temperature = 0; payload.seed = SEED;
  return payload;
 });
 pi.on("after_provider_response", (event) => {
  if (event.headers["x-call-id"] !== CALL) throw new Error("Recorder acknowledgement missing");
 });
}
'''.replace('MODEL', json.dumps(model)).replace('LIMIT', str(hard_output_limit))
          .replace('SEED', str(seed)).replace('CALL', json.dumps(call_id)))
    # Fresh HOME and agent directory prevent credential, MCP and session discovery.
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(path.resolve()),
           'PI_CODING_AGENT_DIR': str(config.resolve()), 'PI_OFFLINE': '1', 'PI_TELEMETRY': '0'}
    command = [binary, '--offline', '--mode', 'json', '--no-session', '--no-tools',
        '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes',
        '--no-context-files', '--no-approve', '--thinking', 'off',
        '--provider', 'recorder', '--model', model, '--system-prompt', instructions,
        '-e', str(extension.resolve())]
    # Schema is carried in the user JSON for Pi, not a claim of wire-identical prompts.
    data = json.loads(prompt)
    data['response_schema'] = schema.model_json_schema()
    attempts.append({'call_id': call_id, 'run_id': tags['X-Run-Id'],
                     'agent_id': tags['X-Agent-Id'], 'step_id': tags['X-Step-Id']})
    process = await asyncio.create_subprocess_exec(*command, cwd=path.resolve(), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(data).encode()), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    (path / 'events.jsonl').write_bytes(stdout)
    (path / 'stderr.txt').write_bytes(stderr)
    if process.returncode:
        raise ValueError('Pi process failed; inspect isolated artifacts')
    return schema.model_validate_json(final_message(stdout.decode()))
