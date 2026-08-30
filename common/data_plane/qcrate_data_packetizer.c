/* SPDX-License-Identifier: MIT */
#include "qcrate_data_packetizer.h"

#include <limits.h>
#include <string.h>

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t bytes)
{
	size_t index;
	unsigned int bit;

	for (index = 0; index < bytes; index++) {
		crc ^= data[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				(UINT32_C(0xedb88320) & (uint32_t)-(int32_t)(crc & 1));
	}
	return crc;
}

static int emit_object(
	const struct qcrate_data_header *header,
	const uint8_t *payload,
	size_t payload_bytes,
	qcrate_data_emit_fn emit,
	void *context)
{
	uint8_t encoded_header[QCRATE_DATA_HEADER_BYTES];

	if (qcrate_data_header_encode(encoded_header, header) !=
	    QCRATE_DATA_CODEC_OK)
		return QCRATE_DATA_PACKETIZER_CODEC_ERROR;
	if (emit(context, encoded_header, payload, payload_bytes))
		return QCRATE_DATA_PACKETIZER_EMIT_ERROR;
	return QCRATE_DATA_PACKETIZER_OK;
}

int qcrate_data_packetize(
	const struct qcrate_data_packetizer_config *config,
	const uint8_t *samples,
	size_t samples_bytes,
	qcrate_data_emit_fn emit,
	void *emit_context,
	struct qcrate_data_packetizer_result *result)
{
	struct qcrate_data_stream_info stream_info;
	struct qcrate_data_shot_end shot_end;
	struct qcrate_data_header header = {0};
	uint8_t object[QCRATE_DATA_STREAM_INFO_BYTES];
	uint64_t data_packets_per_frame;
	uint64_t data_packets;
	uint64_t total_samples;
	uint64_t payload_bytes;
	uint64_t sequence;
	uint32_t samples_per_packet;
	uint32_t frame;
	uint32_t crc = UINT32_MAX;
	size_t frame_bytes;
	int status;

	if (!config || !samples || !emit || !result)
		return QCRATE_DATA_PACKETIZER_BAD_ARGUMENT;
	memset(result, 0, sizeof(*result));
	if (!config->frame_samples || !config->frame_count ||
	    !config->sample_bytes ||
	    !config->sample_rate_denominator ||
	    !config->timestamp_rate_denominator)
		return QCRATE_DATA_PACKETIZER_BAD_ARGUMENT;
	if ((config->payload_format != QCRATE_DATA_FORMAT_COUNTER_U32_LE &&
	     config->payload_format != QCRATE_DATA_FORMAT_IQ_S16_LE) ||
	    config->sample_bytes != 4)
		return QCRATE_DATA_PACKETIZER_BAD_ARGUMENT;
	if (config->sample_bytes > QCRATE_DATA_MAX_PAYLOAD)
		return QCRATE_DATA_PACKETIZER_BAD_SIZE;
	samples_per_packet = QCRATE_DATA_MAX_PAYLOAD / config->sample_bytes;
	if (!samples_per_packet)
		return QCRATE_DATA_PACKETIZER_BAD_SIZE;
	if (config->frame_samples > SIZE_MAX / config->sample_bytes)
		return QCRATE_DATA_PACKETIZER_BAD_SIZE;
	frame_bytes = (size_t)config->frame_samples * config->sample_bytes;
	if (config->frame_count > SIZE_MAX / frame_bytes)
		return QCRATE_DATA_PACKETIZER_BAD_SIZE;
	if ((size_t)config->frame_count * frame_bytes != samples_bytes)
		return QCRATE_DATA_PACKETIZER_BAD_SIZE;

	data_packets_per_frame =
		(config->frame_samples + (uint64_t)samples_per_packet - 1) /
		samples_per_packet;
	data_packets = data_packets_per_frame * config->frame_count;
	if (data_packets > UINT64_MAX - 2 ||
	    config->initial_sequence > UINT64_MAX - data_packets - 2)
		return QCRATE_DATA_PACKETIZER_SEQUENCE_OVERFLOW;
	total_samples = (uint64_t)config->frame_samples * config->frame_count;
	payload_bytes = total_samples * config->sample_bytes;
	sequence = config->initial_sequence;

	stream_info = (struct qcrate_data_stream_info) {
		.frame_samples = config->frame_samples,
		.sample_rate_numerator = config->sample_rate_numerator,
		.sample_rate_denominator = config->sample_rate_denominator,
		.timestamp_rate_numerator = config->timestamp_rate_numerator,
		.timestamp_rate_denominator = config->timestamp_rate_denominator,
		.center_frequency_hz = config->center_frequency_hz,
		.channel_count = config->channel_count,
		.component_bits = config->component_bits,
		.fraction_bits = config->fraction_bits,
		.timestamp_clock_id = config->timestamp_clock_id,
		.config_id = config->config_id,
	};
	if (qcrate_data_stream_info_encode(object, &stream_info) !=
	    QCRATE_DATA_CODEC_OK)
		return QCRATE_DATA_PACKETIZER_CODEC_ERROR;
	header = (struct qcrate_data_header) {
		.header_bytes = QCRATE_DATA_HEADER_BYTES,
		.packet_type = QCRATE_DATA_PACKET_STREAM_INFO,
		.payload_format = config->payload_format,
		.stream_id = config->stream_id,
		.run_id = config->run_id,
		.shot_id = config->shot_id,
		.packet_sequence = sequence++,
		.sample_bytes = config->sample_bytes,
	};
	status = emit_object(&header, object, sizeof(object), emit, emit_context);
	if (status)
		return status;

	result->first_data_sequence = sequence;
	for (frame = 0; frame < config->frame_count; frame++) {
		uint32_t offset = 0;

		while (offset < config->frame_samples) {
			uint32_t count = config->frame_samples - offset;
			bool first_sample = frame == 0 && offset == 0;
			const uint8_t *payload;
			size_t bytes;

			if (count > samples_per_packet)
				count = samples_per_packet;
			bytes = (size_t)count * config->sample_bytes;
			payload = samples + (size_t)frame * frame_bytes +
				(size_t)offset * config->sample_bytes;
			header = (struct qcrate_data_header) {
				.header_bytes = QCRATE_DATA_HEADER_BYTES,
				.packet_type = QCRATE_DATA_PACKET_DATA,
				.payload_format = config->payload_format,
				.flags = (first_sample &&
					  config->first_sample_timestamp_valid ?
					 QCRATE_DATA_FLAG_TIMESTAMP_VALID : 0) |
					(offset == 0 ?
					 QCRATE_DATA_FLAG_FRAME_START : 0) |
					(offset + count == config->frame_samples ?
					 QCRATE_DATA_FLAG_FRAME_END : 0),
				.stream_id = config->stream_id,
				.run_id = config->run_id,
				.shot_id = config->shot_id,
				.packet_sequence = sequence++,
				.frame_id = frame,
				.sample_offset = offset,
				.sample_count = (uint16_t)count,
				.sample_bytes = config->sample_bytes,
				.timestamp_ticks = first_sample &&
					config->first_sample_timestamp_valid ?
					config->first_sample_timestamp : 0,
			};
			status = emit_object(&header, payload, bytes,
					     emit, emit_context);
			if (status)
				return status;
			crc = crc32_update(crc, payload, bytes);
			result->data_packets++;
			offset += count;
		}
	}
	result->last_data_sequence = sequence - 1;
	result->payload_crc32 = crc ^ UINT32_MAX;

	shot_end = (struct qcrate_data_shot_end) {
		.status = QCRATE_DATA_SHOT_COMPLETE,
		.end_flags = QCRATE_DATA_FLAG_PAYLOAD_CRC_VALID |
			(config->end_of_stream ?
			 QCRATE_DATA_FLAG_END_OF_STREAM : 0),
		.total_frames = config->frame_count,
		.total_samples = total_samples,
		.total_data_packets = result->data_packets,
		.first_packet_sequence = result->first_data_sequence,
		.last_packet_sequence = result->last_data_sequence,
		.payload_bytes = payload_bytes,
		.payload_crc32 = result->payload_crc32,
	};
	if (qcrate_data_shot_end_encode(object, &shot_end) !=
	    QCRATE_DATA_CODEC_OK)
		return QCRATE_DATA_PACKETIZER_CODEC_ERROR;
	header = (struct qcrate_data_header) {
		.header_bytes = QCRATE_DATA_HEADER_BYTES,
		.packet_type = QCRATE_DATA_PACKET_SHOT_END,
		.payload_format = QCRATE_DATA_FORMAT_NONE,
		.stream_id = config->stream_id,
		.run_id = config->run_id,
		.shot_id = config->shot_id,
		.packet_sequence = sequence++,
	};
	status = emit_object(&header, object, sizeof(object), emit, emit_context);
	if (status)
		return status;

	result->total_datagrams = result->data_packets + 2;
	result->next_sequence = sequence;
	result->total_samples = total_samples;
	result->payload_bytes = payload_bytes;
	return QCRATE_DATA_PACKETIZER_OK;
}

int qcrate_data_emit_end_of_stream(
	uint32_t stream_id,
	uint64_t run_id,
	uint64_t last_shot_id,
	uint64_t packet_sequence,
	qcrate_data_emit_fn emit,
	void *emit_context,
	uint64_t *next_sequence)
{
	struct qcrate_data_header header = {
		.header_bytes = QCRATE_DATA_HEADER_BYTES,
		.packet_type = QCRATE_DATA_PACKET_HEARTBEAT,
		.payload_format = QCRATE_DATA_FORMAT_NONE,
		.flags = QCRATE_DATA_FLAG_END_OF_STREAM,
		.stream_id = stream_id,
		.run_id = run_id,
		.shot_id = last_shot_id,
		.packet_sequence = packet_sequence,
	};
	int status;

	if (!stream_id || !run_id || !last_shot_id || !emit || !next_sequence)
		return QCRATE_DATA_PACKETIZER_BAD_ARGUMENT;
	if (packet_sequence == UINT64_MAX)
		return QCRATE_DATA_PACKETIZER_SEQUENCE_OVERFLOW;
	status = emit_object(&header, NULL, 0, emit, emit_context);
	if (status)
		return status;
	*next_sequence = packet_sequence + 1;
	return QCRATE_DATA_PACKETIZER_OK;
}
