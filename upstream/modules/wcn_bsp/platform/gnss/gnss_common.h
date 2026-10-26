#ifndef __GNSS_COMMON_H__
#define __GNSS_COMMON_H__

/* start: address map on gnss side */
#define M3L_GNSS_CALI_ADDRESS 0x40aabf4c
#define M3L_GNSS_CALI_DATA_SIZE 0x1c
#define M3L_GNSS_EFUSE_ADDRESS 0x40aabf40
#define M3L_GNSS_BOOTSTATUS_ADDRESS  0x40aabf6c

#define GNSS_CALI_ADDRESS 0x40aaff4c
#define GNSS_CALI_DATA_SIZE 0x14
#define GNSS_EFUSE_ADDRESS 0x40aaff40
#define GNSS_BOOTSTATUS_ADDRESS  0x40aaff6c

#define GNSS_BOOTSTATUS_SIZE     0x4
#define GNSS_BOOTSTATUS_MAGIC    0x12345678
/* end: address map on gnss side */

int gnss_data_init(void);
int gnss_write_data(void);
int gnss_backup_data(void);
int gnss_boot_wait(void);
void gnss_file_path_set(char *buf);

#endif
