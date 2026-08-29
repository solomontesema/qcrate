/* SPDX-License-Identifier: MIT */
#include "qcrate_data_protocol.h"

#include <stdio.h>
#include <string.h>

static int fail(const char *message)
{
	fprintf(stderr, "FAIL: %s\n", message);
	return 1;
}

int main(void)
{
	static const uint8_t expected_header[QCRATE_DATA_HEADER_BYTES] = {
		0x51, 0x43, 0x44, 0x50, 0x00, 0x01, 0x00, 0x40,
		0x00, 0x02, 0x00, 0x02, 0x00, 0x00, 0x00, 0x03,
		0x01, 0x02, 0x03, 0x04, 0x11, 0x12, 0x13, 0x14,
		0x15, 0x16, 0x17, 0x18, 0x21, 0x22, 0x23, 0x24,
		0x25, 0x26, 0x27, 0x28, 0x31, 0x32, 0x33, 0x34,
		0x35, 0x36, 0x37, 0x38, 0x41, 0x42, 0x43, 0x44,
		0x51, 0x52, 0x53, 0x54, 0x01, 0x23, 0x00, 0x04,
		0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
	};
	struct qcrate_data_header header = {
		.packet_type = QCRATE_DATA_PACKET_DATA,
		.payload_format = QCRATE_DATA_FORMAT_IQ_S16_LE,
		.flags = QCRATE_DATA_FLAG_TIMESTAMP_VALID |
			 QCRATE_DATA_FLAG_FRAME_START,
		.stream_id = UINT32_C(0x01020304),
		.run_id = UINT64_C(0x1112131415161718),
		.shot_id = UINT64_C(0x2122232425262728),
		.packet_sequence = UINT64_C(0x3132333435363738),
		.frame_id = UINT32_C(0x41424344),
		.sample_offset = UINT32_C(0x51525354),
		.sample_count = UINT16_C(0x0123),
		.sample_bytes = 4,
		.timestamp_ticks = UINT64_C(0x6162636465666768),
	};
	struct qcrate_data_header decoded = {0};
	struct qcrate_data_stream_info info = {
		.frame_samples = 1024,
		.sample_rate_numerator = UINT64_C(12500000),
		.sample_rate_denominator = 1,
		.timestamp_rate_numerator = UINT64_C(200000000),
		.timestamp_rate_denominator = 1,
		.center_frequency_hz = -1000000,
		.channel_count = 2,
		.component_bits = 16,
		.fraction_bits = 15,
		.timestamp_clock_id = QCRATE_DATA_CLOCK_QCRATE_TIMEBASE,
		.config_id = UINT64_C(0x1020304050607080),
	};
	struct qcrate_data_stream_info decoded_info = {0};
	struct qcrate_data_shot_end end = {
		.status = QCRATE_DATA_SHOT_COMPLETE,
		.end_flags = QCRATE_DATA_FLAG_END_OF_STREAM,
		.total_frames = 4,
		.total_samples = 4096,
		.total_data_packets = 52,
		.first_packet_sequence = 2,
		.last_packet_sequence = 53,
		.payload_bytes = 16384,
		.payload_crc32 = UINT32_C(0x12345678),
	};
	struct qcrate_data_shot_end decoded_end = {0};
	uint8_t encoded[QCRATE_DATA_HEADER_BYTES];
	uint8_t object[QCRATE_DATA_STREAM_INFO_BYTES];

	if (qcrate_data_header_encode(encoded, &header) != QCRATE_DATA_CODEC_OK)
		return fail("header encode rejected a valid object");
	if (memcmp(encoded, expected_header, sizeof(encoded)) != 0)
		return fail("header does not match the cross-language golden vector");
	if (qcrate_data_header_decode(&decoded, encoded, sizeof(encoded)) !=
	    QCRATE_DATA_CODEC_OK)
		return fail("header decode rejected the golden vector");
	if (decoded.packet_sequence != header.packet_sequence ||
	    decoded.timestamp_ticks != header.timestamp_ticks ||
	    decoded.header_bytes != QCRATE_DATA_HEADER_BYTES)
		return fail("header round trip changed a field");

	if (qcrate_data_stream_info_encode(object, &info) != QCRATE_DATA_CODEC_OK ||
	    qcrate_data_stream_info_decode(&decoded_info, object, sizeof(object)) !=
	    QCRATE_DATA_CODEC_OK)
		return fail("stream-info round trip failed");
	if (decoded_info.center_frequency_hz != info.center_frequency_hz ||
	    decoded_info.sample_rate_numerator != info.sample_rate_numerator ||
	    decoded_info.config_id != info.config_id)
		return fail("stream-info round trip changed a field");

	if (qcrate_data_shot_end_encode(object, &end) != QCRATE_DATA_CODEC_OK ||
	    qcrate_data_shot_end_decode(&decoded_end, object, sizeof(object)) !=
	    QCRATE_DATA_CODEC_OK)
		return fail("shot-end round trip failed");
	if (decoded_end.total_samples != end.total_samples ||
	    decoded_end.payload_crc32 != end.payload_crc32)
		return fail("shot-end round trip changed a field");

	encoded[0] = 0;
	if (qcrate_data_header_decode(&decoded, encoded, sizeof(encoded)) !=
	    QCRATE_DATA_CODEC_BAD_MAGIC)
		return fail("bad magic was not rejected");

	puts("PASS: Q-Crate Data Plane v1 C codec");
	return 0;
}
