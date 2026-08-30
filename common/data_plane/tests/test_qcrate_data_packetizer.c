/* SPDX-License-Identifier: MIT */
#include "qcrate_data_packetizer.h"

#include <stdio.h>
#include <string.h>

#define MAX_OBSERVED 8

struct observed_packet {
	struct qcrate_data_header header;
	size_t payload_bytes;
	uint8_t payload[QCRATE_DATA_MAX_PAYLOAD];
};

struct observer {
	struct observed_packet packets[MAX_OBSERVED];
	size_t count;
	int fail_at;
};

static int fail(const char *message)
{
	fprintf(stderr, "FAIL: %s\n", message);
	return 1;
}

static int observe(void *context,
		   const uint8_t header[QCRATE_DATA_HEADER_BYTES],
		   const uint8_t *payload, size_t payload_bytes)
{
	struct observer *observer = context;
	struct observed_packet *packet;

	if (observer->fail_at >= 0 &&
	    observer->count == (size_t)observer->fail_at)
		return -1;
	if (observer->count >= MAX_OBSERVED ||
	    payload_bytes > QCRATE_DATA_MAX_PAYLOAD)
		return -1;
	packet = &observer->packets[observer->count++];
	if (qcrate_data_header_decode(&packet->header, header,
				      QCRATE_DATA_HEADER_BYTES) !=
	    QCRATE_DATA_CODEC_OK)
		return -1;
	packet->payload_bytes = payload_bytes;
	memcpy(packet->payload, payload, payload_bytes);
	return 0;
}

int main(void)
{
	struct qcrate_data_packetizer_config config = {
		.payload_format = QCRATE_DATA_FORMAT_IQ_S16_LE,
		.sample_bytes = 4,
		.stream_id = 3,
		.run_id = UINT64_C(0x1020304050607080),
		.shot_id = 9,
		.initial_sequence = 10,
		.frame_samples = 400,
		.frame_count = 2,
		.sample_rate_numerator = UINT64_C(12500000),
		.sample_rate_denominator = 1,
		.timestamp_rate_numerator = UINT64_C(200000000),
		.timestamp_rate_denominator = 1,
		.center_frequency_hz = INT64_C(29000000),
		.channel_count = 2,
		.component_bits = 16,
		.fraction_bits = 15,
		.timestamp_clock_id = QCRATE_DATA_CLOCK_QCRATE_TIMEBASE,
		.config_id = UINT64_C(0x0fd399e017ecb182),
		.first_sample_timestamp = UINT64_C(0x123456789abcdef0),
		.first_sample_timestamp_valid = true,
		.end_of_stream = true,
	};
	struct qcrate_data_packetizer_result result;
	struct qcrate_data_shot_end end;
	struct observer observer = {.fail_at = -1};
	uint8_t samples[2 * 400 * 4];
	size_t index;
	int status;

	for (index = 0; index < sizeof(samples); index++)
		samples[index] = (uint8_t)index;
	status = qcrate_data_packetize(&config, samples, sizeof(samples),
				       observe, &observer, &result);
	if (status != QCRATE_DATA_PACKETIZER_OK)
		return fail("valid finite shot was rejected");
	if (observer.count != 6 || result.total_datagrams != 6 ||
	    result.data_packets != 4 || result.next_sequence != 16)
		return fail("packet totals are incorrect");
	if (observer.packets[0].header.packet_type !=
	    QCRATE_DATA_PACKET_STREAM_INFO ||
	    observer.packets[0].header.packet_sequence != 10)
		return fail("STREAM_INFO header is incorrect");
	if (observer.packets[1].header.sample_count != 334 ||
	    observer.packets[1].header.sample_offset != 0 ||
	    observer.packets[1].payload_bytes != 1336 ||
	    observer.packets[2].header.sample_count != 66 ||
	    observer.packets[2].header.sample_offset != 334)
		return fail("frame packet splitting is incorrect");
	if (!(observer.packets[1].header.flags &
	      QCRATE_DATA_FLAG_FRAME_START) ||
	    !(observer.packets[1].header.flags &
	      QCRATE_DATA_FLAG_TIMESTAMP_VALID) ||
	    observer.packets[1].header.timestamp_ticks !=
	      config.first_sample_timestamp ||
	    (observer.packets[2].header.flags &
	      QCRATE_DATA_FLAG_TIMESTAMP_VALID) ||
	    observer.packets[2].header.timestamp_ticks != 0 ||
	    !(observer.packets[2].header.flags &
	      QCRATE_DATA_FLAG_FRAME_END) ||
	    observer.packets[3].header.frame_id != 1)
		return fail("frame identity or boundary flags are incorrect");
	if (qcrate_data_shot_end_decode(
			&end, observer.packets[5].payload,
			observer.packets[5].payload_bytes) != QCRATE_DATA_CODEC_OK)
		return fail("SHOT_END could not be decoded");
	if (end.total_samples != 800 || end.total_data_packets != 4 ||
	    end.first_packet_sequence != 11 || end.last_packet_sequence != 14 ||
	    end.payload_crc32 != result.payload_crc32 ||
	    result.payload_crc32 != UINT32_C(0xbcde9e18))
		return fail("SHOT_END totals are incorrect");
	if (!(end.end_flags & QCRATE_DATA_FLAG_END_OF_STREAM) ||
	    !(end.end_flags & QCRATE_DATA_FLAG_PAYLOAD_CRC_VALID))
		return fail("terminal SHOT_END flags are incorrect");

	config.initial_sequence = result.next_sequence;
	config.shot_id++;
	config.first_sample_timestamp_valid = false;
	config.end_of_stream = false;
	observer = (struct observer) {.fail_at = -1};
	status = qcrate_data_packetize(&config, samples, sizeof(samples),
				       observe, &observer, &result);
	if (status != QCRATE_DATA_PACKETIZER_OK ||
	    observer.packets[0].header.packet_sequence != 16 ||
	    observer.packets[0].header.shot_id != 10 ||
	    observer.packets[1].header.timestamp_ticks != 0 ||
	    (observer.packets[1].header.flags &
	     QCRATE_DATA_FLAG_TIMESTAMP_VALID))
		return fail("chained shot identity or timestamp is incorrect");
	if (qcrate_data_shot_end_decode(
			&end, observer.packets[5].payload,
			observer.packets[5].payload_bytes) != QCRATE_DATA_CODEC_OK ||
	    (end.end_flags & QCRATE_DATA_FLAG_END_OF_STREAM))
		return fail("intermediate SHOT_END was marked end-of-stream");
	status = qcrate_data_emit_end_of_stream(
		config.stream_id, config.run_id, config.shot_id,
		result.next_sequence, observe, &observer, &result.next_sequence);
	if (status != QCRATE_DATA_PACKETIZER_OK || observer.count != 7 ||
	    observer.packets[6].header.packet_type !=
		QCRATE_DATA_PACKET_HEARTBEAT ||
	    !(observer.packets[6].header.flags &
	      QCRATE_DATA_FLAG_END_OF_STREAM) ||
	    observer.packets[6].payload_bytes != 0 || result.next_sequence != 23)
		return fail("terminal lifecycle heartbeat is incorrect");

	observer = (struct observer) {.fail_at = 2};
	status = qcrate_data_packetize(&config, samples, sizeof(samples),
				       observe, &observer, &result);
	if (status != QCRATE_DATA_PACKETIZER_EMIT_ERROR)
		return fail("emitter failure was not propagated");
	if (qcrate_data_packetize(&config, samples, sizeof(samples) - 1,
				  observe, &observer, &result) !=
	    QCRATE_DATA_PACKETIZER_BAD_SIZE)
		return fail("incorrect sample-buffer size was accepted");

	puts("PASS: Q-Crate finite-shot C packetizer");
	return 0;
}
