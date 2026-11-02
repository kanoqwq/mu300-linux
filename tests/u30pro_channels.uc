import * as channel from 'mu300.native-channel';
let count=0;
function check(condition) {if(!condition)die('Native SIM channel routing failed');count++;}
for(let n=0;n<6;n++) {
    let card=int(n/3),other=1-card,device='/dev/stty_nr'+n;
    check(channel.slot(device)==card);
    check(channel.wire(device,'AT+SPACTCARD='+card+';+CPIN?')=='AT+SPACTCARD='+card+';+CPIN?');
    check(channel.wire(device,'AT+SPACTCARD='+other+';+SFUN=5')==null);
    check(channel.wire(device,'AT+CGDATA="M-ETHER",1')=='AT+CGDATA="M-ETHER",1');
}
check(channel.slot('/dev/stty_nr31')==null);
check(channel.wire('/dev/stty_nr31','AT')==null);
printf('Native dual SIM channel mapping: %d assertions passed; no AT/device access\n',count);
