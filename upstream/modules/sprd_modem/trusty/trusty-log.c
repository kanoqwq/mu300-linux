/*
 * Copyright (C) 2015 Google, Inc.
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 */
#include <linux/platform_device.h>
#include <linux/mod_devicetable.h>
#include <linux/notifier.h>
#include <linux/slab.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/log2.h>
#include <asm/page.h>
#include <linux/trusty/smcall.h>
#include <linux/vmalloc.h>
#include "trusty-log.h"
#include "trusty.h"

#define SMC_SC_SYSCTL_SET_CONSOLE	SMC_STDCALL_NR(SMC_ENTITY_SYSCTL, 0)
#define SMC_SC_SYSCTL_GET_CONSOLE	SMC_STDCALL_NR(SMC_ENTITY_SYSCTL, 1)
#define SMC_SC_SYSCTL_SET_LOGLEVEL	SMC_STDCALL_NR(SMC_ENTITY_SYSCTL, 2)
#define SMC_SC_SYSCTL_GET_LOGLEVEL	SMC_STDCALL_NR(SMC_ENTITY_SYSCTL, 3)

#define TRUSTY_LOG_DEFAULT_SIZE (PAGE_SIZE * 4)
#define TRUSTY_LOG_MAX_SIZE (PAGE_SIZE * 32)
#define TRUSTY_LINE_BUFFER_SIZE 256

struct trusty_log_state {
	struct device *dev;
	struct device *trusty_dev;

	/*
	 * This lock is here to ensure only one consumer will read
	 * from the log ring buffer at a time.
	 */
	spinlock_t lock;
	struct log_rb *log;
	uint32_t get;

	uint64_t *pfn_list;
	struct page **pages;
	int num_pages;

	int dumpable;

	struct notifier_block call_notifier;
	struct notifier_block panic_notifier;
	char line_buffer[TRUSTY_LINE_BUFFER_SIZE];
};

static int log_read_line(struct trusty_log_state *s, int put, int get)
{
	struct log_rb *log = s->log;
	int i;
	char c = '\0';
	size_t max_to_read = min((size_t)(put - get),
				 sizeof(s->line_buffer) - 1);
	size_t mask = log->sz - 1;

	for (i = 0; i < max_to_read && c != '\n';)
		s->line_buffer[i++] = c = log->data[get++ & mask];
	s->line_buffer[i] = '\0';

	return i;
}

static void trusty_dump_logs(struct trusty_log_state *s, unsigned long action)
{
	struct log_rb *log = s->log;
	uint32_t get, put, alloc;
	int read_chars;

	if (!s->dumpable) {
		pr_err_once("Trusty log currently is not dumpable\n");
		return;
	}

	WARN_ON(!is_power_of_2(log->sz));

	/*
	 * For this ring buffer, at any given point, alloc >= put >= get.
	 * The producer side of the buffer is not locked, so the put and alloc
	 * pointers must be read in a defined order (put before alloc) so
	 * that the above condition is maintained. A read barrier is needed
	 * to make sure the hardware and compiler keep the reads ordered.
	 */
	get = s->get;
	while ((put = log->put) != get) {
		/*
		 * Make sure that the read of put occurs before the read
		 * of log data
		 */
		rmb();

		/* Read a line from the log */
		read_chars = log_read_line(s, put, get);

		/* Force the loads from log_read_line to complete. */
		rmb();
		alloc = log->alloc;

		/*
		 * Discard the line that was just read if the data could
		 * have been corrupted by the producer.
		 */
		if (alloc - get > log->sz) {
			pr_err("trusty: log overflow(sz %d)\n", log->sz);
			get = alloc - log->sz;
			continue;
		}
		if (action == TRUSTY_CALL_PANIC)
			pr_emerg("trusty: %s", s->line_buffer);
		else
			pr_info("trusty: %s", s->line_buffer);
		get += read_chars;
	}
	s->get = get;
}

static int trusty_log_call_notify(struct notifier_block *nb,
				  unsigned long action, void *data)
{
	struct trusty_log_state *s;
	unsigned long flags;

	if (action == TRUSTY_CALL_PREPARE)
		return NOTIFY_DONE;

	s = container_of(nb, struct trusty_log_state, call_notifier);
	spin_lock_irqsave(&s->lock, flags);
	trusty_dump_logs(s, action);
	spin_unlock_irqrestore(&s->lock, flags);
	return NOTIFY_OK;
}

static int trusty_log_panic_notify(struct notifier_block *nb,
				   unsigned long action, void *data)
{
	struct trusty_log_state *s;
	/*
	 * Don't grab the spin lock to hold up the panic notifier, even
	 * though this is racy.
	 */
	s = container_of(nb, struct trusty_log_state, panic_notifier);
	pr_info("trusty-log panic notifier - trusty version %s",
		trusty_version_str_get(s->trusty_dev));
	trusty_dump_logs(s, TRUSTY_CALL_PANIC);
	return NOTIFY_OK;
}

static bool trusty_supports_logging(struct device *device)
{
	int result;

	result = trusty_std_call32(device, SMC_SC_SHARED_LOG_VERSION,
				   TRUSTY_LOG_API_VERSION, 0, 0);
	if (result == SM_ERR_UNDEFINED_SMC) {
		pr_info("trusty-log not supported on secure side.\n");
		return false;
	} else if (result < 0) {
		pr_err("trusty std call (SMC_SC_SHARED_LOG_VERSION) failed: %d\n",
		       result);
		return false;
	}

	if (result == TRUSTY_LOG_API_VERSION)
		return true;

	pr_info("trusty-log unsupported api version: %d, supported: %d\n",
			result, TRUSTY_LOG_API_VERSION);
	return false;
}

static ssize_t trusty_loglevel_store(struct device *dev,
	struct device_attribute *attr, const char *buf, size_t count)
{
	struct trusty_log_state *log_state = NULL;
	unsigned long loglevel;
	int result;


	result = kstrtoul(buf, 0, &loglevel);
	if (result) {
		pr_info("%s is not in hex or decimal form.\n", buf);
		return -EINVAL;
	}

	log_state = platform_get_drvdata(to_platform_device(dev));
	result = trusty_std_call32(log_state->trusty_dev,
	SMC_SC_SYSCTL_SET_LOGLEVEL, loglevel, 0, 0);
	if (result == SM_ERR_UNDEFINED_SMC) {
		pr_info("SMC_SC_SYSCTL_SET_CONSOLE not supported on secure side.\n");
		return -EFAULT;
	}

	return strnlen(buf, count);
}


static ssize_t trusty_loglevel_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct trusty_log_state *log_state = NULL;
	int result;

	log_state = platform_get_drvdata(to_platform_device(dev));
	result = trusty_std_call32(log_state->trusty_dev,
		SMC_SC_SYSCTL_GET_LOGLEVEL, 0, 0, 0);
	if (result == SM_ERR_UNDEFINED_SMC) {
		pr_info("SMC_SC_SYSCTL_GET_CONSOLE not supported on secure side.\n");
		return -EFAULT;
	}

	return scnprintf(buf, PAGE_SIZE, "%d\n", result);
}

static DEVICE_ATTR(trusty_loglevel, 0660,
		   trusty_loglevel_show, trusty_loglevel_store);


static ssize_t trusty_console_show(struct device *dev,
		struct device_attribute *attr, char *buf)
{
	struct trusty_log_state *log_state = NULL;
	int result;

	log_state = platform_get_drvdata(to_platform_device(dev));
	result = trusty_std_call32(log_state->trusty_dev,
		SMC_SC_SYSCTL_GET_CONSOLE, 0, 0, 0);
	if (result == SM_ERR_UNDEFINED_SMC) {
		pr_info("SMC_SC_SYSCTL_GET_CONSOLE not supported on secure side.\n");
		return -EFAULT;
	}

	return scnprintf(buf, PAGE_SIZE, "%d\n", result);
}

static ssize_t trusty_console_store(struct device *dev,
		struct device_attribute *attr, const char *buf, size_t count)
{

	struct trusty_log_state *log_state = NULL;
	unsigned long console;
	int result;

	result = kstrtoul(buf, 0, &console);
	if (result) {
		pr_info("%s is not in hex or decimal form.\n", buf);
		return -EINVAL;
	}

	log_state = platform_get_drvdata(to_platform_device(dev));
	result = trusty_std_call32(log_state->trusty_dev,
				   SMC_SC_SYSCTL_SET_CONSOLE, console, 0, 0);

	if (result == SM_ERR_UNDEFINED_SMC) {
		pr_info("SMC_SC_SYSCTL_SET_CONSOLE not supported on secure side.\n");
		return -EFAULT;
	}

	return strnlen(buf, count);

}
static DEVICE_ATTR(trusty_console, 0660,
		   trusty_console_show, trusty_console_store);


static void trusty_log_free_pages(struct page **pages, size_t num_pages,
				  void *va)
{
	size_t n = num_pages;

	if (va != NULL)
		vm_unmap_ram(va, num_pages);

	while (n--)
		__free_page(pages[n]);
	kfree(pages);
}

static struct page **trusty_log_alloc_pages(size_t size, void **va)
{
	struct page *page, **pages;
	void *addr;
	int i, n;
	int result = 0;

	if (size == 0 || size > (TRUSTY_LOG_MAX_SIZE >> PAGE_SHIFT))
		return ERR_PTR(-EINVAL);

	n = size;
	pages = kmalloc_array(n, sizeof(struct page *), GFP_KERNEL);
	if (!pages)
		return ERR_PTR(-ENOMEM);

	for (i = 0; i < n; i++) {
		page = alloc_page(GFP_KERNEL | __GFP_HIGHMEM);
		if (!page) {
			result = -ENOMEM;
			goto err;
		}
		pages[i] = page;
	}

	addr = vm_map_ram(pages, n, -1, PAGE_KERNEL);
	if (!addr) {
		result = -ENOMEM;
		goto err;
	}
	*va = addr;

	return pages;

err:
	trusty_log_free_pages(pages, i, NULL);
	return ERR_PTR(result);
}

static void *trusty_log_register_buf(struct device *trusty_dev,
				     struct page **pages, size_t num_pages)
{
	uint64_t *pfn_list;
	u32 high_4bytes;
	phys_addr_t pa;
	int n = num_pages;
	int result = 0;

	if (n * sizeof(uint64_t) > PAGE_SIZE)
		return ERR_PTR(-EINVAL);

	pfn_list = (uint64_t *)__get_free_page(GFP_KERNEL | __GFP_ZERO);
	if (!pfn_list)
		return ERR_PTR(-ENOMEM);

	while (n--)
		pfn_list[n] = page_to_pfn(pages[n]);

	pa = virt_to_phys(pfn_list);
#ifdef CONFIG_PHYS_ADDR_T_64BIT
	high_4bytes = (u32)(pa >> 32);
#else
	high_4bytes = 0;
#endif
	result = trusty_std_call32(trusty_dev, SMC_SC_SHARED_LOG_ADD,
				   (u32)pa, high_4bytes, num_pages);
	if (result < 0) {
		pr_err("Trusty std call SMC_SC_SHARED_LOG_ADD failed(%d)\n",
			result);
		free_page((unsigned long)pfn_list);
		return ERR_PTR(result);
	}

	return pfn_list;
}

static int trusty_log_unregister_buf(struct device *trusty_dev, void *buf)
{
	int result;
	u32 high_4bytes;
	phys_addr_t pa = virt_to_phys(buf);

#ifdef CONFIG_PHYS_ADDR_T_64BIT
	high_4bytes = (u32)(pa >> 32);
#else
	high_4bytes = 0;
#endif
	result = trusty_std_call32(trusty_dev, SMC_SC_SHARED_LOG_RM,
				   (u32)pa, high_4bytes, 0);
	if (result) {
		pr_err("Trusty std call SMC_SC_SHARED_LOG_RM failed(%d)\n",
		       result);
		return result;
	}

	free_page((unsigned long)buf);

	return 0;
}

#define trusty_log_dump_lock(s) ({ s->dumpable = 0; })
#define trusty_log_dump_unlock(s) ({ s->dumpable = 1; })

static ssize_t trusty_logsize_store(struct device *dev,
		struct device_attribute  *attr, const char *buf, size_t count)
{
	struct trusty_log_state *s = NULL;
	struct page **pages = NULL;
	void *log_buf, *pfn_list;
	size_t size, n;
	int result;

	result = kstrtoul(buf, 0, (unsigned long *)&size);
	if (result) {
		pr_err("Invalid value argument(%s)!\n", buf);
		return -EINVAL;
	}

	s = platform_get_drvdata(to_platform_device(dev));

	/* Disable log dumpping util log buffer resizing is done */
	trusty_log_dump_lock(s);

	n = size >> PAGE_SHIFT;
	/*
	 * Allocate and register new buffer pages. Doing this before free and
	 * unregister the old buffer is for easily rollback in case of
	 * allocation/register failure
	 */
	pages = trusty_log_alloc_pages(n, &log_buf);
	if (IS_ERR(pages)) {
		pr_err("Trusty log allocate buffer error(%ld)\n",
		       PTR_ERR(pages));
		count = PTR_ERR(pages);
		goto _exit;
	}

	pfn_list = trusty_log_register_buf(s->trusty_dev,
				pages, n);
	if (IS_ERR(pfn_list)) {
		pr_err("Trusty log register buffer failed(%ld)\n",
		       PTR_ERR(pfn_list));
		count = PTR_ERR(pfn_list);
		goto _exit1;
	}

	/* Unregister log buf and free buffer pages */
	result = trusty_log_unregister_buf(s->trusty_dev, s->pfn_list);
	if (result) {
		pr_err("Trusty log unregister buffer error(%d)\n", result);
		count = result;
		goto _exit1;
	}
	trusty_log_free_pages(s->pages, s->num_pages, s->log);

	s->pages = pages;
	s->num_pages = n;
	s->pfn_list = pfn_list;
	s->log = log_buf;
	s->get = 0;
_exit:
	trusty_log_dump_unlock(s);
	return count;
_exit1:
	trusty_log_free_pages(pages, n, log_buf);
	trusty_log_dump_unlock(s);
	return count;

}

static ssize_t trusty_logsize_show(struct device *dev,
		struct device_attribute  *attr, char *buf)
{
	struct trusty_log_state *s = NULL;

	s = platform_get_drvdata(to_platform_device(dev));

	return scnprintf(buf, PAGE_SIZE, "0x%x\n", s->num_pages << PAGE_SHIFT);
}

static DEVICE_ATTR(trusty_logsize, 0660,
		   trusty_logsize_show, trusty_logsize_store);

static struct attribute *trusty_log_attrs[] = {
	&dev_attr_trusty_logsize.attr,
	&dev_attr_trusty_loglevel.attr,
	&dev_attr_trusty_console.attr,
	NULL
};

static const struct attribute_group trusty_log_attr_group = {
	.attrs = trusty_log_attrs,
};

static int trusty_log_probe(struct platform_device *pdev)
{
	struct trusty_log_state *s;
	struct page **pages;
	void *log_buf, *pfn_list;
	size_t num_pages;
	int result;

	dev_dbg(&pdev->dev, "%s\n", __func__);
	if (!trusty_supports_logging(pdev->dev.parent))
		return -ENXIO;

	s = kzalloc(sizeof(*s), GFP_KERNEL);
	if (!s) {
		result = -ENOMEM;
		goto error_alloc_state;
	}

	spin_lock_init(&s->lock);
	s->dev = &pdev->dev;
	s->trusty_dev = s->dev->parent;
	s->get = 0;

	num_pages = TRUSTY_LOG_DEFAULT_SIZE >> PAGE_SHIFT;
	pages = trusty_log_alloc_pages(num_pages, &log_buf);
	if (IS_ERR(pages)) {
		result = PTR_ERR(pages);
		goto error_alloc_pages;
	}

	pfn_list = trusty_log_register_buf(s->trusty_dev, pages, num_pages);
	if (IS_ERR(pfn_list)) {
		dev_err(&pdev->dev, "register failed(%ld)\n",
			PTR_ERR(pfn_list));
		result = PTR_ERR(pfn_list);
		goto error_register_buf;
	}

	s->pages = pages;
	s->num_pages = num_pages;
	s->pfn_list = pfn_list;
	s->log = log_buf;
	s->get = 0;
	s->dumpable = 1;

	s->call_notifier.notifier_call = trusty_log_call_notify;
	result = trusty_call_notifier_register(s->trusty_dev,
					       &s->call_notifier);
	if (result < 0) {
		dev_err(&pdev->dev,
			"failed to register trusty call notifier\n");
		goto error_call_notifier;
	}

	s->panic_notifier.notifier_call = trusty_log_panic_notify;
	result = atomic_notifier_chain_register(&panic_notifier_list,
						&s->panic_notifier);
	if (result < 0) {
		dev_err(&pdev->dev,
			"failed to register panic notifier\n");
		goto error_panic_notifier;
	}
	platform_set_drvdata(pdev, s);

	result = sysfs_create_group(&pdev->dev.kobj, &trusty_log_attr_group);
	if (result)
		dev_err(&pdev->dev, "Failed to create debug files\n");

	return 0;

error_panic_notifier:
	trusty_call_notifier_unregister(s->trusty_dev, &s->call_notifier);
error_call_notifier:
	trusty_log_unregister_buf(s->trusty_dev, pfn_list);
error_register_buf:
	trusty_log_free_pages(pages, num_pages, log_buf);
error_alloc_pages:
	kfree(s);
error_alloc_state:
	return result;
}

static void trusty_log_remove(struct platform_device *pdev)
{
	struct trusty_log_state *s = platform_get_drvdata(pdev);

	dev_dbg(&pdev->dev, "%s\n", __func__);

	atomic_notifier_chain_unregister(&panic_notifier_list,
					 &s->panic_notifier);
	trusty_call_notifier_unregister(s->trusty_dev, &s->call_notifier);

	trusty_log_unregister_buf(s->trusty_dev, s->pfn_list);

	trusty_log_free_pages(s->pages, s->num_pages, s->log);

	kfree(s);

	sysfs_remove_group(&pdev->dev.kobj, &trusty_log_attr_group);

	return;
}

static const struct of_device_id trusty_test_of_match[] = {
	{ .compatible = "sprd,trusty-log-v1", },
	{},
};

struct platform_driver trusty_log_driver = {
	.probe = trusty_log_probe,
	.remove = trusty_log_remove,
	.driver = {
		.name = "sprd-trusty-log",
		.owner = THIS_MODULE,
		.of_match_table = trusty_test_of_match,
	},
};

module_platform_driver(trusty_log_driver);


MODULE_DESCRIPTION("Sprd trusty log driver");
MODULE_LICENSE("GPL v2");
