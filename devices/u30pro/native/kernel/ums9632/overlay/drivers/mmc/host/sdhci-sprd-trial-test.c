// SPDX-License-Identifier: GPL-2.0
/* Runs the production admission function; no emulated successful media writes. */
#include <kunit/test.h>

struct sprd_trial_fixture {
 struct sdhci_sprd_host sprd;
 struct mmc_host mmc;
 struct mmc_card card;
 struct mmc_command cmd;
 struct mmc_data data;
 struct mmc_request mrq;
};
static int sprd_trial_init(struct kunit *test)
{
 struct sprd_trial_fixture *f=kunit_kzalloc(test,sizeof(*f),GFP_KERNEL);
 if(!f)return -ENOMEM;
 test->priv=f;f->sprd.r11p3_trial=true;f->sprd.trial_card=&f->card;
 f->mmc.caps2=MMC_CAP2_NO_SDIO|MMC_CAP2_NO_SD;
 f->mrq.cmd=&f->cmd;
 return 0;
}
static bool denied(struct sprd_trial_fixture *f, u32 op, u32 arg)
{
 f->cmd.opcode=op;f->cmd.arg=arg;
 return sdhci_sprd_trial_forbidden(&f->sprd,&f->mmc,&f->mrq);
}
static void native_initialization_and_read(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 /* Sequence from core/mmc.c and mmc_ops.c, through standard non-CQE reads. */
 const u32 seq[]={0,1,2,3,9,7,8,13,16,23,18,12,17};
 for(int i=0;i<ARRAY_SIZE(seq);i++)
  KUNIT_EXPECT_FALSE_MSG(test,denied(f,seq[i],0),"core init/read CMD%u",seq[i]);
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_READ;f->data.blksz=512;f->data.blocks=8;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_READ_MULTIPLE_BLOCK,1234));
 f->data.flags=MMC_DATA_WRITE;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_READ_MULTIPLE_BLOCK,1234));
}
static void writes_erase_and_vendor_rejected(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 const u32 cmds[]={20,24,25,26,27,28,29,35,36,38,39,42,44,45,46,47,48,49,53,56,60,62,63};
 for(int i=0;i<ARRAY_SIZE(cmds);i++)
  KUNIT_EXPECT_TRUE_MSG(test,denied(f,cmds[i],0),"media/vendor CMD%u",cmds[i]);
 /* Real raw-ioctl data writes are rejected independently of the opcode. */
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_WRITE;f->data.blksz=512;f->data.blocks=1;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,0));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SEND_EXT_CSD,0));
}
static void bounded_bus_width_test(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_WRITE;f->data.blocks=1;f->data.blksz=8;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_BUS_TEST_W,0));
 f->data.blksz=4;KUNIT_EXPECT_FALSE(test,denied(f,MMC_BUS_TEST_W,0));
 f->data.blksz=512;KUNIT_EXPECT_TRUE(test,denied(f,MMC_BUS_TEST_W,0));
 f->data.blksz=8;f->data.blocks=2;KUNIT_EXPECT_TRUE(test,denied(f,MMC_BUS_TEST_W,0));
 f->mrq.data=NULL;KUNIT_EXPECT_TRUE(test,denied(f,MMC_BUS_TEST_W,0));
}
#define SW(index,value) ((3U<<24)|((u32)(index)<<16)|((u32)(value)<<8))
static void switch_preserves_boot_and_blocks_persistent_controls(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 f->card.ext_csd.part_config=0x4a; /* ACK + boot1 + current boot2 access. */
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0x48)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0x49)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0)));
 f->sprd.trial_card=NULL;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0x48)));
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_BUS_WIDTH,2)));
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_HS_TIMING,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_SANITIZE_START,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_BOOT_WP,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_RST_N_FUNCTION,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(30 /* vendor FFU MODE_CONFIG */,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PARTITION_SETTING_COMPLETED,1)));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_BUS_WIDTH,2)^(1U<<24)));
}
static void legacy_r11_and_sdio_unchanged(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 f->sprd.r11p3_trial=false;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_BLOCK,0));
 f->sprd.r11p3_trial=true;f->mmc.caps2=MMC_CAP2_NO_MMC|MMC_CAP2_NO_SD;
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_WRITE;f->data.blocks=1;f->data.blksz=512;
 KUNIT_EXPECT_FALSE(test,denied(f,53,0));
}
static void write_windows_admit_only_their_blocks(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_WRITE;f->data.blksz=512;f->data.blocks=8;
 u30pro_rw_lba=1000;u30pro_rw_sectors=64;u30pro_rw2_lba=5000;u30pro_rw2_sectors=1;
 f->card.state|=MMC_STATE_BLOCKADDR;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,1000));
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,1056));      /* ends exactly at the window end */
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,1057));       /* one sector past the end */
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,999));        /* starts before the window */
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,0));
 f->data.blocks=1;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_BLOCK,5000));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,5001));
 f->data.blksz=1024;KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,1000));  /* only 512-byte blocks */
 f->data.blksz=512;f->card.state&=~MMC_STATE_BLOCKADDR;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,1000));                /* byte-addressed cards: never */
 f->card.state|=MMC_STATE_BLOCKADDR;
 /* erase, sanitize and vendor commands stay forbidden even inside the window */
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_ERASE,1000));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_ERASE_GROUP_START,1000));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_ERASE_GROUP_END,1000));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_SANITIZE_START,1)));
 u30pro_rw_sectors=0;u30pro_rw2_sectors=0;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,1000));                /* no window, no write */
 /* the extra A/B-updater windows are parsed from one string and each admits only its own sectors */
 u30pro_rwx=(char *)"2000+16,3000+4,junk";u30pro_rwx_parse();
 KUNIT_EXPECT_EQ(test,u30pro_rwx_n,2U);
 f->data.blocks=8;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,2008));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,2009));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,3000));      /* crosses the end of the 4-sector window */
 f->data.blocks=4;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,3000));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,2500));       /* between the windows */
 u30pro_rwx=NULL;u30pro_rwx_parse();
 KUNIT_EXPECT_EQ(test,u30pro_rwx_n,0U);
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,2000));
}
static void write_protect_off_admits_everything(struct kunit *test)
{
 struct sprd_trial_fixture *f=test->priv;
 const u32 cmds[]={20,24,25,26,27,28,29,35,36,38,39,42,44,45,46,47,48,49,53,56,60,62,63};
 f->card.ext_csd.part_config=0x48;f->card.state|=MMC_STATE_BLOCKADDR;
 u30pro_rw_sectors=0;u30pro_rw2_sectors=0;u30pro_rwx=NULL;u30pro_rwx_parse();
 write_protect=false;
 for(int i=0;i<ARRAY_SIZE(cmds);i++)
  KUNIT_EXPECT_FALSE_MSG(test,denied(f,cmds[i],0),"protection off: CMD%u",cmds[i]);
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0x4b)));  /* RPMB access */
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_BOOT_WP,1)));
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_SANITIZE_START,1)));
 f->mrq.data=&f->data;f->data.flags=MMC_DATA_WRITE;f->data.blksz=512;f->data.blocks=8;
 KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,0));
 f->data.blksz=1024;KUNIT_EXPECT_FALSE(test,denied(f,MMC_WRITE_BLOCK,12345));
 /* back on: the same requests are refused again */
 write_protect=true;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_BLOCK,12345));
 f->data.blksz=512;KUNIT_EXPECT_TRUE(test,denied(f,MMC_WRITE_MULTIPLE_BLOCK,0));
 f->mrq.data=NULL;
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_ERASE,0));
 KUNIT_EXPECT_TRUE(test,denied(f,MMC_SWITCH,SW(EXT_CSD_PART_CONFIG,0x4b)));
}
static struct kunit_case sprd_trial_cases[]={
 KUNIT_CASE(write_windows_admit_only_their_blocks),
 KUNIT_CASE(write_protect_off_admits_everything),
 KUNIT_CASE(native_initialization_and_read),
 KUNIT_CASE(writes_erase_and_vendor_rejected),
 KUNIT_CASE(bounded_bus_width_test),
 KUNIT_CASE(switch_preserves_boot_and_blocks_persistent_controls),
 KUNIT_CASE(legacy_r11_and_sdio_unchanged),{}
};
static struct kunit_suite sprd_trial_suite={.name="sdhci-sprd-readonly-trial",.init=sprd_trial_init,.test_cases=sprd_trial_cases};
kunit_test_suite(sprd_trial_suite);
