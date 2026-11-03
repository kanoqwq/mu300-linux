// Share only an in-flight observation, never a completed/stale signal cache.
// Wait for this SIM's background collector before taking its AT lock.
export function queue(start, occupied, timer) {
    let pending={};
    function finish(slot,entry,result) {
        if (pending[slot]!==entry) return;
        delete pending[slot];entry.timer?.cancel();
        for(let reply in entry.replies) { try { reply(result); } catch(e) {} }
    }
    function launch(slot,entry) {
        if(entry.started || occupied(slot)) return;
        entry.started=true;entry.timer.cancel();
        let why=start(slot,result=>finish(slot,entry,result));
        if(why!=null) finish(slot,entry,{error:why,busy:why=='busy'?1:0});
    }
    function request(slot,reply) {
        let entry=pending[slot];
        if(entry) { push(entry.replies,reply);return; }
        entry={replies:[reply],started:false};pending[slot]=entry;
        entry.timer=timer(12000,()=>finish(slot,entry,{error:'busy',busy:1}));
        launch(slot,entry);
    }
    function resume() { for(let slot,entry in pending) launch(slot,entry); }
    return {request,resume,has:(slot)=>pending[slot]!=null};
};
