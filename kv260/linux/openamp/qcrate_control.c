/* SPDX-License-Identifier: MIT */
/*
 * Linux userspace client for the Q-Crate R5 FreeRTOS control service.
 *
 * The program talks only through rpmsg_char. It discovers the endpoint by its
 * service name in sysfs, sends one fixed-size protocol request, and validates
 * every response header before interpreting the payload.
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "qcrate_protocol.h"

#define QCRATE_SYSFS_RPMSG_CLASS "/sys/class/rpmsg"
#define QCRATE_DEFAULT_TIMEOUT_MS 2000
#define QCRATE_DEVICE_ID_VALUE    UINT32_C(0x51435254)

static void usage(FILE *stream, const char *program)
{
	fprintf(stream,
		"Usage: %s [--device /dev/rpmsgN] [--timeout-ms N] COMMAND [ARG]\n"
		"\n"
		"Commands:\n"
		"  ping [nonce]          round-trip a 32-bit value\n"
		"  info                  read Q-Crate identity through R5-0\n"
		"  stats                 read R5 uptime and request counters\n"
		"  scratch-test [value]  write, verify, and restore SYS.SCRATCH\n"
		"  test                  run ping, info, scratch-test, and stats\n",
		program);
}

static int read_text_file(const char *path, char *buffer, size_t capacity)
{
	FILE *file;
	size_t length;

	file = fopen(path, "r");
	if (file == NULL)
		return -1;

	if (fgets(buffer, (int)capacity, file) == NULL) {
		fclose(file);
		return -1;
	}
	fclose(file);

	length = strlen(buffer);
	while (length > 0U &&
	       (buffer[length - 1U] == '\n' || buffer[length - 1U] == '\r'))
		buffer[--length] = '\0';
	return 0;
}

static int discover_endpoint(char *device_path, size_t capacity)
{
	struct dirent *entry;
	DIR *directory;
	char name_path[512];
	char service_name[64];

	directory = opendir(QCRATE_SYSFS_RPMSG_CLASS);
	if (directory == NULL)
		return -1;

	while ((entry = readdir(directory)) != NULL) {
		if (strncmp(entry->d_name, "rpmsg", 5U) != 0)
			continue;
		if (snprintf(name_path, sizeof(name_path), "%s/%s/name",
			     QCRATE_SYSFS_RPMSG_CLASS, entry->d_name) >=
		    (int)sizeof(name_path))
			continue;
		if (read_text_file(name_path, service_name,
				   sizeof(service_name)) != 0)
			continue;
		if (strcmp(service_name, QCRATE_RPMSG_SERVICE_NAME) != 0)
			continue;

		if (snprintf(device_path, capacity, "/dev/%s", entry->d_name) >=
		    (int)capacity) {
			closedir(directory);
			errno = ENAMETOOLONG;
			return -1;
		}
		closedir(directory);
		return 0;
	}

	closedir(directory);
	errno = ENODEV;
	return -1;
}

static int parse_u32(const char *text, uint32_t *value)
{
	char *end;
	unsigned long parsed;

	errno = 0;
	parsed = strtoul(text, &end, 0);
	if (errno != 0 || end == text || *end != '\0' || parsed > UINT32_MAX)
		return -1;
	*value = (uint32_t)parsed;
	return 0;
}

static uint32_t next_transaction_id(void)
{
	struct timespec now;
	uint32_t value;

	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
		return (uint32_t)getpid();
	value = (uint32_t)now.tv_nsec ^ (uint32_t)now.tv_sec;
	return value ^ (uint32_t)getpid();
}

static int exchange_message(int file_descriptor, int timeout_ms,
			    struct qcrate_rpmsg_message *message)
{
	struct qcrate_rpmsg_message response;
	struct pollfd poll_descriptor;
	ssize_t count;

	count = write(file_descriptor, message, sizeof(*message));
	if (count != (ssize_t)sizeof(*message)) {
		if (count >= 0)
			errno = EIO;
		perror("qcrate-control: RPMsg write");
		return -1;
	}

	poll_descriptor.fd = file_descriptor;
	poll_descriptor.events = POLLIN;
	poll_descriptor.revents = 0;
	count = poll(&poll_descriptor, 1U, timeout_ms);
	if (count == 0) {
		fprintf(stderr, "qcrate-control: response timed out after %d ms\n",
			timeout_ms);
		return -1;
	}
	if (count < 0) {
		perror("qcrate-control: poll");
		return -1;
	}
	if ((poll_descriptor.revents & POLLIN) == 0) {
		fprintf(stderr, "qcrate-control: endpoint poll error 0x%x\n",
			poll_descriptor.revents);
		return -1;
	}

	count = read(file_descriptor, &response, sizeof(response));
	if (count != (ssize_t)sizeof(response)) {
		if (count < 0)
			perror("qcrate-control: RPMsg read");
		else
			fprintf(stderr,
				"qcrate-control: short response (%zd, expected %zu)\n",
				count, sizeof(response));
		return -1;
	}

	if (response.magic != QCRATE_RPMSG_MAGIC ||
	    response.abi_version != QCRATE_RPMSG_ABI_VERSION ||
	    response.command != message->command ||
	    response.transaction_id != message->transaction_id ||
	    response.payload_words > QCRATE_RPMSG_PAYLOAD_WORDS) {
		fprintf(stderr, "qcrate-control: invalid response header\n");
		return -1;
	}

	*message = response;
	if (message->status != QCRATE_STATUS_OK) {
		fprintf(stderr, "qcrate-control: R5 service status %" PRId32 "\n",
			message->status);
		return -1;
	}
	return 0;
}

static void init_request(struct qcrate_rpmsg_message *message, uint16_t command)
{
	memset(message, 0, sizeof(*message));
	message->magic = QCRATE_RPMSG_MAGIC;
	message->abi_version = QCRATE_RPMSG_ABI_VERSION;
	message->command = command;
	message->transaction_id = next_transaction_id();
}

static int run_ping(int file_descriptor, int timeout_ms, uint32_t nonce)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_PING);
	message.payload_words = 1U;
	message.payload[0] = nonce;
	if (exchange_message(file_descriptor, timeout_ms, &message) != 0)
		return -1;
	if (message.payload_words != 1U || message.payload[0] != nonce) {
		fprintf(stderr, "qcrate-control: ping payload mismatch\n");
		return -1;
	}
	printf("PASS ping 0x%08" PRIx32 "\n", nonce);
	return 0;
}

static int run_info(int file_descriptor, int timeout_ms)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_GET_INFO);
	if (exchange_message(file_descriptor, timeout_ms, &message) != 0)
		return -1;
	if (message.payload_words != 6U ||
	    message.payload[0] != QCRATE_DEVICE_ID_VALUE) {
		fprintf(stderr, "qcrate-control: invalid Q-Crate identity response\n");
		return -1;
	}

	printf("DEVICE_ID       : 0x%08" PRIx32 "\n", message.payload[0]);
	printf("VERSION         : 0x%08" PRIx32 "\n", message.payload[1]);
	printf("BUILD_ID        : 0x%08" PRIx32 "\n", message.payload[2]);
	printf("CAPABILITIES    : 0x%08" PRIx32 "\n", message.payload[3]);
	printf("stream clock    : %" PRIu32 " Hz\n", message.payload[4]);
	printf("control clock   : %" PRIu32 " Hz\n", message.payload[5]);
	printf("PASS R5-mediated Q-Crate identity\n");
	return 0;
}

static int run_scratch_test(int file_descriptor, int timeout_ms, uint32_t value)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_SCRATCH_TEST);
	message.payload_words = 1U;
	message.payload[0] = value;
	if (exchange_message(file_descriptor, timeout_ms, &message) != 0)
		return -1;
	if (message.payload_words != 5U || message.payload[1] != value ||
	    message.payload[2] != value || message.payload[3] != message.payload[0] ||
	    message.payload[4] != message.payload[0]) {
		fprintf(stderr, "qcrate-control: scratch test response mismatch\n");
		return -1;
	}

	printf("PASS scratch 0x%08" PRIx32
	       " (original/restored 0x%08" PRIx32 ")\n",
	       value, message.payload[0]);
	return 0;
}

static int run_stats(int file_descriptor, int timeout_ms)
{
	struct qcrate_rpmsg_message message;
	uint64_t uptime_ms;

	init_request(&message, QCRATE_CMD_GET_R5_STATS);
	if (exchange_message(file_descriptor, timeout_ms, &message) != 0)
		return -1;
	if (message.payload_words != 4U || message.payload[1] == 0U) {
		fprintf(stderr, "qcrate-control: invalid R5 statistics response\n");
		return -1;
	}

	uptime_ms = (uint64_t)message.payload[0] * UINT64_C(1000) /
		    message.payload[1];
	printf("R5 uptime ticks : %" PRIu32 "\n", message.payload[0]);
	printf("tick rate       : %" PRIu32 " Hz\n", message.payload[1]);
	printf("accepted        : %" PRIu32 "\n", message.payload[2]);
	printf("rejected        : %" PRIu32 "\n", message.payload[3]);
	printf("uptime          : %" PRIu64 ".%03" PRIu64 " seconds\n",
	       uptime_ms / UINT64_C(1000), uptime_ms % UINT64_C(1000));
	return 0;
}

int main(int argc, char **argv)
{
	const char *device = NULL;
	const char *command;
	char discovered_device[64];
	uint32_t argument;
	int timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	int file_descriptor;
	int index = 1;
	int status = 0;

	while (index < argc && strncmp(argv[index], "--", 2U) == 0) {
		if (strcmp(argv[index], "--device") == 0 && index + 1 < argc) {
			device = argv[index + 1];
			index += 2;
		} else if (strcmp(argv[index], "--timeout-ms") == 0 &&
			   index + 1 < argc) {
			uint32_t parsed_timeout;
			if (parse_u32(argv[index + 1], &parsed_timeout) != 0 ||
			    parsed_timeout == 0U || parsed_timeout > INT32_MAX) {
				fprintf(stderr, "qcrate-control: invalid timeout\n");
				return EXIT_FAILURE;
			}
			timeout_ms = (int)parsed_timeout;
			index += 2;
		} else {
			usage(stderr, argv[0]);
			return EXIT_FAILURE;
		}
	}

	if (index >= argc) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	command = argv[index++];

	if (device == NULL) {
		if (discover_endpoint(discovered_device,
				      sizeof(discovered_device)) != 0) {
			fprintf(stderr,
				"qcrate-control: %s endpoint is unavailable: %s\n",
				QCRATE_RPMSG_SERVICE_NAME, strerror(errno));
			return EXIT_FAILURE;
		}
		device = discovered_device;
	}

	file_descriptor = open(device, O_RDWR | O_CLOEXEC);
	if (file_descriptor < 0) {
		fprintf(stderr, "qcrate-control: cannot open %s: %s\n",
			device, strerror(errno));
		return EXIT_FAILURE;
	}

	if (strcmp(command, "ping") == 0) {
		argument = UINT32_C(0x51435250);
		if (index < argc && parse_u32(argv[index++], &argument) != 0)
			status = -1;
		else if (index != argc)
			status = -1;
		else
			status = run_ping(file_descriptor, timeout_ms, argument);
	} else if (strcmp(command, "info") == 0 && index == argc) {
		status = run_info(file_descriptor, timeout_ms);
	} else if (strcmp(command, "stats") == 0 && index == argc) {
		status = run_stats(file_descriptor, timeout_ms);
	} else if (strcmp(command, "scratch-test") == 0) {
		argument = UINT32_C(0xA5A55A5A);
		if (index < argc && parse_u32(argv[index++], &argument) != 0)
			status = -1;
		else if (index != argc)
			status = -1;
		else
			status = run_scratch_test(file_descriptor, timeout_ms, argument);
	} else if (strcmp(command, "test") == 0 && index == argc) {
		if (run_ping(file_descriptor, timeout_ms,
			     UINT32_C(0x51435250)) != 0 ||
		    run_info(file_descriptor, timeout_ms) != 0 ||
		    run_scratch_test(file_descriptor, timeout_ms,
				     UINT32_C(0xA5A55A5A)) != 0 ||
		    run_stats(file_descriptor, timeout_ms) != 0)
			status = -1;
		else
			printf("PASS Q-Crate OpenAMP vertical slice\n");
	} else {
		status = -1;
	}

	close(file_descriptor);
	if (status != 0) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	return EXIT_SUCCESS;
}
