/* SPDX-License-Identifier: MIT */
#define _GNU_SOURCE

#include "qcrate_data_protocol.h"
#include "qcrate_run_format.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <limits.h>
#include <netdb.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifndef SO_RXQ_OVFL
#define SO_RXQ_OVFL 40
#endif

#define QCRATE_DEFAULT_PORT 47000U
#define QCRATE_DEFAULT_RCVBUF (16U * 1024U * 1024U)
#define QCRATE_DEFAULT_WAIT_MS 30000U
#define QCRATE_DEFAULT_IDLE_MS 2000U
#define QCRATE_DEFAULT_GRACE_MS 250U
#define QCRATE_DEFAULT_MAX_SHOT (64U * 1024U * 1024U)
#define QCRATE_RECEIVE_BATCH 64U
#define QCRATE_MAX_ACTIVE_SHOTS 256U
#define QCRATE_MAX_UDP_DATAGRAM 65535U
#define QCRATE_MAX_PENDING_PACKETS 4096U

struct recorder_options {
	const char *bind_address;
	const char *source_address;
	const char *output;
	uint16_t port;
	uint32_t receive_buffer;
	uint32_t wait_timeout_ms;
	uint32_t idle_timeout_ms;
	uint32_t end_grace_ms;
	size_t max_shot_bytes;
};

struct source_identity {
	int family;
	uint8_t address[16];
	uint16_t port;
};

struct packet_record {
	struct qcrate_data_header header;
	uint64_t receive_ns;
	size_t bytes;
	uint8_t *datagram;
};

struct pending_record {
	struct source_identity source;
	uint64_t receive_ns;
	size_t bytes;
	uint8_t *datagram;
};

struct shot_state {
	uint64_t shot_id;
	struct packet_record *packets;
	size_t packet_count;
	size_t packet_capacity;
	uint64_t first_receive_ns;
	uint64_t last_receive_ns;
	uint64_t highest_arrival_sequence;
	bool highest_arrival_valid;
	bool have_info;
	bool have_end;
	struct qcrate_data_header info_header;
	struct qcrate_data_stream_info info;
	struct qcrate_data_header end_header;
	struct qcrate_data_shot_end end;
	uint32_t issue_flags;
	uint32_t datagrams;
	uint32_t duplicate_packets;
	uint32_t reordered_packets;
	uint32_t conflicting_packets;
};

struct run_state {
	struct recorder_options options;
	int socket_fd;
	FILE *journal;
	FILE *samples;
	FILE *index;
	uint64_t sample_offset;
	struct source_identity selected_source;
	struct source_identity source_filter;
	bool source_filter_valid;
	bool selected;
	uint64_t run_id;
	uint32_t stream_id;
	bool index_header_written;
	bool profile_valid;
	struct qcrate_data_header profile_header;
	struct qcrate_data_stream_info profile;
	struct shot_state *shots[QCRATE_MAX_ACTIVE_SHOTS];
	size_t shot_count;
	struct pending_record *pending;
	size_t pending_count;
	size_t pending_capacity;
	uint64_t pending_overflow;
	uint64_t expected_sequence;
	bool expected_sequence_valid;
	uint64_t last_shot_id;
	uint64_t last_timestamp;
	bool last_shot_valid;
	bool terminal_seen;
	bool terminal_is_shot_end;
	uint64_t terminal_sequence;
	uint64_t terminal_shot_id;
	uint64_t terminal_seen_ns;
	bool run_continuity_error;
	uint64_t listen_ns;
	uint64_t start_ns;
	uint64_t end_ns;
	uint64_t start_cpu_ns;
	uint64_t end_cpu_ns;
	uint64_t last_datagram_ns;
	uint64_t journal_records;
	uint64_t datagram_bytes;
	uint64_t valid_packets;
	uint64_t malformed_packets;
	uint64_t foreign_packets;
	uint64_t late_packets;
	uint64_t complete_shots;
	uint64_t incomplete_shots;
	uint64_t duplicate_packets;
	uint64_t reordered_packets;
	uint64_t conflicting_packets;
	uint32_t kernel_drops;
	int actual_receive_buffer;
	bool interrupted;
	uint8_t *receive_storage;
};

static volatile sig_atomic_t stop_requested;

static void handle_signal(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

static uint64_t monotonic_ns(void)
{
	struct timespec value;

	if (clock_gettime(CLOCK_MONOTONIC, &value))
		return 0;
	return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
	       (uint64_t)value.tv_nsec;
}

static uint64_t process_cpu_ns(void)
{
	struct timespec value;

	if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &value))
		return 0;
	return (uint64_t)value.tv_sec * UINT64_C(1000000000) +
	       (uint64_t)value.tv_nsec;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t bytes)
{
	size_t index;
	unsigned int bit;

	for (index = 0; index < bytes; index++) {
		crc ^= data[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				(UINT32_C(0xedb88320) &
				 (uint32_t)-(int32_t)(crc & 1));
	}
	return crc;
}

static int parse_u32(const char *text, uint32_t minimum, uint32_t maximum,
		     uint32_t *output)
{
	char *end = NULL;
	unsigned long value;

	errno = 0;
	value = strtoul(text, &end, 0);
	if (errno || !end || *end || value < minimum || value > maximum)
		return -1;
	*output = (uint32_t)value;
	return 0;
}

static int parse_size(const char *text, size_t *output)
{
	char *end = NULL;
	unsigned long long value;
	unsigned long long multiplier = 1;

	errno = 0;
	value = strtoull(text, &end, 0);
	if (errno || !end || end == text)
		return -1;
	if (*end == 'K' || *end == 'k') {
		multiplier = 1024;
		end++;
	} else if (*end == 'M' || *end == 'm') {
		multiplier = 1024 * 1024;
		end++;
	}
	if (*end || !value || value > SIZE_MAX / multiplier)
		return -1;
	*output = (size_t)(value * multiplier);
	return 0;
}

static void usage(FILE *stream, const char *program)
{
	fprintf(stream,
		"Usage: %s --output DIR [OPTIONS]\n"
		"\n"
		"Receive a sustained Q-Crate Data Plane v1 run.\n"
		"\n"
		"  --bind ADDRESS          local bind address (default 0.0.0.0)\n"
		"  --port PORT             UDP port (default 47000)\n"
		"  --source ADDRESS        accept only this sender address\n"
		"  --output DIR            new run directory\n"
		"  --receive-buffer BYTES  requested SO_RCVBUF (default 16M)\n"
		"  --wait-timeout-ms N     first-packet timeout (default 30000)\n"
		"  --idle-timeout-ms N     unterminated-run timeout (default 2000)\n"
		"  --end-grace-ms N        reordering grace after EOS (default 250)\n"
		"  --max-shot-bytes BYTES  reconstruction bound (default 64M)\n"
		"  -h, --help              show this help\n",
		program);
}

static int parse_options(int argc, char **argv, struct recorder_options *options)
{
	enum {
		OPT_BIND = 1000,
		OPT_PORT,
		OPT_SOURCE,
		OPT_OUTPUT,
		OPT_RECEIVE_BUFFER,
		OPT_WAIT_TIMEOUT,
		OPT_IDLE_TIMEOUT,
		OPT_END_GRACE,
		OPT_MAX_SHOT,
	};
	static const struct option long_options[] = {
		{"bind", required_argument, NULL, OPT_BIND},
		{"port", required_argument, NULL, OPT_PORT},
		{"source", required_argument, NULL, OPT_SOURCE},
		{"output", required_argument, NULL, OPT_OUTPUT},
		{"receive-buffer", required_argument, NULL, OPT_RECEIVE_BUFFER},
		{"wait-timeout-ms", required_argument, NULL, OPT_WAIT_TIMEOUT},
		{"idle-timeout-ms", required_argument, NULL, OPT_IDLE_TIMEOUT},
		{"end-grace-ms", required_argument, NULL, OPT_END_GRACE},
		{"max-shot-bytes", required_argument, NULL, OPT_MAX_SHOT},
		{"help", no_argument, NULL, 'h'},
		{NULL, 0, NULL, 0},
	};
	uint32_t value;
	size_t size_value;
	int option;

	*options = (struct recorder_options) {
		.bind_address = "0.0.0.0",
		.port = QCRATE_DEFAULT_PORT,
		.receive_buffer = QCRATE_DEFAULT_RCVBUF,
		.wait_timeout_ms = QCRATE_DEFAULT_WAIT_MS,
		.idle_timeout_ms = QCRATE_DEFAULT_IDLE_MS,
		.end_grace_ms = QCRATE_DEFAULT_GRACE_MS,
		.max_shot_bytes = QCRATE_DEFAULT_MAX_SHOT,
	};
	while ((option = getopt_long(argc, argv, "h", long_options, NULL)) != -1) {
		switch (option) {
		case OPT_BIND:
			options->bind_address = optarg;
			break;
		case OPT_PORT:
			if (parse_u32(optarg, 1, UINT16_MAX, &value))
				return -1;
			options->port = (uint16_t)value;
			break;
		case OPT_SOURCE:
			options->source_address = optarg;
			break;
		case OPT_OUTPUT:
			options->output = optarg;
			break;
		case OPT_RECEIVE_BUFFER:
			if (parse_size(optarg, &size_value))
				return -1;
			if (size_value > INT_MAX)
				return -1;
			options->receive_buffer = (uint32_t)size_value;
			break;
		case OPT_WAIT_TIMEOUT:
			if (parse_u32(optarg, 1, UINT32_MAX, &options->wait_timeout_ms))
				return -1;
			break;
		case OPT_IDLE_TIMEOUT:
			if (parse_u32(optarg, 1, UINT32_MAX, &options->idle_timeout_ms))
				return -1;
			break;
		case OPT_END_GRACE:
			if (parse_u32(optarg, 0, UINT32_MAX, &options->end_grace_ms))
				return -1;
			break;
		case OPT_MAX_SHOT:
			if (parse_size(optarg, &options->max_shot_bytes))
				return -1;
			break;
		case 'h':
			usage(stdout, argv[0]);
			exit(EXIT_SUCCESS);
		default:
			return -1;
		}
	}
	return options->output && optind == argc ? 0 : -1;
}

static int source_from_sockaddr(const struct sockaddr_storage *storage,
				struct source_identity *source)
{
	memset(source, 0, sizeof(*source));
	if (storage->ss_family == AF_INET) {
		const struct sockaddr_in *address =
			(const struct sockaddr_in *)storage;

		source->family = AF_INET;
		memcpy(source->address, &address->sin_addr, 4);
		source->port = ntohs(address->sin_port);
		return 0;
	}
	if (storage->ss_family == AF_INET6) {
		const struct sockaddr_in6 *address =
			(const struct sockaddr_in6 *)storage;

		source->family = AF_INET6;
		memcpy(source->address, &address->sin6_addr, 16);
		source->port = ntohs(address->sin6_port);
		return 0;
	}
	return -1;
}

static bool source_equal(const struct source_identity *left,
			 const struct source_identity *right, bool compare_port)
{
	size_t bytes;

	if (left->family != right->family)
		return false;
	bytes = left->family == AF_INET ? 4 : 16;
	return (!compare_port || left->port == right->port) &&
	       memcmp(left->address, right->address, bytes) == 0;
}

static int parse_source_filter(const char *text, struct source_identity *source)
{
	memset(source, 0, sizeof(*source));
	if (inet_pton(AF_INET, text, source->address) == 1) {
		source->family = AF_INET;
		return 0;
	}
	if (inet_pton(AF_INET6, text, source->address) == 1) {
		source->family = AF_INET6;
		return 0;
	}
	return -1;
}

static int write_exact(FILE *stream, const void *data, size_t bytes)
{
	return fwrite(data, 1, bytes, stream) == bytes ? 0 : -1;
}

static int journal_datagram(struct run_state *run,
			    const struct source_identity *source,
			    uint64_t receive_ns,
			    const uint8_t *datagram, size_t bytes)
{
	uint8_t header[QCRATE_JOURNAL_RECORD_BYTES];
	uint8_t family = source->family == AF_INET ? 4 : 6;

	if (!run->journal_records) {
		run->start_ns = receive_ns;
		run->start_cpu_ns = process_cpu_ns();
	}
	if (bytes > UINT16_MAX ||
	    qcrate_journal_record_encode(header, (uint16_t)bytes, family,
					 source->port, receive_ns,
					 source->address) ||
	    write_exact(run->journal, header, sizeof(header)) ||
	    write_exact(run->journal, datagram, bytes))
		return -1;
	run->journal_records++;
	run->datagram_bytes += bytes;
	if (!(run->journal_records % 256) && fflush(run->journal))
		return -1;
	return 0;
}

static struct shot_state *find_shot(struct run_state *run, uint64_t shot_id)
{
	size_t index;

	for (index = 0; index < run->shot_count; index++)
		if (run->shots[index]->shot_id == shot_id)
			return run->shots[index];
	return NULL;
}

static struct shot_state *create_shot(struct run_state *run, uint64_t shot_id)
{
	struct shot_state *shot;

	if (run->shot_count == QCRATE_MAX_ACTIVE_SHOTS)
		return NULL;
	shot = calloc(1, sizeof(*shot));
	if (!shot)
		return NULL;
	shot->shot_id = shot_id;
	run->shots[run->shot_count++] = shot;
	return shot;
}

static void free_shot(struct shot_state *shot)
{
	size_t index;

	if (!shot)
		return;
	for (index = 0; index < shot->packet_count; index++)
		free(shot->packets[index].datagram);
	free(shot->packets);
	free(shot);
}

static int add_packet(struct shot_state *shot,
		      const struct qcrate_data_header *header,
		      uint64_t receive_ns, const uint8_t *datagram, size_t bytes)
{
	struct packet_record *packet;
	size_t index;

	shot->datagrams++;
	if (!shot->first_receive_ns)
		shot->first_receive_ns = receive_ns;
	shot->last_receive_ns = receive_ns;
	for (index = 0; index < shot->packet_count; index++) {
		packet = &shot->packets[index];
		if (packet->header.packet_sequence != header->packet_sequence)
			continue;
		if (packet->bytes == bytes && !memcmp(packet->datagram, datagram, bytes))
			shot->duplicate_packets++;
		else {
			shot->conflicting_packets++;
			shot->issue_flags |= QCRATE_INDEX_ISSUE_CONFLICT;
		}
		return 1;
	}
	if (shot->packet_count == shot->packet_capacity) {
		size_t capacity = shot->packet_capacity ? shot->packet_capacity * 2 : 32;
		void *grown;

		if (capacity > SIZE_MAX / sizeof(*shot->packets))
			return -1;
		grown = realloc(shot->packets, capacity * sizeof(*shot->packets));
		if (!grown)
			return -1;
		shot->packets = grown;
		shot->packet_capacity = capacity;
	}
	packet = &shot->packets[shot->packet_count++];
	memset(packet, 0, sizeof(*packet));
	packet->datagram = malloc(bytes);
	if (!packet->datagram)
		return -1;
	memcpy(packet->datagram, datagram, bytes);
	packet->bytes = bytes;
	packet->receive_ns = receive_ns;
	packet->header = *header;
	if (shot->highest_arrival_valid &&
	    header->packet_sequence < shot->highest_arrival_sequence)
		shot->reordered_packets++;
	if (!shot->highest_arrival_valid ||
	    header->packet_sequence > shot->highest_arrival_sequence) {
		shot->highest_arrival_sequence = header->packet_sequence;
		shot->highest_arrival_valid = true;
	}
	return 0;
}

static int packet_sequence_compare(const void *left, const void *right)
{
	const struct packet_record *a = left;
	const struct packet_record *b = right;

	if (a->header.packet_sequence < b->header.packet_sequence)
		return -1;
	if (a->header.packet_sequence > b->header.packet_sequence)
		return 1;
	return 0;
}

static int packet_frame_compare(const void *left, const void *right)
{
	const struct packet_record *const *a = left;
	const struct packet_record *const *b = right;

	if ((*a)->header.frame_id != (*b)->header.frame_id)
		return (*a)->header.frame_id < (*b)->header.frame_id ? -1 : 1;
	if ((*a)->header.sample_offset != (*b)->header.sample_offset)
		return (*a)->header.sample_offset < (*b)->header.sample_offset ? -1 : 1;
	return packet_sequence_compare(*a, *b);
}

static bool stream_info_equal(const struct qcrate_data_stream_info *left,
			      const struct qcrate_data_stream_info *right)
{
	return left->frame_samples == right->frame_samples &&
	       left->sample_rate_numerator == right->sample_rate_numerator &&
	       left->sample_rate_denominator == right->sample_rate_denominator &&
	       left->timestamp_rate_numerator == right->timestamp_rate_numerator &&
	       left->timestamp_rate_denominator == right->timestamp_rate_denominator &&
	       left->center_frequency_hz == right->center_frequency_hz &&
	       left->channel_count == right->channel_count &&
	       left->component_bits == right->component_bits &&
	       left->fraction_bits == right->fraction_bits &&
	       left->timestamp_clock_id == right->timestamp_clock_id &&
	       left->config_id == right->config_id;
}

static bool shot_end_equal(const struct qcrate_data_shot_end *left,
			   const struct qcrate_data_shot_end *right)
{
	return left->status == right->status &&
	       left->end_flags == right->end_flags &&
	       left->total_frames == right->total_frames &&
	       left->total_samples == right->total_samples &&
	       left->total_data_packets == right->total_data_packets &&
	       left->first_packet_sequence == right->first_packet_sequence &&
	       left->last_packet_sequence == right->last_packet_sequence &&
	       left->payload_bytes == right->payload_bytes &&
	       left->payload_crc32 == right->payload_crc32;
}

static bool shot_sequence_complete(const struct shot_state *shot)
{
	uint64_t expected;
	size_t index;

	if (!shot->have_info || !shot->have_end ||
	    shot->end_header.packet_sequence < shot->info_header.packet_sequence)
		return false;
	expected = shot->info_header.packet_sequence;
	for (index = 0; index < shot->packet_count; index++) {
		uint64_t sequence = shot->packets[index].header.packet_sequence;

		if (sequence < shot->info_header.packet_sequence ||
		    sequence > shot->end_header.packet_sequence)
			continue;
		if (sequence != expected)
			return false;
		expected++;
	}
	return expected == shot->end_header.packet_sequence + 1;
}

static uint32_t count_missing(const struct shot_state *shot)
{
	uint64_t expected;
	uint64_t missing = 0;
	size_t index;

	if (!shot->have_info || !shot->have_end ||
	    shot->end_header.packet_sequence < shot->info_header.packet_sequence)
		return 1;
	expected = shot->info_header.packet_sequence;
	for (index = 0; index < shot->packet_count; index++) {
		uint64_t sequence = shot->packets[index].header.packet_sequence;

		if (sequence < expected || sequence > shot->end_header.packet_sequence)
			continue;
		if (sequence > expected)
			missing += sequence - expected;
		expected = sequence + 1;
	}
	if (expected <= shot->end_header.packet_sequence)
		missing += shot->end_header.packet_sequence - expected + 1;
	return missing > UINT32_MAX ? UINT32_MAX : (uint32_t)missing;
}

static int write_index_record(struct run_state *run,
			      const struct qcrate_index_record *record)
{
	uint8_t encoded[QCRATE_INDEX_RECORD_BYTES];

	if (qcrate_index_record_encode(encoded, record) ||
	    write_exact(run->index, encoded, sizeof(encoded)) ||
	    fflush(run->index))
		return -1;
	return 0;
}

static int finalize_shot(struct run_state *run, struct shot_state *shot,
			 bool forced)
{
	struct qcrate_index_record index_record = {0};
	struct packet_record **data_packets = NULL;
	uint8_t *samples = NULL;
	uint64_t total_samples = 0;
	uint64_t payload_bytes = 0;
	uint64_t first_data_sequence = 0;
	uint64_t last_data_sequence = 0;
	uint64_t first_timestamp = 0;
	uint32_t crc = UINT32_MAX;
	uint32_t source_flags = 0;
	uint32_t frame_count = 0;
	uint32_t missing;
	size_t data_count = 0;
	size_t index;
	size_t output_offset = 0;
	int result = -1;

	qsort(shot->packets, shot->packet_count, sizeof(*shot->packets),
	      packet_sequence_compare);
	missing = count_missing(shot);
	if (missing) {
		shot->issue_flags |= QCRATE_INDEX_ISSUE_MISSING;
		if (!forced)
			return 1;
	}
	if (!shot->have_info || !shot->have_end)
		shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
	if (shot->have_info &&
	    (!shot->info.frame_samples ||
	     !shot->info.sample_rate_denominator ||
	     !shot->info.timestamp_rate_denominator ||
	     (shot->info_header.payload_format != QCRATE_DATA_FORMAT_COUNTER_U32_LE &&
	      shot->info_header.payload_format != QCRATE_DATA_FORMAT_IQ_S16_LE) ||
	     shot->info_header.sample_bytes != 4))
		shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
	if (shot->have_end && shot->end.status != QCRATE_DATA_SHOT_COMPLETE)
		shot->issue_flags |= QCRATE_INDEX_ISSUE_SENDER;

	if (shot->packet_count) {
		data_packets = calloc(shot->packet_count, sizeof(*data_packets));
		if (!data_packets)
			goto out;
	}
	for (index = 0; index < shot->packet_count; index++) {
		struct packet_record *packet = &shot->packets[index];
		const struct qcrate_data_header *header = &packet->header;
		size_t payload_size = packet->bytes - header->header_bytes;

		if (header->packet_type == QCRATE_DATA_PACKET_DATA) {
			if (!header->sample_count || header->sample_bytes != 4 ||
			    header->payload_format != shot->info_header.payload_format ||
			    (header->flags &
			     ~(QCRATE_DATA_FLAG_TIMESTAMP_VALID |
			       QCRATE_DATA_FLAG_FRAME_START |
			       QCRATE_DATA_FLAG_FRAME_END |
			       QCRATE_DATA_FLAG_DISCONTINUITY_BEFORE |
			       QCRATE_DATA_FLAG_SOURCE_OVERFLOW |
			       QCRATE_DATA_FLAG_SENDER_DROP)) ||
			    payload_size !=
				(size_t)header->sample_count * header->sample_bytes) {
				shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
				continue;
			}
			if (data_count == 0) {
				first_data_sequence = header->packet_sequence;
				if (!(header->flags & QCRATE_DATA_FLAG_TIMESTAMP_VALID) ||
				    !header->timestamp_ticks)
					shot->issue_flags |= QCRATE_INDEX_ISSUE_TIMESTAMP;
				first_timestamp = header->timestamp_ticks;
			} else if ((header->flags & QCRATE_DATA_FLAG_TIMESTAMP_VALID) ||
				   header->timestamp_ticks) {
				shot->issue_flags |= QCRATE_INDEX_ISSUE_TIMESTAMP;
			}
			last_data_sequence = header->packet_sequence;
			total_samples += header->sample_count;
			payload_bytes += payload_size;
			source_flags |= header->flags &
				(QCRATE_DATA_FLAG_DISCONTINUITY_BEFORE |
				 QCRATE_DATA_FLAG_SOURCE_OVERFLOW |
				 QCRATE_DATA_FLAG_SENDER_DROP);
			data_packets[data_count++] = packet;
		} else if (header->packet_type == QCRATE_DATA_PACKET_STREAM_INFO) {
			if (packet->bytes != QCRATE_DATA_HEADER_BYTES +
					     QCRATE_DATA_STREAM_INFO_BYTES ||
			    header->flags || header->frame_id ||
			    header->sample_offset || header->sample_count ||
			    header->timestamp_ticks)
				shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		} else if (header->packet_type == QCRATE_DATA_PACKET_SHOT_END) {
			if (packet->bytes != QCRATE_DATA_HEADER_BYTES +
					     QCRATE_DATA_SHOT_END_BYTES ||
			    header->payload_format != QCRATE_DATA_FORMAT_NONE ||
			    header->flags || header->frame_id ||
			    header->sample_offset || header->sample_count ||
			    header->sample_bytes || header->timestamp_ticks)
				shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		} else {
			shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		}
	}
	if (!data_count || payload_bytes > run->options.max_shot_bytes ||
	    payload_bytes > SIZE_MAX)
		shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
	if (source_flags)
		shot->issue_flags |= QCRATE_INDEX_ISSUE_SENDER;

	if (data_count) {
		uint32_t current_frame = UINT32_MAX;
		uint32_t expected_offset = 0;

		qsort(data_packets, data_count, sizeof(*data_packets),
		      packet_frame_compare);
		if (data_packets[0]->header.frame_id != 0)
			shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
		for (index = 0; index < data_count; index++) {
			struct packet_record *packet = data_packets[index];
			const struct qcrate_data_header *header = &packet->header;
			const uint8_t *payload = packet->datagram + header->header_bytes;
			size_t bytes = packet->bytes - header->header_bytes;

			if (header->frame_id != current_frame) {
				if (current_frame != UINT32_MAX &&
				    (header->frame_id != current_frame + 1 ||
				     expected_offset != shot->info.frame_samples))
					shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
				current_frame = header->frame_id;
				expected_offset = 0;
				frame_count++;
				if (!(header->flags & QCRATE_DATA_FLAG_FRAME_START))
					shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
			}
			if (header->sample_offset != expected_offset)
				shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
			expected_offset = header->sample_offset + header->sample_count;
			if ((header->flags & QCRATE_DATA_FLAG_FRAME_END) !=
			    (expected_offset == shot->info.frame_samples ?
			     QCRATE_DATA_FLAG_FRAME_END : 0))
				shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
			crc = crc32_update(crc, payload, bytes);
		}
		if (expected_offset != shot->info.frame_samples)
			shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
	}
	crc ^= UINT32_MAX;
	if (shot->have_end) {
		if (shot->end.end_flags &
		    ~(QCRATE_DATA_FLAG_PAYLOAD_CRC_VALID |
		      QCRATE_DATA_FLAG_END_OF_STREAM))
			shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		if (shot->end.total_frames != frame_count ||
		    shot->end.total_samples != total_samples ||
		    shot->end.total_data_packets != data_count ||
		    shot->end.payload_bytes != payload_bytes ||
		    (data_count &&
		     (shot->end.first_packet_sequence != first_data_sequence ||
		      shot->end.last_packet_sequence != last_data_sequence)))
			shot->issue_flags |= QCRATE_INDEX_ISSUE_FRAME;
		if (!(shot->end.end_flags & QCRATE_DATA_FLAG_PAYLOAD_CRC_VALID) ||
		    shot->end.payload_crc32 != crc)
			shot->issue_flags |= QCRATE_INDEX_ISSUE_CRC;
	}
	if (run->profile_valid && shot->have_info &&
	    (!stream_info_equal(&run->profile, &shot->info) ||
	     run->profile_header.payload_format != shot->info_header.payload_format ||
	     run->profile_header.sample_bytes != shot->info_header.sample_bytes))
		shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
	if (run->expected_sequence_valid && shot->have_info &&
	    shot->info_header.packet_sequence != run->expected_sequence)
		shot->issue_flags |= QCRATE_INDEX_ISSUE_CONTINUITY;
	if (run->last_shot_valid &&
	    (shot->shot_id <= run->last_shot_id ||
	     !first_timestamp || first_timestamp <= run->last_timestamp))
		shot->issue_flags |= QCRATE_INDEX_ISSUE_CONTINUITY;

	index_record = (struct qcrate_index_record) {
		.shot_id = shot->shot_id,
		.state = shot->issue_flags ? QCRATE_INDEX_INCOMPLETE :
			 QCRATE_INDEX_COMPLETE,
		.issue_flags = shot->issue_flags,
		.first_packet_sequence = shot->have_info ?
			shot->info_header.packet_sequence : 0,
		.last_packet_sequence = shot->have_end ?
			shot->end_header.packet_sequence : 0,
		.first_sample_timestamp = first_timestamp,
		.first_receive_ns = shot->first_receive_ns,
		.last_receive_ns = shot->last_receive_ns,
		.sample_file_offset = shot->issue_flags ? UINT64_MAX :
			run->sample_offset,
		.sample_bytes = shot->issue_flags ? 0 : payload_bytes,
		.frame_count = frame_count,
		.frame_samples = shot->have_info ? shot->info.frame_samples : 0,
		.data_packets = data_count > UINT32_MAX ? UINT32_MAX :
			(uint32_t)data_count,
		.datagrams = shot->datagrams,
		.duplicate_packets = shot->duplicate_packets,
		.reordered_packets = shot->reordered_packets,
		.missing_packets = missing,
		.payload_crc32 = crc,
		.source_flags = source_flags,
		.payload_format = shot->have_info ?
			shot->info_header.payload_format : 0,
		.sample_bytes_per_word = shot->have_info ?
			shot->info_header.sample_bytes : 0,
		.config_id = shot->have_info ? shot->info.config_id : 0,
	};
	if (!shot->issue_flags) {
		samples = malloc((size_t)payload_bytes);
		if (!samples)
			goto out;
		for (index = 0; index < data_count; index++) {
			struct packet_record *packet = data_packets[index];
			size_t bytes = packet->bytes - packet->header.header_bytes;

			memcpy(samples + output_offset,
			       packet->datagram + packet->header.header_bytes, bytes);
			output_offset += bytes;
		}
		if (write_exact(run->samples, samples, (size_t)payload_bytes) ||
		    fflush(run->samples))
			goto out;
		run->sample_offset += payload_bytes;
		run->complete_shots++;
	} else {
		run->incomplete_shots++;
	}
	if (write_index_record(run, &index_record))
		goto out;
	if (shot->have_end) {
		run->expected_sequence = shot->end_header.packet_sequence + 1;
		run->expected_sequence_valid =
			shot->end_header.packet_sequence != UINT64_MAX;
	}
	if (first_timestamp) {
		run->last_timestamp = first_timestamp;
		run->last_shot_id = shot->shot_id;
		run->last_shot_valid = true;
	}
	run->duplicate_packets += shot->duplicate_packets;
	run->reordered_packets += shot->reordered_packets;
	run->conflicting_packets += shot->conflicting_packets;
	result = 0;
out:
	free(samples);
	free(data_packets);
	return result;
}

static size_t lowest_sequence_shot(const struct run_state *run)
{
	size_t lowest = 0;
	size_t index;
	uint64_t sequence = UINT64_MAX;

	for (index = 0; index < run->shot_count; index++) {
		const struct shot_state *shot = run->shots[index];
		uint64_t candidate = shot->have_info ?
			shot->info_header.packet_sequence : UINT64_MAX;

		if (candidate < sequence) {
			sequence = candidate;
			lowest = index;
		}
	}
	return lowest;
}

static int remove_and_finalize(struct run_state *run, size_t position, bool forced)
{
	struct shot_state *shot = run->shots[position];
	int status = finalize_shot(run, shot, forced);

	if (status)
		return status;
	free_shot(shot);
	memmove(&run->shots[position], &run->shots[position + 1],
		(run->shot_count - position - 1) * sizeof(run->shots[0]));
	run->shot_count--;
	return 0;
}

static int finalize_ready(struct run_state *run)
{
	while (run->shot_count) {
		size_t position = lowest_sequence_shot(run);
		struct shot_state *shot = run->shots[position];
		int status;

		qsort(shot->packets, shot->packet_count, sizeof(*shot->packets),
		      packet_sequence_compare);
		if (!shot_sequence_complete(shot))
			return 0;
		status = remove_and_finalize(run, position, false);
		if (status)
			return status < 0 ? -1 : 0;
	}
	return 0;
}

static int finalize_remaining(struct run_state *run)
{
	while (run->shot_count) {
		size_t position = lowest_sequence_shot(run);

		if (remove_and_finalize(run, position, true) < 0)
			return -1;
	}
	return 0;
}

static int select_run(struct run_state *run,
		      const struct source_identity *source,
		      const struct qcrate_data_header *header)
{
	uint8_t encoded[QCRATE_INDEX_HEADER_BYTES];

	run->selected = true;
	run->selected_source = *source;
	run->run_id = header->run_id;
	run->stream_id = header->stream_id;
	qcrate_index_header_encode(encoded, run->run_id, run->stream_id);
	if (write_exact(run->index, encoded, sizeof(encoded)) || fflush(run->index))
		return -1;
	run->index_header_written = true;
	return 0;
}

static int save_pending(struct run_state *run,
			const struct source_identity *source,
			uint64_t receive_ns, const uint8_t *datagram, size_t bytes)
{
	struct pending_record *record;

	if (run->pending_count == QCRATE_MAX_PENDING_PACKETS) {
		run->pending_overflow++;
		return 0;
	}
	if (run->pending_count == run->pending_capacity) {
		size_t capacity = run->pending_capacity ?
			run->pending_capacity * 2 : 64;
		void *grown;

		if (capacity > QCRATE_MAX_PENDING_PACKETS)
			capacity = QCRATE_MAX_PENDING_PACKETS;
		grown = realloc(run->pending, capacity * sizeof(*run->pending));
		if (!grown)
			return -1;
		run->pending = grown;
		run->pending_capacity = capacity;
	}
	record = &run->pending[run->pending_count++];
	memset(record, 0, sizeof(*record));
	record->datagram = malloc(bytes);
	if (!record->datagram)
		return -1;
	memcpy(record->datagram, datagram, bytes);
	record->source = *source;
	record->receive_ns = receive_ns;
	record->bytes = bytes;
	return 0;
}

static int validate_and_ingest(struct run_state *run,
			       const struct source_identity *source,
			       uint64_t receive_ns,
			       const uint8_t *datagram, size_t bytes);

static int replay_pending(struct run_state *run)
{
	size_t index;
	int result = 0;

	for (index = 0; index < run->pending_count; index++) {
		struct pending_record *record = &run->pending[index];

		if (!result && validate_and_ingest(run, &record->source,
						 record->receive_ns,
						 record->datagram, record->bytes))
			result = -1;
		free(record->datagram);
	}
	free(run->pending);
	run->pending = NULL;
	run->pending_count = 0;
	run->pending_capacity = 0;
	return result;
}

static int validate_and_ingest(struct run_state *run,
			       const struct source_identity *source,
			       uint64_t receive_ns,
			       const uint8_t *datagram, size_t bytes)
{
	struct qcrate_data_header header;
	struct shot_state *shot;
	const uint8_t *payload;
	size_t payload_bytes;
	int status;

	if (bytes > QCRATE_DATA_MAX_DATAGRAM) {
		run->malformed_packets++;
		return 0;
	}
	status = qcrate_data_header_decode(&header, datagram, bytes);
	if (status != QCRATE_DATA_CODEC_OK) {
		run->malformed_packets++;
		return 0;
	}
	if (!run->selected) {
		if (header.packet_type != QCRATE_DATA_PACKET_STREAM_INFO)
			return save_pending(run, source, receive_ns, datagram, bytes);
		if (select_run(run, source, &header))
			return -1;
		if (replay_pending(run))
			return -1;
	}
	if (!source_equal(source, &run->selected_source, true) ||
	    header.run_id != run->run_id || header.stream_id != run->stream_id) {
		run->foreign_packets++;
		return 0;
	}
	run->valid_packets++;
	payload = datagram + header.header_bytes;
	payload_bytes = bytes - header.header_bytes;
	if (header.packet_type == QCRATE_DATA_PACKET_HEARTBEAT) {
		if (bytes != QCRATE_DATA_HEADER_BYTES ||
		    header.payload_format != QCRATE_DATA_FORMAT_NONE ||
		    header.sample_bytes || header.sample_count || header.frame_id ||
		    header.sample_offset || header.timestamp_ticks ||
		    header.flags != QCRATE_DATA_FLAG_END_OF_STREAM) {
			run->malformed_packets++;
			return 0;
		}
		run->terminal_seen = true;
		run->terminal_is_shot_end = false;
		run->terminal_sequence = header.packet_sequence;
		run->terminal_shot_id = header.shot_id;
		run->terminal_seen_ns = receive_ns;
		return 0;
	}
	if (run->last_shot_valid && header.shot_id <= run->last_shot_id) {
		run->late_packets++;
		return 0;
	}
	shot = find_shot(run, header.shot_id);
	if (!shot)
		shot = create_shot(run, header.shot_id);
	if (!shot)
		return -1;
	status = add_packet(shot, &header, receive_ns, datagram, bytes);
	if (status < 0)
		return -1;
	if (status > 0)
		return finalize_ready(run);
	if (header.packet_type == QCRATE_DATA_PACKET_STREAM_INFO) {
		struct qcrate_data_stream_info info;

		if (payload_bytes != QCRATE_DATA_STREAM_INFO_BYTES ||
		    qcrate_data_stream_info_decode(&info, payload, payload_bytes) !=
			QCRATE_DATA_CODEC_OK) {
			shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		} else if (!shot->have_info) {
			shot->have_info = true;
			shot->info_header = header;
			shot->info = info;
			if (!run->profile_valid) {
				run->profile_valid = true;
				run->profile_header = header;
				run->profile = info;
			}
		} else {
			shot->issue_flags |= QCRATE_INDEX_ISSUE_CONFLICT;
		}
	} else if (header.packet_type == QCRATE_DATA_PACKET_SHOT_END) {
		struct qcrate_data_shot_end end;

		if (payload_bytes != QCRATE_DATA_SHOT_END_BYTES ||
		    qcrate_data_shot_end_decode(&end, payload, payload_bytes) !=
			QCRATE_DATA_CODEC_OK) {
			shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		} else if (!shot->have_end) {
			shot->have_end = true;
			shot->end_header = header;
			shot->end = end;
			if (end.end_flags & QCRATE_DATA_FLAG_END_OF_STREAM) {
				run->terminal_seen = true;
				run->terminal_is_shot_end = true;
				run->terminal_sequence = header.packet_sequence;
				run->terminal_shot_id = header.shot_id;
				run->terminal_seen_ns = receive_ns;
			}
		} else {
			shot->issue_flags |= QCRATE_INDEX_ISSUE_CONFLICT;
			if (!shot_end_equal(&shot->end, &end))
				shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
		}
	} else if (header.packet_type != QCRATE_DATA_PACKET_DATA) {
		shot->issue_flags |= QCRATE_INDEX_ISSUE_PROTOCOL;
	}
	return finalize_ready(run);
}

static int open_exclusive(const char *path, FILE **stream)
{
	int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);

	if (descriptor < 0)
		return -1;
	*stream = fdopen(descriptor, "wb");
	if (!*stream) {
		close(descriptor);
		return -1;
	}
	return 0;
}

static int ensure_parent_directories(const char *path)
{
	char copy[PATH_MAX];
	char *cursor;
	char *last;

	if (strlen(path) >= sizeof(copy)) {
		errno = ENAMETOOLONG;
		return -1;
	}
	strcpy(copy, path);
	last = strrchr(copy, '/');
	if (!last || last == copy)
		return 0;
	*last = '\0';
	for (cursor = copy + 1; *cursor; cursor++) {
		if (*cursor != '/')
			continue;
		*cursor = '\0';
		if (mkdir(copy, 0755) && errno != EEXIST)
			return -1;
		*cursor = '/';
	}
	if (mkdir(copy, 0755) && errno != EEXIST)
		return -1;
	return 0;
}

static int prepare_output(struct run_state *run)
{
	char path[PATH_MAX];
	uint8_t journal_header[QCRATE_JOURNAL_HEADER_BYTES];

	if (ensure_parent_directories(run->options.output) ||
	    mkdir(run->options.output, 0755))
		return -1;
	if (snprintf(path, sizeof(path), "%s/packets.qcdp", run->options.output) >=
	    (int)sizeof(path) || open_exclusive(path, &run->journal))
		return -1;
	qcrate_journal_header_encode(journal_header);
	if (write_exact(run->journal, journal_header, sizeof(journal_header)))
		return -1;
	if (snprintf(path, sizeof(path), "%s/samples.iq16", run->options.output) >=
	    (int)sizeof(path) || open_exclusive(path, &run->samples))
		return -1;
	if (snprintf(path, sizeof(path), "%s/shots.qidx", run->options.output) >=
	    (int)sizeof(path) || open_exclusive(path, &run->index))
		return -1;
	return 0;
}

static int create_socket(struct run_state *run)
{
	struct addrinfo hints = {0};
	struct addrinfo *addresses = NULL;
	struct addrinfo *address;
	char port[16];
	int enabled = 1;
	int status;
	socklen_t option_size = sizeof(run->actual_receive_buffer);

	snprintf(port, sizeof(port), "%u", run->options.port);
	hints.ai_family = AF_UNSPEC;
	hints.ai_socktype = SOCK_DGRAM;
	hints.ai_flags = AI_NUMERICHOST | AI_PASSIVE;
	status = getaddrinfo(run->options.bind_address, port, &hints, &addresses);
	if (status) {
		fprintf(stderr, "qcrate-recorder: invalid bind address: %s\n",
			gai_strerror(status));
		return -1;
	}
	for (address = addresses; address; address = address->ai_next) {
		run->socket_fd = socket(address->ai_family,
					SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
		if (run->socket_fd < 0)
			continue;
		(void)setsockopt(run->socket_fd, SOL_SOCKET, SO_RCVBUF,
				 &run->options.receive_buffer,
				 sizeof(run->options.receive_buffer));
		(void)setsockopt(run->socket_fd, SOL_SOCKET, SO_RXQ_OVFL,
				 &enabled, sizeof(enabled));
		if (!bind(run->socket_fd, address->ai_addr, address->ai_addrlen))
			break;
		close(run->socket_fd);
		run->socket_fd = -1;
	}
	freeaddrinfo(addresses);
	if (run->socket_fd < 0)
		return -1;
	if (getsockopt(run->socket_fd, SOL_SOCKET, SO_RCVBUF,
		       &run->actual_receive_buffer, &option_size))
		run->actual_receive_buffer = -1;
	return 0;
}

static int receive_batch(struct run_state *run)
{
	struct mmsghdr messages[QCRATE_RECEIVE_BATCH];
	struct iovec vectors[QCRATE_RECEIVE_BATCH];
	struct sockaddr_storage sources[QCRATE_RECEIVE_BATCH];
	uint8_t controls[QCRATE_RECEIVE_BATCH][CMSG_SPACE(sizeof(uint32_t))];
	unsigned int index;
	int count;

	memset(messages, 0, sizeof(messages));
	for (index = 0; index < QCRATE_RECEIVE_BATCH; index++) {
		vectors[index].iov_base = run->receive_storage +
			index * QCRATE_MAX_UDP_DATAGRAM;
		vectors[index].iov_len = QCRATE_MAX_UDP_DATAGRAM;
		messages[index].msg_hdr.msg_iov = &vectors[index];
		messages[index].msg_hdr.msg_iovlen = 1;
		messages[index].msg_hdr.msg_name = &sources[index];
		messages[index].msg_hdr.msg_namelen = sizeof(sources[index]);
		messages[index].msg_hdr.msg_control = controls[index];
		messages[index].msg_hdr.msg_controllen = sizeof(controls[index]);
	}
	count = recvmmsg(run->socket_fd, messages, QCRATE_RECEIVE_BATCH,
			 MSG_DONTWAIT, NULL);
	if (count < 0)
		return errno == EAGAIN || errno == EWOULDBLOCK ? 0 : -1;
	for (index = 0; index < (unsigned int)count; index++) {
		struct source_identity source;
		struct cmsghdr *control;
		uint64_t receive_ns = monotonic_ns();

		if (messages[index].msg_hdr.msg_flags & MSG_TRUNC) {
			run->malformed_packets++;
			continue;
		}
		if (source_from_sockaddr(&sources[index], &source))
			continue;
		for (control = CMSG_FIRSTHDR(&messages[index].msg_hdr); control;
		     control = CMSG_NXTHDR(&messages[index].msg_hdr, control)) {
			if (control->cmsg_level == SOL_SOCKET &&
			    control->cmsg_type == SO_RXQ_OVFL &&
			    control->cmsg_len >= CMSG_LEN(sizeof(uint32_t))) {
				uint32_t dropped;

				memcpy(&dropped, CMSG_DATA(control), sizeof(dropped));
				if (dropped > run->kernel_drops)
					run->kernel_drops = dropped;
			}
		}
		if (run->source_filter_valid) {
			if (!source_equal(&source, &run->source_filter, false))
				continue;
		}
		if (journal_datagram(run, &source, receive_ns,
				     vectors[index].iov_base,
				     messages[index].msg_len) ||
		    validate_and_ingest(run, &source, receive_ns,
					vectors[index].iov_base,
					messages[index].msg_len))
			return -1;
		run->last_datagram_ns = receive_ns;
	}
	return count;
}

static int receive_run(struct run_state *run)
{
	struct pollfd descriptor = {.fd = run->socket_fd, .events = POLLIN};

	run->listen_ns = monotonic_ns();
	run->start_ns = run->listen_ns;
	run->last_datagram_ns = run->listen_ns;
	run->receive_storage = malloc(QCRATE_RECEIVE_BATCH *
				      QCRATE_MAX_UDP_DATAGRAM);
	if (!run->receive_storage)
		return -1;
	while (!stop_requested) {
		uint64_t now = monotonic_ns();
		uint64_t elapsed_ms;
		int status;

		if (run->terminal_seen) {
			elapsed_ms = (now - run->terminal_seen_ns) / UINT64_C(1000000);
			if (elapsed_ms >= run->options.end_grace_ms)
				break;
		} else if (!run->journal_records) {
			elapsed_ms = (now - run->listen_ns) / UINT64_C(1000000);
			if (elapsed_ms >= run->options.wait_timeout_ms)
				break;
		} else {
			elapsed_ms = (now - run->last_datagram_ns) / UINT64_C(1000000);
			if (elapsed_ms >= run->options.idle_timeout_ms)
				break;
		}
		status = poll(&descriptor, 1, 100);
		if (status < 0) {
			if (errno == EINTR)
				continue;
			return -1;
		}
		if (status > 0 && receive_batch(run) < 0)
			return -1;
	}
	run->interrupted = stop_requested != 0;
	free(run->receive_storage);
	run->receive_storage = NULL;
	if (finalize_remaining(run))
		return -1;
	if (run->terminal_seen &&
	    ((!run->expected_sequence_valid ||
	      (run->terminal_is_shot_end ?
	       run->terminal_sequence + 1 != run->expected_sequence :
	       run->terminal_sequence != run->expected_sequence)) ||
	     (run->last_shot_valid &&
	      run->terminal_shot_id != run->last_shot_id)))
		run->run_continuity_error = true;
	return 0;
}

static int write_manifest(const struct run_state *run, bool clean)
{
	char temporary[PATH_MAX];
	char final[PATH_MAX];
	FILE *manifest;
	uint64_t duration_ns = run->end_ns >= run->start_ns ?
		run->end_ns - run->start_ns : 0;
	uint64_t cpu_ns = run->end_cpu_ns >= run->start_cpu_ns ?
		run->end_cpu_ns - run->start_cpu_ns : 0;
	double duration_seconds = (double)duration_ns / 1.0e9;
	double cpu_seconds = (double)cpu_ns / 1.0e9;
	double cpu_percent = duration_seconds > 0.0 ?
		100.0 * cpu_seconds / duration_seconds : 0.0;
	double sample_mbps = duration_seconds > 0.0 ?
		(double)run->sample_offset * 8.0 / duration_seconds / 1.0e6 : 0.0;
	double udp_mbps = duration_seconds > 0.0 ?
		(double)run->datagram_bytes * 8.0 / duration_seconds / 1.0e6 : 0.0;
	double shot_rate = duration_seconds > 0.0 ?
		(double)run->complete_shots / duration_seconds : 0.0;

	if (snprintf(temporary, sizeof(temporary), "%s/.run.json.tmp",
		     run->options.output) >= (int)sizeof(temporary) ||
	    snprintf(final, sizeof(final), "%s/run.json", run->options.output) >=
		     (int)sizeof(final))
		return -1;
	manifest = fopen(temporary, "w");
	if (!manifest)
		return -1;
	fprintf(manifest,
		"{\n"
		"  \"format\": \"qcrate-run-v1\",\n"
		"  \"complete\": %s,\n"
		"  \"run_id\": \"0x%016" PRIx64 "\",\n"
		"  \"stream_id\": \"0x%08" PRIx32 "\",\n",
		clean ? "true" : "false", run->run_id, run->stream_id);
	if (run->profile_valid) {
		fprintf(manifest,
			"  \"stream\": {\n"
			"    \"payload_format\": %" PRIu16 ",\n"
			"    \"sample_bytes\": %" PRIu16 ",\n"
			"    \"frame_samples\": %" PRIu32 ",\n"
			"    \"sample_rate_numerator\": %" PRIu64 ",\n"
			"    \"sample_rate_denominator\": %" PRIu64 ",\n"
			"    \"timestamp_rate_numerator\": %" PRIu64 ",\n"
			"    \"timestamp_rate_denominator\": %" PRIu64 ",\n"
			"    \"center_frequency_hz\": %" PRId64 ",\n"
			"    \"channel_count\": %" PRIu16 ",\n"
			"    \"component_bits\": %" PRIu16 ",\n"
			"    \"fraction_bits\": %" PRIu16 ",\n"
			"    \"timestamp_clock_id\": %" PRIu16 ",\n"
			"    \"config_id\": \"0x%016" PRIx64 "\"\n"
			"  },\n",
			run->profile_header.payload_format,
			run->profile_header.sample_bytes,
			run->profile.frame_samples,
			run->profile.sample_rate_numerator,
			run->profile.sample_rate_denominator,
			run->profile.timestamp_rate_numerator,
			run->profile.timestamp_rate_denominator,
			run->profile.center_frequency_hz,
			run->profile.channel_count, run->profile.component_bits,
			run->profile.fraction_bits,
			run->profile.timestamp_clock_id, run->profile.config_id);
	} else {
		fputs("  \"stream\": null,\n", manifest);
	}
	fprintf(manifest,
		"  \"terminal_received\": %s,\n"
		"  \"run_continuity_error\": %s,\n"
		"  \"interrupted\": %s,\n"
		"  \"timing\": {\"start_monotonic_ns\": %" PRIu64
		", \"end_monotonic_ns\": %" PRIu64
		", \"duration_seconds\": %.6f, \"process_cpu_seconds\": %.6f"
		", \"process_cpu_percent\": %.3f},\n"
		"  \"performance\": {\"shot_rate_hz\": %.6f"
		", \"sample_payload_mbps\": %.6f, \"udp_payload_mbps\": %.6f},\n"
		"  \"shots_complete\": %" PRIu64 ",\n"
		"  \"shots_incomplete\": %" PRIu64 ",\n"
		"  \"datagrams_journaled\": %" PRIu64 ",\n"
		"  \"datagram_bytes_journaled\": %" PRIu64 ",\n"
		"  \"valid_packets\": %" PRIu64 ",\n"
		"  \"malformed_packets\": %" PRIu64 ",\n"
		"  \"foreign_packets\": %" PRIu64 ",\n"
		"  \"duplicate_packets\": %" PRIu64 ",\n"
		"  \"reordered_packets\": %" PRIu64 ",\n"
		"  \"conflicting_packets\": %" PRIu64 ",\n"
		"  \"late_packets\": %" PRIu64 ",\n"
		"  \"preselection_overflow\": %" PRIu64 ",\n"
		"  \"kernel_receive_drops\": %" PRIu32 ",\n"
		"  \"requested_receive_buffer_bytes\": %" PRIu32 ",\n"
		"  \"actual_receive_buffer_bytes\": %d,\n"
		"  \"sample_bytes_published\": %" PRIu64 "\n"
		"}\n",
		run->terminal_seen ? "true" : "false",
		run->run_continuity_error ? "true" : "false",
		run->interrupted ? "true" : "false", run->start_ns, run->end_ns,
		duration_seconds, cpu_seconds, cpu_percent, shot_rate, sample_mbps,
		udp_mbps, run->complete_shots, run->incomplete_shots,
		run->journal_records, run->datagram_bytes, run->valid_packets,
		run->malformed_packets, run->foreign_packets,
		run->duplicate_packets, run->reordered_packets,
		run->conflicting_packets, run->late_packets, run->pending_overflow,
		run->kernel_drops,
		run->options.receive_buffer, run->actual_receive_buffer,
		run->sample_offset);
	if (fclose(manifest) || rename(temporary, final))
		return -1;
	return 0;
}

static void close_run(struct run_state *run)
{
	size_t index;

	if (run->socket_fd >= 0)
		close(run->socket_fd);
	if (run->journal)
		fclose(run->journal);
	if (run->samples)
		fclose(run->samples);
	if (run->index)
		fclose(run->index);
	for (index = 0; index < run->shot_count; index++)
		free_shot(run->shots[index]);
	for (index = 0; index < run->pending_count; index++)
		free(run->pending[index].datagram);
	free(run->pending);
	free(run->receive_storage);
}

int main(int argc, char **argv)
{
	struct run_state run = {.socket_fd = -1};
	struct sigaction action = {.sa_handler = handle_signal};
	bool clean;
	int status;

	if (parse_options(argc, argv, &run.options)) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	if (run.options.source_address) {
		if (parse_source_filter(run.options.source_address,
				&run.source_filter)) {
			fprintf(stderr, "qcrate-recorder: invalid source address\n");
			return EXIT_FAILURE;
		}
		run.source_filter_valid = true;
	}
	if (create_socket(&run)) {
		fprintf(stderr, "qcrate-recorder: cannot bind UDP socket: %s\n",
			strerror(errno));
		close_run(&run);
		return EXIT_FAILURE;
	}
	if (prepare_output(&run)) {
		fprintf(stderr, "qcrate-recorder: cannot create output: %s\n",
			strerror(errno));
		close_run(&run);
		return EXIT_FAILURE;
	}
	sigemptyset(&action.sa_mask);
	action.sa_flags = 0;
	(void)sigaction(SIGINT, &action, NULL);
	(void)sigaction(SIGTERM, &action, NULL);
	printf("Q-Crate recorder listening on %s:%u\n",
	       run.options.bind_address, run.options.port);
	fflush(stdout);
	run.start_cpu_ns = process_cpu_ns();
	status = receive_run(&run);
	run.end_ns = monotonic_ns();
	run.end_cpu_ns = process_cpu_ns();
	clean = status == 0 && run.selected && run.terminal_seen &&
		!run.interrupted && run.complete_shots > 0 &&
		!run.incomplete_shots && !run.malformed_packets &&
		!run.conflicting_packets && !run.kernel_drops &&
		!run.run_continuity_error && !run.pending_overflow;
	if (!run.index_header_written) {
		uint8_t header[QCRATE_INDEX_HEADER_BYTES];

		qcrate_index_header_encode(header, 0, 0);
		if (write_exact(run.index, header, sizeof(header)))
			status = -1;
	}
	if (fflush(run.journal) || fflush(run.samples) || fflush(run.index) ||
	    write_manifest(&run, clean))
		status = -1;
	printf("%s: %" PRIu64 " complete, %" PRIu64
	       " incomplete shot(s), %" PRIu64 " datagrams, %" PRIu32
	       " kernel drop(s)\n",
	       clean ? "PASS" : "INCOMPLETE", run.complete_shots,
	       run.incomplete_shots, run.journal_records, run.kernel_drops);
	close_run(&run);
	if (status < 0) {
		fprintf(stderr, "qcrate-recorder: %s\n", strerror(errno));
		return EXIT_FAILURE;
	}
	return clean ? EXIT_SUCCESS : 2;
}
