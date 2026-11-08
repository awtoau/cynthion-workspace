// SPDX-License-Identifier: GPL-2.0
/*
 * cudc_aux: the Cynthion over the AUX link (docs/aux-link.md) as cynthion_udc's backend.
 * - usb_driver bound to the bridge interface (class FF/43/01); URBs on the control channel and one
 *   pipe per TARGET endpoint.
 * - All link messages from the Cynthion run on one ordered workqueue (strict event order).
 * - Data requests are bounced through private buffers: a dequeued request never has DMA in flight.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/delay.h>
#include <linux/jiffies.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/unaligned.h>
#include <linux/usb.h>
#include <linux/workqueue.h>

#include "cudc.h"

/* Protocol constants: docs/aux-link.md sections 4-6. */
#define AUX_HDR			6
#define AUX_MAX_PAYLOAD		4096
#define AUX_RX_URBS		4
#define AUX_RX_BUF		4608	/* 9 x 512 and 72 x 64: holds a 4102-byte message */
#define AUX_NPIPES		4
#define AUX_CTL_NUM		3

enum {
	T_HELLO = 0x01, T_SETUP, T_CTRL_DATA_OUT, T_CTRL_DATA_IN, T_STATUS, T_RESET, T_BUS_STATE,
	T_DEV_CONFIG, T_WAKEUP, T_EP_CONFIG, T_EP_HALT, T_EP_CLEAR, T_EP_FLUSH, T_EP_DATA,
	T_ACK = 0x10, T_NAK, T_ERROR, T_GET_STATS, T_STATS,
};
enum { EV_SUSPEND = 1, EV_RESUME, EV_VBUS_ON, EV_VBUS_OFF, EV_ADDRESS, EV_FLAGS };
#define NAK_STALE	1

/*
 * Waits for: the HELLO reply after the HELLO command at probe. Expected ~1 ms (one AUX round trip),
 * limit 50 ms = the tightest EP0 deadline (docs/aux-link.md T_HELLO). Expiry: probe fails, logged.
 */
#define AUX_HELLO_MS		50
/*
 * Waits for: one command transfer on the control OUT pipe to complete (T_CMD): 50 ms, the tightest
 * EP0 deadline; a later command already failed its transfer. Expiry: link marked dead, logged.
 */
#define AUX_CMD_MS		50

#ifdef CUDC_MUTATE_ZLP
#define AUX_ZLP_FLAG		0	/* mutation: transfers that need a ZLP do not get one */
#else
#define AUX_ZLP_FLAG		URB_ZERO_PACKET
#endif

struct aux_tx {
	struct list_head	node;
	struct cudc_aux		*aux;
	struct urb		*urb;
	unsigned long		t0;
};

struct aux_xfer {
	struct list_head	node;
	struct urb		*urb;
	struct cudc_ep		*ep;
	struct cudc_request	*req;		/* NULL: dequeued/disabled, completion only frees */
	u8			*buf;
	unsigned int		len;
	bool			in;		/* urb IN: remote host -> PC */
};

struct aux_rx {
	struct cudc_aux	*aux;
	struct urb	*urb;
};

struct aux_msg {
	struct work_struct	work;
	struct cudc_aux		*aux;
	unsigned int		len;
	u8			data[];
};

struct cudc_aux {
	struct usb_interface	*intf;
	struct usb_device	*udev;
	struct cudc		*u;
	struct usb_anchor	anchor;
	struct workqueue_struct	*wq;
	struct work_struct	ctl_work;
	struct delayed_work	wd_work;
	struct completion	hello;
	struct aux_rx		rx[AUX_RX_URBS];
	unsigned int		ctl_in, ctl_out, ctl_maxp;
	unsigned int		pipe_maxp;

	spinlock_t		lock;		/* link state + lists; order: lock -> u->lock, lock -> txlock */
	spinlock_t		txlock;		/* control OUT submission order */
	struct list_head	tx, xfers[CUDC_NUM_EPS];
	bool			dead, connected, hello_ok, wake_en, selfpowered;
	u16			ctl_seq, rst_seq;
	u32			fifo_pkts;

	struct {				/* the control transfer in flight */
		bool		active, done, dir_in;
		u16		seq, wlen;
		u8		*out;
		unsigned int	out_len;
		ktime_t		t_setup;
	} ctl;
	u32			nak, stale_naks;
};


#define adev(a)	(&(a)->intf->dev)

/* ---- control channel TX ---- */

static void aux_tx_done(struct urb *urb)
{
	struct aux_tx *tx = urb->context;
	unsigned long flags;

	spin_lock_irqsave(&tx->aux->txlock, flags);
	list_del(&tx->node);
	spin_unlock_irqrestore(&tx->aux->txlock, flags);
	if (urb->status && urb->status != -ENOENT && urb->status != -ECONNRESET &&
	    urb->status != -ESHUTDOWN)
		dev_warn(adev(tx->aux), "command URB failed: %d\n", urb->status);
	kfree(urb->transfer_buffer);
	kfree(tx);
	usb_free_urb(urb);
}

static int aux_send(struct cudc_aux *aux, u8 type, u8 ep, u16 seq, const void *payload,
		    unsigned int len)
{
	struct aux_tx *tx;
	struct urb *urb;
	u8 *buf;
	unsigned long flags;
	int ret;

	if (READ_ONCE(aux->dead))
		return -ENODEV;
	tx = kzalloc(sizeof(*tx), GFP_ATOMIC);
	buf = kmalloc(AUX_HDR + len, GFP_ATOMIC);
	urb = usb_alloc_urb(0, GFP_ATOMIC);
	if (!tx || !buf || !urb) {
		ret = -ENOMEM;
		goto err;
	}
	buf[0] = type;
	buf[1] = ep;
	put_unaligned_le16(seq, buf + 2);
	put_unaligned_le16(len, buf + 4);
	if (len)
		memcpy(buf + AUX_HDR, payload, len);
	usb_fill_bulk_urb(urb, aux->udev, aux->ctl_out, buf, AUX_HDR + len, aux_tx_done, tx);
	if (!((AUX_HDR + len) % aux->ctl_maxp))
		urb->transfer_flags |= AUX_ZLP_FLAG;
	tx->aux = aux;
	tx->urb = urb;
	tx->t0 = jiffies;
	usb_anchor_urb(urb, &aux->anchor);
	spin_lock_irqsave(&aux->txlock, flags);
	list_add_tail(&tx->node, &aux->tx);
	ret = usb_submit_urb(urb, GFP_ATOMIC);
	if (ret)
		list_del(&tx->node);
	spin_unlock_irqrestore(&aux->txlock, flags);
	if (ret) {
		usb_unanchor_urb(urb);
		goto err;
	}
	schedule_delayed_work(&aux->wd_work, msecs_to_jiffies(AUX_CMD_MS));
	return 0;
err:
	kfree(buf);
	kfree(tx);
	usb_free_urb(urb);
	return ret;
}

/*
 * Command watchdog (T_CMD). Runs every AUX_CMD_MS while commands are in flight: a command still
 * pending after a whole period is older than the limit.
 */
static void aux_wd(struct work_struct *work)
{
	struct cudc_aux *aux = container_of(work, struct cudc_aux, wd_work.work);
	struct aux_tx *tx;
	unsigned long flags, age = 0;
	bool late = false, any = false;

	spin_lock_irqsave(&aux->txlock, flags);
	list_for_each_entry(tx, &aux->tx, node) {
		any = true;
		age = jiffies - tx->t0;
		if (age >= msecs_to_jiffies(AUX_CMD_MS)) {
			late = true;
			break;
		}
	}
	spin_unlock_irqrestore(&aux->txlock, flags);
	if (late && !READ_ONCE(aux->dead)) {
		dev_err(adev(aux), "command not completed within %u ms (elapsed %u ms): link dead\n",
			AUX_CMD_MS, jiffies_to_msecs(age));
		WRITE_ONCE(aux->dead, true);
		usb_kill_anchored_urbs(&aux->anchor);
	} else if (any && !READ_ONCE(aux->dead)) {
		schedule_delayed_work(&aux->wd_work, msecs_to_jiffies(AUX_CMD_MS));
	}
}

/* seq to put on a command: control epoch for EP0 replies, reset epoch for endpoint commands */
static u16 ctl_seq_for_reply(struct cudc_aux *aux)
{
#ifdef CUDC_MUTATE_SEQ
	return aux->ctl.seq + 1;
#else
	return aux->ctl.seq;
#endif
}

static u16 ep_seq(struct cudc_aux *aux)
{
#ifdef CUDC_MUTATE_STALE_EP
	return aux->ctl_seq;
#else
	return aux->rst_seq;
#endif
}

static int aux_status(struct cudc_aux *aux, u8 result)
{
	return aux_send(aux, T_STATUS, 0, ctl_seq_for_reply(aux), &result, 1);
}

/* ---- control transfers: SETUP, data, status ---- */

/* Runs on the ordered wq: moves EP0 requests along the current control transfer. */
static void aux_ctl_progress(struct work_struct *work)
{
	struct cudc_aux *aux = container_of(work, struct cudc_aux, ctl_work);
	struct cudc *u = aux->u;
	struct cudc_ep *ep0;
	struct cudc_request *req;
	unsigned long flags, aflags;
	unsigned int n;
	u8 flag;
	bool dir_in, active, done;
	u16 wlen;
	u8 *out;

	if (!u)
		return;
	ep0 = &u->ep[0];
	for (;;) {
		spin_lock_irqsave(&aux->lock, aflags);
		dir_in = aux->ctl.dir_in;
		active = aux->ctl.active;
		done = aux->ctl.done;
		wlen = aux->ctl.wlen;
		out = aux->ctl.out;
		spin_unlock_irqrestore(&aux->lock, aflags);

		spin_lock_irqsave(&u->lock, flags);
		req = cudc_next_request(ep0);
		if (!req) {
			spin_unlock_irqrestore(&u->lock, flags);
			return;
		}
		if (!active || done) {		/* answer to a transfer that no longer exists */
			cudc_take(req);
			spin_unlock_irqrestore(&u->lock, flags);
			cudc_giveback(ep0, req, -ECONNRESET);
			continue;
		}
		if (!wlen || (!dir_in && !req->req.length)) {	/* no data stage: this is the status */
			cudc_take(req);
			spin_unlock_irqrestore(&u->lock, flags);
			req->req.actual = 0;
			dev_dbg(adev(aux), "relay: status after %lld us\n",
				ktime_to_us(ktime_sub(ktime_get(), aux->ctl.t_setup)));
			spin_lock_irqsave(&aux->lock, aflags);
			aux->ctl.done = true;
			spin_unlock_irqrestore(&aux->lock, aflags);
			aux_status(aux, 0);
			cudc_giveback(ep0, req, 0);
			continue;
		}
		if (dir_in) {
			n = min_t(unsigned int, req->req.length, wlen);
			flag = req->req.zero ? 1 : 0;
			cudc_take(req);
			spin_unlock_irqrestore(&u->lock, flags);
			dev_dbg(adev(aux), "relay: data reply %u B after %lld us\n", n,
				ktime_to_us(ktime_sub(ktime_get(), aux->ctl.t_setup)));
			if (n && !(n % CUDC_EP0_MAXPACKET) && n < wlen && !flag)
				dev_warn(adev(aux), "EP0 reply of %u of %u bytes without req->zero\n", n, wlen);
			aux_send(aux, T_CTRL_DATA_IN, flag, ctl_seq_for_reply(aux), req->req.buf, n);
			req->req.actual = n;
			spin_lock_irqsave(&aux->lock, aflags);
			aux->ctl.done = true;
			spin_unlock_irqrestore(&aux->lock, aflags);
			cudc_giveback(ep0, req, 0);
			continue;
		}
		/* OUT data stage: needs the data from the Cynthion */
		if (!out) {
			spin_unlock_irqrestore(&u->lock, flags);
			return;
		}
		n = min_t(unsigned int, req->req.length, aux->ctl.out_len);
		memcpy(req->req.buf, out, n);
		req->req.actual = n;
		cudc_take(req);
		spin_unlock_irqrestore(&u->lock, flags);
		spin_lock_irqsave(&aux->lock, aflags);
		kfree(aux->ctl.out);
		aux->ctl.out = NULL;
		spin_unlock_irqrestore(&aux->lock, aflags);
		cudc_giveback(ep0, req, 0);
		/* the completion may have halted EP0 (set_halt sets done and sent STATUS(STALL)) */
		spin_lock_irqsave(&aux->lock, aflags);
		done = aux->ctl.done;
		aux->ctl.done = true;
		spin_unlock_irqrestore(&aux->lock, aflags);
		if (!done)
			aux_status(aux, 0);
	}
}

static void aux_on_setup(struct cudc_aux *aux, u16 seq, const u8 *p, unsigned int len)
{
	struct usb_ctrlrequest ctrl;
	unsigned long flags;
	int ret;

	if (len < 8)
		return;
	memcpy(&ctrl, p, 8);
	spin_lock_irqsave(&aux->lock, flags);
	aux->ctl_seq = seq;
	aux->ctl.seq = seq;
	aux->ctl.active = true;
	aux->ctl.done = false;
	aux->ctl.dir_in = ctrl.bRequestType & USB_DIR_IN;
	aux->ctl.wlen = le16_to_cpu(ctrl.wLength);
	aux->ctl.t_setup = ktime_get();
	kfree(aux->ctl.out);
	aux->ctl.out = NULL;
	spin_unlock_irqrestore(&aux->lock, flags);

	ret = cudc_setup(aux->u, &ctrl);
	if (ret < 0) {
		spin_lock_irqsave(&aux->lock, flags);
		ret = aux->ctl.done;
		aux->ctl.done = true;
		spin_unlock_irqrestore(&aux->lock, flags);
		if (!ret)
			aux_status(aux, 1);
	}
}

static void aux_on_ctrl_out(struct cudc_aux *aux, u16 seq, const u8 *p, unsigned int len)
{
	unsigned long flags;
	u8 *copy = kmemdup(p, len ? len : 1, GFP_KERNEL);

	spin_lock_irqsave(&aux->lock, flags);
	if (!copy || seq != aux->ctl.seq || !aux->ctl.active || aux->ctl.dir_in) {
		spin_unlock_irqrestore(&aux->lock, flags);
		kfree(copy);
		return;
	}
	kfree(aux->ctl.out);
	aux->ctl.out = copy;
	aux->ctl.out_len = len;
	spin_unlock_irqrestore(&aux->lock, flags);
	aux_ctl_progress(&aux->ctl_work);
}

/* ---- data pipes ---- */

static unsigned int aux_pipe_of(struct cudc_aux *aux, struct cudc_ep *ep)
{
	unsigned int num = (ep->addr & 0x7f) + 3;

	return (ep->addr & USB_DIR_IN) ? usb_sndbulkpipe(aux->udev, num) : usb_rcvbulkpipe(aux->udev, num);
}

static void aux_xfer_done(struct urb *urb)
{
	struct aux_xfer *x = urb->context;
	struct cudc_aux *aux = x->ep->udc->backend;
	struct cudc *u = aux->u;
	struct cudc_request *req;
	unsigned long flags, aflags;
	int status = urb->status;
	unsigned int n = urb->actual_length;

	spin_lock_irqsave(&aux->lock, aflags);
	req = x->req;
	list_del(&x->node);
	spin_unlock_irqrestore(&aux->lock, aflags);

	if (req) {
		spin_lock_irqsave(&u->lock, flags);
		if (list_empty(&req->queue)) {		/* dequeued meanwhile: core gave it back */
			req = NULL;
		} else {
			cudc_take(req);
			if (status == -ENOENT || status == -ECONNRESET || status == -ESHUTDOWN)
				status = -ESHUTDOWN;
			else if (status)
				status = -EPROTO;
			if (!status && x->in) {
				if (n > req->req.length) {
					status = -EOVERFLOW;
					n = req->req.length;
				}
				memcpy(req->req.buf, x->buf, n);
				req->req.actual = n;
			} else if (!status) {
				req->req.actual = req->req.length;
			}
		}
		spin_unlock_irqrestore(&u->lock, flags);
		if (req)
			cudc_giveback(x->ep, req, status);
	}
	kfree(x->buf);
	usb_free_urb(urb);
	kfree(x);
}

/* Submits every not yet sent request of the endpoint, in queue order. Atomic. */
static void aux_queue(struct cudc *u, struct cudc_ep *ep)
{
	struct cudc_aux *aux = u->backend;
	struct cudc_request *req;
	struct aux_xfer *x;
	unsigned long flags, aflags;
	unsigned int len, maxp;
	bool in = !(ep->addr & USB_DIR_IN);	/* remote host -> PC */
	int ret;

	spin_lock_irqsave(&aux->lock, aflags);
	if (aux->dead) {
		spin_unlock_irqrestore(&aux->lock, aflags);
		return;
	}
	spin_lock_irqsave(&u->lock, flags);
	list_for_each_entry(req, &ep->queue, queue) {
		if (req->sent)
			continue;
		maxp = ep->ep.maxpacket;
		len = req->req.length;
		if (in)
			len = max(roundup(len, maxp), maxp);
		x = kzalloc(sizeof(*x), GFP_ATOMIC);
		if (x) {
			x->buf = kmalloc(len ? len : 1, GFP_ATOMIC);
			x->urb = usb_alloc_urb(0, GFP_ATOMIC);
		}
		if (!x || !x->buf || !x->urb) {
			if (x) {
				kfree(x->buf);
				usb_free_urb(x->urb);
			}
			kfree(x);
			break;			/* retried by the next queue() */
		}
		x->ep = ep;
		x->req = req;
		x->len = len;
		x->in = in;
		if (!in && len)
			memcpy(x->buf, req->req.buf, len);
		usb_fill_bulk_urb(x->urb, aux->udev, aux_pipe_of(aux, ep), x->buf, len, aux_xfer_done, x);
		if (!in && req->req.zero)
			x->urb->transfer_flags |= AUX_ZLP_FLAG;
		if (!in && !len)
			x->urb->transfer_flags |= URB_ZERO_PACKET;	/* explicit ZLP request */
		req->sent = true;
		list_add_tail(&x->node, &aux->xfers[ep - u->ep]);
		usb_anchor_urb(x->urb, &aux->anchor);
		ret = usb_submit_urb(x->urb, GFP_ATOMIC);
		if (ret) {
			usb_unanchor_urb(x->urb);
			list_del(&x->node);
			x->req = NULL;
			dev_warn(adev(aux), "%s: submit failed: %d\n", ep->ep.name, ret);
			kfree(x->buf);
			usb_free_urb(x->urb);
			kfree(x);
		}
	}
	spin_unlock_irqrestore(&u->lock, flags);
	spin_unlock_irqrestore(&aux->lock, aflags);
}

/* Detaches every in-flight URB of an endpoint from its request and unlinks it (async). */
static void aux_abort_ep(struct cudc_aux *aux, int idx)
{
	struct aux_xfer *x;
	unsigned long flags;

	spin_lock_irqsave(&aux->lock, flags);
	list_for_each_entry(x, &aux->xfers[idx], node) {
		x->req = NULL;
		usb_unlink_urb(x->urb);
	}
	spin_unlock_irqrestore(&aux->lock, flags);
}

/* ---- backend ops ---- */

static int aux_connect(struct cudc *u)
{
	struct cudc_aux *aux = u->backend;
	u8 p[4] = { 1, 2, u->gadget.is_selfpowered, 0 };

	if (READ_ONCE(aux->dead))
		return -ENODEV;
	WRITE_ONCE(aux->connected, true);
	return aux_send(aux, T_DEV_CONFIG, 0, 0, p, sizeof(p));
}

static void aux_selfpowered(struct cudc *u, bool on)
{
	struct cudc_aux *aux = u->backend;
	u8 p[4] = { 1, 2, on, 0 };

	if (READ_ONCE(aux->connected))
		aux_send(aux, T_DEV_CONFIG, 0, 0, p, sizeof(p));
}

static void aux_disconnect(struct cudc *u)
{
	struct cudc_aux *aux = u->backend;
	u8 p[4] = { 0, 2, 0, 0 };
	unsigned long flags;
	int i;

	WRITE_ONCE(aux->connected, false);
	aux_send(aux, T_DEV_CONFIG, 0, 0, p, sizeof(p));
	for (i = 0; i < CUDC_NUM_EPS; i++)
		aux_abort_ep(aux, i);
	spin_lock_irqsave(&aux->lock, flags);
	aux->ctl.active = false;
	kfree(aux->ctl.out);
	aux->ctl.out = NULL;
	spin_unlock_irqrestore(&aux->lock, flags);
	flush_workqueue(aux->wq);	/* no event handler runs after this returns */
}

static int aux_ep_enable(struct cudc *u, struct cudc_ep *ep, const struct usb_endpoint_descriptor *desc)
{
	struct cudc_aux *aux = u->backend;
	u8 p[4] = { 1, usb_endpoint_xfer_int(desc) ? 3 : 2, 0, 0 };

	put_unaligned_le16(usb_endpoint_maxp(desc), p + 2);
	return aux_send(aux, T_EP_CONFIG, ep->addr, ep_seq(aux), p, sizeof(p));
}

static void aux_ep_disable(struct cudc *u, struct cudc_ep *ep)
{
	struct cudc_aux *aux = u->backend;
	u8 p[4] = { 0, 0, 0, 0 };

	aux_abort_ep(aux, ep - u->ep);
	aux_send(aux, T_EP_CONFIG, ep->addr, ep_seq(aux), p, sizeof(p));
}

static void aux_dequeue(struct cudc *u, struct cudc_ep *ep, struct cudc_request *req)
{
	struct cudc_aux *aux = u->backend;
	struct aux_xfer *x;
	unsigned long flags;
	u8 discard = ep->addr & USB_DIR_IN ? 1 : 0;

	if (ep == &u->ep[0])
		return;
	spin_lock_irqsave(&aux->lock, flags);
	list_for_each_entry(x, &aux->xfers[ep - u->ep], node) {
		if (x->req == req) {
			x->req = NULL;
			usb_unlink_urb(x->urb);
		}
	}
	spin_unlock_irqrestore(&aux->lock, flags);
#ifndef CUDC_MUTATE_FLUSH
	aux_send(aux, T_EP_FLUSH, ep->addr, ep_seq(aux), &discard, 1);
#endif
}

static int aux_set_halt(struct cudc *u, struct cudc_ep *ep, bool halt, bool wedge)
{
	struct cudc_aux *aux = u->backend;
	unsigned long flags;
	u8 flag = wedge;
	bool send;

	if (ep == &u->ep[0]) {			/* STALL the control transfer in flight */
		if (!halt)
			return 0;
		spin_lock_irqsave(&aux->lock, flags);
		send = aux->ctl.active && !aux->ctl.done;
		aux->ctl.done = true;
		spin_unlock_irqrestore(&aux->lock, flags);
		return send ? aux_status(aux, 1) : 0;
	}
	if (halt)
		return aux_send(aux, T_EP_HALT, ep->addr, ep_seq(aux), &flag, 1);
	return aux_send(aux, T_EP_CLEAR, ep->addr, ep_seq(aux), NULL, 0);
}

static void aux_setup_reply(struct cudc *u)
{
	struct cudc_aux *aux = u->backend;

	queue_work(aux->wq, &aux->ctl_work);
}

static int aux_wakeup(struct cudc *u)
{
	struct cudc_aux *aux = u->backend;

	aux_send(aux, T_WAKEUP, 0, 0, NULL, 0);	/* the Cynthion counts a refusal too */
	return READ_ONCE(aux->wake_en) ? 0 : -EINVAL;
}

static const struct cudc_backend_ops aux_ops = {
	.name		= "aux",
	.connect	= aux_connect,
	.disconnect	= aux_disconnect,
	.ep_enable	= aux_ep_enable,
	.ep_disable	= aux_ep_disable,
	.queue		= aux_queue,
	.dequeue	= aux_dequeue,
	.set_halt	= aux_set_halt,
	.setup_reply	= aux_setup_reply,
	.wakeup		= aux_wakeup,
	.selfpowered	= aux_selfpowered,
};

/* ---- control channel RX ---- */

static void aux_on_message(struct cudc_aux *aux, const u8 *d, unsigned int total)
{
	u8 type = d[0], ep = d[1];
	u16 seq = get_unaligned_le16(d + 2), len = get_unaligned_le16(d + 4);
	const u8 *p = d + AUX_HDR;
	unsigned long flags;
	struct cudc *u = aux->u;

	if (total != AUX_HDR + len) {
		dev_warn(adev(aux), "message type %02x: len %u but %u bytes\n", type, len, total);
		return;
	}
	if (type == T_HELLO) {
		if (len >= 8 && p[0] == 1) {
			aux->pipe_maxp = p[1] == 2 ? 512 : 64;
			aux->fifo_pkts = len >= 10 ? get_unaligned_le16(p + 8) : 0;
			aux->hello_ok = true;
		}
		complete(&aux->hello);
		return;
	}
	if (!u || READ_ONCE(aux->dead))
		return;
	switch (type) {
	case T_ACK:
		dev_dbg(adev(aux), "ACK type %02x ep %02x\n", p[0], p[1]);
		break;
	case T_NAK:
		aux->nak++;
		if (len >= 5 && p[4] == NAK_STALE) {
			aux->stale_naks++;
			dev_dbg(adev(aux), "NAK STALE for type %02x ep %02x seq %u\n", p[0], p[1],
				get_unaligned_le16(p + 2));
		} else if (len >= 5) {
			dev_warn(adev(aux), "NAK type %02x ep %02x seq %u code %u\n", p[0], p[1],
				 get_unaligned_le16(p + 2), p[4]);
		}
		break;
	case T_ERROR:
		dev_warn(adev(aux), "ERROR code %u detail %u\n", len > 0 ? p[0] : 0, len > 1 ? p[1] : 0);
		break;
	case T_STATS:
		break;
	case T_BUS_STATE:
		if (len >= 4) {
			WRITE_ONCE(aux->wake_en, p[2] & 1);
			if (p[0] == EV_VBUS_ON || p[0] == EV_VBUS_OFF) {	/* also while detached */
				cudc_vbus(u, p[0] == EV_VBUS_ON);
				break;
			}
			if (!READ_ONCE(aux->connected))
				break;
			switch (p[0]) {
			case EV_SUSPEND:
				cudc_suspend(u);
				break;
			case EV_RESUME:
				cudc_resume(u);
				break;
			case EV_ADDRESS:
				cudc_set_address(u, p[1]);
				break;
			}
		}
		break;
	case T_RESET: {
		int i;

		if (!READ_ONCE(aux->connected) || !len)
			break;
		spin_lock_irqsave(&aux->lock, flags);
		aux->rst_seq = seq;
		aux->ctl_seq = seq;
		aux->ctl.active = false;
		kfree(aux->ctl.out);
		aux->ctl.out = NULL;
		spin_unlock_irqrestore(&aux->lock, flags);
		for (i = 1; i < CUDC_NUM_EPS; i++)
			aux_abort_ep(aux, i);
		cudc_bus_reset(u, p[0] == 1 ? USB_SPEED_FULL : USB_SPEED_HIGH);
		break;
	}
	case T_SETUP:
		if (READ_ONCE(aux->connected))
			aux_on_setup(aux, seq, p, len);
		break;
	case T_CTRL_DATA_OUT:
		if (READ_ONCE(aux->connected))
			aux_on_ctrl_out(aux, seq, p, len);
		break;
	case T_EP_HALT:
		cudc_ep_halted(u, ep, true);
		break;
	case T_EP_CLEAR:
		cudc_ep_halted(u, ep, false);
		break;
	default:
		dev_dbg(adev(aux), "ignoring message type %02x\n", type);
	}
}

static void aux_msg_work(struct work_struct *work)
{
	struct aux_msg *m = container_of(work, struct aux_msg, work);

	aux_on_message(m->aux, m->data, m->len);
	kfree(m);
}

/* Completion: copy out and resubmit at once; the ordered wq keeps message order. */
static void aux_rx_done(struct urb *urb)
{
	struct aux_rx *rx = urb->context;
	struct cudc_aux *aux = rx->aux;
	struct aux_msg *m;

	switch (urb->status) {
	case 0:
		break;
	case -ENOENT:
	case -ECONNRESET:
	case -ESHUTDOWN:
		return;
	default:
		dev_warn(adev(aux), "control IN URB error %d\n", urb->status);
		goto resubmit;
	}
	if (urb->actual_length < AUX_HDR) {
		dev_warn(adev(aux), "short message (%u B)\n", urb->actual_length);
		goto resubmit;
	}
	m = kmalloc(sizeof(*m) + urb->actual_length, GFP_ATOMIC);
	if (m) {
		INIT_WORK(&m->work, aux_msg_work);
		m->aux = aux;
		m->len = urb->actual_length;
		memcpy(m->data, urb->transfer_buffer, m->len);
		queue_work(aux->wq, &m->work);
	}
resubmit:
	if (!READ_ONCE(aux->dead) && usb_submit_urb(urb, GFP_ATOMIC))
		dev_warn(adev(aux), "control IN resubmit failed\n");
}

/* ---- usb_driver ---- */

static int aux_find_endpoints(struct cudc_aux *aux, struct usb_host_interface *alt)
{
	int i, found = 0;

	for (i = 0; i < alt->desc.bNumEndpoints; i++) {
		struct usb_endpoint_descriptor *e = &alt->endpoint[i].desc;
		unsigned int num = usb_endpoint_num(e);

		if (!usb_endpoint_xfer_bulk(e) || num < AUX_CTL_NUM || num > AUX_CTL_NUM + AUX_NPIPES)
			continue;
		if (num == AUX_CTL_NUM) {
			if (usb_endpoint_dir_in(e))
				aux->ctl_in = usb_rcvbulkpipe(aux->udev, num);
			else {
				aux->ctl_out = usb_sndbulkpipe(aux->udev, num);
				aux->ctl_maxp = usb_endpoint_maxp(e);
			}
		}
		found++;
	}
	return found == 2 * (1 + AUX_NPIPES) && aux->ctl_in && aux->ctl_out ? 0 : -ENODEV;
}

static int aux_probe(struct usb_interface *intf, const struct usb_device_id *id)
{
	struct cudc_aux *aux;
	u8 hello[4] = { 1, 2, 0, 0 };
	int i, ret;

	aux = kzalloc(sizeof(*aux), GFP_KERNEL);
	if (!aux)
		return -ENOMEM;
	aux->intf = intf;
	aux->udev = interface_to_usbdev(intf);
	spin_lock_init(&aux->lock);
	spin_lock_init(&aux->txlock);
	INIT_LIST_HEAD(&aux->tx);
	for (i = 0; i < CUDC_NUM_EPS; i++)
		INIT_LIST_HEAD(&aux->xfers[i]);
	init_usb_anchor(&aux->anchor);
	init_completion(&aux->hello);
	INIT_WORK(&aux->ctl_work, aux_ctl_progress);
	INIT_DELAYED_WORK(&aux->wd_work, aux_wd);
	ret = aux_find_endpoints(aux, intf->cur_altsetting);
	if (ret) {
		dev_err(&intf->dev, "interface does not carry the AUX link endpoint set\n");
		goto err_free;
	}
	aux->wq = alloc_ordered_workqueue("cudc_aux", 0);
	if (!aux->wq) {
		ret = -ENOMEM;
		goto err_free;
	}
	usb_set_intfdata(intf, aux);

	for (i = 0; i < AUX_RX_URBS; i++) {
		struct aux_rx *rx = &aux->rx[i];
		void *buf = kmalloc(AUX_RX_BUF, GFP_KERNEL);

		rx->aux = aux;
		rx->urb = usb_alloc_urb(0, GFP_KERNEL);
		if (!buf || !rx->urb) {
			kfree(buf);
			ret = -ENOMEM;
			goto err_urbs;
		}
		usb_fill_bulk_urb(rx->urb, aux->udev, aux->ctl_in, buf, AUX_RX_BUF, aux_rx_done, rx);
		rx->urb->transfer_flags |= URB_FREE_BUFFER;
		usb_anchor_urb(rx->urb, &aux->anchor);
		ret = usb_submit_urb(rx->urb, GFP_KERNEL);
		if (ret) {
			usb_unanchor_urb(rx->urb);
			goto err_urbs;
		}
	}

	ret = aux_send(aux, T_HELLO, 0, 0, hello, sizeof(hello));
	if (ret)
		goto err_urbs;
	if (!wait_for_completion_timeout(&aux->hello, msecs_to_jiffies(AUX_HELLO_MS)) ||
	    !aux->hello_ok) {
		dev_err(&intf->dev, "no valid HELLO reply within %u ms\n", AUX_HELLO_MS);
		ret = -ETIMEDOUT;
		goto err_urbs;
	}
	aux->u = cudc_create(&intf->dev, &aux_ops, aux);
	if (IS_ERR(aux->u)) {
		ret = PTR_ERR(aux->u);
		aux->u = NULL;
		goto err_urbs;
	}
	dev_info(&intf->dev, "AUX link up: pipe maxp %u, FIFO %u packets\n", aux->pipe_maxp,
		 aux->fifo_pkts);
	return 0;

err_urbs:
	WRITE_ONCE(aux->dead, true);
	usb_kill_anchored_urbs(&aux->anchor);
	for (i = 0; i < AUX_RX_URBS; i++)
		usb_free_urb(aux->rx[i].urb);
	destroy_workqueue(aux->wq);
err_free:
	kfree(aux);
	return ret;
}

static void aux_disconnect_usb(struct usb_interface *intf)
{
	struct cudc_aux *aux = usb_get_intfdata(intf);
	int i;

	cudc_destroy(aux->u);			/* unbinds the gadget: pullup(0) -> aux_disconnect */
	WRITE_ONCE(aux->dead, true);
	cancel_delayed_work_sync(&aux->wd_work);
	usb_kill_anchored_urbs(&aux->anchor);
	flush_workqueue(aux->wq);
	destroy_workqueue(aux->wq);
	for (i = 0; i < AUX_RX_URBS; i++)
		usb_free_urb(aux->rx[i].urb);
	kfree(aux->ctl.out);
	kfree(aux);
}

static const struct usb_device_id aux_ids[] = {
	{ USB_DEVICE_AND_INTERFACE_INFO(0x1d50, 0x6189, 0xff, 0x43, 0x01) },
	{ }
};
MODULE_DEVICE_TABLE(usb, aux_ids);

static struct usb_driver aux_driver = {
	.name		= "cynthion_udc_aux",
	.id_table	= aux_ids,
	.probe		= aux_probe,
	.disconnect	= aux_disconnect_usb,
};

int cudc_aux_init(void)
{
	return usb_register(&aux_driver);
}

void cudc_aux_exit(void)
{
	usb_deregister(&aux_driver);
}
