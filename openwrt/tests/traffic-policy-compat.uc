// Execute inputs against the actual ucode module, not a second implementation.
import * as fs from 'fs';
import * as p from 'traffic-policy';
function clone(v) { return json(sprintf('%J',v)); }
function evaluate(c) {
    let state=clone(c.state || {}), writes=[], events=[], status=null;
    try {
        if (c.op=='config') return {value:p.config(c.value)};
        if (c.op=='due') return {value:p.due(state,c.schedule,c.now),state};
        if (c.op=='next_run') return {value:p.next_run(c.now,c.clock)};
        if (c.op=='exceeded') return {value:p.exceeded(c.settings,c.snapshot)};
        let current=clone(c.state || {}), network=clone(c.network || {up:true,autostart:true,pending:false});
        let io={
            settings:()=>clone(c.settings),state:()=>clone(current),snapshot:()=>clone(c.snapshot || {}),
            save:(s)=>{if(c.write_fail)die('storage_failed');current=clone(s);push(writes,clone(s));},
            status:(s)=>{if(c.write_fail)die('storage_failed');status=clone(s);},
            active_slot:()=>c.slot || '0',uci:(key,fallback)=>c.uci?.[key] ?? fallback,
            now:()=>c.now,boot:()=>c.boot || 'one',switching:()=>!!c.switching,
            bus:(iface,method)=>{
                push(events,[iface,method]);
                if (c.bus_fail==method) die('bus_failed');
                if(method=='down') {network.up=false;network.autostart=false;}
                if(method=='up') {network.up=true;network.autostart=true;}
                return clone(network);
            },
            restart:(slot)=>{push(events,['restart',slot]);if(c.restart=='error')die('restart_failed');return c.restart || 'done';}
        };
        let controller=p.controller(io);
        if(c.op=='reconcile') {
            let value=controller.reconcile(state,c.settings,c.slot || '0',c.iface || 'wan',network,c.boot || 'one');
            return {value,state,writes,events,network};
        }
        if(c.op=='tick') {
            controller.tick(!!c.force);
            return {state:current,writes,events,status,network};
        }
        die('unknown_test_operation');
    }
    catch(e) { return {error:e.message,writes,events}; }
}
let cases=json(fs.readfile('/dev/stdin'));
print(sprintf('%J\n',map(cases,evaluate)));
