"""Offline renderer for the final measured evidence; no network/provider access."""
from pathlib import Path
import json,statistics
ROOT=Path(__file__).resolve().parent


def percentile(values,q):
    v=sorted(values);i=(len(v)-1)*q;a=int(i);b=min(a+1,len(v)-1)
    return v[a]+(v[b]-v[a])*(i-a)


def render():
    rows=[]
    for mode in ['current','staged']:
        run=json.loads((ROOT/'results'/f'{mode}.json').read_text());assert len(run['waves'])==10
        for n in [1,2,4,8,16]:
            waves=[w for w in run['waves'] if w['learners']==n];raw=[r for w in waves for r in w['raw']]
            times=[r['ms'] for r in raw if r['ok']]
            row=dict(mode=mode,learners=n,completed=len(times),requested=len(raw),
                p50_ms=round(percentile(times,.5),1),p95_ms=round(percentile(times,.95),1),
                peak_sessions=max(w['peak_physical_sessions'] for w in waves),
                connection_seconds_per_wave=round(statistics.mean(w['connection_seconds'] for w in waves),3),
                pool_wait_count=sum(w['pool_wait_count'] for w in waves),
                worst_wave_pool_wait_p95=max(w['pool_wait_ms']['p95'] or 0 for w in waves),
                queries_per_wave=statistics.mean(w['queries'] for w in waves),
                server_exec_ms_per_wave=round(statistics.mean(next(r['exec_ms'] for r in w['server']['execution'] if r['toplevel']) for w in waves),3),
                stt_calls=sum(w['provider_calls'] for w in waves),assessment_calls=sum(w['assessment_calls'] for w in waves),
                duplicate_stt=sum(w['extra_provider_calls'] for w in waves),duplicate_assessments=sum(w['duplicate_assessments'] for w in waves),
                max_sends_per_recording=max(w['max_sends_per_recording'] for w in waves),
                held_during_provider=max(w['max_request_connections_at_stt'] for w in waves),
                persisted={k:sum(w['persisted'][k] for w in waves) for k in ('attempts','turns','evaluations','events')})
            assert row['duplicate_stt']==row['duplicate_assessments']==0 and row['max_sends_per_recording']<=3
            assert row['persisted']['events']==row['persisted']['evaluations']==row['completed']
            if mode=='staged':
                assert row['completed']==row['requested'] and row['held_during_provider']==0
                assert row['persisted']['attempts']==row['persisted']['turns']==2*row['completed']
            rows.append(row)
    (ROOT/'results/summary.json').write_text(json.dumps(rows,indent=2)+'\n')
    get=lambda mode,n:next(r for r in rows if r['mode']==mode and r['learners']==n)
    a,b=get('current',16),get('staged',16)
    lines=['# AUD-94 — Authoritative assessment stage results','',
        '**GO for the next reviewed development stage; NO-GO for production activation or merging/deploying PR110/111.** The local staged backend now covers authoritative spoken assessment, follow-up issuance, final evaluation/adaptation, session completion, readiness and lesson checkpoint advancement. It is not a production feature-complete lesson client/server rollout.','',
        'Implementation: separate `codex/AUD-94-assessment-claims` review branch from approved `3d7fb27`. Application startup, production schemas/flags/infrastructure and frontend transport source remain unchanged. This package installs only in disposable loopback PostgreSQL databases with deterministic mock-provider configuration.','',
        '## Full-path measurements','',
        'Real HTTP and PostgreSQL17.10; nominal72ms DB RTT; mocked300ms auth,1100ms STT and3500ms assessment; main pool3/zero overflow;32 executor threads; idempotency/P2 enabled; unchanged three-send cap and50-second delivery deadline. Each measured exercise starts with a seeded clip/listening checkpoint, then two recordings, two assessments with one follow-up, final adaptation, readiness and persisted lesson advancement. Generation, tutor TTS/playback, real microphone capture and physical cellular/Safari are excluded. This is not speech-end-to-Pip latency.','',
        'Two waves per concurrency,62 requested exercises per variant. Latencies below pool successful completions across both waves; the control failure is counted separately and not assigned a successful latency. Small sample sizes do not establish statistical production acceptance.','',
        '| Concurrent learners | Current completed | Current p50/p95(s) | Staged completed | Staged p50/p95(s) |',
        '|---:|---:|---:|---:|---:|']
    for n in [1,2,4,8,16]:
        x,y=get('current',n),get('staged',n)
        lines.append(f"| {n} | {x['completed']}/{x['requested']} | {x['p50_ms']/1000:.2f}/{x['p95_ms']/1000:.2f} | {y['completed']}/{y['requested']} | {y['p50_ms']/1000:.2f}/{y['p95_ms']/1000:.2f} |")
    lines+=['',f"At 16 learners, staged p95 is {100*(1-b['p95_ms']/a['p95_ms']):.1f}% lower and checked-out connection-seconds per wave are {100*(1-b['connection_seconds_per_wave']/a['connection_seconds_per_wave']):.1f}% lower. This includes batching/projection offloads as well as durable ownership; it is not an isolated flag-toggle measurement.",'',
        '| At16 learners | Current | Staged |','|---|---:|---:|']
    for title,key in [('Peak app physical sessions','peak_sessions'),('Connection-seconds/wave','connection_seconds_per_wave'),('Pool waits, both waves','pool_wait_count'),('Worst-wave pool-wait p95(ms)','worst_wave_pool_wait_p95'),('Client SQL statements/wave','queries_per_wave'),('Server execution ms/wave','server_exec_ms_per_wave'),('Max request-owned checkouts at STT/assessment','held_during_provider'),('Max audio sends per recording','max_sends_per_recording')]:
        lines.append(f"| {title} | {a[key]} | {b[key]} |")
    lines+=['',
        'The control emitted pool-timeout diagnostics at its unchanged10-second QueuePool timeout and one16-learner exercise needed manual recovery. Its stopped client did not cause a false completed evaluation: persisted counts show the remaining transcript/partial evidence separately. Precise client error classification was not captured for that failure, so it is not attributed to Safari or a12-second network signature. No pool, timeout or route was changed to improve either variant.','',
        'The local server permits more than15 sessions. Control success with20–21 app sessions is not proof of feasibility on the supplied15-backend Supavisor session pool;200 pooler clients is a separate limit. The upload/assessment workload excludes the accent pool and other traffic. Full migration still needs production capacity verification and an old-worker drain.','',
        '## Persisted and provider invariants','',
        '| Total across all levels | Current | Staged |','|---|---:|---:|']
    for title,getvalue in [('Completed exercises',lambda r:r['completed']),('Requested exercises',lambda r:r['requested']),('STT calls',lambda r:r['stt_calls']),('Assessment calls',lambda r:r['assessment_calls']),('Persisted attempts',lambda r:r['persisted']['attempts']),('Persisted turns',lambda r:r['persisted']['turns']),('Evaluations',lambda r:r['persisted']['evaluations']),('Adaptation events',lambda r:r['persisted']['events']),('Duplicate STT',lambda r:r['duplicate_stt']),('Duplicate assessment',lambda r:r['duplicate_assessments'])]:
        totals=[sum(getvalue(r) for r in rows if r['mode']==m) for m in ['current','staged']]
        lines.append(f'| {title} | {totals[0]} | {totals[1]} |')
    lines+=['',
        'Every staged successful response follows committed transaction cleanup. No provider wait retains a request-owned connection in claims-only mode. Atomic progression tests also read persisted profile counts and lesson revisions/counts. Normal duplicates replay/join exactly the same operation; a different live operation conflicts rather than returning an ambiguous processing join.','',
        '## Correctness and review findings','',
        '- Final PostgreSQL/SQLite broad regression:219 passed,1 expected SQLite skip for a PostgreSQL-only lease test. After the last recovery-identity change, the complete38-case staged suite passed again. Existing P2 sender/deadline/recording-delivery checks:31 passed. Results overlap and should not be added as independent case counts. Full remote CI and physical Safari tests were not run.',
        '- Covers concurrent duplicates/finalizers/learners, killed worker, stale/expired claims, cancellation/unknown commits, live reset/pause/expiry, question/input changes, uncertainty/confidence boundaries, two-follow-up limit, late transaction rollback, account isolation, client verdict rejection and actual lost HTTP response recovery.',
        '- Reuses the existing grounding, final-evaluation, adaptation and terminal-feedback domain functions. Insufficient evidence is unknown, not zero; unreliable recognition does not adapt. Evaluation/adaptation uniqueness and profile/version checks remain transaction backstops.',
        '- Every current learner-owned table has a guard. A review found and corrected cross-parent inserts that could otherwise derive ownership from only one reference. All relevant parent references are checked. Active-question existence is also validated by a deferred constraint at commit to preserve the original conversation-before-followup transaction order. Both legacy-first and bridge-first real-middleware tests pass.',
        '- Wrong reset revisions roll back invalidation; explicit record-again is CAS-bound to the pending attempt/question and preserves evidence rows. Unconverted direct writers fail closed, and NOWAIT owner checks prevent reversed lock waiting from becoming a deadlock.',
        '- Deliberate crash/expiry ambiguity can repeat external provider work whose result was not persisted. Exactly-once persisted progression is proved for this scoped path; exactly-once external billing is not promised. No real providers were called.','',
        '## Compatibility and next gates','',
        'See README.md for scope and DEPLOYMENT.md for the proposed writer matrix, bridge/drain sequence and rollback. The guard-aware bridge retains original advisory ownership and therefore still holds a connection during provider work; its compatibility tests are not claims-only capacity tests. Unaware workers must be drained before enforcing a new ownership protocol. Roll back only to guard-aware bridge code after resolving in-flight claims; never delete receipts/learner work or drop guards to recover traffic.','',
        'Next development gates: typed processing/status/resume and explicit record-again client integration; remaining provider writers (generation, check-ins, tutor audio/TTS, closing/homework and onboarding); complete account-deletion/retention semantics; versioned least-privilege schema/role rollout and conflict handling; full CI/WebKit/physical Safari; fresh capacity/monitoring approval. Claims-only review mode rejects unsupported mutations, so it is intentionally not a drop-in production lesson client. The stage advances completed-exercise lesson checkpoints; it does not yet migrate the complete closing/homework/lesson-completion lifecycle.','',
        'Keep the existing60-second server claim TTL and50-second P2 deadline policy: timeout is unconfirmed delivery, never permission to steal a live claim; preserve the recording and use status-first explicit recovery. No existing production flag was changed. PR110/111 remain unmerged and undeployed.','',
        '## Provenance and artifacts','',
        '- `results/current.json`, `staged.json`: exact raw waves, numeric request/SQL profiles, provider counts, persisted counts, package versions and source hashes.',
        '- `results/summary.json`: pooled comparison above. `tests-*.txt`: successful targeted verification outputs.',
        '- Control source is732e420 with the full-path harness captured at review checkpoint2187614. Final staged hardening changes ownership validation and the202 join identity contract; the control never executes staged functions or returns202. Shared mock latencies, pool/deadline settings, normal client sequence and P2 source are identical. Unused staged-file hashes recorded with the control can be verified against2187614; final staged hashes identify the reviewed implementation.',
        '- Synthetic setup clears its own per-wave data outside measurements, with guards enforced during workload. No production credentials/session files were read. All experimental databases and local services are cleaned up after verification.','']
    rendered='\n'.join(lines)
    for a,b in [('PostgreSQL17.10','PostgreSQL 17.10'),('nominal72','nominal 72'),('mocked300','mocked 300'),('auth,1100','auth, 1100'),('and3500','and 3500'),('pool3/zero','pool 3/zero'),('overflow;32','overflow; 32'),('and50','and 50'),('concurrency,62','concurrency, 62'),('At16','At 16'),('lower and','lower and'),('are93','are 93'),('is64','is 64'),('is63','is 63'),('unchanged10','unchanged 10'),('one16','one 16'),('a12','a 12'),('than15','than 15'),('with20','with 20'),('supplied15','supplied 15'),(';200','; 200'),('- Final PostgreSQL','- Final PostgreSQL'),('regression:219','regression: 219'),(',1 expected',', 1 expected'),('complete38','complete 38'),('checks:31','checks: 31'),('existing60','existing 60'),('is732e420','is `732e420`'),('checkpoint2187614','checkpoint `2187614`'),('against2187614','against `2187614`'),('the202','the 202'),('returns202','returns 202')]:rendered=rendered.replace(a,b)
    (ROOT/'RESULTS.md').write_text(rendered)
    print(rendered[:5200])


if __name__=='__main__':render()
