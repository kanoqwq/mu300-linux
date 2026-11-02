// Display-only names from the maintained dashboard's PLMN/alias mapping.
// Preserve COPS parsing, registration state and the shared AT owner's order.
const PLMN_CN={
    '46000':'中国移动','46002':'中国移动','46004':'中国移动','46007':'中国移动','46008':'中国移动',
    '46001':'中国联通','46006':'中国联通','46009':'中国联通',
    '46003':'中国电信','46005':'中国电信','46011':'中国电信','46012':'中国电信',
    '46015':'中国广电','46020':'中国铁通'
};
const ALIASES={CHINAMOBILE:'中国移动',CHNMOBILE:'中国移动',CMCC:'中国移动','中国移动通信':'中国移动',
    CHINAUNICOM:'中国联通',CHNUNICOM:'中国联通',UNICOM:'中国联通',CUCC:'中国联通',
    CHINATELECOM:'中国电信',CHNCT:'中国电信',CHNCTLTE:'中国电信',CTCC:'中国电信',
    CHINABROADNET:'中国广电',CHNBG:'中国广电',CBN:'中国广电',CHINABROADCASTINGNETWORK:'中国广电'};
export function carrier_name(op) {
    let name=type(op?.name)=='string'?trim(op.name):'';
    let plmn=type(op?.plmn)=='string'?trim(op.plmn):'';
    if(name=='' || match(name,/^[0-9]{5,6}$/))return PLMN_CN[name] || PLMN_CN[plmn] || name || plmn;
    return ALIASES[replace(uc(name),/[[:space:]_-]/g,'')] || name;
};
