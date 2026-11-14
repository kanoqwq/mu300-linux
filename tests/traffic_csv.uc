import * as c from 'traffic-core';
import * as csv from 'traffic-csv';
let checks=0;
function check(ok,label) { if (!ok) die('FAIL '+label+'\n');checks++; }
function bad(fn,error) {
    let caught=null;try { fn(); } catch(e) { caught=e.message; }
    check(caught==error,'reject '+error+': '+caught);
}
const now=timelocal({year:2026,mon:11,mday:14,hour:12,min:0,sec:0,isdst:-1});
let db=c.fresh(now);db.config.plan_name='=not-a-spreadsheet-formula';
db.days={'2026-08-08':{rx:100,tx:10},'2026-11-14':{rx:200,tx:20}};
db.months={'2026-08':{rx:500,tx:50},'2026-11':{rx:200,tx:20}};
let text=csv.encode(db,1,now), decoded=csv.parse(text);
check(decoded.slot==1 && decoded.days['2026-08-08'].rx==100,'round trip and zero-padded date');
check(decoded.months['2026-08'].rx==500,'monthly older totals kept');
check(index(text,db.config.plan_name)<0,'no formula-capable free text');
check(length(keys(decoded.days))==2 && length(keys(decoded.months))==2,'all records exported');
check(csv.parse('\xef\xbb\xbf'+replace(text,'\r\n','\n')).slot==1,'BOM and LF');
let quoted=join('\r\n',map(split(trim(text),'\r\n'),line=>join(',',map(split(line,','),f=>'"'+f+'"'))))+'\r\n';
check(csv.parse(quoted).days['2026-11-14'].tx==20,'RFC quoted fields');
let sparse=json(sprintf('%J',db));sparse.days['2024-01-01']={rx:42,tx:7};
check(csv.parse(csv.encode(sparse,0,now)).days['2024-01-01'].rx==42,'sparse days may outlive retained monthly totals');
let empty=c.fresh(now), restore=csv.prepare(empty,text,'replace',now);
check(restore.db.days['2026-11-14'].rx==200 && restore.preview.days==2,'replace preview');
check(restore.db.last==null && restore.db.pending_rx==0,'kernel baseline excluded');
check(restore.db.config.plan_name=='','plan preserved, not imported');
let again=csv.prepare(restore.db,text,'merge',now);
check(again.db.months['2026-11'].rx==200,'repeat restore never adds');
empty.days={'2026-10-01':{rx:5,tx:2}};empty.months={'2026-10':{rx:5,tx:2}};
check(csv.prepare(empty,text,'merge',now).db.days['2026-10-01'].rx==5,'merge keeps unlisted dates');
check(csv.prepare(empty,text,'replace',now).db.days['2026-10-01']==null,'replace removes old history');
let conflict=json(sprintf('%J',db));conflict.days['2026-11-15']={rx:999,tx:10};conflict.months['2026-11'].rx+=999;
bad(()=>csv.prepare(conflict,text,'merge',now),'csv_conflict');
check(csv.prepare(conflict,text,'replace',now).db.days['2026-11-15']==null,'replace resolves aggregate conflict');
bad(()=>csv.parse(replace(text,'2026-08-08','2026-02-30')),'csv_invalid');
bad(()=>csv.parse(replace(text,',100,10,110,',',-1,10,9,')),'csv_invalid');
bad(()=>csv.parse(replace(text,',100,10,110,',',1e2,10,110,')),'csv_invalid');
bad(()=>csv.parse(replace(text,',100,10,110,',',100,10,111,')),'csv_invalid');
bad(()=>csv.parse(text+split(text,'\r\n')[2]+'\r\n'),'csv_invalid');
bad(()=>csv.parse(replace(text,'1,1,day','1,0,day')),'csv_invalid');
bad(()=>csv.parse(substr(text,0,60)),'csv_invalid');
bad(()=>csv.prepare(db,text,'add',now),'csv_invalid');
bad(()=>csv.parse(replace(text,',500,50,550,',',50,5,55,')),'csv_conflict');
let actual=csv.prepare(db,text,'replace',now).db;
c.advance(actual,{boot:'new',device:'fake',ifindex:1,rx:1000000,tx:200000},now);
c.advance(actual,{boot:'new',device:'fake',ifindex:1,rx:1000007,tx:200003},now+10);
check(actual.days['2026-11-14'].rx==207 && actual.days['2026-11-14'].tx==23,'only new counter delta after restore');
printf('traffic CSV: %d checks passed\n',checks);
