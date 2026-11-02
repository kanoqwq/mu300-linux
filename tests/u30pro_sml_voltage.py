"""Mutate the device's read-only SML capture; never execute code or write hardware."""
import hashlib,json,os,struct,subprocess,tempfile,unittest
from pathlib import Path
TOP=Path(__file__).resolve().parents[1]
IMAGE=Path(os.environ.get('U30PRO_SML_FIXTURE',TOP/'work/u30pro-runtime/stock-sml-b.bin'))

@unittest.skipUnless(IMAGE.is_file(),'requires the device SML capture, not a similar model firmware')
class NativeABI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.checker=cls.root/'check';cls.original=IMAGE.read_bytes()
        subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(TOP/'tools/sml-voltage/sml-voltage-check.c'),'-o',str(cls.checker)],check=True)
        cls.report=json.loads(cls.run_image(cls.original).stdout)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    @classmethod
    def run_image(cls,image,domains=2):
        path=cls.root/'sml.bin';path.write_bytes(image)
        return subprocess.run([str(cls.checker),'--domains',str(domains),str(path)],capture_output=True,text=True)
    def fix_hash(self,data):
        size=struct.unpack_from('<I',data,0x30)[0]
        data[8:40]=hashlib.sha256(data[512:512+size]).digest()
        return data
    def mutated(self,p,word):
        data=bytearray(self.original);struct.pack_into('<I',data,512+p,word)
        return self.fix_hash(data)
    def test_actual_linked_abi_and_domain_count(self):
        self.assertEqual(self.report['abi'],'unisoc-dvfs-init-ums9632')
        self.assertEqual(self.report['domain_count'],2)
        self.assertNotEqual(self.run_image(self.original,3).returncode,0)
    def test_each_semantic_link_is_required_with_valid_checksum(self):
        r=self.report
        mutations=[(r['dispatch'],0x529f600a),(r['init'],0x52800000),
                   (r['decoder']+4,0x12191809),(r['decoder']+16,0x53093c08),
                   (r['decoder']+48,struct.unpack_from('<I',self.original,512+r['decoder']+48)[0]+1024)]
        for p in r['consumers']:
            mutations.extend([(p+16*4,0x54000243), # selector table bound
                              (p+19*4,0x721a011f), # sign bit
                              (p+22*4,0x72a9f14c), # ceil division constant
                              (p+29*4,0x5a8b0168), # sign direction
                              (p+33*4,0x7800452b)]) # selector element width
        for offset,word in mutations:
            with self.subTest(offset=offset):
                result=self.run_image(self.mutated(offset,word))
                self.assertNotEqual(result.returncode,0,result.stdout)
    def test_integrity_failure_cannot_enable_an_offset(self):
        data=bytearray(self.original);data[8]^=1
        self.assertNotEqual(self.run_image(data).returncode,0)
    def test_decoder_relocation_is_followed_without_fixed_offsets(self):
        data=bytearray(self.original);old=self.report['decoder']
        size=struct.unpack_from('<I',data,0x30)[0];new=size
        code=bytearray(data[512:512+size]);routine=bytearray(code[old:old+56])
        for relative in (20,44):
            pos=old+relative;w=struct.unpack_from('<I',routine,relative)[0]
            imm=((w>>5)&0x7ffff)<<2|((w>>29)&3)
            if imm&(1<<20):imm-=1<<21
            target=(pos&~4095)+imm*4096
            pages=(target-((new+relative)&~4095))//4096
            imm=pages&((1<<21)-1)
            w=(w&~0x60ffffe0)|((imm&3)<<29)|((imm>>2)<<5)
            struct.pack_into('<I',routine,relative,w)
        call=None
        for pos in range(self.report['init'],self.report['init']+1024,4):
            w=struct.unpack_from('<I',code,pos)[0]
            if w&0xfc000000==0x94000000:
                call=pos;break
        self.assertIsNotNone(call)
        struct.pack_into('<I',code,call,0x94000000|(((new-call)//4)&0x3ffffff))
        code[old:old+56]=bytes(56);code.extend(routine)
        data[512:512+len(code)]=code
        struct.pack_into('<I',data,0x30,len(code));struct.pack_into('<I',data,0x50,max(512+len(code),struct.unpack_from('<I',data,0x50)[0]))
        result=self.run_image(self.fix_hash(data))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['decoder'],new)

if __name__=='__main__':unittest.main()
