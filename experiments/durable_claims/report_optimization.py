"""Render measured local artifacts; does not connect to a database or provider."""
from pathlib import Path
import json
import statistics as st

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'results/optimization'


def percentile(values,q):
    values=sorted(values);f=(len(values)-1)*q;i=int(f);j=min(i+1,len(values)-1)
    return values[i]+(values[j]-values[i])*(f-i)


def top_execution(wave):
    return next(row['exec_ms'] for row in wave['server']['execution'] if row['toplevel'])


def render():
    modes=['current','claims','optimized']
    data={mode:json.loads((OUT/f'{mode}.json').read_text()) for mode in modes}
    rows=[]
    for mode,run in data.items():
        assert len(run['waves'])==10
        for level in [1,2,4,8,16]:
            waves=[w for w in run['waves'] if w['learners']==level]
            clients=[r for w in waves for r in w['raw']]
            times=[r['ms'] for r in clients if r['ok']]
            rows.append(dict(mode=mode,learners=level,completed=len(times),requested=len(clients),
                p50_ms=round(percentile(times,.5),1),p95_ms=round(percentile(times,.95),1),
                peak_sessions=max(w['peak_physical_sessions'] for w in waves),
                connection_seconds=round(st.mean(w['connection_seconds'] for w in waves),3),
                queries_per_wave=st.mean(w['queries'] for w in waves),
                server_exec_ms_per_wave=round(st.mean(top_execution(w) for w in waves),3),
                pool_waits=sum(w['pool_wait_count'] for w in waves),
                worst_wave_pool_wait_p95=max(w['pool_wait_ms']['p95'] or 0 for w in waves),
                max_lock_waiters=max(w['server']['max_lock_waiters'] for w in waves),
                calls=sum(w['provider_calls'] for w in waves),duplicates=sum(w['extra_provider_calls'] for w in waves),
                attempts=sum(w['persisted_attempts'] for w in waves),sends=sum(w['sends'] for w in waves),
                max_sends=max(w['max_sends'] for w in waves),
                held_at_stt=max(w['max_request_connections_at_stt'] for w in waves)))
        assert all(w['outstanding']==0 and w['extra_provider_calls']==0 for w in run['waves'])
    (OUT/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
    timeline={}
    for mode in ['claims','optimized']:
        wave=json.loads((OUT/f'timeline-{mode}.json').read_text())['waves'][0]
        middle=sorted(enumerate(wave['raw']),key=lambda p:p[1]['ms'])[7:9]
        selected=[next(t for t in wave['diagnostic_timeline'] if t['index']==index) for index,_ in middle]
        timeline[mode]=dict(client_wall_ms=st.mean(client['ms'] for _,client in middle),
            observed_window_ms=st.mean(t['observed_window_ms'] for t in selected),
            covered_ms=st.mean(t['covered_ms'] for t in selected),
            overlap_ms=st.mean(t['cross_category_overlap_ms'] for t in selected),
            unclassified_ms=st.mean(t['unclassified_window_ms'] for t in selected),
            components_ms={key:st.mean(t['union_ms'].get(key,0) for t in selected) for key in selected[0]['union_ms']},
            sql_server_exec_ms_entire_wave=top_execution(wave))
    (OUT/'median-timeline.json').write_text(json.dumps(timeline,indent=2)+'\n')
    find=lambda mode,n:next(r for r in rows if r['mode']==mode and r['learners']==n)
    before,after,current=find('claims',16),find('optimized',16),find('current',16)
    lines=['# AUD-94 — Optimization results','',
        '**GO to continue a staged, reviewed architecture migration; NO-GO for production activation or removing remaining middleware leases.** The local upload proof meets the observed latency and connection targets. Full authoritative progression and all-writer migration remain unfinished.','',
        '## Fresh comparison','',
        'The same instrumented harness reran current source732e420, the original claims algorithm frozen at9d9fc7e, and optimized claims. Two waves per concurrency,62 requested uploads per variant. Fixed300ms mocked auth,1100ms mocked STT,72ms nominal database RTT, pool3/overflow0,32 executor threads, unchanged P2 policy. Latency includes policy read through upload acknowledgment, not speech-end-to-Pip. Percentiles pool successful uploads across both waves; low-concurrency samples are small.','',
        '| Learners | Current p50/p95 (s) | Original claims p50/p95 (s) | Optimized claims p50/p95 (s) | Completion per variant |',
        '|---:|---:|---:|---:|---:|']
    for n in [1,2,4,8,16]:
        selected=[find(mode,n) for mode in modes]
        lines.append('| '+str(n)+' | '+' | '.join(f"{r['p50_ms']/1000:.2f}/{r['p95_ms']/1000:.2f}" for r in selected)+f" | {selected[0]['completed']}/{selected[0]['requested']} each |")
    lines+=['',
        f"At16 learners, optimized claims reduces p50 by{100*(1-after['p50_ms']/before['p50_ms']):.1f}% and p95 by{100*(1-after['p95_ms']/before['p95_ms']):.1f}% versus original claims. Connection-seconds fall{100*(1-after['connection_seconds']/before['connection_seconds']):.1f}% versus original claims and{100*(1-after['connection_seconds']/current['connection_seconds']):.1f}% versus current architecture. All variants completed62/62 with62 STT calls and62 attempts; zero duplicate provider calls in the normal matrix.",'',
        '| Architecture at16 learners | Peak sessions | Connection-seconds/wave | SQL statements/wave | Server execution ms/wave | Pool waits, two waves | Worst-wave pool-wait p95 (ms) | Sends, two waves |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode in modes:
        r=find(mode,16)
        lines.append(f"| {mode} | {r['peak_sessions']} | {r['connection_seconds']:.2f} | {r['queries_per_wave']:.0f} | {r['server_exec_ms_per_wave']:.2f} | {r['pool_waits']} | {r['worst_wave_pool_wait_p95']:.1f} | {r['sends']} |")
    lines+=['','Full1/2/4/8/16 connection, query, wait and provider figures are in `results/optimization/summary.json`; raw request/statement profiles are in the three variant JSON files. Observed DB lock-wait samples were zero for independent learners at10ms sampling; this does not rule out shorter waits. Original and optimized claims held zero request-owned connections at STT; current held one. Idle physical pool sessions remain connected. The local server allows more than15 sessions, so legacy19-session success is not proof of Supavisor feasibility.','',
        '## Before/after ordinary upload profile','',
        '| Stage | Original client SQL calls | Optimized client SQL calls | Transaction boundary |',
        '|---|---:|---:|---|',
        '| Authenticated policy/upload request scopes | 2 | 2 | Short existing reads |',
        '| Claim | 14 | 2 | One explicit transaction; commit before STT |',
        '| Finalize | 17 | 2 | One explicit atomic transaction after STT |',
        '| Total | 33 | 6 | No split transactions or provider-spanning connection |','',
        'The savings batch required owner/slot/operation/question reads, eliminate duplicate ownership lookups within a locked snapshot, reuse database-clock values, and avoid rewriting unchanged conversation child projections. Required checks remain in the batched PostgreSQL reader and Python validation. Claim hashes and stored records remain bidirectionally compatible with the original PoC. SQL templates and exact counts are recorded without bound values.','',
        'At one learner, original claims spends about2.85s of checked-out DB time while PostgreSQL top-level execution averages only4.3ms. Optimized checkout time is about0.78s. This is predominantly round-trip reduction, not faster STT or relaxed validation. The helper function adds some local server CPU, but its absolute cost remains milliseconds; nested execution totals must not be added to parent totals. Planning time is not separately measured.','',
        '## Why the original16-learner median was17.50seconds','',
        'The fresh matrix reproduces the historical result. Long read/check/write sequences hold all three main-pool connections for many network round trips. Requests queue, cross the first-send timeout and retry while the original server work continues. Original claims issues645 statements per16-upload wave versus528 without retries;29 audio sends replace the16 first sends. Retries do not repeat STT, but their reads/authentication amplify queueing.','',
        'A separate interval-correlated diagnostic (same algorithm, timings and pools; not folded into the matrix) reproduces that behavior. Values below average the middle two recordings defining each16-learner median. Components are interval unions per recording across retries; SQL cursor/commit times are children of checked-out time.','',
        '| Diagnostic component | Original claims (s) | Optimized claims (s) |',
        '|---|---:|---:|']
    fields=[('Client wall latency','client_wall_ms'),('Checked-out DB intervals','checkout_ms'),('SQL cursor intervals, inside DB time','cursor_ms'),('Commit intervals, inside DB time','do_commit_ms'),('Pool-wait intervals','pool_wait_ms'),('Mocked auth intervals','auth_ms'),('Mocked STT intervals','provider_ms'),('Executor queue intervals','executor_queue_ms'),('Cross-category overlap, subtract when combining','overlap_ms'),('Unclassified server-window time','unclassified_ms')]
    for title,key in fields:
        values=[t.get(key,t['components_ms'].get(key,0)) for t in timeline.values()]
        lines.append(f'| {title} | {values[0]/1000:.3f} | {values[1]/1000:.3f} |')
    lines+=['',
        'The original diagnostic has about1.73s of overlap between server requests for the same recording. Adding all work totals directly to client wall time would double-count it. Its remaining unclassified/window-to-client time is roughly0.15s, covering framework/audio validation/HTTP scheduling; client retry backoff can overlap the still-running server work. Exact raw intervals are retained. Across each entire diagnostic wave, actual top-level PostgreSQL execution is only a few tens of milliseconds, not seconds. No lock-wait sample or executor starvation explains the17.5s result.','',
        '## Correctness and policy','',
        '-149 passed,1 expected SQLite skip across59 original/optimized PostgreSQL proof cases plus existing upload-idempotency, conversation and per-learner locking tests. Additional JS checks:20 sender/deadline tests and11 recording-delivery tests passed. Full remote CI was not run; no implementation PR is changed.',
        '-Preserved duplicate submissions/finalizers, killed worker, expiry/stale fencing, cancellation/uncertain commit, reset, injected rollback, account isolation and real legacy-first/bridge-first middleware overlap.',
        '-Added bidirectional original/optimized claim compatibility, different-recording expiry, partial multi-write failure rollback, inconsistent-projection rejection, issued follow-up/turn/evidence preservation and function privilege/transaction-lock checks.',
        '-50/59.999/60-second boundaries are deterministic. Proposed policy retains60-second ownership TTL and50-second delivery deadline: client expiry is unconfirmed, retains recording and schedules no extra sends; explicit recovery checks status before reclaiming expired work. See `TTL-POLICY.md`. Production status-first UX is not implemented.',
        '-Deliberate provider-expiry takeover still permits two mock STT calls but only one persisted attempt/checkpoint; external exactly-once billing is not promised.','',
        '## Recommendation and remaining gates','',
        'The observed upload acceptance targets pass: no duplicate persisted acceptance or stale-worker mutation, no connection across mocked STT in claims-only mode, no single-learner regression, substantially lower8/16-learner latency, and retained/improved connection efficiency. These are synthetic local measurements, not statistical production acceptance.','',
        'Proceed next with a reviewed all-writer inventory and bridge/drain/rollback design, then a separately approved migration of one authoritative assessment path and typed client recovery integration. Keep production activation blocked until those proofs and physical acceptance are complete. In particular:',
        '-This still covers standalone upload acceptance/pending checkpoints only, not exactly-once final assessment/adaptation/lesson progression. Lesson/check-in, generation, onboarding, GET settlement, end/reset and other writers remain unmigrated.',
        '-Unaware legacy workers require the original advisory-lock bridge and a verified drain before claims-only activation. That bridge intentionally retains a provider-spanning connection. Original/optimized PoC record compatibility does not solve unaware legacy-writer overlap.',
        '-The local function/CTE approach needs versioned schema deployment, role/security review and coordinated rollback. No function was installed on production.',
        '-TTL/status-first recovery, reload disclosure, retained-recording UX, retention and provider/memory budgets remain integration work. Neither retry timeouts nor pool sizes should be increased to compensate.',
        '-Upload-only tests do not measure complete lessons, real providers, cellular transport, physical Safari or the accent pool. Full connection budget remains5 per worker/10 across two fully migrated overlapping workers plus other clients; observed3 is not a production budget.',
        '-PR110 remains useful for offloading whole short transactions. PR111 remains useful on legacy/bridge lease routes; its JSON barrier becomes unnecessary only after a migrated route commits and cleans up before acknowledgment. Neither PR was merged or deployed.','',
        '## Reproduce','',
        'Use the same local environment/proxy as `README.md`, with pg_stat_statements preloaded and track=all on the disposable local server. Run the same `benchmark.py` for modes current,claims,optimized; pass `--claims-root` pointing to a clean9d9fc7e checkout for original claims. Use `diagnostic_timeline.py` with the same arguments and levels16/repetitions1 for interval diagnosis. No production credentials are needed.','']
    text='\n'.join(lines)
    replacements={
        'source732e420':'source `732e420`','at9d9fc7e':'at `9d9fc7e`',
        ',62':', 62',',1100':', 1100',',72':', 72',',32':', 32',
        'Fixed300ms':'Fixed 300 ms','1100ms':'1100 ms','72ms':'72 ms',
        'pool3/overflow0':'pool 3/overflow 0','At16':'At 16','at16':'at 16',
        'by78':'by 78','by76':'by 76','fall78':'fall 78','and87':'and 87',
        'completed62':'completed 62','with62':'with 62','and62':'and 62',
        'Full1':'Full 1','at10ms':'at 10 ms','than15':'than 15','legacy19':'legacy 19',
        'about2.85s':'about 2.85 s','only4.3ms':'only 4.3 ms','about0.78s':'about 0.78 s',
        'original16':'original 16','was17.50seconds':'was 17.50 seconds',
        'issues645':'issues 645','per16':'per 16','versus528':'versus 528',';29':'; 29',
        'the16':'the 16','about1.73s':'about 1.73 s','roughly0.15s':'roughly 0.15 s',
        '-149':'- 149',',1 expected':', 1 expected','across59':'across 59',
        'PostgreSQL proof cases':'prototype cases (real PostgreSQL for database tests)',
        'checks:20':'checks: 20','and11':'and 11','-Preserved':'- Preserved','-Added':'- Added',
        '-50':'- 50','retains60':'retains 60','and50':'and 50','-Deliberate':'- Deliberate',
        'lower8/16':'lower 8/16','-This':'- This','-Unaware':'- Unaware','-The':'- The',
        '-TTL':'- TTL','-Upload':'- Upload','-PR':'- PR','remains5':'remains 5',
        'worker/10':'worker / 10','observed3':'observed 3','modes current,claims,optimized':'modes current, claims, optimized',
        'clean9d9fc7e':'clean `9d9fc7e`','levels16/repetitions1':'levels 16/repetitions 1'
    }
    for before,after in replacements.items():text=text.replace(before,after)
    (ROOT/'OPTIMIZATION-RESULTS.md').write_text(text)
    print(text[:6500])


if __name__=='__main__':render()
