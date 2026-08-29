/* SPDX-License-Identifier: MIT */
/*
 * Q-Crate finite-shot KV260 sender.
 *
 * The qcrate_dma driver remains the sole owner of AXI DMA and the coherent
 * capture buffer. This process requests one finite capture, maps that buffer
 * read-only, and passes frame slices directly to sendmsg() through the shared
 * Data Plane v1 packetizer.
 */
#define _POSIX_C_SOURCE 200809L

#include "qcrate_data_packetizer.h"
#include "qcrate_dma_uapi.h"
#include "qcrate_stream_profiles.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <limits.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netinet/ip6.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define QCRATE_DEFAULT_DEVICE          "/dev/qcrate-dma"
#define QCRATE_DEFAULT_PORT            "47000"
#define QCRATE_DEFAULT_WORDS           1024U
#define QCRATE_DEFAULT_FRAMES          4U
#define QCRATE_DEFAULT_TIMEOUT_MS      2000U
#define QCRATE_MAX_TIMEOUT_MS          60000U
#define QCRATE_DEFAULT_STREAM_ID       1U
#define QCRATE_DEFAULT_SHOT_ID         UINT64_C(1)
#define QCRATE_DEFAULT_SEND_BUFFER     (4U * 1024U * 1024U)
#define QCRATE_DEFAULT_RATE_MBPS       420U
#define QCRATE_DMA_RESULT_NOERROR      0U
#define QCRATE_STREAM_MODE_COUNTER     0U
#define QCRATE_STREAM_MODE_DSP         1U

struct options {
	const char *destination;
	const char *port;
	const char *device;
	uint32_t words;
	uint32_t frames;
	uint32_t timeout_ms;
	uint32_t stream_id;
	uint64_t run_id;
	uint64_t shot_id;
	uint32_t send_buffer;
	uint32_t rate_mbps;
	uint32_t stream_mode;
	bool run_id_set;
};

struct udp_emitter {
	int socket_fd;
	uint64_t rate_bps;
	struct timespec next_send;
	bool pacing_started;
	uint64_t datagrams;
	uint64_t bytes;
	int error_number;
};

static void usage(FILE *stream, const char *program)
{
	fprintf(stream,
		"Usage: %s --destination HOST [OPTIONS]\n"
		"\n"
		"Capture one finite Q-Crate DMA shot and send Data Plane v1 UDP.\n"
		"\n"
		"  -d, --destination HOST   receiver address (required)\n"
		"  -p, --port PORT          receiver UDP port (default %s)\n"
		"      --device PATH        DMA device (default %s)\n"
		"      --mode MODE          dsp or counter (default dsp)\n"
		"      --words N            samples per frame (default %u)\n"
		"      --frames N           finite frame count (default %u)\n"
		"      --timeout-ms N        DMA timeout (default %u)\n"
		"      --stream-id N        logical stream ID (default %u)\n"
		"      --run-id N           fixed run ID; default is random\n"
		"      --shot-id N          shot ID (default 1)\n"
		"      --send-buffer N      requested SO_SNDBUF bytes\n"
		"      --rate-mbps N        UDP payload pacing; 0 disables\n"
		"  -h, --help               show this help\n",
		program, QCRATE_DEFAULT_PORT, QCRATE_DEFAULT_DEVICE,
		QCRATE_DEFAULT_WORDS, QCRATE_DEFAULT_FRAMES,
		QCRATE_DEFAULT_TIMEOUT_MS, QCRATE_DEFAULT_STREAM_ID);
}

static int parse_u64(const char *text, uint64_t minimum, uint64_t maximum,
		     uint64_t *value)
{
	char *end;
	unsigned long long parsed;

	errno = 0;
	parsed = strtoull(text, &end, 0);
	if (errno || !text[0] || *end || parsed < minimum || parsed > maximum)
		return -1;
	*value = (uint64_t)parsed;
	return 0;
}

static int parse_u32(const char *text, uint32_t minimum, uint32_t maximum,
		     uint32_t *value)
{
	uint64_t parsed;

	if (parse_u64(text, minimum, maximum, &parsed))
		return -1;
	*value = (uint32_t)parsed;
	return 0;
}

static int parse_options(int argc, char **argv, struct options *options)
{
	enum {
		OPT_DEVICE = 1000,
		OPT_MODE,
		OPT_WORDS,
		OPT_FRAMES,
		OPT_TIMEOUT,
		OPT_STREAM_ID,
		OPT_RUN_ID,
		OPT_SHOT_ID,
		OPT_SEND_BUFFER,
		OPT_RATE,
	};
	static const struct option long_options[] = {
		{"destination", required_argument, NULL, 'd'},
		{"port", required_argument, NULL, 'p'},
		{"device", required_argument, NULL, OPT_DEVICE},
		{"mode", required_argument, NULL, OPT_MODE},
		{"words", required_argument, NULL, OPT_WORDS},
		{"frames", required_argument, NULL, OPT_FRAMES},
		{"timeout-ms", required_argument, NULL, OPT_TIMEOUT},
		{"stream-id", required_argument, NULL, OPT_STREAM_ID},
		{"run-id", required_argument, NULL, OPT_RUN_ID},
		{"shot-id", required_argument, NULL, OPT_SHOT_ID},
		{"send-buffer", required_argument, NULL, OPT_SEND_BUFFER},
		{"rate-mbps", required_argument, NULL, OPT_RATE},
		{"help", no_argument, NULL, 'h'},
		{NULL, 0, NULL, 0},
	};
	int option;

	*options = (struct options) {
		.port = QCRATE_DEFAULT_PORT,
		.device = QCRATE_DEFAULT_DEVICE,
		.words = QCRATE_DEFAULT_WORDS,
		.frames = QCRATE_DEFAULT_FRAMES,
		.timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS,
		.stream_id = QCRATE_DEFAULT_STREAM_ID,
		.shot_id = QCRATE_DEFAULT_SHOT_ID,
		.send_buffer = QCRATE_DEFAULT_SEND_BUFFER,
		.rate_mbps = QCRATE_DEFAULT_RATE_MBPS,
		.stream_mode = QCRATE_STREAM_MODE_DSP,
	};
	while ((option = getopt_long(argc, argv, "d:p:h", long_options, NULL)) != -1) {
		switch (option) {
		case 'd':
			options->destination = optarg;
			break;
		case 'p':
			options->port = optarg;
			break;
		case 'h':
			usage(stdout, argv[0]);
			exit(EXIT_SUCCESS);
		case OPT_DEVICE:
			options->device = optarg;
			break;
		case OPT_MODE:
			if (!strcmp(optarg, "dsp"))
				options->stream_mode = QCRATE_STREAM_MODE_DSP;
			else if (!strcmp(optarg, "counter"))
				options->stream_mode = QCRATE_STREAM_MODE_COUNTER;
			else
				return -1;
			break;
		case OPT_WORDS:
			if (parse_u32(optarg, 1, UINT32_MAX, &options->words))
				return -1;
			break;
		case OPT_FRAMES:
			if (parse_u32(optarg, 1, QCRATE_DMA_MAX_CHAIN_FRAMES,
				      &options->frames))
				return -1;
			break;
		case OPT_TIMEOUT:
			if (parse_u32(optarg, 1, QCRATE_MAX_TIMEOUT_MS,
				      &options->timeout_ms))
				return -1;
			break;
		case OPT_STREAM_ID:
			if (parse_u32(optarg, 1, UINT32_MAX, &options->stream_id))
				return -1;
			break;
		case OPT_RUN_ID:
			if (parse_u64(optarg, 1, UINT64_MAX, &options->run_id))
				return -1;
			options->run_id_set = true;
			break;
		case OPT_SHOT_ID:
			if (parse_u64(optarg, 1, UINT64_MAX, &options->shot_id))
				return -1;
			break;
		case OPT_SEND_BUFFER:
			if (parse_u32(optarg, 1, INT_MAX, &options->send_buffer))
				return -1;
			break;
		case OPT_RATE:
			if (parse_u32(optarg, 0, UINT32_MAX, &options->rate_mbps))
				return -1;
			break;
		default:
			return -1;
		}
	}
	if (!options->destination || optind != argc)
		return -1;
	return 0;
}

static int random_nonzero_u64(uint64_t *value)
{
	int fd;
	ssize_t received;

	do {
		received = getrandom(value, sizeof(*value), 0);
	} while (received < 0 && errno == EINTR);
	if (received == (ssize_t)sizeof(*value) && *value)
		return 0;
	fd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -1;
	do {
		received = read(fd, value, sizeof(*value));
	} while (received < 0 && errno == EINTR);
	close(fd);
	if (received != (ssize_t)sizeof(*value) || !*value) {
		errno = EIO;
		return -1;
	}
	return 0;
}

static int connect_udp(const struct options *options, int *actual_send_buffer)
{
	struct addrinfo hints = {
		.ai_family = AF_UNSPEC,
		.ai_socktype = SOCK_DGRAM,
		.ai_protocol = IPPROTO_UDP,
	};
	struct addrinfo *addresses;
	struct addrinfo *address;
	int gai_status;
	int socket_fd = -1;
	socklen_t length = sizeof(*actual_send_buffer);

	gai_status = getaddrinfo(options->destination, options->port,
				 &hints, &addresses);
	if (gai_status) {
		fprintf(stderr, "destination resolution failed: %s\n",
			gai_strerror(gai_status));
		errno = EINVAL;
		return -1;
	}
	for (address = addresses; address; address = address->ai_next) {
		int path_mtu = IP_PMTUDISC_DO;
		int mtu_status = 0;

		socket_fd = socket(address->ai_family,
				   address->ai_socktype | SOCK_CLOEXEC,
				   address->ai_protocol);
		if (socket_fd < 0)
			continue;
		if (setsockopt(socket_fd, SOL_SOCKET, SO_SNDBUF,
			       &options->send_buffer, sizeof(options->send_buffer))) {
			close(socket_fd);
			socket_fd = -1;
			continue;
		}
		if (address->ai_family == AF_INET)
			mtu_status = setsockopt(socket_fd, IPPROTO_IP,
						IP_MTU_DISCOVER,
						&path_mtu, sizeof(path_mtu));
#ifdef IPV6_MTU_DISCOVER
		else if (address->ai_family == AF_INET6) {
			path_mtu = IPV6_PMTUDISC_DO;
			mtu_status = setsockopt(socket_fd, IPPROTO_IPV6,
						IPV6_MTU_DISCOVER,
						&path_mtu, sizeof(path_mtu));
		}
#endif
		if (mtu_status) {
			close(socket_fd);
			socket_fd = -1;
			continue;
		}
		if (!connect(socket_fd, address->ai_addr, address->ai_addrlen))
			break;
		close(socket_fd);
		socket_fd = -1;
	}
	freeaddrinfo(addresses);
	if (socket_fd < 0)
		return -1;
	if (getsockopt(socket_fd, SOL_SOCKET, SO_SNDBUF,
		       actual_send_buffer, &length)) {
		close(socket_fd);
		return -1;
	}
	return socket_fd;
}

static void add_nanoseconds(struct timespec *time, uint64_t nanoseconds)
{
	time->tv_sec += (time_t)(nanoseconds / UINT64_C(1000000000));
	time->tv_nsec += (long)(nanoseconds % UINT64_C(1000000000));
	if (time->tv_nsec >= 1000000000L) {
		time->tv_sec++;
		time->tv_nsec -= 1000000000L;
	}
}

static int wait_until(const struct timespec *deadline)
{
	int status;

	do {
		status = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME,
					 deadline, NULL);
	} while (status == EINTR);
	if (status) {
		errno = status;
		return -1;
	}
	return 0;
}

static int emit_udp(void *context,
		    const uint8_t header[QCRATE_DATA_HEADER_BYTES],
		    const uint8_t *payload, size_t payload_bytes)
{
	struct udp_emitter *emitter = context;
	struct iovec vectors[2] = {
		{.iov_base = (void *)header, .iov_len = QCRATE_DATA_HEADER_BYTES},
		{.iov_base = (void *)payload, .iov_len = payload_bytes},
	};
	struct msghdr message = {
		.msg_iov = vectors,
		.msg_iovlen = payload_bytes ? 2 : 1,
	};
	size_t datagram_bytes = QCRATE_DATA_HEADER_BYTES + payload_bytes;
	ssize_t sent;

	if (!emitter->pacing_started) {
		if (clock_gettime(CLOCK_MONOTONIC, &emitter->next_send))
			goto error;
		emitter->pacing_started = true;
	} else if (emitter->rate_bps && wait_until(&emitter->next_send)) {
		goto error;
	}
	sent = sendmsg(emitter->socket_fd, &message, 0);
	if (sent < 0 || (size_t)sent != datagram_bytes) {
		if (sent >= 0)
			errno = EIO;
		goto error;
	}
	emitter->datagrams++;
	emitter->bytes += datagram_bytes;
	if (emitter->rate_bps) {
		uint64_t duration =
			((uint64_t)datagram_bytes * 8 * UINT64_C(1000000000) +
			 emitter->rate_bps - 1) / emitter->rate_bps;

		add_nanoseconds(&emitter->next_send, duration);
	}
	return 0;

error:
	emitter->error_number = errno;
	return -1;
}

static void fill_profile(const struct options *options,
			 struct qcrate_data_packetizer_config *profile)
{
	*profile = (struct qcrate_data_packetizer_config) {
		.sample_bytes = 4,
		.stream_id = options->stream_id,
		.run_id = options->run_id,
		.shot_id = options->shot_id,
		.frame_samples = options->words,
		.frame_count = options->frames,
		.sample_rate_denominator = 1,
		.timestamp_rate_denominator = 1,
		.timestamp_clock_id = QCRATE_DATA_CLOCK_QCRATE_TIMEBASE,
	};
	if (options->stream_mode == QCRATE_STREAM_MODE_DSP) {
		profile->payload_format = QCRATE_DATA_FORMAT_IQ_S16_LE;
		profile->sample_rate_numerator = QCRATE_DSP_SAMPLE_RATE_HZ;
		profile->timestamp_rate_numerator = QCRATE_DSP_TIMEBASE_RATE_HZ;
		profile->center_frequency_hz = QCRATE_DSP_CENTER_FREQUENCY_HZ;
		profile->channel_count = 2;
		profile->component_bits = 16;
		profile->fraction_bits = 15;
		profile->config_id = QCRATE_DSP_CONFIG_ID;
	} else {
		profile->payload_format = QCRATE_DATA_FORMAT_COUNTER_U32_LE;
		profile->sample_rate_numerator = QCRATE_COUNTER_SAMPLE_RATE_HZ;
		profile->timestamp_rate_numerator =
			QCRATE_COUNTER_TIMEBASE_RATE_HZ;
		profile->channel_count = 1;
		profile->component_bits = 32;
		profile->config_id = QCRATE_COUNTER_CONFIG_ID;
	}
}

int main(int argc, char **argv)
{
	struct qcrate_data_packetizer_config profile;
	struct qcrate_data_packetizer_result packet_result;
	struct qcrate_dma_capture_frames capture = {0};
	struct qcrate_dma_info info = {0};
	struct qcrate_dma_caps caps = {0};
	struct udp_emitter emitter = {0};
	struct options options;
	size_t total_bytes;
	void *buffer = MAP_FAILED;
	int actual_send_buffer;
	int device_fd = -1;
	int socket_fd = -1;
	int status = EXIT_FAILURE;
	int packet_status;

	if (parse_options(argc, argv, &options)) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	if (!options.run_id_set && random_nonzero_u64(&options.run_id)) {
		perror("run ID generation failed");
		return EXIT_FAILURE;
	}
	socket_fd = connect_udp(&options, &actual_send_buffer);
	if (socket_fd < 0) {
		perror("UDP connection failed");
		goto out;
	}
	device_fd = open(options.device, O_RDWR | O_CLOEXEC);
	if (device_fd < 0) {
		perror("DMA device open failed");
		goto out;
	}
	if (ioctl(device_fd, QCRATE_DMA_IOC_GET_INFO, &info) ||
	    ioctl(device_fd, QCRATE_DMA_IOC_GET_CAPS, &caps)) {
		perror("DMA capability query failed");
		goto out;
	}
	if (info.abi_version != QCRATE_DMA_ABI_VERSION ||
	    caps.abi_version != QCRATE_DMA_ABI_VERSION ||
	    info.stream_word_bytes != 4) {
		fprintf(stderr, "unsupported Q-Crate DMA ABI or stream word\n");
		goto out;
	}
	if (options.frames > 1 &&
	    !(caps.feature_flags & QCRATE_DMA_CAP_SG_CHAIN)) {
		fprintf(stderr, "finite multi-frame capture requires DMA SG\n");
		goto out;
	}
	if (options.frames > caps.max_chain_frames) {
		fprintf(stderr, "requested frames exceed driver SG limit %u\n",
			caps.max_chain_frames);
		goto out;
	}
	if (options.stream_mode == QCRATE_STREAM_MODE_DSP &&
	    !(caps.feature_flags & QCRATE_DMA_CAP_DSP_MODE)) {
		fprintf(stderr, "driver does not advertise DSP stream mode\n");
		goto out;
	}
	if (options.words > SIZE_MAX / info.stream_word_bytes ||
	    options.frames > SIZE_MAX /
		((size_t)options.words * info.stream_word_bytes)) {
		fprintf(stderr, "capture size overflows size_t\n");
		goto out;
	}
	total_bytes = (size_t)options.words * options.frames *
		info.stream_word_bytes;
	if ((size_t)options.words * info.stream_word_bytes >
	    info.max_transfer_bytes || total_bytes > info.buffer_bytes) {
		fprintf(stderr,
			"capture exceeds DMA frame or coherent-buffer limit\n");
		goto out;
	}
	buffer = mmap(NULL, info.buffer_bytes, PROT_READ, MAP_SHARED, device_fd, 0);
	if (buffer == MAP_FAILED) {
		perror("DMA buffer mmap failed");
		goto out;
	}
	capture.frame_length_words = options.words;
	capture.frame_count = options.frames;
	capture.timeout_ms = options.timeout_ms;
	capture.stream_mode = options.stream_mode;
	if (ioctl(device_fd, QCRATE_DMA_IOC_CAPTURE_FRAMES, &capture)) {
		perror("DMA capture failed");
		goto out;
	}
	if (capture.transferred_bytes != total_bytes ||
	    capture.last_residue_bytes ||
	    capture.dma_result != QCRATE_DMA_RESULT_NOERROR ||
	    capture.completed_frames != options.frames) {
		fprintf(stderr,
			"DMA evidence mismatch: bytes=%u/%zu residue=%u "
			"result=%u frames=%u/%u\n",
			capture.transferred_bytes, total_bytes,
			capture.last_residue_bytes, capture.dma_result,
			capture.completed_frames, options.frames);
		goto out;
	}

	fill_profile(&options, &profile);
	emitter.socket_fd = socket_fd;
	emitter.rate_bps = (uint64_t)options.rate_mbps * UINT64_C(1000000);
	packet_status = qcrate_data_packetize(
		&profile, buffer, total_bytes, emit_udp, &emitter, &packet_result);
	if (packet_status != QCRATE_DATA_PACKETIZER_OK) {
		if (emitter.error_number) {
			errno = emitter.error_number;
			perror("UDP send failed");
		} else {
			fprintf(stderr, "packetizer failed with status %d\n",
				packet_status);
		}
		goto out;
	}

	printf("PASS Q-Crate finite shot transmitted\n");
	printf("destination        : %s:%s\n",
	       options.destination, options.port);
	printf("mode               : %s\n",
	       options.stream_mode == QCRATE_STREAM_MODE_DSP ? "dsp" : "counter");
	printf("run/stream/shot    : 0x%016" PRIx64 " / %u / 0x%016" PRIx64 "\n",
	       options.run_id, options.stream_id, options.shot_id);
	printf("frames/samples     : %u / %u\n", options.frames, options.words);
	printf("datagrams/bytes    : %" PRIu64 " / %" PRIu64 "\n",
	       emitter.datagrams, emitter.bytes);
	printf("sample payload     : %" PRIu64 " bytes, CRC 0x%08x\n",
	       packet_result.payload_bytes, packet_result.payload_crc32);
	printf("SO_SNDBUF          : requested %u, actual %d bytes\n",
	       options.send_buffer, actual_send_buffer);
	printf("UDP pacing         : %u Mb/s%s\n", options.rate_mbps,
	       options.rate_mbps ? "" : " (disabled)");
	printf("DMA stall cycles   : %u\n", capture.stall_cycles);
	status = EXIT_SUCCESS;

out:
	if (buffer != MAP_FAILED)
		munmap(buffer, info.buffer_bytes);
	if (device_fd >= 0)
		close(device_fd);
	if (socket_fd >= 0)
		close(socket_fd);
	return status;
}
