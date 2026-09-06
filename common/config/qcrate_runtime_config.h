#ifndef QCRATE_RUNTIME_CONFIG_H
#define QCRATE_RUNTIME_CONFIG_H

#include <stdint.h>

#include "qcrate_protocol.h"

/* Validate the complete eleven-word CONFIG_STAGE payload. */
uint32_t qcrate_validate_runtime_config(const uint32_t *words);

#endif /* QCRATE_RUNTIME_CONFIG_H */
