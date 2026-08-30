/* SPDX-License-Identifier: MIT */
/*
 * Linux userspace client for the Q-Crate R5 FreeRTOS control service.
 *
 * The program talks only through rpmsg_char. It discovers the endpoint by its
 * service name in sysfs, sends one fixed-size protocol request, and validates
 * every response header before interpreting the payload.
 */

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "qcrate_rpmsg_client.h"

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

static int exchange_message(struct qcrate_rpmsg_client *client,
			    struct qcrate_rpmsg_message *message)
{
	struct qcrate_rpmsg_message response;
	int result;

	result = qcrate_rpmsg_client_exchange(client, message->command,
		message->payload, message->payload_words, &response);
	if (result && errno == EREMOTEIO) {
		fprintf(stderr, "qcrate-control: R5 service status %" PRId32 "\n",
			response.status);
		return -1;
	}
	if (result) {
		fprintf(stderr, "qcrate-control: RPMsg exchange failed: %s\n",
			strerror(errno));
		return -1;
	}
	*message = response;
	return 0;
}

static void init_request(struct qcrate_rpmsg_message *message, uint16_t command)
{
	memset(message, 0, sizeof(*message));
	message->command = command;
}

static int run_ping(struct qcrate_rpmsg_client *client, uint32_t nonce)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_PING);
	message.payload_words = 1U;
	message.payload[0] = nonce;
	if (exchange_message(client, &message) != 0)
		return -1;
	if (message.payload_words != 1U || message.payload[0] != nonce) {
		fprintf(stderr, "qcrate-control: ping payload mismatch\n");
		return -1;
	}
	printf("PASS ping 0x%08" PRIx32 "\n", nonce);
	return 0;
}

static int run_info(struct qcrate_rpmsg_client *client)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_GET_INFO);
	if (exchange_message(client, &message) != 0)
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

static int run_scratch_test(struct qcrate_rpmsg_client *client, uint32_t value)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, QCRATE_CMD_SCRATCH_TEST);
	message.payload_words = 1U;
	message.payload[0] = value;
	if (exchange_message(client, &message) != 0)
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

static int run_stats(struct qcrate_rpmsg_client *client)
{
	struct qcrate_rpmsg_message message;
	uint64_t uptime_ms;

	init_request(&message, QCRATE_CMD_GET_R5_STATS);
	if (exchange_message(client, &message) != 0)
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
	struct qcrate_rpmsg_client client;
	uint32_t argument;
	int timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
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

	if (qcrate_rpmsg_client_open(&client, device, timeout_ms)) {
		fprintf(stderr, "qcrate-control: %s endpoint is unavailable: %s\n",
			QCRATE_RPMSG_SERVICE_NAME, strerror(errno));
		return EXIT_FAILURE;
	}

	if (strcmp(command, "ping") == 0) {
		argument = UINT32_C(0x51435250);
		if (index < argc && parse_u32(argv[index++], &argument) != 0)
			status = -1;
		else if (index != argc)
			status = -1;
		else
			status = run_ping(&client, argument);
	} else if (strcmp(command, "info") == 0 && index == argc) {
		status = run_info(&client);
	} else if (strcmp(command, "stats") == 0 && index == argc) {
		status = run_stats(&client);
	} else if (strcmp(command, "scratch-test") == 0) {
		argument = UINT32_C(0xA5A55A5A);
		if (index < argc && parse_u32(argv[index++], &argument) != 0)
			status = -1;
		else if (index != argc)
			status = -1;
		else
			status = run_scratch_test(&client, argument);
	} else if (strcmp(command, "test") == 0 && index == argc) {
		if (run_ping(&client, UINT32_C(0x51435250)) != 0 ||
		    run_info(&client) != 0 ||
		    run_scratch_test(&client, UINT32_C(0xA5A55A5A)) != 0 ||
		    run_stats(&client) != 0)
			status = -1;
		else
			printf("PASS Q-Crate OpenAMP vertical slice\n");
	} else {
		status = -1;
	}

	qcrate_rpmsg_client_close(&client);
	if (status != 0) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	return EXIT_SUCCESS;
}
