# MU300 Linux @TAG@: @SYSTEM@, kernel @KERNEL@

[English](#english) · [Türkçe](#türkçe) · [中文](#中文)

## English

This zip installs **@SYSTEM@ with kernel @KERNEL@** (release @TAG@) on a ZTE F50 / MU300, U30 Air or V50 that runs
a rooted Android (Magisk 26 or newer). It can be used in two ways:

**1. On the device, with Magisk (no computer).** Do not unpack the zip. Put it on the device and open it in the Magisk
app (Modules, Install from storage), or run `su -c 'magisk --install-module /sdcard/Download/<this zip>'`.

**2. From a computer.** Unpack the whole zip into a folder, connect the device by USB (USB debugging on, Magisk
letting "Shell" have root) and start:

| | the installer | in a window |
|---|---|---|
| Windows | `start.cmd` | `start-gui.cmd` |
| macOS, Linux | `sh start.sh` | `sh start-gui.sh` |

It installs exactly the system and the kernel of this zip, from the files in it (`payload/`), and downloads nothing:
both files are checked against `mu300/manifest` and the release's `mu300/SHA256SUMS` first. The rest is the
project's installer as always: it reads the device's own Wi-Fi and modem files, shows what it will write and waits for
`INSTALL`. `uninstall-linux.cmd` / `sh uninstall-linux.sh` remove Linux again.

What the computer needs:

* **adb**: Google's platform-tools are not in this zip (their licence does not allow passing them on). Download them
  from https://developer.android.com/tools/releases/platform-tools and unpack them into this folder, so that
  `platform-tools/adb` (Windows: `platform-tools\adb.exe`) is next to this file - or have adb on the PATH.
* **Python 3.8 or newer** and **lz4**. On Windows the installer installs both when they are missing (that needs the
  internet once); on macOS `brew install python lz4`, on Linux `python3` and `lz4` from the package manager. The
  window needs Python's tkinter too; the installer says how to get it.

More: https://github.com/dikeckaan/mu300-linux (README, wiki: Installation).

## Türkçe

Bu zip, rootlu Android (Magisk 26 veya daha yeni) çalışan bir ZTE F50 / MU300, U30 Air veya V50'ye **@SYSTEM@ ve
@KERNEL@ çekirdeğini** (sürüm @TAG@) kurar. İki şekilde kullanılır:

**1. Cihazda, Magisk ile (bilgisayarsız).** Zip'i açmayın. Cihaza koyup Magisk uygulamasında açın (Modüller,
Depolamadan yükle) ya da `su -c 'magisk --install-module /sdcard/Download/<bu zip>'` çalıştırın.

**2. Bilgisayardan.** Zip'in tamamını bir klasöre açın, cihazı USB ile bağlayın (USB hata ayıklama açık, Magisk
"Shell"e root veriyor) ve başlatın: Windows'ta `start.cmd` (pencereyle: `start-gui.cmd`), macOS ve Linux'ta
`sh start.sh` (pencereyle: `sh start-gui.sh`).

Yalnızca bu zip'teki sistemi ve çekirdeği, içindeki dosyalardan (`payload/`) kurar, hiçbir şey indirmez: iki dosya da
önce `mu300/manifest` ve sürümün `mu300/SHA256SUMS` dosyasıyla doğrulanır. Gerisi projenin her zamanki kurulum
programıdır: cihazın kendi Wi-Fi ve modem dosyalarını okur, ne yazacağını gösterir ve `INSTALL` bekler. Linux'u
kaldırmak için: `uninstall-linux.cmd` / `sh uninstall-linux.sh`.

Bilgisayarda gerekenler: **adb** (Google'ın platform-tools paketi bu zip'te yok, lisansı dağıtıma izin vermiyor:
https://developer.android.com/tools/releases/platform-tools adresinden indirip bu klasöre açın, `platform-tools/adb`
bu dosyanın yanında olsun, ya da adb PATH'te olsun), **Python 3.8+** ve **lz4** (Windows'ta eksikse kurulum programı
kurar, bir kez internet gerekir; macOS'ta `brew install python lz4`, Linux'ta paket yöneticisinden). Pencere için
Python'un tkinter'i de gerekir.

## 中文

本 zip 在运行已 root 的 Android（Magisk 26 或更新）的 ZTE F50 / MU300、U30 Air 或 V50 上安装 **@SYSTEM@，内核
@KERNEL@**（版本 @TAG@）。有两种用法：

**1. 在设备上用 Magisk 安装（无需电脑）。** 不要解压。把 zip 放到设备上，在 Magisk 应用中打开（模块 → 从本地安装），
或运行 `su -c 'magisk --install-module /sdcard/Download/<本 zip>'`。

**2. 在电脑上安装。** 把整个 zip 解压到一个文件夹，用 USB 连接设备（打开 USB 调试，Magisk 允许 Shell 获取 root），
然后运行：Windows 上 `start.cmd`（图形界面：`start-gui.cmd`），macOS 和 Linux 上 `sh start.sh`（图形界面：
`sh start-gui.sh`）。

它只安装本 zip 中的系统和内核，使用 zip 自带的文件（`payload/`），不下载任何东西：两个文件都会先按 `mu300/manifest`
和该版本的 `mu300/SHA256SUMS` 校验。其余与本项目的安装程序相同：读取设备自己的 Wi-Fi 和基带文件，显示将要写入的内容，
等待输入 `INSTALL`。卸载 Linux：`uninstall-linux.cmd` / `sh uninstall-linux.sh`。

电脑需要：**adb**（本 zip 不含 Google 的 platform-tools，其许可不允许再分发：请从
https://developer.android.com/tools/releases/platform-tools 下载并解压到本文件夹，使 `platform-tools/adb` 与本文件
同级，或把 adb 加入 PATH）、**Python 3.8+** 和 **lz4**（Windows 上缺少时安装程序会自动安装，需要联网一次；macOS：
`brew install python lz4`；Linux：用包管理器安装）。图形界面还需要 Python 的 tkinter。
