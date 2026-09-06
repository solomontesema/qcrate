/*
 * Q-Crate R5-0 FreeRTOS control service.
 *
 * AMD's OpenAMP echo-test template supplies the generated ZynqMP machine
 * layer, resource table, linker script, and platform_info implementation.
 * The reproducible Vitis flow replaces only the template endpoint source and
 * header with the tracked Q-Crate implementation.
 */

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "xil_io.h"

#include <metal/log.h>
#include <openamp/open_amp.h>

#include "platform_info.h"
#include "qcrate_protocol.h"
#include "qcrate_runtime_config.h"
#include "qcrate_sequence_format.h"
#include "rpmsg-echo.h"

#define QCRATE_APB_BASE             UINT32_C(0xA0010000)
#define QCRATE_SYS_DEVICE_ID        UINT32_C(0x0000)
#define QCRATE_SYS_VERSION          UINT32_C(0x0004)
#define QCRATE_SYS_BUILD_ID         UINT32_C(0x0008)
#define QCRATE_SYS_CAPABILITIES     UINT32_C(0x000C)
#define QCRATE_SYS_SCRATCH          UINT32_C(0x0010)
#define QCRATE_SYS_STREAM_CLOCK_HZ  UINT32_C(0x0014)
#define QCRATE_SYS_CONTROL_CLOCK_HZ UINT32_C(0x0018)
#define QCRATE_DEVICE_ID_VALUE      UINT32_C(0x51435254)

#define QCRATE_SEQ_CONTROL          UINT32_C(0x2000)
#define QCRATE_SEQ_STATUS           UINT32_C(0x2004)
#define QCRATE_SEQ_EVENT_COUNT      UINT32_C(0x2008)
#define QCRATE_SEQ_ACTIVE_EVENT     UINT32_C(0x200C)
#define QCRATE_SEQ_COMPLETED_SHOTS  UINT32_C(0x2010)
#define QCRATE_SEQ_FAULT_INFO       UINT32_C(0x2014)
#define QCRATE_SEQ_TIMEBASE_LO      UINT32_C(0x2018)
#define QCRATE_SEQ_TIMEBASE_HI      UINT32_C(0x201C)
#define QCRATE_SEQ_START_TIME_LO    UINT32_C(0x2020)
#define QCRATE_SEQ_START_TIME_HI    UINT32_C(0x2024)
#define QCRATE_SEQ_ELAPSED_LO       UINT32_C(0x2028)
#define QCRATE_SEQ_ELAPSED_HI       UINT32_C(0x202C)
#define QCRATE_SEQ_EVENT_MEMORY     UINT32_C(0x2800)

#define QCRATE_SEQ_CONTROL_ARM      UINT32_C(0x00000001)
#define QCRATE_SEQ_CONTROL_START    UINT32_C(0x00000002)
#define QCRATE_SEQ_CONTROL_ABORT    UINT32_C(0x00000004)
#define QCRATE_SEQ_CONTROL_RESET    UINT32_C(0x00000008)
#define QCRATE_SEQ_CONTROL_EXTERNAL UINT32_C(0x00000100)

#define QCRATE_SEQ_HW_IDLE          UINT32_C(0x00000001)
#define QCRATE_SEQ_HW_ARMED         UINT32_C(0x00000004)
#define QCRATE_SEQ_HW_BUSY          UINT32_C(0x00000008)
#define QCRATE_SEQ_HW_FAULTED       UINT32_C(0x00000010)
#define QCRATE_SEQ_HW_COMMAND_BUSY  UINT32_C(0x00000100)
#define QCRATE_SEQ_HW_MEMORY_LOCKED UINT32_C(0x00000200)

#define QCRATE_SEQ_COMMAND_TIMEOUT_MS 100U

#define QCRATE_STREAM_FRAME_LENGTH    UINT32_C(0x1004)
#define QCRATE_STREAM_FRAME_COUNT     UINT32_C(0x1008)
#define QCRATE_STREAM_MODE            UINT32_C(0x100C)
#define QCRATE_STREAM_STATUS          UINT32_C(0x1010)
#define QCRATE_STREAM_MODE_DSP        UINT32_C(1)
#define QCRATE_STREAM_HW_BUSY         UINT32_C(0x00000001)
#define QCRATE_STREAM_HW_ARMED        UINT32_C(0x00000008)
#define QCRATE_STREAM_HW_COMMAND_BUSY UINT32_C(0x00000100)

#define QCRATE_DSP_COMMAND            UINT32_C(0x3000)
#define QCRATE_DSP_STATUS             UINT32_C(0x3004)
#define QCRATE_DSP_REJECT_REASON      UINT32_C(0x3008)
#define QCRATE_DSP_ACTIVE_GENERATION  UINT32_C(0x300C)
#define QCRATE_DSP_SHADOW_BASE        UINT32_C(0x3010)
#define QCRATE_DSP_ACTIVE_BASE        UINT32_C(0x3040)
#define QCRATE_DSP_COMMAND_COMMIT     UINT32_C(0x00000001)
#define QCRATE_DSP_COMMAND_DISCARD    UINT32_C(0x00000002)
#define QCRATE_DSP_STATUS_DIRTY       UINT32_C(0x00000001)
#define QCRATE_DSP_STATUS_BUSY        UINT32_C(0x00000002)
#define QCRATE_DSP_STATUS_VALID       UINT32_C(0x00000004)
#define QCRATE_DSP_STATUS_REJECTED    UINT32_C(0x00000008)
#define QCRATE_DSP_COMMIT_TIMEOUT_MS  100U

#define QCRATE_INFO_WORDS           6U
#define QCRATE_SCRATCH_RESULT_WORDS 5U
#define QCRATE_R5_STATS_WORDS       4U

static struct rpmsg_endpoint qcrate_endpoint;

/* AMD's OpenAMP polling task serializes endpoint callback execution. */
static uint32_t qcrate_accepted_requests;
static uint32_t qcrate_rejected_requests;

struct qcrate_sequence_load_state {
	bool active;
	bool committed;
	uint32_t expected_events;
	uint32_t next_event;
	uint32_t expected_crc32;
	uint32_t crc32_state;
	uint64_t previous_timestamp;
	uint32_t previous_state;
};

static struct qcrate_sequence_load_state qcrate_sequence_load;

struct qcrate_config_state {
	bool staged;
	bool validated;
	uint32_t reason;
	uint32_t words[QCRATE_CONFIG_STAGE_WORDS];
};

static struct qcrate_config_state qcrate_config;

static uint32_t qcrate_reg_read(uint32_t offset)
{
	return Xil_In32((UINTPTR)(QCRATE_APB_BASE + offset));
}

static void qcrate_reg_write(uint32_t offset, uint32_t value)
{
	Xil_Out32((UINTPTR)(QCRATE_APB_BASE + offset), value);
}

static void qcrate_config_status_payload(struct qcrate_rpmsg_message *response)
{
	uint32_t generation_before;
	uint32_t generation_after;
	uint32_t flags = 0U;
	uint32_t attempt;

	if (qcrate_config.staged)
		flags |= QCRATE_CONFIG_FLAG_STAGED;
	if (qcrate_config.validated)
		flags |= QCRATE_CONFIG_FLAG_VALIDATED;

	/* Generation bracketing keeps the multiword active snapshot coherent. */
	for (attempt = 0U; attempt < 3U; attempt++) {
		generation_before = qcrate_reg_read(QCRATE_DSP_ACTIVE_GENERATION);
		response->payload[4] = qcrate_reg_read(QCRATE_DSP_ACTIVE_BASE);
		response->payload[5] = qcrate_reg_read(QCRATE_DSP_ACTIVE_BASE + 4U);
		response->payload[6] = qcrate_reg_read(QCRATE_DSP_ACTIVE_BASE + 8U);
		response->payload[7] = qcrate_reg_read(QCRATE_DSP_ACTIVE_BASE + 28U);
		generation_after = qcrate_reg_read(QCRATE_DSP_ACTIVE_GENERATION);
		if (generation_before == generation_after)
			break;
	}

	response->payload_words = QCRATE_CONFIG_STATUS_WORDS;
	response->payload[0] = flags;
	response->payload[1] = qcrate_reg_read(QCRATE_DSP_STATUS);
	response->payload[2] = qcrate_reg_read(QCRATE_DSP_REJECT_REASON);
	response->payload[3] = generation_after;
	response->payload[8] = qcrate_reg_read(QCRATE_STREAM_FRAME_LENGTH);
	response->payload[9] = qcrate_reg_read(QCRATE_STREAM_FRAME_COUNT);
	response->payload[10] = qcrate_config.reason;
}

static void qcrate_config_error(struct qcrate_rpmsg_message *response,
				int32_t status, uint32_t reason)
{
	qcrate_config.reason = reason;
	response->status = status;
	qcrate_config_status_payload(response);
}

static int qcrate_config_wait_for_commit(uint32_t generation_before)
{
	TickType_t start;
	TickType_t timeout;
	uint32_t status;

	start = xTaskGetTickCount();
	timeout = pdMS_TO_TICKS(QCRATE_DSP_COMMIT_TIMEOUT_MS);
	if (timeout == 0U)
		timeout = 1U;

	for (;;) {
		status = qcrate_reg_read(QCRATE_DSP_STATUS);
		if ((status & QCRATE_DSP_STATUS_REJECTED) != 0U)
			return -2;
		if ((status & QCRATE_DSP_STATUS_BUSY) == 0U &&
		    qcrate_reg_read(QCRATE_DSP_ACTIVE_GENERATION) ==
		    generation_before + UINT32_C(1))
			return 0;
		if ((TickType_t)(xTaskGetTickCount() - start) >= timeout)
			return -1;
		taskYIELD();
	}
}

static uint32_t qcrate_crc32_word(uint32_t crc, uint32_t word)
{
	uint32_t byte_index;
	uint32_t bit_index;

	for (byte_index = 0U; byte_index < 4U; byte_index++) {
		crc ^= word & UINT32_C(0xFF);
		word >>= 8U;
		for (bit_index = 0U; bit_index < 8U; bit_index++)
			crc = (crc >> 1U) ^
			      ((crc & 1U) ? UINT32_C(0xEDB88320) : 0U);
	}
	return crc;
}

static void qcrate_sequence_status_payload(
	struct qcrate_rpmsg_message *response)
{
	uint32_t status;

	status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if (qcrate_sequence_load.active)
		status |= QCRATE_SEQ_STATUS_LOAD_ACTIVE;
	if (qcrate_sequence_load.committed)
		status |= QCRATE_SEQ_STATUS_COMMITTED;

	response->payload_words = QCRATE_SEQ_STATUS_WORDS;
	response->payload[0] = status;
	response->payload[1] = qcrate_reg_read(QCRATE_SEQ_EVENT_COUNT);
	response->payload[2] = qcrate_reg_read(QCRATE_SEQ_ACTIVE_EVENT);
	response->payload[3] = qcrate_reg_read(QCRATE_SEQ_COMPLETED_SHOTS);
	response->payload[4] = qcrate_reg_read(QCRATE_SEQ_FAULT_INFO);
	response->payload[5] = qcrate_reg_read(QCRATE_SEQ_TIMEBASE_LO);
	response->payload[6] = qcrate_reg_read(QCRATE_SEQ_TIMEBASE_HI);
	response->payload[7] = qcrate_reg_read(QCRATE_SEQ_START_TIME_LO);
	response->payload[8] = qcrate_reg_read(QCRATE_SEQ_START_TIME_HI);
	response->payload[9] = qcrate_reg_read(QCRATE_SEQ_ELAPSED_LO);
	response->payload[10] = qcrate_reg_read(QCRATE_SEQ_ELAPSED_HI);
}

static void qcrate_sequence_status_error(
	struct qcrate_rpmsg_message *response, int32_t status)
{
	response->status = status;
	qcrate_sequence_status_payload(response);
}

static void qcrate_sequence_load_error(
	struct qcrate_rpmsg_message *response, int32_t status,
	uint32_t event_index, uint32_t detail)
{
	qcrate_sequence_load.active = false;
	qcrate_sequence_load.committed = false;
	response->status = status;
	response->payload_words = 2U;
	response->payload[0] = event_index;
	response->payload[1] = detail;
}

enum qcrate_sequence_wait_condition {
	QCRATE_SEQ_WAIT_ARMED,
	QCRATE_SEQ_WAIT_STARTED,
	QCRATE_SEQ_WAIT_IDLE
};

static int qcrate_sequence_wait(enum qcrate_sequence_wait_condition condition,
				uint32_t completed_before)
{
	TickType_t start;
	TickType_t timeout;
	uint32_t status;

	start = xTaskGetTickCount();
	timeout = pdMS_TO_TICKS(QCRATE_SEQ_COMMAND_TIMEOUT_MS);
	if (timeout == 0U)
		timeout = 1U;

	for (;;) {
		status = qcrate_reg_read(QCRATE_SEQ_STATUS);
		if ((status & QCRATE_SEQ_HW_FAULTED) != 0U)
			return -2;

		if (condition == QCRATE_SEQ_WAIT_ARMED &&
		    (status & (QCRATE_SEQ_HW_ARMED |
			       QCRATE_SEQ_HW_COMMAND_BUSY)) ==
		    QCRATE_SEQ_HW_ARMED)
			return 0;
		if (condition == QCRATE_SEQ_WAIT_STARTED &&
		    (((status & QCRATE_SEQ_HW_BUSY) != 0U) ||
		     qcrate_reg_read(QCRATE_SEQ_COMPLETED_SHOTS) !=
		     completed_before))
			return 0;
		if (condition == QCRATE_SEQ_WAIT_IDLE &&
		    (status & (QCRATE_SEQ_HW_IDLE |
			       QCRATE_SEQ_HW_COMMAND_BUSY |
			       QCRATE_SEQ_HW_MEMORY_LOCKED)) ==
		    QCRATE_SEQ_HW_IDLE)
			return 0;

		if ((TickType_t)(xTaskGetTickCount() - start) >= timeout)
			return -1;
		taskYIELD();
	}
}

static void qcrate_response_init(struct qcrate_rpmsg_message *response,
				 const struct qcrate_rpmsg_message *request)
{
	memset(response, 0, sizeof(*response));
	response->magic = QCRATE_RPMSG_MAGIC;
	response->abi_version = QCRATE_RPMSG_ABI_VERSION;
	response->command = request->command;
	response->transaction_id = request->transaction_id;
	response->status = QCRATE_STATUS_OK;
}

static void qcrate_handle_ping(const struct qcrate_rpmsg_message *request,
			       struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 1U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	response->payload_words = 1U;
	response->payload[0] = request->payload[0];
}

static void qcrate_handle_get_info(const struct qcrate_rpmsg_message *request,
				   struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	response->payload_words = QCRATE_INFO_WORDS;
	response->payload[0] = qcrate_reg_read(QCRATE_SYS_DEVICE_ID);
	response->payload[1] = qcrate_reg_read(QCRATE_SYS_VERSION);
	response->payload[2] = qcrate_reg_read(QCRATE_SYS_BUILD_ID);
	response->payload[3] = qcrate_reg_read(QCRATE_SYS_CAPABILITIES);
	response->payload[4] = qcrate_reg_read(QCRATE_SYS_STREAM_CLOCK_HZ);
	response->payload[5] = qcrate_reg_read(QCRATE_SYS_CONTROL_CLOCK_HZ);

	if (response->payload[0] != QCRATE_DEVICE_ID_VALUE)
		response->status = QCRATE_STATUS_BAD_DEVICE_ID;
}

static void qcrate_handle_scratch_test(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t original;
	uint32_t readback;
	uint32_t restored;

	if (request->payload_words != 1U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	original = qcrate_reg_read(QCRATE_SYS_SCRATCH);
	qcrate_reg_write(QCRATE_SYS_SCRATCH, request->payload[0]);
	readback = qcrate_reg_read(QCRATE_SYS_SCRATCH);
	qcrate_reg_write(QCRATE_SYS_SCRATCH, original);
	restored = qcrate_reg_read(QCRATE_SYS_SCRATCH);

	response->payload_words = QCRATE_SCRATCH_RESULT_WORDS;
	response->payload[0] = original;
	response->payload[1] = request->payload[0];
	response->payload[2] = readback;
	response->payload[3] = original;
	response->payload[4] = restored;

	if (readback != request->payload[0] || restored != original)
		response->status = QCRATE_STATUS_SCRATCH_VERIFY;
}

static void qcrate_handle_get_r5_stats(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	response->payload_words = QCRATE_R5_STATS_WORDS;
	response->payload[0] = (uint32_t)xTaskGetTickCount();
	response->payload[1] = (uint32_t)configTICK_RATE_HZ;
	/* The callback fills counters after accounting for this request. */
}

static void qcrate_handle_seq_load_begin(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t event_count;
	uint32_t status;

	if (request->payload_words != QCRATE_SEQ_LOAD_BEGIN_WORDS) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	event_count = request->payload[0];
	if (event_count < QCRATE_SEQUENCE_MIN_EVENTS ||
	    event_count > QCRATE_SEQUENCE_MAX_EVENTS) {
		qcrate_sequence_load_error(response,
			QCRATE_STATUS_BAD_SEQUENCE, 0U,
			QCRATE_SEQUENCE_FAULT_BAD_EVENT_COUNT);
		return;
	}

	status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if ((status & (QCRATE_SEQ_HW_IDLE | QCRATE_SEQ_HW_COMMAND_BUSY |
		       QCRATE_SEQ_HW_MEMORY_LOCKED | QCRATE_SEQ_HW_FAULTED)) !=
	    QCRATE_SEQ_HW_IDLE) {
		qcrate_sequence_status_error(response, QCRATE_STATUS_BAD_STATE);
		return;
	}

	memset(&qcrate_sequence_load, 0, sizeof(qcrate_sequence_load));
	qcrate_sequence_load.active = true;
	qcrate_sequence_load.expected_events = event_count;
	qcrate_sequence_load.expected_crc32 = request->payload[1];
	qcrate_sequence_load.crc32_state = UINT32_MAX;

	response->payload_words = QCRATE_SEQ_LOAD_BEGIN_WORDS;
	response->payload[0] = event_count;
	response->payload[1] = request->payload[1];
}

static uint32_t qcrate_sequence_validate_event(uint32_t index,
	uint64_t timestamp, uint32_t output_state, uint32_t flags)
{
	if ((output_state & ~QCRATE_SEQUENCE_OUTPUT_MASK) != 0U)
		return QCRATE_SEQUENCE_FAULT_RESERVED_STATE;
	if (flags != 0U)
		return QCRATE_SEQUENCE_FAULT_RESERVED_FLAGS;
	if (output_state == qcrate_sequence_load.previous_state)
		return QCRATE_SEQUENCE_FAULT_NO_STATE_CHANGE;
	if (index != 0U &&
	    timestamp <= qcrate_sequence_load.previous_timestamp)
		return QCRATE_SEQUENCE_FAULT_TIME_ORDER;
	if (index + 1U == qcrate_sequence_load.expected_events &&
	    output_state != 0U)
		return QCRATE_SEQUENCE_FAULT_UNSAFE_FINAL;
	return QCRATE_SEQUENCE_FAULT_NONE;
}

static void qcrate_handle_seq_load_event(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t words[4];
	uint32_t event_index;
	uint32_t fault;
	uint32_t word_index;
	uint32_t readback;
	uint64_t timestamp;

	if (request->payload_words != QCRATE_SEQ_LOAD_EVENT_WORDS) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	if (!qcrate_sequence_load.active) {
		qcrate_sequence_status_error(response, QCRATE_STATUS_BAD_STATE);
		return;
	}

	event_index = request->payload[0];
	if (event_index != qcrate_sequence_load.next_event ||
	    event_index >= qcrate_sequence_load.expected_events) {
		qcrate_sequence_load_error(response, QCRATE_STATUS_BAD_INDEX,
			event_index, qcrate_sequence_load.next_event);
		return;
	}

	words[0] = request->payload[1];
	words[1] = request->payload[2];
	words[2] = request->payload[3];
	words[3] = request->payload[4];
	timestamp = ((uint64_t)words[1] << 32U) | words[0];
	fault = qcrate_sequence_validate_event(event_index, timestamp,
		words[2], words[3]);
	if (fault != QCRATE_SEQUENCE_FAULT_NONE) {
		qcrate_sequence_load_error(response,
			QCRATE_STATUS_BAD_SEQUENCE, event_index, fault);
		return;
	}

	for (word_index = 0U; word_index < 4U; word_index++) {
		uint32_t offset = QCRATE_SEQ_EVENT_MEMORY +
			event_index * QCRATE_SEQUENCE_EVENT_SIZE +
			word_index * sizeof(uint32_t);

		qcrate_reg_write(offset, words[word_index]);
		readback = qcrate_reg_read(offset);
		if (readback != words[word_index]) {
			qcrate_sequence_load.active = false;
			qcrate_sequence_load.committed = false;
			response->status = QCRATE_STATUS_SEQUENCE_VERIFY;
			response->payload_words = 4U;
			response->payload[0] = event_index;
			response->payload[1] = word_index;
			response->payload[2] = words[word_index];
			response->payload[3] = readback;
			return;
		}
		qcrate_sequence_load.crc32_state = qcrate_crc32_word(
			qcrate_sequence_load.crc32_state, words[word_index]);
	}

	qcrate_sequence_load.previous_timestamp = timestamp;
	qcrate_sequence_load.previous_state = words[2];
	qcrate_sequence_load.next_event++;
	response->payload_words = 1U;
	response->payload[0] = event_index;
}

static void qcrate_handle_seq_load_commit(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t actual_crc32;
	uint32_t readback;

	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	if (!qcrate_sequence_load.active ||
	    qcrate_sequence_load.next_event !=
	    qcrate_sequence_load.expected_events) {
		qcrate_sequence_load_error(response, QCRATE_STATUS_BAD_STATE,
			qcrate_sequence_load.next_event,
			qcrate_sequence_load.expected_events);
		return;
	}

	actual_crc32 = qcrate_sequence_load.crc32_state ^ UINT32_MAX;
	if (actual_crc32 != qcrate_sequence_load.expected_crc32) {
		qcrate_sequence_load.active = false;
		qcrate_sequence_load.committed = false;
		response->status = QCRATE_STATUS_SEQUENCE_CRC;
		response->payload_words = 2U;
		response->payload[0] = qcrate_sequence_load.expected_crc32;
		response->payload[1] = actual_crc32;
		return;
	}

	qcrate_reg_write(QCRATE_SEQ_EVENT_COUNT,
		qcrate_sequence_load.expected_events);
	readback = qcrate_reg_read(QCRATE_SEQ_EVENT_COUNT);
	if (readback != qcrate_sequence_load.expected_events) {
		qcrate_sequence_load.active = false;
		qcrate_sequence_load.committed = false;
		response->status = QCRATE_STATUS_SEQUENCE_VERIFY;
		response->payload_words = 2U;
		response->payload[0] = qcrate_sequence_load.expected_events;
		response->payload[1] = readback;
		return;
	}

	qcrate_sequence_load.active = false;
	qcrate_sequence_load.committed = true;
	response->payload_words = 2U;
	response->payload[0] = qcrate_sequence_load.expected_events;
	response->payload[1] = actual_crc32;
}

static void qcrate_handle_seq_get_status(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	qcrate_sequence_status_payload(response);
}

static void qcrate_sequence_wait_response(
	struct qcrate_rpmsg_message *response, int wait_status)
{
	if (wait_status == -2)
		response->status = QCRATE_STATUS_HARDWARE_FAULT;
	else if (wait_status != 0)
		response->status = QCRATE_STATUS_TIMEOUT;
	qcrate_sequence_status_payload(response);
}

static void qcrate_handle_seq_arm(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t control;
	uint32_t status;

	if (request->payload_words != 1U || request->payload[0] > 1U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if (!qcrate_sequence_load.committed ||
	    (status & (QCRATE_SEQ_HW_IDLE | QCRATE_SEQ_HW_COMMAND_BUSY |
		       QCRATE_SEQ_HW_MEMORY_LOCKED | QCRATE_SEQ_HW_FAULTED)) !=
	    QCRATE_SEQ_HW_IDLE) {
		qcrate_sequence_status_error(response, QCRATE_STATUS_BAD_STATE);
		return;
	}

	control = QCRATE_SEQ_CONTROL_ARM;
	if (request->payload[0] != 0U)
		control |= QCRATE_SEQ_CONTROL_EXTERNAL;
	qcrate_reg_write(QCRATE_SEQ_CONTROL, control);
	qcrate_sequence_wait_response(response,
		qcrate_sequence_wait(QCRATE_SEQ_WAIT_ARMED, 0U));
}

static void qcrate_handle_seq_start(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t completed_before;
	uint32_t status;

	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if (!qcrate_sequence_load.committed ||
	    (status & (QCRATE_SEQ_HW_ARMED | QCRATE_SEQ_HW_COMMAND_BUSY |
		       QCRATE_SEQ_HW_FAULTED)) != QCRATE_SEQ_HW_ARMED) {
		qcrate_sequence_status_error(response, QCRATE_STATUS_BAD_STATE);
		return;
	}

	completed_before = qcrate_reg_read(QCRATE_SEQ_COMPLETED_SHOTS);
	qcrate_reg_write(QCRATE_SEQ_CONTROL, QCRATE_SEQ_CONTROL_START);
	qcrate_sequence_wait_response(response,
		qcrate_sequence_wait(QCRATE_SEQ_WAIT_STARTED, completed_before));
}

static void qcrate_handle_seq_abort(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t status;

	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if ((status & (QCRATE_SEQ_HW_IDLE | QCRATE_SEQ_HW_FAULTED)) != 0U) {
		qcrate_sequence_status_error(response, QCRATE_STATUS_BAD_STATE);
		return;
	}

	qcrate_reg_write(QCRATE_SEQ_CONTROL, QCRATE_SEQ_CONTROL_ABORT);
	qcrate_sequence_wait_response(response,
		qcrate_sequence_wait(QCRATE_SEQ_WAIT_IDLE, 0U));
}

static void qcrate_handle_seq_reset(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	qcrate_sequence_load.active = false;
	qcrate_reg_write(QCRATE_SEQ_CONTROL, QCRATE_SEQ_CONTROL_RESET);
	qcrate_sequence_wait_response(response,
		qcrate_sequence_wait(QCRATE_SEQ_WAIT_IDLE, 0U));
}

static void qcrate_handle_config_stage(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != QCRATE_CONFIG_STAGE_WORDS) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	memcpy(qcrate_config.words, request->payload,
	       sizeof(qcrate_config.words));
	qcrate_config.staged = true;
	qcrate_config.validated = false;
	qcrate_config.reason = QCRATE_CONFIG_REASON_NONE;
	qcrate_config_status_payload(response);
}

static void qcrate_handle_config_validate(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t reason;

	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	if (!qcrate_config.staged) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_NOT_STAGED);
		return;
	}

	reason = qcrate_validate_runtime_config(qcrate_config.words);
	qcrate_config.validated = reason == QCRATE_CONFIG_REASON_NONE;
	qcrate_config.reason = reason;
	if (!qcrate_config.validated)
		response->status = QCRATE_STATUS_BAD_CONFIG;
	qcrate_config_status_payload(response);
}

static void qcrate_config_restore_stream(const uint32_t *previous)
{
	qcrate_reg_write(QCRATE_STREAM_FRAME_LENGTH, previous[0]);
	qcrate_reg_write(QCRATE_STREAM_FRAME_COUNT, previous[1]);
	qcrate_reg_write(QCRATE_STREAM_MODE, previous[2]);
}

static void qcrate_handle_config_commit(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	uint32_t previous_stream[3];
	uint32_t sequence_status;
	uint32_t stream_status;
	uint32_t generation_before;
	uint32_t index;
	int wait_status;

	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	if (!qcrate_config.staged || !qcrate_config.validated) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_NOT_STAGED);
		return;
	}
	qcrate_config.reason = qcrate_validate_runtime_config(qcrate_config.words);
	if (qcrate_config.reason != QCRATE_CONFIG_REASON_NONE) {
		qcrate_config.validated = false;
		qcrate_config_error(response, QCRATE_STATUS_BAD_CONFIG,
			qcrate_config.reason);
		return;
	}

	sequence_status = qcrate_reg_read(QCRATE_SEQ_STATUS);
	if (qcrate_sequence_load.active ||
	    (sequence_status & (QCRATE_SEQ_HW_IDLE |
		QCRATE_SEQ_HW_COMMAND_BUSY | QCRATE_SEQ_HW_MEMORY_LOCKED |
		QCRATE_SEQ_HW_FAULTED)) != QCRATE_SEQ_HW_IDLE) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_SEQUENCE_STATE);
		return;
	}
	stream_status = qcrate_reg_read(QCRATE_STREAM_STATUS);
	if ((stream_status & (QCRATE_STREAM_HW_BUSY | QCRATE_STREAM_HW_ARMED |
		QCRATE_STREAM_HW_COMMAND_BUSY)) != 0U) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_STREAM_STATE);
		return;
	}
	if ((qcrate_reg_read(QCRATE_DSP_STATUS) & QCRATE_DSP_STATUS_BUSY) != 0U) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_PL_REJECTED);
		return;
	}

	previous_stream[0] = qcrate_reg_read(QCRATE_STREAM_FRAME_LENGTH);
	previous_stream[1] = qcrate_reg_read(QCRATE_STREAM_FRAME_COUNT);
	previous_stream[2] = qcrate_reg_read(QCRATE_STREAM_MODE);
	qcrate_reg_write(QCRATE_STREAM_FRAME_LENGTH,
		qcrate_config.words[QCRATE_CONFIG_FRAME_LENGTH_WORD]);
	qcrate_reg_write(QCRATE_STREAM_FRAME_COUNT,
		qcrate_config.words[QCRATE_CONFIG_FRAME_COUNT_WORD]);
	qcrate_reg_write(QCRATE_STREAM_MODE, QCRATE_STREAM_MODE_DSP);
	if (qcrate_reg_read(QCRATE_STREAM_FRAME_LENGTH) !=
		qcrate_config.words[QCRATE_CONFIG_FRAME_LENGTH_WORD] ||
	    qcrate_reg_read(QCRATE_STREAM_FRAME_COUNT) !=
		qcrate_config.words[QCRATE_CONFIG_FRAME_COUNT_WORD] ||
	    qcrate_reg_read(QCRATE_STREAM_MODE) != QCRATE_STREAM_MODE_DSP) {
		qcrate_config_restore_stream(previous_stream);
		qcrate_config_error(response, QCRATE_STATUS_CONFIG_VERIFY,
			QCRATE_CONFIG_REASON_SHADOW_VERIFY);
		return;
	}

	/* Clear a prior rejection and restore a known shadow baseline first. */
	qcrate_reg_write(QCRATE_DSP_COMMAND, QCRATE_DSP_COMMAND_DISCARD);
	for (index = 0U; index < 9U; index++)
		qcrate_reg_write(QCRATE_DSP_SHADOW_BASE + index * 4U,
			qcrate_config.words[index]);
	for (index = 0U; index < 9U; index++) {
		if (qcrate_reg_read(QCRATE_DSP_SHADOW_BASE + index * 4U) !=
		    qcrate_config.words[index]) {
			qcrate_reg_write(QCRATE_DSP_COMMAND,
				QCRATE_DSP_COMMAND_DISCARD);
			qcrate_config_restore_stream(previous_stream);
			qcrate_config_error(response, QCRATE_STATUS_CONFIG_VERIFY,
				QCRATE_CONFIG_REASON_SHADOW_VERIFY);
			return;
		}
	}

	generation_before = qcrate_reg_read(QCRATE_DSP_ACTIVE_GENERATION);
	qcrate_reg_write(QCRATE_DSP_COMMAND, QCRATE_DSP_COMMAND_COMMIT);
	wait_status = qcrate_config_wait_for_commit(generation_before);
	if (wait_status != 0) {
		if (wait_status == -2) {
			qcrate_reg_write(QCRATE_DSP_COMMAND,
				QCRATE_DSP_COMMAND_DISCARD);
			qcrate_config_restore_stream(previous_stream);
		}
		qcrate_config_error(response,
			wait_status == -2 ? QCRATE_STATUS_CONFIG_REJECTED :
			QCRATE_STATUS_TIMEOUT,
			wait_status == -2 ? QCRATE_CONFIG_REASON_PL_REJECTED :
			QCRATE_CONFIG_REASON_COMMIT_TIMEOUT);
		return;
	}

	for (index = 0U; index < 9U; index++) {
		if (qcrate_reg_read(QCRATE_DSP_ACTIVE_BASE + index * 4U) !=
		    qcrate_config.words[index]) {
			qcrate_config_error(response, QCRATE_STATUS_CONFIG_VERIFY,
				QCRATE_CONFIG_REASON_ACTIVE_VERIFY);
			return;
		}
	}
	if ((qcrate_reg_read(QCRATE_DSP_STATUS) &
	     (QCRATE_DSP_STATUS_DIRTY | QCRATE_DSP_STATUS_BUSY |
	      QCRATE_DSP_STATUS_VALID | QCRATE_DSP_STATUS_REJECTED)) !=
	    QCRATE_DSP_STATUS_VALID) {
		qcrate_config_error(response, QCRATE_STATUS_CONFIG_VERIFY,
			QCRATE_CONFIG_REASON_ACTIVE_VERIFY);
		return;
	}

	qcrate_config.reason = QCRATE_CONFIG_REASON_NONE;
	qcrate_config_status_payload(response);
}

static void qcrate_handle_config_get_status(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	qcrate_config_status_payload(response);
}

static void qcrate_handle_config_recover(
	const struct qcrate_rpmsg_message *request,
	struct qcrate_rpmsg_message *response)
{
	if (request->payload_words != 0U) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}
	if ((qcrate_reg_read(QCRATE_DSP_STATUS) & QCRATE_DSP_STATUS_BUSY) != 0U) {
		qcrate_config_error(response, QCRATE_STATUS_BAD_STATE,
			QCRATE_CONFIG_REASON_COMMIT_TIMEOUT);
		return;
	}
	qcrate_reg_write(QCRATE_DSP_COMMAND, QCRATE_DSP_COMMAND_DISCARD);
	memset(&qcrate_config, 0, sizeof(qcrate_config));
	qcrate_config_status_payload(response);
}

static void qcrate_handle_request(const struct qcrate_rpmsg_message *request,
				  struct qcrate_rpmsg_message *response)
{
	qcrate_response_init(response, request);

	if (request->magic != QCRATE_RPMSG_MAGIC) {
		response->status = QCRATE_STATUS_BAD_MAGIC;
		return;
	}
	if (request->abi_version != QCRATE_RPMSG_ABI_VERSION) {
		response->status = QCRATE_STATUS_BAD_ABI;
		return;
	}
	if (request->payload_words > QCRATE_RPMSG_PAYLOAD_WORDS) {
		response->status = QCRATE_STATUS_BAD_LENGTH;
		return;
	}

	switch (request->command) {
	case QCRATE_CMD_PING:
		qcrate_handle_ping(request, response);
		break;
	case QCRATE_CMD_GET_INFO:
		qcrate_handle_get_info(request, response);
		break;
	case QCRATE_CMD_SCRATCH_TEST:
		qcrate_handle_scratch_test(request, response);
		break;
	case QCRATE_CMD_GET_R5_STATS:
		qcrate_handle_get_r5_stats(request, response);
		break;
	case QCRATE_CMD_SEQ_LOAD_BEGIN:
		qcrate_handle_seq_load_begin(request, response);
		break;
	case QCRATE_CMD_SEQ_LOAD_EVENT:
		qcrate_handle_seq_load_event(request, response);
		break;
	case QCRATE_CMD_SEQ_LOAD_COMMIT:
		qcrate_handle_seq_load_commit(request, response);
		break;
	case QCRATE_CMD_SEQ_GET_STATUS:
		qcrate_handle_seq_get_status(request, response);
		break;
	case QCRATE_CMD_SEQ_ARM:
		qcrate_handle_seq_arm(request, response);
		break;
	case QCRATE_CMD_SEQ_START:
		qcrate_handle_seq_start(request, response);
		break;
	case QCRATE_CMD_SEQ_ABORT:
		qcrate_handle_seq_abort(request, response);
		break;
	case QCRATE_CMD_SEQ_RESET:
		qcrate_handle_seq_reset(request, response);
		break;
	case QCRATE_CMD_CONFIG_STAGE:
		qcrate_handle_config_stage(request, response);
		break;
	case QCRATE_CMD_CONFIG_VALIDATE:
		qcrate_handle_config_validate(request, response);
		break;
	case QCRATE_CMD_CONFIG_COMMIT:
		qcrate_handle_config_commit(request, response);
		break;
	case QCRATE_CMD_CONFIG_GET_STATUS:
		qcrate_handle_config_get_status(request, response);
		break;
	case QCRATE_CMD_CONFIG_RECOVER:
		qcrate_handle_config_recover(request, response);
		break;
	default:
		response->status = QCRATE_STATUS_BAD_COMMAND;
		break;
	}
}

static int qcrate_endpoint_cb(struct rpmsg_endpoint *endpoint, void *data,
			      size_t length, uint32_t source, void *private_data)
{
	struct qcrate_rpmsg_message request;
	struct qcrate_rpmsg_message response;

	(void)source;
	(void)private_data;
	memset(&request, 0, sizeof(request));

	if (data == NULL || length != sizeof(request)) {
		if (data != NULL)
			memcpy(&request, data,
			       length < sizeof(request) ? length : sizeof(request));
		qcrate_response_init(&response, &request);
		response.status = QCRATE_STATUS_BAD_LENGTH;
	} else {
		memcpy(&request, data, sizeof(request));
		qcrate_handle_request(&request, &response);
	}

	if (response.status == QCRATE_STATUS_OK)
		qcrate_accepted_requests++;
	else
		qcrate_rejected_requests++;

	if (response.status == QCRATE_STATUS_OK &&
	    response.command == QCRATE_CMD_GET_R5_STATS) {
		response.payload[2] = qcrate_accepted_requests;
		response.payload[3] = qcrate_rejected_requests;
	}

	if (rpmsg_send(endpoint, &response, sizeof(response)) < 0)
		ML_ERR("Q-Crate response send failed\r\n");

	return RPMSG_SUCCESS;
}

static void qcrate_service_unbind(struct rpmsg_endpoint *endpoint)
{
	(void)endpoint;
	ML_INFO("Q-Crate control endpoint unbound\r\n");
}

int app(struct rpmsg_device *rpmsg_device, void *platform)
{
	int status;

	status = rpmsg_create_ept(&qcrate_endpoint, rpmsg_device,
				  QCRATE_RPMSG_SERVICE_NAME,
				  RPMSG_ADDR_ANY, RPMSG_ADDR_ANY,
				  qcrate_endpoint_cb,
				  qcrate_service_unbind);
	if (status != 0) {
		ML_ERR("Failed to create Q-Crate control endpoint\r\n");
		return status;
	}

	ML_INFO("Q-Crate control endpoint ready\r\n");
	return platform_poll_on_vdev_reset(rpmsg_device, platform);
}
