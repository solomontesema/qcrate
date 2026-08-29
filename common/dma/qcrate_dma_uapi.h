/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_DMA_UAPI_H
#define QCRATE_DMA_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define QCRATE_DMA_ABI_VERSION 1U
#define QCRATE_DMA_IOC_MAGIC   'Q'

#define QCRATE_DMA_CAP_SG_CHAIN      (1U << 0)
#define QCRATE_DMA_CAP_DSP_MODE      (1U << 1)
#define QCRATE_DMA_CAP_TRIGGERED     (1U << 2)
#define QCRATE_DMA_MAX_CHAIN_FRAMES  255U

#define QCRATE_DMA_TRIGGER_SEEN                  (1U << 0)
#define QCRATE_DMA_FIRST_SAMPLE_TIME_VALID       (1U << 1)

struct qcrate_dma_info {
	__u32 abi_version;
	__u32 buffer_bytes;
	__u32 max_transfer_bytes;
	__u32 stream_word_bytes;
	__u32 reserved[4];
};

struct qcrate_dma_capture {
	__u32 frame_length_words;
	__u32 timeout_ms;
	__u32 transferred_bytes;
	__u32 residue_bytes;
	__u32 dma_result;
	__u32 stream_status;
	__u32 completed_frames;
	__u32 current_frame_id;
	__u32 current_sample_index;
	__u32 stall_cycles;
	__u32 stream_mode;
	__u32 reserved[5];
};

struct qcrate_dma_caps {
	__u32 abi_version;
	__u32 feature_flags;
	__u32 max_chain_frames;
	__u32 reserved[5];
};

struct qcrate_dma_capture_frames {
	__u32 frame_length_words;
	__u32 frame_count;
	__u32 timeout_ms;
	__u32 transferred_bytes;
	__u32 last_residue_bytes;
	__u32 dma_result;
	__u32 stream_status;
	__u32 completed_frames;
	__u32 current_frame_id;
	__u32 current_sample_index;
	__u32 stall_cycles;
	__u32 stream_mode;
	__u32 reserved[4];
};

/* Configure and arm S2MM without starting the stream source. */
struct qcrate_dma_triggered_arm {
	__u32 frame_length_words;
	__u32 frame_count;
	__u32 stream_mode;
	__u32 reserved[5];
};

/* Wait for an armed trigger capture and return its timing evidence. */
struct qcrate_dma_triggered_result {
	__u32 timeout_ms;
	__u32 transferred_bytes;
	__u32 last_residue_bytes;
	__u32 dma_result;
	__u32 stream_status;
	__u32 completed_frames;
	__u32 current_frame_id;
	__u32 current_sample_index;
	__u32 stall_cycles;
	__u32 trigger_shot_id;
	__u32 trigger_count;
	__u32 missed_trigger_count;
	__u32 timestamp_flags;
	__u32 reserved0;
	__u64 trigger_time;
	__u64 first_sample_time;
	__u32 reserved[6];
};

#define QCRATE_DMA_IOC_GET_INFO \
	_IOR(QCRATE_DMA_IOC_MAGIC, 0x00, struct qcrate_dma_info)
#define QCRATE_DMA_IOC_CAPTURE \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x01, struct qcrate_dma_capture)
#define QCRATE_DMA_IOC_GET_CAPS \
	_IOR(QCRATE_DMA_IOC_MAGIC, 0x02, struct qcrate_dma_caps)
#define QCRATE_DMA_IOC_CAPTURE_FRAMES \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x03, struct qcrate_dma_capture_frames)
#define QCRATE_DMA_IOC_ARM_TRIGGERED \
	_IOW(QCRATE_DMA_IOC_MAGIC, 0x04, struct qcrate_dma_triggered_arm)
#define QCRATE_DMA_IOC_WAIT_TRIGGERED \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x05, struct qcrate_dma_triggered_result)
#define QCRATE_DMA_IOC_CANCEL_TRIGGERED \
	_IO(QCRATE_DMA_IOC_MAGIC, 0x06)

#ifdef __cplusplus
#define QCRATE_DMA_STATIC_ASSERT static_assert
#else
#define QCRATE_DMA_STATIC_ASSERT _Static_assert
#endif
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_info) == 32,
			 "qcrate_dma_info ABI size changed");
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_capture) == 64,
			 "qcrate_dma_capture ABI size changed");
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_caps) == 32,
			 "qcrate_dma_caps ABI size changed");
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_capture_frames) == 64,
			 "qcrate_dma_capture_frames ABI size changed");
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_triggered_arm) == 32,
			 "qcrate_dma_triggered_arm ABI size changed");
QCRATE_DMA_STATIC_ASSERT(sizeof(struct qcrate_dma_triggered_result) == 96,
			 "qcrate_dma_triggered_result ABI size changed");
#undef QCRATE_DMA_STATIC_ASSERT

#endif
