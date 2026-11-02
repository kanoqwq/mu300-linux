#!/usr/bin/env python3
"""Build the existing U30 Pro KMS panel UI as a static aarch64-musl binary."""
import argparse
import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'tools' / 'u30pro_panel.c'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cached_zig():
    """the Zig 0.15.2 unpacked in .cache/zig-0.15.2 for this host: zig-x86_64-windows-0.15.2/zig.exe or zig-x86_64-linux-0.15.2/zig"""
    found = sorted((REPO / '.cache' / 'zig-0.15.2').glob('*/zig.exe' if os.name == 'nt' else '*/zig'))
    if len(found) != 1 or subprocess.check_output([str(found[0]), 'version'], text=True).strip() != '0.15.2':
        raise SystemExit('expected exactly one cached Zig 0.15.2 for this host in .cache/zig-0.15.2')
    return found[0]


def verify_static_aarch64(data):
    if len(data) < 64 or data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1 or struct.unpack_from('<H', data, 18)[0] != 183:
        raise SystemExit('output is not a little-endian ELF64 AArch64 binary')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    phentsize = struct.unpack_from('<H', data, 54)[0]
    phnum = struct.unpack_from('<H', data, 56)[0]
    if phentsize < 56 or phoff + phentsize * phnum > len(data):
        raise SystemExit('output ELF program-header table is invalid')
    ph_types = [struct.unpack_from('<I', data, phoff + i * phentsize)[0] for i in range(phnum)]
    if 3 in ph_types:  # PT_INTERP
        raise SystemExit('output has PT_INTERP; refusing a dynamically linked panel binary')
    return {'elf_class': 64, 'machine': 'AArch64', 'program_headers': phnum, 'has_interpreter': False}


def native_panel_source():
    text = SOURCE.read_text(encoding='utf-8')
    patches = [
        (
            'static long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000+t.tv_nsec/1000000;}\n',
            'static long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000+t.tv_nsec/1000000;}\n'
            'static long home_threshold_ms(unsigned code){if(code!=KEY_HOME)return 1800;FILE *f=fopen("/run/u30pro-home-long-press-ms","r");long n=1800;'
            'if(f){if(fscanf(f,"%ld",&n)!=1)n=1800;fclose(f);}switch(n){case 1000:case 1500:case 1800:case 2000:'
            'case 2500:case 3000:case 4000:case 5000:return n;default:return 1800;}}\n'
            'static int home_short_is_page(void){FILE *f=fopen("/run/u30pro-home-short-action","r");char action[32]="page-next";'
            'if(f){if(fscanf(f,"%31s",action)!=1)strcpy(action,"page-next");fclose(f);}return !(!strcmp(action,"none")||'
            '!strcmp(action,"toggle-wifi")||!strcmp(action,"reconnect-cellular")||!strcmp(action,"refresh-status"));}\n'
            'static void refresh_brightness(void){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","r");'
            'int n;if(f){if(fscanf(f,"%d",&n)==1&&n>0&&n<=4095)brightness=n;fclose(f);}}\n'
        ),
        (
            'if(asleep){asleep=0;backlight(0);last_gesture="wake";}',
            'if(asleep){refresh_brightness();asleep=0;backlight(0);last_gesture="wake";}'
        ),
        (
            'if(duration>1800)continue;',
            'if(duration>=home_threshold_ms(e.code)){if(e.code==KEY_HOME)launch_home_action("long");continue;}'
        ),
        (
            'else{if(asleep){asleep=0;backlight(0);}page=(page+(e.code==KEY_VOLUMEDOWN?2:1))%3;}',
            'else{if(asleep){asleep=0;backlight(0);}if(e.code==KEY_HOME&&!home_short_is_page())launch_home_action("short");'
            'else page=(page+(e.code==KEY_VOLUMEDOWN?2:1))%3;}'
        ),
        (
            'if(e.code==KEY_POWER){asleep=!asleep;backlight(asleep);}',
            'if(e.code==KEY_POWER){refresh_brightness();asleep=!asleep;backlight(asleep);}'
        ),
        (
            '#include <linux/input.h>\n',
            '#include <linux/input.h>\n#include <linux/fb.h>\n'
        ),
        (
            '#include <unistd.h>\n',
            '#include <unistd.h>\n#include <sys/types.h>\n#include <sys/wait.h>\n'
        ),
        (
            'static int fd=-1;\n',
            'static int fd=-1,fbdev;\n'
        ),
        (
            'static int keyfd=-1,page,asleep;\n',
            'static int keyfd=-1,page,asleep;\nstatic pid_t home_action_pid=-1;\n'
        ),
        (
            'static void backlight(int off){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","w");if(f){fprintf(f,"%d\\n",off?0:brightness);fclose(f);}}',
            'static int backlight(int off){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","w");if(!f)return 0;int ok=fprintf(f,"%d\\n",off?0:brightness)>0;if(fclose(f))ok=0;return ok;}'
        ),
        (
            'static void keys_open(void){\n char name[256]={0};keyfd=open("/dev/input/event0",O_RDONLY|O_NONBLOCK|O_CLOEXEC);\n'
            ' if(keyfd>=0&&(ioctl(keyfd,EVIOCGNAME(sizeof name),name)<0||strcmp(name,"gpio-keys"))){close(keyfd);keyfd=-1;}\n'
            ' if(keyfd>=0){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","r");int n;if(f){if(fscanf(f,"%d",&n)==1&&n>0&&n<=4095)brightness=n;fclose(f);}printf("KEYS device=gpio-keys power=116 down=114 up=115 home=102\\n");}\n'
            ' else fprintf(stderr,"gpio-keys input unavailable; display continues\\n");\n}',
            'static void keys_open(void){\n'
            ' for(int i=0;i<32;i++){char path[64],name[256]={0};snprintf(path,sizeof path,"/dev/input/event%d",i);int k=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(k<0)continue;\n'
            '  if(ioctl(k,EVIOCGNAME(sizeof name),name)>=0&&!strcmp(name,"gpio-keys")){keyfd=k;refresh_brightness();printf("KEYS device=%s power=116 down=114 up=115 home=102\\n",path);fflush(stdout);break;}\n'
            '  close(k);\n'
            ' }\n'
            ' if(keyfd<0)fprintf(stderr,"gpio-keys input unavailable; display continues\\n");\n}'
        ),
        (
            'if(asleep){refresh_brightness();asleep=0;backlight(0);last_gesture="wake";}',
            'if(asleep){refresh_brightness();if(!backlight(0)){fprintf(stderr,"backlight unavailable; touch wake not applied\\n");return 0;}asleep=0;last_gesture="wake";}'
        ),
        (
            'if(e.code==KEY_POWER){refresh_brightness();asleep=!asleep;backlight(asleep);}',
            'if(e.code==KEY_POWER){refresh_brightness();int next=!asleep;if(backlight(next))asleep=next;else fprintf(stderr,"backlight unavailable; power key cannot blank panel\\n");}'
        ),
        (
            'static void setup(int allocate){\n',
            'static void setup_fbdev(int allocate){\n'
            ' fd=open("/dev/fb0",O_RDWR|O_CLOEXEC);if(fd<0)die("open DRM/fbdev");\n'
            ' struct fb_fix_screeninfo fix={0};struct fb_var_screeninfo var={0};\n'
            ' if(ioctl(fd,FBIOGET_FSCREENINFO,&fix)<0)die("fb fixed info");\n'
            ' if(ioctl(fd,FBIOGET_VSCREENINFO,&var)<0)die("fb variable info");\n'
            ' if(var.xres!=240||var.yres!=240||var.bits_per_pixel!=32||fix.visual!=FB_VISUAL_TRUECOLOR||fix.line_length<960||var.red.offset!=16||var.red.length<8||var.green.offset!=8||var.green.length<8||var.blue.offset!=0||var.blue.length<8){errno=ENODEV;die("unsupported 240x240 XRGB8888 fbdev");}\n'
            ' uint64_t offset=(uint64_t)var.yoffset*fix.line_length+(uint64_t)var.xoffset*4;uint64_t visible=(uint64_t)fix.line_length*var.yres;\n'
            ' if(!fix.smem_len||offset+visible>fix.smem_len){errno=EOVERFLOW;die("fbdev memory bounds");}\n'
            ' width=var.xres;height=var.yres;pitch[0]=pitch[1]=fix.line_length;fbdev=1;\n'
            ' printf("FBDEV mode=%ux%u bpp=%u pitch=%u offset=%llu\\n",width,height,var.bits_per_pixel,fix.line_length,(unsigned long long)offset);fflush(stdout);\n'
            ' if(!allocate)return;\n'
            ' void *map=mmap(NULL,fix.smem_len,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);if(map==MAP_FAILED)die("mmap fbdev");\n'
            ' pixels[0]=pixels[1]=(uint32_t*)((unsigned char*)map+offset);\n'
            '}\n'
            'static void setup(int allocate){\n'
        ),
        (
            ' fd=open("/dev/dri/card0",O_RDWR|O_CLOEXEC);if(fd<0)die("open DRM");',
            ' fd=open("/dev/dri/card0",O_RDWR|O_CLOEXEC);if(fd<0){setup_fbdev(allocate);return;}'
        ),
        (
            ' struct drm_mode_card_res r={0};xio(DRM_IOCTL_MODE_GETRESOURCES,&r,"resources count");',
            ' struct drm_mode_card_res r={0};if(ioctl(fd,DRM_IOCTL_MODE_GETRESOURCES,&r)<0){close(fd);fd=-1;setup_fbdev(allocate);return;}'
        ),
        (
            ' if(r.count_crtcs>16||r.count_connectors>16||r.count_encoders>16||r.count_fbs>64||!r.count_crtcs||!r.count_connectors){errno=ENODEV;die("resources bounds");}',
            ' if(r.count_crtcs>16||r.count_connectors>16||r.count_encoders>16||r.count_fbs>64||!r.count_crtcs||!r.count_connectors){close(fd);fd=-1;setup_fbdev(allocate);return;}'
        ),
        (
            ' if(!connector||!crtc){errno=ENODEV;die("240x240 connected mode / CRTC");}',
            ' if(!connector||!crtc){close(fd);fd=-1;setup_fbdev(allocate);return;}'
        ),
        (
            'static int keys_read(void){\n',
            'static void launch_home_action(const char *press){\n'
            ' if(home_action_pid>0)return;\n'
            ' pid_t p=fork();if(p==0){umask(077);int logfd=open("/run/u30pro-key-action.log",O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0600);if(logfd>=0){dup2(logfd,STDOUT_FILENO);dup2(logfd,STDERR_FILENO);close(logfd);}execl("/usr/bin/ucode","ucode","/usr/share/u30pro/native-control.uc","key-action",press,(char*)NULL);_exit(127);}\n'
            ' if(p<0)perror("KEY_HOME action fork");else home_action_pid=p;\n'
            '}\n'
            'static void reap_home_action(void){if(home_action_pid>0){int child_status;pid_t p=waitpid(home_action_pid,&child_status,WNOHANG);if(p==home_action_pid||(p<0&&errno!=EINTR))home_action_pid=-1;}}\n'
            'static int keys_read(void){\n'
        ),
        (
            'static void present(int b,int first){\n',
            'static void present(int b,int first){\n if(fbdev)return;\n'
        ),
        (
            ' while(running){\n  int changed=keys_read();',
            ' while(running){\n  reap_home_action();\n  int changed=keys_read();'
        ),
    ]
    for old, new in patches:
        if text.count(old) != 1:
            raise SystemExit('panel source changed; refusing to apply an unreviewed native patch')
        text = text.replace(old, new, 1)
    return text.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True,
                        help='new build directory; must not already exist')
    parser.add_argument('--zig', type=Path,
                        help='Zig 0.15.2 executable; defaults to the project cache')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if out.exists():
        raise SystemExit(f'output already exists; choose a new directory: {out}')
    if not SOURCE.is_file():
        raise SystemExit(f'panel source not found: {SOURCE}')
    zig = args.zig or cached_zig()
    if not zig.is_file():
        raise SystemExit(f'Zig executable not found: {zig}')
    out.mkdir(parents=True)
    binary = out / 'u30pro-panel'
    patched_source = native_panel_source()
    panel_source_path = out / 'panel-source.c'
    panel_source_path.write_bytes(patched_source)
    cmd = [str(zig), 'cc', '-target', 'aarch64-linux-musl',
           '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-static', '-s',
           str(panel_source_path), '-o', str(binary)]
    version = subprocess.check_output([str(zig), 'version'], text=True).strip()
    if version != '0.15.2':
        raise SystemExit(f'expected Zig 0.15.2, found {version}')
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    (out / 'compile.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(f'panel build failed; see {out / "compile.log"}')
    data = binary.read_bytes()
    elf = verify_static_aarch64(data)
    manifest = {
        'scope': 'static musl aarch64 panel UI; not a kernel/module or flash image',
        'source': str(SOURCE),
        'source_sha256': sha(SOURCE.read_bytes()),
        'patched_source_sha256': sha(patched_source),
        'native_patches': ['reload current backlight before power/touch wake',
                           'KEY_HOME ignore duration follows /run/u30pro-home-long-press-ms',
                           'configurable KEY_HOME short press with page-next default',
                           'long KEY_HOME dispatches asynchronously through native-control allowlist',
                           'validated 240x240 XRGB8888 fbdev fallback when DRM/KMS is absent',
                           'runtime screen on/off command from /run/u30pro-panel-screen-command'],
        'binary': binary.name,
        'binary_sha256': sha(data),
        'binary_bytes': len(data),
        'libc': 'musl',
        'target': 'aarch64-linux-musl',
        'static_verified': True,
        'elf': elf,
        'compiler': str(zig),
        'compiler_version': version,
        'headers': 'Zig 0.15.2 aarch64-linux-musl sysroot',
        'physical_display_verified': False,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(binary), 'sha256': manifest['binary_sha256'], 'bytes': len(data), 'libc': 'musl', 'static_verified': True}, indent=2))


if __name__ == '__main__':
    main()
