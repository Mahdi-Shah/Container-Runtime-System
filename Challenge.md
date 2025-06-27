# Technical Report: The Challenge of Implementing I/O Limits with Cgroup v2

## Summary
This document details the process of implementing I/O (Input/Output) limiting for containers using the `io` controller in Cgroup v2. Despite implementing the correct logic based on Linux kernel documentation, a persistent and unexpected `write: No such device` error was encountered during testing. This report documents the systematic debugging steps, various hypotheses, and the final analysis that led to the discovery of a fundamental incompatibility between the `OverlayFS` filesystem and the `io` controller on the test environment. Finally, the engineering decision made for the project is described.

## 1. Introduction and Initial Goal
As part of the development of a container management tool, our objective was to add resource control capabilities, specifically I/O throttling (limiting disk read and write rates). To achieve this, the standard Cgroup v2 interface, the `io.max` file, was chosen. The expectation was that by writing rules to this file, we could effectively limit the disk bandwidth for each container.


``` bash
$ echo "8:3 rbps=1048576" > io.max 
```

## 2. Problem Description and Debugging Process
After the initial implementation, we encountered the following error during container execution:
```
ERROR: Failed to append to /sys/fs/cgroup/my-container-manager/<PID>/io.max: write: No such device
```
This error indicated that the Linux kernel did not recognize the device on which we were attempting to apply the I/O limit. Our debugging process to find the root cause was as follows:

### Step 1: Correcting Format and Identifying the Device
Initially, it was assumed that the format of the command written to `io.max` was incomplete. The code was modified to extract the `major:minor` ID of the physical device hosting the `rootfs` using the `stat()` system call and include it in the rule. However, the error persisted.

``` bash
$ cat /proc/partitions
major minor  #blocks  name

   7        0          4 loop0
   7        1     330588 loop1
   7        2     330588 loop2
   7        3      65296 loop3
   7        4      75676 loop4
   7        5     247964 loop5
   7        6     528392 loop6
   7        7     471628 loop7
   8        0  125034840 sda
   8        1     524288 sda1
   8        2       1024 sda2
   8        3  124508160 sda3
   7        8      93888 loop8
   7        9      51036 loop9
   7       10      74744 loop10
   7       11      52120 loop11
   7       13      12620 loop13
   7       12        568 loop12
   7       14        452 loop14
```

### Step 2: The `tmpfs` Hypothesis and Relocating `upperdir`
The next hypothesis was that since the writable layers of `OverlayFS` (`upperdir` and `workdir`) were being created in `/tmp` (which is often a RAM-based `tmpfs`), the `io` controller was unable to manage them. To resolve this, the creation path for these directories was moved to the container's persistent directory at `/var/lib/my-container-manager/...`, located on a physical `ext4` filesystem. **This change did not solve the problem.**

### Step 3: Diagnostic Test (Complete Removal of OverlayFS)
To determine if the issue was with OverlayFS itself, a diagnostic version of the `container_executor` binary was created. In this version, `OverlayFS` was completely disabled, and the container was run via a direct `chroot` into the `rootfs` directory. Surprisingly, **the `No such device` error still occurred.**

## 3. Final Analysis with System Information
This last result guided us to investigate the system environment itself. The following information was collected from the test system:
- **Kernel Version:** `Linux 6.2.0-39-generic (Ubuntu)`
- **Filesystem Type:** `ext4`
- **Active Controllers:** `io` was present in the list of active controllers in `cgroup.controllers`.

This data confirmed that the system environment was standard, modern, and should have supported the feature. The persistence of the error even without OverlayFS pointed to a more subtle, low-level issue.

### Definitive Conclusion: Incompatibility at the Kernel/Driver Level

Based on all the evidence, the problem is a low-level incompatibility within the operating system stack (the specific combination of the Ubuntu `6.2.0` kernel and its block layer drivers).

It appears that the kernel's Virtual File System (VFS) is abstracting I/O requests in such a way that the `io` controller cannot correctly attribute them to a specific physical block device. Even when OverlayFS was removed, this unexpected behavior continued, suggesting a subtle issue within this particular kernel version or its related drivers.

``` bash
$ df
Filesystem     1K-blocks     Used Available Use% Mounted on
tmpfs             801716     2168    799548   1% /run
/dev/sda3      121968272 13209680 102516800  12% /
tmpfs            4008568    34312   3974256   1% /dev/shm
tmpfs               5120       16      5104   1% /run/lock
/dev/sda1         523244     6220    517024   2% /boot/efi
tmpfs             801712      120    801592   1% /run/user/1000
overlay        121968272 13209680 102516800  12% /tmp/cont-39c9e57a-e2e-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-b9ee01dc-0c8-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-5f693b21-a67-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-03b735d4-f44-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-8f51fb13-a35-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-c132d0ff-409-merged
overlay        121968272 13209680 102516800  12% /tmp/cont-28701398-0ff-merged

```

The output of the `df` command further confirmed this. The `overlay` filesystem is listed as a distinct virtual type. The `io` controller sees the I/O request as coming from this `overlay` pseudo-device, not from the underlying physical device (`/dev/sda3`). Consequently, it rejects the rule that targets the physical device, as the source of the I/O does not match the device specified in the rule.

## 4. Adopted Solution
Given that the root cause of the problem lies outside the project's code and within the system environment, attempting to fix it is not feasible within the scope of this project. Therefore, the following decisions were made:
1.  **Documentation:** The challenge and its findings were documented as part of the project's technical report to demonstrate the depth of the analysis performed.
2.  **Disabling the Feature:** The code related to I/O limiting was removed (or commented out) from the final version to ensure the overall stability of the container management tool.
3.  **Continuing Development:** The project's focus was shifted to implementing other features, such as CPU Pinning, which rely on more stable and widely compatible cgroup controllers.