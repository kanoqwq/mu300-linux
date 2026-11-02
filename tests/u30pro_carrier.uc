import { carrier_name } from 'mu300.carrier';
const cases=[
 [{plmn:'46000'},'中国移动'],[{name:'46002',plmn:'46002'},'中国移动'],
 [{plmn:'46001'},'中国联通'],[{plmn:'46011'},'中国电信'],[{plmn:'46015'},'中国广电'],
 [{plmn:'46020'},'中国铁通'],[{plmn:'00101'},'00101'],
 [{name:'China Mobile',plmn:'46000'},'中国移动'],[{name:'CHN-UNICOM'},'中国联通'],
 [{name:'CHN-CT'},'中国电信'],[{name:'CHINA_BROADNET'},'中国广电'],
 [{name:'Turkcell',plmn:'28601'},'Turkcell'],[{name:'Custom card brand',plmn:'46000'},'Custom card brand'],
 [null,''],[{},'']
];
for(let row in cases)if(carrier_name(row[0])!=row[1])die('carrier display mismatch: '+sprintf('%J',row));
print('PASS 15 operator display cases; COPS parsing and AT ownership unchanged\n');
