/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_DMA_UAPI_H
#define QCRATE_DMA_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define QCRATE_DMA_ABI_VERSION 1U
#define QCRATE_DMA_IOC_MAGIC   'Q'

#define QCRATE_DMA_CAP_SG_CHAIN      (1U << 0)
#define QCRATE_DMA_CAP_DSP_MODE      (1U << 1)
#define QCRATE_DMA_MAX_CHAIN_FRAMES  255U

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

#define QCRATE_DMA_IOC_GET_INFO \
	_IOR(QCRATE_DMA_IOC_MAGIC, 0x00, struct qcrate_dma_info)
#define QCRATE_DMA_IOC_CAPTURE \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x01, struct qcrate_dma_capture)
#define QCRATE_DMA_IOC_GET_CAPS \
	_IOR(QCRATE_DMA_IOC_MAGIC, 0x02, struct qcrate_dma_caps)
#define QCRATE_DMA_IOC_CAPTURE_FRAMES \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x03, struct qcrate_dma_capture_frames)

#endif
