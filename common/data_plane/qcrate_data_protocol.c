/* SPDX-License-Identifier: MIT */
#include "qcrate_data_protocol.h"

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

static uint16_t get_u16(const uint8_t *input)
{
	return ((uint16_t)input[0] << 8) | input[1];
}

static uint32_t get_u32(const uint8_t *input)
{
	return ((uint32_t)input[0] << 24) |
	       ((uint32_t)input[1] << 16) |
	       ((uint32_t)input[2] << 8) |
	       input[3];
}

static uint64_t get_u64(const uint8_t *input)
{
	return ((uint64_t)get_u32(input) << 32) | get_u32(input + 4);
}

static int packet_type_valid(uint16_t packet_type)
{
	return packet_type >= QCRATE_DATA_PACKET_STREAM_INFO &&
	       packet_type <= QCRATE_DATA_PACKET_HEARTBEAT;
}

int qcrate_data_header_encode(
	uint8_t output[QCRATE_DATA_HEADER_BYTES],
	const struct qcrate_data_header *header)
{
	if (!output || !header)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;
	if (!packet_type_valid(header->packet_type))
		return QCRATE_DATA_CODEC_BAD_PACKET_TYPE;
	if (header->header_bytes != 0 &&
	    header->header_bytes != QCRATE_DATA_HEADER_BYTES)
		return QCRATE_DATA_CODEC_BAD_HEADER_SIZE;

	put_u32(output + 0, QCRATE_DATA_MAGIC);
	put_u16(output + 4, QCRATE_DATA_VERSION);
	put_u16(output + 6, QCRATE_DATA_HEADER_BYTES);
	put_u16(output + 8, header->packet_type);
	put_u16(output + 10, header->payload_format);
	put_u32(output + 12, header->flags);
	put_u32(output + 16, header->stream_id);
	put_u64(output + 20, header->run_id);
	put_u64(output + 28, header->shot_id);
	put_u64(output + 36, header->packet_sequence);
	put_u32(output + 44, header->frame_id);
	put_u32(output + 48, header->sample_offset);
	put_u16(output + 52, header->sample_count);
	put_u16(output + 54, header->sample_bytes);
	put_u64(output + 56, header->timestamp_ticks);
	return QCRATE_DATA_CODEC_OK;
}

int qcrate_data_header_decode(
	struct qcrate_data_header *header,
	const uint8_t *input,
	size_t input_bytes)
{
	uint16_t header_bytes;
	uint16_t packet_type;

	if (!header || !input)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;
	if (input_bytes < QCRATE_DATA_HEADER_BYTES)
		return QCRATE_DATA_CODEC_SHORT_INPUT;
	if (get_u32(input + 0) != QCRATE_DATA_MAGIC)
		return QCRATE_DATA_CODEC_BAD_MAGIC;
	if (get_u16(input + 4) != QCRATE_DATA_VERSION)
		return QCRATE_DATA_CODEC_BAD_VERSION;
	header_bytes = get_u16(input + 6);
	if (header_bytes != QCRATE_DATA_HEADER_BYTES || header_bytes > input_bytes)
		return QCRATE_DATA_CODEC_BAD_HEADER_SIZE;
	packet_type = get_u16(input + 8);
	if (!packet_type_valid(packet_type))
		return QCRATE_DATA_CODEC_BAD_PACKET_TYPE;

	header->header_bytes = header_bytes;
	header->packet_type = packet_type;
	header->payload_format = get_u16(input + 10);
	header->flags = get_u32(input + 12);
	header->stream_id = get_u32(input + 16);
	header->run_id = get_u64(input + 20);
	header->shot_id = get_u64(input + 28);
	header->packet_sequence = get_u64(input + 36);
	header->frame_id = get_u32(input + 44);
	header->sample_offset = get_u32(input + 48);
	header->sample_count = get_u16(input + 52);
	header->sample_bytes = get_u16(input + 54);
	header->timestamp_ticks = get_u64(input + 56);
	return QCRATE_DATA_CODEC_OK;
}

int qcrate_data_stream_info_encode(
	uint8_t output[QCRATE_DATA_STREAM_INFO_BYTES],
	const struct qcrate_data_stream_info *info)
{
	if (!output || !info)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;

	put_u16(output + 0, QCRATE_DATA_VERSION);
	put_u16(output + 2, QCRATE_DATA_STREAM_INFO_BYTES);
	put_u32(output + 4, info->frame_samples);
	put_u64(output + 8, info->sample_rate_numerator);
	put_u64(output + 16, info->sample_rate_denominator);
	put_u64(output + 24, info->timestamp_rate_numerator);
	put_u64(output + 32, info->timestamp_rate_denominator);
	put_u64(output + 40, (uint64_t)info->center_frequency_hz);
	put_u16(output + 48, info->channel_count);
	put_u16(output + 50, info->component_bits);
	put_u16(output + 52, info->fraction_bits);
	put_u16(output + 54, info->timestamp_clock_id);
	put_u64(output + 56, info->config_id);
	return QCRATE_DATA_CODEC_OK;
}

int qcrate_data_stream_info_decode(
	struct qcrate_data_stream_info *info,
	const uint8_t *input,
	size_t input_bytes)
{
	if (!info || !input)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;
	if (input_bytes < QCRATE_DATA_STREAM_INFO_BYTES)
		return QCRATE_DATA_CODEC_SHORT_INPUT;
	if (get_u16(input + 0) != QCRATE_DATA_VERSION)
		return QCRATE_DATA_CODEC_BAD_VERSION;
	if (get_u16(input + 2) != QCRATE_DATA_STREAM_INFO_BYTES)
		return QCRATE_DATA_CODEC_BAD_OBJECT_SIZE;

	info->frame_samples = get_u32(input + 4);
	info->sample_rate_numerator = get_u64(input + 8);
	info->sample_rate_denominator = get_u64(input + 16);
	info->timestamp_rate_numerator = get_u64(input + 24);
	info->timestamp_rate_denominator = get_u64(input + 32);
	info->center_frequency_hz = (int64_t)get_u64(input + 40);
	info->channel_count = get_u16(input + 48);
	info->component_bits = get_u16(input + 50);
	info->fraction_bits = get_u16(input + 52);
	info->timestamp_clock_id = get_u16(input + 54);
	info->config_id = get_u64(input + 56);
	return QCRATE_DATA_CODEC_OK;
}

int qcrate_data_shot_end_encode(
	uint8_t output[QCRATE_DATA_SHOT_END_BYTES],
	const struct qcrate_data_shot_end *end)
{
	if (!output || !end)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;

	put_u32(output + 0, end->status);
	put_u32(output + 4, end->end_flags);
	put_u32(output + 8, end->total_frames);
	put_u32(output + 12, 0);
	put_u64(output + 16, end->total_samples);
	put_u64(output + 24, end->total_data_packets);
	put_u64(output + 32, end->first_packet_sequence);
	put_u64(output + 40, end->last_packet_sequence);
	put_u64(output + 48, end->payload_bytes);
	put_u32(output + 56, end->payload_crc32);
	put_u32(output + 60, 0);
	return QCRATE_DATA_CODEC_OK;
}

int qcrate_data_shot_end_decode(
	struct qcrate_data_shot_end *end,
	const uint8_t *input,
	size_t input_bytes)
{
	if (!end || !input)
		return QCRATE_DATA_CODEC_BAD_ARGUMENT;
	if (input_bytes < QCRATE_DATA_SHOT_END_BYTES)
		return QCRATE_DATA_CODEC_SHORT_INPUT;
	if (get_u32(input + 12) != 0 || get_u32(input + 60) != 0)
		return QCRATE_DATA_CODEC_BAD_OBJECT_SIZE;

	end->status = get_u32(input + 0);
	end->end_flags = get_u32(input + 4);
	end->total_frames = get_u32(input + 8);
	end->total_samples = get_u64(input + 16);
	end->total_data_packets = get_u64(input + 24);
	end->first_packet_sequence = get_u64(input + 32);
	end->last_packet_sequence = get_u64(input + 40);
	end->payload_bytes = get_u64(input + 48);
	end->payload_crc32 = get_u32(input + 56);
	return QCRATE_DATA_CODEC_OK;
}
