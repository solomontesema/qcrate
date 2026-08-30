/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_RPMSG_CLIENT_H
#define QCRATE_RPMSG_CLIENT_H

#include <stddef.h>
#include <stdint.h>

#include "qcrate_protocol.h"

struct qcrate_rpmsg_client {
	int file_descriptor;
	int timeout_ms;
	uint32_t next_transaction_id;
};

int qcrate_rpmsg_discover(char *device_path, size_t capacity);
int qcrate_rpmsg_client_open(struct qcrate_rpmsg_client *client,
			     const char *device_path, int timeout_ms);
void qcrate_rpmsg_client_close(struct qcrate_rpmsg_client *client);

/*
 * Returns zero only for a transport-valid response with QCRATE_STATUS_OK.
 * On an R5 service error, response still contains the complete reply and
 * errno is EREMOTEIO.
 */
int qcrate_rpmsg_client_exchange(
	struct qcrate_rpmsg_client *client,
	uint16_t command,
	const uint32_t *request_words,
	size_t request_word_count,
	struct qcrate_rpmsg_message *response);

#endif /* QCRATE_RPMSG_CLIENT_H */
