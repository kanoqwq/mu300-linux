/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef U30PRO_SGM41513_VALUES_H
#define U30PRO_SGM41513_VALUES_H
/* SGM41513 tables, not the TI 60 mA linear charge-current encoding. */
static inline unsigned int sgm41513_ichg_ua(unsigned int reg)
{
 unsigned int code = reg & 63, ma;
 if (code <= 8) ma = code * 5;
 else if (code <= 15) ma = 50 + (code - 9) * 10;
 else if (code <= 23) ma = 130 + (code - 16) * 20;
 else if (code <= 31) ma = 300 + (code - 24) * 30;
 else if (code <= 47) ma = 540 + (code - 32) * 60;
 else { ma = 1500 + (code - 48) * 120; if (ma > 3000) ma = 3000; }
 return ma * 1000;
}
static inline unsigned int sgm41513_cv_uv(unsigned int reg)
{
 unsigned int code = (reg >> 3) & 31;
 if (code == 15) return 4350000;
 if (code > 24) code = 24;
 return (3856 + code * 32) * 1000;
}
static inline unsigned int sgm41513_iin_ua(unsigned int reg)
{
 return (100 + (reg & 31) * 100) * 1000;
}
static inline unsigned int sgm41513_small_current_ua(unsigned int code)
{
 static const unsigned int ma[] = {5,10,15,20,30,40,50,60,80,100,120,140,160,180,200,240};
 return ma[code & 15] * 1000;
}
#endif
