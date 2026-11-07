// SPDX-License-Identifier: GPL-2.0
/*
 * Stub backend: an in-kernel fake USB host + the Cynthion firmware/gateware contract.
 * - Enumerates the bound gadget like Linux's hub driver, then (autoloop) loops each IN endpoint
 *   packet-for-packet into its OUT endpoint.
 * - Answers locally what the firmware will (docs/usb-device-mode.md, "Locally answered"):
 *   SET_ADDRESS, GET_STATUS, SET/CLEAR_FEATURE.
 * - Models per-endpoint data toggle, NAK, STALL, ZLP rules.
 * - debugfs `ctl` drives the host side for tests (linux/cynthion_udc/README.md).
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/completion.h>
#include <linux/ctype.h>
#include <linux/debugfs.h>
#include <linux/hex.h>
#include <linux/jiffies.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/unaligned.h>
#include <linux/wait.h>
#include <linux/workqueue.h>

#include "cudc.h"

#define STUB_ADDRESS	1
#define STUB_MAX_PAIRS	8
#define STUB_CFG_MAX	4096		/* USB_COMP_EP0_BUFSIZ */
#define STUB_EP0_MAXP	CUDC_EP0_MAXPACKET
#define STUB_MAX_PKTS	(STUB_CFG_MAX / STUB_EP0_MAXP + 1)
#define STUB_CMD_MAX	(2 * STUB_CFG_MAX + 128)
#define STUB_RES_MAX	(2 * 16 * CUDC_EPN_MAXPACKET + 512)
#define STUB_IN_PKTS	16		/* `in` reads at most this many packets */

/* Host-side control limits, USB 2.0 9.2.6.4; expiry fails the step and logs the elapsed time. */
#define STUB_DATA_IN_MS		500	/* first data packet to host */
#define STUB_NO_DATA_MS		50	/* status, no data stage */
#define STUB_DATA_OUT_MS	5000	/* all data to device + status */

struct stub_pair {
	struct cudc_ep	*in, *out;
	u8		hold[CUDC_EPN_MAXPACKET];	/* packet taken from IN, not yet accepted by OUT */
	unsigned int	hlen;
	bool		valid;
	u64		looped;
};

struct stub_ep {
	u8			tog;		/* next data PID: 0 DATA0, 1 DATA1 */
	unsigned int		off;		/* IN: bytes of the head request already sent */
	struct cudc_request	*cur;		/* IN: request `off` belongs to */
	u32			naks, dequeues;
};

struct stub {
	struct platform_device	*pdev;
	struct cudc		*u;
	struct workqueue_struct	*wq;		/* ordered: enum, loop, resume never overlap */
	struct work_struct	enum_work, loop_work, resume_work;
	struct completion	ep0_queued;
	wait_queue_head_t	evt;		/* kicked on queue/dequeue/halt/disconnect */
	atomic_t		gen;
	bool			stopping, looping, autoloop, suspended, ctl_stalled;
	u8			*cfg;
	u8			address, testmode;
	u32			attaches, detaches, wakeups, refused;
	struct stub_pair	pairs[STUB_MAX_PAIRS];
	int			npairs;
	struct stub_ep		ep[CUDC_NUM_EPS];

	/* last control transfer's data-stage packets */
	u16			pk[STUB_MAX_PKTS];
	int			npk;

	/* debugfs ctl */
	struct mutex		cmd_lock;
	char			*res;
	size_t			res_len;
	struct dentry		*dir;
};

static struct stub *the_stub;

#define sdev(s)	(&(s)->u->gadget.dev)

static struct stub_ep *sep(struct stub *s, struct cudc_ep *ep)
{
	return &s->ep[ep - s->u->ep];
}

static void stub_kick(struct stub *s)
{
	atomic_inc(&s->gen);
	wake_up(&s->evt);
	if (READ_ONCE(s->looping))
		queue_work(s->wq, &s->loop_work);
}

/* ---- packet engine: one bus transaction per call; the gateware's contract ---- */

/* IN token. 0: packet in buf; -EAGAIN: NAK; -EPIPE: STALL; -ENODEV: endpoint not enabled. */
static int stub_try_in(struct stub *s, struct cudc_ep *ep, u8 *buf, unsigned int *len, u8 *tog)
{
	struct cudc *u = s->u;
	struct stub_ep *se = sep(s, ep);
	struct cudc_request *req;
	unsigned long flags;
	unsigned int n, maxp;
	bool done = false;

	spin_lock_irqsave(&u->lock, flags);
	if (!ep->desc) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -ENODEV;
	}
	if (ep->halted) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -EPIPE;
	}
	req = cudc_next_request(ep);
	if (!req) {
		se->naks++;
		spin_unlock_irqrestore(&u->lock, flags);
		return -EAGAIN;
	}
	maxp = ep->ep.maxpacket;
	se->cur = req;
	if (se->off < req->req.length) {
		n = min(maxp, req->req.length - se->off);
		memcpy(buf, req->req.buf + se->off, n);
		se->off += n;
		/* full last packet of a req->zero transfer: the ZLP is the next transaction */
		done = se->off == req->req.length && !(req->req.zero && n == maxp);
	} else {
		n = 0;		/* zero-length request, or the pending ZLP */
		done = true;
	}
	*len = n;
	*tog = se->tog;
	se->tog ^= 1;
	if (done) {
		req->req.actual = req->req.length;
		cudc_take(req);
		se->off = 0;
		se->cur = NULL;
	}
	spin_unlock_irqrestore(&u->lock, flags);
	if (done)
		cudc_giveback(ep, req, 0);
	return 0;
}

/* OUT token + data. 0: accepted; -EAGAIN: NAK; -EPIPE: STALL; -ENODEV: not enabled. */
static int stub_try_out(struct stub *s, struct cudc_ep *ep, const u8 *buf, unsigned int len, u8 *tog)
{
	struct cudc *u = s->u;
	struct stub_ep *se = sep(s, ep);
	struct cudc_request *req;
	unsigned long flags;
	unsigned int space, maxp;
	int status = 0;

	spin_lock_irqsave(&u->lock, flags);
	if (!ep->desc) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -ENODEV;
	}
	if (ep->halted) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -EPIPE;
	}
	req = cudc_next_request(ep);
	if (!req) {
		se->naks++;
		spin_unlock_irqrestore(&u->lock, flags);
		return -EAGAIN;
	}
	maxp = ep->ep.maxpacket;
	space = req->req.length - req->req.actual;
	if (len > space) {
		len = space;
		status = -EOVERFLOW;
	}
	memcpy(req->req.buf + req->req.actual, buf, len);
	req->req.actual += len;
	*tog = se->tog;
	se->tog ^= 1;
	/* a short packet (incl. ZLP) or a full buffer ends the request */
	if (status || len < maxp || req->req.actual == req->req.length) {
		cudc_take(req);
		spin_unlock_irqrestore(&u->lock, flags);
		cudc_giveback(ep, req, status);
		return 0;
	}
	spin_unlock_irqrestore(&u->lock, flags);
	return 0;
}

/* Retries a NAKed transaction until it is taken or `ms` pass. Wakes on any queue/halt event. */
static int stub_in_wait(struct stub *s, struct cudc_ep *ep, u8 *buf, unsigned int *len, u8 *tog,
			unsigned int ms)
{
	unsigned long end = jiffies + msecs_to_jiffies(ms);
	int r, gen;

	for (;;) {
		gen = atomic_read(&s->gen);
		r = stub_try_in(s, ep, buf, len, tog);
		if (r != -EAGAIN || time_after_eq(jiffies, end) || READ_ONCE(s->stopping))
			return r;
		wait_event_timeout(s->evt, atomic_read(&s->gen) != gen || READ_ONCE(s->stopping),
				   end - jiffies);
	}
}

static int stub_out_wait(struct stub *s, struct cudc_ep *ep, const u8 *buf, unsigned int len,
			 u8 *tog, unsigned int ms)
{
	unsigned long end = jiffies + msecs_to_jiffies(ms);
	int r, gen;

	for (;;) {
		gen = atomic_read(&s->gen);
		r = stub_try_out(s, ep, buf, len, tog);
		if (r != -EAGAIN || time_after_eq(jiffies, end) || READ_ONCE(s->stopping))
			return r;
		wait_event_timeout(s->evt, atomic_read(&s->gen) != gen || READ_ONCE(s->stopping),
				   end - jiffies);
	}
}

/* ---- local answers: what the firmware does without asking the PC ---- */

/* 1: handled OK (out/outn filled), 0: pass to the gadget, < 0: handled, STALL. */
static int stub_local(struct stub *s, const struct usb_ctrlrequest *cr, u8 *out, u16 *outn)
{
	struct cudc *u = s->u;
	u8 rt = cr->bRequestType;
	u16 val = le16_to_cpu(cr->wValue), idx = le16_to_cpu(cr->wIndex);
	bool set = cr->bRequest == USB_REQ_SET_FEATURE;
	struct cudc_ep *ep;
	unsigned long flags;
	u16 st;

	*outn = 0;
	switch (cr->bRequest) {
	case USB_REQ_GET_STATUS:
		if (rt == (USB_DIR_IN | USB_RECIP_DEVICE)) {
			st = u->devstatus & (BIT(USB_DEVICE_SELF_POWERED) |
					     BIT(USB_DEVICE_REMOTE_WAKEUP));
		} else if (rt == (USB_DIR_IN | USB_RECIP_INTERFACE)) {
			st = 0;
		} else if (rt == (USB_DIR_IN | USB_RECIP_ENDPOINT)) {
			if (!(idx & 0x7f)) {
				st = 0;
			} else {
				ep = cudc_ep_by_addr(u, idx);
				if (!ep || !ep->desc)
					return -EPIPE;
				st = ep->halted;
			}
		} else {
			return 0;
		}
		put_unaligned_le16(st, out);
		*outn = 2;
		return 1;
	case USB_REQ_SET_FEATURE:
	case USB_REQ_CLEAR_FEATURE:
		if (rt == USB_RECIP_DEVICE) {
			if (val == USB_DEVICE_REMOTE_WAKEUP) {
				spin_lock_irqsave(&u->lock, flags);
				if (set)
					u->devstatus |= BIT(USB_DEVICE_REMOTE_WAKEUP);
				else
					u->devstatus &= ~BIT(USB_DEVICE_REMOTE_WAKEUP);
				spin_unlock_irqrestore(&u->lock, flags);
				return 1;
			}
			if (val == USB_DEVICE_TEST_MODE && set && !(idx & 0xff) &&
			    (idx >> 8) >= 1 && (idx >> 8) <= 5) {
				s->testmode = idx >> 8;
				return 1;
			}
			return -EPIPE;
		}
		if (rt == USB_RECIP_ENDPOINT) {
			if (val != USB_ENDPOINT_HALT)
				return -EPIPE;
			if (!(idx & 0x7f))
				return set ? -EPIPE : 1;
			ep = cudc_ep_by_addr(u, idx);
			if (!ep || !ep->desc)
				return -EPIPE;
			spin_lock_irqsave(&u->lock, flags);
			if (set) {
				ep->halted = true;
			} else if (!ep->wedged) {	/* a wedged endpoint ignores the host */
				ep->halted = false;
				sep(s, ep)->tog = 0;
			}
			spin_unlock_irqrestore(&u->lock, flags);
			stub_kick(s);
			return 1;
		}
		return 0;
	}
	return 0;
}

/* ---- control transfers, host side ---- */

/* One control transfer. Returns bytes transferred or < 0 (-EPIPE STALL, -ETIMEDOUT, -EPROTO). */
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
	u8 lbuf[2];
	u16 ln;
	int ret, n, i;

	s->npk = 0;
	ret = stub_local(s, &cr, lbuf, &ln);
	if (ret) {
		if (ret < 0) {
			dev_info(sdev(s), "stub host: %s: STALL (local)\n", what);
			return -EPIPE;
		}
		n = min_t(unsigned int, length, ln);
		if (n && in)
			memcpy(data, lbuf, n);
		if (in && length)
			s->pk[s->npk++] = n;
		return n;
	}

	reinit_completion(&s->ep0_queued);
	WRITE_ONCE(s->ctl_stalled, false);
	t0 = jiffies;
	ret = cudc_setup(u, &cr);
	if (ret < 0) {
		dev_info(sdev(s), "stub host: %s: STALL (%d)\n", what, ret);
		return -EPIPE;
	}
	left = wait_for_completion_timeout(&s->ep0_queued, msecs_to_jiffies(limit_ms));
	if (READ_ONCE(s->stopping))
		return -ESHUTDOWN;
	if (READ_ONCE(s->ctl_stalled)) {		/* function called usb_ep_set_halt(ep0) */
		dev_info(sdev(s), "stub host: %s: STALL (function)\n", what);
		return -EPIPE;
	}
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
	if (in && length && req->req.length < length && req->req.length % STUB_EP0_MAXP == 0 &&
	    !req->req.zero) {
		/* the host would wait for more: a function that skips req->zero hangs real hosts */
		cudc_take(req);
		spin_unlock_irqrestore(&u->lock, flags);
		dev_err(sdev(s), "stub host: %s: %u of %u bytes on a packet boundary without ZLP\n",
			what, req->req.length, length);
		cudc_giveback(&u->ep[0], req, -EPROTO);
		return -EPROTO;
	}
	if (n && in)
		memcpy(data, req->req.buf, n);
	else if (n)
		memcpy(req->req.buf, data, n);
	req->req.actual = n;
	if (in && length) {
		for (i = 0; i < n / STUB_EP0_MAXP; i++)
			s->pk[s->npk++] = STUB_EP0_MAXP;
		if (n % STUB_EP0_MAXP || (req->req.length < length && req->req.length % STUB_EP0_MAXP == 0))
			s->pk[s->npk++] = n % STUB_EP0_MAXP;
	}
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
	s->address = STUB_ADDRESS;
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
		if (!intf[i].in || !intf[i].out || !READ_ONCE(s->autoloop))
			continue;
		p = &s->pairs[s->npairs];
		p->in = cudc_ep_by_addr(s->u, intf[i].in);
		p->out = cudc_ep_by_addr(s->u, intf[i].out);
		if (!p->in || !p->out) {
			ret = -ENODEV;
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

/* Moves packets host-side: gadget IN endpoint -> hold -> gadget OUT endpoint, boundaries kept. */
static void stub_loop_work(struct work_struct *work)
{
	struct stub *s = container_of(work, struct stub, loop_work);
	bool progress;
	u8 tog;
	int i;

	do {
		progress = false;
		for (i = 0; i < s->npairs && !READ_ONCE(s->stopping); i++) {
			struct stub_pair *p = &s->pairs[i];

			if (!p->valid && !stub_try_in(s, p->in, p->hold, &p->hlen, &tog)) {
				p->valid = true;
				progress = true;
			}
			if (p->valid && !stub_try_out(s, p->out, p->hold, p->hlen, &tog)) {
				p->valid = false;
				p->looped += p->hlen;
				progress = true;
			}
		}
	} while (progress && !READ_ONCE(s->stopping));
}

static void stub_resume_work(struct work_struct *work)
{
	struct stub *s = container_of(work, struct stub, resume_work);

	WRITE_ONCE(s->suspended, false);
	cudc_resume(s->u);
}

/* ---- debugfs ctl: test-only host commands, one line per write, result in the next read ---- */

__printf(2, 3)
static void res_add(struct stub *s, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	s->res_len += vscnprintf(s->res + s->res_len, STUB_RES_MAX - s->res_len, fmt, ap);
	va_end(ap);
}

static void res_hex(struct stub *s, const u8 *p, size_t n)
{
	if (s->res_len + 2 * n + 2 >= STUB_RES_MAX)
		return;
	bin2hex(s->res + s->res_len, p, n);
	s->res_len += 2 * n;
	s->res[s->res_len] = 0;
}

static void res_list(struct stub *s, const char *key, const unsigned int *v, int n)
{
	int i;

	res_add(s, " %s=", key);
	for (i = 0; i < n; i++)
		res_add(s, "%s%u", i ? "," : "", v[i]);
}

static char *tok(char **p)
{
	char *t;

	while ((t = strsep(p, " \t")) && !*t)
		;
	return t;
}

static int tok_u(char **p, unsigned long *v)
{
	char *t = tok(p);

	return t ? kstrtoul(t, 0, v) : -EINVAL;
}

static void cmd_ctrl(struct stub *s, char *args)
{
	unsigned long rt, rq, val, idx, len;
	u8 *data = NULL;
	char *hex;
	int n;

	if (tok_u(&args, &rt) || tok_u(&args, &rq) || tok_u(&args, &val) ||
	    tok_u(&args, &idx) || tok_u(&args, &len) || len > STUB_CFG_MAX) {
		res_add(s, "ctrl err usage");
		return;
	}
	data = kzalloc(STUB_CFG_MAX, GFP_KERNEL);
	if (!data) {
		res_add(s, "ctrl err nomem");
		return;
	}
	hex = tok(&args);
	if (hex && *hex && (strlen(hex) / 2 > len || hex2bin(data, hex, strlen(hex) / 2))) {
		res_add(s, "ctrl err data");
		goto out;
	}
	n = stub_control(s, rt, rq, val, idx, len, data, "ctl");
	if (n == -EPIPE) {
		res_add(s, "ctrl stall");
	} else if (n == -ETIMEDOUT) {
		res_add(s, "ctrl timeout");
	} else if (n < 0) {
		res_add(s, "ctrl err %d", n);
	} else {
		unsigned int pk[STUB_MAX_PKTS];
		int i;

		res_add(s, "ctrl ok n=%d", n);
		for (i = 0; i < s->npk; i++)
			pk[i] = s->pk[i];
		if (s->npk)
			res_list(s, "pkts", pk, s->npk);
		if (rt & USB_DIR_IN) {
			res_add(s, " data=");
			res_hex(s, data, n);
		}
	}
out:
	kfree(data);
}

static struct cudc_ep *cmd_ep(struct stub *s, char **args, const char *cmd)
{
	unsigned long a;
	struct cudc_ep *ep;

	if (tok_u(args, &a) || !(ep = cudc_ep_by_addr(s->u, a))) {
		res_add(s, "%s err ep", cmd);
		return NULL;
	}
	return ep;
}

static void cmd_in(struct stub *s, char *args)
{
	unsigned int lens[STUB_IN_PKTS], togs[STUB_IN_PKTS], n = 0, total = 0;
	u8 *buf, pkt[CUDC_EPN_MAXPACKET];
	unsigned long ms, maxp;
	struct cudc_ep *ep;
	const char *st = "ok";
	u8 tog;
	int r;

	ep = cmd_ep(s, &args, "in");
	if (!ep)
		return;
	if (tok_u(&args, &ms) || tok_u(&args, &maxp) || !maxp || maxp > STUB_IN_PKTS) {
		res_add(s, "in err usage");
		return;
	}
	buf = kmalloc(STUB_IN_PKTS * CUDC_EPN_MAXPACKET, GFP_KERNEL);
	if (!buf) {
		res_add(s, "in err nomem");
		return;
	}
	while (n < maxp) {
		unsigned int len;

		r = stub_in_wait(s, ep, pkt, &len, &tog, ms);
		if (r) {
			st = r == -EAGAIN ? "nak" : r == -EPIPE ? "stall" : "err";
			break;
		}
		memcpy(buf + total, pkt, len);
		total += len;
		lens[n] = len;
		togs[n++] = tog;
		if (len < ep->ep.maxpacket)
			break;
	}
	res_add(s, "in %s bytes=%u", st, total);
	res_list(s, "pkts", lens, n);
	res_list(s, "tog", togs, n);
	res_add(s, " data=");
	res_hex(s, buf, total);
	kfree(buf);
}

static void cmd_out(struct stub *s, char *args)
{
	unsigned int togs[STUB_IN_PKTS], n = 0, off = 0;
	u8 pkt[CUDC_EPN_MAXPACKET], tog;
	unsigned long ms, len;
	struct cudc_ep *ep;
	const char *st = "ok";
	int r, j;

	ep = cmd_ep(s, &args, "out");
	if (!ep)
		return;
	if (tok_u(&args, &ms)) {
		res_add(s, "out err usage");
		return;
	}
	while (!tok_u(&args, &len)) {
		if (len > ep->ep.maxpacket || n >= STUB_IN_PKTS) {
			res_add(s, "out err len");
			return;
		}
		for (j = 0; j < len; j++)
			pkt[j] = ((off + j) * 13 + 17) & 0xff;	/* stream pattern, tests' pattern() */
		r = stub_out_wait(s, ep, pkt, len, &tog, ms);
		if (r) {
			st = r == -EAGAIN ? "nak" : r == -EPIPE ? "stall" : "err";
			break;
		}
		off += len;
		togs[n++] = tog;
	}
	res_add(s, "out %s done=%u", st, n);
	res_list(s, "tog", togs, n);
}

static void cmd_stat(struct stub *s, char *args)
{
	struct cudc_ep *ep = cmd_ep(s, &args, "stat");
	struct stub_ep *se;
	unsigned long flags;

	if (!ep)
		return;
	se = sep(s, ep);
	spin_lock_irqsave(&s->u->lock, flags);
	res_add(s, "stat ok tog=%u halted=%u wedged=%u enabled=%u naks=%u dequeues=%u", se->tog,
		ep->halted, ep->wedged, !!ep->desc, se->naks, se->dequeues);
	spin_unlock_irqrestore(&s->u->lock, flags);
}

static void cmd_devstat(struct stub *s)
{
	res_add(s, "devstat ok address=%u wakeup=%u selfpowered=%u testmode=%u wakeups=%u refused=%u attaches=%u detaches=%u suspended=%u",
		s->address, !!(s->u->devstatus & BIT(USB_DEVICE_REMOTE_WAKEUP)),
		!!(s->u->devstatus & BIT(USB_DEVICE_SELF_POWERED)), s->testmode, s->wakeups,
		s->refused, s->attaches, s->detaches, s->suspended);
}

static void stub_command(struct stub *s, char *line)
{
	char *p = line, *cmd = tok(&p);
	unsigned long v;

	s->res_len = 0;
	s->res[0] = 0;
	if (!cmd) {
		res_add(s, "err empty");
	} else if (!strcmp(cmd, "ctrl")) {
		cmd_ctrl(s, p);
	} else if (!strcmp(cmd, "in")) {
		cmd_in(s, p);
	} else if (!strcmp(cmd, "out")) {
		cmd_out(s, p);
	} else if (!strcmp(cmd, "stat")) {
		cmd_stat(s, p);
	} else if (!strcmp(cmd, "devstat")) {
		cmd_devstat(s);
	} else if (!strcmp(cmd, "suspend")) {
		WRITE_ONCE(s->suspended, true);
		cudc_suspend(s->u);
		res_add(s, "suspend ok");
	} else if (!strcmp(cmd, "resume")) {
		WRITE_ONCE(s->suspended, false);
		cudc_resume(s->u);
		res_add(s, "resume ok");
	} else if (!strcmp(cmd, "vbus") && !tok_u(&p, &v)) {
		cudc_vbus(s->u, v);
		res_add(s, "vbus ok");
	} else if (!strcmp(cmd, "autoloop") && !tok_u(&p, &v)) {
		WRITE_ONCE(s->autoloop, v);
		res_add(s, "autoloop ok");
	} else {
		res_add(s, "%s err unknown", cmd);
	}
}

static ssize_t ctl_write(struct file *f, const char __user *ubuf, size_t count, loff_t *ppos)
{
	struct stub *s = f->private_data;
	char *line;

	if (count > STUB_CMD_MAX)
		return -E2BIG;
	line = memdup_user_nul(ubuf, count);
	if (IS_ERR(line))
		return PTR_ERR(line);
	mutex_lock(&s->cmd_lock);
	stub_command(s, strim(line));
	mutex_unlock(&s->cmd_lock);
	kfree(line);
	return count;
}

static ssize_t ctl_read(struct file *f, char __user *ubuf, size_t count, loff_t *ppos)
{
	struct stub *s = f->private_data;
	ssize_t r;

	mutex_lock(&s->cmd_lock);
	r = simple_read_from_buffer(ubuf, count, ppos, s->res, s->res_len);
	mutex_unlock(&s->cmd_lock);
	return r;
}

static const struct file_operations ctl_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.read = ctl_read,
	.write = ctl_write,
	.llseek = default_llseek,
};

/* ---- backend ops ---- */

static int stub_connect(struct cudc *u)
{
	struct stub *s = u->backend;

	WRITE_ONCE(s->stopping, false);
	s->attaches++;
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
	wake_up(&s->evt);
	cancel_work_sync(&s->enum_work);
	cancel_work_sync(&s->loop_work);
	cancel_work_sync(&s->resume_work);
	for (i = 0; i < s->npairs; i++)
		dev_info(sdev(s), "stub host: %s -> %s looped %llu bytes\n", s->pairs[i].in->ep.name,
			 s->pairs[i].out->ep.name, s->pairs[i].looped);
	stub_free_pairs(s);
	s->detaches++;
	s->address = 0;
	s->testmode = 0;
	s->suspended = false;
	dev_info(sdev(s), "stub host: detach\n");
}

static void stub_ep_reset(struct stub *s, struct cudc_ep *ep)
{
	struct stub_ep *se = sep(s, ep);

	se->tog = 0;
	se->off = 0;
	se->cur = NULL;
}

static int stub_ep_enable(struct cudc *u, struct cudc_ep *ep,
			  const struct usb_endpoint_descriptor *desc)
{
	stub_ep_reset(u->backend, ep);
	dev_dbg(&u->gadget.dev, "stub: EP_ENABLE %s addr %02x attr %02x maxp %u\n", ep->ep.name,
		desc->bEndpointAddress, desc->bmAttributes, usb_endpoint_maxp(desc));
	return 0;
}

static void stub_ep_disable(struct cudc *u, struct cudc_ep *ep)
{
	stub_ep_reset(u->backend, ep);
	dev_dbg(&u->gadget.dev, "stub: EP_DISABLE %s\n", ep->ep.name);
}

static void stub_queue(struct cudc *u, struct cudc_ep *ep)
{
	stub_kick(u->backend);
}

static void stub_dequeue(struct cudc *u, struct cudc_ep *ep, struct cudc_request *req)
{
	struct stub *s = u->backend;
	struct stub_ep *se = sep(s, ep);
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	se->dequeues++;
	if (se->cur == req) {		/* partly sent: the next request starts clean */
		se->off = 0;
		se->cur = NULL;
	}
	spin_unlock_irqrestore(&u->lock, flags);
	dev_dbg(&u->gadget.dev, "stub: dequeue %s req %p\n", ep->ep.name, req);
	stub_kick(s);
}

static int stub_set_halt(struct cudc *u, struct cudc_ep *ep, bool halt, bool wedge)
{
	struct stub *s = u->backend;
	unsigned long flags;

	dev_dbg(&u->gadget.dev, "stub: EP_HALT %s halt %d wedge %d\n", ep->ep.name, halt, wedge);
	if (ep == &u->ep[0]) {		/* STALL the control transfer in flight */
		if (halt) {
			WRITE_ONCE(s->ctl_stalled, true);
			complete(&s->ep0_queued);
		}
		return 0;
	}
	if (!halt) {
		spin_lock_irqsave(&u->lock, flags);
		sep(s, ep)->tog = 0;		/* 9.4.5: clearing a halt resets the toggle */
		spin_unlock_irqrestore(&u->lock, flags);
	}
	return 0;
}

static void stub_setup_reply(struct cudc *u)
{
	struct stub *s = u->backend;

	complete(&s->ep0_queued);
}

static int stub_wakeup(struct cudc *u)
{
	struct stub *s = u->backend;

	if (!(u->devstatus & BIT(USB_DEVICE_REMOTE_WAKEUP))) {
		s->refused++;
		return -EINVAL;
	}
	s->wakeups++;
	queue_work(s->wq, &s->resume_work);
	return 0;
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
	.wakeup		= stub_wakeup,
};

int cudc_stub_init(void)
{
	struct stub *s;
	int ret;

	s = kzalloc(sizeof(*s), GFP_KERNEL);
	if (!s)
		return -ENOMEM;
	init_completion(&s->ep0_queued);
	init_waitqueue_head(&s->evt);
	mutex_init(&s->cmd_lock);
	s->autoloop = true;
	INIT_WORK(&s->enum_work, stub_enum_work);
	INIT_WORK(&s->loop_work, stub_loop_work);
	INIT_WORK(&s->resume_work, stub_resume_work);
	ret = -ENOMEM;
	s->cfg = kzalloc(STUB_CFG_MAX, GFP_KERNEL);
	s->res = kzalloc(STUB_RES_MAX, GFP_KERNEL);
	s->wq = alloc_ordered_workqueue(KBUILD_MODNAME "_stub", 0);
	if (!s->cfg || !s->res || !s->wq)
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
	s->dir = debugfs_create_dir(KBUILD_MODNAME, NULL);
	debugfs_create_file("ctl", 0600, s->dir, s, &ctl_fops);
	the_stub = s;
	return 0;

err_pdev:
	platform_device_unregister(s->pdev);
err_free:
	if (s->wq)
		destroy_workqueue(s->wq);
	kfree(s->res);
	kfree(s->cfg);
	kfree(s);
	return ret;
}

void cudc_stub_exit(void)
{
	struct stub *s = the_stub;

	debugfs_remove_recursive(s->dir);
	cudc_destroy(s->u);	/* unbinds any gadget: pullup(0) -> stub_disconnect */
	platform_device_unregister(s->pdev);
	destroy_workqueue(s->wq);
	kfree(s->res);
	kfree(s->cfg);
	kfree(s);
}
