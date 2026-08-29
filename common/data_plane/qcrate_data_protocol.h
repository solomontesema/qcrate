/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_DATA_PROTOCOL_H
#define QCRATE_DATA_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define QCRATE_DATA_MAGIC            UINT32_C(0x51434450) /* "QCDP" */
#define QCRATE_DATA_VERSION          UINT16_C(1)
#define QCRATE_DATA_HEADER_BYTES     UINT16_C(64)
#define QCRATE_DATA_MAX_DATAGRAM     UINT16_C(1400)
#define QCRATE_DATA_MAX_PAYLOAD      UINT16_C(1336)
#define QCRATE_DATA_STREAM_INFO_BYTES UINT16_C(64)
#define QCRATE_DATA_SHOT_END_BYTES   UINT16_C(64)

enum qcrate_data_packet_type {
	QCRATE_DATA_PACKET_STREAM_INFO = 1,
	QCRATE_DATA_PACKET_DATA = 2,
	QCRATE_DATA_PACKET_SHOT_END = 3,
	QCRATE_DATA_PACKET_HEARTBEAT = 4,
};

enum qcrate_data_payload_format {
	QCRATE_DATA_FORMAT_NONE = 0,
	QCRATE_DATA_FORMAT_COUNTER_U32_LE = 1,
	QCRATE_DATA_FORMAT_IQ_S16_LE = 2,
};

enum qcrate_data_timestamp_clock {
	QCRATE_DATA_CLOCK_UNSPECIFIED = 0,
	QCRATE_DATA_CLOCK_QCRATE_TIMEBASE = 1,
	QCRATE_DATA_CLOCK_TAI_NS = 2,
	QCRATE_DATA_CLOCK_MONOTONIC_NS = 3,
};

enum qcrate_data_flags {
	QCRATE_DATA_FLAG_TIMESTAMP_VALID = UINT32_C(1) << 0,
	QCRATE_DATA_FLAG_FRAME_START = UINT32_C(1) << 1,
	QCRATE_DATA_FLAG_FRAME_END = UINT32_C(1) << 2,
	QCRATE_DATA_FLAG_DISCONTINUITY_BEFORE = UINT32_C(1) << 3,
	QCRATE_DATA_FLAG_PAYLOAD_CRC_VALID = UINT32_C(1) << 4,
	QCRATE_DATA_FLAG_SOURCE_OVERFLOW = UINT32_C(1) << 5,
	QCRATE_DATA_FLAG_SENDER_DROP = UINT32_C(1) << 6,
	QCRATE_DATA_FLAG_END_OF_STREAM = UINT32_C(1) << 7,
};

enum qcrate_data_shot_status {
	QCRATE_DATA_SHOT_COMPLETE = 0,
	QCRATE_DATA_SHOT_ABORTED = 1,
	QCRATE_DATA_SHOT_DMA_ERROR = 2,
	QCRATE_DATA_SHOT_TIMEOUT = 3,
	QCRATE_DATA_SHOT_SOURCE_ERROR = 4,
	QCRATE_DATA_SHOT_SENDER_ERROR = 5,
};

enum qcrate_data_codec_status {
	QCRATE_DATA_CODEC_OK = 0,
	QCRATE_DATA_CODEC_BAD_ARGUMENT = -1,
	QCRATE_DATA_CODEC_SHORT_INPUT = -2,
	QCRATE_DATA_CODEC_BAD_MAGIC = -3,
	QCRATE_DATA_CODEC_BAD_VERSION = -4,
	QCRATE_DATA_CODEC_BAD_HEADER_SIZE = -5,
	QCRATE_DATA_CODEC_BAD_PACKET_TYPE = -6,
	QCRATE_DATA_CODEC_BAD_OBJECT_SIZE = -7,
};

/* Host-native representation. The wire object is always explicitly encoded. */
struct qcrate_data_header {
	uint16_t header_bytes;
	uint16_t packet_type;
	uint16_t payload_format;
	uint32_t flags;
	uint32_t stream_id;
	uint64_t run_id;
	uint64_t shot_id;
	uint64_t packet_sequence;
	uint32_t frame_id;
	uint32_t sample_offset;
	uint16_t sample_count;
	uint16_t sample_bytes;
	uint64_t timestamp_ticks;
};

struct qcrate_data_stream_info {
	uint32_t frame_samples;
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

struct qcrate_data_shot_end {
	uint32_t status;
	uint32_t end_flags;
	uint32_t total_frames;
	uint64_t total_samples;
	uint64_t total_data_packets;
	uint64_t first_packet_sequence;
	uint64_t last_packet_sequence;
	uint64_t payload_bytes;
	uint32_t payload_crc32;
};

int qcrate_data_header_encode(
	uint8_t output[QCRATE_DATA_HEADER_BYTES],
	const struct qcrate_data_header *header);
int qcrate_data_header_decode(
	struct qcrate_data_header *header,
	const uint8_t *input,
	size_t input_bytes);
int qcrate_data_stream_info_encode(
	uint8_t output[QCRATE_DATA_STREAM_INFO_BYTES],
	const struct qcrate_data_stream_info *info);
int qcrate_data_stream_info_decode(
	struct qcrate_data_stream_info *info,
	const uint8_t *input,
	size_t input_bytes);
int qcrate_data_shot_end_encode(
	uint8_t output[QCRATE_DATA_SHOT_END_BYTES],
	const struct qcrate_data_shot_end *end);
int qcrate_data_shot_end_decode(
	struct qcrate_data_shot_end *end,
	const uint8_t *input,
	size_t input_bytes);

#endif /* QCRATE_DATA_PROTOCOL_H */
