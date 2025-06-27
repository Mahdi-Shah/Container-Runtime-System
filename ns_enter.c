#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define errExit(msg)    do { perror(msg); exit(EXIT_FAILURE); } while (0)

int main(int argc, char *argv[]) {
    int fd;
    const char *namespaces[] = { "ipc", "uts", "net", "pid", "mnt", "cgroup" };
    int num_namespaces = sizeof(namespaces) / sizeof(char *);

    // [تغییر ۱] حالا به یک آرگومان اضافه برای مسیر chroot نیاز داریم
    if (argc < 4) {
        fprintf(stderr, "Usage: %s <pid> <chroot_path> <command> [args...]\n", argv[0]);
        exit(EXIT_FAILURE);
    }

    pid_t target_pid = atoi(argv[1]);
    char *chroot_path = argv[2]; // مسیر ریشه کانتینر

    fprintf(stderr, "==> NS_ENTER: Attempting to join namespaces of PID: %d\n", target_pid);

    // ابتدا به Mount Namespace ملحق می‌شویم تا بتوانیم chroot کنیم
    char mnt_ns_path[256];
    snprintf(mnt_ns_path, sizeof(mnt_ns_path), "/proc/%d/ns/mnt", target_pid);
    fd = open(mnt_ns_path, O_RDONLY);
    if (fd == -1) errExit("open mnt namespace");
    if (setns(fd, 0) == -1) errExit("setns on mnt namespace");
    close(fd);

    // [تغییر ۲] حالا که در Mount Namespace صحیح هستیم، chroot می‌کنیم
    if (chroot(chroot_path) == -1) {
        fprintf(stderr, "NS_ENTER ERROR: chroot to '%s' failed", chroot_path);
        errExit("chroot");
    }
    // و دایرکتوری کاری را به ریشه جدید تغییر می‌دهیم
    if (chdir("/") == -1) errExit("chdir to new root");
    
    fprintf(stderr, "==> NS_ENTER: Successfully chrooted to %s\n", chroot_path);


    // حالا به بقیه Namespaceها ملحق می‌شویم
    for (int i = 0; i < num_namespaces; i++) {
        // از mnt رد می‌شویم چون قبلاً انجام شده
        if (strcmp(namespaces[i], "mnt") == 0) continue;

        char ns_path[256];
        snprintf(ns_path, sizeof(ns_path), "/proc/%d/ns/%s", target_pid, namespaces[i]);
        
        fd = open(ns_path, O_RDONLY);
        if (fd == -1) {
            fprintf(stderr, "NS_ENTER WARNING: Could not open ns %s: ", namespaces[i]);
            perror("");
            continue; 
        }
        
        if (setns(fd, 0) == -1) {
            fprintf(stderr, "NS_ENTER ERROR: setns for %s failed: ", namespaces[i]);
            errExit("setns");
        }
        close(fd);
    }

    // در آخر به User Namespace ملحق می‌شویم
    char user_ns_path[256];
    snprintf(user_ns_path, sizeof(user_ns_path), "/proc/%d/ns/user", target_pid);
    fd = open(user_ns_path, O_RDONLY);
    if (fd != -1) {
        if (setns(fd, 0) == -1) errExit("setns on user namespace");
        close(fd);
    }

    fprintf(stderr, "==> NS_ENTER: All namespaces joined. Executing command...\n");
    // [تغییر ۳] آرگومان‌ها را برای execvp تنظیم می‌کنیم
    if (execvp(argv[3], &argv[3]) == -1) {
        errExit("execvp");
    }

    return 0;
}
