/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_RPMSG_SERVICE_H
#define QCRATE_RPMSG_SERVICE_H

struct rpmsg_device;

/* Called by AMD's generated FreeRTOS OpenAMP task for each RPMsg lifecycle. */
int app(struct rpmsg_device *rpmsg_device, void *platform);

#endif /* QCRATE_RPMSG_SERVICE_H */
