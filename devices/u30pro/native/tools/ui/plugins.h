/* Function-page plugins of the screen UI.
 *
 * Every file /usr/share/u30pro/ui-plugins/<name>.plugin (installed by a package) adds one entry to the function menu (the "功能" page).  Format: plain
 * key=value lines, '#' starts a comment:
 *
 *   title=Speed test          name under the icon (up to 7 CJK characters or 14 Latin letters are shown)
 *   icon=chart                built-in icon: wifi bolt gear sun moon mail chart globe power refresh info app; or
 *   glyph=测                  one character drawn in a circle (default: first character of the title)
 *   type=info                 info   opens a page that shows the output of cmd (up to 6 lines), refreshed every <refresh> seconds
 *                             action runs cmd when tapped; the first line of its output is shown as a message
 *                             toggle tile with an on/off state: state_cmd prints 1 or 0, cmd switches it
 *                             qr     shows a QR code of <text> (or of the first output line of cmd)
 *                             view   opens a page drawn from the output of cmd (below), refreshed every <refresh> seconds; cmd runs with
 *                                    U30PRO_PANEL_VIEW=1 in its environment, so that it can print plain lines for a screen program that
 *                                    does not know this type (it shows them as an info page)
 *   cmd=/usr/bin/my-app status   run by /bin/sh as root (stdout and stderr are captured)
 *   state_cmd=/usr/bin/my-app is-on
 *   text=http://192.168.25.1/my-app    (type=qr)
 *   refresh=5                 seconds between runs of cmd for type=info (2..3600)
 *   order=50                  position in the menu (lower first; the built-in entries are 0-9)
 *
 * The output of a view page: one element per line, drawn from the top down in this order of lines:
 *   gauge <value> <max> [colour]      an arc along the edge of the window, filled to value/max (not part of the flow)
 *   label <text>                      a small line
 *   big <number> [unit]               a large number (digits and . - : %) and a unit after it
 *   spark <colour> <max> <v> <v> ...  a line chart of up to 60 values from 0 to max; spark lines that follow each other share one chart
 *   row <name>|<value>                name on the left, value on the right
 *   text <text>                       a centred line
 * colour: a, b (the theme's two accents), ok, warn, bad, dim.
 *
 * The UI rescans the directory every 10 s, so an installed or removed package shows up without a restart.  See docs/SCREEN.md. */
#ifndef U30PRO_UI_PLUGINS_H
#define U30PRO_UI_PLUGINS_H
#include <dirent.h>
#include <sys/stat.h>

static void run_async(const char *cmd);

#ifndef UI_PLUGIN_DIR
#define UI_PLUGIN_DIR "/usr/share/u30pro/ui-plugins"
#endif
#define MAXPLUG 24
#define PLUG_LINES 6
#define PLUG_VIEW 1024

enum { PT_INFO, PT_ACTION, PT_TOGGLE, PT_QR, PT_VIEW };

typedef struct {
	char id[40], title[32], icon[16], glyph[8], cmd[256], state_cmd[256], text[160];
	int type, refresh, order, state;
	int nlines;
	char lines[PLUG_LINES][48];
	char view[PLUG_VIEW];
	char msg[96];
	long next_run, next_state, run_at;
	time_t out_mt, state_mt;
	int msg_pending;
} Plugin;

static Plugin plugs[MAXPLUG];
static int nplug;
static long plug_scan_next;
static time_t plug_dir_mtime;

static void plug_trim(char *s)
{
	size_t n = strlen(s);

	while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t')) s[--n] = 0;
}

static int plug_cmp(const void *a, const void *b)
{
	const Plugin *x = a, *y = b;

	if (x->order != y->order) return x->order < y->order ? -1 : 1;
	return strcmp(x->id, y->id);
}

static void plug_path(const Plugin *p, const char *ext, char *out, size_t cap)
{
	snprintf(out, cap, "%s/plug-%s.%s", UI_RUN, p->id, ext);
}

static int plug_load_file(const char *dir, const char *name, Plugin *p)
{
	char path[320], line[400];
	FILE *f;
	size_t n = strlen(name) - 7;	/* ".plugin" */

	memset(p, 0, sizeof *p);
	snprintf(path, sizeof path, "%s/%s", dir, name);
	f = fopen(path, "r");
	if (!f) return 0;
	for (size_t i = 0; i < n && i < sizeof p->id - 1; i++) {
		char ch = name[i];

		p->id[i] = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' ? ch : '_';
	}
	p->refresh = 5;
	p->order = 50;
	while (fgets(line, sizeof line, f)) {
		char *eq;

		plug_trim(line);
		if (!line[0] || line[0] == '#' || !(eq = strchr(line, '='))) continue;
		*eq++ = 0;
		if (!strcmp(line, "title")) snprintf(p->title, sizeof p->title, "%s", eq);
		else if (!strcmp(line, "icon")) snprintf(p->icon, sizeof p->icon, "%s", eq);
		else if (!strcmp(line, "glyph")) snprintf(p->glyph, sizeof p->glyph, "%s", eq);
		else if (!strcmp(line, "cmd")) snprintf(p->cmd, sizeof p->cmd, "%s", eq);
		else if (!strcmp(line, "state_cmd")) snprintf(p->state_cmd, sizeof p->state_cmd, "%s", eq);
		else if (!strcmp(line, "text")) snprintf(p->text, sizeof p->text, "%s", eq);
		else if (!strcmp(line, "type")) {
			if (!strcmp(eq, "action")) p->type = PT_ACTION;
			else if (!strcmp(eq, "toggle")) p->type = PT_TOGGLE;
			else if (!strcmp(eq, "qr")) p->type = PT_QR;
			else if (!strcmp(eq, "view")) p->type = PT_VIEW;
			else p->type = PT_INFO;
		} else if (!strcmp(line, "refresh")) { p->refresh = atoi(eq); if (p->refresh < 2) p->refresh = 2; if (p->refresh > 3600) p->refresh = 3600; }
		else if (!strcmp(line, "order")) p->order = atoi(eq);
	}
	fclose(f);
	if (!p->title[0] || !p->id[0]) return 0;
	if (p->type != PT_QR && !p->cmd[0]) return 0;
	if (p->type == PT_QR && !p->text[0] && !p->cmd[0]) return 0;
	if (p->type == PT_TOGGLE && !p->state_cmd[0]) return 0;
	return 1;
}

/* rescan the plugin directory; keeps the state of entries that are still there */
static void plug_scan(long now)
{
	struct stat st;
	DIR *d;
	struct dirent *e;
	Plugin fresh[MAXPLUG];
	int n = 0;

	if (now < plug_scan_next) return;
	plug_scan_next = now + 10000;
	if (stat(UI_PLUGIN_DIR, &st)) { nplug = 0; return; }
	if (st.st_mtime == plug_dir_mtime && nplug >= 0 && plug_dir_mtime) {
		/* unchanged directory: files inside may still have been edited, so only the mtime of the directory decides (packages add/remove files) */
		return;
	}
	plug_dir_mtime = st.st_mtime;
	d = opendir(UI_PLUGIN_DIR);
	if (!d) return;
	while ((e = readdir(d)) && n < MAXPLUG) {
		size_t l = strlen(e->d_name);

		if (l < 8 || strcmp(e->d_name + l - 7, ".plugin")) continue;
		if (plug_load_file(UI_PLUGIN_DIR, e->d_name, &fresh[n])) n++;
	}
	closedir(d);
	qsort(fresh, (size_t)n, sizeof fresh[0], plug_cmp);
	for (int i = 0; i < n; i++)
		for (int j = 0; j < nplug; j++)
			if (!strcmp(fresh[i].id, plugs[j].id)) {
				fresh[i].state = plugs[j].state;
				fresh[i].nlines = plugs[j].nlines;
				memcpy(fresh[i].lines, plugs[j].lines, sizeof fresh[i].lines);
				memcpy(fresh[i].view, plugs[j].view, sizeof fresh[i].view);
				memcpy(fresh[i].msg, plugs[j].msg, sizeof fresh[i].msg);
				fresh[i].out_mt = plugs[j].out_mt;
				fresh[i].state_mt = plugs[j].state_mt;
			}
	memcpy(plugs, fresh, sizeof fresh[0] * (size_t)n);
	nplug = n;
}

/* run a command of a plugin with its output captured in a file under /run/u30pro-ui (asynchronously on the device) */
static void plug_exec(const Plugin *p, const char *cmd, const char *ext)
{
	char out[160], full[420];

	plug_path(p, ext, out, sizeof out);
	snprintf(full, sizeof full, "(%s) >%s.tmp 2>&1; mv %s.tmp %s", cmd, out, out, out);
#ifdef UI_HOST
	if (system(full)) {}
#else
	run_async(full);
#endif
}

static int plug_read_lines(const char *path, char lines[PLUG_LINES][48], char *first, size_t firstcap)
{
	FILE *f = fopen(path, "r");
	char b[160];
	int n = 0;

	if (!f) return 0;
	while (fgets(b, sizeof b, f) && n < PLUG_LINES) {
		plug_trim(b);
		if (n == 0 && first) snprintf(first, firstcap, "%s", b);
		if (lines) snprintf(lines[n], 48, "%.47s", b);
		n++;
	}
	fclose(f);
	return n;
}

/* the entry whose page is open (info) or that is visible in the grid (toggle state): refresh what is due, pick up finished output */
static void plug_poll(long now, int open_idx, int grid_visible)
{
	char path[200];
	struct stat st;

	for (int i = 0; i < nplug; i++) {
		Plugin *p = &plugs[i];

		if ((p->type == PT_INFO || p->type == PT_VIEW) && i == open_idx && now >= p->next_run) {
			char vc[300];

			snprintf(vc, sizeof vc, "U30PRO_PANEL_VIEW=1 %s", p->cmd);
			plug_exec(p, p->type == PT_VIEW ? vc : p->cmd, "out");
			p->next_run = now + p->refresh * 1000L;
		}
		if (p->type == PT_TOGGLE && grid_visible && now >= p->next_state) {
			plug_exec(p, p->state_cmd, "st");
			p->next_state = now + 5000;
		}
		plug_path(p, "out", path, sizeof path);
		if (!stat(path, &st) && st.st_mtime != p->out_mt) {
			p->out_mt = st.st_mtime;
			if (p->type == PT_VIEW) {
				FILE *f = fopen(path, "r");
				size_t n = f ? fread(p->view, 1, sizeof p->view - 1, f) : 0;

				if (f) fclose(f);
				p->view[n] = 0;
				p->nlines = n > 0;
			} else if (p->type == PT_ACTION) {
				if (p->msg_pending) { p->msg_pending = 0; plug_read_lines(path, NULL, p->msg, sizeof p->msg); if (!p->msg[0]) snprintf(p->msg, sizeof p->msg, "已执行"); }
			} else {
				p->nlines = plug_read_lines(path, p->lines, p->type == PT_QR ? p->msg : NULL, sizeof p->msg);
			}
		}
		plug_path(p, "st", path, sizeof path);
		if (!stat(path, &st) && st.st_mtime != p->state_mt) {
			char first[16] = "";

			p->state_mt = st.st_mtime;
			plug_read_lines(path, NULL, first, sizeof first);
			p->state = first[0] == '1' || !strncmp(first, "on", 2);
		}
	}
}
#endif
