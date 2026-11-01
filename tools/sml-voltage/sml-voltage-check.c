/* SPDX-License-Identifier: GPL-2.0-only */
/* Read-only Unisoc DVFS_INIT ABI recognizer. Never executes firmware or SMC.
 * Locate linked instructions, not fixed file offsets or firmware identities.
 * Recognizes the two audited vendor implementations; unfamiliar code is not
 * proof of incompatibility, but cannot safely establish the signed-offset ABI.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_IMAGE (4U * 1024U * 1024U)
#define BASE UINT64_C(0xb0000000)
#define NONE SIZE_MAX
static const uint8_t *code;
static size_t length;
static uint32_t le32(const uint8_t *p)
{ return p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24; }
static uint32_t word(size_t p) { return p <= length && length-p >= 4 ? le32(code+p) : 0; }
static int64_t sx(uint32_t n, unsigned bits)
{ return (int64_t)(n & ((UINT64_C(1)<<bits)-1)) - ((n & (1U<<(bits-1))) ? (INT64_C(1)<<bits) : 0); }
static size_t bl(size_t p)
{
    uint32_t w=word(p);
    int64_t target=(int64_t)p+sx(w&0x3ffffff,26)*4;
    return (w&0xfc000000)==0x94000000 && target>=0 && (uint64_t)target<length ? (size_t)target : NONE;
}
static uint64_t address(size_t p, unsigned reg)
{
    uint32_t a=word(p), b=word(p+4);
    unsigned source=a&31;
    if ((a&0x9f000000)!=0x90000000U ||
        (b&0xffc003ff)!=(0x91000000U|(source<<5)|reg)) return 0;
    uint32_t imm=((a>>5)&0x7ffff)<<2 | ((a>>29)&3);
    return ((BASE+p)&~UINT64_C(4095))+sx(imm,21)*4096+((b>>10)&4095);
}
static uint32_t ror(uint32_t x, unsigned n) { return (x>>n)|(x<<(32-n)); }
/* SHA256 is only a dynamic DHTB integrity check, never a firmware allowlist. */
static const uint32_t K[64]={
 0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
 0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
 0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
 0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
 0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
 0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
 0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
 0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static void sha256(const uint8_t *data, size_t size, uint8_t digest[32])
{
    uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    size_t blocks=(size+9+63)/64;
    for(size_t block=0;block<blocks;block++) {
        uint8_t chunk[64]={0}; uint32_t w[64];
        for(unsigned i=0;i<64;i++) {
            size_t off=block*64+i;
            if(off<size) chunk[i]=data[off];
            else if(off==size) chunk[i]=128;
            else if(off>=blocks*64-8) chunk[i]=(uint8_t)((uint64_t)size*8 >> ((blocks*64-1-off)*8));
        }
        for(unsigned i=0;i<16;i++) w[i]=(uint32_t)chunk[4*i]<<24|(uint32_t)chunk[4*i+1]<<16|(uint32_t)chunk[4*i+2]<<8|chunk[4*i+3];
        for(unsigned i=16;i<64;i++) w[i]=w[i-16]+(ror(w[i-15],7)^ror(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(ror(w[i-2],17)^ror(w[i-2],19)^(w[i-2]>>10));
        uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
        for(unsigned i=0;i<64;i++) {
            uint32_t t=z+(ror(e,6)^ror(e,11)^ror(e,25))+((e&f)^(~e&g))+K[i]+w[i];
            uint32_t u=(ror(a,2)^ror(a,13)^ror(a,22))+((a&b)^(a&c)^(b&c));
            z=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;
    }
    for(unsigned i=0;i<32;i++) digest[i]=(uint8_t)(h[i/4]>>(24-8*(i%4)));
}

/* Complete arithmetic routine: sign bit 7, 7-bit mV, ceil(mV*1000/3125),
 * 16-bit CPU/SRAM selectors and bounded table iteration. Only table bound
 * 15/16 varies; every branch and arithmetic instruction remains checked. */
static const uint32_t selector[]={
 0xb4000340,0xf9400002,0xb4000302,0xb9400803,0x51000460,0x7100381f,0x54000288,
 0x52807d00,0x12190024,0x12001821,0x1b007c21,0x528186a0,0x1130d021,0x1ac00821,
 0xaa0203e0,0x52800002,0x79400005,0x35000064,0x0b050025,0x14000002,0x4b0100a5,
 0x11000442,0x79000005,0x6b03005f,0x91000800,0x54fffee1,0xd65f03c0};
static int selector_at(size_t p)
{
    if(p>length || length-p<sizeof selector) return 0;
    for(unsigned i=0;i<sizeof selector/4;i++)
        if(word(p+4*i)!=selector[i] && !(i==5 && word(p+4*i)==0x71003c1f)) return 0;
    return 1;
}
static const uint32_t loop_decode[]={
 0xa9bb7bfd,0x910003fd,0xa90153f3,0,0xa9025bf5,0xf90023f9,0xa90363f7,0x2a0003f9,0,
 0x52800000,0x52807d16,0x11000414,0x531d7295,0x1ad52735,0x12001ab7,0x710192ff,
 0x54000168,0x12001eb8,0x2a1803e1,0,0xb9400262,0x340000c2,0x37380075,0x1b160b02,
 0x14000002,0x1b168ae2,0xb9000262,0x71000e9f,0x91007273,0x54000060,0x2a1403e0,
 0x17ffffec,0x52800000,0xa94153f3,0xa9425bf5,0xa94363f7,0xf94023f9,0xa8c57bfd,0xd65f03c0};
static const uint32_t unrolled_decode[]={
 0x53087c01,0x12001822,0x7101905f,0x540000a8,0,0,0x12001c21,0xb9001c41,
 0x53107c01,0x12001822,0x7101905f,0x540000a8,0,0,0x12001c21,0xb9004c41,
 0x53187c00,0x12001801,0x7101903f,0x54000088,0,0,0xb9007c20,0xd65f03c0};
static int state_address(uint64_t p) { return p>=BASE && p<BASE+MAX_IMAGE && !(p&3); }
static uint64_t decode_at(size_t p, int *old)
{
    if(p>length) return 0;
    if(length-p>=sizeof loop_decode) {
        unsigned i;
        for(i=0;i<sizeof loop_decode/4;i++)
            if(i!=3 && i!=8 && i!=19 && word(p+i*4)!=loop_decode[i]) break;
        if(i==sizeof loop_decode/4 && (word(p+12)&0x9f00001f)==0x90000013 &&
           (word(p+32)&0xffc003ff)==0x91000273) {
            size_t setter=bl(p+76);
            /* Setter: domain*0x30, flag at +0x1c, clear cached table state. */
            if(setter!=NONE && word(setter)==0x7100081f && word(setter+4)==0x2a0003e2 &&
               word(setter+8)==0x540000c9 && word(setter+32)==0x52800600 &&
               word(setter+36)==0x9ba07c42 && word(setter+48)==0x8b020000 &&
               word(setter+52)==0x91008000 && word(setter+56)==0xd2800102 &&
               word(setter+60)==0xb81fc001 && word(setter+64)==0xb900081f &&
               word(setter+68)==0x52800001 && (word(setter+72)&0xfc000000)==0x14000000) {
                uint64_t a=address(setter+40,0);
                if(state_address(a)) { *old=1; return a; }
            }
        }
    }
    if(length-p>=sizeof unrolled_decode) {
        unsigned i;
        for(i=0;i<sizeof unrolled_decode/4;i++)
            if(i!=4 && i!=5 && i!=12 && i!=13 && i!=20 && i!=21 && word(p+i*4)!=unrolled_decode[i]) break;
        if(i==sizeof unrolled_decode/4) {
            uint64_t a=address(p+16,2);
            if(state_address(a) && address(p+48,2)==a && address(p+80,1)==a) { *old=0; return a; }
        }
    }
    return 0;
}
static uint64_t init_decoder(size_t init, int *old, size_t *decoder)
{
    uint64_t state=decode_at(init,old);
    if(state) { *decoder=init; return state; }
    /* Newer initialization saves w0 in a callee-saved register, does the
     * stock hardware setup, then passes that unchanged flag to its decoder. */
    for(size_t p=init;p<length && p-init<4096;p+=4) {
        if(word(p)==0xd65f03c0) break;
        size_t target=bl(p);
        if(target==NONE || p<4 || (word(p-4)&0xffe0ffff)!=0x2a0003e0) continue;
        unsigned saved=(word(p-4)>>16)&31;
        if(saved<19 || saved>28) continue;
        int found=0;
        for(size_t q=init;q<p && q-init<80;q+=4)
            if(word(q)==(0x2a0003e0U|saved)) found=1;
        if(found && (state=decode_at(target,old))) { *decoder=target; return state; }
    }
    return 0;
}
static unsigned table_paths(size_t adjust, uint64_t state)
{
    unsigned flags=0, paired=0, calls=0;
    for(size_t p=8;p+4<=length;p+=4) {
        if(bl(p)!=adjust) continue;
        calls++;
        /* All adjustment calls consume a table object at +0x20 or +0x30. */
        int second=word(p-4)==0x9100c260;
        if(!second && word(p-8)!=0x91008260) return 0;
        uint32_t slot=0; int root=0;
        size_t start=p>1024?p-1024:0;
        for(size_t q=start;q<p;q+=4) {
            uint64_t candidate=address(q,20);
            if(candidate) root=candidate==state;
            if((word(q)&0xffc003ff)==0xb9400281) {
                unsigned off=((word(q)>>10)&4095)*4;
                slot=off;
            }
        }
        if(!root || (slot!=0x1c && slot!=0x4c && slot!=0x7c)) return 0;
        unsigned bit=1U<<((slot-0x1c)/0x30);
        flags|=bit;
        if(!second && bl(p+12)==adjust && word(p+8)==0x9100c260 &&
           word(p+4)==(0xb9400281U|((slot/4)<<10))) paired|=bit;
    }
    /* Older firmware has a CPU-only third table path; other two are paired.
     * Newer firmware pairs all three. Both retain the same offset ABI. */
    return calls>=5 && flags==7 && (paired&3)==3 ? paired : 0;
}
static const uint32_t dispatch[]={
 0xa9bd7bfd,0x2a0003e3,0x910003fd,0xf9000bf3,0xaa0103e0,0xaa0603f3,0xaa0203e1,
 0,0,0x529f4002,0x72afbfe2,0x0b020063,0x7100307f,0,0,0,0x38634845,0x10000064,0x8b258885,0xd61f00a0};
static size_t dispatch_init(size_t p)
{
    if(p>length || length-p<sizeof dispatch) return NONE;
    for(unsigned i=0;i<sizeof dispatch/4;i++)
        if(i!=7 && i!=8 && i!=13 && i!=14 && i!=15 && word(p+4*i)!=dispatch[i]) return NONE;
    if((word(p+28)&0xfff8001f)!=0x36000007 || (word(p+32)&0xfff8001f)!=0x37f00003 ||
       (word(p+52)&0xff00001f)!=0x54000008) return NONE;
    uint64_t table=address(p+56,2);
    if(table<BASE || table-BASE>length || length-(size_t)(table-BASE)<13) return NONE;
    int64_t target=(int64_t)p+80+(int8_t)code[table-BASE+11]*4;
    return target>=0 && (uint64_t)target<length ? bl((size_t)target) : NONE;
}
static int inspect(const uint8_t *image, size_t size)
{
    if(size<512 || memcmp(image,"DHTB\1\0\0\0",8)) return 0;
    size_t payload=le32(image+0x30), signed_size=le32(image+0x50);
    if(!payload || payload%4 || payload>size-512 || signed_size>size || signed_size<512+payload) return 0;
    uint8_t digest[32]; sha256(image+512,payload,digest);
    if(memcmp(digest,image+8,32)) return 0;
    code=image+512; length=payload;
    size_t adjust=NONE;
    for(size_t p=0;p+sizeof selector<=length;p+=4) if(selector_at(p)) {
        if(adjust!=NONE) return 0;  /* Do not guess between ambiguous routines. */
        adjust=p;
    }
    if(adjust==NONE) return 0;
    for(size_t p=0;p+sizeof dispatch<=length;p+=4) {
        size_t init=dispatch_init(p), decoder=NONE;
        if(init==NONE) continue;
        int old=0; uint64_t state=init_decoder(init,&old,&decoder);
        unsigned paired=state ? table_paths(adjust,state) : 0;
        if(paired && (old || paired==7)) {
            printf("{\"abi\":\"unisoc-dvfs-init-v1\",\"dispatch\":%zu,\"init\":%zu,\"decoder\":%zu,\"selector\":%zu,\"paired_domains\":%u,\"decoder_kind\":\"%s\"}\n",p,init,decoder,adjust,paired,old?"loop":"unrolled");
            return 1;
        }
    }
    return 0;
}
int main(int argc, char **argv)
{
    if(argc!=2) { fputs("usage: sml-voltage-check FILE|-\n",stderr); return 2; }
    FILE *f=strcmp(argv[1],"-") ? fopen(argv[1],"rb") : stdin;
    if(!f) { fputs("SML read failed\n",stderr); return 2; }
    uint8_t *data=malloc(MAX_IMAGE+1);
    if(!data) { if(f!=stdin) fclose(f); return 2; }
    size_t size=fread(data,1,MAX_IMAGE+1,f); int failed=ferror(f);
    if(f!=stdin) fclose(f);
    int supported=!failed && size<=MAX_IMAGE && inspect(data,size);
    free(data);
    if(!supported && !failed) fputs("SML voltage ABI could not be established\n",stderr);
    return failed ? 2 : supported ? 0 : 1;
}
