// One bounded, versioned report/backup format across all platform adapters.
// No shell commands, filesystem paths or modem identity come from this CSV.
import * as core from './traffic-core.uc';

const HEADER = 'format_version,slot,record_type,date,rx_bytes,tx_bytes,total_bytes,exported_at';
const MAX = 9007199254740991;
function bytes(value) {
    if (type(value) == 'string' && match(value, /^(0|[1-9][0-9]*)$/)) value = int(value);
    if (type(value) != 'int' || value < 0 || value > MAX) die('csv_invalid');
    return value;
}
function pair(row) {
    let rx = bytes(row.rx), tx = bytes(row.tx);
    if (rx + tx > MAX) die('csv_invalid');
    return {rx, tx};
}
function date_key(value, kind) {
    let pattern = kind == 'day' ? /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/ : /^[0-9]{4}-[0-9]{2}$/;
    if (!match(value, pattern)) die('csv_invalid');
    let p = split(value, '-'), day = kind == 'day' ? int(p[2]) : 1;
    let at = timelocal({year:int(p[0]),mon:int(p[1]),mday:day,hour:12,min:0,sec:0,isdst:-1});
    if (substr(core.day_key(at), 0, length(value)) != value) die('csv_invalid');
    return value;
}
// RFC 4180 quoting and CRLF; BOM accepted for spreadsheet round trips.
function rows(text) {
    if (type(text) != 'string' || length(text) > 65536) die('csv_invalid');
    if (substr(text, 0, 3) == '\xef\xbb\xbf') text = substr(text, 3);
    let result = [], row = [], field = '', quoted = false, closed = false;
    for (let i = 0; i < length(text); i++) {
        let c = substr(text, i, 1);
        if (quoted) {
            if (c == '"') {
                if (substr(text, i+1, 1) == '"') { field += '"'; i++; }
                else { quoted = false; closed = true; }
            } else field += c;
        } else if (c == '"' && field == '' && !closed) quoted = true;
        else if (c == ',' || c == '\n' || c == '\r') {
            push(row, field); field = ''; closed = false;
            if (c != ',') {
                if (c == '\r' && substr(text,i+1,1) == '\n') i++;
                push(result, row); row = [];
            }
        } else {
            if (closed || c == '"') die('csv_invalid');
            field += c;
        }
        if (length(result) > 120 || length(row) > 8 || length(field) > 128) die('csv_invalid');
    }
    if (quoted) die('csv_invalid');
    if (length(row) || field != '' || closed) { push(row,field); push(result,row); }
    return result;
}
function validate_totals(days,months) {
    let sums = {}, oldest = sort(keys(months))[0];
    for (let date, value in days) {
        let month = substr(date,0,7), s = sums[month] ?? {rx:0,tx:0};
        s.rx += value.rx; s.tx += value.tx; sums[month] = s;
    }
    for (let month,s in sums) {
        // Sparse daily history may outlive the 24 retained month buckets.
        // That missing aggregate is unknown, not a total we may invent.
        if (!months[month]) {
            if (oldest != null && month < oldest) continue;
            die('csv_conflict');
        }
        if (months[month].rx < s.rx || months[month].tx < s.tx) die('csv_conflict');
    }
}
export function parse(text) {
    let data = rows(text);
    if (!length(data) || join(',', shift(data)) != HEADER) die('csv_invalid');
    let result = {days:{},months:{},slot:null,exported_at:null}, metadata = false;
    for (let row in data) {
        if (length(row) != 8 || row[0] != '1' || !(row[1] in ['0','1'])) die('csv_invalid');
        let stamp = bytes(row[7]);
        if (stamp < 1704067200) die('csv_invalid');
        if (result.slot != null && (result.slot != int(row[1]) || result.exported_at != stamp)) die('csv_invalid');
        result.slot = int(row[1]); result.exported_at = stamp;
        let value = pair({rx:row[4],tx:row[5]});
        if (bytes(row[6]) != value.rx + value.tx) die('csv_invalid');
        if (row[2] == 'meta') {
            if (metadata || row[3] != '' || value.rx || value.tx) die('csv_invalid');
            metadata = true; continue;
        }
        if (!(row[2] in ['day','month'])) die('csv_invalid');
        let key = date_key(row[3],row[2]), table = row[2] == 'day' ? result.days : result.months;
        if (table[key] != null) die('csv_invalid');
        table[key] = value;
    }
    if (!metadata || length(keys(result.days)) > 93 || length(keys(result.months)) > 24) die('csv_invalid');
    validate_totals(result.days,result.months);
    return result;
};
export function encode(db,slot,now) {
    let lines = [HEADER, sprintf('1,%d,meta,,0,0,0,%d',slot,now)];
    for (let kind in ['day','month']) {
        let table = kind == 'day' ? db.days : db.months;
        for (let date in sort(keys(table))) {
            let p = pair(table[date]);
            push(lines,sprintf('1,%d,%s,%s,%d,%d,%d,%d',slot,kind,date,p.rx,p.tx,p.rx+p.tx,now));
        }
    }
    return join('\r\n',lines) + '\r\n';
};
export function prepare(db,text,mode,now) {
    if (!(mode in ['merge','replace'])) die('csv_invalid');
    let data = parse(text), next = json(sprintf('%J',db));
    if (mode == 'replace') { next.days = {}; next.months = {}; }
    let preview = {source_slot:data.slot, exported_at:data.exported_at,days:0,months:0,conflicts:0,
        first:null,last:null,mode};
    for (let kind in ['days','months']) {
        for (let key,value in data[kind]) {
            preview[kind]++;
            if (db[kind][key] != null) preview.conflicts++;
            if (preview.first == null || key < preview.first) preview.first = key;
            if (preview.last == null || key > preview.last) preview.last = key;
            next[kind][key] = value;
        }
        if (length(keys(next[kind])) > (kind == 'days' ? 93 : 24)) die('csv_retention');
    }
    validate_totals(next.days,next.months);
    // Preserve the user's plan and calibration. Never import kernel counters,
    // paths, devices, command strings or a last sample from another machine.
    next.last = null; next.pending_rx = 0; next.pending_tx = 0;
    next.revision = (db.revision ?? 0) + 1;
    return {db:next,preview};
};
