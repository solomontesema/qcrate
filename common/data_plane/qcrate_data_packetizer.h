/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_DATA_PACKETIZER_H
#define QCRATE_DATA_PACKETIZER_H

#include <stddef.h>
#include <stdint.h>

#include "qcrate_data_protocol.h"

enum qcrate_data_packetizer_status {
	QCRATE_DATA_PACKETIZER_OK = 0,
	QCRATE_DATA_PACKETIZER_BAD_ARGUMENT = -1,
	QCRATE_DATA_PACKETIZER_BAD_SIZE = -2,
	QCRATE_DATA_PACKETIZER_SEQUENCE_OVERFLOW = -3,
	QCRATE_DATA_PACKETIZER_CODEC_ERROR = -4,
	QCRATE_DATA_PACKETIZER_EMIT_ERROR = -5,
};

struct qcrate_data_packetizer_config {
	uint16_t payload_format;
	uint16_t sample_bytes;
	uint32_t stream_id;
	uint64_t run_id;
	uint64_t shot_id;
	uint64_t initial_sequence;
	uint32_t frame_samples;
	uint32_t frame_count;
	uint64_t sample_rate_numerator;
	uint64_t sample_rate_denominator;
	uint64_t timestamp_rate_numerator;
	uint64_t timestamp_rate_denominator;
	int64_t center_frequency_hz;
	uint16_t channel_count;
	uint16_t component_bits;
	uint16_t fraction_bits;
	uint16_t timestamp_clock_id;
	uint64_t config_id;
};

struct qcrate_data_packetizer_result {
	uint64_t total_datagrams;
	uint64_t data_packets;
	uint64_t first_data_sequence;
	uint64_t last_data_sequence;
	uint64_t next_sequence;
	uint64_t total_samples;
	uint64_t payload_bytes;
	uint32_t payload_crc32;
};

typedef int (*qcrate_data_emit_fn)(
	void *context,
	const uint8_t header[QCRATE_DATA_HEADER_BYTES],
	const uint8_t *payload,
	size_t payload_bytes);

int qcrate_data_packetize(
	const struct qcrate_data_packetizer_config *config,
	const uint8_t *samples,
	size_t samples_bytes,
	qcrate_data_emit_fn emit,
	void *emit_context,
	struct qcrate_data_packetizer_result *result);

#endif /* QCRATE_DATA_PACKETIZER_H */
