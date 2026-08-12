/* SPDX-License-Identifier: MIT */
#ifndef QCRATE_DMA_UAPI_H
#define QCRATE_DMA_UAPI_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define QCRATE_DMA_ABI_VERSION 1U
#define QCRATE_DMA_IOC_MAGIC   'Q'

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
	__u32 reserved[6];
};

#define QCRATE_DMA_IOC_GET_INFO \
	_IOR(QCRATE_DMA_IOC_MAGIC, 0x00, struct qcrate_dma_info)
#define QCRATE_DMA_IOC_CAPTURE \
	_IOWR(QCRATE_DMA_IOC_MAGIC, 0x01, struct qcrate_dma_capture)

#endif
