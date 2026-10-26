/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * mu300_thermal's trend for step_wise (docs/FINDINGS.md 166): plain C with no kernel header, so that
 * tests/test_mu300_thermal.py compiles and runs it on the host. Temperatures in mC, times in ticks.
 *
 * Without a get_trend op the thermal core compares each reading with the one before it, 100 ms earlier while
 * passive. The hottest of eleven on-die sensors moves up and down by a degree or more between two such readings
 * whatever the load does; step_wise lowers the cooling state one step on every "dropping" reading above the trip, and
 * lifts all of it on one "dropping" reading under it. Under the toolkit's CPU stress the three cpufreq cooling devices
 * sat at 0-3 of their 9-10 steps and the SoC went from 38 to 98 C in three minutes (6.18.56, F50-C).
 *
 * Here the trend is the slope between the mean of the readings of the last half second and the mean of those of a
 * half second about a second earlier, with a dead band. The cooling goes up fast and comes back slowly:
 *   at or above trip + MU300_HOT_MARGIN: raising (step_wise adds a step every poll) unless the SoC is clearly
 *     falling already (stable: the state is kept);
 *   from the trip less its hysteresis (where step_wise throttles) up to there: raising when the slope rises above the
 *     trip; one step back, at most one per drop_every, when the slope falls or the SoC is under the trip; else stable;
 *   from MU300_RELEASE under the trip up to the hysteresis: stable. step_wise would lift every step at once on a
 *     dropping trend here, and the die jumps 10-20 C within a second of that under a full load;
 *   below: dropping (the cooling of this trip is lifted).
 */
#ifndef MU300_TREND_H
#define MU300_TREND_H

#define MU300_HISTORY		32	/* readings kept: >1.5 s at 100 ms passive polling, plus sysfs reads */
#define MU300_DEADBAND		1000	/* mC per second: a slope inside +-this is no trend */
#define MU300_HOT_MARGIN	3000	/* mC above the trip: from here the cooling only goes up */
#define MU300_RELEASE		5000	/* mC under the trip: from here down the cooling is lifted */

enum mu300_trend { MU300_STABLE, MU300_RAISING, MU300_DROPPING };

struct mu300_sample {
	unsigned long when;	/* ticks */
	int temp;		/* mC */
};

struct mu300_history {
	struct mu300_sample s[MU300_HISTORY];
	unsigned int head, filled;
	unsigned long last_drop;	/* when a step back was last given */
	int dropped;			/* last_drop is set */
};

static inline void mu300_record(struct mu300_history *h, unsigned long when, int temp)
{
	h->s[h->head].when = when;
	h->s[h->head].temp = temp;
	h->head = (h->head + 1) % MU300_HISTORY;
	if (h->filled < MU300_HISTORY)
		h->filled++;
}

/* the I-th newest reading, 1 the newest */
static inline const struct mu300_sample *mu300_nth(const struct mu300_history *h, unsigned int i)
{
	return &h->s[(h->head + MU300_HISTORY - i) % MU300_HISTORY];
}

/*
 * The slope in mC per second: the mean of the readings less than SPAN ticks older than the newest, against the mean
 * of those from the newest one at least WINDOW older (else the oldest kept, when that is at least SPAN older) to SPAN
 * before it. *now is the newest reading. 0, or -1 without enough history.
 */
static inline int mu300_slope(const struct mu300_history *h, unsigned long hz, unsigned long window,
			      unsigned long span, int *now, long *slope)
{
	const struct mu300_sample *last, *s;
	unsigned int i, p, n_new = 0, n_old = 0;
	long sum_new = 0, sum_old = 0, age;

	if (h->filled < 2)
		return -1;
	last = mu300_nth(h, 1);
	for (i = 1; i <= h->filled && (long)(last->when - mu300_nth(h, i)->when) < (long)span; i++) {
		sum_new += mu300_nth(h, i)->temp;
		n_new++;
	}
	for (p = i; p < h->filled && (long)(last->when - mu300_nth(h, p)->when) < (long)window; p++)
		;
	if (p > h->filled)
		return -1;
	age = (long)(last->when - mu300_nth(h, p)->when);
	if (age < (long)span)
		return -1;
	for (i = p; i <= h->filled; i++) {
		s = mu300_nth(h, i);
		if ((long)(mu300_nth(h, p)->when - s->when) >= (long)span)
			break;
		sum_old += s->temp;
		n_old++;
	}
	*now = last->temp;
	*slope = (sum_new / (long)n_new - sum_old / (long)n_old) * (long)hz / age;
	return 0;
}

/* the trend for a trip at TRIP mC with HYST mC of hysteresis at time WHEN; DROP_EVERY in ticks */
static inline enum mu300_trend mu300_trend(struct mu300_history *h, int now, long slope, int trip, int hyst,
					   unsigned long when, unsigned long drop_every)
{
	if (now >= trip + MU300_HOT_MARGIN)
		return slope < -MU300_DEADBAND ? MU300_STABLE : MU300_RAISING;
	if (now >= trip - hyst) {
		if (slope > MU300_DEADBAND && now >= trip)
			return MU300_RAISING;
		if ((slope < -MU300_DEADBAND || now < trip) &&
		    (!h->dropped || (long)(when - h->last_drop) >= (long)drop_every)) {
			h->last_drop = when;
			h->dropped = 1;
			return MU300_DROPPING;
		}
		return MU300_STABLE;
	}
	return now < trip - MU300_RELEASE ? MU300_DROPPING : MU300_STABLE;
}

#endif
