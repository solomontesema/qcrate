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

/* Coherent active configuration snapshot owned and validated by R5-0. */
struct qcrate_runtime_config_status {
	uint32_t flags;
	uint32_t pl_status;
	uint32_t pl_reject_reason;
	uint32_t active_generation;
	uint64_t config_id;
	uint32_t signal_phase_increment;
	uint32_t lo_phase_increment;
	uint32_t frame_length_words;
	uint32_t frame_count;
	uint32_t reason;
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

int qcrate_rpmsg_get_runtime_config(
	struct qcrate_rpmsg_client *client,
	struct qcrate_runtime_config_status *status);

#endif /* QCRATE_RPMSG_CLIENT_H */
