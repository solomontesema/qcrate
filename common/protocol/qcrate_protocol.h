/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_PROTOCOL_H
#define QCRATE_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

/* Both ZynqMP A53 Linux and Cortex-R5 FreeRTOS endpoints are little-endian. */
#define QCRATE_RPMSG_SERVICE_NAME "qcrate-control"
#define QCRATE_RPMSG_MAGIC        UINT32_C(0x51524350)
#define QCRATE_RPMSG_ABI_VERSION  UINT16_C(1)
#define QCRATE_RPMSG_PAYLOAD_WORDS 11U
#define QCRATE_RPMSG_MESSAGE_SIZE 64U

#define QCRATE_CMD_PING           UINT16_C(1)
#define QCRATE_CMD_GET_INFO       UINT16_C(2)
#define QCRATE_CMD_SCRATCH_TEST   UINT16_C(3)
#define QCRATE_CMD_SCRATCH_TEST   UINT16_C(3)

#define QCRATE_STATUS_OK              INT32_C(0)
#define QCRATE_STATUS_BAD_MAGIC       INT32_C(-1)
#define QCRATE_STATUS_BAD_ABI         INT32_C(-2)
#define QCRATE_STATUS_BAD_LENGTH      INT32_C(-3)
#define QCRATE_STATUS_BAD_COMMAND     INT32_C(-4)
#define QCRATE_STATUS_BAD_DEVICE_ID   INT32_C(-5)
#define QCRATE_STATUS_SCRATCH_VERIFY  INT32_C(-6)

/*
 * Requests and responses use the same fixed-size wire object. A response
 * repeats command and transaction_id, places its result in status, and reports
 * the number of valid 32-bit payload words. Reserved payload words are zero.
 */
struct qcrate_rpmsg_message {
	uint32_t magic;
	uint16_t abi_version;
	uint16_t command;
	uint32_t transaction_id;
	uint32_t payload_words;
	int32_t status;
	uint32_t payload[QCRATE_RPMSG_PAYLOAD_WORDS];
};

_Static_assert(sizeof(struct qcrate_rpmsg_message) ==
	       QCRATE_RPMSG_MESSAGE_SIZE,
	       "Q-Crate RPMsg ABI must remain 64 bytes");
_Static_assert(offsetof(struct qcrate_rpmsg_message, payload) == 20U,
	       "Q-Crate RPMsg header layout changed");

#endif /* QCRATE_PROTOCOL_H */
