// SPDX-License-Identifier: MIT
/*
 * Q-Crate receive-only DMAEngine client
 *
 * This driver owns the Q-Crate stream-control registers and one AXI DMA S2MM
 * channel. It allocates a DMA-coherent capture buffer and exposes a small
 * ioctl/mmap ABI through /dev/qcrate-dma. Userspace requests a frame and reads
 * the completed buffer; it never programs AXI DMA registers, handles physical
 * addresses, or performs cache maintenance.
 *
 * The critical transaction order is:
 *   reset stream -> configure frame -> arm S2MM -> start stream -> wait
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
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <linux/uaccess.h>

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

#define QCRATE_CONTROL_START            BIT(0)
#define QCRATE_CONTROL_SOFT_RESET       BIT(2)
#define QCRATE_STATUS_BUSY              BIT(0)
#define QCRATE_STATUS_DONE              BIT(1)
#define QCRATE_STATUS_ERROR             BIT(2)
#define QCRATE_STATUS_COMMAND_BUSY      BIT(8)

#define QCRATE_STREAM_WORD_BYTES        4U
#define QCRATE_DEFAULT_BUFFER_BYTES     SZ_1M
#define QCRATE_MAX_BUFFER_BYTES         SZ_16M
#define QCRATE_DEFAULT_TIMEOUT_MS       1000U
#define QCRATE_MAX_TIMEOUT_MS           60000U
#define QCRATE_STATUS_TIMEOUT_US        100000U

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

	/* One in-flight transaction and one userspace owner are allowed. */
	struct completion completion;
	struct dmaengine_result result;
	struct mutex capture_lock;
	atomic_t opened;
	struct miscdevice miscdev;
};

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

/*
 * Execute one finite-frame capture while holding capture_lock. The coherent
 * buffer remains allocated for the device lifetime and is reused per request.
 */
static int qcrate_capture(struct qcrate_dma_dev *qdma,
			  struct qcrate_dma_capture *capture)
{
	struct dma_async_tx_descriptor *desc;
	dma_cookie_t cookie;
	unsigned long wait_result;
	size_t transfer_bytes;
	u32 status;
	int ret;

	if (!capture->frame_length_words)
		return -EINVAL;

	if (!capture->timeout_ms)
		capture->timeout_ms = QCRATE_DEFAULT_TIMEOUT_MS;
	if (capture->timeout_ms > QCRATE_MAX_TIMEOUT_MS)
		return -EINVAL;

	transfer_bytes = (size_t)capture->frame_length_words *
			 QCRATE_STREAM_WORD_BYTES;
	if (transfer_bytes > qdma->buffer_bytes ||
	    transfer_bytes > qdma->max_transfer_bytes)
		return -EMSGSIZE;

	ret = mutex_lock_interruptible(&qdma->capture_lock);
	if (ret)
		return ret;

	ret = dmaengine_terminate_sync(qdma->rx_chan);
	if (ret)
		goto out_unlock;

	ret = qcrate_reset_stream(qdma);
	if (ret)
		goto out_terminate;

	/* Poisoning makes a missing or short DMA write obvious during verification. */
	memset(qdma->buffer, 0xa5, transfer_bytes);
	reinit_completion(&qdma->completion);
	qdma->result.result = DMA_TRANS_ABORTED;
	qdma->result.residue = transfer_bytes;

	/* Program one finite pattern frame and clear stale Q-Crate IRQ status. */
	iowrite32(0, qdma->regs + QCRATE_STREAM_CONTROL);
	iowrite32(capture->frame_length_words,
		  qdma->regs + QCRATE_STREAM_FRAME_LENGTH);
	iowrite32(1, qdma->regs + QCRATE_STREAM_FRAME_COUNT);
	iowrite32(0, qdma->regs + QCRATE_STREAM_MODE);
	iowrite32(0, qdma->regs + QCRATE_STREAM_IRQ_ENABLE);
	iowrite32(0x3, qdma->regs + QCRATE_STREAM_IRQ_CLEAR);

	desc = dmaengine_prep_slave_single(qdma->rx_chan, qdma->buffer_dma,
					   transfer_bytes, DMA_DEV_TO_MEM,
					   DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!desc) {
		ret = -EIO;
		goto out_reset;
	}

	desc->callback_result = qcrate_dma_complete;
	desc->callback_param = qdma;
	cookie = dmaengine_submit(desc);
	ret = dma_submit_error(cookie);
	if (ret)
		goto out_reset;

	/* Arm S2MM first; START may assert AXI4-Stream TVALID immediately. */
	dma_async_issue_pending(qdma->rx_chan);
	iowrite32(QCRATE_CONTROL_START,
		  qdma->regs + QCRATE_STREAM_CONTROL);

	wait_result = wait_for_completion_interruptible_timeout(
		&qdma->completion, msecs_to_jiffies(capture->timeout_ms));
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
	capture->residue_bytes = min_t(u32, qdma->result.residue,
					 transfer_bytes);
	capture->transferred_bytes = transfer_bytes - capture->residue_bytes;
	capture->dma_result = qdma->result.result;
	capture->stream_status = status;
	capture->completed_frames = ioread32(
		qdma->regs + QCRATE_STREAM_COMPLETED_FRAMES);
	capture->current_frame_id = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_FRAME_ID);
	capture->current_sample_index = ioread32(
		qdma->regs + QCRATE_STREAM_CURRENT_SAMPLE);
	capture->stall_cycles = ioread32(
		qdma->regs + QCRATE_STREAM_STALL_CYCLES);

	if (capture->dma_result != DMA_TRANS_NOERROR ||
	    capture->residue_bytes ||
	    !(capture->stream_status & QCRATE_STATUS_DONE) ||
	    (capture->stream_status & QCRATE_STATUS_ERROR) ||
	    capture->completed_frames != 1) {
		dev_err(qdma->dev,
			"capture failed: dma=%u residue=%u status=0x%08x frames=%u\n",
			capture->dma_result, capture->residue_bytes,
			capture->stream_status, capture->completed_frames);
		ret = -EIO;
		goto out_reset;
	}

	ret = 0;
	goto out_unlock;

out_stop:
out_reset:
	qcrate_reset_stream(qdma);
out_terminate:
	dmaengine_terminate_sync(qdma->rx_chan);
out_unlock:
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
	struct qcrate_dma_info info = {
		.abi_version = QCRATE_DMA_ABI_VERSION,
		.buffer_bytes = qdma->buffer_bytes,
		.max_transfer_bytes = qdma->max_transfer_bytes,
		.stream_word_bytes = QCRATE_STREAM_WORD_BYTES,
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
	mutex_init(&qdma->capture_lock);
	atomic_set(&qdma->opened, 0);
	qdma->miscdev.minor = MISC_DYNAMIC_MINOR;
	qdma->miscdev.name = "qcrate-dma";
	qdma->miscdev.fops = &qcrate_dma_fops;
	qdma->miscdev.parent = &pdev->dev;
	qdma->miscdev.mode = 0600;

	ret = misc_register(&qdma->miscdev);
	if (ret)
		goto free_buffer;

	platform_set_drvdata(pdev, qdma);
	dev_info(&pdev->dev,
		 "ready: buffer=%zu bytes, maximum transfer=%u bytes\n",
		 qdma->buffer_bytes, qdma->max_transfer_bytes);
	return 0;

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

	misc_deregister(&qdma->miscdev);
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
MODULE_DESCRIPTION("Q-Crate AXI DMA S2MM capture driver");
MODULE_LICENSE("GPL");
