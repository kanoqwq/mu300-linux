#!/bin/sh
# Decode one CID's CGCONTRDP records. IPv6 is an RA hint, never a static /64.
# Output variables are committed together only after a complete valid reply.
pdp_address_parse() {
    local parsed
    parsed=$(printf '%s\n' "$1" | awk -F, -v cid="${2:-1}" '
    function octets(s,a, n,i) {
        n=split(s,a,".")
        for(i=1;i<=n;i++) if(a[i]!~/^[0-9]+$/ || a[i]+0>255) return 0
        return n
    }
    function v6(s, a,n,i,k,c) {
        if(s!~/^[0-9a-fA-F:]+$/ || s=="::" || s~/:::/) return ""
        c=index(s,"::"); k=s; gsub(/::/,"|",k)
        if(k~/\|.*\|/) return ""
        if(!c && (s~/^:/ || s~/:$/)) return ""
        n=split(s,a,":"); k=0
        for(i=1;i<=n;i++) if(a[i]!="") {
            if(length(a[i])>4) return ""
            k++
        }
        if((!c && k!=8) || (c && k>=8)) return ""
        k=s; gsub(/[:0]/,"",k); if(k=="") return ""
        return tolower(s)
    }
    function dotted6(a, s,i,sum) {
        sum=0; s=""
        for(i=1;i<=16;i++) {sum+=a[i]; if(i%2) s=s sprintf("%s%x",i>1?":":"",a[i]*256+a[i+1])}
        return sum ? s : ""
    }
    function address(s, a,n) {
        if(index(s,":")) return v6(s)
        n=octets(s,a)
        if(n==16) return dotted6(a)
        if(n==4 && a[1]+a[2]+a[3]+a[4]>0) return (a[1]+0) "." (a[2]+0) "." (a[3]+0) "." (a[4]+0)
        return ""
    }
    function dns(s, d) {
        if(s=="" || s~/^[0.:]+$/) return
        d=address(s)
        if(d=="") { if(s!~/^[0.]+$/) bad=1; return }
        if(d==d1 || d==d2) return
        if(d1=="") d1=d; else if(d2=="") d2=d
    }
    /^\+CGCONTRDP:/ {
        for(i=1;i<=NF;i++) gsub(/["\r[:space:]]/,"",$i)
        split($1,id,":"); if(id[2]!=cid) next
        n=octets($4,a)
        if(n==4 || n==8) {
            ip=(a[1]+0) "." (a[2]+0) "." (a[3]+0) "." (a[4]+0)
            if(ip!="0.0.0.0") {
                bits=0; partial=0
                for(i=5;i<=8;i++) {
                    o=(n==4?255:a[i]+0)
                    if(partial && o!=0) bad=1
                    if(o==255) bits+=8
                    else {partial=1; if(o==254)bits+=7; else if(o==252)bits+=6;
                        else if(o==248)bits+=5; else if(o==240)bits+=4;
                        else if(o==224)bits+=3; else if(o==192)bits+=2;
                        else if(o==128)bits++; else if(o!=0)bad=1}
                }
                ip4=ip; prefix=bits
            }
        } else if(n==16 || n==32) ip6=dotted6(a)
        else if(index($4,":")) ip6=v6($4)
        else if($4!="") bad=1
        dns($6); dns($7)
    }
    END {
        if(bad || (ip4=="" && ip6=="")) exit 1
        print ip4 "|" prefix "|" ip6 "|" d1 "|" d2
    }') || return 1
    IFS='|' read -r PDP_IP4 PDP_PREFIX4 PDP_IP6 PDP_DNS1 PDP_DNS2 <<-EOF
	$parsed
	EOF
}
