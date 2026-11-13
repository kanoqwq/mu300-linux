// Pure accounting rules shared by the daemon and deterministic boundary tests.
const BACKUP_FORMAT = 'mu300-traffic-pool';
const MAX_BACKUP_NUMBER = 9007199254740991;

export const defaults = { plan_name: '', monthly_gb: 0, daily_gb: 0,
    reset_day: 1, count_mode: 'total', device: '' };

export function config(input) {
    if (type(input) != 'object') die('invalid_config');
    let c = {};
    for (let k, v in defaults) c[k] = input[k] ?? v;
    if (type(c.plan_name) != 'string' || length(c.plan_name) > 128 ||
        match(c.plan_name, /[[:cntrl:]]/)) die('invalid_config');
    if (type(c.device) != 'string' || (c.device != '' &&
        !match(c.device, /^[a-zA-Z0-9_.:-]{1,15}$/))) die('invalid_config');
    for (let k in ['monthly_gb', 'daily_gb']) {
        if ((type(c[k]) != 'int' && type(c[k]) != 'double') ||
            !(c[k] >= 0 && c[k] <= 100000)) die('invalid_config');
    }
    if (type(c.reset_day) != 'int' || c.reset_day < 1 || c.reset_day > 31 ||
        index(['total', 'rx', 'tx'], c.count_mode) < 0) die('invalid_config');
    return c;
};

export function day_key(now) {
    let t = localtime(now);
    return sprintf('%04d-%02d-%02d', t.year, t.mon, t.mday);
};
function date_epoch(y, m, d) {
    while (m < 1) { m += 12; y--; }
    while (m > 12) { m -= 12; y++; }
    return timelocal({ year: y, mon: m, mday: d, hour: 0, min: 0, sec: 0, isdst: -1 });
}
function reset_epoch(y, m, d) {
    let first = localtime(date_epoch(y, m, 1));
    let last = localtime(date_epoch(first.year, first.mon + 1, 1) - 1).mday;
    return date_epoch(first.year, first.mon, d < last ? d : last);
}
export function cycle(now, reset_day) {
    let t = localtime(now), start = reset_epoch(t.year, t.mon, reset_day), offset = 0;
    if (now < start) { offset = -1; start = reset_epoch(t.year, t.mon - 1, reset_day); }
    let end = reset_epoch(t.year, t.mon + offset + 1, reset_day);
    return { start: day_key(start), end: day_key(end), start_ts: start, end_ts: end };
};
export function fresh(now) {
    return { version: 1, config: config({}), started_at: now, last: null,
        days: {}, months: {}, pending_rx: 0, pending_tx: 0, adjustment: null };
};
// Version-1 ledgers without revision remain valid. A committed clear/config
// change must win over an older RAM snapshot after a failed RAM checkpoint.
export function restore(ram, disk, now) {
    if (disk && (!ram || (disk.revision ?? 0) > (ram.revision ?? 0))) return disk;
    return ram ?? disk ?? fresh(now);
};
function add(db, at, rx, tx) {
    let day = day_key(at), month = substr(day, 0, 7);
    for (let pair in [[db.days, day], [db.months, month]]) {
        let bucket = pair[0][pair[1]] ?? { rx: 0, tx: 0 };
        bucket.rx += rx; bucket.tx += tx;
        pair[0][pair[1]] = bucket;
    }
}
function prune(obj, keep) {
    let list = sort(keys(obj));
    for (let i = 0; i < length(list) - keep; i++) delete(obj, list[i]);
}
export function advance(db, sample, now) {
    db.updated_at = now;
    db.clock_ok = now >= 1704067200;
    db.available = sample != null;
    if (!sample) return;
    let prev = db.last, rx = 0, tx = 0;
    if (prev) {
        if (prev.boot == sample.boot && prev.ifindex == sample.ifindex && prev.device == sample.device) {
            rx = sample.rx >= prev.rx ? sample.rx - prev.rx : sample.rx;
            tx = sample.tx >= prev.tx ? sample.tx - prev.tx : sample.tx;
        }
        else { rx = sample.rx; tx = sample.tx; }
    }
    // First activation only establishes a baseline: earlier traffic cannot be
    // honestly assigned to a day. A reboot/recreated interface adds its new counters.
    db.last = { boot: sample.boot, ifindex: sample.ifindex, device: sample.device,
        rx: sample.rx, tx: sample.tx, at: now };
    if (!db.clock_ok) { db.pending_rx += rx; db.pending_tx += tx; return; }
    let from = prev?.at ?? now;
    if (db.pending_rx || db.pending_tx) {
        rx += db.pending_rx; tx += db.pending_tx;
        db.pending_rx = 0; db.pending_tx = 0; from = now;
    }
    // Split the short sampling interval at local midnight (including DST).
    // After a reboot or clock jump the exact temporal split is unknowable;
    // retain every byte and assign it to the current valid date.
    if (prev && prev.boot == sample.boot && from >= 1704067200 &&
        now > from && now - from <= 120 && day_key(from) != day_key(now)) {
        let t = localtime(from), boundary = date_epoch(t.year, t.mon, t.mday + 1);
        let part_rx = int(rx * (boundary - from) / (now - from));
        let part_tx = int(tx * (boundary - from) / (now - from));
        add(db, from, part_rx, part_tx);
        add(db, now, rx - part_rx, tx - part_tx);
    }
    else add(db, now, rx, tx);
    prune(db.days, 93); prune(db.months, 24);
};
export function clear_records(db, sample, now) {
    let next = fresh(now);
    next.config = config(db.config);
    next.revision = (db.revision ?? 0) + 1;
    // Do not mutate the old ledger: persistence can fail and must leave it intact.
    // First advance establishes the live baseline, never importing old counters.
    advance(next, sample, now);
    return next;
};
export function counted(pair, mode) {
    return mode == 'rx' ? pair.rx : mode == 'tx' ? pair.tx : pair.rx + pair.tx;
};
export function status(db, now) {
    let c = db.config, range = cycle(now, c.reset_day), today = day_key(now);
    let day = db.days[today] ?? { rx: 0, tx: 0 };
    let month = db.months[substr(today, 0, 7)] ?? { rx: 0, tx: 0 };
    let period = { rx: 0, tx: 0 };
    for (let date, data in db.days) if (date >= range.start && date < range.end) {
        period.rx += data.rx; period.tx += data.tx;
    }
    let adjusted = db.adjustment;
    let offset = adjusted && adjusted.start == range.start && adjusted.reset_day == c.reset_day &&
        adjusted.mode == c.count_mode ? adjusted.bytes : 0;
    let used = counted(period, c.count_mode) + offset;
    if (used < 0) used = 0;
    let quota = int(c.monthly_gb * 1000000000), daily_quota = int(c.daily_gb * 1000000000);
    let daily_used = counted(day, c.count_mode);
    return { available: db.available ?? false, clock_ok: now >= 1704067200,
        updated_at: db.updated_at ?? 0, started_at: db.started_at,
        device: db.last?.device ?? '', today: day, month: month, period: period,
        today_used: daily_used, month_used: counted(month, c.count_mode),
        cycle_used: used, cycle_adjustment: offset, cycle_start: range.start, cycle_end: range.end,
        plan_name: c.plan_name, monthly_limit: quota, daily_limit: daily_quota,
        remaining: quota ? (quota > used ? quota - used : 0) : null,
        daily_remaining: daily_quota ? (daily_quota > daily_used ? daily_quota - daily_used : 0) : null,
        over_limit: quota > 0 && used >= quota, daily_over_limit: daily_quota > 0 && daily_used >= daily_quota };
};
export function update_config(db, input, now) {
    let next = config(input);
    if (input.used_gb != null && ((type(input.used_gb) != 'int' && type(input.used_gb) != 'double') ||
        !(input.used_gb >= 0 && input.used_gb <= 100000))) die('invalid_config');
    let changed_device = next.device != db.config.device;
    db.config = next;
    if (changed_device) db.last = null;
    if (input.used_gb != null) {
        let view = status(db, now);
        db.adjustment = { start: view.cycle_start, reset_day: next.reset_day, mode: next.count_mode,
            bytes: int(input.used_gb * 1000000000) - counted(view.period, next.count_mode) };
    }
};

function exact_keys(value, expected) {
    if (type(value) != 'object') return false;
    let found = keys(value);
    if (length(found) != length(expected)) return false;
    for (let name in expected) if (index(found, name) < 0) return false;
    return true;
};

function integer_between(value, minimum, maximum) {
    return (type(value) == 'int' || type(value) == 'double') &&
        value >= minimum && value <= maximum && value == int(value);
};

function valid_day(value) {
    if (type(value) != 'string' || !match(value, /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/)) return false;
    let year = int(substr(value, 0, 4)), month = int(substr(value, 5, 2)), day = int(substr(value, 8, 2));
    if (year < 2000 || month < 1 || month > 12 || day < 1 || day > 31) return false;
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let month_days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return day <= month_days[month - 1];
};

function validate_records(records, limit, is_day) {
    if (type(records) != 'object' || length(keys(records)) > limit) die('invalid_backup');
    for (let date, row in records) {
        if ((is_day && !valid_day(date)) ||
            (!is_day && (!match(date, /^[0-9]{4}-[0-9]{2}$/) || int(substr(date, 0, 4)) < 2000 ||
                int(substr(date, 5, 2)) < 1 || int(substr(date, 5, 2)) > 12)) ||
            !exact_keys(row, ['rx', 'tx']) ||
            !integer_between(row.rx, 0, MAX_BACKUP_NUMBER) || !integer_between(row.tx, 0, MAX_BACKUP_NUMBER))
            die('invalid_backup');
    }
};

export function export_backup(db, now, source_slot) {
    return {
        format: BACKUP_FORMAT,
        version: 1,
        source_slot: source_slot,
        exported_at: now,
        config: config(db.config),
        started_at: db.started_at,
        days: db.days,
        months: db.months,
        pending_rx: db.pending_rx ?? 0,
        pending_tx: db.pending_tx ?? 0,
        adjustment: db.adjustment ?? null
    };
};

export function import_backup(input, current, now) {
    if (!exact_keys(input, ['format', 'version', 'source_slot', 'exported_at', 'config', 'started_at',
            'days', 'months', 'pending_rx', 'pending_tx', 'adjustment']) ||
        input.format != BACKUP_FORMAT || !integer_between(input.version, 1, 1) ||
        !integer_between(input.source_slot, 0, 1) ||
        !integer_between(input.exported_at, 0, 4102444800) ||
        !integer_between(input.started_at, 0, 4102444800) ||
        !exact_keys(input.config, keys(defaults))) die('invalid_backup');

    let saved_config = config(input.config);
    validate_records(input.days, 93, true);
    validate_records(input.months, 24, false);
    if (!integer_between(input.pending_rx, 0, MAX_BACKUP_NUMBER) ||
        !integer_between(input.pending_tx, 0, MAX_BACKUP_NUMBER)) die('invalid_backup');

    let adjustment = input.adjustment;
    if (adjustment != null && (!exact_keys(adjustment, ['start', 'reset_day', 'mode', 'bytes']) ||
        !valid_day(adjustment.start) || !integer_between(adjustment.reset_day, 1, 31) ||
        index(['total', 'rx', 'tx'], adjustment.mode) < 0 ||
        !integer_between(adjustment.bytes, -MAX_BACKUP_NUMBER, MAX_BACKUP_NUMBER))) die('invalid_backup');

    let days = {}, months = {};
    for (let date, row in input.days) days[date] = { rx: row.rx, tx: row.tx };
    for (let month, row in input.months) months[month] = { rx: row.rx, tx: row.tx };
    return {
        version: 1,
        revision: (current.revision ?? 0) + 1,
        config: saved_config,
        started_at: input.started_at,
        last: null,
        days: days,
        months: months,
        pending_rx: input.pending_rx,
        pending_tx: input.pending_tx,
        adjustment: adjustment == null ? null : {
            start: adjustment.start, reset_day: adjustment.reset_day,
            mode: adjustment.mode, bytes: adjustment.bytes
        },
        updated_at: now,
        clock_ok: now >= 1704067200,
        available: false
    };
};
