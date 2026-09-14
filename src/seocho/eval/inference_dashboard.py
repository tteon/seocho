"""Generate a self-contained, filterable operator dashboard with no external assets."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any


def write_dashboard(
    path: Path, report: dict[str, Any], telemetry: dict[str, Any] | None = None
) -> None:
    payload = (
        json.dumps({"report": report, "telemetry": telemetry}, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SEOCHO · Inference cost and traces</title><style>
body{font:15px system-ui,sans-serif;margin:0;background:#101722;color:#e2eaf3}main{max-width:1450px;margin:auto;padding:30px}h1{font-size:28px}h2{font-size:18px;margin-top:30px}.muted{color:#a6b7ca}select{padding:8px;background:#1c2939;color:inherit;border:1px solid #455873;margin:4px 16px 10px 6px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px;border-bottom:1px solid #354359;text-align:left}th{color:#81d9cc}tr:hover{background:#1c2939}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#192637;padding:16px;max-height:360px;overflow:auto}.scroll{overflow:auto}.cards{display:flex;flex-wrap:wrap;gap:14px}.card{padding:18px;background:#1c2939;border-radius:8px;min-width:180px}.value{font-size:24px;color:#81d9cc}.alert{color:#ffba91}button{color:#81d9cc;background:transparent;border:1px solid #435570;cursor:pointer;padding:6px}
</style><main><p class="muted">SEOCHO / OPERATOR WORKBENCH</p><h1>Inference cost and traces</h1>
<p class="muted">Recorded request costs by model, tenant and route. “—” means unmeasured. Server timing and DCGM are engine/device observations, not tenant-attributed GPU measurements.</p>
<p id="evidence" class="alert"></p><details><summary>Run cost boundary and allocation (whole run)</summary><pre id="allocation"></pre></details>
<div id="filters"></div><div class="cards" id="cards"></div><h2>Request accounting and latency</h2><div class="scroll"><table><thead id="head"></thead><tbody id="groups"></tbody></table></div>
<p class="muted" id="boundary"></p><h2>Engine window · speculative decoding and cache</h2><div class="cards" id="engine"></div>
<details><summary>Engine observation scope, sources and missing measurements</summary><pre id="telemetry"></pre></details>
<h2>DCGM device samples</h2><div class="scroll"><table><thead><tr><th>Metric</th><th>Device labels</th><th>Value</th></tr></thead><tbody id="gpu"></tbody></table></div>
<h2>Drift, errors and cost alerts</h2><pre class="alert" id="alerts"></pre><h2>Request / trace inspection</h2>
<p class="muted">Select a request to inspect its recorded stages, errors and measurement sources. Output variation is not a quality verdict.</p><div class="scroll"><table><thead><tr><th>Request</th><th>Model</th><th>Tenant</th><th>Route</th><th>Status</th><th>Trace</th></tr></thead><tbody id="requests"></tbody></table></div><pre id="detail">Select a request.</pre></main>
<script type="application/json" id="data">__PAYLOAD__</script><script>
const {report,telemetry}=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const fmt=v=>v===null||v===undefined?'—':typeof v==='number'?v.toLocaleString(undefined,{maximumFractionDigits:3}):String(v);
const columns=[['Model','model'],['Tenant','tenant'],['Route','route'],['Config','configuration_id'],['Requests','requests'],['Errors','errors'],['Priced','priced_requests'],['Cost USD','cost_usd'],['$/1M output','cost_per_1m_output_tokens'],['$/1M total','cost_per_1m_total_tokens'],['TTFT p95 ms','ttft_ms_p95'],['ITL p95 ms','itl_ms_p95'],['TTFT coverage','ttft_ms_coverage'],['Queue p50 ms','queue_ms_p50'],['Prefill p50 ms','prefill_ms_p50'],['Decode p50 ms','decode_ms_p50']];
const filterKeys=['model','tenant','route','configuration_id'];
for(const k of filterKeys){const label=document.createElement('label');label.textContent=k;const select=document.createElement('select');select.id='filter-'+k;for(const v of ['',...new Set(report.groups.map(r=>r[k]))]){const o=document.createElement('option');o.value=v;o.textContent=v||'All';select.append(o)}select.onchange=render;label.append(select);$('filters').append(label)}
const matches=r=>filterKeys.every(k=>!$('filter-'+k).value||r[k]===$('filter-'+k).value);
const cell=(row,value,tag='td')=>{const el=document.createElement(tag);el.textContent=fmt(value);row.append(el);return el};
const card=(parent,title,value)=>{const d=document.createElement('div');d.className='card';const label=document.createElement('div');label.textContent=title;const v=document.createElement('div');v.className='value';v.textContent=fmt(value);d.append(label,v);parent.append(d)};
const header=document.createElement('tr');for(const [name] of columns)cell(header,name,'th');$('head').append(header);
function render(){const groups=report.groups.filter(matches),requests=report.requests.filter(matches);$('groups').replaceChildren();for(const r of groups){const tr=document.createElement('tr');for(const [,key] of columns)cell(tr,r[key]);$('groups').append(tr)}
$('cards').replaceChildren();card($('cards'),'Requests',requests.length);card($('cards'),'Failed requests',groups.reduce((a,r)=>a+r.errors,0));card($('cards'),'Cost coverage',groups.reduce((a,r)=>a+r.priced_requests,0)+' / '+requests.length);card($('cards'),'Known attributed cost USD',groups.reduce((a,r)=>a+r.known_cost_usd,0));
$('requests').replaceChildren();for(const r of requests){const tr=document.createElement('tr');for(const k of ['request_id','model','tenant','route','status','trace_id'])cell(tr,r[k]);tr.tabIndex=0;tr.onclick=()=>{$('detail').textContent=JSON.stringify(r,null,2)};tr.onkeydown=e=>{if(e.key==='Enter')tr.click()};$('requests').append(tr)}
$('alerts').textContent=JSON.stringify((report.alerts||[]).filter(matches),null,2);}
$('boundary').textContent=report.cost_boundary;
$('evidence').textContent=report.evidence_status||'Request completion and answer correctness are evaluated separately.';
$('allocation').textContent=JSON.stringify(report.allocation||{status:'No run-level allocation receipt supplied; per-request cost_basis remains available in request details.'},null,2);
$('telemetry').textContent=JSON.stringify(telemetry||{status:'No engine telemetry supplied'},null,2);
for(const key of ['ttft_ms','itl_ms','queue_ms','prefill_ms','decode_ms','accepted_token_rate','accepted_per_draft_step','prefix_hit_rate'])card($('engine'),key,telemetry?.[key]);
for(const r of telemetry?.gpu||[]){const tr=document.createElement('tr');cell(tr,r.name);cell(tr,JSON.stringify(r.labels));cell(tr,r.value);$('gpu').append(tr)}
if(!telemetry?.gpu?.length){const tr=document.createElement('tr');cell(tr,'DCGM not collected');$('gpu').append(tr)}render();
</script></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.replace("__PAYLOAD__", payload), encoding="utf-8")
