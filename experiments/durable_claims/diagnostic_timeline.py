"""Extra diagnostic run of the unchanged benchmark with interval correlation.

Forward benchmark arguments. This subclasses only numeric instrumentation; store,
request pipeline, database pools and sender behavior are unchanged.
"""
import runpy
from time import perf_counter
from . import metrics


def union(intervals):
    end=None;total=0
    for start,stop in sorted(intervals):
        if end is None or start>=end:total+=stop-start;end=stop
        elif stop>end:total+=stop-end;end=stop
    return total


class TimelineMeter(metrics.Meter):
    def reset(self):
        super().reset();self.spans=[]

    def add(self,name,value):
        super().add(name,value)
        index=metrics.client_index.get()
        if index is not None and name in ('checkout_ms','cursor_ms','do_commit_ms','do_rollback_ms','auth_ms','provider_ms','pool_wait_ms','executor_queue_ms'):
            end=perf_counter();self.spans.append((index,name,end-value/1000,end))

    def summary(self):
        result=super().summary();rows=[]
        primary=('checkout_ms','auth_ms','provider_ms','pool_wait_ms','executor_queue_ms')
        for index in sorted({s[0] for s in self.spans}):
            spans=[s for s in self.spans if s[0]==index]
            begin=min(s[2] for s in spans);end=max(s[3] for s in spans)
            by_name={name:[(s[2],s[3]) for s in spans if s[1]==name] for name in {s[1] for s in spans}}
            covered=union([(s[2],s[3]) for s in spans if s[1] in primary])
            category_union=sum(union(by_name.get(name,[])) for name in primary)
            rows.append(dict(index=index,observed_window_ms=(end-begin)*1000,
                covered_ms=covered*1000,cross_category_overlap_ms=(category_union-covered)*1000,
                unclassified_window_ms=(end-begin-covered)*1000,
                union_ms={name:union(intervals)*1000 for name,intervals in by_name.items()},
                spans=[dict(kind=s[1],start_ms=(s[2]-begin)*1000,end_ms=(s[3]-begin)*1000) for s in spans]))
        result['diagnostic_timeline']=rows
        return result


metrics.Meter=TimelineMeter
runpy.run_module('experiments.durable_claims.benchmark',run_name='__main__')
