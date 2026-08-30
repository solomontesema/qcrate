/* SPDX-License-Identifier: MIT */
#include "qcrate_run_format.h"

#include <string.h>

static void put_u16(uint8_t *output, uint16_t value)
{
	output[0] = (uint8_t)(value >> 8);
	output[1] = (uint8_t)value;
}

static void put_u32(uint8_t *output, uint32_t value)
{
	output[0] = (uint8_t)(value >> 24);
	output[1] = (uint8_t)(value >> 16);
	output[2] = (uint8_t)(value >> 8);
	output[3] = (uint8_t)value;
}

static void put_u64(uint8_t *output, uint64_t value)
{
	put_u32(output, (uint32_t)(value >> 32));
	put_u32(output + 4, (uint32_t)value);
}

void qcrate_journal_header_encode(
	uint8_t output[QCRATE_JOURNAL_HEADER_BYTES])
{
	memset(output, 0, QCRATE_JOURNAL_HEADER_BYTES);
	put_u32(output, QCRATE_JOURNAL_MAGIC);
	put_u16(output + 4, QCRATE_JOURNAL_VERSION);
	put_u16(output + 6, QCRATE_JOURNAL_HEADER_BYTES);
}

int qcrate_journal_record_encode(
	uint8_t output[QCRATE_JOURNAL_RECORD_BYTES],
	uint16_t datagram_bytes,
	uint8_t address_family,
	uint16_t source_port,
	uint64_t receive_monotonic_ns,
	const uint8_t source_address[16])
{
	if (!output || !source_address ||
	    (address_family != 4 && address_family != 6))
		return -1;
	if (address_family == 4 &&
	    memcmp(source_address + 4, (uint8_t[12]){0}, 12))
		return -1;
	memset(output, 0, QCRATE_JOURNAL_RECORD_BYTES);
	put_u16(output, datagram_bytes);
	output[2] = address_family;
	put_u16(output + 4, source_port);
	put_u64(output + 8, receive_monotonic_ns);
	memcpy(output + 16, source_address, 16);
	return 0;
}

void qcrate_index_header_encode(
	uint8_t output[QCRATE_INDEX_HEADER_BYTES],
	uint64_t run_id,
	uint32_t stream_id)
{
	memset(output, 0, QCRATE_INDEX_HEADER_BYTES);
	put_u32(output, QCRATE_INDEX_MAGIC);
	put_u16(output + 4, QCRATE_INDEX_VERSION);
	put_u16(output + 6, QCRATE_INDEX_HEADER_BYTES);
	put_u16(output + 8, QCRATE_INDEX_RECORD_BYTES);
	put_u64(output + 16, run_id);
	put_u32(output + 24, stream_id);
}

int qcrate_index_record_encode(
	uint8_t output[QCRATE_INDEX_RECORD_BYTES],
	const struct qcrate_index_record *record)
{
	if (!output || !record ||
	    (record->state != QCRATE_INDEX_COMPLETE &&
	     record->state != QCRATE_INDEX_INCOMPLETE))
		return -1;
	memset(output, 0, QCRATE_INDEX_RECORD_BYTES);
	put_u64(output + 0, record->shot_id);
	put_u32(output + 8, record->state);
	put_u32(output + 12, record->issue_flags);
	put_u64(output + 16, record->first_packet_sequence);
	put_u64(output + 24, record->last_packet_sequence);
	put_u64(output + 32, record->first_sample_timestamp);
	put_u64(output + 40, record->first_receive_ns);
	put_u64(output + 48, record->last_receive_ns);
	put_u64(output + 56, record->sample_file_offset);
	put_u64(output + 64, record->sample_bytes);
	put_u32(output + 72, record->frame_count);
	put_u32(output + 76, record->frame_samples);
	put_u32(output + 80, record->data_packets);
	put_u32(output + 84, record->datagrams);
	put_u32(output + 88, record->duplicate_packets);
	put_u32(output + 92, record->reordered_packets);
	put_u32(output + 96, record->missing_packets);
	put_u32(output + 100, record->payload_crc32);
	put_u32(output + 104, record->source_flags);
	put_u16(output + 108, record->payload_format);
	put_u16(output + 110, record->sample_bytes_per_word);
	put_u64(output + 112, record->config_id);
	return 0;
}
