/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_RUN_FORMAT_H
#define QCRATE_RUN_FORMAT_H

#include <stddef.h>
#include <stdint.h>

#define QCRATE_JOURNAL_MAGIC        UINT32_C(0x5143524a) /* QCRJ */
#define QCRATE_JOURNAL_VERSION      UINT16_C(1)
#define QCRATE_JOURNAL_HEADER_BYTES 16U
#define QCRATE_JOURNAL_RECORD_BYTES 32U

#define QCRATE_INDEX_MAGIC          UINT32_C(0x51494458) /* QIDX */
#define QCRATE_INDEX_VERSION        UINT16_C(1)
#define QCRATE_INDEX_HEADER_BYTES   32U
#define QCRATE_INDEX_RECORD_BYTES   128U

enum qcrate_index_state {
	QCRATE_INDEX_COMPLETE = 1,
	QCRATE_INDEX_INCOMPLETE = 2,
};

enum qcrate_index_issue {
	QCRATE_INDEX_ISSUE_PROTOCOL = UINT32_C(1) << 0,
	QCRATE_INDEX_ISSUE_MISSING = UINT32_C(1) << 1,
	QCRATE_INDEX_ISSUE_CONFLICT = UINT32_C(1) << 2,
	QCRATE_INDEX_ISSUE_FRAME = UINT32_C(1) << 3,
	QCRATE_INDEX_ISSUE_CRC = UINT32_C(1) << 4,
	QCRATE_INDEX_ISSUE_SENDER = UINT32_C(1) << 5,
	QCRATE_INDEX_ISSUE_TIMESTAMP = UINT32_C(1) << 6,
	QCRATE_INDEX_ISSUE_CONTINUITY = UINT32_C(1) << 7,
};

struct qcrate_index_record {
	uint64_t shot_id;
	uint32_t state;
	uint32_t issue_flags;
	uint64_t first_packet_sequence;
	uint64_t last_packet_sequence;
	uint64_t first_sample_timestamp;
	uint64_t first_receive_ns;
	uint64_t last_receive_ns;
	uint64_t sample_file_offset;
	uint64_t sample_bytes;
	uint32_t frame_count;
	uint32_t frame_samples;
	uint32_t data_packets;
	uint32_t datagrams;
	uint32_t duplicate_packets;
	uint32_t reordered_packets;
	uint32_t missing_packets;
	uint32_t payload_crc32;
	uint32_t source_flags;
	uint16_t payload_format;
	uint16_t sample_bytes_per_word;
	uint64_t config_id;
};

void qcrate_journal_header_encode(
	uint8_t output[QCRATE_JOURNAL_HEADER_BYTES]);
int qcrate_journal_record_encode(
	uint8_t output[QCRATE_JOURNAL_RECORD_BYTES],
	uint16_t datagram_bytes,
	uint8_t address_family,
	uint16_t source_port,
	uint64_t receive_monotonic_ns,
	const uint8_t source_address[16]);
void qcrate_index_header_encode(
	uint8_t output[QCRATE_INDEX_HEADER_BYTES],
	uint64_t run_id,
	uint32_t stream_id);
int qcrate_index_record_encode(
	uint8_t output[QCRATE_INDEX_RECORD_BYTES],
	const struct qcrate_index_record *record);

#endif /* QCRATE_RUN_FORMAT_H */
