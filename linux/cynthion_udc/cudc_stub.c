// SPDX-License-Identifier: GPL-2.0
/*
 * Stub backend: an in-kernel fake USB host in place of the Cynthion + remote host.
 * - pullup on: attach at high speed, enumerate the bound gadget like Linux's hub driver.
 * - SET_ADDRESS answered locally, as the Cynthion firmware will (docs/usb-device-mode.md).
 * - after SET_CONFIGURATION: loops each interface's bulk IN data back into its bulk OUT.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/completion.h>
#include <linux/jiffies.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/workqueue.h>

#include "cudc.h"

#define STUB_ADDRESS	1
#define STUB_MAX_PAIRS	8
#define STUB_LOOP_BUF	(64 * 1024)	/* >= NCM's largest NTB (16 KiB); full = NAK the IN side */
#define STUB_CFG_MAX	4096		/* USB_COMP_EP0_BUFSIZ */

/* Host-side control limits, USB 2.0 9.2.6.4; expiry fails the step and logs the elapsed time. */
#define STUB_DATA_IN_MS		500	/* first data packet to host */
#define STUB_NO_DATA_MS		50	/* status, no data stage */
#define STUB_DATA_OUT_MS	5000	/* all data to device + status */

struct stub_pair {
	struct cudc_ep	*in, *out;
	u8		*buf;
	size_t		len;
	u64		looped;
};

struct stub {
	struct platform_device	*pdev;
	struct cudc		*u;
	struct workqueue_struct	*wq;		/* ordered: enum and loop never overlap */
	struct work_struct	enum_work;
	struct work_struct	loop_work;
	struct completion	ep0_queued;
	bool			stopping;
	bool			looping;
	u8			*cfg;
	struct stub_pair	pairs[STUB_MAX_PAIRS];
	int			npairs;
};

static struct stub *the_stub;

#define sdev(s)	(&(s)->u->gadget.dev)

/* One control transfer, host side. Returns bytes transferred or < 0. */
static int stub_control(struct stub *s, u8 type, u8 request, u16 value, u16 index,
			u16 length, void *data, const char *what)
{
	struct usb_ctrlrequest cr = {
		.bRequestType = type, .bRequest = request,
		.wValue = cpu_to_le16(value), .wIndex = cpu_to_le16(index),
		.wLength = cpu_to_le16(length),
	};
	bool in = type & USB_DIR_IN;
	unsigned int limit_ms = !length ? STUB_NO_DATA_MS : in ? STUB_DATA_IN_MS : STUB_DATA_OUT_MS;
	struct cudc *u = s->u;
	struct cudc_request *req;
	unsigned long t0, left, flags;
	int ret, n;

	reinit_completion(&s->ep0_queued);
	t0 = jiffies;
	ret = cudc_setup(u, &cr);
	if (ret < 0) {
		dev_info(sdev(s), "stub host: %s: STALL (%d)\n", what, ret);
		return -EPIPE;
	}
	left = wait_for_completion_timeout(&s->ep0_queued, msecs_to_jiffies(limit_ms));
	if (READ_ONCE(s->stopping))
		return -ESHUTDOWN;
	if (!left) {
		dev_err(sdev(s), "stub host: %s: no ep0 %s within %u ms (elapsed %u ms)\n", what,
			length ? "data" : "status", limit_ms, jiffies_to_msecs(jiffies - t0));
		return -ETIMEDOUT;
	}

	spin_lock_irqsave(&u->lock, flags);
	req = cudc_next_request(&u->ep[0]);
	if (!req) {
		spin_unlock_irqrestore(&u->lock, flags);
		dev_err(sdev(s), "stub host: %s: ep0 signalled but empty\n", what);
		return -EPROTO;
	}
	n = min_t(unsigned int, length, req->req.length);
	if (n && in)
		memcpy(data, req->req.buf, n);
	else if (n)
		memcpy(req->req.buf, data, n);
	req->req.actual = n;
	cudc_take(req);
	spin_unlock_irqrestore(&u->lock, flags);
	cudc_giveback(&u->ep[0], req, 0);
	dev_dbg(sdev(s), "stub host: %s: %d bytes in %u ms\n", what, n,
		jiffies_to_msecs(jiffies - t0));
	return n;
}

static int stub_get_descriptor(struct stub *s, u8 dtype, u16 length, void *buf, const char *what)
{
	return stub_control(s, USB_DIR_IN | USB_TYPE_STANDARD | USB_RECIP_DEVICE,
			    USB_REQ_GET_DESCRIPTOR, dtype << 8, 0, length, buf, what);
}

/* Per interface: the first alt setting that has bulk endpoints, and its first bulk IN / OUT. */
struct stub_intf {
	u8 num, alt, in, out;
	bool found;
};

static int stub_parse_config(struct stub *s, int total, struct stub_intf *intf, int max)
{
	struct stub_intf *cur = NULL;
	int pos = 0, n = 0, i;

	while (pos + 2 <= total) {
		struct usb_descriptor_header *h = (void *)(s->cfg + pos);

		if (h->bLength < 2 || pos + h->bLength > total)
			return -EPROTO;
		if (h->bDescriptorType == USB_DT_INTERFACE && h->bLength >= USB_DT_INTERFACE_SIZE) {
			struct usb_interface_descriptor *d = (void *)h;

			cur = NULL;
			for (i = 0; i < n; i++)
				if (intf[i].num == d->bInterfaceNumber)
					cur = &intf[i];
			if (!cur && n < max) {
				cur = &intf[n++];
				cur->num = d->bInterfaceNumber;
			}
			if (cur && cur->found)
				cur = NULL;	/* keep the first alt with bulk */
			else if (cur)
				cur->alt = d->bAlternateSetting;
		} else if (h->bDescriptorType == USB_DT_ENDPOINT && cur &&
			   h->bLength >= USB_DT_ENDPOINT_SIZE) {
			struct usb_endpoint_descriptor *e = (void *)h;

			if (usb_endpoint_xfer_bulk(e)) {
				if (usb_endpoint_dir_in(e) && !cur->in)
					cur->in = e->bEndpointAddress;
				else if (usb_endpoint_dir_out(e) && !cur->out)
					cur->out = e->bEndpointAddress;
				cur->found = true;
			}
		}
		pos += h->bLength;
	}
	return n;
}

static void stub_free_pairs(struct stub *s)
{
	int i;

	for (i = 0; i < s->npairs; i++)
		kvfree(s->pairs[i].buf);
	memset(s->pairs, 0, sizeof(s->pairs));
	s->npairs = 0;
}

static void stub_enum_work(struct work_struct *work)
{
	struct stub *s = container_of(work, struct stub, enum_work);
	struct usb_device_descriptor dev = {};
	struct usb_config_descriptor *cfg = (void *)s->cfg;
	struct stub_intf intf[STUB_MAX_PAIRS] = {};
	int ret, total, nintf, i;

	dev_info(sdev(s), "stub host: attach, high speed\n");
	cudc_bus_reset(s->u, USB_SPEED_HIGH);

	/* Linux hub_port_init: 64-byte device descriptor read, second reset, then SET_ADDRESS. */
	ret = stub_get_descriptor(s, USB_DT_DEVICE, 64, s->cfg, "GET_DESCRIPTOR(device, 64)");
	if (ret < 8)
		goto fail;
	if (READ_ONCE(s->stopping))
		return;
	cudc_bus_reset(s->u, USB_SPEED_HIGH);
	cudc_set_address(s->u, STUB_ADDRESS);	/* answered locally, never reaches the gadget */
	dev_dbg(sdev(s), "stub host: SET_ADDRESS(%u) answered locally\n", STUB_ADDRESS);

	ret = stub_get_descriptor(s, USB_DT_DEVICE, USB_DT_DEVICE_SIZE, &dev,
				  "GET_DESCRIPTOR(device)");
	if (ret != USB_DT_DEVICE_SIZE || dev.bDescriptorType != USB_DT_DEVICE)
		goto fail;
	dev_info(sdev(s), "stub host: device descriptor %04x:%04x bcdUSB %04x class %02x bMaxPacketSize0 %u bNumConfigurations %u\n",
		 le16_to_cpu(dev.idVendor), le16_to_cpu(dev.idProduct), le16_to_cpu(dev.bcdUSB),
		 dev.bDeviceClass, dev.bMaxPacketSize0, dev.bNumConfigurations);

	ret = stub_get_descriptor(s, USB_DT_CONFIG, USB_DT_CONFIG_SIZE, s->cfg,
				  "GET_DESCRIPTOR(config, 9)");
	if (ret != USB_DT_CONFIG_SIZE)
		goto fail;
	total = min_t(int, le16_to_cpu(cfg->wTotalLength), STUB_CFG_MAX);
	ret = stub_get_descriptor(s, USB_DT_CONFIG, total, s->cfg, "GET_DESCRIPTOR(config)");
	if (ret != total)
		goto fail;
	nintf = stub_parse_config(s, total, intf, ARRAY_SIZE(intf));
	if (nintf < 0) {
		ret = nintf;
		goto fail;
	}
	dev_info(sdev(s), "stub host: config %u: wTotalLength %d, %u interfaces\n",
		 cfg->bConfigurationValue, total, cfg->bNumInterfaces);

	ret = stub_control(s, USB_DIR_OUT | USB_TYPE_STANDARD | USB_RECIP_DEVICE,
			   USB_REQ_SET_CONFIGURATION, cfg->bConfigurationValue, 0, 0, NULL,
			   "SET_CONFIGURATION");
	if (ret < 0)
		goto fail;
	dev_info(sdev(s), "stub host: configuration %u set\n", cfg->bConfigurationValue);

	for (i = 0; i < nintf; i++) {
		struct stub_pair *p;

		if (!intf[i].found)
			continue;
		if (intf[i].alt) {
			ret = stub_control(s, USB_DIR_OUT | USB_TYPE_STANDARD | USB_RECIP_INTERFACE,
					   USB_REQ_SET_INTERFACE, intf[i].alt, intf[i].num, 0, NULL,
					   "SET_INTERFACE");
			if (ret < 0)
				goto fail;
			dev_info(sdev(s), "stub host: interface %u alt %u set\n", intf[i].num,
				 intf[i].alt);
		}
		if (!intf[i].in || !intf[i].out)
			continue;
		p = &s->pairs[s->npairs];
		p->in = cudc_ep_by_addr(s->u, intf[i].in);
		p->out = cudc_ep_by_addr(s->u, intf[i].out);
		p->buf = kvmalloc(STUB_LOOP_BUF, GFP_KERNEL);
		if (!p->in || !p->out || !p->buf) {
			kvfree(p->buf);
			ret = -ENOMEM;
			goto fail;
		}
		s->npairs++;
		dev_info(sdev(s), "stub host: loopback interface %u: ep%02x -> ep%02x\n",
			 intf[i].num, intf[i].in, intf[i].out);
	}
	WRITE_ONCE(s->looping, true);
	dev_info(sdev(s), "stub host: enumeration done\n");
	queue_work(s->wq, &s->loop_work);
	return;

fail:
	if (!READ_ONCE(s->stopping))
		dev_err(sdev(s), "stub host: enumeration failed: %d\n", ret);
}

/* Moves bytes host-side: gadget bulk IN requests -> buffer -> gadget bulk OUT requests. */
static void stub_loop_work(struct work_struct *work)
{
	struct stub *s = container_of(work, struct stub, loop_work);
	struct cudc *u = s->u;
	struct cudc_request *req;
	unsigned long flags;
	bool progress;
	int i;

	do {
		progress = false;
		for (i = 0; i < s->npairs && !READ_ONCE(s->stopping); i++) {
			struct stub_pair *p = &s->pairs[i];
			unsigned int n;

			spin_lock_irqsave(&u->lock, flags);
			req = p->in->desc && !p->in->halted ? cudc_next_request(p->in) : NULL;
			if (req && p->len + req->req.length <= STUB_LOOP_BUF) {
				memcpy(p->buf + p->len, req->req.buf, req->req.length);
				p->len += req->req.length;
				req->req.actual = req->req.length;
				cudc_take(req);
				spin_unlock_irqrestore(&u->lock, flags);
				dev_dbg(sdev(s), "stub host: %s IN %u bytes\n", p->in->ep.name,
					req->req.length);
				cudc_giveback(p->in, req, 0);
				progress = true;
				spin_lock_irqsave(&u->lock, flags);
			}
			req = p->len && p->out->desc && !p->out->halted ?
				cudc_next_request(p->out) : NULL;
			if (req) {
				n = min_t(size_t, p->len, req->req.length);
				memcpy(req->req.buf, p->buf, n);
				memmove(p->buf, p->buf + n, p->len - n);
				p->len -= n;
				p->looped += n;
				req->req.actual = n;
				cudc_take(req);
				spin_unlock_irqrestore(&u->lock, flags);
				dev_dbg(sdev(s), "stub host: %s OUT %u bytes (looped %llu)\n",
					p->out->ep.name, n, p->looped);
				cudc_giveback(p->out, req, 0);
				progress = true;
				continue;
			}
			spin_unlock_irqrestore(&u->lock, flags);
		}
	} while (progress && !READ_ONCE(s->stopping));
}

/* ---- backend ops ---- */

static int stub_connect(struct cudc *u)
{
	struct stub *s = u->backend;

	WRITE_ONCE(s->stopping, false);
	queue_work(s->wq, &s->enum_work);
	return 0;
}

static void stub_disconnect(struct cudc *u)
{
	struct stub *s = u->backend;
	int i;

	WRITE_ONCE(s->stopping, true);
	WRITE_ONCE(s->looping, false);
	complete(&s->ep0_queued);
	cancel_work_sync(&s->enum_work);
	cancel_work_sync(&s->loop_work);
	for (i = 0; i < s->npairs; i++)
		dev_info(sdev(s), "stub host: %s -> %s looped %llu bytes\n", s->pairs[i].in->ep.name,
			 s->pairs[i].out->ep.name, s->pairs[i].looped);
	stub_free_pairs(s);
	dev_info(sdev(s), "stub host: detach\n");
}

static int stub_ep_enable(struct cudc *u, struct cudc_ep *ep,
			  const struct usb_endpoint_descriptor *desc)
{
	dev_dbg(&u->gadget.dev, "stub: EP_ENABLE %s addr %02x attr %02x maxp %u\n", ep->ep.name,
		desc->bEndpointAddress, desc->bmAttributes, usb_endpoint_maxp(desc));
	return 0;
}

static void stub_ep_disable(struct cudc *u, struct cudc_ep *ep)
{
	dev_dbg(&u->gadget.dev, "stub: EP_DISABLE %s\n", ep->ep.name);
}

static void stub_queue(struct cudc *u, struct cudc_ep *ep)
{
	struct stub *s = u->backend;

	if (READ_ONCE(s->looping))
		queue_work(s->wq, &s->loop_work);
}

static void stub_dequeue(struct cudc *u, struct cudc_ep *ep, struct cudc_request *req)
{
	dev_dbg(&u->gadget.dev, "stub: dequeue %s req %p\n", ep->ep.name, req);
}

static int stub_set_halt(struct cudc *u, struct cudc_ep *ep, bool halt, bool wedge)
{
	dev_dbg(&u->gadget.dev, "stub: EP_HALT %s halt %d wedge %d\n", ep->ep.name, halt, wedge);
	return 0;
}

static void stub_setup_reply(struct cudc *u)
{
	struct stub *s = u->backend;

	complete(&s->ep0_queued);
}

static const struct cudc_backend_ops stub_ops = {
	.name		= "stub",
	.connect	= stub_connect,
	.disconnect	= stub_disconnect,
	.ep_enable	= stub_ep_enable,
	.ep_disable	= stub_ep_disable,
	.queue		= stub_queue,
	.dequeue	= stub_dequeue,
	.set_halt	= stub_set_halt,
	.setup_reply	= stub_setup_reply,
};

int cudc_stub_init(void)
{
	struct stub *s;
	int ret;

	s = kzalloc(sizeof(*s), GFP_KERNEL);
	if (!s)
		return -ENOMEM;
	init_completion(&s->ep0_queued);
	INIT_WORK(&s->enum_work, stub_enum_work);
	INIT_WORK(&s->loop_work, stub_loop_work);
	ret = -ENOMEM;
	s->cfg = kzalloc(STUB_CFG_MAX, GFP_KERNEL);
	s->wq = alloc_ordered_workqueue(KBUILD_MODNAME "_stub", 0);
	if (!s->cfg || !s->wq)
		goto err_free;

	s->pdev = platform_device_register_simple(KBUILD_MODNAME, PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(s->pdev)) {
		ret = PTR_ERR(s->pdev);
		goto err_free;
	}
	s->u = cudc_create(&s->pdev->dev, &stub_ops, s);
	if (IS_ERR(s->u)) {
		ret = PTR_ERR(s->u);
		goto err_pdev;
	}
	the_stub = s;
	return 0;

err_pdev:
	platform_device_unregister(s->pdev);
err_free:
	if (s->wq)
		destroy_workqueue(s->wq);
	kfree(s->cfg);
	kfree(s);
	return ret;
}

void cudc_stub_exit(void)
{
	struct stub *s = the_stub;

	cudc_destroy(s->u);	/* unbinds any gadget: pullup(0) -> stub_disconnect */
	platform_device_unregister(s->pdev);
	destroy_workqueue(s->wq);
	kfree(s->cfg);
	kfree(s);
}
