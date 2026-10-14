import assert from 'node:assert/strict';
import { planStatus, checkinStatus } from '../web/lib/planner.ts';
const base = {readiness:'ready', block_progress:1, checkin:{status:'not_due',available:false}};
assert.match(checkinStatus(base), /2 completed lessons/);
assert.match(planStatus({...base,readiness:'resume_lesson'}), /Resume/);
assert.match(checkinStatus({...base,checkin:{status:'due',available:false}}), /between lessons/);
assert.match(checkinStatus({...base,checkin:{status:'answered'}}), /saved/);
assert.match(checkinStatus({...base,checkin:{status:'skipped'}}), /skipped/);
console.log('Planner presentation checks passed.');
