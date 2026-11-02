// MU5358 banks provide independent owners; firmware still needs SPACTCARD. No I/O.
export function slot(device) {
    let m=match(device || '',/^\/dev\/stty_nr([0-5])$/);
    return m?int(int(m[1])/3):null;
};
export function wire(device,command) {
    let card=slot(device), scoped=match(command || '',/^AT\+SPACTCARD=([01]);/);
    if(card==null || (scoped && int(scoped[1])!=card))return null;
    return command;
};
