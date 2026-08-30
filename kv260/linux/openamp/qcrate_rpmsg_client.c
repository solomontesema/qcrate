/* SPDX-License-Identifier: MIT */
#define _POSIX_C_SOURCE 200809L

#include "qcrate_rpmsg_client.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define QCRATE_SYSFS_RPMSG_CLASS "/sys/class/rpmsg"

static int read_text_file(const char *path, char *buffer, size_t capacity)
{
	FILE *file;
	size_t length;

	file = fopen(path, "r");
	if (!file)
		return -1;
	if (!fgets(buffer, (int)capacity, file)) {
		int saved_errno = errno ? errno : EIO;

		fclose(file);
		errno = saved_errno;
		return -1;
	}
	if (fclose(file))
		return -1;
	length = strlen(buffer);
	while (length &&
	       (buffer[length - 1] == '\n' || buffer[length - 1] == '\r'))
		buffer[--length] = '\0';
	return 0;
}

int qcrate_rpmsg_discover(char *device_path, size_t capacity)
{
	struct dirent *entry;
	DIR *directory;
	char name_path[512];
	char service_name[64];

	if (!device_path || !capacity) {
		errno = EINVAL;
		return -1;
	}
	directory = opendir(QCRATE_SYSFS_RPMSG_CLASS);
	if (!directory)
		return -1;
	while ((entry = readdir(directory)) != NULL) {
		if (strncmp(entry->d_name, "rpmsg", 5))
			continue;
		if (snprintf(name_path, sizeof(name_path), "%s/%s/name",
			     QCRATE_SYSFS_RPMSG_CLASS, entry->d_name) >=
		    (int)sizeof(name_path))
			continue;
		if (read_text_file(name_path, service_name, sizeof(service_name)))
			continue;
		if (strcmp(service_name, QCRATE_RPMSG_SERVICE_NAME))
			continue;
		if (snprintf(device_path, capacity, "/dev/%s", entry->d_name) >=
		    (int)capacity) {
			closedir(directory);
			errno = ENAMETOOLONG;
			return -1;
		}
		closedir(directory);
		return 0;
	}
	closedir(directory);
	errno = ENODEV;
	return -1;
}

static uint32_t initial_transaction_id(void)
{
	struct timespec now;
	uint32_t value;

	if (clock_gettime(CLOCK_MONOTONIC, &now))
		return (uint32_t)getpid() | 1U;
	value = (uint32_t)now.tv_nsec ^ (uint32_t)now.tv_sec ^
		(uint32_t)getpid();
	return value ? value : 1U;
}

int qcrate_rpmsg_client_open(struct qcrate_rpmsg_client *client,
			     const char *device_path, int timeout_ms)
{
	char discovered[64];

	if (!client || timeout_ms <= 0) {
		errno = EINVAL;
		return -1;
	}
	memset(client, 0, sizeof(*client));
	client->file_descriptor = -1;
	if (!device_path) {
		if (qcrate_rpmsg_discover(discovered, sizeof(discovered)))
			return -1;
		device_path = discovered;
	}
	client->file_descriptor = open(device_path, O_RDWR | O_CLOEXEC);
	if (client->file_descriptor < 0)
		return -1;
	client->timeout_ms = timeout_ms;
	client->next_transaction_id = initial_transaction_id();
	return 0;
}

void qcrate_rpmsg_client_close(struct qcrate_rpmsg_client *client)
{
	if (!client)
		return;
	if (client->file_descriptor >= 0)
		close(client->file_descriptor);
	client->file_descriptor = -1;
}

int qcrate_rpmsg_client_exchange(
	struct qcrate_rpmsg_client *client,
	uint16_t command,
	const uint32_t *request_words,
	size_t request_word_count,
	struct qcrate_rpmsg_message *response)
{
	struct qcrate_rpmsg_message request = {0};
	struct pollfd descriptor;
	ssize_t count;

	if (!client || client->file_descriptor < 0 || !response ||
	    request_word_count > QCRATE_RPMSG_PAYLOAD_WORDS ||
	    (request_word_count && !request_words)) {
		errno = EINVAL;
		return -1;
	}
	request.magic = QCRATE_RPMSG_MAGIC;
	request.abi_version = QCRATE_RPMSG_ABI_VERSION;
	request.command = command;
	request.transaction_id = client->next_transaction_id++;
	if (!client->next_transaction_id)
		client->next_transaction_id = 1U;
	request.payload_words = (uint32_t)request_word_count;
	if (request_word_count)
		memcpy(request.payload, request_words,
		       request_word_count * sizeof(*request_words));

	do {
		count = write(client->file_descriptor, &request, sizeof(request));
	} while (count < 0 && errno == EINTR);
	if (count != (ssize_t)sizeof(request)) {
		if (count >= 0)
			errno = EIO;
		return -1;
	}
	descriptor = (struct pollfd) {
		.fd = client->file_descriptor,
		.events = POLLIN,
	};
	do {
		count = poll(&descriptor, 1, client->timeout_ms);
	} while (count < 0 && errno == EINTR);
	if (!count) {
		errno = ETIMEDOUT;
		return -1;
	}
	if (count < 0)
		return -1;
	if (!(descriptor.revents & POLLIN)) {
		errno = EIO;
		return -1;
	}
	do {
		count = read(client->file_descriptor, response, sizeof(*response));
	} while (count < 0 && errno == EINTR);
	if (count != (ssize_t)sizeof(*response)) {
		if (count >= 0)
			errno = EPROTO;
		return -1;
	}
	if (response->magic != QCRATE_RPMSG_MAGIC ||
	    response->abi_version != QCRATE_RPMSG_ABI_VERSION ||
	    response->command != command ||
	    response->transaction_id != request.transaction_id ||
	    response->payload_words > QCRATE_RPMSG_PAYLOAD_WORDS) {
		errno = EPROTO;
		return -1;
	}
	if (response->status != QCRATE_STATUS_OK) {
		errno = EREMOTEIO;
		return -1;
	}
	return 0;
}
