/* SPDX-License-Identifier: MIT */
/*
 * Q-Crate R5-0 FreeRTOS control service.
 *
 * AMD's OpenAMP echo-test template supplies the generated ZynqMP machine
 * layer, resource table, linker script, and platform_info implementation.
 * The reproducible Vitis flow replaces only the template endpoint source and
 * header with the tracked Q-Crate implementation.
 */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "xil_io.h"

#include <metal/log.h>
#include <openamp/open_amp.h>

#include "platform_info.h"
#include "qcrate_protocol.h"
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

#define QCRATE_INFO_WORDS           6U
#define QCRATE_SCRATCH_RESULT_WORDS 5U

static struct rpmsg_endpoint qcrate_endpoint;

static uint32_t qcrate_reg_read(uint32_t offset)
{
	return Xil_In32((UINTPTR)(QCRATE_APB_BASE + offset));
}

static void qcrate_reg_write(uint32_t offset, uint32_t value)
{
	Xil_Out32((UINTPTR)(QCRATE_APB_BASE + offset), value);
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
