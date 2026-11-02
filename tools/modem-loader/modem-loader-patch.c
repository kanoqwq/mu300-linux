/* SPDX-License-Identifier: MIT */
/* Unisoc charger boot: recognize the stock loader ABI, not a build ID.
 * Only a RAM copy is changed. No device, AT channel, firmware or NV is opened.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_SEGMENTS 32
#define MAX_FILE (64U * 1024U * 1024U)
#define NOP 0xd503201fU
#define RET 0xd65f03c0U
typedef struct { uint64_t va, off, size, memory; uint32_t flags; } Segment;
typedef struct {
    unsigned char *data;
    size_t size, count;
    unsigned klass;
    Segment seg[MAX_SEGMENTS];
} Elf;
typedef struct { size_t gate, mask; unsigned saved, thumb; } Match;

static uint32_t u32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint64_t u64(const unsigned char *p) { return u32(p) | (uint64_t)u32(p+4)<<32; }
static unsigned u16(const unsigned char *p) { return p[0] | (unsigned)p[1]<<8; }
static void put32(unsigned char *p, uint32_t w)
{
    for (unsigned i=0; i<4; i++) p[i]=(unsigned char)(w>>(8*i));
}
static int range(uint64_t at, uint64_t n, uint64_t size) { return at<=size && n<=size-at; }
static int64_t sx(uint64_t value, unsigned bits)
{
    return (int64_t)(value ^ (UINT64_C(1)<<(bits-1))) - (int64_t)(UINT64_C(1)<<(bits-1));
}
static int mapped(const Elf *e, uint64_t va, size_t n, unsigned flags, size_t *off)
{
    for (size_t i=0; i<e->count; i++) {
        const Segment *s=&e->seg[i];
        if ((s->flags & flags)==flags && va>=s->va && range(va-s->va,n,s->size)) {
            *off=(size_t)(s->off+va-s->va); return 1;
        }
    }
    return 0;
}
static int parse(Elf *e)
{
    const unsigned char *p=e->data;
    if (e->size<52 || memcmp(p,"\177ELF",4) || p[5]!=1 || p[6]!=1 ||
        (u16(p+16)!=2 && u16(p+16)!=3) || u32(p+20)!=1) return 0;
    e->klass=p[4];
    unsigned stride; uint64_t table; unsigned n;
    if (e->klass==2 && e->size>=64 && u16(p+18)==183 && u16(p+52)==64 && u16(p+54)==56) {
        stride=56; table=u64(p+32); n=u16(p+56);
    } else if (e->klass==1 && u16(p+18)==40 && u16(p+40)==52 && u16(p+42)==32) {
        stride=32; table=u32(p+28); n=u16(p+44);
    } else return 0;
    if (!n || n>1024 || !range(table,(uint64_t)n*stride,e->size)) return 0;
    for (unsigned i=0; i<n; i++) {
        const unsigned char *h=p+table+i*stride;
        if (u32(h)!=1) continue;
        if (e->count==MAX_SEGMENTS) return 0;
        Segment s=e->klass==2 ? (Segment){u64(h+16),u64(h+8),u64(h+32),u64(h+40),u32(h+4)} :
                               (Segment){u32(h+8),u32(h+4),u32(h+16),u32(h+20),u32(h+24)};
        if (!range(s.off,s.size,e->size) || s.size>s.memory ||
            s.va>UINT64_MAX-s.memory) return 0;
        for (size_t j=0; j<e->count; j++) {
            const Segment *a=&e->seg[j];
            if (s.memory && a->memory && s.va<a->va+a->memory && a->va<s.va+s.memory) return 0;
        }
        e->seg[e->count++]=s;
    }
    return e->count>0;
}
static int instruction(const Elf *e, uint64_t va, uint32_t *word)
{
    size_t off;
    if ((va&3) || !mapped(e,va,4,1,&off)) return 0;
    *word=u32(e->data+off); return 1;
}
static int literal(const Elf *e, uint64_t va, const char *text)
{
    size_t off, n=strlen(text)+1;
    return mapped(e,va,n,4,&off) && !memcmp(e->data+off,text,n);
}
static int adrp(uint32_t w, uint64_t pc, unsigned *rd, uint64_t *value)
{
    if ((w&0x9f000000U)!=0x90000000U) return 0;
    *rd=w&31;
    *value=(pc&~UINT64_C(4095)) + sx(((w>>5)&0x7ffffU)*4+((w>>29)&3),21)*4096;
    return 1;
}
static int add(uint32_t w, unsigned rd, unsigned rn, uint64_t *imm)
{
    if ((w&0xff800000U)!=0x91000000U || (w&31)!=rd || ((w>>5)&31)!=rn) return 0;
    *imm=(uint64_t)((w>>10)&4095)<<((w&(1U<<22))?12:0); return 1;
}
static int bl(uint32_t w, uint64_t pc, uint64_t *target)
{
    if ((w&0xfc000000U)!=0x94000000U) return 0;
    *target=pc+sx(w&0x3ffffffU,26)*4; return 1;
}
/* The recognizer deliberately supports this proved instruction ABI. A different
 * compiler ABI needs its own semantic recognizer, never a hash/offset fallback.
 */
static size_t body(const Elf *e, uint64_t va, uint32_t *w, uint64_t *pc, size_t cap)
{
    size_t n=0;
    for (size_t i=0; i<1024 && n<cap; i++,va+=4) {
        uint32_t word;
        if (!instruction(e,va,&word)) break;
        if (word==NOP) continue;
        pc[n]=va; w[n++]=word;
        if (word==RET) return n;
    }
    return 0;
}
static int reference(const Elf *e, const uint32_t *w, const uint64_t *pc, size_t n, const char *text)
{
    for (size_t i=0; i<n; i++) {
        unsigned rd; uint64_t base, imm;
        if (!adrp(w[i],pc[i],&rd,&base)) continue;
        for (size_t j=i+1; j<n && j<=i+6; j++) {
            if (add(w[j],rd,rd,&imm) && literal(e,base+imm,text)) return 1;
            /* A call or GPR destination write invalidates this ADRP value.
             * SIMD instructions use a separate register bank.
             */
            if ((w[j]&0xfc000000U)==0x94000000U ||
                (((w[j]>>25)&7)!=7 && (w[j]&31)==rd)) break;
        }
    }
    return 0;
}
static int anchors(const Elf *e, uint64_t va, const char *a, const char *b)
{
    uint32_t w[1024]; uint64_t pc[1024];
    size_t n=body(e,va,w,pc,1024);
    return n && reference(e,w,pc,n,a) && reference(e,w,pc,n,b);
}
/* A stop/start operation takes x1=context, w0=0/1 and calls the same function. */
static int operation(const uint32_t *w, const uint64_t *pc, unsigned state,
                     uint64_t *context, uint64_t *callee)
{
    unsigned rd; uint64_t base, imm;
    int state_ok=w[1]==(0x52800000U|(state<<5)) || (!state && w[1]==0x2a1f03e0U);
    if (!adrp(w[0],pc[0],&rd,&base) || rd!=1 ||
        !state_ok || !add(w[2],1,1,&imm) ||
        !bl(w[3],pc[3],callee)) return 0;
    *context=base+imm; return 1;
}
static int flag_block(const uint32_t *w, const uint64_t *pc, unsigned reg, unsigned bit,
                      unsigned state, uint64_t *context, uint64_t *callee)
{
    uint32_t want=0x36000000U|(bit<<19)|reg;
    uint64_t target=pc[0]+sx((w[0]>>5)&0x3fffU,14)*4;
    return (w[0]&0xfff8001fU)==want && target==pc[4]+4 &&
           operation(w+1,pc+1,state,context,callee);
}
static int nr_block(const uint32_t *w, const uint64_t *pc, unsigned reg,
                    unsigned state, uint64_t *context, uint64_t *callee)
{
    unsigned tmp=w[0]&31;
    uint64_t target=pc[2]+sx((w[2]>>5)&0x7ffffU,19)*4;
    return tmp<31 && w[0]==(0x52800140U|tmp) &&
           w[1]==(0x6a00001fU|(tmp<<16)|(reg<<5)) &&
           (w[2]&0xff00001fU)==0x54000000U && target==pc[6]+4 &&
           operation(w+3,pc+3,state,context,callee);
}
static int load_protocol(const Elf *e, uint64_t va)
{
    uint32_t w[1024]; uint64_t pc[1024];
    size_t n=body(e,va,w,pc,1024); unsigned reg=31;
    if (!n || !reference(e,w,pc,n,"load_modem_img")) return 0;
    for (size_t i=0; i<n && i<20; i++)
        if ((w[i]&0xffffffe0U)==0x2a0003e0U && (w[i]&31)>=19 && (w[i]&31)<=28) {
            if (reg!=31) return 0;
            reg=w[i]&31;
        }
    if (reg==31) return 0;
    /* Prove bit0=PM and bit5=CH are independent, and NR mask=0x0a.
     * All three contexts must have matching stop/start calls. The PM predicate
     * and original 63 mask are checked separately at the caller.
     */
    uint64_t contexts[2][3]={{0}}, callee=0; unsigned counts[2][3]={{0}};
    for (size_t i=0; i+6<n; i++) {
        for (unsigned state=0; state<2; state++) for (unsigned part=0; part<3; part++) {
            uint64_t ctx, fn;
            int ok=part<2 ? flag_block(w+i,pc+i,reg,part?5:0,state,&ctx,&fn) :
                            nr_block(w+i,pc+i,reg,state,&ctx,&fn);
            if (!ok) continue;
            if (counts[state][part]++ || (callee && callee!=fn)) return 0;
            size_t off; int context_ok=0;
            for (size_t k=0; k<e->count; k++) {
                const Segment *s=&e->seg[k];
                if ((s->flags&6)==6 && ctx>=s->va && range(ctx-s->va,8,s->memory)) context_ok=1;
            }
            if (!context_ok || !mapped(e,fn,4,1,&off)) return 0;
            contexts[state][part]=ctx; callee=fn;
        }
    }
    for (unsigned p=0; p<3; p++)
        if (counts[0][p]!=1 || counts[1][p]!=1 || contexts[0][p]!=contexts[1][p]) return 0;
    return contexts[0][0]!=contexts[0][1] && contexts[0][0]!=contexts[0][2] &&
           contexts[0][1]!=contexts[0][2];
}
static int candidate(const Elf *e, uint64_t start, Match *m)
{
    uint32_t w[20]; uint64_t pc[20]; uint64_t va=start;
    for (unsigned i=0; i<20; i++,va+=4) {
        unsigned padding=0;
        do {
            if (!instruction(e,va,&w[i])) return 0;
            if (w[i]!=NOP) break;
            va+=4;
        } while (++padding<16);
        if (w[i]==NOP) return 0;
        pc[i]=va;
    }
    uint64_t pm, parse_fn, load, base, imm;
    unsigned rd, saved=w[1]&31, textreg=w[6]&31, wordreg=w[7]&31, bytereg=w[9]&31;
    if (!bl(w[0],pc[0],&pm) || saved<19 || saved>28 ||
        w[1]!=(0x2a0003e0U|saved) || !adrp(w[2],pc[2],&rd,&base) || rd!=0 ||
        !add(w[3],0,0,&imm) || !literal(e,base+imm,"modem")) return 0;
    uint64_t stack=0; uint32_t load_word, load_byte;
    if (add(w[4],1,31,&stack) && stack%8==0 && stack+8<=4095) {
        load_word=0xf9400000U|((unsigned)(stack/8)<<10)|(31<<5)|wordreg;
        load_byte=0x39400000U|((unsigned)(stack+8)<<10)|(31<<5)|bytereg;
    } else if ((w[4]&0xffc003ffU)==0xd10003a1U) {
        /* SUB x1,x29,#n; LDUR/LDURB through the same frame-relative buffer. */
        unsigned distance=(w[4]>>10)&4095;
        if (distance<8 || distance>256) return 0;
        load_word=0xf8400000U|(((0U-distance)&511)<<12)|(29<<5)|wordreg;
        load_byte=0x38400000U|(((8U-distance)&511)<<12)|(29<<5)|bytereg;
    } else return 0;
    if (!bl(w[5],pc[5],&parse_fn) ||
        textreg>=31 || wordreg>=31 || bytereg>=31 || textreg==wordreg ||
        bytereg==textreg || bytereg==wordreg || bytereg==saved || textreg==saved ||
        wordreg==saved) return 0;
    unsigned result=w[15]&31;
    if (result>=31 || result==saved ||
        w[6]!=(0xd2800000U|(0x6873U<<5)|textreg) ||
        w[7]!=load_word ||
        w[8]!=(0xf2a00000U|(0x7475U<<5)|textreg) ||
        w[9]!=load_byte ||
        w[10]!=(0xf2c00000U|(0x6f64U<<5)|textreg) ||
        w[11]!=(0xf2e00000U|(0x6e77U<<5)|textreg) ||
        w[12]!=(0xca000000U|(textreg<<16)|(wordreg<<5)|wordreg) ||
        w[13]!=(0xaa000000U|(bytereg<<16)|(wordreg<<5)|wordreg) ||
        w[14]!=(0xf100001fU|(wordreg<<5)) ||
        w[15]!=(0x1a9f07e0U|result) ||
        w[16]!=(0x2a000000U|(saved<<16)|(result<<5)|result) ||
        (w[17]&0xff00001fU)!=(0x35000000U|result) ||
        pc[17]+sx((w[17]>>5)&0x7ffffU,19)*4!=pc[19]+4 ||
        w[18]!=0x528007e0U || !bl(w[19],pc[19],&load)) return 0;
    if (!anchors(e,pm,"/dev/sctl_pm","modem_has_been_boot") ||
        !anchors(e,parse_fn,"/proc/cmdline","modem_ctrl_parse_cmdline") ||
        !load_protocol(e,load)) return 0;
    if (!mapped(e,pc[1],4,1,&m->gate) || !mapped(e,pc[18],4,1,&m->mask)) return 0;
    m->saved=saved; m->thumb=0; return 1;
}
/* Thumb-2 uses a memcmp(buf,"shutdown",9) call instead of an inline compare.
 * Resolve its ARM PLT/GOT relocation to prove the callee really is memcmp.
 */
static uint32_t arm_imm(uint32_t w)
{
    unsigned shift=((w>>8)&15)*2; uint32_t v=w&255;
    return shift ? (v>>shift)|(v<<(32-shift)) : v;
}
static int memcmp_plt(const Elf *e, uint64_t target)
{
    size_t off;
    if (!mapped(e,target,12,1,&off)) return 0;
    uint32_t a=u32(e->data+off), b=u32(e->data+off+4), c=u32(e->data+off+8);
    if ((a&0xfffff000U)!=0xe28fc000U || (b&0xfffff000U)!=0xe28cc000U ||
        (c&0xfffff000U)!=0xe5bcf000U) return 0;
    uint32_t got=(uint32_t)target+8+arm_imm(a)+arm_imm(b)+(c&4095);
    const unsigned char *p=e->data;
    uint64_t table=u32(p+28), syms=0, strings=0, rels=0, bytes=0, strsz=0;
    for (unsigned i=0; i<u16(p+44); i++) {
        const unsigned char *h=p+table+i*32;
        if (u32(h)!=2) continue;
        uint64_t at=u32(h+4), size=u32(h+16);
        if (!range(at,size,e->size) || size>65536 || size%8) return 0;
        for (uint64_t k=0; k<size; k+=8) {
            uint32_t tag=u32(p+at+k), value=u32(p+at+k+4);
            if (!tag) break;
            if (tag==2) bytes=value;
            if (tag==5) strings=value;
            if (tag==6) syms=value;
            if (tag==10) strsz=value;
            if (tag==11 && value!=16) return 0;
            if (tag==20 && value!=17) return 0; /* DT_PLTREL must be DT_REL */
            if (tag==23) rels=value;
        }
    }
    if (!syms || !strings || !strsz || !rels || !bytes || bytes>MAX_FILE || bytes%8 ||
        !mapped(e,rels,(size_t)bytes,4,&off)) return 0;
    for (size_t k=0; k<bytes; k+=8) {
        uint32_t address=u32(p+off+k), info=u32(p+off+k+4);
        if (address!=got || (info&255)!=22) continue;
        size_t symbol;
        if (!mapped(e,syms+(uint64_t)(info>>8)*16,16,4,&symbol)) return 0;
        unsigned name=u32(p+symbol);
        return range(name,7,strsz) && literal(e,strings+name,"memcmp");
    }
    return 0;
}
static int thumb_insn(const Elf *e, uint64_t va, uint32_t *word, unsigned *width)
{
    size_t off;
    if ((va&1) || !mapped(e,va,2,1,&off)) return 0;
    unsigned h=u16(e->data+off);
    *width=(h&0xf800)>=0xe800 ? 4:2;
    if (!mapped(e,va,*width,1,&off)) return 0;
    *word=*width==4 ? u32(e->data+off) : h;
    return 1;
}
static int thumb_call(uint32_t w, uint64_t pc, int exchange, uint64_t *target)
{
    unsigned a=w&65535, b=w>>16;
    if ((a&0xf800)!=0xf000 || (b&0xd001)!=(exchange?0xc000U:0xd001U)) {
        /* BL has no restriction on bit0 of its immediate. */
        if (exchange || (a&0xf800)!=0xf000 || (b&0xd000)!=0xd000) return 0;
    }
    unsigned s=(a>>10)&1, i1=1^((b>>13)&1)^s, i2=1^((b>>11)&1)^s;
    uint32_t imm=(s<<24)|(i1<<23)|(i2<<22)|((a&1023)<<12)|((b&2047)<<1);
    uint64_t base=pc+4;
    if (exchange) base&=~UINT64_C(3);
    *target=base+sx(imm,25); return 1;
}
static size_t thumb_body(const Elf *e, uint64_t va, uint32_t *w, uint64_t *pc)
{
    for (size_t n=0; n<1024; n++) {
        unsigned width;
        if (!thumb_insn(e,va,&w[n],&width)) return 0;
        pc[n]=va; va+=width;
        if ((w[n]&0xff00)==0xbd00 && width==2) return n+1;
    }
    return 0;
}
static int thumb_address(const Elf *e, uint32_t ldr, uint64_t pc, uint32_t plus,
                         uint64_t add_pc, unsigned reg, uint64_t *value)
{
    size_t off;
    if ((ldr&0xff00)!=(0x4800|(reg<<8)) || plus!=(0x4478|reg) ||
        !mapped(e,((pc+4)&~UINT64_C(3))+(ldr&255)*4,4,4,&off)) return 0;
    *value=(uint32_t)(u32(e->data+off)+add_pc+4); return 1;
}
static int thumb_ref(const Elf *e, const uint32_t *w, const uint64_t *pc, size_t n, const char *s)
{
    for (size_t i=0; i<n; i++) {
        if ((w[i]&0xf800)!=0x4800) continue;
        unsigned reg=(w[i]>>8)&7;
        for (size_t j=i+1; j<n && j<i+6; j++) {
            uint64_t address;
            if (thumb_address(e,w[i],pc[i],w[j],pc[j],reg,&address) && literal(e,address,s)) return 1;
            uint64_t target;
            if (thumb_call(w[j],pc[j],0,&target) || thumb_call(w[j],pc[j],1,&target)) break;
        }
    }
    return 0;
}
static int thumb_anchors(const Elf *e, uint64_t va, const char *a, const char *b)
{
    uint32_t w[1024]; uint64_t pc[1024]; size_t n=thumb_body(e,va,w,pc);
    return n && thumb_ref(e,w,pc,n,a) && thumb_ref(e,w,pc,n,b);
}
static int thumb_branch(uint32_t w, uint64_t pc, unsigned condition, uint64_t end)
{
    return (w&0xff00)==(0xd000|(condition<<8)) && pc+4+sx(w&255,8)*2==end;
}
static int thumb_cbz(uint32_t w, uint64_t pc, unsigned reg, uint64_t end)
{
    return (w&0xfd07)==(0xb100|reg) && pc+4+((w>>3)&31)*2+((w>>9)&1)*64==end;
}
static int thumb_load(const Elf *e, uint64_t va)
{
    uint32_t w[1024]; uint64_t pc[1024]; size_t n=thumb_body(e,va,w,pc);
    if (!n || !thumb_ref(e,w,pc,n,"load_modem_img")) return 0;
    unsigned flags=8;
    /* BIC effective,original,#10 followed by the conditional restoration is
     * the stock wifi-only adjustment. All dispatches must use effective.
     */
    for (size_t i=0; i+3<n && i<20; i++) {
        unsigned a=w[i]&65535, b=w[i]>>16, original=a&15, dest=(b>>8)&15;
        if ((a&0xfff0)!=0xf020 || (b&0xf0ff)!=10 || original<4 || original>7 || dest<4 || dest>7) continue;
        int from_argument=0;
        for (size_t k=0; k<i; k++) if (w[k]==(0x4600|original)) from_argument=1;
        if (!from_argument) continue;
        if (w[i+2]!=0x2800 || w[i+3]!=0xbf08 || i+4>=n ||
            w[i+4]!=(0x4600|(original<<3)|dest)) continue;
        if (flags!=8) return 0;
        flags=dest;
    }
    if (flags==8) return 0;
    uint64_t contexts[2][3]={{0}}, callee=0; unsigned counts[2][3]={{0}};
    for (size_t i=3; i+3<n; i++) {
        uint64_t ctx, fn, end=pc[i+3]+4;
        if (!thumb_address(e,w[i],pc[i],w[i+2],pc[i+2],1,&ctx) ||
            !thumb_call(w[i+3],pc[i+3],0,&fn) || (w[i+1]!=0x2000 && w[i+1]!=0x2001)) continue;
        unsigned state=w[i+1]&1, part=3;
        if (w[i-2]==(0x0f0af010U|flags) && thumb_branch(w[i-1],pc[i-1],0,end)) part=2;
        else if (!state) {
            unsigned out=(w[i-3]>>24)&15;
            if (out<8 && w[i-3]==((out<<24)|0x0001f000U|flags) &&
                thumb_call(w[i-2],pc[i-2],0,&fn) && thumb_cbz(w[i-1],pc[i-1],out,end)) part=0;
            out=(w[i-2]>>24)&15;
            if (out<8 && w[i-2]==((out<<24)|0x0020f010U|flags) &&
                thumb_branch(w[i-1],pc[i-1],0,end)) part=1;
        } else {
            if (w[i-2]==(0x07c0U|(flags<<3)) && thumb_branch(w[i-1],pc[i-1],0,end)) part=0;
            if (w[i-2]==(0x0680U|(flags<<3)) && thumb_branch(w[i-1],pc[i-1],5,end)) part=1;
        }
        if (part==3) continue;
        if (!thumb_call(w[i+3],pc[i+3],0,&fn) || counts[state][part]++ || (callee && callee!=fn)) return 0;
        int valid=0; size_t off;
        for (size_t j=0; j<e->count; j++) {
            const Segment *s=&e->seg[j];
            if ((s->flags&6)==6 && ctx>=s->va && range(ctx-s->va,8,s->memory)) valid=1;
        }
        if (!valid || !mapped(e,fn,2,1,&off)) return 0;
        contexts[state][part]=ctx; callee=fn;
    }
    for (unsigned p=0; p<3; p++)
        if (counts[0][p]!=1 || counts[1][p]!=1 || contexts[0][p]!=contexts[1][p]) return 0;
    return contexts[0][0]!=contexts[0][1] && contexts[0][0]!=contexts[0][2] && contexts[0][1]!=contexts[0][2];
}
static int thumb_candidate(const Elf *e, uint64_t start, Match *m)
{
    uint32_t w[16]; uint64_t pc[16]; uint64_t va=start;
    for (unsigned i=0; i<16; i++) {
        unsigned width;
        if (!thumb_insn(e,va,&w[i],&width)) return 0;
        pc[i]=va; va+=width;
    }
    unsigned saved=w[1]&7, buffer=(w[3]>>8)&7;
    uint64_t pm, parser, compare, load, text, shutdown;
    if (!thumb_call(w[0],pc[0],0,&pm) || saved<4 || buffer<4 || saved==buffer ||
        w[1]!=(0x4600|saved) || (w[3]&0xf800)!=0xa800 ||
        w[4]!=(0x4601|(buffer<<3)) || w[8]!=(0x4600|(buffer<<3)) ||
        !thumb_address(e,w[2],pc[2],w[5],pc[5],0,&text) || !literal(e,text,"modem") ||
        !thumb_call(w[6],pc[6],0,&parser) ||
        !thumb_address(e,w[7],pc[7],w[10],pc[10],1,&shutdown) || !literal(e,shutdown,"shutdown") ||
        w[9]!=0x2209 || !thumb_call(w[11],pc[11],1,&compare) || !memcmp_plt(e,compare) ||
        w[12]!=(0x4300|(saved<<3)) || !thumb_branch(w[13],pc[13],1,pc[15]+4) ||
        w[14]!=0x203f || !thumb_call(w[15],pc[15],0,&load) ||
        !thumb_anchors(e,pm,"/dev/sctl_pm","modem_has_been_boot") ||
        !thumb_anchors(e,parser,"/proc/cmdline","modem_ctrl_parse_cmdline") || !thumb_load(e,load)) return 0;
    if (!mapped(e,pc[1],2,1,&m->gate) || !mapped(e,pc[14],2,1,&m->mask)) return 0;
    m->saved=saved; m->thumb=1; return 1;
}
static int transfer(int fd, unsigned char *p, size_t n, int writing)
{
    while (n) {
        ssize_t got=writing ? write(fd,p,n) : read(fd,p,n);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) return 0;
        p+=got; n-=(size_t)got;
    }
    return 1;
}
int main(int argc, char **argv)
{
    if (argc!=3 && argc!=4) { fprintf(stderr,"usage: %s inspect SOURCE | patch SOURCE RAM_COPY\n",argv[0]); return 2; }
    int patch=!strcmp(argv[1],"patch");
    if ((patch && argc!=4) || (!patch && (strcmp(argv[1],"inspect") || argc!=3))) return 2;
    int input=open(argv[2],O_RDONLY|O_CLOEXEC|O_NONBLOCK); struct stat st; Elf e={0};
    if (input<0 || fstat(input,&st) || !S_ISREG(st.st_mode) || st.st_size<52 || st.st_size>MAX_FILE) {
        fprintf(stderr,"loader is not a readable ELF32 ARM / ELF64 AArch64 regular file\n");
        if (input>=0) close(input);
        return 1;
    }
    e.size=(size_t)st.st_size; e.data=malloc(e.size);
    if (!e.data || !transfer(input,e.data,e.size,0)) { close(input); free(e.data); return 1; }
    close(input);
    if (!parse(&e)) { fprintf(stderr,"invalid ELF32 ARM / ELF64 AArch64 loader layout\n"); free(e.data); return 1; }
    unsigned matches=0; Match found={0};
    for (size_t s=0; s<e.count; s++) {
        const Segment *seg=&e.seg[s];
        if (!(seg->flags&1)) continue;
        for (uint64_t at=0; range(at,4,seg->size); at+=e.klass==2?4:2) {
            uint32_t word=u32(e.data+seg->off+at);
            if (e.klass==2 ? (word&0xfc000000U)!=0x94000000U : (word&0xf800)!=0xf000) continue;
            Match m;
            if (e.klass==2 ? candidate(&e,seg->va+at,&m) : thumb_candidate(&e,seg->va+at,&m)) { found=m; matches++; }
        }
    }
    if (matches!=1) {
        fprintf(stderr,"cannot verify selective loader ABI: %u matching boot paths (need exactly one); original unchanged\n",matches);
        free(e.data); return 1;
    }
    fprintf(stderr,"verified loader ABI: PM gate=0x%zx, load mask=0x%zx; CP|CH=34, PM preserved\n",found.gate,found.mask);
    if (!patch) { free(e.data); return 0; }
    /* Never open/truncate output until every semantic check has passed. */
    int out=open(argv[3],O_WRONLY|O_CREAT|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK,0700);
    struct stat dst;
    if (out<0 || fstat(out,&dst) || !S_ISREG(dst.st_mode) ||
        (st.st_dev==dst.st_dev && st.st_ino==dst.st_ino)) {
        fprintf(stderr,"RAM output must be a separate regular file\n");
        if (out>=0) close(out);
        free(e.data); return 1;
    }
    if (found.thumb) {
        e.data[found.gate]=0; e.data[found.gate+1]=(unsigned char)(0x20|found.saved);
        e.data[found.mask]=34; e.data[found.mask+1]=0x20;
    } else {
        put32(e.data+found.gate,0x2a1f03e0U|found.saved);
        put32(e.data+found.mask,0x52800440U);
    }
    int ok=!ftruncate(out,0) && transfer(out,e.data,e.size,1) && !fsync(out);
    if (close(out)) ok=0;
    /* Compare the complete copy, including bytes outside the two instructions. */
    out=open(argv[3],O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
    unsigned char block[4096];
    if (out<0 || fstat(out,&dst) || dst.st_size!=(off_t)e.size) ok=0;
    for (size_t at=0; ok && at<e.size;) {
        size_t n=e.size-at; if (n>sizeof(block)) n=sizeof(block);
        if (!transfer(out,block,n,0) || memcmp(block,e.data+at,n)) ok=0;
        at+=n;
    }
    if (out>=0 && close(out)) ok=0;
    if (!ok) fprintf(stderr,"RAM loader copy failed write/readback verification\n");
    free(e.data); return ok?0:1;
}
