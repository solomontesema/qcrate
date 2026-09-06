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
#define QCRATE_STREAM_CLOCK_HZ    UINT32_C(200000000)

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
		"  test                  run ping, info, scratch-test, and stats\n"
		"  config-stage ID SIGNAL_INC SIGNAL_PHASE SIGNAL_AMP NOISE_AMP "
		"SEED LO_INC LO_PHASE FRAME_WORDS FRAMES\n"
		"  config-validate       validate the R5-owned staged profile\n"
		"  config-commit         atomically activate the validated profile\n"
		"  config-status         report R5 and PL configuration state\n"
		"  config-recover        discard an incomplete staged transaction\n"
		"  config-apply ...      stage, validate, and commit the ten values above\n",
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

static int parse_u64(const char *text, uint64_t *value)
{
	char *end;
	unsigned long long parsed;

	errno = 0;
	parsed = strtoull(text, &end, 0);
	if (errno != 0 || end == text || *end != '\0')
		return -1;
	*value = (uint64_t)parsed;
	return 0;
}

static int exchange_message(struct qcrate_rpmsg_client *client,
			    struct qcrate_rpmsg_message *message)
{
	struct qcrate_rpmsg_message response;
	int result;

	result = qcrate_rpmsg_client_exchange(client, message->command,
		message->payload, message->payload_words, &response);
	*message = response;
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

static const char *config_reason_name(uint32_t reason)
{
	switch (reason) {
	case QCRATE_CONFIG_REASON_NONE: return "none";
	case QCRATE_CONFIG_REASON_NOT_STAGED: return "no validated staged profile";
	case QCRATE_CONFIG_REASON_ZERO_ID: return "configuration ID is zero";
	case QCRATE_CONFIG_REASON_SIGNAL_AMPLITUDE: return "invalid signal amplitude";
	case QCRATE_CONFIG_REASON_NOISE_AMPLITUDE: return "invalid noise amplitude";
	case QCRATE_CONFIG_REASON_AMPLITUDE_SUM: return "signal plus noise exceeds unity";
	case QCRATE_CONFIG_REASON_ZERO_NOISE_SEED: return "noise seed is zero or malformed";
	case QCRATE_CONFIG_REASON_FRAME_LENGTH: return "invalid frame length";
	case QCRATE_CONFIG_REASON_FRAME_COUNT: return "invalid frame count";
	case QCRATE_CONFIG_REASON_TOTAL_WORDS: return "acquisition exceeds word limit";
	case QCRATE_CONFIG_REASON_SEQUENCE_STATE: return "sequence engine is not idle";
	case QCRATE_CONFIG_REASON_STREAM_STATE: return "stream engine is not idle";
	case QCRATE_CONFIG_REASON_SHADOW_VERIFY: return "shadow register verification failed";
	case QCRATE_CONFIG_REASON_PL_REJECTED: return "PL rejected the safe-boundary commit";
	case QCRATE_CONFIG_REASON_ACTIVE_VERIFY: return "active register verification failed";
	case QCRATE_CONFIG_REASON_COMMIT_TIMEOUT: return "PL commit timed out";
	default: return "unknown";
	}
}

static void print_config_status(const struct qcrate_rpmsg_message *message)
{
	uint64_t config_id;
	double signal_hz;
	double lo_hz;

	if (message->payload_words != QCRATE_CONFIG_STATUS_WORDS) {
		fprintf(stderr, "qcrate-control: invalid configuration status payload\n");
		return;
	}
	config_id = ((uint64_t)message->payload[5] << 32U) | message->payload[4];
	signal_hz = (double)message->payload[6] * QCRATE_STREAM_CLOCK_HZ /
		4294967296.0;
	lo_hz = (double)message->payload[7] * QCRATE_STREAM_CLOCK_HZ /
		4294967296.0;
	printf("R5 staged        : %s\n",
	       (message->payload[0] & QCRATE_CONFIG_FLAG_STAGED) ? "yes" : "no");
	printf("R5 validated     : %s\n",
	       (message->payload[0] & QCRATE_CONFIG_FLAG_VALIDATED) ? "yes" : "no");
	printf("PL status        : 0x%08" PRIx32 "\n", message->payload[1]);
	printf("PL reject reason : %" PRIu32 "\n", message->payload[2]);
	printf("active generation: %" PRIu32 "\n", message->payload[3]);
	printf("active config ID : 0x%016" PRIx64 "\n", config_id);
	printf("signal tuning    : 0x%08" PRIx32 " (%.6f Hz)\n",
	       message->payload[6], signal_hz);
	printf("LO tuning        : 0x%08" PRIx32 " (%.6f Hz)\n",
	       message->payload[7], lo_hz);
	printf("frame geometry   : %" PRIu32 " words x %" PRIu32 " frames\n",
	       message->payload[8], message->payload[9]);
	printf("R5 reason        : %" PRIu32 " (%s)\n", message->payload[10],
	       config_reason_name(message->payload[10]));
}

static int config_exchange(struct qcrate_rpmsg_client *client, uint16_t command,
			   const uint32_t *payload, uint32_t payload_words,
			   const char *success)
{
	struct qcrate_rpmsg_message message;

	init_request(&message, command);
	message.payload_words = payload_words;
	if (payload_words != 0U)
		memcpy(message.payload, payload, payload_words * sizeof(payload[0]));
	if (exchange_message(client, &message) != 0) {
		if (message.payload_words == QCRATE_CONFIG_STATUS_WORDS)
			print_config_status(&message);
		return -1;
	}
	print_config_status(&message);
	if (success != NULL)
		printf("PASS %s\n", success);
	return 0;
}

static int parse_config_payload(int argc, char **argv, uint32_t *payload)
{
	uint64_t config_id;
	uint32_t index;

	if (argc != 10 || parse_u64(argv[0], &config_id) != 0 || config_id == 0U)
		return -1;
	payload[QCRATE_CONFIG_ID_LOW_WORD] = (uint32_t)config_id;
	payload[QCRATE_CONFIG_ID_HIGH_WORD] = (uint32_t)(config_id >> 32U);
	for (index = 0U; index < 9U; index++) {
		if (parse_u32(argv[index + 1U], &payload[index + 2U]) != 0)
			return -1;
	}
	return 0;
}

static int run_config_apply(struct qcrate_rpmsg_client *client,
			    const uint32_t *payload)
{
	if (config_exchange(client, QCRATE_CMD_CONFIG_STAGE, payload,
		QCRATE_CONFIG_STAGE_WORDS, "R5 staged resolved profile") != 0 ||
	    config_exchange(client, QCRATE_CMD_CONFIG_VALIDATE, NULL, 0U,
		"R5 validated resolved profile") != 0 ||
	    config_exchange(client, QCRATE_CMD_CONFIG_COMMIT, NULL, 0U,
		"R5 committed resolved profile") != 0)
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	const char *device = NULL;
	const char *command;
	struct qcrate_rpmsg_client client;
	uint32_t argument;
	uint32_t config_payload[QCRATE_CONFIG_STAGE_WORDS];
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
	} else if ((strcmp(command, "config-stage") == 0 ||
		    strcmp(command, "config-apply") == 0) &&
		   parse_config_payload(argc - index, &argv[index], config_payload) == 0) {
		if (strcmp(command, "config-apply") == 0)
			status = run_config_apply(&client, config_payload);
		else
			status = config_exchange(&client, QCRATE_CMD_CONFIG_STAGE,
				config_payload, QCRATE_CONFIG_STAGE_WORDS,
				"R5 staged resolved profile");
	} else if (strcmp(command, "config-validate") == 0 && index == argc) {
		status = config_exchange(&client, QCRATE_CMD_CONFIG_VALIDATE, NULL, 0U,
			"R5 validated resolved profile");
	} else if (strcmp(command, "config-commit") == 0 && index == argc) {
		status = config_exchange(&client, QCRATE_CMD_CONFIG_COMMIT, NULL, 0U,
			"R5 committed resolved profile");
	} else if (strcmp(command, "config-status") == 0 && index == argc) {
		status = config_exchange(&client, QCRATE_CMD_CONFIG_GET_STATUS, NULL,
			0U, NULL);
	} else if (strcmp(command, "config-recover") == 0 && index == argc) {
		status = config_exchange(&client, QCRATE_CMD_CONFIG_RECOVER, NULL, 0U,
			"R5 discarded staged configuration");
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
