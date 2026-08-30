// SPDX-License-Identifier: MIT
/*
 * Q-Crate receive-only DMAEngine client
 *
 * This driver owns the Q-Crate stream-control registers and one AXI DMA S2MM
 * channel. It allocates a DMA-coherent capture buffer and exposes a small
 * ioctl/mmap ABI through /dev/qcrate-dma. Userspace requests one frame or a
 * finite scatter-gather frame chain and reads the completed buffer; it never
 * programs AXI DMA registers, handles physical addresses, or performs cache
 * maintenance.
 *
 * The critical transaction order is:
 *   reset stream -> configure frame -> arm S2MM -> start stream -> wait
 * Triggered capture splits the last two steps so userspace can start the R5
 * sequence only after ARM_TRIGGERED confirms both S2MM and the PL are armed.
 * Starting the stream before S2MM is armed is intentionally impossible through
 * this interface. Timeout and error paths reset the source and synchronously
 * terminate DMA so that the next capture begins from a defined state.
 */

#include <linux/atomic.h>
#include <linux/clk.h>
#include <linux/completion.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/dmaengine.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/kthread.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/overflow.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "qcrate_dma_uapi.h"

#define QCRATE_DEVICE_ID_VALUE          0x51435254U
#define QCRATE_SYS_DEVICE_ID            0x0000U

#define QCRATE_STREAM_CONTROL           0x1000U
#define QCRATE_STREAM_FRAME_LENGTH      0x1004U
#define QCRATE_STREAM_FRAME_COUNT       0x1008U
#define QCRATE_STREAM_MODE              0x100cU
#define QCRATE_STREAM_STATUS            0x1010U
#define QCRATE_STREAM_COMPLETED_FRAMES  0x1014U
#define QCRATE_STREAM_CURRENT_FRAME_ID  0x1018U
#define QCRATE_STREAM_CURRENT_SAMPLE    0x101cU
#define QCRATE_STREAM_STALL_CYCLES      0x1020U
#define QCRATE_STREAM_IRQ_ENABLE        0x1028U
#define QCRATE_STREAM_IRQ_CLEAR         0x102cU
#define QCRATE_STREAM_TRIGGER_SHOT_ID   0x1030U
#define QCRATE_STREAM_TRIGGER_COUNT     0x1034U
#define QCRATE_STREAM_MISSED_TRIGGERS   0x1038U
#define QCRATE_STREAM_TRIGGER_TIME_LO   0x103cU
#define QCRATE_STREAM_TRIGGER_TIME_HI   0x1040U
#define QCRATE_STREAM_FIRST_TIME_LO     0x1044U
#define QCRATE_STREAM_FIRST_TIME_HI     0x1048U

#define QCRATE_CONTROL_START            BIT(0)
#define QCRATE_CONTROL_ABORT            BIT(1)
#define QCRATE_CONTROL_SOFT_RESET       BIT(2)
#define QCRATE_CONTROL_ARM_TRIGGERED    BIT(3)
#define QCRATE_STATUS_BUSY              BIT(0)
#define QCRATE_STATUS_DONE              BIT(1)
#define QCRATE_STATUS_ERROR             BIT(2)
#define QCRATE_STATUS_ARMED             BIT(4)
#define QCRATE_STATUS_TRIGGER_SEEN      BIT(5)
#define QCRATE_STATUS_FIRST_TIME_VALID  BIT(6)
#define QCRATE_STATUS_COMMAND_BUSY      BIT(8)

#define QCRATE_STREAM_WORD_BYTES        4U
#define QCRATE_DEFAULT_BUFFER_BYTES     SZ_1M
#define QCRATE_MAX_BUFFER_BYTES         SZ_16M
#define QCRATE_DEFAULT_TIMEOUT_MS       1000U
#define QCRATE_MAX_TIMEOUT_MS           60000U
#define QCRATE_CANCEL_TIMEOUT_MS        1000U
#define QCRATE_STATUS_TIMEOUT_US        100000U

#define QCRATE_POOL_EVENT_KICK          BIT(0)
#define QCRATE_POOL_EVENT_DMA_DONE      BIT(1)
#define QCRATE_POOL_EVENT_STOP          BIT(2)

enum qcrate_pool_bank_state {
	QCRATE_BANK_FREE = 0,
	QCRATE_BANK_FILLING,
	QCRATE_BANK_READY,
	QCRATE_BANK_USER_OWNED,
};

struct qcrate_pool_bank {
	enum qcrate_pool_bank_state state;
	u32 generation;
	u64 sequence;
	struct qcrate_dma_pool_dequeue result;
};

/* All state owned by the single Q-Crate platform-device instance. */
struct qcrate_dma_dev {
	/* Platform control path and DMAEngine provider selected by device tree. */
	struct device *dev;
	void __iomem *regs;
	struct dma_chan *rx_chan;
	struct device *dma_dev;

	/* CPU virtual and DMA bus views of the same coherent capture buffer. */
	void *buffer;
	dma_addr_t buffer_dma;
	size_t buffer_bytes;
	u32 max_transfer_bytes;
	bool has_sg;

	/* One in-flight transaction and one userspace owner are allowed. */
	struct completion completion;
	struct dmaengine_result result;
	struct mutex capture_lock;
	bool triggered_active;
	bool triggered_cancelled;
	u32 triggered_frame_length_words;
	u32 triggered_frame_count;
	size_t triggered_frame_bytes;
	size_t triggered_total_bytes;

	/* Additive asynchronous finite-shot bank pool. */
	struct task_struct *pool_thread;
	wait_queue_head_t pool_waitq;
	wait_queue_head_t pool_ready_waitq;
	atomic_t pool_events;
	struct completion pool_stop_done;
	int pool_stop_result;
	bool pool_configured;
	bool pool_running;
	bool pool_stopping;
	bool pool_faulted;
	bool pool_starved;
	bool pool_dma_active;
	u64 pool_dma_completion_ns;
	u32 pool_frame_length_words;
	u32 pool_frame_count;
	u32 pool_stream_mode;
	u32 pool_arm_timeout_ms;
	u32 pool_bank_count;
	u32 pool_bank_bytes;
	u32 pool_frame_bytes;
	u32 pool_filling_bank;
	u32 pool_ready_banks;
	u32 pool_user_banks;
	u32 pool_free_banks;
	u32 pool_queue_high_water;
	u32 pool_last_discarded_ready;
	u32 pool_last_error;
	u32 pool_last_trigger_shot_id;
	u64 pool_run_id;
	u64 pool_next_run_id;
	u64 pool_next_sequence;
	u64 pool_produced_banks;
	u64 pool_dequeued_banks;
	u64 pool_consumed_banks;
	u64 pool_starvation_events;
	u64 pool_skipped_triggers;
	u64 pool_error_events;
	u64 pool_produced_bytes;
	struct qcrate_pool_bank pool_banks[QCRATE_DMA_MAX_POOL_BANKS];

	atomic_t opened;
	struct miscdevice miscdev;
};

/* Internal result shared by the stable single- and multi-frame UAPIs. */
struct qcrate_capture_result {
	u32 transferred_bytes;
	u32 last_residue_bytes;
	u32 dma_result;
	u32 stream_status;
	u32 completed_frames;
	u32 current_frame_id;
	u32 current_sample_index;
	u32 stall_cycles;
};

static bool qcrate_pool_owns_buffer_locked(struct qcrate_dma_dev *qdma);

/* DMAEngine invokes this callback from its tasklet after S2MM completion. */
static void qcrate_dma_complete(void *arg,
			       const struct dmaengine_result *result)
{
	struct qcrate_dma_dev *qdma = arg;

	if (result) {
		qdma->result = *result;
	} else {
		qdma->result.result = DMA_TRANS_NOERROR;
		qdma->result.residue = 0;
	}
	if (READ_ONCE(qdma->pool_dma_active)) {
		WRITE_ONCE(qdma->pool_dma_completion_ns, ktime_get_ns());
		atomic_or(QCRATE_POOL_EVENT_DMA_DONE, &qdma->pool_events);
		wake_up_interruptible(&qdma->pool_waitq);
		return;
	}
	complete(&qdma->completion);
}

/* Wait until both the stream engine and command CDC mailbox are idle. */
static int qcrate_wait_idle(struct qcrate_dma_dev *qdma, u32 *status)
{
	return readl_poll_timeout(qdma->regs + QCRATE_STREAM_STATUS, *status,
				  !(*status & (QCRATE_STATUS_BUSY |
					      QCRATE_STATUS_COMMAND_BUSY)),
				  10, QCRATE_STATUS_TIMEOUT_US);
}

/* ARM_TRIGGERED is complete only when the stream domain reports ownership. */
static int qcrate_wait_armed(struct qcrate_dma_dev *qdma, u32 *status)
{
	return readl_poll_timeout(qdma->regs + QCRATE_STREAM_STATUS, *status,
				  (*status & QCRATE_STATUS_ARMED) &&
				  !(*status & QCRATE_STATUS_COMMAND_BUSY),
				  10, QCRATE_STATUS_TIMEOUT_US);
}

/*
 * DMA completion and Q-Crate DONE cross through separate paths. Require the
 * APB-visible terminal status as well as idle to avoid a false CDC race.
 */
static int qcrate_wait_complete(struct qcrate_dma_dev *qdma, u32 *status)
{
	return readl_poll_timeout(qdma->regs + QCRATE_STREAM_STATUS, *status,
				  !(*status & (QCRATE_STATUS_BUSY |
					      QCRATE_STATUS_COMMAND_BUSY)) &&
				  (*status & (QCRATE_STATUS_DONE |
					      QCRATE_STATUS_ERROR)),
				  10, QCRATE_STATUS_TIMEOUT_US);
}

/* Reset the stream domain and wait for the APB-to-stream command handshake. */
static int qcrate_reset_stream(struct qcrate_dma_dev *qdma)
{
	u32 status;
	int ret;

	iowrite32(QCRATE_CONTROL_SOFT_RESET,
		  qdma->regs + QCRATE_STREAM_CONTROL);
	/* Allow the APB-to-stream command handshake to complete before polling. */
	usleep_range(10, 20);
	ret = qcrate_wait_idle(qdma, &status);
	if (ret)
		dev_warn(qdma->dev,
			 "stream did not become idle after soft reset (status 0x%08x)\n",
			 status);
	return ret;
}

/* Validate a finite capture and derive its byte geometry without side effects. */
static int qcrate_validate_capture(struct qcrate_dma_dev *qdma,
				   u32 frame_length_words, u32 frame_count,
				   u32 stream_mode, size_t *frame_bytes,
				   size_t *total_bytes)
{
	if (!frame_length_words || !frame_count)
		return -EINVAL;
	if (stream_mode > 1)
		return -EINVAL;
	if (frame_count > QCRATE_DMA_MAX_CHAIN_FRAMES)
		return -E2BIG;
	if (frame_count > 1 && !qdma->has_sg)
		return -EOPNOTSUPP;
	if (check_mul_overflow((size_t)frame_length_words,
			       (size_t)QCRATE_STREAM_WORD_BYTES, frame_bytes) ||
	    check_mul_overflow(*frame_bytes, (size_t)frame_count, total_bytes))
		return -EOVERFLOW;
	if (*frame_bytes > qdma->max_transfer_bytes ||
	    *total_bytes > qdma->buffer_bytes)
		return -EMSGSIZE;
	return 0;
}

/* Submit every frame before allowing either immediate or triggered start. */
static int qcrate_submit_frames(struct qcrate_dma_dev *qdma,
				dma_addr_t buffer_dma, size_t frame_bytes,
				u32 frame_count,
				bool *descriptors_submitted)
{
	struct dma_async_tx_descriptor *desc;
	dma_cookie_t cookie;
	u32 frame;
	int ret;

	for (frame = 0; frame < frame_count; frame++) {
		unsigned long flags = DMA_CTRL_ACK;

		if (frame == frame_count - 1)
			flags |= DMA_PREP_INTERRUPT;
		desc = dmaengine_prep_slave_single(
			qdma->rx_chan, buffer_dma + frame * frame_bytes,
			frame_bytes, DMA_DEV_TO_MEM, flags);
		if (!desc)
			return -EIO;

		if (frame == frame_count - 1) {
			desc->callback_result = qcrate_dma_complete;
			desc->callback_param = qdma;
		}
		cookie = dmaengine_submit(desc);
		ret = dma_submit_error(cookie);
		if (ret)
			return ret;
		*descriptors_submitted = true;
	}
	return 0;
}

/* APB low-word reads latch the matching high word in qcrate_stream_regs. */
static u64 qcrate_read_timestamp(struct qcrate_dma_dev *qdma,
				 u32 low_offset, u32 high_offset)
{
	u32 low = ioread32(qdma->regs + low_offset);
	u32 high = ioread32(qdma->regs + high_offset);

	return ((u64)high << 32) | low;
}

/*
 * Execute one finite stream command into consecutive frame slots. With SG
 * enabled, all DMA descriptors are submitted before START, so Linux is absent
 * from the frame-to-frame data path.
 */
static int qcrate_run_capture(struct qcrate_dma_dev *qdma,
			      u32 frame_length_words, u32 frame_count,
			      u32 stream_mode,
			      u32 *timeout_ms,
			      struct qcrate_capture_result *result)
{
	unsigned long wait_result;
	size_t frame_bytes;
	size_t total_bytes;
	u32 status;
	bool descriptors_submitted = false;
	int ret;

	if (!*timeout_ms)
		*timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	if (*timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;

	ret = qcrate_validate_capture(qdma, frame_length_words, frame_count,
				      stream_mode, &frame_bytes, &total_bytes);
	if (ret)
		return ret;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (qdma->triggered_active || qcrate_pool_owns_buffer_locked(qdma)) {
		ret = -EBUSY;
		goto out_unlock;
	}

	ret = qcrate_reset_stream(qdma);
	if (ret)
		goto out_unlock;

	/* Poisoning makes a missing or short DMA write obvious during verification. */
	memset(qdma->buffer, 0xa5, total_bytes);
	reinit_completion(&qdma->completion);
	qdma->result.result = DMA_TRANS_ABORTED;
	qdma->result.residue = frame_bytes;

	/* Program one finite stream sequence and clear stale Q-Crate IRQ status. */
	iowrite32(0, qdma->regs + QCRATE_STREAM_CONTROL);
	iowrite32(frame_length_words,
		  qdma->regs + QCRATE_STREAM_FRAME_LENGTH);
	iowrite32(frame_count, qdma->regs + QCRATE_STREAM_FRAME_COUNT);
	iowrite32(stream_mode, qdma->regs + QCRATE_STREAM_MODE);
	iowrite32(0, qdma->regs + QCRATE_STREAM_IRQ_ENABLE);
	iowrite32(0x3, qdma->regs + QCRATE_STREAM_IRQ_CLEAR);

	ret = qcrate_submit_frames(qdma, qdma->buffer_dma, frame_bytes, frame_count,
				   &descriptors_submitted);
	if (ret)
		goto out_reset;

	/* Arm S2MM first; START may assert AXI4-Stream TVALID immediately. */
	dma_async_issue_pending(qdma->rx_chan);
	iowrite32(QCRATE_CONTROL_START,
		  qdma->regs + QCRATE_STREAM_CONTROL);

	wait_result = wait_for_completion_interruptible_timeout(
		&qdma->completion, msecs_to_jiffies(*timeout_ms));
	if (!wait_result) {
		ret = -ETIMEDOUT;
		goto out_stop;
	}
	if ((long)wait_result < 0) {
		ret = (long)wait_result;
		goto out_stop;
	}

	ret = qcrate_wait_complete(qdma, &status);
	if (ret)
		goto out_stop;

	/* Return both DMAEngine and APB-domain evidence to userspace. */
	result->last_residue_bytes = min_t(u32, qdma->result.residue,
					    frame_bytes);
	result->dma_result = qdma->result.result;
	result->stream_status = status;
	result->completed_frames = ioread32(
		qdma->regs + QCRATE_STREAM_COMPLETED_FRAMES);
	result->current_frame_id = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_FRAME_ID);
	result->current_sample_index = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_SAMPLE);
	result->stall_cycles = ioread32(
		qdma->regs + QCRATE_STREAM_STALL_CYCLES);

	if (result->dma_result != DMA_TRANS_NOERROR ||
	    result->last_residue_bytes ||
	    !(result->stream_status & QCRATE_STATUS_DONE) ||
	    (result->stream_status & QCRATE_STATUS_ERROR) ||
	    result->completed_frames != frame_count ||
	    result->current_frame_id != frame_count - 1 ||
	    result->current_sample_index != frame_length_words - 1) {
		dev_err(qdma->dev,
			"capture failed: dma=%u residue=%u status=0x%08x frames=%u/%u frame=%u sample=%u\n",
			result->dma_result, result->last_residue_bytes,
			result->stream_status, result->completed_frames, frame_count,
			result->current_frame_id, result->current_sample_index);
		ret = -EIO;
		goto out_reset;
	}

	result->transferred_bytes = total_bytes;
	ret = 0;
	goto out_unlock;

out_stop:
out_reset:
	qcrate_reset_stream(qdma);
	if (descriptors_submitted)
		dmaengine_terminate_sync(qdma->rx_chan);
out_unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

/* Preserve the original one-frame ioctl by translating it internally. */
static int qcrate_capture(struct qcrate_dma_dev *qdma,
			  struct qcrate_dma_capture *capture)
{
	struct qcrate_capture_result result = { };
	int ret;

	ret = qcrate_run_capture(qdma, capture->frame_length_words, 1,
				 capture->stream_mode,
				 &capture->timeout_ms, &result);
	capture->transferred_bytes = result.transferred_bytes;
	capture->residue_bytes = result.last_residue_bytes;
	capture->dma_result = result.dma_result;
	capture->stream_status = result.stream_status;
	capture->completed_frames = result.completed_frames;
	capture->current_frame_id = result.current_frame_id;
	capture->current_sample_index = result.current_sample_index;
	capture->stall_cycles = result.stall_cycles;
	return ret;
}

/* Implement the finite pre-armed SG-chain ioctl. */
static int qcrate_capture_frames(struct qcrate_dma_dev *qdma,
				 struct qcrate_dma_capture_frames *capture)
{
	struct qcrate_capture_result result = { };
	int ret;

	ret = qcrate_run_capture(qdma, capture->frame_length_words,
				 capture->frame_count, capture->stream_mode,
				 &capture->timeout_ms,
				 &result);
	capture->transferred_bytes = result.transferred_bytes;
	capture->last_residue_bytes = result.last_residue_bytes;
	capture->dma_result = result.dma_result;
	capture->stream_status = result.stream_status;
	capture->completed_frames = result.completed_frames;
	capture->current_frame_id = result.current_frame_id;
	capture->current_sample_index = result.current_sample_index;
	capture->stall_cycles = result.stall_cycles;
	return ret;
}

/*
 * Complete and discard an armed transaction instead of halting AXI DMA while
 * it waits for its first packet. The stream engine turns armed ABORT into a
 * finite counter-pattern drain with the original descriptor geometry.
 */
static int qcrate_cancel_triggered_locked(struct qcrate_dma_dev *qdma)
{
	unsigned long wait_result;
	u32 status;
	int ret;

	if (!qdma->triggered_active)
		return 0;

	status = ioread32(qdma->regs + QCRATE_STREAM_STATUS);
	if (!(status & (QCRATE_STATUS_BUSY | QCRATE_STATUS_TRIGGER_SEEN))) {
		iowrite32(QCRATE_CONTROL_ABORT,
			  qdma->regs + QCRATE_STREAM_CONTROL);
		usleep_range(10, 20);
	}

	wait_result = wait_for_completion_timeout(
		&qdma->completion, msecs_to_jiffies(QCRATE_CANCEL_TIMEOUT_MS));
	if (!wait_result) {
		dev_err(qdma->dev,
			"triggered cancel drain timed out; DMA remains owned\n");
		return -ETIMEDOUT;
	}

	ret = qcrate_wait_complete(qdma, &status);
	if (ret)
		return ret;
	if (qdma->result.result != DMA_TRANS_NOERROR || qdma->result.residue) {
		dev_err(qdma->dev,
			"triggered cancel drain failed: dma=%u residue=%u status=0x%08x\n",
			qdma->result.result, qdma->result.residue, status);
		return -EIO;
	}

	ret = qcrate_reset_stream(qdma);
	if (ret)
		return ret;
	qdma->triggered_active = false;
	qdma->triggered_cancelled = true;
	return 0;
}

/*
 * Establish the trigger-before-data ordering contract. This ioctl returns only
 * after S2MM owns all destination descriptors and the PL source is armed.
 */
static int qcrate_arm_triggered(struct qcrate_dma_dev *qdma,
				struct qcrate_dma_triggered_arm *arm)
{
	size_t frame_bytes;
	size_t total_bytes;
	u32 status;
	bool descriptors_submitted = false;
	int ret;

	ret = qcrate_validate_capture(qdma, arm->frame_length_words,
				      arm->frame_count, arm->stream_mode,
				      &frame_bytes, &total_bytes);
	if (ret)
		return ret;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (qdma->triggered_active || qcrate_pool_owns_buffer_locked(qdma)) {
		ret = -EBUSY;
		goto out_unlock;
	}

	ret = qcrate_reset_stream(qdma);
	if (ret)
		goto out_unlock;

	memset(qdma->buffer, 0xa5, total_bytes);
	reinit_completion(&qdma->completion);
	qdma->result.result = DMA_TRANS_ABORTED;
	qdma->result.residue = frame_bytes;

	iowrite32(0, qdma->regs + QCRATE_STREAM_CONTROL);
	iowrite32(arm->frame_length_words,
		  qdma->regs + QCRATE_STREAM_FRAME_LENGTH);
	iowrite32(arm->frame_count, qdma->regs + QCRATE_STREAM_FRAME_COUNT);
	iowrite32(arm->stream_mode, qdma->regs + QCRATE_STREAM_MODE);
	iowrite32(0, qdma->regs + QCRATE_STREAM_IRQ_ENABLE);
	iowrite32(0x3, qdma->regs + QCRATE_STREAM_IRQ_CLEAR);

	ret = qcrate_submit_frames(qdma, qdma->buffer_dma, frame_bytes,
				   arm->frame_count,
				   &descriptors_submitted);
	if (ret)
		goto out_terminate;

	dma_async_issue_pending(qdma->rx_chan);
	qdma->triggered_active = true;
	qdma->triggered_cancelled = false;
	qdma->triggered_frame_length_words = arm->frame_length_words;
	qdma->triggered_frame_count = arm->frame_count;
	qdma->triggered_frame_bytes = frame_bytes;
	qdma->triggered_total_bytes = total_bytes;

	iowrite32(QCRATE_CONTROL_ARM_TRIGGERED,
		  qdma->regs + QCRATE_STREAM_CONTROL);
	usleep_range(10, 20);
	ret = qcrate_wait_armed(qdma, &status);
	if (ret) {
		dev_err(qdma->dev,
			"triggered source did not arm (status 0x%08x)\n", status);
		qcrate_cancel_triggered_locked(qdma);
	}
	goto out_unlock;

out_terminate:
	qcrate_reset_stream(qdma);
	if (descriptors_submitted)
		dmaengine_terminate_sync(qdma->rx_chan);
out_unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

/* Wait for the already-armed transaction and collect coherent trigger timing. */
static int qcrate_wait_triggered(struct qcrate_dma_dev *qdma,
				 struct qcrate_dma_triggered_result *result)
{
	unsigned long wait_result;
	u32 status;
	int ret;

	if (!result->timeout_ms)
		result->timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	if (result->timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (!qdma->triggered_active) {
		ret = qdma->triggered_cancelled ? -ECANCELED : -EINVAL;
		goto out_unlock;
	}
	mutex_unlock(&qdma->capture_lock);

	wait_result = wait_for_completion_interruptible_timeout(
		&qdma->completion, msecs_to_jiffies(result->timeout_ms));

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (!qdma->triggered_active) {
		ret = -ECANCELED;
		goto out_unlock;
	}
	if (!wait_result) {
		ret = -ETIMEDOUT;
		goto out_cancel;
	}
	if ((long)wait_result < 0) {
		ret = (long)wait_result;
		goto out_cancel;
	}

	ret = qcrate_wait_complete(qdma, &status);
	if (ret)
		goto out_cancel;

	result->timestamp_flags = 0;
	result->last_residue_bytes = min_t(u32, qdma->result.residue,
					   qdma->triggered_frame_bytes);
	result->dma_result = qdma->result.result;
	result->stream_status = status;
	result->completed_frames = ioread32(
		qdma->regs + QCRATE_STREAM_COMPLETED_FRAMES);
	result->current_frame_id = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_FRAME_ID);
	result->current_sample_index = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_SAMPLE);
	result->stall_cycles = ioread32(
		qdma->regs + QCRATE_STREAM_STALL_CYCLES);
	result->trigger_shot_id = ioread32(
		qdma->regs + QCRATE_STREAM_TRIGGER_SHOT_ID);
	result->trigger_count = ioread32(
		qdma->regs + QCRATE_STREAM_TRIGGER_COUNT);
	result->missed_trigger_count = ioread32(
		qdma->regs + QCRATE_STREAM_MISSED_TRIGGERS);
	result->trigger_time = qcrate_read_timestamp(
		qdma, QCRATE_STREAM_TRIGGER_TIME_LO,
		QCRATE_STREAM_TRIGGER_TIME_HI);
	result->first_sample_time = qcrate_read_timestamp(
		qdma, QCRATE_STREAM_FIRST_TIME_LO,
		QCRATE_STREAM_FIRST_TIME_HI);
	if (status & QCRATE_STATUS_TRIGGER_SEEN)
		result->timestamp_flags |= QCRATE_DMA_TRIGGER_SEEN;
	if (status & QCRATE_STATUS_FIRST_TIME_VALID)
		result->timestamp_flags |= QCRATE_DMA_FIRST_SAMPLE_TIME_VALID;

	if (result->dma_result != DMA_TRANS_NOERROR ||
	    result->last_residue_bytes ||
	    !(result->stream_status & QCRATE_STATUS_DONE) ||
	    (result->stream_status & QCRATE_STATUS_ERROR) ||
	    result->completed_frames != qdma->triggered_frame_count ||
	    result->current_frame_id != qdma->triggered_frame_count - 1 ||
	    result->current_sample_index !=
			qdma->triggered_frame_length_words - 1 ||
	    (result->timestamp_flags &
		(QCRATE_DMA_TRIGGER_SEEN |
		 QCRATE_DMA_FIRST_SAMPLE_TIME_VALID)) !=
		(QCRATE_DMA_TRIGGER_SEEN |
		 QCRATE_DMA_FIRST_SAMPLE_TIME_VALID) ||
	    result->trigger_count != 1 ||
	    result->missed_trigger_count != 0 ||
	    result->first_sample_time < result->trigger_time) {
		dev_err(qdma->dev,
			"triggered capture failed: dma=%u residue=%u status=0x%08x "
			"frames=%u/%u frame=%u sample=%u flags=0x%x\n",
			result->dma_result, result->last_residue_bytes,
			result->stream_status, result->completed_frames,
			qdma->triggered_frame_count, result->current_frame_id,
			result->current_sample_index, result->timestamp_flags);
		ret = -EIO;
		goto out_cancel;
	}

	result->transferred_bytes = qdma->triggered_total_bytes;
	qdma->triggered_active = false;
	qdma->triggered_cancelled = false;
	ret = 0;
	goto out_unlock;

out_cancel:
	if (qcrate_cancel_triggered_locked(qdma))
		dev_err(qdma->dev, "failed to recover triggered transaction\n");
out_unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

static int qcrate_cancel_triggered(struct qcrate_dma_dev *qdma)
{
	int ret;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (!qdma->triggered_active) {
		ret = -EINVAL;
	} else {
		ret = qcrate_cancel_triggered_locked(qdma);
	}
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

static bool qcrate_pool_owns_buffer_locked(struct qcrate_dma_dev *qdma)
{
	return qdma->pool_running || qdma->pool_stopping ||
		qdma->pool_dma_active || qdma->pool_ready_banks ||
		qdma->pool_user_banks;
}

static u32 qcrate_pool_flags_locked(struct qcrate_dma_dev *qdma)
{
	u32 flags = 0;
	u32 status;

	if (qdma->pool_running)
		flags |= QCRATE_DMA_POOL_RUNNING;
	if (qdma->pool_starved)
		flags |= QCRATE_DMA_POOL_STARVED;
	if (qdma->pool_faulted)
		flags |= QCRATE_DMA_POOL_FAULTED;
	if (qdma->pool_stopping)
		flags |= QCRATE_DMA_POOL_STOPPING;
	if (qdma->pool_dma_active) {
		status = ioread32(qdma->regs + QCRATE_STREAM_STATUS);
		if (status & QCRATE_STATUS_ARMED)
			flags |= QCRATE_DMA_POOL_ARMED;
	}
	return flags;
}

static void qcrate_pool_account_missed_locked(struct qcrate_dma_dev *qdma)
{
	qdma->pool_skipped_triggers += ioread32(
		qdma->regs + QCRATE_STREAM_MISSED_TRIGGERS);
}

static int qcrate_pool_find_free_locked(struct qcrate_dma_dev *qdma)
{
	u32 bank;

	for (bank = 0; bank < qdma->pool_bank_count; bank++) {
		if (qdma->pool_banks[bank].state == QCRATE_BANK_FREE)
			return bank;
	}
	return -ENOSPC;
}

static int qcrate_pool_find_ready_locked(struct qcrate_dma_dev *qdma)
{
	u64 oldest = U64_MAX;
	int selected = -ENOENT;
	u32 bank;

	for (bank = 0; bank < qdma->pool_bank_count; bank++) {
		if (qdma->pool_banks[bank].state == QCRATE_BANK_READY &&
		    qdma->pool_banks[bank].sequence < oldest) {
			oldest = qdma->pool_banks[bank].sequence;
			selected = bank;
		}
	}
	return selected;
}

static void qcrate_pool_fault_locked(struct qcrate_dma_dev *qdma, int error)
{
	qdma->pool_running = false;
	qdma->pool_faulted = true;
	qdma->pool_starved = false;
	qdma->pool_last_error = error < 0 ? -error : error;
	qdma->pool_error_events++;
}

/* Drain a finite issued bank; never halt an armed pre-packet S2MM channel. */
static int qcrate_pool_drain_locked(struct qcrate_dma_dev *qdma,
				    bool dma_done)
{
	unsigned long wait_result;
	struct qcrate_pool_bank *bank;
	u32 status;
	int ret;

	if (!qdma->pool_dma_active)
		return 0;

	status = ioread32(qdma->regs + QCRATE_STREAM_STATUS);
	if (!(status & (QCRATE_STATUS_BUSY | QCRATE_STATUS_TRIGGER_SEEN))) {
		iowrite32(QCRATE_CONTROL_ABORT,
			  qdma->regs + QCRATE_STREAM_CONTROL);
		usleep_range(10, 20);
	}

	if (!dma_done) {
		wait_result = wait_event_timeout(
			qdma->pool_waitq,
			atomic_read(&qdma->pool_events) &
				QCRATE_POOL_EVENT_DMA_DONE,
			msecs_to_jiffies(QCRATE_CANCEL_TIMEOUT_MS));
		if (!wait_result)
			return -ETIMEDOUT;
		atomic_andnot(QCRATE_POOL_EVENT_DMA_DONE, &qdma->pool_events);
	}

	ret = qcrate_wait_complete(qdma, &status);
	if (ret)
		return ret;
	if (qdma->result.result != DMA_TRANS_NOERROR || qdma->result.residue)
		return -EIO;

	qcrate_pool_account_missed_locked(qdma);
	ret = qcrate_reset_stream(qdma);
	if (ret)
		return ret;

	bank = &qdma->pool_banks[qdma->pool_filling_bank];
	bank->state = QCRATE_BANK_FREE;
	qdma->pool_free_banks++;
	qdma->pool_filling_bank = QCRATE_DMA_BANK_NONE;
	qdma->pool_dma_active = false;
	return 0;
}

static int qcrate_pool_arm_bank_locked(struct qcrate_dma_dev *qdma, u32 index)
{
	struct qcrate_pool_bank *bank = &qdma->pool_banks[index];
	dma_addr_t bank_dma = qdma->buffer_dma + index * qdma->pool_bank_bytes;
	bool descriptors_submitted = false;
	u32 status;
	int ret;

	if (bank->state != QCRATE_BANK_FREE || qdma->pool_dma_active)
		return -EINVAL;

	/* Include triggers accumulated while the pool had no FREE bank. */
	qcrate_pool_account_missed_locked(qdma);
	ret = qcrate_reset_stream(qdma);
	if (ret)
		return ret;

	memset((u8 *)qdma->buffer + index * qdma->pool_bank_bytes, 0xa5,
	       qdma->pool_bank_bytes);
	qdma->result.result = DMA_TRANS_ABORTED;
	qdma->result.residue = qdma->pool_frame_bytes;

	iowrite32(0, qdma->regs + QCRATE_STREAM_CONTROL);
	iowrite32(qdma->pool_frame_length_words,
		  qdma->regs + QCRATE_STREAM_FRAME_LENGTH);
	iowrite32(qdma->pool_frame_count,
		  qdma->regs + QCRATE_STREAM_FRAME_COUNT);
	iowrite32(qdma->pool_stream_mode,
		  qdma->regs + QCRATE_STREAM_MODE);
	iowrite32(0, qdma->regs + QCRATE_STREAM_IRQ_ENABLE);
	iowrite32(0x3, qdma->regs + QCRATE_STREAM_IRQ_CLEAR);

	ret = qcrate_submit_frames(qdma, bank_dma, qdma->pool_frame_bytes,
				   qdma->pool_frame_count,
				   &descriptors_submitted);
	if (ret)
		goto terminate;

	bank->state = QCRATE_BANK_FILLING;
	bank->generation++;
	qdma->pool_free_banks--;
	qdma->pool_filling_bank = index;
	qdma->pool_dma_active = true;
	dma_async_issue_pending(qdma->rx_chan);

	iowrite32(QCRATE_CONTROL_ARM_TRIGGERED,
		  qdma->regs + QCRATE_STREAM_CONTROL);
	usleep_range(10, 20);
	ret = readl_poll_timeout(
		qdma->regs + QCRATE_STREAM_STATUS, status,
		(status & QCRATE_STATUS_ARMED) &&
			!(status & QCRATE_STATUS_COMMAND_BUSY),
		10, qdma->pool_arm_timeout_ms * 1000U);
	if (!ret) {
		qdma->pool_starved = false;
		return 0;
	}

	dev_err(qdma->dev, "pool bank %u failed to arm (status 0x%08x)\n",
		index, status);
	if (qcrate_pool_drain_locked(qdma, false))
		dev_err(qdma->dev, "pool bank %u arm recovery failed\n", index);
	return ret;

terminate:
	qcrate_reset_stream(qdma);
	if (descriptors_submitted)
		dmaengine_terminate_sync(qdma->rx_chan);
	return ret;
}

static int qcrate_pool_finalize_locked(struct qcrate_dma_dev *qdma)
{
	struct qcrate_dma_pool_dequeue *result;
	struct qcrate_pool_bank *bank;
	u32 index = qdma->pool_filling_bank;
	u32 status;
	int ret;

	if (!qdma->pool_dma_active || index >= qdma->pool_bank_count)
		return -EINVAL;

	bank = &qdma->pool_banks[index];
	result = &bank->result;
	memset(result, 0, sizeof(*result));

	ret = qcrate_wait_complete(qdma, &status);
	if (ret)
		goto failed;

	result->bank_index = index;
	result->bank_generation = bank->generation;
	result->offset_bytes = index * qdma->pool_bank_bytes;
	result->valid_bytes = qdma->pool_bank_bytes;
	result->frame_length_words = qdma->pool_frame_length_words;
	result->frame_count = qdma->pool_frame_count;
	result->stream_mode = qdma->pool_stream_mode;
	result->dma_result = qdma->result.result;
	result->last_residue_bytes = min_t(u32, qdma->result.residue,
					      qdma->pool_frame_bytes);
	result->stream_status = status;
	result->completed_frames = ioread32(
		qdma->regs + QCRATE_STREAM_COMPLETED_FRAMES);
	result->current_frame_id = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_FRAME_ID);
	result->current_sample_index = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_SAMPLE);
	result->stall_cycles = ioread32(
		qdma->regs + QCRATE_STREAM_STALL_CYCLES);
	result->trigger_shot_id = ioread32(
		qdma->regs + QCRATE_STREAM_TRIGGER_SHOT_ID);
	result->trigger_count = ioread32(
		qdma->regs + QCRATE_STREAM_TRIGGER_COUNT);
	result->missed_trigger_count = ioread32(
		qdma->regs + QCRATE_STREAM_MISSED_TRIGGERS);
	result->trigger_time = qcrate_read_timestamp(
		qdma, QCRATE_STREAM_TRIGGER_TIME_LO,
		QCRATE_STREAM_TRIGGER_TIME_HI);
	result->first_sample_time = qcrate_read_timestamp(
		qdma, QCRATE_STREAM_FIRST_TIME_LO,
		QCRATE_STREAM_FIRST_TIME_HI);
	if (status & QCRATE_STATUS_TRIGGER_SEEN)
		result->timestamp_flags |= QCRATE_DMA_TRIGGER_SEEN;
	if (status & QCRATE_STATUS_FIRST_TIME_VALID)
		result->timestamp_flags |=
			QCRATE_DMA_FIRST_SAMPLE_TIME_VALID;

	if (result->dma_result != DMA_TRANS_NOERROR ||
	    result->last_residue_bytes ||
	    !(status & QCRATE_STATUS_DONE) ||
	    (status & QCRATE_STATUS_ERROR) ||
	    result->completed_frames != qdma->pool_frame_count ||
	    result->current_frame_id != qdma->pool_frame_count - 1 ||
	    result->current_sample_index !=
		qdma->pool_frame_length_words - 1 ||
	    result->trigger_count != 1 ||
	    (result->timestamp_flags &
		(QCRATE_DMA_TRIGGER_SEEN |
		 QCRATE_DMA_FIRST_SAMPLE_TIME_VALID)) !=
		(QCRATE_DMA_TRIGGER_SEEN |
		 QCRATE_DMA_FIRST_SAMPLE_TIME_VALID) ||
	    result->first_sample_time < result->trigger_time) {
		ret = -EIO;
		goto failed;
	}

	qcrate_pool_account_missed_locked(qdma);
	ret = qcrate_reset_stream(qdma);
	if (ret)
		goto failed_without_reset;

	qdma->pool_dma_active = false;
	qdma->pool_filling_bank = QCRATE_DMA_BANK_NONE;
	bank->state = QCRATE_BANK_READY;
	bank->sequence = qdma->pool_next_sequence++;
	result->run_id = qdma->pool_run_id;
	result->bank_sequence = bank->sequence;
	result->completion_mono_ns = READ_ONCE(qdma->pool_dma_completion_ns);
	qdma->pool_ready_banks++;
	qdma->pool_produced_banks++;
	qdma->pool_produced_bytes += qdma->pool_bank_bytes;
	qdma->pool_last_trigger_shot_id = result->trigger_shot_id;
	qdma->pool_queue_high_water = max(qdma->pool_queue_high_water,
					  qdma->pool_ready_banks);
	return 0;

failed:
	qcrate_pool_account_missed_locked(qdma);
	if (qcrate_reset_stream(qdma))
		dev_err(qdma->dev, "pool completion reset failed\n");
failed_without_reset:
	qdma->pool_dma_active = false;
	qdma->pool_filling_bank = QCRATE_DMA_BANK_NONE;
	bank->state = QCRATE_BANK_FREE;
	qdma->pool_free_banks++;
	return ret;
}

static void qcrate_pool_discard_ready_locked(struct qcrate_dma_dev *qdma)
{
	u32 bank;

	qdma->pool_last_discarded_ready = 0;
	for (bank = 0; bank < qdma->pool_bank_count; bank++) {
		if (qdma->pool_banks[bank].state == QCRATE_BANK_READY) {
			qdma->pool_banks[bank].state = QCRATE_BANK_FREE;
			qdma->pool_ready_banks--;
			qdma->pool_free_banks++;
			qdma->pool_last_discarded_ready++;
		}
	}
}

static int qcrate_pool_thread_fn(void *argument)
{
	struct qcrate_dma_dev *qdma = argument;

	while (!kthread_should_stop()) {
		int events;
		int bank;
		int ret = 0;
		bool wake_ready = false;

		wait_event_interruptible(qdma->pool_waitq,
			kthread_should_stop() || atomic_read(&qdma->pool_events));
		if (kthread_should_stop())
			break;
		events = atomic_xchg(&qdma->pool_events, 0);

		mutex_lock(&qdma->capture_lock);
		if (events & QCRATE_POOL_EVENT_STOP) {
			qdma->pool_running = false;
			if (qdma->pool_dma_active)
				ret = qcrate_pool_drain_locked(
					qdma,
					events & QCRATE_POOL_EVENT_DMA_DONE);
			else if (qdma->pool_configured) {
				qcrate_pool_account_missed_locked(qdma);
				ret = qcrate_reset_stream(qdma);
			}
			if (!ret)
				qcrate_pool_discard_ready_locked(qdma);
			else
				qcrate_pool_fault_locked(qdma, ret);
			qdma->pool_stop_result = ret;
			qdma->pool_stopping = false;
			qdma->pool_starved = false;
			complete_all(&qdma->pool_stop_done);
			wake_up_interruptible(&qdma->pool_ready_waitq);
			mutex_unlock(&qdma->capture_lock);
			continue;
		}

		if ((events & QCRATE_POOL_EVENT_DMA_DONE) &&
		    qdma->pool_dma_active) {
			ret = qcrate_pool_finalize_locked(qdma);
			wake_ready = true;
			if (ret)
				qcrate_pool_fault_locked(qdma, ret);
		}

		if (qdma->pool_running && !qdma->pool_faulted &&
		    !qdma->pool_dma_active) {
			bank = qcrate_pool_find_free_locked(qdma);
			if (bank < 0) {
				if (!qdma->pool_starved) {
					qdma->pool_starved = true;
					qdma->pool_starvation_events++;
				}
			} else {
				ret = qcrate_pool_arm_bank_locked(qdma, bank);
				if (ret)
					qcrate_pool_fault_locked(qdma, ret);
			}
		}

		if (wake_ready || (events & QCRATE_POOL_EVENT_KICK) ||
		    qdma->pool_faulted)
			wake_up_interruptible(&qdma->pool_ready_waitq);
		mutex_unlock(&qdma->capture_lock);
	}
	return 0;
}

static int qcrate_pool_start(struct qcrate_dma_dev *qdma,
			     struct qcrate_dma_pool_start *start)
{
	size_t frame_bytes;
	size_t bank_bytes;
	size_t pool_bytes;
	u32 bank;
	int ret;

	if (start->flags != QCRATE_DMA_POOL_START_TRIGGERED)
		return -EINVAL;
	if (!qdma->has_sg)
		return -EOPNOTSUPP;
	if (start->bank_count < 2 ||
	    start->bank_count > QCRATE_DMA_MAX_POOL_BANKS)
		return -EINVAL;
	if (!start->arm_timeout_ms)
		start->arm_timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	if (start->arm_timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;

	ret = qcrate_validate_capture(qdma, start->frame_length_words,
				      start->frame_count, start->stream_mode,
				      &frame_bytes, &bank_bytes);
	if (ret)
		return ret;
	if (check_mul_overflow(bank_bytes, (size_t)start->bank_count,
			       &pool_bytes) || pool_bytes > qdma->buffer_bytes)
		return -EMSGSIZE;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (qdma->triggered_active || qcrate_pool_owns_buffer_locked(qdma)) {
		ret = -EBUSY;
		goto unlock;
	}

	/* Clear stale status before run-local counters begin. */
	ret = qcrate_reset_stream(qdma);
	if (ret)
		goto unlock;

	qdma->pool_configured = true;
	qdma->pool_running = true;
	qdma->pool_stopping = false;
	qdma->pool_faulted = false;
	qdma->pool_starved = false;
	qdma->pool_dma_active = false;
	qdma->pool_frame_length_words = start->frame_length_words;
	qdma->pool_frame_count = start->frame_count;
	qdma->pool_stream_mode = start->stream_mode;
	qdma->pool_arm_timeout_ms = start->arm_timeout_ms;
	qdma->pool_bank_count = start->bank_count;
	qdma->pool_frame_bytes = frame_bytes;
	qdma->pool_bank_bytes = bank_bytes;
	qdma->pool_filling_bank = QCRATE_DMA_BANK_NONE;
	qdma->pool_ready_banks = 0;
	qdma->pool_user_banks = 0;
	qdma->pool_free_banks = start->bank_count;
	qdma->pool_queue_high_water = 0;
	qdma->pool_last_error = 0;
	qdma->pool_last_discarded_ready = 0;
	qdma->pool_last_trigger_shot_id = 0;
	qdma->pool_run_id = qdma->pool_next_run_id++;
	qdma->pool_next_sequence = 1;
	qdma->pool_produced_banks = 0;
	qdma->pool_dequeued_banks = 0;
	qdma->pool_consumed_banks = 0;
	qdma->pool_starvation_events = 0;
	qdma->pool_skipped_triggers = 0;
	qdma->pool_error_events = 0;
	qdma->pool_produced_bytes = 0;
	for (bank = 0; bank < QCRATE_DMA_MAX_POOL_BANKS; bank++) {
		qdma->pool_banks[bank].state = QCRATE_BANK_FREE;
		qdma->pool_banks[bank].sequence = 0;
		memset(&qdma->pool_banks[bank].result, 0,
		       sizeof(qdma->pool_banks[bank].result));
	}

	ret = qcrate_pool_arm_bank_locked(qdma, 0);
	if (ret) {
		qcrate_pool_fault_locked(qdma, ret);
		goto unlock;
	}
	start->bank_bytes = qdma->pool_bank_bytes;
	start->run_id = qdma->pool_run_id;

unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

static bool qcrate_pool_dequeue_ready(struct qcrate_dma_dev *qdma)
{
	return READ_ONCE(qdma->pool_ready_banks) ||
		!READ_ONCE(qdma->pool_running) ||
		READ_ONCE(qdma->pool_faulted);
}

static int qcrate_pool_dequeue(struct qcrate_dma_dev *qdma,
			       struct qcrate_dma_pool_dequeue *dequeue)
{
	struct qcrate_pool_bank *bank;
	long wait_result;
	int index;
	int ret;

	if (dequeue->timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;
	if (dequeue->timeout_ms) {
		wait_result = wait_event_interruptible_timeout(
			qdma->pool_ready_waitq,
			qcrate_pool_dequeue_ready(qdma),
			msecs_to_jiffies(dequeue->timeout_ms));
		if (wait_result < 0)
			return wait_result;
		if (!wait_result)
			return -ETIMEDOUT;
	}

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	index = qcrate_pool_find_ready_locked(qdma);
	if (index < 0) {
		ret = qdma->pool_faulted ? -EIO :
			(qdma->pool_running ? -EAGAIN : -ESHUTDOWN);
		goto unlock;
	}

	bank = &qdma->pool_banks[index];
	*dequeue = bank->result;
	dequeue->pool_flags = qcrate_pool_flags_locked(qdma);
	bank->state = QCRATE_BANK_USER_OWNED;
	qdma->pool_ready_banks--;
	qdma->pool_user_banks++;
	qdma->pool_dequeued_banks++;
	ret = 0;

unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

static int qcrate_pool_release_bank(struct qcrate_dma_dev *qdma,
				    struct qcrate_dma_pool_release *release)
{
	struct qcrate_pool_bank *bank;
	long wait_result;
	bool wait_for_arm;
	u32 timeout_ms;
	int ret;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (release->bank_index >= qdma->pool_bank_count ||
	    release->run_id != qdma->pool_run_id) {
		ret = -ESTALE;
		goto unlock;
	}
	bank = &qdma->pool_banks[release->bank_index];
	if (bank->state != QCRATE_BANK_USER_OWNED ||
	    release->bank_generation != bank->generation ||
	    release->bank_sequence != bank->sequence) {
		ret = -ESTALE;
		goto unlock;
	}

	bank->state = QCRATE_BANK_FREE;
	qdma->pool_user_banks--;
	qdma->pool_free_banks++;
	qdma->pool_consumed_banks++;
	wait_for_arm = qdma->pool_running && !qdma->pool_dma_active;
	timeout_ms = qdma->pool_arm_timeout_ms;
	ret = 0;
	atomic_or(QCRATE_POOL_EVENT_KICK, &qdma->pool_events);
	wake_up_interruptible(&qdma->pool_waitq);
	mutex_unlock(&qdma->capture_lock);

	if (!wait_for_arm)
		return 0;
	wait_result = wait_event_interruptible_timeout(
		qdma->pool_ready_waitq,
		READ_ONCE(qdma->pool_dma_active) ||
		READ_ONCE(qdma->pool_faulted) ||
		!READ_ONCE(qdma->pool_running),
		msecs_to_jiffies(timeout_ms));
	if (wait_result < 0)
		return wait_result;
	if (!wait_result)
		return -ETIMEDOUT;
	if (READ_ONCE(qdma->pool_faulted))
		return -EIO;
	return READ_ONCE(qdma->pool_dma_active) ? 0 : -ESHUTDOWN;

unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

static void qcrate_pool_get_status_locked(
	struct qcrate_dma_dev *qdma, struct qcrate_dma_pool_status *status)
{
	memset(status, 0, sizeof(*status));
	status->pool_flags = qcrate_pool_flags_locked(qdma);
	status->bank_count = qdma->pool_bank_count;
	status->bank_bytes = qdma->pool_bank_bytes;
	status->frame_length_words = qdma->pool_frame_length_words;
	status->frame_count = qdma->pool_frame_count;
	status->stream_mode = qdma->pool_stream_mode;
	status->filling_bank = qdma->pool_filling_bank;
	status->ready_banks = qdma->pool_ready_banks;
	status->user_banks = qdma->pool_user_banks;
	status->free_banks = qdma->pool_free_banks;
	status->queue_high_water = qdma->pool_queue_high_water;
	status->last_error = qdma->pool_last_error;
	status->last_trigger_shot_id = qdma->pool_last_trigger_shot_id;
	status->run_id = qdma->pool_run_id;
	status->produced_banks = qdma->pool_produced_banks;
	status->dequeued_banks = qdma->pool_dequeued_banks;
	status->consumed_banks = qdma->pool_consumed_banks;
	status->starvation_events = qdma->pool_starvation_events;
	status->skipped_triggers = qdma->pool_skipped_triggers;
	if (qdma->pool_configured)
		status->skipped_triggers += ioread32(
			qdma->regs + QCRATE_STREAM_MISSED_TRIGGERS);
	status->error_events = qdma->pool_error_events;
	status->produced_bytes = qdma->pool_produced_bytes;
}

static int qcrate_pool_get_status(struct qcrate_dma_dev *qdma,
				  struct qcrate_dma_pool_status *status)
{
	int ret = mutex_lock_interruptible(&qdma->capture_lock);

	if (ret)
		return ret;
	qcrate_pool_get_status_locked(qdma, status);
	mutex_unlock(&qdma->capture_lock);
	return 0;
}

static int qcrate_pool_stop(struct qcrate_dma_dev *qdma,
			    struct qcrate_dma_pool_stop *stop)
{
	unsigned long wait_result;
	int ret;

	if (stop->flags || memchr_inv(stop->reserved, 0,
				      sizeof(stop->reserved)))
		return -EINVAL;
	if (!stop->timeout_ms)
		stop->timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	if (stop->timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;
	if (!qdma->pool_configured ||
	    (stop->run_id && stop->run_id != qdma->pool_run_id)) {
		ret = -ESTALE;
		goto unlock;
	}
	if (qdma->pool_stopping) {
		ret = -EBUSY;
		goto unlock;
	}

	qdma->pool_running = false;
	qdma->pool_stopping = true;
	qdma->pool_stop_result = 0;
	reinit_completion(&qdma->pool_stop_done);
	atomic_or(QCRATE_POOL_EVENT_STOP, &qdma->pool_events);
	wake_up_interruptible(&qdma->pool_waitq);
	mutex_unlock(&qdma->capture_lock);

	wait_result = wait_for_completion_interruptible_timeout(
		&qdma->pool_stop_done, msecs_to_jiffies(stop->timeout_ms));
	if (wait_result < 0)
		return wait_result;
	if (!wait_result)
		return -ETIMEDOUT;

	mutex_lock(&qdma->capture_lock);
	stop->run_id = qdma->pool_run_id;
	stop->discarded_ready_banks = qdma->pool_last_discarded_ready;
	ret = qdma->pool_stop_result;
	mutex_unlock(&qdma->capture_lock);
	return ret;

unlock:
	mutex_unlock(&qdma->capture_lock);
	return ret;
}

/* Implement the fixed-width UAPI declared in qcrate_dma_uapi.h. */
static long qcrate_dma_ioctl(struct file *file, unsigned int command,
			     unsigned long argument)
{
	struct qcrate_dma_dev *qdma = file->private_data;
	void __user *user_arg = (void __user *)argument;
	struct qcrate_dma_capture capture;
	struct qcrate_dma_capture_frames capture_frames;
	struct qcrate_dma_triggered_arm triggered_arm;
	struct qcrate_dma_triggered_result triggered_result;
	struct qcrate_dma_pool_start pool_start;
	struct qcrate_dma_pool_dequeue pool_dequeue;
	struct qcrate_dma_pool_release pool_release;
	struct qcrate_dma_pool_status pool_status;
	struct qcrate_dma_pool_stop pool_stop;
	struct qcrate_dma_info info = {
		.abi_version = QCRATE_DMA_ABI_VERSION,
		.buffer_bytes = qdma->buffer_bytes,
		.max_transfer_bytes = qdma->max_transfer_bytes,
		.stream_word_bytes = QCRATE_STREAM_WORD_BYTES,
	};
	struct qcrate_dma_caps caps = {
		.abi_version = QCRATE_DMA_ABI_VERSION,
		.feature_flags = QCRATE_DMA_CAP_DSP_MODE |
			QCRATE_DMA_CAP_TRIGGERED |
			(qdma->has_sg ? (QCRATE_DMA_CAP_SG_CHAIN |
					 QCRATE_DMA_CAP_BANK_POOL) : 0),
		.max_chain_frames = qdma->has_sg ?
			QCRATE_DMA_MAX_CHAIN_FRAMES : 1,
	};
	int ret;

	switch (command) {
	case QCRATE_DMA_IOC_GET_INFO:
		if (copy_to_user(user_arg, &info, sizeof(info)))
			return -EFAULT;
		return 0;

	case QCRATE_DMA_IOC_CAPTURE:
		if (copy_from_user(&capture, user_arg, sizeof(capture)))
			return -EFAULT;
		if (memchr_inv(capture.reserved, 0, sizeof(capture.reserved)))
			return -EINVAL;

		ret = qcrate_capture(qdma, &capture);
		if (copy_to_user(user_arg, &capture, sizeof(capture)))
			return -EFAULT;
		return ret;

	case QCRATE_DMA_IOC_GET_CAPS:
		if (copy_to_user(user_arg, &caps, sizeof(caps)))
			return -EFAULT;
		return 0;

	case QCRATE_DMA_IOC_CAPTURE_FRAMES:
		if (copy_from_user(&capture_frames, user_arg,
				   sizeof(capture_frames)))
			return -EFAULT;
		if (memchr_inv(capture_frames.reserved, 0,
			       sizeof(capture_frames.reserved)))
			return -EINVAL;

		ret = qcrate_capture_frames(qdma, &capture_frames);
		if (copy_to_user(user_arg, &capture_frames,
				 sizeof(capture_frames)))
			return -EFAULT;
		return ret;

	case QCRATE_DMA_IOC_ARM_TRIGGERED:
		if (copy_from_user(&triggered_arm, user_arg,
				   sizeof(triggered_arm)))
			return -EFAULT;
		if (memchr_inv(triggered_arm.reserved, 0,
			       sizeof(triggered_arm.reserved)))
			return -EINVAL;
		return qcrate_arm_triggered(qdma, &triggered_arm);

	case QCRATE_DMA_IOC_WAIT_TRIGGERED:
		if (copy_from_user(&triggered_result, user_arg,
				   sizeof(triggered_result)))
			return -EFAULT;
		if (triggered_result.reserved0 ||
		    memchr_inv(triggered_result.reserved, 0,
			       sizeof(triggered_result.reserved)))
			return -EINVAL;
		ret = qcrate_wait_triggered(qdma, &triggered_result);
		if (copy_to_user(user_arg, &triggered_result,
				 sizeof(triggered_result)))
			return -EFAULT;
		return ret;

	case QCRATE_DMA_IOC_CANCEL_TRIGGERED:
		return qcrate_cancel_triggered(qdma);

	case QCRATE_DMA_IOC_POOL_START:
		if (copy_from_user(&pool_start, user_arg, sizeof(pool_start)))
			return -EFAULT;
		if (pool_start.reserved0 || pool_start.bank_bytes ||
		    pool_start.run_id ||
		    memchr_inv(pool_start.reserved, 0,
			       sizeof(pool_start.reserved)))
			return -EINVAL;
		ret = qcrate_pool_start(qdma, &pool_start);
		if (copy_to_user(user_arg, &pool_start, sizeof(pool_start)))
			return -EFAULT;
		return ret;

	case QCRATE_DMA_IOC_POOL_DEQUEUE:
		if (copy_from_user(&pool_dequeue, user_arg,
				   sizeof(pool_dequeue)))
			return -EFAULT;
		if (memchr_inv(pool_dequeue.reserved, 0,
			       sizeof(pool_dequeue.reserved)))
			return -EINVAL;
		ret = qcrate_pool_dequeue(qdma, &pool_dequeue);
		if (copy_to_user(user_arg, &pool_dequeue,
				 sizeof(pool_dequeue)))
			return -EFAULT;
		return ret;

	case QCRATE_DMA_IOC_POOL_RELEASE:
		if (copy_from_user(&pool_release, user_arg,
				   sizeof(pool_release)))
			return -EFAULT;
		if (memchr_inv(pool_release.reserved, 0,
			       sizeof(pool_release.reserved)))
			return -EINVAL;
		return qcrate_pool_release_bank(qdma, &pool_release);

	case QCRATE_DMA_IOC_POOL_STATUS:
		ret = qcrate_pool_get_status(qdma, &pool_status);
		if (ret)
			return ret;
		if (copy_to_user(user_arg, &pool_status, sizeof(pool_status)))
			return -EFAULT;
		return 0;

	case QCRATE_DMA_IOC_POOL_STOP:
		if (copy_from_user(&pool_stop, user_arg, sizeof(pool_stop)))
			return -EFAULT;
		if (pool_stop.reserved0 || pool_stop.discarded_ready_banks)
			return -EINVAL;
		ret = qcrate_pool_stop(qdma, &pool_stop);
		if (copy_to_user(user_arg, &pool_stop, sizeof(pool_stop)))
			return -EFAULT;
		return ret;

	default:
		return -ENOTTY;
	}
}

/* Exclusive open prevents capture and coherent-buffer ownership races. */
static int qcrate_dma_open(struct inode *inode, struct file *file)
{
	struct miscdevice *miscdev = file->private_data;
	struct qcrate_dma_dev *qdma = container_of(miscdev,
						   struct qcrate_dma_dev,
						   miscdev);

	if (atomic_cmpxchg(&qdma->opened, 0, 1))
		return -EBUSY;
	file->private_data = qdma;
	return 0;
}

static int qcrate_dma_release(struct inode *inode, struct file *file)
{
	struct qcrate_dma_dev *qdma = file->private_data;
	struct qcrate_dma_pool_stop stop = {
		.timeout_ms = QCRATE_CANCEL_TIMEOUT_MS,
	};
	bool stop_pool;
	u32 bank;

	mutex_lock(&qdma->capture_lock);
	stop_pool = qdma->pool_configured;
	stop.run_id = qdma->pool_run_id;
	mutex_unlock(&qdma->capture_lock);
	if (stop_pool && qcrate_pool_stop(qdma, &stop))
		dev_err(qdma->dev, "release failed to stop DMA bank pool\n");

	mutex_lock(&qdma->capture_lock);
	if (qcrate_cancel_triggered_locked(qdma))
		dev_err(qdma->dev, "release left triggered DMA transaction active\n");
	for (bank = 0; bank < qdma->pool_bank_count; bank++)
		qdma->pool_banks[bank].state = QCRATE_BANK_FREE;
	qdma->pool_ready_banks = 0;
	qdma->pool_user_banks = 0;
	qdma->pool_free_banks = qdma->pool_bank_count;
	qdma->pool_configured = false;
	mutex_unlock(&qdma->capture_lock);
	atomic_set(&qdma->opened, 0);
	return 0;
}

/* Map only the driver-owned coherent buffer, never arbitrary physical memory. */
static int qcrate_dma_mmap(struct file *file, struct vm_area_struct *vma)
{
	struct qcrate_dma_dev *qdma = file->private_data;
	size_t requested = vma->vm_end - vma->vm_start;

	if (vma->vm_pgoff || requested > qdma->buffer_bytes)
		return -EINVAL;

	vm_flags_set(vma, VM_DONTEXPAND | VM_DONTDUMP);
	return dma_mmap_coherent(qdma->dma_dev, vma, qdma->buffer,
				 qdma->buffer_dma, qdma->buffer_bytes);
}

static const struct file_operations qcrate_dma_fops = {
	.owner = THIS_MODULE,
	.open = qcrate_dma_open,
	.release = qcrate_dma_release,
	.unlocked_ioctl = qcrate_dma_ioctl,
	.compat_ioctl = qcrate_dma_ioctl,
	.mmap = qcrate_dma_mmap,
	.llseek = no_llseek,
};

/*
 * Despite its DT name, xlnx,sg-length-width also limits simple-mode BTT.
 * Reading the live provider property keeps software aligned with each XSA.
 */
static u32 qcrate_max_transfer_bytes(struct dma_chan *channel)
{
	struct device *dma_dev = dmaengine_get_dma_device(channel);
	u32 width = 23;

	of_property_read_u32(dma_dev->of_node, "xlnx,sg-length-width", &width);
	if (width < 8 || width > 26)
		width = 23;
	return (1U << width) - 1U;
}

/* The generated AXI DMA node records whether the deployed IP includes SG. */
static bool qcrate_dma_has_sg(struct dma_chan *channel)
{
	struct device_node *node = dmaengine_get_dma_device(channel)->of_node;
	u32 include_sg;

	if (!node)
		return false;
	if (!of_property_read_u32(node, "xlnx,include-sg", &include_sg))
		return include_sg != 0;
	return of_property_read_bool(node, "xlnx,include-sg");
}

/* Acquire clocks and the DMA provider before the first potentially unsafe APB read. */
static int qcrate_dma_probe(struct platform_device *pdev)
{
	struct qcrate_dma_dev *qdma;
	struct clk *clock;
	u32 buffer_bytes = QCRATE_DEFAULT_BUFFER_BYTES;
	u32 device_id;
	int ret;

	qdma = devm_kzalloc(&pdev->dev, sizeof(*qdma), GFP_KERNEL);
	if (!qdma)
		return -ENOMEM;

	qdma->dev = &pdev->dev;
	qdma->regs = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(qdma->regs))
		return PTR_ERR(qdma->regs);

	clock = devm_clk_get_enabled(&pdev->dev, "stream");
	if (IS_ERR(clock))
		return dev_err_probe(&pdev->dev, PTR_ERR(clock),
				     "failed to enable stream clock\n");
	clock = devm_clk_get_enabled(&pdev->dev, "control");
	if (IS_ERR(clock))
		return dev_err_probe(&pdev->dev, PTR_ERR(clock),
				     "failed to enable control clock\n");

	qdma->rx_chan = dma_request_chan(&pdev->dev, "rx");
	if (IS_ERR(qdma->rx_chan))
		return dev_err_probe(&pdev->dev, PTR_ERR(qdma->rx_chan),
				     "failed to request S2MM DMA channel\n");

	device_id = ioread32(qdma->regs + QCRATE_SYS_DEVICE_ID);
	if (device_id != QCRATE_DEVICE_ID_VALUE) {
		ret = -ENODEV;
		dev_err(&pdev->dev, "unexpected DEVICE_ID 0x%08x\n", device_id);
		goto release_channel;
	}

	qdma->dma_dev = dmaengine_get_dma_device(qdma->rx_chan);
	qdma->max_transfer_bytes = qcrate_max_transfer_bytes(qdma->rx_chan);
	qdma->has_sg = qcrate_dma_has_sg(qdma->rx_chan);

	of_property_read_u32(pdev->dev.of_node, "qcrate,buffer-bytes",
			     &buffer_bytes);
	buffer_bytes = PAGE_ALIGN(buffer_bytes);
	if (buffer_bytes < PAGE_SIZE || buffer_bytes > QCRATE_MAX_BUFFER_BYTES) {
		ret = -EINVAL;
		dev_err(&pdev->dev, "invalid capture buffer size %u\n",
			buffer_bytes);
		goto release_channel;
	}
	qdma->buffer_bytes = buffer_bytes;

	qdma->buffer = dma_alloc_coherent(qdma->dma_dev, qdma->buffer_bytes,
					  &qdma->buffer_dma, GFP_KERNEL);
	if (!qdma->buffer) {
		ret = -ENOMEM;
		goto release_channel;
	}

	init_completion(&qdma->completion);
	init_completion(&qdma->pool_stop_done);
	mutex_init(&qdma->capture_lock);
	init_waitqueue_head(&qdma->pool_waitq);
	init_waitqueue_head(&qdma->pool_ready_waitq);
	atomic_set(&qdma->pool_events, 0);
	qdma->pool_filling_bank = QCRATE_DMA_BANK_NONE;
	qdma->pool_next_run_id = 1;
	atomic_set(&qdma->opened, 0);
	qdma->pool_thread = kthread_run(qcrate_pool_thread_fn, qdma,
					"qcrate-dma-pool");
	if (IS_ERR(qdma->pool_thread)) {
		ret = PTR_ERR(qdma->pool_thread);
		goto free_buffer;
	}
	qdma->miscdev.minor = MISC_DYNAMIC_MINOR;
	qdma->miscdev.name = "qcrate-dma";
	qdma->miscdev.fops = &qcrate_dma_fops;
	qdma->miscdev.parent = &pdev->dev;
	qdma->miscdev.mode = 0600;

	ret = misc_register(&qdma->miscdev);
	if (ret)
		goto stop_thread;

	platform_set_drvdata(pdev, qdma);
	dev_info(&pdev->dev,
		 "ready: buffer=%zu bytes, maximum transfer=%u bytes, SG=%s\n",
		 qdma->buffer_bytes, qdma->max_transfer_bytes,
		 qdma->has_sg ? "enabled" : "disabled");
	return 0;

stop_thread:
	kthread_stop(qdma->pool_thread);
free_buffer:
	dma_free_coherent(qdma->dma_dev, qdma->buffer_bytes, qdma->buffer,
			  qdma->buffer_dma);
release_channel:
	dma_release_channel(qdma->rx_chan);
	return ret;
}

/* Stop hardware activity before releasing the coherent buffer and channel. */
static int qcrate_dma_remove(struct platform_device *pdev)
{
	struct qcrate_dma_dev *qdma = platform_get_drvdata(pdev);
	struct qcrate_dma_pool_stop stop = {
		.timeout_ms = QCRATE_CANCEL_TIMEOUT_MS,
	};

	misc_deregister(&qdma->miscdev);
	if (qdma->pool_configured) {
		stop.run_id = qdma->pool_run_id;
		if (qcrate_pool_stop(qdma, &stop))
			dev_err(qdma->dev, "remove failed to stop DMA bank pool\n");
	}
	kthread_stop(qdma->pool_thread);
	dmaengine_terminate_sync(qdma->rx_chan);
	qcrate_reset_stream(qdma);
	dma_free_coherent(qdma->dma_dev, qdma->buffer_bytes, qdma->buffer,
			  qdma->buffer_dma);
	dma_release_channel(qdma->rx_chan);
	return 0;
}

static const struct of_device_id qcrate_dma_of_match[] = {
	{ .compatible = "qcrate,dma-capture-1.0" },
	{ }
};
MODULE_DEVICE_TABLE(of, qcrate_dma_of_match);

static struct platform_driver qcrate_dma_driver = {
	.probe = qcrate_dma_probe,
	.remove = qcrate_dma_remove,
	.driver = {
		.name = "qcrate-dma",
		.of_match_table = qcrate_dma_of_match,
	},
};
module_platform_driver(qcrate_dma_driver);

MODULE_AUTHOR("Q-Crate project");
MODULE_DESCRIPTION("Q-Crate synchronous and bank-pooled AXI DMA capture driver");
MODULE_LICENSE("GPL");
