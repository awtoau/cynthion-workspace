// SPDX-License-Identifier: GPL-2.0
/*
 * cynthion_udc core: a UDC for udc-core whose hardware is a backend (cudc.h).
 * Structure after drivers/usb/gadget/udc/dummy_hcd.c (gadget side) and max3420_udc.c.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/slab.h>
#include <linux/usb/ch9.h>
#include <linux/usb/gadget.h>

#include "cudc.h"

static const char *const ep_names[CUDC_NUM_EPS] = {
	"ep0", "ep1in", "ep2in", "ep3in", "ep4in", "ep1out", "ep2out", "ep3out", "ep4out",
};

#define udc_dev(u)	(&(u)->gadget.dev)

static struct cudc_ep *to_cudc_ep(struct usb_ep *ep)
{
	return container_of(ep, struct cudc_ep, ep);
}

static bool is_ep0(const struct cudc_ep *ep)
{
	return ep == &ep->udc->ep[0];
}

/* ---- request queue ---- */

struct cudc_request *cudc_next_request(struct cudc_ep *ep)
{
	lockdep_assert_held(&ep->udc->lock);
	return list_first_entry_or_null(&ep->queue, struct cudc_request, queue);
}

void cudc_take(struct cudc_request *req)
{
	list_del_init(&req->queue);
}

/* Gives back with IRQs off: gadget completions expect hardware-IRQ context. */
void cudc_giveback(struct cudc_ep *ep, struct cudc_request *req, int status)
{
	unsigned long flags;

	if (req->req.status == -EINPROGRESS)
		req->req.status = status;
	dev_dbg(udc_dev(ep->udc), "giveback %s req %p len %u actual %u status %d\n",
		ep->ep.name, req, req->req.length, req->req.actual, req->req.status);
	local_irq_save(flags);
	usb_gadget_giveback_request(&ep->ep, &req->req);
	local_irq_restore(flags);
}

void cudc_nuke(struct cudc_ep *ep, int status)
{
	struct cudc *u = ep->udc;
	struct cudc_request *req;
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	while ((req = cudc_next_request(ep))) {
		cudc_take(req);
		spin_unlock_irqrestore(&u->lock, flags);
		cudc_giveback(ep, req, status);
		spin_lock_irqsave(&u->lock, flags);
	}
	spin_unlock_irqrestore(&u->lock, flags);
}

static void nuke_all(struct cudc *u, int status)
{
	int i;

	for (i = 0; i < CUDC_NUM_EPS; i++)
		cudc_nuke(&u->ep[i], status);
}

struct cudc_ep *cudc_ep_by_addr(struct cudc *u, u8 addr)
{
	int i;

	for (i = 1; i < CUDC_NUM_EPS; i++)
		if (u->ep[i].addr == addr)
			return &u->ep[i];
	return NULL;
}

/* ---- usb_ep_ops ---- */

static int cudc_ep_enable(struct usb_ep *_ep, const struct usb_endpoint_descriptor *desc)
{
	struct cudc_ep *ep = to_cudc_ep(_ep);
	struct cudc *u = ep->udc;
	unsigned int max = usb_endpoint_maxp(desc);
	unsigned long flags;
	int ret;

	if (is_ep0(ep) || !desc || desc->bDescriptorType != USB_DT_ENDPOINT ||
	    desc->bEndpointAddress != ep->addr || !max)
		return -EINVAL;
	if (!u->driver || u->gadget.speed == USB_SPEED_UNKNOWN)
		return -ESHUTDOWN;

	switch (usb_endpoint_type(desc)) {
	case USB_ENDPOINT_XFER_BULK:
		if (u->gadget.speed == USB_SPEED_HIGH ? max != 512 : max > 64)
			return -EINVAL;
		break;
	case USB_ENDPOINT_XFER_INT:
		/* one AUX packet per TARGET packet: no high-bandwidth interrupt */
		if (max > CUDC_EPN_MAXPACKET || usb_endpoint_maxp_mult(desc) > 1)
			return -EINVAL;
		break;
	default:
		return -EINVAL;	/* iso: later (docs/usb-device-mode.md, endpoint limits) */
	}

	spin_lock_irqsave(&u->lock, flags);
	ep->desc = desc;
	ep->ep.maxpacket = max;
	ep->halted = ep->wedged = false;
	spin_unlock_irqrestore(&u->lock, flags);

	ret = u->ops->ep_enable(u, ep, desc);
	if (ret) {
		spin_lock_irqsave(&u->lock, flags);
		ep->desc = NULL;
		spin_unlock_irqrestore(&u->lock, flags);
	}
	dev_dbg(udc_dev(u), "enable %s type %s maxpacket %u: %d\n", _ep->name,
		usb_ep_type_string(usb_endpoint_type(desc)), max, ret);
	return ret;
}

static int cudc_ep_disable(struct usb_ep *_ep)
{
	struct cudc_ep *ep = to_cudc_ep(_ep);
	struct cudc *u = ep->udc;
	unsigned long flags;

	if (is_ep0(ep) || !ep->desc)
		return -EINVAL;
	spin_lock_irqsave(&u->lock, flags);
	ep->desc = NULL;
	spin_unlock_irqrestore(&u->lock, flags);
	u->ops->ep_disable(u, ep);
	cudc_nuke(ep, -ESHUTDOWN);
	dev_dbg(udc_dev(u), "disable %s\n", _ep->name);
	return 0;
}

static struct usb_request *cudc_alloc_request(struct usb_ep *_ep, gfp_t gfp)
{
	struct cudc_request *req = kzalloc(sizeof(*req), gfp);

	if (!req)
		return NULL;
	INIT_LIST_HEAD(&req->queue);
	return &req->req;
}

static void cudc_free_request(struct usb_ep *_ep, struct usb_request *_req)
{
	struct cudc_request *req = to_cudc_req(_req);

	WARN_ON(!list_empty(&req->queue));
	kfree(req);
}

static int cudc_queue(struct usb_ep *_ep, struct usb_request *_req, gfp_t gfp)
{
	struct cudc_ep *ep = to_cudc_ep(_ep);
	struct cudc_request *req = to_cudc_req(_req);
	struct cudc *u = ep->udc;
	unsigned long flags;

	if (!_req->complete || (!_req->buf && _req->length) || !list_empty(&req->queue))
		return -EINVAL;

	spin_lock_irqsave(&u->lock, flags);
	if (!u->driver || !u->pulled_up || (!is_ep0(ep) && !ep->desc)) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -ESHUTDOWN;
	}
	_req->status = -EINPROGRESS;
	_req->actual = 0;
	req->sent = false;
	list_add_tail(&req->queue, &ep->queue);
	spin_unlock_irqrestore(&u->lock, flags);

	dev_dbg(udc_dev(u), "queue %s req %p len %u zero %u\n", _ep->name, req,
		_req->length, _req->zero);
	if (is_ep0(ep))
		u->ops->setup_reply(u);
	else
		u->ops->queue(u, ep);
	return 0;
}

static int cudc_dequeue(struct usb_ep *_ep, struct usb_request *_req)
{
	struct cudc_ep *ep = to_cudc_ep(_ep);
	struct cudc *u = ep->udc;
	struct cudc_request *req = NULL, *iter;
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	list_for_each_entry(iter, &ep->queue, queue) {
		if (&iter->req == _req) {
			req = iter;
			cudc_take(req);
			break;
		}
	}
	spin_unlock_irqrestore(&u->lock, flags);
	if (!req)
		return -EINVAL;

	u->ops->dequeue(u, ep, req);
	cudc_giveback(ep, req, -ECONNRESET);
	return 0;
}

static int cudc_halt(struct usb_ep *_ep, int value, bool wedge)
{
	struct cudc_ep *ep = to_cudc_ep(_ep);
	struct cudc *u = ep->udc;
	unsigned long flags;
	int ret;

	if (!u->driver)
		return -ESHUTDOWN;
	spin_lock_irqsave(&u->lock, flags);
	if (value && !is_ep0(ep) && usb_endpoint_dir_in(ep->desc) && !list_empty(&ep->queue)) {
		spin_unlock_irqrestore(&u->lock, flags);
		return -EAGAIN;
	}
	spin_unlock_irqrestore(&u->lock, flags);

	ret = u->ops->set_halt(u, ep, value, wedge);
	if (ret)
		return ret;
	spin_lock_irqsave(&u->lock, flags);
	ep->halted = value && !is_ep0(ep);
	ep->wedged = ep->halted && wedge;
	spin_unlock_irqrestore(&u->lock, flags);
	dev_dbg(udc_dev(u), "%s %s\n", value ? (wedge ? "wedge" : "halt") : "clear halt", _ep->name);
	return 0;
}

static int cudc_set_halt(struct usb_ep *_ep, int value)
{
	return cudc_halt(_ep, value, false);
}

static int cudc_set_wedge(struct usb_ep *_ep)
{
	if (is_ep0(to_cudc_ep(_ep)))
		return -EINVAL;
	return cudc_halt(_ep, 1, true);
}

static const struct usb_ep_ops cudc_ep_ops = {
	.enable		= cudc_ep_enable,
	.disable	= cudc_ep_disable,
	.alloc_request	= cudc_alloc_request,
	.free_request	= cudc_free_request,
	.queue		= cudc_queue,
	.dequeue	= cudc_dequeue,
	.set_halt	= cudc_set_halt,
	.set_wedge	= cudc_set_wedge,
};

/* ---- usb_gadget_ops ---- */

static struct cudc *gadget_to_cudc(struct usb_gadget *g)
{
	return container_of(g, struct cudc, gadget);
}

static int cudc_wakeup(struct usb_gadget *g)
{
	struct cudc *u = gadget_to_cudc(g);

	return u->ops->wakeup ? u->ops->wakeup(u) : -EOPNOTSUPP;
}

static int cudc_get_frame(struct usb_gadget *g)
{
	return -EOPNOTSUPP;
}

static int cudc_set_selfpowered(struct usb_gadget *g, int value)
{
	struct cudc *u = gadget_to_cudc(g);

	g->is_selfpowered = !!value;
	if (value)
		u->devstatus |= BIT(USB_DEVICE_SELF_POWERED);
	else
		u->devstatus &= ~BIT(USB_DEVICE_SELF_POWERED);
	if (u->ops->selfpowered)
		u->ops->selfpowered(u, !!value);
	return 0;
}

static int cudc_pullup(struct usb_gadget *g, int on)
{
	struct cudc *u = gadget_to_cudc(g);
	unsigned long flags;

	dev_dbg(udc_dev(u), "pullup %d\n", on);
	spin_lock_irqsave(&u->lock, flags);
	u->pulled_up = on;
	spin_unlock_irqrestore(&u->lock, flags);

	if (on)
		return u->ops->connect(u);

	u->ops->disconnect(u);	/* backend stops its callbacks before returning */
	nuke_all(u, -ESHUTDOWN);
	u->gadget.speed = USB_SPEED_UNKNOWN;
	usb_gadget_set_state(g, USB_STATE_NOTATTACHED);
	return 0;
}

static int cudc_udc_start(struct usb_gadget *g, struct usb_gadget_driver *driver)
{
	struct cudc *u = gadget_to_cudc(g);
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	u->driver = driver;
	u->devstatus &= BIT(USB_DEVICE_SELF_POWERED);
	spin_unlock_irqrestore(&u->lock, flags);
	dev_info(udc_dev(u), "bound gadget driver %s\n", driver->function);
	return 0;
}

static int cudc_udc_stop(struct usb_gadget *g)
{
	struct cudc *u = gadget_to_cudc(g);
	unsigned long flags;

	nuke_all(u, -ESHUTDOWN);
	spin_lock_irqsave(&u->lock, flags);
	u->driver = NULL;
	u->callbacks = false;
	spin_unlock_irqrestore(&u->lock, flags);
	dev_info(udc_dev(u), "gadget driver unbound\n");
	return 0;
}

/* udc-core calls this after pullup(0), so the backend has already flushed its callbacks. */
static void cudc_udc_async_callbacks(struct usb_gadget *g, bool enable)
{
	struct cudc *u = gadget_to_cudc(g);
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	u->callbacks = enable;
	spin_unlock_irqrestore(&u->lock, flags);
}

static const struct usb_gadget_ops cudc_gadget_ops = {
	.get_frame		= cudc_get_frame,
	.wakeup			= cudc_wakeup,
	.set_selfpowered	= cudc_set_selfpowered,
	.pullup			= cudc_pullup,
	.udc_start		= cudc_udc_start,
	.udc_stop		= cudc_udc_stop,
	.udc_async_callbacks	= cudc_udc_async_callbacks,
};

/* ---- events from the backend ---- */

static struct usb_gadget_driver *callback_driver(struct cudc *u)
{
	struct usb_gadget_driver *d;
	unsigned long flags;

	spin_lock_irqsave(&u->lock, flags);
	d = u->callbacks ? u->driver : NULL;
	spin_unlock_irqrestore(&u->lock, flags);
	return d;
}

void cudc_bus_reset(struct cudc *u, enum usb_device_speed speed)
{
	struct usb_gadget_driver *d = callback_driver(u);
	unsigned long flags;

	dev_dbg(udc_dev(u), "bus reset, speed %s\n", usb_speed_string(speed));
	cudc_nuke(&u->ep[0], -ECONNRESET);
	u->gadget.speed = speed;
	u->ep[0].ep.maxpacket = CUDC_EP0_MAXPACKET;
	u->devstatus &= BIT(USB_DEVICE_SELF_POWERED);
	if (!d)
		return;
	local_irq_save(flags);
	usb_gadget_udc_reset(&u->gadget, d);
	local_irq_restore(flags);
}

/* Returns driver->setup()'s value: < 0 means STALL ep0; USB_GADGET_DELAYED_STATUS passes through. */
int cudc_setup(struct cudc *u, const struct usb_ctrlrequest *ctrl)
{
	struct usb_gadget_driver *d = callback_driver(u);
	unsigned long flags;
	int ret;

	if (!d)
		return -ESHUTDOWN;
	cudc_nuke(&u->ep[0], -ECONNRESET);	/* a new SETUP aborts the old transfer (USB 2.0 8.5.3) */
	local_irq_save(flags);
	ret = d->setup(&u->gadget, ctrl);
	local_irq_restore(flags);
	dev_dbg(udc_dev(u), "setup %02x %02x %04x %04x %04x -> %d\n", ctrl->bRequestType,
		ctrl->bRequest, le16_to_cpu(ctrl->wValue), le16_to_cpu(ctrl->wIndex),
		le16_to_cpu(ctrl->wLength), ret);
	return ret;
}

void cudc_set_address(struct cudc *u, u8 addr)
{
	dev_dbg(udc_dev(u), "address %u\n", addr);
	usb_gadget_set_state(&u->gadget, addr ? USB_STATE_ADDRESS : USB_STATE_DEFAULT);
}

void cudc_suspend(struct cudc *u)
{
	struct usb_gadget_driver *d = callback_driver(u);
	unsigned long flags;

	if (u->gadget.state < USB_STATE_DEFAULT || u->gadget.state == USB_STATE_SUSPENDED)
		return;
	u->resume_state = u->gadget.state;
	usb_gadget_set_state(&u->gadget, USB_STATE_SUSPENDED);
	if (!d || !d->suspend)
		return;
	local_irq_save(flags);
	d->suspend(&u->gadget);
	local_irq_restore(flags);
}

void cudc_resume(struct cudc *u)
{
	struct usb_gadget_driver *d = callback_driver(u);
	unsigned long flags;

	if (u->gadget.state != USB_STATE_SUSPENDED)
		return;
	usb_gadget_set_state(&u->gadget, u->resume_state);
	if (!d || !d->resume)
		return;
	local_irq_save(flags);
	d->resume(&u->gadget);
	local_irq_restore(flags);
}

void cudc_vbus(struct cudc *u, bool on)
{
	usb_udc_vbus_handler(&u->gadget, on);
}

void cudc_ep_halted(struct cudc *u, u8 addr, bool halted)
{
	struct cudc_ep *ep = cudc_ep_by_addr(u, addr);
	unsigned long flags;

	if (!ep)
		return;
	spin_lock_irqsave(&u->lock, flags);
	ep->halted = halted;
	if (!halted)
		ep->wedged = false;
	spin_unlock_irqrestore(&u->lock, flags);
}

/* ---- registration ---- */

struct cudc *cudc_create(struct device *parent, const struct cudc_backend_ops *ops, void *backend)
{
	struct cudc *u;
	int i, ret;

	u = kzalloc(sizeof(*u), GFP_KERNEL);
	if (!u)
		return ERR_PTR(-ENOMEM);
	spin_lock_init(&u->lock);
	u->ops = ops;
	u->backend = backend;

	u->gadget.name = KBUILD_MODNAME;
	u->gadget.ops = &cudc_gadget_ops;
	u->gadget.max_speed = USB_SPEED_HIGH;
	u->gadget.speed = USB_SPEED_UNKNOWN;
	INIT_LIST_HEAD(&u->gadget.ep_list);

	for (i = 0; i < CUDC_NUM_EPS; i++) {
		struct cudc_ep *ep = &u->ep[i];
		bool in = i >= 1 && i <= CUDC_NUM_EPN;

		ep->udc = u;
		ep->ep.name = ep_names[i];
		ep->ep.ops = &cudc_ep_ops;
		INIT_LIST_HEAD(&ep->queue);
		if (i == 0) {
			ep->ep.caps.type_control = true;
			ep->ep.caps.dir_in = ep->ep.caps.dir_out = true;
			usb_ep_set_maxpacket_limit(&ep->ep, CUDC_EP0_MAXPACKET);
			continue;
		}
		ep->addr = (in ? USB_DIR_IN : USB_DIR_OUT) | (in ? i : i - CUDC_NUM_EPN);
		ep->ep.caps.type_bulk = ep->ep.caps.type_int = true;
		ep->ep.caps.dir_in = in;
		ep->ep.caps.dir_out = !in;
		usb_ep_set_maxpacket_limit(&ep->ep, CUDC_EPN_MAXPACKET);
		list_add_tail(&ep->ep.ep_list, &u->gadget.ep_list);
	}
	u->gadget.ep0 = &u->ep[0].ep;

	ret = usb_add_gadget_udc(parent, &u->gadget);
	if (ret) {
		kfree(u);
		return ERR_PTR(ret);
	}
	dev_info(udc_dev(u), "UDC registered, backend %s\n", ops->name);
	return u;
}

void cudc_destroy(struct cudc *u)
{
	usb_del_gadget_udc(&u->gadget);
	kfree(u);
}

static bool stub = true;
module_param(stub, bool, 0444);
MODULE_PARM_DESC(stub, "register the in-kernel stub backend (tests); the AUX usb_driver is always registered");

static int __init cudc_init(void)
{
	int ret = cudc_aux_init();

	if (ret)
		return ret;
	if (stub) {
		ret = cudc_stub_init();
		if (ret)
			cudc_aux_exit();
	}
	return ret;
}
module_init(cudc_init);

static void __exit cudc_exit(void)
{
	if (stub)
		cudc_stub_exit();
	cudc_aux_exit();
}
module_exit(cudc_exit);

MODULE_DESCRIPTION("Cynthion USB device controller (UDC) bridge: AUX link backend, stub backend for tests");
MODULE_LICENSE("GPL");
