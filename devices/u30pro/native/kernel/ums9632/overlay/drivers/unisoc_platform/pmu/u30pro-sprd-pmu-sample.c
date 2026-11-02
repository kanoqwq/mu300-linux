// SPDX-License-Identifier: GPL-2.0-only
/*
 * PMU event sampling ("sprd,pmu-sampler"): the data source of scufreq.
 *
 * Every sample_ms it hands the performance counter deltas of every CPU (instructions, cycles, back-end stall cycles, L2 refills) and
 * the measured CPU frequency (cycles per microsecond) to the registered clients through a notifier chain.
 *
 * Based on the vendor drivers pmu.c and pmu_sample.c (GPL-2.0, Unisoc).  Differences:
 *  - The vendor driver is hooked into the Android scheduler tick, the jiffies update and the cpuidle enter/exit vendor hooks, which the
 *    mainline kernel does not have.  Here every CPU has a deferrable timer: it only runs while the CPU is awake, reads the counters of
 *    that CPU (perf_event_read_local) and, when sample_ms have passed, kicks the aggregation work.  An idle CPU therefore costs nothing
 *    and delivers no samples (a CPU that was idle for a while is re-based so the first sample does not average the idle time in).
 *  - The counters are plain perf kernel events (pinned, raw event numbers).  The AMU counters of the vendor driver are not used.
 *  - The counters and the timers only exist while a client has called sprd_pmu_sample_enable(true), so nothing is spent (and the four
 *    counters of every CPU stay free for perf) when scufreq is off.
 *  - The event numbers are fixed (the ones of the stock device tree table: 0x08 instructions, 0x11 cycles, 0x17 L2 refills, 0x24
 *    back-end stalls) and the AMU column of "sprd,pmu-events-tbl" is ignored.
 */
#define pr_fmt(fmt) "sprd-pmu-sample: " fmt

#include <linux/cpu.h>
#include <linux/cpuhotplug.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/notifier.h>
#include <linux/of.h>
#include <linux/percpu.h>
#include <linux/perf_event.h>
#include <linux/platform_device.h>
#include <linux/soc/sprd/pmu_sample.h>
#include <linux/spinlock.h>
#include <linux/sysfs.h>
#include <linux/timer.h>
#include <linux/workqueue.h>

#define INST_EV		0x08
#define CYC_EV		0x11
#define MISS_EV		0x17
#define STAL_EV		0x24

#define MIN_SAMPLE_MS	4U
#define MAX_SAMPLE_MS	1000U
/* a CPU that did not sample for this many sample periods was idle: its next sample starts a new baseline */
#define IDLE_GAP_PERIODS	4
#define HALF_TICK_NS	((NSEC_PER_SEC / HZ) >> 1)

struct cpu_samp {
	struct perf_event	*pev[NUM_EVS];
	struct timer_list	timer;
	spinlock_t		lock;		/* prev/curr and their timestamps */
	bool			active;
	bool			have_base;
	struct cpu_ctrs		prev, curr;
	ktime_t			prev_ts, curr_ts;
};

static DEFINE_PER_CPU(struct cpu_samp, cpu_samp);
static u32 ev_ids[NUM_EVS] = {
	[INST_IDX] = INST_EV,
	[CYC_IDX] = CYC_EV,
	[BE_STALL_IDX] = STAL_EV,
	[MISS_IDX] = MISS_EV,
};
static struct sprd_pmu_sample_data sample_data;
static BLOCKING_NOTIFIER_HEAD(sample_notify_chain);
static DEFINE_MUTEX(enable_lock);
static bool enabled;
static int cpuhp_state;
static u32 sample_ms = 16;
static atomic64_t last_agg_ns;
static struct work_struct agg_work;

int sprd_pmu_sample_notify_register(struct notifier_block *nb)
{
	return blocking_notifier_chain_register(&sample_notify_chain, nb);
}
EXPORT_SYMBOL(sprd_pmu_sample_notify_register);

int sprd_pmu_sample_notify_unregister(struct notifier_block *nb)
{
	return blocking_notifier_chain_unregister(&sample_notify_chain, nb);
}
EXPORT_SYMBOL(sprd_pmu_sample_notify_unregister);

/* runs on the CPU that owns cs (timer softirq) */
static void sample_read_local(struct cpu_samp *cs)
{
	struct cpu_ctrs now;
	unsigned long flags;
	ktime_t ts;
	u64 val;
	int i;

	spin_lock_irqsave(&cs->lock, flags);
	now = cs->curr;
	spin_unlock_irqrestore(&cs->lock, flags);

	/* an event that cannot be read keeps its last value so that no bogus delta appears */
	for (i = 0; i < NUM_EVS; i++) {
		if (cs->pev[i] && !perf_event_read_local(cs->pev[i], &val, NULL, NULL))
			now.ctrs[i] = val;
	}

	ts = ktime_get();
	spin_lock_irqsave(&cs->lock, flags);
	if (!cs->have_base || ktime_to_ms(ktime_sub(ts, cs->curr_ts)) > (s64)IDLE_GAP_PERIODS * sample_ms) {
		cs->prev = now;
		cs->prev_ts = ts;
		cs->have_base = true;
	}
	cs->curr = now;
	cs->curr_ts = ts;
	spin_unlock_irqrestore(&cs->lock, flags);
}

static void sample_timer_fn(struct timer_list *t)
{
	struct cpu_samp *cs = from_timer(cs, t, timer);
	s64 now_ns, last;

	if (!READ_ONCE(cs->active))
		return;

	sample_read_local(cs);
	mod_timer(&cs->timer, jiffies + msecs_to_jiffies(sample_ms));

	/* the first CPU that finds the period over starts the aggregation (the vendor driver does this from the jiffies update) */
	now_ns = ktime_get_ns();
	last = atomic64_read(&last_agg_ns);
	if (now_ns - last >= (s64)sample_ms * NSEC_PER_MSEC - HALF_TICK_NS &&
	    atomic64_cmpxchg(&last_agg_ns, last, now_ns) == last)
		queue_work(system_freezable_wq, &agg_work);
}

static void agg_work_fn(struct work_struct *work)
{
	struct cpu_samp *cs;
	unsigned long flags;
	int cpu, i;

	for_each_possible_cpu(cpu) {
		struct cpu_ctrs delta = {};
		u32 freq = 0;
		s64 dt_us;

		if (cpu >= MAX_CPU_NUM)
			break;

		cs = per_cpu_ptr(&cpu_samp, cpu);
		spin_lock_irqsave(&cs->lock, flags);
		dt_us = ktime_us_delta(cs->curr_ts, cs->prev_ts);
		if (dt_us > 0) {
			for (i = 0; i < NUM_EVS; i++) {
				if (ev_ids[i] && cs->curr.ctrs[i] >= cs->prev.ctrs[i])
					delta.ctrs[i] = cs->curr.ctrs[i] - cs->prev.ctrs[i];
			}
			freq = div64_u64(delta.ctrs[CYC_IDX], dt_us);
			cs->prev = cs->curr;
			cs->prev_ts = cs->curr_ts;
		}
		spin_unlock_irqrestore(&cs->lock, flags);

		sample_data.delta[cpu] = delta;
		sample_data.freq_mhz[cpu] = freq;
	}
	sample_data.ctrs_released = false;

	blocking_notifier_call_chain(&sample_notify_chain, 0, &sample_data);
}

static int pmu_sample_cpu_up(unsigned int cpu)
{
	struct cpu_samp *cs = per_cpu_ptr(&cpu_samp, cpu);
	struct perf_event_attr attr = {
		.type = PERF_TYPE_RAW,
		.size = sizeof(attr),
		.pinned = 1,
	};
	int i;

	for (i = 0; i < NUM_EVS; i++) {
		struct perf_event *pev;

		if (!ev_ids[i])
			continue;
		attr.config = ev_ids[i];
		pev = perf_event_create_kernel_counter(&attr, cpu, NULL, NULL, NULL);
		if (IS_ERR(pev)) {
			pr_warn("event 0x%x is not available on cpu%u: %ld\n", ev_ids[i], cpu, PTR_ERR(pev));
			continue;
		}
		perf_event_enable(pev);
		cs->pev[i] = pev;
	}

	spin_lock_irq(&cs->lock);
	cs->have_base = false;
	cs->prev_ts = cs->curr_ts = 0;
	memset(&cs->prev, 0, sizeof(cs->prev));
	memset(&cs->curr, 0, sizeof(cs->curr));
	spin_unlock_irq(&cs->lock);

	WRITE_ONCE(cs->active, true);
	mod_timer(&cs->timer, jiffies + msecs_to_jiffies(sample_ms));

	return 0;
}

static int pmu_sample_cpu_down(unsigned int cpu)
{
	struct cpu_samp *cs = per_cpu_ptr(&cpu_samp, cpu);
	int i;

	WRITE_ONCE(cs->active, false);
	timer_delete_sync(&cs->timer);

	for (i = 0; i < NUM_EVS; i++) {
		if (cs->pev[i]) {
			perf_event_release_kernel(cs->pev[i]);
			cs->pev[i] = NULL;
		}
	}

	/* an offline CPU delivers zero samples */
	spin_lock_irq(&cs->lock);
	cs->have_base = false;
	cs->prev_ts = cs->curr_ts = 0;
	spin_unlock_irq(&cs->lock);

	return 0;
}

int sprd_pmu_sample_enable(bool on)
{
	int ret = 0;

	mutex_lock(&enable_lock);
	if (on == enabled)
		goto out;

	if (on) {
		atomic64_set(&last_agg_ns, ktime_get_ns());
		ret = cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "soc/sprd-pmu-sample:online", pmu_sample_cpu_up, pmu_sample_cpu_down);
		if (ret < 0)
			goto out;
		cpuhp_state = ret;
		ret = 0;
		enabled = true;
		pr_info("sampling every %u ms\n", sample_ms);
	} else {
		cpuhp_remove_state(cpuhp_state);
		enabled = false;
		cancel_work_sync(&agg_work);
		pr_info("sampling stopped, the counters are released\n");
	}
out:
	mutex_unlock(&enable_lock);

	return ret;
}
EXPORT_SYMBOL(sprd_pmu_sample_enable);

static ssize_t sample_ms_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%u\n", sample_ms);
}

static ssize_t sample_ms_store(struct device *dev, struct device_attribute *attr, const char *buf, size_t count)
{
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 10, &val);
	if (ret)
		return ret;

	WRITE_ONCE(sample_ms, clamp(val, MIN_SAMPLE_MS, MAX_SAMPLE_MS));

	return count;
}
static DEVICE_ATTR_RW(sample_ms);

static struct attribute *pmu_sample_attrs[] = {
	&dev_attr_sample_ms.attr,
	NULL,
};

static const struct attribute_group pmu_sample_group = {
	.attrs = pmu_sample_attrs,
};

static int pmu_sample_probe(struct platform_device *pdev)
{
	int cpu;

	for_each_possible_cpu(cpu) {
		struct cpu_samp *cs = per_cpu_ptr(&cpu_samp, cpu);

		spin_lock_init(&cs->lock);
		timer_setup(&cs->timer, sample_timer_fn, TIMER_PINNED | TIMER_DEFERRABLE);
	}
	INIT_WORK(&agg_work, agg_work_fn);
	memcpy(sample_data.ev_ids, ev_ids, sizeof(ev_ids));

	return devm_device_add_group(&pdev->dev, &pmu_sample_group);
}

static const struct of_device_id pmu_sample_of_match[] = {
	{ .compatible = "sprd,pmu-sampler" },
	{ /* sentinel */ },
};

static struct platform_driver pmu_sample_driver = {
	.probe = pmu_sample_probe,
	.driver = {
		.name = "sprd-pmu-sampler",
		.of_match_table = pmu_sample_of_match,
		.suppress_bind_attrs = true,
	},
};
builtin_platform_driver(pmu_sample_driver);

MODULE_DESCRIPTION("Unisoc PMU event sampling");
MODULE_LICENSE("GPL");
