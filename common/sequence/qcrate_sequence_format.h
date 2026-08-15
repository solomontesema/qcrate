/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_SEQUENCE_FORMAT_H
#define QCRATE_SEQUENCE_FORMAT_H

#include <stddef.h>
#include <stdint.h>

/* The file and RPMsg payload representation is little-endian. */
#define QCRATE_SEQUENCE_MAGIC          UINT32_C(0x51455351) /* "QSEQ" */
#define QCRATE_SEQUENCE_FORMAT_VERSION UINT16_C(1)
#define QCRATE_SEQUENCE_HEADER_SIZE    UINT16_C(32)
#define QCRATE_SEQUENCE_EVENT_SIZE     UINT32_C(16)
#define QCRATE_SEQUENCE_TICK_HZ        UINT32_C(200000000)
#define QCRATE_SEQUENCE_MIN_EVENTS     UINT32_C(2)
#define QCRATE_SEQUENCE_MAX_EVENTS     UINT32_C(128)
#define QCRATE_SEQUENCE_OUTPUT_MASK    UINT32_C(0x00000003)

struct qcrate_sequence_header {
	uint32_t magic;
	uint16_t format_version;
	uint16_t header_size;
	uint32_t event_count;
	uint32_t event_size;
	uint32_t tick_hz;
	uint32_t payload_crc32;
	uint64_t reserved;
};

struct qcrate_sequence_event {
	uint64_t timestamp;
	uint32_t output_state;
	uint32_t flags;
};

_Static_assert(sizeof(struct qcrate_sequence_header) ==
		       QCRATE_SEQUENCE_HEADER_SIZE,
		       "Q-Crate sequence header layout changed");
_Static_assert(offsetof(struct qcrate_sequence_header, reserved) == 24U,
		       "Q-Crate sequence header offsets changed");
_Static_assert(sizeof(struct qcrate_sequence_event) ==
		       QCRATE_SEQUENCE_EVENT_SIZE,
		       "Q-Crate sequence event layout changed");

#endif /* QCRATE_SEQUENCE_FORMAT_H */
