#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "qcrate_runtime_config.h"

static void valid_payload(uint32_t *words)
{
	memset(words, 0, QCRATE_CONFIG_STAGE_WORDS * sizeof(words[0]));
	words[QCRATE_CONFIG_ID_LOW_WORD] = UINT32_C(0x8B27B09F);
	words[QCRATE_CONFIG_ID_HIGH_WORD] = UINT32_C(0x5DB4FB57);
	words[QCRATE_CONFIG_SIGNAL_INCREMENT_WORD] = UINT32_C(0x26666666);
	words[QCRATE_CONFIG_SIGNAL_AMPLITUDE_WORD] = UINT32_C(0x6000);
	words[QCRATE_CONFIG_NOISE_AMPLITUDE_WORD] = UINT32_C(0x0148);
	words[QCRATE_CONFIG_NOISE_SEED_WORD] = UINT32_C(0xACE1);
	words[QCRATE_CONFIG_LO_INCREMENT_WORD] = UINT32_C(0x251EB852);
	words[QCRATE_CONFIG_FRAME_LENGTH_WORD] = UINT32_C(1024);
	words[QCRATE_CONFIG_FRAME_COUNT_WORD] = UINT32_C(4);
}

int main(void)
{
	uint32_t words[QCRATE_CONFIG_STAGE_WORDS];

	valid_payload(words);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_NONE);
	words[QCRATE_CONFIG_ID_LOW_WORD] = 0U;
	words[QCRATE_CONFIG_ID_HIGH_WORD] = 0U;
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_ZERO_ID);

	valid_payload(words);
	words[QCRATE_CONFIG_SIGNAL_AMPLITUDE_WORD] = UINT32_C(0x8000);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_SIGNAL_AMPLITUDE);

	valid_payload(words);
	words[QCRATE_CONFIG_NOISE_AMPLITUDE_WORD] = UINT32_C(0x3000);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_AMPLITUDE_SUM);

	valid_payload(words);
	words[QCRATE_CONFIG_NOISE_SEED_WORD] = 0U;
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_ZERO_NOISE_SEED);

	valid_payload(words);
	words[QCRATE_CONFIG_FRAME_LENGTH_WORD] = UINT32_C(262145);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_FRAME_LENGTH);

	valid_payload(words);
	words[QCRATE_CONFIG_FRAME_COUNT_WORD] = UINT32_C(256);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_FRAME_COUNT);

	valid_payload(words);
	words[QCRATE_CONFIG_FRAME_LENGTH_WORD] = UINT32_C(10000);
	words[QCRATE_CONFIG_FRAME_COUNT_WORD] = UINT32_C(101);
	assert(qcrate_validate_runtime_config(words) ==
	       QCRATE_CONFIG_REASON_TOTAL_WORDS);

	puts("PASS qcrate_runtime_config semantic validation");
	return 0;
}
