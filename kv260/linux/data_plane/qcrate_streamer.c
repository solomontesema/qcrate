/* SPDX-License-Identifier: MIT */
/*
 * Q-Crate KV260 Data Plane v1 sender.
 *
 * The qcrate_dma driver remains the sole owner of AXI DMA and the coherent
 * capture buffer. The accepted default path sends one completed finite shot.
 * Triggered pool mode overlaps bank acquisition and UDP transmission using a
 * bounded ownership-token queue; sample bytes remain in the read-only mmap.
 */
#define _POSIX_C_SOURCE 200809L

#include "qcrate_data_packetizer.h"
#include "qcrate_dma_uapi.h"
#include "qcrate_protocol.h"
#include "qcrate_rpmsg_client.h"
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
#include <pthread.h>
#include <signal.h>
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
#define QCRATE_DEFAULT_BANKS           4U
#define QCRATE_DEFAULT_SEQUENCE_TIMEOUT_MS 2000U
#define QCRATE_DMA_RESULT_NOERROR      0U
#define QCRATE_STREAM_MODE_COUNTER     0U
#define QCRATE_STREAM_MODE_DSP         1U
#define QCRATE_SEQ_STATUS_IDLE         (UINT32_C(1) << 0)
#define QCRATE_SEQ_STATUS_ARMED        (UINT32_C(1) << 2)
#define QCRATE_SEQ_STATUS_FAULTED      (UINT32_C(1) << 4)
#define QCRATE_SEQ_STATUS_MEMORY_LOCKED (UINT32_C(1) << 9)

struct options {
	const char *destination;
	const char *port;
	const char *device;
	const char *rpmsg_device;
	uint32_t words;
	uint32_t frames;
	uint32_t timeout_ms;
	uint32_t stream_id;
	uint64_t run_id;
	uint64_t shot_id;
	uint32_t send_buffer;
	uint32_t rate_mbps;
	uint32_t stream_mode;
	uint32_t bank_count;
	uint32_t sequence_timeout_ms;
	uint64_t triggered_shots;
	bool run_id_set;
	bool triggered_mode;
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

struct bank_queue {
	struct qcrate_dma_pool_dequeue tokens[QCRATE_DMA_MAX_POOL_BANKS];
	size_t capacity;
	size_t head;
	size_t tail;
	size_t count;
	size_t high_water;
	bool producer_done;
	bool failed;
	int error_number;
	char error_context[96];
	pthread_mutex_t mutex;
	pthread_cond_t not_empty;
	pthread_cond_t not_full;
};

struct sustained_context {
	const struct options *options;
	int device_fd;
	const uint8_t *buffer;
	size_t buffer_bytes;
	struct udp_emitter *emitter;
	struct qcrate_rpmsg_client *rpmsg;
	struct bank_queue queue;
	uint64_t pool_run_id;
	uint64_t packet_sequence;
	uint64_t acquired_shots;
	uint64_t transmitted_shots;
	uint64_t payload_bytes;
	uint32_t last_shot_id;
	uint64_t last_first_sample_time;
};

static volatile sig_atomic_t stop_requested;

static void usage(FILE *stream, const char *program)
{
	fprintf(stream,
		"Usage: %s --destination HOST [OPTIONS]\n"
		"\n"
		"Send Q-Crate DMA captures using Data Plane v1 UDP.\n"
		"\n"
		"  -d, --destination HOST   receiver address (required)\n"
		"  -p, --port PORT          receiver UDP port (default %s)\n"
		"      --device PATH        DMA device (default %s)\n"
		"      --rpmsg-device PATH  R5 endpoint; default is discovery\n"
		"      --mode MODE          dsp or counter (default dsp)\n"
		"      --words N            samples per frame (default %u)\n"
		"      --frames N           finite frame count (default %u)\n"
		"      --timeout-ms N        DMA timeout (default %u)\n"
		"      --stream-id N        logical stream ID (default %u)\n"
		"      --run-id N           fixed run ID; default is random\n"
		"      --shot-id N          shot ID (default 1)\n"
		"      --send-buffer N      requested SO_SNDBUF bytes\n"
		"      --rate-mbps N        UDP payload pacing; 0 disables\n"
		"      --triggered-shots N  overlap N R5-triggered bank captures\n"
		"      --banks N            pool/queue banks (default %u)\n"
		"      --sequence-timeout-ms N  R5 state timeout (default %u)\n"
		"  -h, --help               show this help\n",
		program, QCRATE_DEFAULT_PORT, QCRATE_DEFAULT_DEVICE,
		QCRATE_DEFAULT_WORDS, QCRATE_DEFAULT_FRAMES,
		QCRATE_DEFAULT_TIMEOUT_MS, QCRATE_DEFAULT_STREAM_ID,
		QCRATE_DEFAULT_BANKS, QCRATE_DEFAULT_SEQUENCE_TIMEOUT_MS);
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
		OPT_RPMSG_DEVICE,
		OPT_TRIGGERED_SHOTS,
		OPT_BANKS,
		OPT_SEQUENCE_TIMEOUT,
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
		{"rpmsg-device", required_argument, NULL, OPT_RPMSG_DEVICE},
		{"triggered-shots", required_argument, NULL, OPT_TRIGGERED_SHOTS},
		{"banks", required_argument, NULL, OPT_BANKS},
		{"sequence-timeout-ms", required_argument, NULL,
		 OPT_SEQUENCE_TIMEOUT},
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
		.bank_count = QCRATE_DEFAULT_BANKS,
		.sequence_timeout_ms = QCRATE_DEFAULT_SEQUENCE_TIMEOUT_MS,
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
		case OPT_RPMSG_DEVICE:
			options->rpmsg_device = optarg;
			break;
		case OPT_TRIGGERED_SHOTS:
			if (parse_u64(optarg, 1, UINT64_MAX,
				      &options->triggered_shots))
				return -1;
			options->triggered_mode = true;
			break;
		case OPT_BANKS:
			if (parse_u32(optarg, 2, QCRATE_DMA_MAX_POOL_BANKS,
				      &options->bank_count))
				return -1;
			break;
		case OPT_SEQUENCE_TIMEOUT:
			if (parse_u32(optarg, 1, QCRATE_MAX_TIMEOUT_MS,
				      &options->sequence_timeout_ms))
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

static void handle_stop_signal(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

static int install_signal_handlers(void)
{
	struct sigaction action = {0};

	action.sa_handler = handle_stop_signal;
	sigemptyset(&action.sa_mask);
	if (sigaction(SIGINT, &action, NULL) ||
	    sigaction(SIGTERM, &action, NULL))
		return -1;
	return 0;
}

static int queue_init(struct bank_queue *queue, size_t capacity)
{
	int status;

	memset(queue, 0, sizeof(*queue));
	queue->capacity = capacity;
	status = pthread_mutex_init(&queue->mutex, NULL);
	if (status)
		return status;
	status = pthread_cond_init(&queue->not_empty, NULL);
	if (status) {
		pthread_mutex_destroy(&queue->mutex);
		return status;
	}
	status = pthread_cond_init(&queue->not_full, NULL);
	if (status) {
		pthread_cond_destroy(&queue->not_empty);
		pthread_mutex_destroy(&queue->mutex);
		return status;
	}
	return 0;
}

static void queue_destroy(struct bank_queue *queue)
{
	pthread_cond_destroy(&queue->not_full);
	pthread_cond_destroy(&queue->not_empty);
	pthread_mutex_destroy(&queue->mutex);
}

static void pipeline_fail(struct sustained_context *context,
			  const char *operation, int error_number)
{
	struct bank_queue *queue = &context->queue;

	pthread_mutex_lock(&queue->mutex);
	if (!queue->failed) {
		queue->failed = true;
		queue->error_number = error_number ? error_number : EIO;
		snprintf(queue->error_context, sizeof(queue->error_context), "%s",
			 operation);
	}
	pthread_cond_broadcast(&queue->not_empty);
	pthread_cond_broadcast(&queue->not_full);
	pthread_mutex_unlock(&queue->mutex);
}

static bool queue_failed(struct bank_queue *queue)
{
	bool failed;

	pthread_mutex_lock(&queue->mutex);
	failed = queue->failed;
	pthread_mutex_unlock(&queue->mutex);
	return failed;
}

static int queue_push(struct bank_queue *queue,
		      const struct qcrate_dma_pool_dequeue *token)
{
	pthread_mutex_lock(&queue->mutex);
	while (queue->count == queue->capacity && !queue->failed)
		pthread_cond_wait(&queue->not_full, &queue->mutex);
	if (queue->failed) {
		pthread_mutex_unlock(&queue->mutex);
		return -1;
	}
	queue->tokens[queue->tail] = *token;
	queue->tail = (queue->tail + 1) % queue->capacity;
	queue->count++;
	if (queue->count > queue->high_water)
		queue->high_water = queue->count;
	pthread_cond_signal(&queue->not_empty);
	pthread_mutex_unlock(&queue->mutex);
	return 0;
}

static int queue_pop(struct bank_queue *queue,
		     struct qcrate_dma_pool_dequeue *token)
{
	pthread_mutex_lock(&queue->mutex);
	while (!queue->count && !queue->producer_done && !queue->failed)
		pthread_cond_wait(&queue->not_empty, &queue->mutex);
	if (queue->failed) {
		pthread_mutex_unlock(&queue->mutex);
		return -1;
	}
	if (!queue->count) {
		pthread_mutex_unlock(&queue->mutex);
		return 0;
	}
	*token = queue->tokens[queue->head];
	queue->head = (queue->head + 1) % queue->capacity;
	queue->count--;
	pthread_cond_signal(&queue->not_full);
	pthread_mutex_unlock(&queue->mutex);
	return 1;
}

static void queue_finish(struct bank_queue *queue)
{
	pthread_mutex_lock(&queue->mutex);
	queue->producer_done = true;
	pthread_cond_broadcast(&queue->not_empty);
	pthread_mutex_unlock(&queue->mutex);
}

static int sequence_exchange(struct sustained_context *context,
			     uint16_t command, const uint32_t *payload,
			     size_t payload_words,
			     struct qcrate_rpmsg_message *response)
{
	if (qcrate_rpmsg_client_exchange(context->rpmsg, command, payload,
					 payload_words, response)) {
		if (errno == EREMOTEIO)
			fprintf(stderr, "R5 command %u rejected with status %" PRId32
				"\n", command, response->status);
		return -1;
	}
	return 0;
}

static int sequence_get_status(struct sustained_context *context,
			       struct qcrate_rpmsg_message *response)
{
	if (sequence_exchange(context, QCRATE_CMD_SEQ_GET_STATUS, NULL, 0,
			      response))
		return -1;
	if (response->payload_words != QCRATE_SEQ_STATUS_WORDS) {
		errno = EPROTO;
		return -1;
	}
	return 0;
}

static int sequence_wait_idle(struct sustained_context *context)
{
	struct timespec delay = {.tv_nsec = 1000000L};
	struct timespec start;
	struct timespec now;
	struct qcrate_rpmsg_message response;
	uint64_t elapsed_ms;

	if (clock_gettime(CLOCK_MONOTONIC, &start))
		return -1;
	for (;;) {
		if (sequence_get_status(context, &response))
			return -1;
		if (response.payload[0] & QCRATE_SEQ_STATUS_FAULTED) {
			errno = EIO;
			return -1;
		}
		if ((response.payload[0] &
		     (QCRATE_SEQ_STATUS_IDLE | QCRATE_SEQ_STATUS_MEMORY_LOCKED)) ==
		    QCRATE_SEQ_STATUS_IDLE)
			return 0;
		if (clock_gettime(CLOCK_MONOTONIC, &now))
			return -1;
		elapsed_ms = (uint64_t)(now.tv_sec - start.tv_sec) * 1000U;
		if (now.tv_nsec >= start.tv_nsec)
			elapsed_ms += (uint64_t)(now.tv_nsec - start.tv_nsec) /
				1000000U;
		else
			elapsed_ms -= (uint64_t)(start.tv_nsec - now.tv_nsec) /
				1000000U;
		if (elapsed_ms >= context->options->sequence_timeout_ms) {
			errno = ETIMEDOUT;
			return -1;
		}
		nanosleep(&delay, NULL);
	}
}

static int sequence_require_committed_idle(struct sustained_context *context)
{
	struct qcrate_rpmsg_message response;
	uint32_t status;

	if (sequence_get_status(context, &response))
		return -1;
	status = response.payload[0];
	if (!(status & QCRATE_SEQ_STATUS_COMMITTED) ||
	    (status & (QCRATE_SEQ_STATUS_IDLE | QCRATE_SEQ_STATUS_FAULTED |
		       QCRATE_SEQ_STATUS_MEMORY_LOCKED)) != QCRATE_SEQ_STATUS_IDLE) {
		errno = EBUSY;
		return -1;
	}
	return 0;
}

static int sequence_arm_start(struct sustained_context *context)
{
	struct qcrate_rpmsg_message response;
	uint32_t internal_trigger = 0;

	if (sequence_exchange(context, QCRATE_CMD_SEQ_ARM, &internal_trigger, 1,
			      &response))
		return -1;
	if (response.payload_words != QCRATE_SEQ_STATUS_WORDS ||
	    !(response.payload[0] & QCRATE_SEQ_STATUS_ARMED)) {
		errno = EPROTO;
		return -1;
	}
	if (sequence_exchange(context, QCRATE_CMD_SEQ_START, NULL, 0, &response))
		return -1;
	if (response.payload_words != QCRATE_SEQ_STATUS_WORDS) {
		errno = EPROTO;
		return -1;
	}
	return 0;
}

static int wait_pool_armed(struct sustained_context *context)
{
	struct timespec delay = {.tv_nsec = 1000000L};
	uint32_t attempts = context->options->timeout_ms + 1U;
	struct qcrate_dma_pool_status status;

	while (attempts--) {
		memset(&status, 0, sizeof(status));
		if (ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_STATUS, &status))
			return -1;
		if (status.pool_flags & QCRATE_DMA_POOL_ARMED)
			return 0;
		if (status.pool_flags & QCRATE_DMA_POOL_FAULTED) {
			errno = EIO;
			return -1;
		}
		if (!(status.pool_flags & QCRATE_DMA_POOL_RUNNING)) {
			errno = ESHUTDOWN;
			return -1;
		}
		nanosleep(&delay, NULL);
	}
	errno = ETIMEDOUT;
	return -1;
}

static int validate_bank(const struct sustained_context *context,
			 const struct qcrate_dma_pool_dequeue *bank)
{
	size_t expected = (size_t)context->options->words *
		context->options->frames * sizeof(uint32_t);

	if (bank->run_id != context->pool_run_id ||
	    bank->bank_index >= context->options->bank_count ||
	    bank->offset_bytes > context->buffer_bytes ||
	    bank->valid_bytes > context->buffer_bytes - bank->offset_bytes ||
	    bank->valid_bytes != expected || bank->dma_result ||
	    bank->last_residue_bytes ||
	    bank->completed_frames != context->options->frames ||
	    bank->frame_length_words != context->options->words ||
	    bank->frame_count != context->options->frames ||
	    bank->stream_mode != context->options->stream_mode ||
	    !bank->trigger_shot_id || bank->trigger_count != 1 ||
	    !(bank->timestamp_flags & QCRATE_DMA_TRIGGER_SEEN) ||
	    !(bank->timestamp_flags & QCRATE_DMA_FIRST_SAMPLE_TIME_VALID)) {
		errno = EPROTO;
		return -1;
	}
	return 0;
}

static void *acquisition_thread(void *argument)
{
	struct sustained_context *context = argument;
	struct qcrate_dma_pool_dequeue bank;
	uint64_t previous_first_sample_time = 0;
	uint32_t previous_shot_id = 0;
	bool have_previous = false;
	uint64_t shot;

	if (sequence_require_committed_idle(context)) {
		pipeline_fail(context, "R5 sequence is not committed and idle", errno);
		goto done;
	}
	for (shot = 0; shot < context->options->triggered_shots; shot++) {
		if (stop_requested || queue_failed(&context->queue))
			break;
		if (wait_pool_armed(context)) {
			pipeline_fail(context, "waiting for an armed DMA bank", errno);
			break;
		}
		if (sequence_arm_start(context)) {
			pipeline_fail(context, "R5 sequence arm/start", errno);
			break;
		}
		memset(&bank, 0, sizeof(bank));
		bank.timeout_ms = context->options->timeout_ms;
		if (ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_DEQUEUE, &bank)) {
			pipeline_fail(context, "DMA pool dequeue", errno);
			break;
		}
		if (validate_bank(context, &bank)) {
			pipeline_fail(context, "DMA bank evidence validation", errno);
			break;
		}
		if (have_previous &&
		    (bank.trigger_shot_id <= previous_shot_id ||
		     bank.first_sample_time <= previous_first_sample_time)) {
			pipeline_fail(context,
				      "non-monotonic hardware shot evidence", EPROTO);
			break;
		}
		previous_shot_id = bank.trigger_shot_id;
		previous_first_sample_time = bank.first_sample_time;
		have_previous = true;
		if (sequence_wait_idle(context)) {
			pipeline_fail(context, "R5 sequence completion", errno);
			break;
		}
		if (queue_push(&context->queue, &bank))
			break;
		context->acquired_shots++;
	}
done:
	queue_finish(&context->queue);
	return NULL;
}

static int release_bank(struct sustained_context *context,
			const struct qcrate_dma_pool_dequeue *bank)
{
	struct qcrate_dma_pool_release release = {
		.bank_index = bank->bank_index,
		.bank_generation = bank->bank_generation,
		.run_id = bank->run_id,
		.bank_sequence = bank->bank_sequence,
	};

	return ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_RELEASE, &release);
}

static void *sender_thread(void *argument)
{
	struct sustained_context *context = argument;
	struct qcrate_data_packetizer_result result;
	struct qcrate_data_packetizer_config profile;
	struct qcrate_dma_pool_dequeue bank;
	int pop_status;
	int packet_status;

	while ((pop_status = queue_pop(&context->queue, &bank)) > 0) {
		fill_profile(context->options, &profile);
		profile.run_id = context->options->run_id;
		profile.shot_id = bank.trigger_shot_id;
		profile.initial_sequence = context->packet_sequence;
		profile.first_sample_timestamp = bank.first_sample_time;
		profile.first_sample_timestamp_valid = true;
		profile.end_of_stream = false;
		packet_status = qcrate_data_packetize(
			&profile, context->buffer + bank.offset_bytes,
			bank.valid_bytes, emit_udp, context->emitter, &result);
		if (packet_status != QCRATE_DATA_PACKETIZER_OK) {
			int error_number = context->emitter->error_number ?
				context->emitter->error_number : EPROTO;

			pipeline_fail(context, "UDP packetization/transmission",
				      error_number);
			break;
		}
		if (release_bank(context, &bank)) {
			pipeline_fail(context, "DMA bank release", errno);
			break;
		}
		context->packet_sequence = result.next_sequence;
		context->transmitted_shots++;
		context->payload_bytes += result.payload_bytes;
		context->last_shot_id = bank.trigger_shot_id;
		context->last_first_sample_time = bank.first_sample_time;
	}
	if (pop_status == 0 &&
	    context->transmitted_shots == context->options->triggered_shots) {
		packet_status = qcrate_data_emit_end_of_stream(
			context->options->stream_id, context->options->run_id,
			context->last_shot_id, context->packet_sequence, emit_udp,
			context->emitter, &context->packet_sequence);
		if (packet_status != QCRATE_DATA_PACKETIZER_OK) {
			int error_number = context->emitter->error_number ?
				context->emitter->error_number : EPROTO;

			pipeline_fail(context, "UDP end-of-stream transmission",
				      error_number);
		}
	}
	if (pop_status < 0)
		return NULL;
	return NULL;
}

static void abort_sequence_best_effort(struct sustained_context *context)
{
	struct qcrate_rpmsg_message response;

	if (sequence_get_status(context, &response))
		return;
	if (!(response.payload[0] & QCRATE_SEQ_STATUS_IDLE))
		(void)sequence_exchange(context, QCRATE_CMD_SEQ_ABORT, NULL, 0,
					&response);
}

static int run_sustained(struct sustained_context *context)
{
	struct qcrate_dma_pool_status pool_status = {0};
	struct qcrate_dma_pool_stop pool_stop = {
		.timeout_ms = context->options->timeout_ms,
	};
	struct qcrate_dma_pool_start pool_start = {
		.frame_length_words = context->options->words,
		.frame_count = context->options->frames,
		.stream_mode = context->options->stream_mode,
		.bank_count = context->options->bank_count,
		.arm_timeout_ms = context->options->timeout_ms,
		.flags = QCRATE_DMA_POOL_START_TRIGGERED,
	};
	pthread_t acquisition;
	pthread_t sender;
	bool acquisition_created = false;
	bool sender_created = false;
	int thread_status;
	int result = -1;

	thread_status = queue_init(&context->queue, context->options->bank_count);
	if (thread_status) {
		errno = thread_status;
		return -1;
	}
	if (install_signal_handlers())
		goto out;
	if (ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_START, &pool_start))
		goto out;
	context->pool_run_id = pool_start.run_id;
	pool_stop.run_id = pool_start.run_id;
	if (!pool_start.run_id ||
	    pool_start.bank_bytes != (size_t)context->options->words *
		context->options->frames * sizeof(uint32_t)) {
		errno = EPROTO;
		goto stop;
	}
	thread_status = pthread_create(&sender, NULL, sender_thread, context);
	if (thread_status) {
		errno = thread_status;
		goto stop;
	}
	sender_created = true;
	thread_status = pthread_create(&acquisition, NULL, acquisition_thread,
				       context);
	if (thread_status) {
		errno = thread_status;
		pipeline_fail(context, "acquisition thread creation", errno);
		goto join;
	}
	acquisition_created = true;

join:
	if (acquisition_created)
		pthread_join(acquisition, NULL);
	else
		queue_finish(&context->queue);
	if (sender_created)
		pthread_join(sender, NULL);
	if (ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_STATUS, &pool_status))
		pipeline_fail(context, "DMA pool status", errno);
	if (!queue_failed(&context->queue) && !stop_requested &&
	    context->acquired_shots == context->options->triggered_shots &&
	    context->transmitted_shots == context->options->triggered_shots)
		result = 0;
	else if (!queue_failed(&context->queue) && stop_requested)
		errno = EINTR;

stop:
	if (ioctl(context->device_fd, QCRATE_DMA_IOC_POOL_STOP, &pool_stop) &&
	    result == 0) {
		result = -1;
	}
	if (result)
		abort_sequence_best_effort(context);
out:
	if (context->queue.failed) {
		errno = context->queue.error_number;
		fprintf(stderr, "%s failed: %s\n", context->queue.error_context,
			strerror(errno));
	}
	if (!result) {
		printf("PASS Q-Crate sustained triggered run transmitted\n");
		printf("shots acquired/sent: %" PRIu64 " / %" PRIu64 "\n",
		       context->acquired_shots, context->transmitted_shots);
		printf("pool run/data run : 0x%016" PRIx64 " / 0x%016" PRIx64
		       "\n", context->pool_run_id, context->options->run_id);
		printf("last shot/time    : %u / %" PRIu64 " ticks\n",
		       context->last_shot_id, context->last_first_sample_time);
		printf("payload bytes     : %" PRIu64 "\n", context->payload_bytes);
		printf("packet sequence   : 0..%" PRIu64 "\n",
		       context->packet_sequence ? context->packet_sequence - 1 : 0);
		printf("token queue HWM   : %zu banks\n",
		       context->queue.high_water);
		printf("DMA ready HWM     : %u banks\n",
		       pool_status.queue_high_water);
		printf("starvation/skipped: %" PRIu64 " / %" PRIu64 "\n",
		       (uint64_t)pool_status.starvation_events,
		       (uint64_t)pool_status.skipped_triggers);
	}
	queue_destroy(&context->queue);
	return result;
}

int main(int argc, char **argv)
{
	struct qcrate_data_packetizer_config profile;
	struct qcrate_data_packetizer_result packet_result;
	struct qcrate_dma_capture_frames capture = {0};
	struct qcrate_dma_info info = {0};
	struct qcrate_dma_caps caps = {0};
	struct udp_emitter emitter = {0};
	struct qcrate_rpmsg_client rpmsg = {.file_descriptor = -1};
	struct sustained_context sustained = {0};
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
	    info.max_transfer_bytes || total_bytes > info.buffer_bytes ||
	    (options.triggered_mode &&
	     options.bank_count > info.buffer_bytes / total_bytes)) {
		fprintf(stderr,
			"capture exceeds DMA frame or coherent-buffer limit\n");
		goto out;
	}
	buffer = mmap(NULL, info.buffer_bytes, PROT_READ, MAP_SHARED, device_fd, 0);
	if (buffer == MAP_FAILED) {
		perror("DMA buffer mmap failed");
		goto out;
	}
	emitter.socket_fd = socket_fd;
	emitter.rate_bps = (uint64_t)options.rate_mbps * UINT64_C(1000000);
	if (options.triggered_mode) {
		if (!(caps.feature_flags & QCRATE_DMA_CAP_BANK_POOL) ||
		    !(caps.feature_flags & QCRATE_DMA_CAP_TRIGGERED)) {
			fprintf(stderr,
				"driver does not advertise triggered bank pools\n");
			goto out;
		}
		if (qcrate_rpmsg_client_open(&rpmsg, options.rpmsg_device,
					      (int)options.sequence_timeout_ms)) {
			perror("R5 RPMsg endpoint open failed");
			goto out;
		}
		sustained = (struct sustained_context) {
			.options = &options,
			.device_fd = device_fd,
			.buffer = buffer,
			.buffer_bytes = info.buffer_bytes,
			.emitter = &emitter,
			.rpmsg = &rpmsg,
		};
		if (run_sustained(&sustained)) {
			if (errno == EINTR)
				fprintf(stderr, "triggered run interrupted cleanly\n");
			else
				perror("triggered run failed");
			goto out;
		}
		printf("destination        : %s:%s\n",
		       options.destination, options.port);
		printf("SO_SNDBUF          : requested %u, actual %d bytes\n",
		       options.send_buffer, actual_send_buffer);
		printf("UDP pacing         : %u Mb/s%s\n", options.rate_mbps,
		       options.rate_mbps ? "" : " (disabled)");
		status = EXIT_SUCCESS;
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
	profile.end_of_stream = true;
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
	qcrate_rpmsg_client_close(&rpmsg);
	if (buffer != MAP_FAILED)
		munmap(buffer, info.buffer_bytes);
	if (device_fd >= 0)
		close(device_fd);
	if (socket_fd >= 0)
		close(socket_fd);
	return status;
}
