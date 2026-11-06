/* SPDX-License-Identifier: GPL-2.0 */
/*
 * cynthion_udc: UDC core <-> backend interface.
 * - core (cudc_core.c): usb_gadget_ops / usb_ep_ops, request queues, gadget callbacks.
 * - backend: the Cynthion over the AUX link (later) or the in-kernel stub host (cudc_stub.c).
 * Design: docs/usb-device-mode.md ("Linux ops onto the link").
 */
#ifndef CUDC_H
#define CUDC_H

#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/usb/ch9.h>
#include <linux/usb/gadget.h>

#define CUDC_EP0_MAXPACKET	64
#define CUDC_EPN_MAXPACKET	512	/* HS bulk max; interrupt capped here too (doc: endpoint limits) */
#define CUDC_NUM_EPN		4	/* ep1..ep4, each direction */
#define CUDC_NUM_EPS		(1 + 2 * CUDC_NUM_EPN)

struct cudc;

struct cudc_request {
	struct usb_request	req;
	struct list_head	queue;
};

struct cudc_ep {
	struct usb_ep		ep;
	struct cudc		*udc;
	const struct usb_endpoint_descriptor *desc;	/* NULL: disabled (always NULL on ep0) */
	struct list_head	queue;			/* cudc_request, oldest first */
	u8			addr;			/* bEndpointAddress this ep serves */
	bool			halted;
	bool			wedged;
};

/*
 * Backend ops. Context:
 * - connect/disconnect: process context, may sleep (pullup, under udc-core's connect_lock).
 * - everything else: may be atomic (called from inside driver->setup); must not sleep.
 * - none are called with cudc.lock held.
 */
struct cudc_backend_ops {
	const char *name;
	int  (*connect)(struct cudc *u);		/* pullup on: attach to the host */
	void (*disconnect)(struct cudc *u);		/* pullup off: detach, stop all traffic */
	int  (*ep_enable)(struct cudc *u, struct cudc_ep *ep,
			  const struct usb_endpoint_descriptor *desc);
	void (*ep_disable)(struct cudc *u, struct cudc_ep *ep);
	void (*queue)(struct cudc *u, struct cudc_ep *ep);	/* epN: request(s) pending */
	void (*dequeue)(struct cudc *u, struct cudc_ep *ep, struct cudc_request *req);
	int  (*set_halt)(struct cudc *u, struct cudc_ep *ep, bool halt, bool wedge);
	void (*setup_reply)(struct cudc *u);		/* ep0: data or status request queued */
};

struct cudc {
	spinlock_t		lock;
	struct usb_gadget	gadget;
	struct usb_gadget_driver *driver;
	struct cudc_ep		ep[CUDC_NUM_EPS];	/* [0] ep0, [1..4] ep1in-ep4in, [5..8] ep1out-ep4out */
	const struct cudc_backend_ops *ops;
	void			*backend;		/* backend private */
	bool			callbacks;		/* udc_async_callbacks */
	bool			pulled_up;
	u16			devstatus;		/* GET_STATUS(device) bits */
};

struct cudc *cudc_create(struct device *parent, const struct cudc_backend_ops *ops, void *backend);
void cudc_destroy(struct cudc *u);

/* Events backend -> core: the link's BUS_RESET / SETUP / ADDRESS messages. Atomic-safe. */
void cudc_bus_reset(struct cudc *u, enum usb_device_speed speed);
int cudc_setup(struct cudc *u, const struct usb_ctrlrequest *ctrl);
void cudc_set_address(struct cudc *u, u8 addr);

/*
 * Request access for the backend: peek + touch req->buf + take, all under u->lock
 * (dequeue may race), then give back without it.
 */
struct cudc_ep *cudc_ep_by_addr(struct cudc *u, u8 addr);
struct cudc_request *cudc_next_request(struct cudc_ep *ep);
void cudc_take(struct cudc_request *req);
void cudc_giveback(struct cudc_ep *ep, struct cudc_request *req, int status);
void cudc_nuke(struct cudc_ep *ep, int status);	/* takes u->lock */

static inline struct cudc_request *to_cudc_req(struct usb_request *r)
{
	return container_of(r, struct cudc_request, req);
}

/* stub backend */
int cudc_stub_init(void);
void cudc_stub_exit(void);

#endif
