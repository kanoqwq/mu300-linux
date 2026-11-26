// One boot deadline, not a polling loop. Charger-only time is not normal uptime.
export function delay_ms(uptime,after_charging) {
    if(after_charging || (type(uptime)!='double' && type(uptime)!='int') || uptime<0)return 60000;
    return max(0,60000-int(uptime*1000));
};
