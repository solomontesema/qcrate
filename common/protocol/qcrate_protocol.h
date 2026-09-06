#ifndef QCRATE_PROTOCOL_H
#define QCRATE_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

/* Both ZynqMP A53 Linux and Cortex-R5 FreeRTOS endpoints are little-endian. */
#define QCRATE_RPMSG_SERVICE_NAME "qcrate-control"
#define QCRATE_RPMSG_MAGIC        UINT32_C(0x51524350)
#define QCRATE_RPMSG_ABI_VERSION  UINT16_C(3)
#define QCRATE_RPMSG_PAYLOAD_WORDS 11U
#define QCRATE_RPMSG_MESSAGE_SIZE 64U

#define QCRATE_CMD_PING           UINT16_C(1)
#define QCRATE_CMD_GET_INFO       UINT16_C(2)
#define QCRATE_CMD_SCRATCH_TEST   UINT16_C(3)
#define QCRATE_CMD_GET_R5_STATS   UINT16_C(4)
#define QCRATE_CMD_SEQ_LOAD_BEGIN UINT16_C(5)
#define QCRATE_CMD_SEQ_LOAD_EVENT UINT16_C(6)
#define QCRATE_CMD_SEQ_LOAD_COMMIT UINT16_C(7)
#define QCRATE_CMD_SEQ_GET_STATUS UINT16_C(8)
#define QCRATE_CMD_SEQ_ARM        UINT16_C(9)
#define QCRATE_CMD_SEQ_START      UINT16_C(10)
#define QCRATE_CMD_SEQ_ABORT      UINT16_C(11)
#define QCRATE_CMD_SEQ_RESET      UINT16_C(12)
#define QCRATE_CMD_CONFIG_STAGE   UINT16_C(13)
#define QCRATE_CMD_CONFIG_VALIDATE UINT16_C(14)
#define QCRATE_CMD_CONFIG_COMMIT  UINT16_C(15)
#define QCRATE_CMD_CONFIG_GET_STATUS UINT16_C(16)
#define QCRATE_CMD_CONFIG_RECOVER UINT16_C(17)

#define QCRATE_STATUS_OK              INT32_C(0)
#define QCRATE_STATUS_BAD_MAGIC       INT32_C(-1)
#define QCRATE_STATUS_BAD_ABI         INT32_C(-2)
#define QCRATE_STATUS_BAD_LENGTH      INT32_C(-3)
#define QCRATE_STATUS_BAD_COMMAND     INT32_C(-4)
#define QCRATE_STATUS_BAD_DEVICE_ID   INT32_C(-5)
#define QCRATE_STATUS_SCRATCH_VERIFY  INT32_C(-6)
#define QCRATE_STATUS_BAD_STATE       INT32_C(-7)
#define QCRATE_STATUS_BAD_INDEX       INT32_C(-8)
#define QCRATE_STATUS_BAD_SEQUENCE    INT32_C(-9)
#define QCRATE_STATUS_SEQUENCE_CRC    INT32_C(-10)
#define QCRATE_STATUS_SEQUENCE_VERIFY INT32_C(-11)
#define QCRATE_STATUS_HARDWARE_FAULT  INT32_C(-12)
#define QCRATE_STATUS_TIMEOUT         INT32_C(-13)
#define QCRATE_STATUS_BAD_CONFIG      INT32_C(-14)
#define QCRATE_STATUS_CONFIG_VERIFY   INT32_C(-15)
#define QCRATE_STATUS_CONFIG_REJECTED INT32_C(-16)

/* Sequence status response payload and R5 ownership flags. */
#define QCRATE_SEQ_STATUS_WORDS       11U
#define QCRATE_SEQ_STATUS_LOAD_ACTIVE UINT32_C(0x40000000)
#define QCRATE_SEQ_STATUS_COMMITTED   UINT32_C(0x80000000)

/* Fixed request payload lengths. */
#define QCRATE_SEQ_LOAD_BEGIN_WORDS   2U
#define QCRATE_SEQ_LOAD_EVENT_WORDS   5U

/* Runtime configuration stage request: one complete resolved experiment. */
#define QCRATE_CONFIG_STAGE_WORDS     11U
#define QCRATE_CONFIG_STATUS_WORDS    11U
#define QCRATE_CONFIG_ID_LOW_WORD     0U
#define QCRATE_CONFIG_ID_HIGH_WORD    1U
#define QCRATE_CONFIG_SIGNAL_INCREMENT_WORD 2U
#define QCRATE_CONFIG_SIGNAL_INITIAL_WORD 3U
#define QCRATE_CONFIG_SIGNAL_AMPLITUDE_WORD 4U
#define QCRATE_CONFIG_NOISE_AMPLITUDE_WORD 5U
#define QCRATE_CONFIG_NOISE_SEED_WORD 6U
#define QCRATE_CONFIG_LO_INCREMENT_WORD 7U
#define QCRATE_CONFIG_LO_INITIAL_WORD 8U
#define QCRATE_CONFIG_FRAME_LENGTH_WORD 9U
#define QCRATE_CONFIG_FRAME_COUNT_WORD 10U

/* R5 configuration ownership flags returned in status payload word zero. */
#define QCRATE_CONFIG_FLAG_STAGED     UINT32_C(0x00000001)
#define QCRATE_CONFIG_FLAG_VALIDATED  UINT32_C(0x00000002)

/* Stable semantic/recovery reasons returned in status payload word ten. */
#define QCRATE_CONFIG_REASON_NONE             UINT32_C(0)
#define QCRATE_CONFIG_REASON_NOT_STAGED       UINT32_C(1)
#define QCRATE_CONFIG_REASON_ZERO_ID          UINT32_C(2)
#define QCRATE_CONFIG_REASON_SIGNAL_AMPLITUDE UINT32_C(3)
#define QCRATE_CONFIG_REASON_NOISE_AMPLITUDE  UINT32_C(4)
#define QCRATE_CONFIG_REASON_AMPLITUDE_SUM    UINT32_C(5)
#define QCRATE_CONFIG_REASON_ZERO_NOISE_SEED  UINT32_C(6)
#define QCRATE_CONFIG_REASON_FRAME_LENGTH     UINT32_C(7)
#define QCRATE_CONFIG_REASON_FRAME_COUNT      UINT32_C(8)
#define QCRATE_CONFIG_REASON_TOTAL_WORDS      UINT32_C(9)
#define QCRATE_CONFIG_REASON_SEQUENCE_STATE   UINT32_C(10)
#define QCRATE_CONFIG_REASON_STREAM_STATE     UINT32_C(11)
#define QCRATE_CONFIG_REASON_SHADOW_VERIFY    UINT32_C(12)
#define QCRATE_CONFIG_REASON_PL_REJECTED      UINT32_C(13)
#define QCRATE_CONFIG_REASON_ACTIVE_VERIFY    UINT32_C(14)
#define QCRATE_CONFIG_REASON_COMMIT_TIMEOUT   UINT32_C(15)

/*
 * Requests and responses use the same fixed-size wire object. A response
 * repeats command and transaction_id, places its result in status, and reports
 * the number of valid 32-bit payload words. Reserved payload words are zero.
 */
struct qcrate_rpmsg_message {
	uint32_t magic;
	uint16_t abi_version;
	uint16_t command;
	uint32_t transaction_id;
	uint32_t payload_words;
	int32_t status;
	uint32_t payload[QCRATE_RPMSG_PAYLOAD_WORDS];
};

_Static_assert(sizeof(struct qcrate_rpmsg_message) ==
	       QCRATE_RPMSG_MESSAGE_SIZE,
	       "Q-Crate RPMsg ABI must remain 64 bytes");
_Static_assert(offsetof(struct qcrate_rpmsg_message, payload) == 20U,
	       "Q-Crate RPMsg header layout changed");

#endif /* QCRATE_PROTOCOL_H */
